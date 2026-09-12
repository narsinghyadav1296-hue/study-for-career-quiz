"""
DOCX question-paper import (Section 6/13/14/15/16 of the requirement).

Design goals:
- Modular: `parse_docx()` picks a parser strategy; more strategies (for
  different question-paper formats, or PDF later) can be added without
  touching the admin routes or the preview/edit UI.
- Never auto-publish: `parse_docx()` only ever returns a preview structure
  with per-question confidence/warnings. The admin panel always shows this
  preview and lets the admin fix mistakes before anything is written to
  the database (see blueprints/admin_docx_import.py).
- Preserves statement-based UPPCS/UPSC style questions (कथन-I / कथन-II /
  कूट) and Hindi+English bilingual text as a single question block, the
  same convention already used by the existing quiz_2.json content -
  we don't invent a new rigid schema that would break that pattern.

Expected plain-text layout per question (the most common Word export
pattern for these question papers):

    1. Question text (can span multiple lines, including
       कथन-I: ...
       कथन-II: ...
       कूट: ...)
    (a) Option one
    (b) Option two
    (c) Option three
    (d) Option four
    Answer: b

Numbering styles are flexible: "1.", "1)", "Q1.", "Q1)" for questions and
"(a)/(b)/(c)/(d)", "a)/b)/c)/d)", "A./B./C./D." for options. The "Answer:"
line accepts a letter (a-d), a number (1-4), or the literal option text.
"""
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Optional

import docx


# ---------------------------------------------------------------------
# Data shapes returned to the admin preview screen
# ---------------------------------------------------------------------
@dataclass
class ParsedQuestion:
    index: int
    question: str
    options: List[str]
    correct_answer: Optional[int]  # 0-based, None if it couldn't be determined
    explanation: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self):
        return (
            bool(self.question.strip())
            and len(self.options) == 4
            and all(opt.strip() for opt in self.options)
            and self.correct_answer is not None
            and 0 <= self.correct_answer <= 3
        )


@dataclass
class ParseResult:
    questions: List[ParsedQuestion]
    raw_paragraph_count: int
    global_warnings: List[str] = field(default_factory=list)

    @property
    def valid_count(self):
        return sum(1 for q in self.questions if q.is_valid)

    @property
    def invalid_count(self):
        return len(self.questions) - self.valid_count


# ---------------------------------------------------------------------
# Line classification helpers
# ---------------------------------------------------------------------
_QUESTION_START_RE = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,4})[.)]\s*(.*)$")
_OPTION_RE = re.compile(r"^\s*[\(\[]?([a-dA-D])[\)\].]\s*(.*)$")
_ANSWER_RE = re.compile(
    r"^\s*(?:Answer|Ans|उत्तर|सही उत्तर)\s*[:\-]?\s*(.+)$", re.IGNORECASE
)
_EXPLANATION_RE = re.compile(
    r"^\s*(?:Explanation|व्याख्या)\s*[:\-]?\s*(.+)$", re.IGNORECASE
)

# Statement-block markers that must stay glued to the question text rather
# than being mistaken for a new question/option (Section 15).
_STATEMENT_MARKER_RE = re.compile(
    r"^\s*(कथन[\s\-–]*[IVX0-9०-९]*|कूट|Statement[\s\-–]*[IVX0-9]*|Codes?)\s*[:\-]"
)

_LETTER_TO_INDEX = {"a": 0, "b": 1, "c": 2, "d": 3}


def _iter_paragraph_texts(document):
    for para in document.paragraphs:
        text = para.text.replace("\u00a0", " ").rstrip()
        if text.strip():
            yield text


def _match_answer_to_index(answer_text, options):
    answer_text = answer_text.strip()
    # Letter form: a / b / c / d (optionally with trailing punctuation/word)
    letter_match = re.match(r"^([a-dA-D])\b", answer_text)
    if letter_match:
        return _LETTER_TO_INDEX[letter_match.group(1).lower()]
    # Numeric form: 1 / 2 / 3 / 4
    num_match = re.match(r"^([1-4])\b", answer_text)
    if num_match:
        return int(num_match.group(1)) - 1
    # Fall back: exact/substring match against option text
    normalized = answer_text.lower()
    for i, opt in enumerate(options):
        if opt.strip().lower() == normalized or normalized in opt.strip().lower():
            return i
    return None


def _parse_numbered_paragraphs(paragraph_texts) -> ParseResult:
    """Primary/default parser strategy: '1. question / (a).. (d) options /
    Answer: x' pattern, one question per numbered block."""
    questions: List[ParsedQuestion] = []
    current: Optional[ParsedQuestion] = None
    question_lines: List[str] = []

    def flush():
        if current is not None:
            current.question = "\n".join(question_lines).strip()
            questions.append(current)

    for raw_line in paragraph_texts:
        q_match = _QUESTION_START_RE.match(raw_line)
        opt_match = _OPTION_RE.match(raw_line)
        ans_match = _ANSWER_RE.match(raw_line)
        exp_match = _EXPLANATION_RE.match(raw_line)

        # A line starting with "कथन-I:", "कूट:", etc. must never be treated
        # as a new option/answer line even though it contains a colon -
        # it's part of the question body (Section 15).
        looks_like_statement_line = bool(_STATEMENT_MARKER_RE.match(raw_line))

        if q_match and not looks_like_statement_line and current is None:
            # First question in the document.
            flush()
            current = ParsedQuestion(index=len(questions) + 1, question="", options=[], correct_answer=None)
            question_lines = [q_match.group(2).strip()]
            continue

        if q_match and not looks_like_statement_line and current is not None and current.options:
            # A new question only starts once we've already seen at least
            # one option for the current question - otherwise a numbered
            # sub-point inside the question text (e.g. "1. ... 2. ...")
            # would be mis-detected as a new question.
            flush()
            current = ParsedQuestion(index=len(questions) + 1, question="", options=[], correct_answer=None)
            question_lines = [q_match.group(2).strip()]
            continue

        if current is None:
            # Content before the first recognised question number - ignore
            # (title pages, instructions, etc.)
            continue

        if opt_match and not looks_like_statement_line:
            current.options.append(opt_match.group(2).strip())
            continue

        if ans_match:
            idx = _match_answer_to_index(ans_match.group(1), current.options)
            current.correct_answer = idx
            if idx is None:
                current.warnings.append(
                    f"Could not match answer text '{ans_match.group(1)}' to an option."
                )
            continue

        if exp_match:
            current.explanation = (current.explanation + " " + exp_match.group(1)).strip()
            continue

        if current.options:
            # Extra line after options have started but before "Answer:" -
            # most likely a continuation of the last option or an
            # explanation without the "Explanation:" prefix. Append to the
            # last option to avoid silently dropping content.
            current.options[-1] = (current.options[-1] + " " + raw_line).strip()
        else:
            # Still inside the question body (covers multi-line statement
            # blocks: कथन-I / कथन-II / कूट, or a wrapped English line).
            question_lines.append(raw_line)

    flush()

    for q in questions:
        if len(q.options) != 4:
            q.warnings.append(f"Expected 4 options, found {len(q.options)}.")
        if q.correct_answer is None:
            q.warnings.append("Correct answer could not be detected - please select it manually.")

    return ParseResult(questions=questions, raw_paragraph_count=0)


# Registry of parser strategies, tried in order until one yields at least
# one question. Keeping this a list (rather than a single hardcoded
# function) is what makes the module "future formats" ready (Section 14).
_PARSER_STRATEGIES = [
    _parse_numbered_paragraphs,
]


def parse_docx(file_stream) -> ParseResult:
    """file_stream: a binary file-like object (already opened/seeked to 0)."""
    data = file_stream.read()
    document = docx.Document(BytesIO(data))
    paragraph_texts = list(_iter_paragraph_texts(document))

    result = None
    for strategy in _PARSER_STRATEGIES:
        candidate = strategy(paragraph_texts)
        candidate.raw_paragraph_count = len(paragraph_texts)
        if candidate.questions:
            result = candidate
            break

    if result is None:
        result = ParseResult(questions=[], raw_paragraph_count=len(paragraph_texts))
        result.global_warnings.append(
            "No questions could be detected in this document. Please check the "
            "format matches: '1. Question' / '(a)/(b)/(c)/(d) options' / 'Answer: b'."
        )

    if not paragraph_texts:
        result.global_warnings.append("The document appears to be empty.")

    return result
