"use strict";

import {
  llmUnlocked,
  loadLlmProfile,
  lockLlm,
  profileRequestReady,
  reasoningFailureText,
  requestExplanation,
  requestOverallSummary,
  unlockLlm,
} from "/static/js/analysis-llm.js";
import {
  clearWorkspace,
  engineSettingsCompatible,
  engineSnapshot,
  loadWorkspace,
  saveWorkspace,
} from "/static/js/analysis-workspace.js";

const PRESETS = {
  fast: { depth: 14, movetime: 1000 },
  standard: { depth: 18, movetime: 2000 },
  deep: { depth: 22, movetime: 4000 },
};
const STORED = (() => {
  try {
    return JSON.parse(
      window.localStorage.getItem("chessfen.analysis.settings.v1") || "{}"
    );
  } catch (error) { return {}; }
})();
const MULTIPV = Number.isInteger(STORED.multipv) && STORED.multipv >= 1 && STORED.multipv <= 5 ? STORED.multipv : 3;
const PRESET = Object.hasOwn(PRESETS, STORED.preset) ? PRESETS[STORED.preset] : PRESETS.standard;
const SEARCH_DEPTH = PRESET.depth;
const SEARCH_MOVETIME_MS = PRESET.movetime;
const INIT_TIMEOUT_MS = 30000;
const STOP_TIMEOUT_MS = 2000;

class EngineError extends Error {}
class AnalysisRequestError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

class EngineSupervisor {
  constructor(workerUrl, onInfo) {
    this.workerUrl = workerUrl;
    this.onInfo = onInfo;
    this.worker = null;
    this.ready = false;
    this.initPhase = "uci";
    this.initTimer = null;
    this.readyWaiters = [];
    this.activeJob = null;
  }

  get busy() {
    return this.activeJob !== null;
  }

  analyze(fen) {
    if (this.activeJob) {
      throw new EngineError("The engine is already analyzing.");
    }
    const job = this._newJob(fen);
    this.activeJob = job;
    this._prepareAndSearch(job);
    return job.promise;
  }

  async cancel() {
    const job = this.activeJob;
    if (!job) {
      this._terminateWorker();
      return;
    }
    if (job.cancelled) {
      return job.promise;
    }
    job.cancelled = true;
    if (this.ready && this.worker) {
      this.worker.postMessage("stop");
      job.stopTimer = setTimeout(() => {
        if (this.activeJob !== job) {
          return;
        }
        this._terminateWorker();
        this._finishJob(job, { cancelled: true });
      }, STOP_TIMEOUT_MS);
    } else {
      this._terminateWorker();
      this._finishJob(job, { cancelled: true });
    }
    return job.promise;
  }

  _newJob(fen) {
    const job = {
      fen,
      cancelled: false,
      settled: false,
      stopTimer: null,
      lines: new Map(),
      depth: null,
      nps: null,
      rootMove: null,
      resolve: null,
      reject: null,
      promise: null,
    };
    job.promise = new Promise((resolve, reject) => {
      job.resolve = resolve;
      job.reject = reject;
    });
    return job;
  }

  async _prepareAndSearch(job) {
    try {
      await this._ensureReady();
    } catch (error) {
      if (job.cancelled) {
        this._finishJob(job, { cancelled: true });
      } else {
        this._finishJob(job, { error: new EngineError(String(error)) });
      }
      return;
    }
    if (this.activeJob !== job) {
      return;
    }
    if (job.cancelled) {
      this._finishJob(job, { cancelled: true });
      return;
    }
    this.worker.postMessage(`position fen ${job.fen}`);
    this.worker.postMessage(
      `go depth ${SEARCH_DEPTH} movetime ${SEARCH_MOVETIME_MS}`
    );
  }

  _ensureReady() {
    if (this.ready) {
      return Promise.resolve();
    }
    if (!this.worker) {
      this._createWorker();
    }
    return new Promise((resolve, reject) => {
      this.readyWaiters.push({ resolve, reject });
    });
  }

  _createWorker() {
    this._terminateWorker();
    const worker = new Worker(this.workerUrl);
    this.worker = worker;
    this.ready = false;
    this.initPhase = "uci";
    worker.onmessage = (event) => {
      if (this.worker === worker) {
        this._onMessage(event);
      }
    };
    worker.onerror = () => {
      if (this.worker === worker) {
        this._onEngineFailure();
      }
    };
    this.initTimer = setTimeout(() => {
      if (this.worker === worker && !this.ready) {
        this._onEngineFailure();
      }
    }, INIT_TIMEOUT_MS);
    worker.postMessage("uci");
  }

  _onMessage(event) {
    const text = this._messageText(event);
    if (!text) {
      return;
    }
    if (!this.ready) {
      this._handleInitialization(text.trim());
      return;
    }
    const job = this.activeJob;
    if (!job) {
      return;
    }
    if (text.startsWith("info ")) {
      this._handleInfo(job, text);
    } else if (text.startsWith("bestmove")) {
      const bestMove = text.split(/\s+/)[1] || null;
      if (job.cancelled) {
        this._finishJob(job, { cancelled: true });
      } else {
        const lines = [...job.lines.values()]
          .sort((left, right) => left.multipv - right.multipv)
          .slice(0, MULTIPV);
        if (lines.length === 0) {
          this._finishJob(job, {
            error: new EngineError("Stockfish returned no Candidate Lines."),
          });
        } else {
          this._finishJob(job, {
            cancelled: false,
            lines,
            bestMove,
            depth: job.depth,
            nps: job.nps,
            rootMove: job.rootMove || lines[0].pv[0],
          });
        }
      }
    }
  }

  _handleInitialization(text) {
    if (this.initPhase === "uci" && text === "uciok") {
      this.initPhase = "ready";
      this.worker.postMessage(`setoption name MultiPV value ${MULTIPV}`);
      this.worker.postMessage("setoption name Threads value 1");
      this.worker.postMessage("setoption name Hash value 32");
      this.worker.postMessage("isready");
    } else if (this.initPhase === "ready" && text === "readyok") {
      this.ready = true;
      clearTimeout(this.initTimer);
      this.initTimer = null;
      const waiters = this.readyWaiters;
      this.readyWaiters = [];
      for (const waiter of waiters) {
        waiter.resolve();
      }
    }
  }

  _handleInfo(job, text) {
    const tokens = text.split(/\s+/);
    const info = {
      depth: null,
      multipv: 1,
      nps: null,
      rootMove: null,
      scoreKind: null,
      scoreValue: null,
      pv: [],
    };
    for (let index = 1; index < tokens.length; index += 1) {
      const token = tokens[index];
      if (token === "depth") {
        info.depth = Number.parseInt(tokens[index + 1], 10);
        index += 1;
      } else if (token === "multipv") {
        info.multipv = Number.parseInt(tokens[index + 1], 10);
        index += 1;
      } else if (token === "nps") {
        info.nps = Number.parseInt(tokens[index + 1], 10);
        index += 1;
      } else if (token === "currmove") {
        info.rootMove = tokens[index + 1] || null;
        index += 1;
      } else if (token === "score") {
        info.scoreKind = tokens[index + 1] || null;
        info.scoreValue = Number.parseInt(tokens[index + 2], 10);
        index += 2;
      } else if (token === "pv") {
        info.pv = tokens.slice(index + 1);
        break;
      }
    }

    if (Number.isFinite(info.depth)) {
      job.depth = info.depth;
    }
    if (Number.isFinite(info.nps)) {
      job.nps = info.nps;
    }
    if (info.rootMove) {
      job.rootMove = info.rootMove;
    }
    this.onInfo({
      depth: job.depth,
      nps: job.nps,
      rootMove: job.rootMove,
    });

    if (
      info.pv.length > 0 &&
      Number.isFinite(info.depth) &&
      Number.isFinite(info.scoreValue) &&
      (info.scoreKind === "cp" || info.scoreKind === "mate")
    ) {
      const previous = job.lines.get(info.multipv);
      if (!previous || info.depth >= previous.depth) {
        job.lines.set(info.multipv, {
          multipv: info.multipv,
          depth: info.depth,
          score: {
            kind: info.scoreKind,
            value: info.scoreValue,
          },
          pv: info.pv,
        });
      }
    }
  }

  _onEngineFailure() {
    this._terminateWorker();
    const error = new EngineError("Stockfish failed.");
    const waiters = this.readyWaiters;
    this.readyWaiters = [];
    for (const waiter of waiters) {
      waiter.reject(error);
    }
    const job = this.activeJob;
    if (job) {
      this._finishJob(job, { error });
    }
  }

  _finishJob(job, outcome) {
    if (job.settled) {
      return;
    }
    job.settled = true;
    if (job.stopTimer !== null) {
      clearTimeout(job.stopTimer);
      job.stopTimer = null;
    }
    if (this.activeJob === job) {
      this.activeJob = null;
    }
    if (outcome.error) {
      job.reject(outcome.error);
    } else {
      job.resolve(outcome);
    }
  }

  _terminateWorker() {
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    this.ready = false;
    this.initPhase = "uci";
    if (this.initTimer !== null) {
      clearTimeout(this.initTimer);
      this.initTimer = null;
    }
    const waiters = this.readyWaiters;
    this.readyWaiters = [];
    for (const waiter of waiters) {
      waiter.reject(new EngineError("Stockfish was stopped."));
    }
  }

  _messageText(event) {
    if (typeof event.data === "string") {
      return event.data;
    }
    if (event.data && typeof event.data.data === "string") {
      return event.data.data;
    }
    return "";
  }
}

function initializeAnalysis() {
  const root = document.querySelector("[data-analysis-root]");
  if (!root) {
    return;
  }

  const form = root.querySelector("[data-analysis-form]");
  const fenInput = root.querySelector("#analysis-fen");
  const moveInput = root.querySelector("[data-analysis-move-input]");
  const startButton = root.querySelector("[data-analysis-start]");
  const cancelButton = root.querySelector("[data-analysis-cancel]");
  const restartButton = root.querySelector("[data-analysis-restart]");
  const statusElement = root.querySelector("[data-analysis-status]");
  const errorElement = root.querySelector("[data-analysis-error]");
  const moveErrorElement = root.querySelector("[data-analysis-move-error]");
  const emptyState = root.querySelector("[data-analysis-empty]");
  const resultElement = root.querySelector("[data-analysis-result]");
  const boardImage = root.querySelector("[data-analysis-board]");
  const sideElement = root.querySelector("[data-analysis-side]");
  const evaluationElement = root.querySelector("[data-analysis-evaluation]");
  const terminalElement = root.querySelector("[data-analysis-terminal]");
  const telemetryElement = root.querySelector("[data-analysis-telemetry]");
  const depthElement = root.querySelector("[data-analysis-depth]");
  const npsElement = root.querySelector("[data-analysis-nps]");
  const rootMoveElement = root.querySelector("[data-analysis-root-move]");
  const assessmentSection = root.querySelector(
    "[data-analysis-assessment]"
  );
  const assessmentActual = root.querySelector("[data-assessment-actual]");
  const assessmentBest = root.querySelector("[data-assessment-best]");
  const assessmentQuality = root.querySelector("[data-assessment-quality]");
  const assessmentLossLabel = root.querySelector(
    "[data-assessment-loss-label]"
  );
  const assessmentCpl = root.querySelector("[data-assessment-cpl]");
  const assessmentOutcome = root.querySelector(
    "[data-assessment-outcome]"
  );
  const assessmentAfterFen = root.querySelector(
    "[data-assessment-after-fen]"
  );
  const linesSection = root.querySelector("[data-analysis-lines-section]");
  const linesElement = root.querySelector("[data-analysis-lines]");
  const playbackSection = root.querySelector("[data-analysis-playback]");
  const playbackStatus = root.querySelector("[data-playback-status]");
  const playbackPrevious = root.querySelector("[data-playback-prev]");
  const playbackNext = root.querySelector("[data-playback-next]");
  const playbackReset = root.querySelector("[data-playback-reset]");
  const workspaceStatus = root.querySelector("[data-workspace-status]");
  const workspaceRestore = root.querySelector("[data-workspace-restore]");
  const explanationSection = root.querySelector("[data-explanation-section]");
  const explanationOutput = root.querySelector("[data-explanation-output]");
  const explanationSummary = root.querySelector("[data-explanation-summary]");
  const explanationWhy = root.querySelector("[data-explanation-why]");
  const explanationAlternatives = root.querySelector("[data-explanation-alternatives]");
  const explanationUncertainty = root.querySelector("[data-explanation-uncertainty]");
  const explanationStale = root.querySelector("[data-explanation-stale]");
  const explanationPassphrase = root.querySelector("[data-explanation-passphrase]");
  const explanationStatus = root.querySelector("[data-explanation-status]");
  const explanationUsage = root.querySelector("[data-explanation-usage]");
  const positionSummaryPlan = root.querySelector("[data-position-summary-plan]");
  const positionSummaryPreview = root.querySelector("[data-position-summary-preview]");
  const positionSummaryConfirm = root.querySelector("[data-position-summary-confirm]");
  const positionSummaryCancel = root.querySelector("[data-position-summary-cancel]");
  const positionSummaryWarning = root.querySelector("[data-position-summary-warning]");
  const positionSummaryStatus = root.querySelector("[data-position-summary-status]");
  const positionSummaryResult = root.querySelector("[data-position-summary-result]");
  const positionSummaryText = root.querySelector("[data-position-summary-text]");
  const positionSummaryUncertainty = root.querySelector("[data-position-summary-uncertainty]");
  const positionSummaryStale = root.querySelector("[data-position-summary-stale]");
  const positionSummaryRetry = root.querySelector("[data-position-summary-retry]");
  const positionSummaryUsage = root.querySelector("[data-position-summary-usage]");

  const messages = {
    validating: root.dataset.labelValidating,
    preparing: root.dataset.labelPreparing,
    analyzing: root.dataset.labelAnalyzing,
    analyzingMove: root.dataset.labelAnalyzingMove,
    stopping: root.dataset.labelStopping,
    restarting: root.dataset.labelRestarting,
    complete: root.dataset.labelComplete,
    cancelled: root.dataset.labelCancelled,
    stopped: root.dataset.labelStopped,
    ready: root.dataset.labelReady,
    restored: "Restored from the local Analysis Workspace.",
    workspaceUnsaved: "Analysis results are not being saved locally.",
    workspaceCorrupt: "The saved Analysis Workspace could not be read.",
    workspaceInvalidated:
      "Saved engine results were discarded because engine settings changed.",
    errors: {
      placement_only: root.dataset.errorPlacementOnly,
      invalid_fen: root.dataset.errorInvalidFen,
      illegal_position: root.dataset.errorIllegalPosition,
      invalid_move: root.dataset.errorInvalidMove,
      invalid_origin: root.dataset.errorInvalidOrigin,
      invalid_request: root.dataset.errorInvalidRequest,
      request_too_large: root.dataset.errorRequestTooLarge,
      invalid_candidate_line: root.dataset.errorInvalidCandidateLine,
    },
    network: root.dataset.errorNetwork,
    engine: root.dataset.errorEngine,
    sides: {
      white: root.dataset.sideWhite,
      black: root.dataset.sideBlack,
    },
    mates: {
      white: root.dataset.mateWhite,
      black: root.dataset.mateBlack,
    },
    terminals: {
      "checkmate:white": root.dataset.terminalCheckmateWhite,
      "checkmate:black": root.dataset.terminalCheckmateBlack,
      stalemate: root.dataset.terminalStalemate,
      insufficient_material: root.dataset.terminalInsufficientMaterial,
      seventyfive_moves: root.dataset.terminalSeventyfiveMoves,
    },
    qualities: {
      best: root.dataset.qualityBest,
      excellent: root.dataset.qualityExcellent,
      good: root.dataset.qualityGood,
      inaccuracy: root.dataset.qualityInaccuracy,
      mistake: root.dataset.qualityMistake,
      blunder: root.dataset.qualityBlunder,
      forced: root.dataset.qualityForced,
    },
    centipawnLoss: root.dataset.labelCentipawnLoss,
    mateLoss: root.dataset.labelMateLoss,
    yes: root.dataset.labelYes,
  };

  const numberFormat = new Intl.NumberFormat(document.documentElement.lang);
  const analysisSettings = {
    preset: Object.hasOwn(PRESETS, STORED.preset) ? STORED.preset : "standard",
    multipv: MULTIPV,
    resource: STORED.resource === "full" ? "full" : "lite",
  };
  const runtimeElement = root.querySelector("[data-engine-runtime]");
  const resource = STORED.resource === "full" ? "Full" : "Lite";
  if (runtimeElement) {
    runtimeElement.textContent = `${resource} · single-threaded · ${Object.hasOwn(PRESETS, STORED.preset) ? STORED.preset : "standard"}`;
  }
  const engineUrl = STORED.resource === "full"
    ? "/static/vendor/stockfish/stockfish-19-single.js"
    : root.dataset.engineUrl;
  const engine = new EngineSupervisor(engineUrl, (info) => {
    if (root.dataset.analysisState === "preparing") {
      setStatus("analyzing");
    }
    if (root.dataset.analysisState !== "analyzing") {
      return;
    }
    updateTelemetry(info);
  });

  let runSequence = 0;
  let activeAbort = null;
  let activePosition = null;
  let busy = false;
  let activeAssessment = null;
  let activeResult = null;
  let lastRunInput = null;
  let summaryController = null;
  let summaryBusy = false;
  let cancellationRequested = false;
  let originalBoardUri = null;
  let currentLines = [];
  let selectedLineIndex = null;
  let playbackStep = 0;
  let playbackToken = 0;
  const boardCache = new Map();

  function setStatus(kind) {
    statusElement.textContent = messages[kind] || "";
    root.dataset.analysisState = kind;
  }

  function updateTelemetry(info) {
    if (info.depth !== null) {
      depthElement.textContent = String(info.depth);
    }
    if (info.nps !== null) {
      npsElement.textContent = numberFormat.format(info.nps);
    }
    if (info.rootMove) {
      rootMoveElement.textContent = info.rootMove;
    }
    telemetryElement.hidden = false;
  }

  function setWorkspaceStatus(text, isError = false) {
    if (!workspaceStatus) {
      return;
    }
    workspaceStatus.textContent = text;
    workspaceStatus.hidden = !text;
    workspaceStatus.dataset.error = isError ? "true" : "false";
  }

  function positionRecord(status, result = null) {
    return {
      kind: "position",
      input: {
        fen: lastRunInput?.fen || fenInput.value.trim(),
        move: lastRunInput?.move || moveInput.value.trim(),
      },
      settings: { ...analysisSettings },
      engine: engineSnapshot(analysisSettings),
      presentation: {
        language: document.documentElement.lang,
        audience: "club",
      },
      status,
      result,
    };
  }

  async function persistPosition(status, result = null) {
    if (!activePosition) {
      return;
    }
    const saved = await saveWorkspace(positionRecord(status, result));
    if (!saved.ok) {
      setWorkspaceStatus(messages.workspaceUnsaved, true);
    } else if (workspaceStatus?.dataset.error === "true") {
      setWorkspaceStatus("");
    }
  }

  function displaySavedResult(record) {
    const result = record.result;
    if (!result || !result.lines) {
      return false;
    }
    showPosition(result.position);
    if (result.position.terminal) {
      return true;
    }
    updateTelemetry(result.telemetry || { depth: null, nps: null, rootMove: null });
    renderLines(result.lines);
    activeResult = result;
    if (result.assessment) {
      activeAssessment = result.assessment;
      renderAssessment(result.assessment);
    }
    explanationSection.hidden = false;
    renderExplanation(result.explanation, result.explanationMeta);
    renderPositionSummary(result.summaryExplanation, result.summaryMeta);
    setStatus("restored");
    return true;
  }

  async function restorePositionWorkspace() {
    const loaded = await loadWorkspace();
    if (!loaded.ok) {
      setWorkspaceStatus(messages.workspaceCorrupt, true);
      return;
    }
    const record = loaded.record;
    if (!record || record.kind !== "position") {
      return;
    }
    if (!engineSettingsCompatible(record.engine, analysisSettings)) {
      const accepted = window.confirm(
        "Engine settings changed. Discard saved engine results and keep the input?"
      );
      if (!accepted) {
        return;
      }
      await clearWorkspace();
      fenInput.value = record.input.fen || "";
      moveInput.value = record.input.move || "";
      setWorkspaceStatus(messages.workspaceInvalidated, true);
      return;
    }
    fenInput.value = record.input.fen || "";
    moveInput.value = record.input.move || "";
    lastRunInput = {
      fen: fenInput.value.trim(),
      move: moveInput.value.trim(),
    };
    if (!displaySavedResult(record)) {
      setStatus("restored");
    }
    setBusy(false);
    if (workspaceRestore) {
      workspaceRestore.hidden = true;
    }
  }

  function clearError() {
    errorElement.textContent = "";
    errorElement.hidden = true;
  }

  function showError(text) {
    errorElement.textContent = text;
    errorElement.hidden = false;
  }

  function clearMoveError() {
    moveErrorElement.textContent = "";
    moveErrorElement.hidden = true;
  }

  function showMoveError(text) {
    moveErrorElement.textContent = text;
    moveErrorElement.hidden = false;
  }

  function clearErrors() {
    clearError();
    clearMoveError();
  }

  function setBusy(value) {
    busy = value;
    startButton.disabled = value;
    fenInput.disabled = value;
    moveInput.disabled = value;
    cancelButton.disabled = !value;
    restartButton.disabled = value
      ? false
      : !activePosition || activePosition.terminal !== null;
    root.dataset.busy = value ? "true" : "false";
  }

  function resetPlayback() {
    playbackToken += 1;
    currentLines = [];
    selectedLineIndex = null;
    playbackStep = 0;
    playbackSection.hidden = true;
    playbackStatus.textContent = "";
    playbackPrevious.disabled = true;
    playbackNext.disabled = true;
    playbackReset.disabled = true;
    delete resultElement.dataset.playback;
    for (const button of linesElement.querySelectorAll(
      "[data-candidate-line-button]"
    )) {
      button.setAttribute("aria-pressed", "false");
    }
  }

  function resetEngineDisplay() {
    evaluationElement.textContent = "";
    terminalElement.textContent = "";
    terminalElement.hidden = true;
    telemetryElement.hidden = true;
    depthElement.textContent = "";
    npsElement.textContent = "";
    rootMoveElement.textContent = "";
    assessmentSection.hidden = true;
    assessmentActual.textContent = "";
    assessmentBest.textContent = "";
    assessmentQuality.textContent = "";
    assessmentLossLabel.textContent = messages.centipawnLoss;
    assessmentCpl.textContent = "";
    assessmentOutcome.textContent = "";
    assessmentOutcome.hidden = true;
    assessmentAfterFen.textContent = "";
    linesElement.replaceChildren();
    linesSection.hidden = true;
    resetPlayback();
  }

  function terminalText(terminal) {
    if (!terminal) {
      return "";
    }
    const terminalKey =
      terminal.kind === "checkmate"
        ? `checkmate:${terminal.winner}`
        : terminal.kind;
    return messages.terminals[terminalKey] || "";
  }

  function showPosition(position) {
    activePosition = position;
    activeAssessment = null;
    activeResult = null;
    renderExplanation(null, null);
    renderPositionSummary(null, null);
    originalBoardUri = position.board_uri;
    boardCache.clear();
    boardCache.set(position.fen, position.board_uri);
    emptyState.hidden = true;
    resultElement.hidden = false;
    boardImage.src = position.board_uri;
    sideElement.textContent = messages.sides[position.side_to_move] || "";
    resetEngineDisplay();
    if (position.terminal) {
      terminalElement.textContent = terminalText(position.terminal);
      terminalElement.hidden = false;
      setStatus("complete");
    }
  }

  function renderAssessment(assessment) {
    assessmentSection.hidden = false;
    assessmentActual.textContent =
      `${assessment.actual_move.san} (${assessment.actual_move.uci})`;
    assessmentBest.textContent =
      `${assessment.best_move.san} (${assessment.best_move.uci})`;
    assessmentQuality.textContent =
      messages.qualities[assessment.quality] || assessment.quality;
    if (assessment.mate_loss) {
      assessmentLossLabel.textContent = messages.mateLoss;
      assessmentCpl.textContent = messages.yes;
    } else {
      assessmentLossLabel.textContent = messages.centipawnLoss;
      assessmentCpl.textContent = String(assessment.cpl);
    }
    assessmentAfterFen.textContent = assessment.actual_move.fen;
    assessmentOutcome.textContent = terminalText(assessment.terminal);
    assessmentOutcome.hidden = !assessment.terminal;
  }

  function renderExplanation(explanation, meta) {
    if (!explanation) {
      explanationSection.hidden = Boolean(
        !activePosition || activePosition.terminal
      );
      explanationOutput.hidden = true;
      explanationStale.hidden = true;
      return;
    }
    explanationSection.hidden = false;
    explanationOutput.hidden = false;
    explanationSummary.textContent = explanation.summary;
    explanationWhy.textContent = explanation.why;
    explanationAlternatives.textContent = explanation.alternatives;
    explanationUncertainty.textContent = explanation.uncertainty;
    const profile = loadLlmProfile();
    explanationStale.hidden =
      !profile ||
      !meta ||
      (profile.language === meta.language && profile.audience === meta.audience);
  }

  function renderPositionSummary(summary, meta) {
    if (!summary) {
      positionSummaryResult.hidden = true;
      return;
    }
    positionSummaryResult.hidden = false;
    positionSummaryText.textContent = summary.summary;
    positionSummaryUncertainty.textContent = summary.uncertainty;
    positionSummaryRetry.hidden = true;
    const profile = loadLlmProfile();
    positionSummaryStale.hidden =
      !profile ||
      !meta ||
      (profile.language === meta.language && profile.audience === meta.audience);
  }

  async function ensureLlmProfileUnlocked() {
    const profile = loadLlmProfile();
    if (!profile) {
      positionSummaryStatus.textContent = llmText.configure_profile;
      return null;
    }
    if (!profileRequestReady(profile)) {
      positionSummaryStatus.textContent = llmText.confirm_request;
      return null;
    }
    if (!llmUnlocked()) {
      const unlocked = await unlockLlm(explanationPassphrase.value, profile);
      if (!unlocked.ok) {
        positionSummaryStatus.textContent = llmText.unlock_failed;
        return null;
      }
    }
    return profile;
  }

  async function generatePositionSummary() {
    if (summaryBusy || !activePosition) return;
    const profile = await ensureLlmProfileUnlocked();
    if (!profile) return;
    summaryBusy = true;
    summaryController = new AbortController();
    positionSummaryConfirm.hidden = true;
    positionSummaryCancel.hidden = false;
    positionSummaryWarning.hidden = false;
    positionSummaryStatus.textContent = "Generating summary…";
    try {
      const result = await requestOverallSummary(
        profile,
        {
          fen: activePosition.fen,
          side_to_move: activePosition.side_to_move,
          evaluation: currentLines[0]?.evaluation || null,
          candidate_lines: currentLines,
          assessment: activeAssessment,
          actual_move: activePosition.actual_move,
        },
        summaryController.signal
      );
      if (!result.ok) {
        positionSummaryStatus.textContent = llmFailureText(
          result.code,
          result.code === "cancelled"
            ? "Summary cancelled. Submitted requests may still finish."
            : "Summary failed. You can retry this summary request manually."
        );
        positionSummaryConfirm.hidden = false;
        positionSummaryRetry.hidden = false;
        return;
      }
      const meta = { language: profile.language, audience: profile.audience };
      positionSummaryUsage.textContent = result.usage
        ? `Tokens · prompt ${result.usage.prompt} · completion ${result.usage.completion} · reasoning ${result.usage.reasoning} · total ${result.usage.total}`
        : "";
      renderPositionSummary(result.summary, meta);
      if (activeResult) {
        activeResult = {
          ...activeResult,
          summaryExplanation: result.summary,
          summaryMeta: meta,
        };
        await persistPosition("complete", activeResult);
      }
      positionSummaryStatus.textContent = "Summary ready.";
    } finally {
      summaryBusy = false;
      summaryController = null;
      positionSummaryCancel.hidden = true;
    }
  }

  function evaluationText(evaluation) {
    if (evaluation.kind !== "mate") {
      return evaluation.text;
    }
    const matingSide = evaluation.value > 0 ? messages.mates.white : messages.mates.black;
    return `${evaluation.text} · ${matingSide}`;
  }

  function renderLines(lines) {
    if (lines.length === 0) {
      throw new EngineError("No Candidate Lines were returned.");
    }
    resetPlayback();
    currentLines = lines;
    evaluationElement.textContent = evaluationText(lines[0].evaluation);
    for (const [index, line] of lines.entries()) {
      const item = document.createElement("li");
      item.className = "candidate-line";
      item.dataset.candidateLine = "";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "candidate-line-button";
      button.dataset.candidateLineButton = "";
      button.dataset.lineIndex = String(index);
      button.setAttribute("aria-pressed", "false");

      const header = document.createElement("span");
      header.className = "candidate-line-header";

      const rank = document.createElement("span");
      rank.className = "candidate-line-rank";
      rank.textContent = `#${line.rank}`;

      const evaluation = document.createElement("span");
      evaluation.className = "candidate-line-evaluation";
      evaluation.textContent = line.evaluation.text;

      const moves = document.createElement("span");
      moves.className = "candidate-line-moves";
      moves.textContent = line.moves.map((move) => move.san).join(" ");

      header.append(rank, evaluation);
      button.append(header, moves);
      item.append(button);
      linesElement.append(item);
    }
    linesSection.hidden = false;
    if (!rootMoveElement.textContent) {
      rootMoveElement.textContent = lines[0].moves[0].uci;
    }
    if (!depthElement.textContent) {
      depthElement.textContent = String(lines[0].depth);
    }
    telemetryElement.hidden = false;
  }

  async function showPlaybackBoard() {
    const token = (playbackToken += 1);
    if (selectedLineIndex === null) {
      return;
    }
    if (playbackStep === 0) {
      boardImage.src = originalBoardUri;
      return;
    }
    const line = currentLines[selectedLineIndex];
    const move = line.moves[playbackStep - 1];
    const cached = boardCache.get(move.fen);
    if (cached) {
      boardImage.src = cached;
      return;
    }
    try {
      const position = await postJson(
        root.dataset.positionEndpoint,
        { fen: move.fen }
      );
      boardCache.set(move.fen, position.board_uri);
      if (token === playbackToken) {
        boardImage.src = position.board_uri;
      }
    } catch (error) {
      if (token === playbackToken) {
        showError(messages.network);
      }
    }
  }

  async function updatePlayback() {
    if (selectedLineIndex === null) {
      return;
    }
    const line = currentLines[selectedLineIndex];
    const currentMove =
      playbackStep > 0 ? line.moves[playbackStep - 1] : null;
    playbackSection.hidden = false;
    if (playbackStep > 0) {
      resultElement.dataset.playback = "true";
    } else {
      delete resultElement.dataset.playback;
    }
    playbackStatus.textContent =
      `#${line.rank} · ${playbackStep}/${line.moves.length}` +
      (currentMove ? ` · ${currentMove.san}` : "");
    playbackPrevious.disabled = playbackStep === 0;
    playbackNext.disabled = playbackStep >= line.moves.length;
    playbackReset.disabled = playbackStep === 0;
    await showPlaybackBoard();
  }

  function selectLine(index) {
    if (index < 0 || index >= currentLines.length) {
      return;
    }
    selectedLineIndex = index;
    playbackStep = 1;
    for (const button of linesElement.querySelectorAll(
      "[data-candidate-line-button]"
    )) {
      button.setAttribute(
        "aria-pressed",
        button.dataset.lineIndex === String(index) ? "true" : "false"
      );
    }
    void updatePlayback();
  }

  async function postJson(url, body, signal = null) {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "same-origin",
        signal,
      });
    } catch (error) {
      if (error.name === "AbortError") {
        throw error;
      }
      throw new AnalysisRequestError("network");
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      if (!response.ok) {
        throw new AnalysisRequestError("network");
      }
    }
    if (!response.ok) {
      throw new AnalysisRequestError(payload?.error?.code || "network");
    }
    return payload;
  }

  function stopIfStale(runId) {
    if (runId !== runSequence) {
      return true;
    }
    if (cancellationRequested) {
      setStatus("cancelled");
      return true;
    }
    return false;
  }

  async function startRun(fen, actualMove) {
    const runId = (runSequence += 1);
    cancellationRequested = false;
    if (activeAbort) {
      activeAbort.abort();
    }
    const controller = new AbortController();
    activeAbort = controller;
    clearErrors();
    setBusy(true);
    setStatus("validating");

    try {
      const request = { fen };
      if (actualMove) {
        request.move = actualMove;
      }
      const position = await postJson(
        root.dataset.positionEndpoint,
        request,
        controller.signal
      );
      if (stopIfStale(runId)) {
        return;
      }
      showPosition(position);
      lastRunInput = { fen: position.fen, move: actualMove || "" };
      if (position.terminal) {
        await persistPosition("complete", {
          position,
          lines: [],
          assessment: null,
          telemetry: null,
        });
        return;
      }
      await persistPosition("incomplete");

      setStatus("preparing");
      const beforeResult = await engine.analyze(position.fen);
      if (stopIfStale(runId)) {
        return;
      }
      if (beforeResult.cancelled) {
        setStatus("cancelled");
        return;
      }
      updateTelemetry(beforeResult);

      if (position.actual_move) {
        let afterResult = null;
        if (!position.actual_move.terminal) {
          setStatus("analyzing-move");
          afterResult = await engine.analyze(
            position.actual_move.after_fen
          );
          if (stopIfStale(runId)) {
            return;
          }
          if (afterResult.cancelled) {
            setStatus("cancelled");
            return;
          }
        }
        const assessed = await postJson(
          root.dataset.assessEndpoint,
          {
            fen: position.fen,
            move: position.actual_move.uci,
            lines: beforeResult.lines,
            after_lines: afterResult ? afterResult.lines : [],
          },
          controller.signal
        );
        if (stopIfStale(runId)) {
          return;
        }
        renderLines(assessed.lines);
        activeAssessment = assessed.assessment;
        renderAssessment(assessed.assessment);
        activeResult = {
          position,
          lines: assessed.lines,
          assessment: assessed.assessment,
          explanation: null,
          explanationMeta: null,
          telemetry: {
            depth: beforeResult.depth,
            nps: beforeResult.nps,
            rootMove: beforeResult.rootMove,
          },
        };
        await persistPosition("complete", activeResult);
        explanationSection.hidden = false;
        renderExplanation(null, null);
      } else {
        const normalized = await postJson(
          root.dataset.linesEndpoint,
          { fen: position.fen, lines: beforeResult.lines },
          controller.signal
        );
        if (stopIfStale(runId)) {
          return;
        }
        renderLines(normalized.lines);
        activeResult = {
          position,
          lines: normalized.lines,
          assessment: null,
          explanation: null,
          explanationMeta: null,
          telemetry: {
            depth: beforeResult.depth,
            nps: beforeResult.nps,
            rootMove: beforeResult.rootMove,
          },
        };
        await persistPosition("complete", activeResult);
        explanationSection.hidden = false;
        renderExplanation(null, null);
      }
      setStatus("complete");
    } catch (error) {
      if (runId !== runSequence) {
        return;
      }
      if (error.name === "AbortError" || cancellationRequested) {
        setStatus("cancelled");
        return;
      }
      if (
        error instanceof AnalysisRequestError &&
        error.code === "invalid_move"
      ) {
        showMoveError(messages.errors.invalid_move);
        setStatus("ready");
        return;
      }
      if (error instanceof AnalysisRequestError) {
        showError(messages.errors[error.code] || messages.network);
        setStatus("ready");
      } else {
        showError(messages.engine);
        setStatus("stopped");
      }
    } finally {
      if (runId === runSequence) {
        activeAbort = null;
        setBusy(false);
      }
    }
  }

  async function cancelCurrentRun() {
    if (!busy) {
      return;
    }
    cancellationRequested = true;
    setStatus("stopping");
    if (activeAbort) {
      activeAbort.abort();
    }
    if (engine.busy) {
      try {
        await engine.cancel();
      } catch (error) {
        /* The active run surfaces the engine failure. */
      }
    }
  }

  async function restartCurrentRun() {
    if (!activePosition) {
      return;
    }
    if (!window.confirm("Restart analysis and clear saved results?")) {
      return;
    }
    const fen = activePosition.fen;
    const actualMove = activePosition.actual_move
      ? activePosition.actual_move.uci
      : null;
    runSequence += 1;
    if (activeAbort) {
      activeAbort.abort();
    }
    if (engine.busy) {
      setStatus("restarting");
      try {
        await engine.cancel();
      } catch (error) {
        /* A failed old Worker is rebuilt by the new run. */
      }
    }
    await startRun(fen, actualMove);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const fen = fenInput.value.trim();
    if (!fen) {
      fenInput.reportValidity();
      return;
    }
    const move = moveInput.value.trim();
    if (
      lastRunInput &&
      (lastRunInput.fen !== fen || lastRunInput.move !== move) &&
      !window.confirm("Replace the saved Analysis Workspace with this input?")
    ) {
      return;
    }
    startRun(fen, move);
  });

  cancelButton.addEventListener("click", () => {
    cancelCurrentRun();
  });

  restartButton.addEventListener("click", () => {
    restartCurrentRun();
  });

  linesElement.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const button = event.target.closest("[data-candidate-line-button]");
    if (!button) {
      return;
    }
    selectLine(Number.parseInt(button.dataset.lineIndex || "", 10));
  });

  playbackPrevious.addEventListener("click", () => {
    if (selectedLineIndex === null || playbackStep <= 0) {
      return;
    }
    playbackStep -= 1;
    void updatePlayback();
  });

  playbackNext.addEventListener("click", () => {
    if (selectedLineIndex === null) {
      return;
    }
    const line = currentLines[selectedLineIndex];
    playbackStep = Math.min(playbackStep + 1, line.moves.length);
    void updatePlayback();
  });

  playbackReset.addEventListener("click", () => {
    if (selectedLineIndex === null) {
      return;
    }
    playbackStep = 0;
    void updatePlayback();
  });

  const llmText = Object.fromEntries(
    [...document.querySelectorAll("[data-llm-message]")].map((node) => [
      node.dataset.llmMessage,
      node.textContent,
    ])
  );
  const llmFailureText = (code, fallback) =>
    reasoningFailureText(code, llmText) || fallback;
  let explanationBusy = false;
  window.addEventListener("chessfen:llm-lock", () => {
    explanationStatus.textContent = llmText.locked_timeout;
  });
  root.querySelector("[data-explanation-generate]")?.addEventListener(
    "click",
    async () => {
      if (explanationBusy) return;
      explanationBusy = true;
      const profile = loadLlmProfile();
      if (!profile) {
        explanationStatus.textContent =
          llmText.configure_profile;
        return;
      }
      if (!profileRequestReady(profile)) {
        explanationStatus.textContent =
          llmText.confirm_request;
        return;
      }
      if (!llmUnlocked()) {
        const unlocked = await unlockLlm(
          explanationPassphrase.value,
          profile
        );
        if (!unlocked.ok) {
          explanationStatus.textContent =
            llmText.unlock_failed;
          return;
        }
      }
      explanationStatus.textContent = llmText.generating;
      try {
        const result = await requestExplanation(profile, {
        fen: activePosition.fen,
        side_to_move: activePosition.side_to_move,
        candidate_lines: currentLines,
        assessment: activeAssessment,
        actual_move: activePosition.actual_move,
      });
      if (!result.ok) {
        explanationStatus.textContent = llmFailureText(
          result.code,
          result.code === "provider_error"
            ? "The Provider rejected the request. Confirm the Model and Reasoning settings."
            : "Explanation could not be generated."
        );
        return;
      }
      const meta = {
        language: profile.language,
        audience: profile.audience,
      };
      renderExplanation(result.explanation, meta);
      if (activeResult) {
        activeResult = {
          ...activeResult,
          explanation: result.explanation,
          explanationMeta: meta,
        };
        await persistPosition("complete", activeResult);
      }
        explanationUsage.textContent = result.usage
        ? `Tokens · prompt ${result.usage.prompt} · completion ${result.usage.completion} · reasoning ${result.usage.reasoning} · total ${result.usage.total}`
        : "";
      explanationStatus.textContent = llmText.explanation_ready;
      } finally {
        explanationBusy = false;
      }
    }
  );
  root.querySelector("[data-explanation-lock]")?.addEventListener("click", () => {
    lockLlm();
    explanationStatus.textContent = llmText.locked;
  });

  positionSummaryPreview?.addEventListener("click", () => {
    positionSummaryPlan.textContent =
      "1 overall Position summary request. No batch requests.";
    positionSummaryConfirm.hidden = false;
    positionSummaryStatus.textContent =
      "Review the request count, then confirm the summary.";
  });
  positionSummaryConfirm?.addEventListener("click", generatePositionSummary);
  positionSummaryRetry?.addEventListener("click", generatePositionSummary);
  positionSummaryCancel?.addEventListener("click", () => {
    summaryController?.abort();
    positionSummaryStatus.textContent =
      "Cancelling… submitted requests may still finish and incur charges.";
    positionSummaryWarning.hidden = false;
  });

  void restorePositionWorkspace();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeAnalysis);
} else {
  initializeAnalysis();
}

export { EngineSupervisor };
