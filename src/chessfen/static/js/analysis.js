"use strict";

const MULTIPV = 3;
const SEARCH_DEPTH = 18;
const SEARCH_MOVETIME_MS = 2000;
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
  const startButton = root.querySelector("[data-analysis-start]");
  const cancelButton = root.querySelector("[data-analysis-cancel]");
  const restartButton = root.querySelector("[data-analysis-restart]");
  const statusElement = root.querySelector("[data-analysis-status]");
  const errorElement = root.querySelector("[data-analysis-error]");
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
  const linesSection = root.querySelector("[data-analysis-lines-section]");
  const linesElement = root.querySelector("[data-analysis-lines]");

  const messages = {
    validating: root.dataset.labelValidating,
    preparing: root.dataset.labelPreparing,
    analyzing: root.dataset.labelAnalyzing,
    stopping: root.dataset.labelStopping,
    restarting: root.dataset.labelRestarting,
    complete: root.dataset.labelComplete,
    cancelled: root.dataset.labelCancelled,
    stopped: root.dataset.labelStopped,
    ready: root.dataset.labelReady,
    errors: {
      placement_only: root.dataset.errorPlacementOnly,
      invalid_fen: root.dataset.errorInvalidFen,
      illegal_position: root.dataset.errorIllegalPosition,
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
  };

  const numberFormat = new Intl.NumberFormat(document.documentElement.lang);
  const engine = new EngineSupervisor(root.dataset.engineUrl, (info) => {
    if (root.dataset.analysisState === "preparing") {
      setStatus("analyzing");
    }
    updateTelemetry(info);
  });

  let runSequence = 0;
  let activeAbort = null;
  let activePosition = null;
  let busy = false;

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

  function clearError() {
    errorElement.textContent = "";
    errorElement.hidden = true;
  }

  function showError(text) {
    errorElement.textContent = text;
    errorElement.hidden = false;
  }

  function setBusy(value) {
    busy = value;
    startButton.disabled = value;
    cancelButton.disabled = !value;
    restartButton.disabled = value
      ? false
      : !activePosition || activePosition.terminal !== null;
    root.dataset.busy = value ? "true" : "false";
  }

  function resetEngineDisplay() {
    evaluationElement.textContent = "";
    terminalElement.textContent = "";
    terminalElement.hidden = true;
    telemetryElement.hidden = true;
    depthElement.textContent = "";
    npsElement.textContent = "";
    rootMoveElement.textContent = "";
    linesElement.replaceChildren();
    linesSection.hidden = true;
  }

  function showPosition(position) {
    activePosition = position;
    emptyState.hidden = true;
    resultElement.hidden = false;
    boardImage.src = position.board_uri;
    sideElement.textContent = messages.sides[position.side_to_move] || "";
    resetEngineDisplay();
    if (position.terminal) {
      const terminalKey =
        position.terminal.kind === "checkmate"
          ? `checkmate:${position.terminal.winner}`
          : position.terminal.kind;
      terminalElement.textContent = messages.terminals[terminalKey] || "";
      terminalElement.hidden = false;
      setStatus("complete");
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
    evaluationElement.textContent = evaluationText(lines[0].evaluation);
    for (const line of lines) {
      const item = document.createElement("li");
      item.className = "candidate-line";
      item.dataset.candidateLine = "";

      const header = document.createElement("div");
      header.className = "candidate-line-header";

      const rank = document.createElement("span");
      rank.className = "candidate-line-rank";
      rank.textContent = `#${line.rank}`;

      const evaluation = document.createElement("span");
      evaluation.className = "candidate-line-evaluation";
      evaluation.textContent = line.evaluation.text;

      const moves = document.createElement("p");
      moves.className = "candidate-line-moves";
      moves.textContent = line.moves.map((move) => move.san).join(" ");

      header.append(rank, evaluation);
      item.append(header, moves);
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

  async function postJson(url, body, signal) {
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

  async function startRun(fen) {
    const runId = (runSequence += 1);
    if (activeAbort) {
      activeAbort.abort();
    }
    const controller = new AbortController();
    activeAbort = controller;
    clearError();
    setBusy(true);
    setStatus("validating");

    try {
      const position = await postJson(
        root.dataset.positionEndpoint,
        { fen },
        controller.signal
      );
      if (runId !== runSequence) {
        return;
      }
      showPosition(position);
      if (position.terminal) {
        return;
      }

      setStatus("preparing");
      const result = await engine.analyze(position.fen);
      if (runId !== runSequence) {
        return;
      }
      if (result.cancelled) {
        setStatus("cancelled");
        return;
      }
      updateTelemetry(result);

      const normalized = await postJson(
        root.dataset.linesEndpoint,
        { fen: position.fen, lines: result.lines },
        controller.signal
      );
      if (runId !== runSequence) {
        return;
      }
      renderLines(normalized.lines);
      setStatus("complete");
    } catch (error) {
      if (runId !== runSequence) {
        return;
      }
      if (error.name === "AbortError") {
        setStatus("cancelled");
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
    const fen = activePosition.fen;
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
    await startRun(fen);
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const fen = fenInput.value.trim();
    if (!fen) {
      fenInput.reportValidity();
      return;
    }
    startRun(fen);
  });

  cancelButton.addEventListener("click", () => {
    cancelCurrentRun();
  });

  restartButton.addEventListener("click", () => {
    restartCurrentRun();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeAnalysis);
} else {
  initializeAnalysis();
}
