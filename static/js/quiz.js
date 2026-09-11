(function () {
  "use strict";

  const quizId = document.querySelector('meta[name="quiz-id"]').content;
  const telegramUrl = document.querySelector('meta[name="channel-url"]').content;
  const STORAGE_KEY = `sfc_quiz_${quizId}_state`;

  const screens = {
    intro: document.getElementById("screen-intro"),
    quiz: document.getElementById("screen-quiz"),
    result: document.getElementById("screen-result"),
    error: document.getElementById("screen-error"),
  };

  const els = {
    btnStart: document.getElementById("btn-start"),
    btnPrev: document.getElementById("btn-prev"),
    btnNext: document.getElementById("btn-next"),
    btnTelegram: document.getElementById("btn-telegram"),
    timer: document.getElementById("timer"),
    progressBar: document.getElementById("progress-bar"),
    progressLabel: document.getElementById("progress-label"),
    questionText: document.getElementById("question-text"),
    optionsList: document.getElementById("options-list"),
    resultScore: document.getElementById("result-score"),
    resultHeadline: document.getElementById("result-headline"),
    resultEligible: document.getElementById("result-eligible"),
    errorText: document.getElementById("error-text"),
  };

  /** @type {{sessionId:string, questions:any[], durationSeconds:number,
   *  startedAtClient:number, answers:Object<string, number>,
   *  currentIndex:number, submitted:boolean, result:any}} */
  let state = null;
  let timerInterval = null;

  function showScreen(name) {
    Object.values(screens).forEach((s) => s.classList.add("hidden"));
    screens[name].classList.remove("hidden");
  }

  function showError(msg) {
    els.errorText.textContent = msg || "कृपया पुनः प्रयास करें।";
    showScreen("error");
  }

  function saveState() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* sessionStorage unavailable (e.g. private mode) - quiz still works,
         just won't survive a refresh. Fail silently. */
    }
  }

  function loadState() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function clearState() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function track(event, extra) {
    const body = Object.assign({ event, quiz_id: Number(quizId) }, extra || {});
    fetch("/api/analytics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {
      /* analytics must never break the quiz UX */
    });
  }

  function scoreRangeOf(score, maxMarks) {
    const pct = maxMarks ? (score / maxMarks) * 100 : 0;
    if (pct >= 80) return "80-100";
    if (pct >= 60) return "60-79";
    return "0-59";
  }

  // ---------------------------------------------------------------------
  // Fetch a fresh quiz + session from the server
  // ---------------------------------------------------------------------
  async function fetchNewQuiz() {
    const res = await fetch(`/api/quiz/${quizId}`);
    if (res.status === 404) {
      throw { kind: "not_found" };
    }
    if (!res.ok) {
      throw { kind: "generic" };
    }
    const data = await res.json();
    state = {
      sessionId: data.session_id,
      questions: data.questions,
      durationSeconds: data.duration_seconds,
      startedAtClient: Date.now(),
      answers: {},
      currentIndex: 0,
      submitted: false,
      result: null,
    };
    saveState();
    return state;
  }

  function remainingSeconds() {
    const elapsed = Math.floor((Date.now() - state.startedAtClient) / 1000);
    return Math.max(0, state.durationSeconds - elapsed);
  }

  function formatTime(sec) {
    const m = Math.floor(sec / 60).toString().padStart(2, "0");
    const s = Math.floor(sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  function startTimer() {
    stopTimer();
    updateTimerDisplay();
    timerInterval = setInterval(() => {
      const left = remainingSeconds();
      updateTimerDisplay(left);
      if (left <= 0) {
        stopTimer();
        submitQuiz(true);
      }
    }, 1000);
  }

  function stopTimer() {
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
  }

  function updateTimerDisplay(secOverride) {
    const left = secOverride === undefined ? remainingSeconds() : secOverride;
    els.timer.textContent = formatTime(left);
    els.timer.classList.remove("timer-warn", "timer-danger");
    if (left <= 15) els.timer.classList.add("timer-danger");
    else if (left <= 45) els.timer.classList.add("timer-warn");
  }

  // ---------------------------------------------------------------------
  // Rendering the current question
  // ---------------------------------------------------------------------
  function renderQuestion() {
    const total = state.questions.length;
    const q = state.questions[state.currentIndex];

    els.questionText.textContent = q.question;
    els.progressLabel.textContent = `Q${state.currentIndex + 1} / ${total}`;
    els.progressBar.style.width = `${((state.currentIndex + 1) / total) * 100}%`;

    els.optionsList.innerHTML = "";
    q.options.forEach((optionText, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "option-btn";
      btn.textContent = optionText;
      if (state.answers[q.id] === idx) btn.classList.add("selected");
      btn.addEventListener("click", () => selectOption(q.id, idx));
      els.optionsList.appendChild(btn);
    });

    els.btnPrev.disabled = state.currentIndex === 0;
    els.btnNext.textContent =
      state.currentIndex === total - 1 ? "✅ Submit करें" : "आगे ➡";
  }

  function selectOption(questionId, idx) {
    state.answers[questionId] = idx;
    saveState();
    renderQuestion();
  }

  function goNext() {
    const total = state.questions.length;
    if (state.currentIndex < total - 1) {
      state.currentIndex += 1;
      saveState();
      renderQuestion();
    } else {
      submitQuiz(false);
    }
  }

  function goPrev() {
    if (state.currentIndex > 0) {
      state.currentIndex -= 1;
      saveState();
      renderQuestion();
    }
  }

  // ---------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------
  async function submitQuiz(dueToTimeout) {
    if (state.submitted) return;
    stopTimer();
    state.submitted = true;
    saveState();

    try {
      const res = await fetch(`/api/quiz/${quizId}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.sessionId,
          answers: state.answers,
        }),
      });
      if (!res.ok) throw new Error("submit failed");
      const data = await res.json();
      state.result = data;
      saveState();
      renderResult(data, dueToTimeout);
      track("quiz_completed", { score_range: scoreRangeOf(data.score, data.max_marks || data.total) });
    } catch (e) {
      showError("Result submit करने में समस्या आई। कृपया दोबारा कोशिश करें।");
    }
  }

  function renderResult(data, dueToTimeout) {
    els.resultScore.textContent = `${data.score}/${data.max_marks || data.total}`;
    els.resultHeadline.textContent = data.message.headline;
    els.resultEligible.textContent = data.message.eligible;
    showScreen("result");
  }

  // ---------------------------------------------------------------------
  // Init / resume
  // ---------------------------------------------------------------------
  async function init() {
    els.btnTelegram.addEventListener("click", () => {
      track("telegram_button_clicked");
    });
    els.btnNext.addEventListener("click", goNext);
    els.btnPrev.addEventListener("click", goPrev);

    const saved = loadState();
    if (saved && saved.sessionId) {
      state = saved;
      if (state.submitted && state.result) {
        renderResult(state.result, false);
        return;
      }
      if (!state.submitted) {
        // Resume in-progress quiz (handles refresh / back-button-forward)
        showScreen("quiz");
        renderQuestion();
        if (remainingSeconds() <= 0) {
          submitQuiz(true);
        } else {
          startTimer();
        }
        return;
      }
    }

    // No valid saved state -> show intro, wait for Start
    showScreen("intro");
    els.btnStart.addEventListener("click", async () => {
      els.btnStart.disabled = true;
      els.btnStart.textContent = "लोड हो रहा है...";
      try {
        await fetchNewQuiz();
        track("quiz_started");
        showScreen("quiz");
        renderQuestion();
        startTimer();
      } catch (err) {
        if (err && err.kind === "not_found") {
          showError("यह Quiz उपलब्ध नहीं है।");
        } else {
          showError("Quiz लोड नहीं हो सका। कृपया इंटरनेट जांचें और पुनः प्रयास करें।");
        }
        els.btnStart.disabled = false;
        els.btnStart.textContent = "🚀 Start Quiz";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
