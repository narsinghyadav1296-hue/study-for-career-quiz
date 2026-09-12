"""
Persistent file storage abstraction (Section 7/8/14 of the requirement).

Render's (and most PaaS) web-service filesystem is ephemeral - anything
written to local disk disappears on the next deploy/restart. So uploaded
PDFs/DOCX/images must never be treated as permanently stored just because
they were saved to disk.

This module hides the actual storage provider behind one small interface:

    storage.save(file_storage, folder) -> (storage_key, size_bytes)
    storage.url(storage_key) -> a URL the browser can use to view/download
    storage.delete(storage_key) -> remove the object
    storage.open_stream(storage_key) -> binary stream (used by DOCX import,
                                         which needs to read the file back)

Two backends are provided:

- LocalStorageBackend: saves under LOCAL_UPLOAD_DIR. Fine for local
  development/testing on your own machine. Do NOT rely on this in
  production on Render.
- S3StorageBackend: any S3-compatible object storage (AWS S3, Cloudflare
  R2, Backblaze B2, MinIO...). This is what production should use.

Which one is active is controlled entirely by the STORAGE_BACKEND
environment variable, so switching providers later is a config change,
not a code change.
"""
import mimetypes
import os
import uuid
from pathlib import Path

from flask import current_app


class StorageError(Exception):
    pass


class LocalStorageBackend:
    name = "local"

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file_storage, folder, safe_filename):
        folder_path = self.base_dir / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        key = f"{folder}/{safe_filename}"
        dest = self.base_dir / key
        file_storage.save(dest)
        size = dest.stat().st_size
        return key, size

    def url(self, storage_key):
        # Served via the /media/<path:key> route (see blueprints/public.py)
        return f"/media/{storage_key}"

    def delete(self, storage_key):
        path = self.base_dir / storage_key
        if path.exists():
            path.unlink()

    def open_stream(self, storage_key):
        path = self.base_dir / storage_key
        return open(path, "rb")

    def full_path(self, storage_key):
        return self.base_dir / storage_key


class S3StorageBackend:
    """S3-compatible backend. Works with AWS S3 directly, and with
    Cloudflare R2 / Backblaze B2 / MinIO via S3_ENDPOINT_URL.

    boto3 is imported lazily so that local development (STORAGE_BACKEND=
    local, the default) never needs boto3 installed at all.
    """

    name = "s3"

    def __init__(self, cfg):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - only hit if misconfigured
            raise StorageError(
                "STORAGE_BACKEND=s3 requires the 'boto3' package. "
                "Add it to requirements.txt and pip install it."
            ) from exc

        self.bucket = cfg["S3_BUCKET"]
        if not self.bucket:
            raise StorageError("S3_BUCKET is not configured.")
        session_kwargs = {}
        if cfg.get("S3_ACCESS_KEY_ID"):
            session_kwargs["aws_access_key_id"] = cfg["S3_ACCESS_KEY_ID"]
            session_kwargs["aws_secret_access_key"] = cfg["S3_SECRET_ACCESS_KEY"]
        client_kwargs = {"region_name": cfg.get("S3_REGION", "auto")}
        if cfg.get("S3_ENDPOINT_URL"):
            client_kwargs["endpoint_url"] = cfg["S3_ENDPOINT_URL"]
        self.client = boto3.client("s3", **session_kwargs, **client_kwargs)
        self.public_base_url = cfg.get("S3_PUBLIC_BASE_URL", "")

    def save(self, file_storage, folder, safe_filename):
        key = f"{folder}/{safe_filename}"
        file_storage.stream.seek(0)
        content_type = file_storage.mimetype or "application/octet-stream"
        self.client.upload_fileobj(
            file_storage.stream,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        size = head["ContentLength"]
        return key, size

    def url(self, storage_key):
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}/{storage_key}"
        # Fall back to a time-limited signed URL if no public CDN domain is
        # configured.
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=3600,
        )

    def delete(self, storage_key):
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

    def open_stream(self, storage_key):
        obj = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        return obj["Body"]


def get_storage():
    """Return the configured backend. Cached on the app object per-request
    isn't necessary since construction is cheap (local) or done once
    (S3 client construction) - but we keep it simple and construct fresh
    each time it's requested to avoid stale app-context issues."""
    cfg = current_app.config
    backend = cfg.get("STORAGE_BACKEND", "local")
    if backend == "s3":
        return S3StorageBackend(cfg)
    return LocalStorageBackend(cfg["LOCAL_UPLOAD_DIR"])


def safe_unique_filename(original_filename):
    """Never trust the uploaded filename directly (Section 20: safe
    filenames). Keep the extension, replace the name with a random id."""
    ext = ""
    if "." in original_filename:
        ext = "." + original_filename.rsplit(".", 1)[1].lower()
        ext = "".join(ch for ch in ext if ch.isalnum() or ch == ".")
    return f"{uuid.uuid4().hex}{ext}"


def guess_mime_type(filename):
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"
