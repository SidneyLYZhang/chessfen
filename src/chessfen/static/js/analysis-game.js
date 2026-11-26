"use strict";

import {
  llmUnlocked,
  loadLlmProfile,
  lockLlm,
  profileRequestReady,
  llmFailureMessage,
  requestExplanation,
  requestKeyPlyBatch,
  requestOverallSummary,
  unlockLlm,
} from "/static/js/analysis-llm.js";
import {
  clearWorkspace,
  engineSettingsCompatible,
  engineSnapshot,
  loadWorkspace,
  saveWorkspace,
  workspaceIsActive,
} from "/static/js/analysis-workspace.js";
import {
  buildGameMarkdown,
  collectExportText,
  downloadText,
  exportPgn,
} from "/static/js/analysis-export.js";
import { EngineSupervisor, engineCandidates } from "/static/js/analysis.js";
import { credentialSecurityAvailable } from "/static/js/analysis-credential.js";
import { readMessages } from "/static/js/ui-messages.js";

function initializeGameAnalysis() {
  const root = document.querySelector("[data-game-root]");
  if (!root) {
    return;
  }
  const game = JSON.parse(root.dataset.game);
  const startButton = root.querySelector("[data-game-start]");
  const cancelButton = root.querySelector("[data-game-cancel]");
  const continueButton = root.querySelector("[data-game-continue]");
  const restartButton = root.querySelector("[data-game-restart]");
  const status = root.querySelector("[data-game-status]");
  const error = root.querySelector("[data-game-error]");
  const empty = document.querySelector("[data-game-empty]");
  const result = document.querySelector("[data-game-result]");
  const board = document.querySelector("[data-game-board]");
  const evaluation = document.querySelector("[data-game-evaluation]");
  const quality = document.querySelector("[data-game-quality]");
  const linesElement = document.querySelector("[data-game-lines]");
  const playback = document.querySelector("[data-game-playback]");
  const playbackStatus = document.querySelector("[data-game-playback-status]");
  const playbackPrev = document.querySelector("[data-game-playback-prev]");
  const playbackNext = document.querySelector("[data-game-playback-next]");
  const playbackReset = document.querySelector("[data-game-playback-reset]");
  const exactElement = document.querySelector("[data-game-exact]");
  const indicatorsElement = document.querySelector("[data-game-indicators]");
  const ribbon = document.querySelector("[data-game-ribbon]");
  const graph = document.querySelector("[data-game-graph]");
  const moveList = document.querySelector("[data-game-moves]");
  const total = game.positions.length;
  const completed = new Array(total).fill(null);
  const qualityLabels = {
    best: root.dataset.qualityBest,
    excellent: root.dataset.qualityExcellent,
    good: root.dataset.qualityGood,
    inaccuracy: root.dataset.qualityInaccuracy,
    mistake: root.dataset.qualityMistake,
    blunder: root.dataset.qualityBlunder,
    forced: root.dataset.qualityForced,
  };
  let analysis = null;
  let runToken = 0;
  let cancelled = false;
  let busy = false;
  let hasStarted = false;
  let activePly = 0;
  let activeCandidateLines = [];
  let selectedLineIndex = null;
  let playbackStep = 0;
  let playbackToken = 0;
  const normalizedLines = new Map();
  let explanationBusy = false;
  const llmText = readMessages("llm-message");
  const uiText = readMessages("ui-message");
  const failureMessage = (code, fallback) =>
    llmFailureMessage(code, llmText) || fallback;
  const explanations = {};
  let summaryExplanation = null;
  let summaryMeta = null;
  let batchController = null;
  let batchBatches = null;
  let pendingBatchIndex = null;
  let lastSummaryProfile = null;
  const usageTotals = { prompt: 0, completion: 0, reasoning: 0, total: 0 };
  window.addEventListener("chessfen:llm-lock", () => {
    explanationStatus.textContent = llmText.locked_timeout;
  });
  const labels = {
    analyzing: root.dataset.statusAnalyzing,
    complete: root.dataset.statusComplete,
    cancelled: root.dataset.statusCancelled,
    stopping: root.dataset.statusStopping,
    ready: root.dataset.statusReady,
    generic: root.dataset.errorGeneric,
    board: root.dataset.errorBoard,
    graph: root.dataset.labelGraph,
    key: root.dataset.labelKey,
    mateWhite: root.dataset.mateWhite,
    mateBlack: root.dataset.mateBlack,
  };
  const boardCache = new Map();
  const stored = (() => {
    try { return JSON.parse(window.localStorage.getItem("chessfen.analysis.settings.v1") || "{}"); }
    catch (error) { return {}; }
  })();
  const workspaceStatus = document.querySelector("[data-workspace-status]");
  const explanationSection = document.querySelector("[data-game-explanation-section]");
  const explanationOutput = document.querySelector("[data-game-explanation-output]");
  const explanationSummary = document.querySelector("[data-game-explanation-summary]");
  const explanationWhy = document.querySelector("[data-game-explanation-why]");
  const explanationAlternatives = document.querySelector("[data-game-explanation-alternatives]");
  const explanationUncertainty = document.querySelector("[data-game-explanation-uncertainty]");
  const explanationStale = document.querySelector("[data-game-explanation-stale]");
  const explanationPassphrase = document.querySelector("[data-game-explanation-passphrase]");
  const explanationStatus = document.querySelector("[data-game-explanation-status]");
  const explanationUsage = document.querySelector("[data-game-explanation-usage]");
  const batchPlan = document.querySelector("[data-game-batch-plan]");
  const batchPreview = document.querySelector("[data-game-batch-preview]");
  const batchGenerate = document.querySelector("[data-game-batch-generate]");
  const batchCancel = document.querySelector("[data-game-batch-cancel]");
  const batchRetry = document.querySelector("[data-game-batch-retry]");
  const batchWarning = document.querySelector("[data-game-batch-warning]");
  const batchStatus = document.querySelector("[data-game-batch-status]");
  const tokenUsage = document.querySelector("[data-game-token-usage]");
  const summarySection = document.querySelector("[data-game-overall-summary]");
  const summaryText = document.querySelector("[data-game-summary-text]");
  const summaryUncertainty = document.querySelector("[data-game-summary-uncertainty]");
  const summaryStale = document.querySelector("[data-game-summary-stale]");
  const summaryRetry = document.querySelector("[data-game-summary-retry]");
  const exportExplanations = document.querySelector(
    "[data-game-export-explanations]"
  );
  const exportPgnButton = document.querySelector("[data-game-export-pgn]");
  const exportMarkdownButton = document.querySelector(
    "[data-game-export-markdown]"
  );
  const exportStatus = document.querySelector("[data-game-export-status]");
  const exportText = collectExportText();
  const workspaceRestore = document.querySelector("[data-workspace-restore]");
  const analysisSettings = {
    preset: ["fast", "standard", "deep"].includes(stored.preset)
      ? stored.preset
      : "standard",
    multipv:
      Number.isInteger(stored.multipv) && stored.multipv >= 1 && stored.multipv <= 5
        ? stored.multipv
        : 3,
    resource: stored.resource === "full" ? "full" : "lite",
  };
  const runtime = document.querySelector("[data-engine-runtime]");
  const runtimeText = {
    checking: root.dataset.runtimeChecking,
    multithreaded: root.dataset.runtimeMultithreaded,
    single: root.dataset.runtimeSingle,
    mixed: root.dataset.runtimeMixed,
  };
  const engineCandidatesForSettings = engineCandidates(
    analysisSettings.resource
  );
  let engineRuntime = engineCandidatesForSettings[0].runtime;
  let mixedRuntime = false;
  let runtimeFellBack = false;
  function paintRuntime(value, mixed = false) {
    if (!runtime) return;
    const label =
      value === "multithreaded"
        ? runtimeText.multithreaded
        : value === "checking"
          ? runtimeText.checking
          : runtimeText.single;
    runtime.textContent =
      `${analysisSettings.resource === "full" ? "Full" : "Lite"} · ${label}` +
      (mixed ? ` · ${runtimeText.mixed}` : "") +
      ` · ${analysisSettings.preset}`;
  }
  paintRuntime("checking");
  const credentialFeaturesAvailable = credentialSecurityAvailable();
  const engine = new EngineSupervisor(
    engineCandidatesForSettings,
    (info) => {
      if (busy && info.depth !== null) {
        status.textContent =
          `${labels.analyzing} ${completed.filter(Boolean).length + 1}/${total}` +
          ` · depth ${info.depth}`;
      }
    },
    {
      onRuntime: ({ runtime: value, mixed, fellBack }) => {
        engineRuntime = value;
        mixedRuntime = mixed;
        runtimeFellBack = runtimeFellBack || fellBack;
        paintRuntime(value, mixed);
        if (mixed) {
          setWorkspaceStatus(root.dataset.runtimeMixedMessage, false);
        } else if (fellBack) {
          setWorkspaceStatus(root.dataset.runtimeFallbackMessage, false);
        }
      },
    }
  );

  function setWorkspaceStatus(text, isError = false) {
    if (!workspaceStatus) return;
    workspaceStatus.textContent = text;
    workspaceStatus.hidden = !text;
    workspaceStatus.dataset.error = isError ? "true" : "false";
  }

  function gameRecord(status, result = analysis) {
    const form = document.querySelector(".pgn-form");
    const pgn = form?.querySelector("#pgn")?.value || "";
    const gameIndex = form?.querySelector("#game_index")?.value || "";
    return {
      kind: "game",
      input: { pgn, gameIndex },
      settings: { ...analysisSettings },
      engine: {
        ...engineSnapshot(
          analysisSettings,
          engineRuntime,
          mixedRuntime,
          runtimeFellBack,
        ),
        completed,
      },
      presentation: {
        language: document.documentElement.lang,
        audience: "club",
      },
      status,
      result,
      gameData: game,
      explanations,
      summaryExplanation,
      summaryMeta,
      usageTotals,
    };
  }

  async function persistGame(status, result = analysis) {
    const saved = await saveWorkspace(gameRecord(status, result));
    if (!saved.ok) {
      setWorkspaceStatus(
        uiText.workspace_unsaved,
        true
      );
    } else if (workspaceStatus?.dataset.error === "true") {
      setWorkspaceStatus("");
    }
  }

  function setBusy(value) {
    busy = value;
    startButton.disabled = value;
    cancelButton.disabled = !value;
    continueButton.disabled =
      value || !hasStarted || completed.every(Boolean);
    restartButton.disabled = value;
  }

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.error?.code || "network");
    }
    return payload;
  }

  async function showBoard(fen) {
    if (boardCache.has(fen)) {
      board.src = boardCache.get(fen);
      return;
    }
    const position = await postJson(root.dataset.positionEndpoint, { fen });
    boardCache.set(fen, position.board_uri);
    board.src = position.board_uri;
  }

  function renderGameExplanation(ply) {
    if (!credentialFeaturesAvailable) {
      explanationSection.hidden = true;
      explanationOutput.hidden = true;
      explanationStale.hidden = true;
      explanationUsage.textContent = "";
      return;
    }
    if (ply <= 0) {
      explanationSection.hidden = !analysis;
      explanationOutput.hidden = true;
      explanationStale.hidden = true;
      explanationUsage.textContent = "";
      return;
    }
    explanationSection.hidden = false;
    const stored = explanations[ply];
    if (!stored) {
      explanationOutput.hidden = true;
      explanationStale.hidden = true;
      explanationUsage.textContent = "";
      return;
    }
    explanationOutput.hidden = false;
    explanationSummary.textContent = stored.explanation.summary;
    explanationWhy.textContent = stored.explanation.why;
    explanationAlternatives.textContent = stored.explanation.alternatives;
    explanationUncertainty.textContent = stored.explanation.uncertainty;
    explanationUsage.textContent = usageText(stored.usage);
    const profile = loadLlmProfile();
    explanationStale.hidden =
      !profile ||
      (profile.language === stored.meta.language &&
        profile.audience === stored.meta.audience);
  }

  function evaluationLabel(evaluation) {
    if (evaluation.kind !== "mate" || evaluation.value === 0) {
      return evaluation.text;
    }
    return (
      `${evaluation.text} · ` +
      (evaluation.value > 0 ? labels.mateWhite : labels.mateBlack)
    );
  }

  async function renderPlaybackStep() {
    if (selectedLineIndex === null) {
      return;
    }
    const line = activeCandidateLines[selectedLineIndex];
    if (!line) {
      return;
    }
    const token = (playbackToken += 1);
    const fen =
      playbackStep === 0
        ? game.positions[activePly - 1]
        : line.moves[playbackStep - 1].fen;
    await showBoard(fen);
    if (token !== playbackToken) {
      return;
    }
    evaluation.textContent = evaluationLabel(line.evaluation);
    quality.textContent = "";
    playbackStatus.textContent =
      `#${line.rank} · ${playbackStep}/${line.moves.length}`;
    playbackPrev.disabled = playbackStep === 0;
    playbackNext.disabled = playbackStep >= line.moves.length;
  }

  async function startCandidatePlayback(index) {
    if (!activeCandidateLines[index] || activePly <= 0) {
      return;
    }
    selectedLineIndex = index;
    playbackStep = 0;
    playback.hidden = false;
    for (const item of linesElement.querySelectorAll("[data-line-index]")) {
      item.setAttribute(
        "aria-pressed",
        item.dataset.lineIndex === String(index) ? "true" : "false"
      );
    }
    await renderPlaybackStep();
  }

  async function selectPly(ply) {
    activePly = ply;
    activeCandidateLines = [];
    selectedLineIndex = null;
    playbackStep = 0;
    playbackToken += 1;
    playback.hidden = true;
    playbackStatus.textContent = "";
    renderGameExplanation(ply);
    const index = Math.max(0, ply);
    await showBoard(game.positions[index]);
    evaluation.textContent = evaluationLabel(analysis.evaluations[index]);
    quality.textContent = "";
    linesElement.replaceChildren();
    exactElement.textContent = "";
    indicatorsElement.textContent = "";
    if (ply > 0) {
      const data = analysis.plies[ply - 1];
      quality.textContent = qualityLabels[data.quality] || data.quality;
      exactElement.textContent = JSON.stringify(data.exact_facts);
      indicatorsElement.textContent = JSON.stringify(data.indicators);
      const normalized = await postJson(root.dataset.linesEndpoint, {
        fen: game.positions[index - 1],
        lines: completed[index - 1].lines,
      });
      activeCandidateLines = normalized.lines;
      normalizedLines.set(ply, activeCandidateLines);
      normalized.lines.forEach((line, lineIndex) => {
        const item = document.createElement("li");
        item.className = "candidate-line";
        const select = document.createElement("button");
        select.type = "button";
        select.className = "candidate-line-select";
        select.dataset.lineIndex = String(lineIndex);
        select.setAttribute("aria-pressed", "false");
        const moves = document.createElement("p");
        moves.className = "candidate-line-moves";
        moves.textContent =
          `#${line.rank} ${line.evaluation.text} · ` +
          line.moves.map((move) => move.san).join(" ");
        select.append(moves);
        item.append(select);
        linesElement.append(item);
      });
      renderGameExplanation(ply);
    }
    for (const button of moveList.querySelectorAll("[data-ply]")) {
      button.setAttribute(
        "aria-current",
        button.dataset.ply === String(ply) ? "true" : "false"
      );
    }
  }

  function lineIndexFromTarget(target) {
    if (!(target instanceof Element)) {
      return null;
    }
    const item = target.closest("[data-line-index]");
    return item ? Number.parseInt(item.dataset.lineIndex, 10) : null;
  }

  linesElement.addEventListener("click", (event) => {
    const index = lineIndexFromTarget(event.target);
    if (index !== null) {
      void startCandidatePlayback(index);
    }
  });
  playbackPrev?.addEventListener("click", () => {
    if (playbackStep === 0) return;
    playbackStep -= 1;
    void renderPlaybackStep();
  });
  playbackNext?.addEventListener("click", () => {
    const line = activeCandidateLines[selectedLineIndex];
    if (!line || playbackStep >= line.moves.length) return;
    playbackStep += 1;
    void renderPlaybackStep();
  });
  playbackReset?.addEventListener("click", () => {
    if (activePly > 0) {
      void selectPly(activePly);
    }
  });

  function renderGraph() {
    const ns = "http://www.w3.org/2000/svg";
    const width = 800;
    const height = 120;
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", labels.graph);
    const points = analysis.evaluations.map((item, index) => {
      const value =
        item.kind === "mate"
          ? (item.value > 0 ? 800 : -800)
          : Math.max(-800, Math.min(800, item.value));
      return [
        (index / Math.max(1, total - 1)) * width,
        height / 2 - (value / 800) * (height / 2 - 8),
      ];
    });
    const polyline = document.createElementNS(ns, "polyline");
    polyline.setAttribute(
      "points",
      points.map((point) => point.join(",")).join(" ")
    );
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", "#3B2A17");
    polyline.setAttribute("stroke-width", "2");
    svg.append(polyline);
    const step = width / Math.max(1, total);
    points.forEach((point, index) => {
      const hit = document.createElementNS(ns, "rect");
      hit.setAttribute("x", String(Math.max(0, point[0] - step / 2)));
      hit.setAttribute("y", "0");
      hit.setAttribute("width", String(step));
      hit.setAttribute("height", String(height));
      hit.setAttribute("fill", "transparent");
      hit.setAttribute("data-graph-ply", String(index));
      hit.setAttribute("tabindex", "0");
      hit.setAttribute("role", "button");
      hit.setAttribute("aria-label", `${labels.graph} ${index}`);
      svg.append(hit);
    });
    graph.replaceChildren(svg);
  }

  function renderMoveList() {
    moveList.replaceChildren();
    const initial = document.createElement("button");
    initial.type = "button";
    initial.className = "pgn-move-button";
    initial.dataset.ply = "0";
    initial.textContent = "0";
    moveList.append(initial);
    for (const ply of analysis.plies) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pgn-move-button";
      button.dataset.ply = String(ply.ply);
      button.textContent =
        `${ply.number}${ply.side === "white" ? "." : "..."} ${ply.san} ` +
        `[${(qualityLabels[ply.quality] || ply.quality)} · ` +
        `${ply.evaluation_after.text}` +
        `${ply.is_key ? ` · ${labels.key}` : ""}]`;
      moveList.append(button);
    }
  }

  function usageText(usage) {
    if (!usage) return "";
    return uiText.token_usage
      .replace("{prompt}", String(usage.prompt))
      .replace("{completion}", String(usage.completion))
      .replace("{reasoning}", String(usage.reasoning))
      .replace("{total}", String(usage.total));
  }

  function renderUsage() {
    const used = Object.values(usageTotals).some((value) => value > 0);
    tokenUsage.textContent = used ? usageText(usageTotals) : "";
  }

  function exportProvenance() {
    const storedMeta =
      Object.values(explanations)[0]?.meta || summaryMeta || null;
    const profile = loadLlmProfile();
    return {
      white: exportText.white,
      black: exportText.black,
      engine: root.dataset.engineVersion || "Stockfish 19",
      preset: analysisSettings.preset,
      multipv: analysisSettings.multipv,
      resource: analysisSettings.resource,
      provider:
        storedMeta?.provider || profile?.provider || exportText.not_available,
      model: storedMeta?.model || profile?.model || exportText.not_available,
    };
  }

  function exportAnnotations(includeExplanations) {
    return (analysis?.plies || []).map((ply) => {
      const explanation =
        includeExplanations ? explanations[ply.ply]?.explanation || null : null;
      return {
        ply: ply.ply,
        quality: ply.quality,
        score_kind: ply.evaluation_after.kind,
        score_value: ply.evaluation_after.value,
        cpl: ply.cpl,
        mate_loss: ply.mate_loss,
        explanation,
      };
    });
  }

  async function handleGamePgnExport() {
    if (!analysis) return;
    const form = document.querySelector(".pgn-form");
    const selectedPgn = game.pgn || "";
    const fallbackPgn = form?.querySelector("#pgn")?.value || "";
    const fallbackIndex = Number.parseInt(
      form?.querySelector("#game_index")?.value || "0",
      10
    );
    try {
      await exportPgn(root.dataset.exportPgnEndpoint, {
        pgn: selectedPgn || fallbackPgn,
        game_index: selectedPgn ? 0 : fallbackIndex,
        include_explanations: exportExplanations.checked,
        annotations: exportAnnotations(exportExplanations.checked),
      });
      exportStatus.textContent = exportText.export_ready;
    } catch (error) {
      exportStatus.textContent = exportText.export_failed;
    }
  }

  function handleGameMarkdownExport() {
    if (!analysis) return;
    const includeExplanations = exportExplanations.checked;
    const form = document.querySelector(".pgn-form");
    const inputPgn =
      game.pgn || form?.querySelector("#pgn")?.value || "";
    const markdown = buildGameMarkdown({
      text: exportText,
      qualityLabels,
      game,
      inputPgn,
      analysis,
      completed,
      explanations,
      summaryExplanation,
      includeExplanations,
      provenance: exportProvenance(),
    });
    downloadText(
      "chessfen-game-analysis.md",
      markdown,
      "text/markdown;charset=utf-8"
    );
    exportStatus.textContent = exportText.export_ready;
  }

  exportPgnButton?.addEventListener("click", handleGamePgnExport);
  exportMarkdownButton?.addEventListener(
    "click",
    handleGameMarkdownExport
  );

  function renderSummary() {
    if (!summaryExplanation) {
      summarySection.hidden = true;
      return;
    }
    summarySection.hidden = false;
    summaryText.textContent = summaryExplanation.summary;
    summaryUncertainty.textContent = summaryExplanation.uncertainty;
    const profile = loadLlmProfile();
    summaryStale.hidden =
      !profile ||
      !summaryMeta ||
      (profile.language === summaryMeta.language &&
        profile.audience === summaryMeta.audience);
  }

  async function gameTargets() {
    const current = analysis;
    const targets = [];
    if (!current) {
      return targets;
    }
    for (const ply of current.plies.filter((item) => item.is_key)) {
      let candidateLines = normalizedLines.get(ply.ply);
      const rawLines = completed[ply.ply - 1]?.lines || [];
      if (!candidateLines && rawLines.length) {
        const normalized = await postJson(root.dataset.linesEndpoint, {
          fen: game.positions[ply.ply - 1],
          lines: rawLines,
        });
        candidateLines = normalized.lines;
        normalizedLines.set(ply.ply, candidateLines);
      }
      targets.push({
        ply: ply.ply,
        move: `${ply.number}${ply.side === "white" ? "." : "..."} ${ply.san}`,
        quality: ply.quality,
        cpl: ply.cpl,
        mate_loss: ply.mate_loss,
        key_reasons: ply.key_reasons,
        exact_facts: ply.exact_facts,
        indicators: ply.indicators,
        evaluation_before: ply.evaluation_before,
        evaluation_after: ply.evaluation_after,
        candidate_lines: candidateLines || [],
        neighbors: current.plies
          .filter((item) => Math.abs(item.ply - ply.ply) === 1)
          .map((item) => ({ ply: item.ply, san: item.san, quality: item.quality })),
      });
    }
    return targets;
  }

  function compactGame() {
    return analysis.plies
      .map(
        (ply) =>
          `${ply.number}${ply.side === "white" ? "." : "..."} ${ply.san}` +
          ` (${ply.quality}${ply.is_key ? ", key" : ""})`
      )
      .join(" ");
  }

  async function prepareBatch() {
    let targets;
    try {
      targets = await gameTargets();
    } catch (error) {
      batchStatus.textContent = uiText.batch_prepare_failed;
      return;
    }
    if (!targets.length) {
      batchStatus.textContent = uiText.no_key_plies;
      return;
    }
    batchBatches = [];
    for (let index = 0; index < targets.length; index += 5) {
      batchBatches.push(targets.slice(index, index + 5));
    }
    pendingBatchIndex = null;
    batchPlan.textContent = uiText.batch_plan
      .replace("{keyPlies}", String(targets.length))
      .replace("{batches}", String(batchBatches.length));
    batchGenerate.hidden = false;
    batchRetry.hidden = true;
    batchStatus.textContent = uiText.review_batch_plan;
  }

  async function ensureLlmReady() {
    const profile = loadLlmProfile();
    if (!profile) {
      explanationStatus.textContent = llmText.configure_profile;
      return null;
    }
    if (!profileRequestReady(profile)) {
      explanationStatus.textContent = llmText.confirm_request;
      return null;
    }
    if (!llmUnlocked()) {
      const unlocked = await unlockLlm(explanationPassphrase.value, profile);
      if (!unlocked.ok) {
        explanationStatus.textContent = llmText.unlock_failed;
        return null;
      }
    }
    return profile;
  }

  function addUsage(usage) {
    if (!usage) return;
    for (const key of ["prompt", "completion", "reasoning", "total"]) {
      usageTotals[key] += usage[key] || 0;
    }
    renderUsage();
  }

  async function generateBatches(profile, startIndex = 0) {
    if (!batchBatches) return;
    batchController = new AbortController();
    batchGenerate.hidden = true;
    batchCancel.hidden = false;
    batchRetry.hidden = true;
    batchWarning.hidden = false;
    const meta = {
      language: profile.language,
      audience: profile.audience,
      provider: profile.provider,
      model: profile.model,
    };
    try {
      for (let index = startIndex; index < batchBatches.length; index += 1) {
        batchStatus.textContent = uiText.generating_batch
          .replace("{current}", String(index + 1))
          .replace("{total}", String(batchBatches.length));
        const result = await requestKeyPlyBatch(
          profile,
          {
            game: { white: game.white, black: game.black, result: game.result, mainline: compactGame() },
            targets: batchBatches[index],
          },
          batchController.signal
        );
        if (!result.ok) {
          if (result.code === "cancelled") {
            batchStatus.textContent = uiText.batch_cancelled;
            return;
          }
          pendingBatchIndex = index;
          batchRetry.hidden = false;
          batchStatus.textContent = failureMessage(
            result.code,
            result.code === "budget_exhausted"
              ? uiText.generation_budget_exhausted
              : uiText.batch_failed.replace("{code}", result.code)
          );
          return;
        }
        for (const [ply, explanation] of Object.entries(result.explanations)) {
          explanations[Number(ply)] = { explanation, meta };
        }
        addUsage(result.usage);
        await persistGame(analysis ? "complete" : "incomplete", analysis);
        renderGameExplanation(activePly);
        batchStatus.textContent = uiText.completed_batch
          .replace("{current}", String(index + 1))
          .replace("{total}", String(batchBatches.length));
      }
      batchStatus.textContent = uiText.overall_summary_generating;
      lastSummaryProfile = profile;
      const summary = await requestOverallSummary(
        profile,
        {
          game: { white: game.white, black: game.black, result: game.result, mainline: compactGame() },
          key_plies: analysis.key_plies,
        },
        batchController.signal
      );
      if (!summary.ok) {
        summarySection.hidden = false;
        summaryRetry.hidden = false;
        batchStatus.textContent = failureMessage(
          summary.code,
          summary.code === "cancelled"
            ? uiText.batch_cancelled
            : uiText.summary_failed_after_batches
        );
        return;
      }
      summaryExplanation = summary.summary;
      summaryMeta = meta;
      summaryRetry.hidden = true;
      addUsage(summary.usage);
      renderSummary();
      await persistGame(analysis ? "complete" : "incomplete", analysis);
      batchStatus.textContent = uiText.key_ply_summary_complete;
    } finally {
      batchController = null;
      batchCancel.hidden = true;
    }
  }

  async function renderGameAnalysis(payload, restored = false) {
    analysis = payload;
    empty.hidden = true;
    result.hidden = false;
    ribbon.hidden = false;
    renderGraph();
    renderMoveList();
    await selectPly(0);
    explanationSection.hidden = !credentialFeaturesAvailable;
    renderSummary();
    renderUsage();
    status.textContent = restored
      ? uiText.workspace_restored
      : labels.complete;
  }

  async function finish() {
    const payload = await postJson(root.dataset.resultsEndpoint, {
      start_fen: game.start_fen,
      moves: game.moves.map((move) => move.uci),
      positions: completed.map((entry) => ({ lines: entry.lines })),
    });
    await persistGame("complete", payload);
    await renderGameAnalysis(payload);
  }

  function clearDependentResults() {
    for (const key of Object.keys(explanations)) {
      delete explanations[key];
    }
    summaryExplanation = null;
    summaryMeta = null;
    lastSummaryProfile = null;
    pendingBatchIndex = null;
    batchBatches = null;
    for (const key of Object.keys(usageTotals)) {
      usageTotals[key] = 0;
    }
    explanationUsage.textContent = "";
    explanationOutput.hidden = true;
    explanationStale.hidden = true;
    normalizedLines.clear();
    summarySection.hidden = true;
    summaryRetry.hidden = true;
    batchPlan.textContent = "";
    batchGenerate.hidden = true;
    batchRetry.hidden = true;
    batchCancel.hidden = true;
    batchWarning.hidden = true;
    batchStatus.textContent = "";
    renderUsage();
  }

  async function runQueue(reset) {
    hasStarted = true;
    if (reset) {
      completed.fill(null);
      analysis = null;
      clearDependentResults();
      result.hidden = true;
      ribbon.hidden = true;
      empty.hidden = false;
      linesElement.replaceChildren();
      graph.replaceChildren();
      moveList.replaceChildren();
    }
    if (reset) {
      await persistGame("incomplete", null);
    }
    const token = (runToken += 1);
    cancelled = false;
    setBusy(true);
    error.hidden = true;
    try {
      for (let index = 0; index < total; index += 1) {
        if (completed[index]) {
          continue;
        }
        if (token !== runToken || cancelled) {
          return;
        }
        if (game.terminal_positions[index]) {
          completed[index] = { lines: [] };
          continue;
        }
        status.textContent = uiText.analyzing_position
          .replace("{current}", String(index + 1))
          .replace("{total}", String(total));
        const result = await engine.analyze(game.positions[index]);
        if (token !== runToken) {
          return;
        }
        if (result.cancelled || cancelled) {
          status.textContent = labels.cancelled;
          return;
        }
        completed[index] = { lines: result.lines };
        await persistGame("incomplete", analysis);
      }
      await finish();
    } catch (exception) {
      showError(labels.generic);
      status.textContent = uiText.analysis_stopped;
    } finally {
      if (token === runToken) {
        setBusy(false);
      }
    }
  }

  startButton.addEventListener("click", () => runQueue(true));
  continueButton.addEventListener("click", () => runQueue(false));
  restartButton.addEventListener("click", async () => {
    if (!window.confirm(uiText.restart_game_confirm)) {
      return;
    }
    completed.fill(null);
    analysis = null;
    await persistGame("incomplete", null);
    await runQueue(true);
  });
  cancelButton.addEventListener("click", async () => {
    cancelled = true;
    status.textContent = labels.stopping;
    if (engine.busy) {
      try {
        await engine.cancel();
      } catch (exception) {
        showError(uiText.engine_stop_failed);
      }
    }
  });

  function plyFromTarget(target) {
    if (!(target instanceof Element)) {
      return null;
    }
    const node = target.closest("[data-ply], [data-graph-ply]");
    if (!node) {
      return null;
    }
    return Number.parseInt(
      node.dataset.ply ?? node.dataset.graphPly ?? "",
      10
    );
  }

  for (const container of [moveList, graph]) {
    container.addEventListener("click", (event) => {
      const ply = plyFromTarget(event.target);
      if (ply !== null && analysis) {
        selectPly(ply);
      }
    });
    container.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && analysis) {
        const ply = plyFromTarget(event.target);
        if (ply !== null) {
          event.preventDefault();
          selectPly(ply);
        }
      }
    });
  }

  const pgnForm = document.querySelector(".pgn-form");
  let confirmedSubmit = false;
  pgnForm?.addEventListener("submit", async (event) => {
    if (confirmedSubmit) {
      confirmedSubmit = false;
      return;
    }
    const submitter =
      event.submitter instanceof HTMLElement ? event.submitter : null;
    if (submitter?.name !== "mode" || submitter.value !== "open") {
      return;
    }
    event.preventDefault();
    const loaded = await loadWorkspace();
    const record = loaded.ok ? loaded.record : null;
    const pgn = pgnForm.querySelector("#pgn")?.value || "";
    const gameIndex = pgnForm.querySelector("#game_index")?.value || "";
    const sameGame =
      record?.kind === "game" &&
      record.input?.pgn === pgn &&
      (record.input?.gameIndex || "") === gameIndex;
    if (record && !sameGame) {
      const message =
        record.kind === "position"
          ? uiText.cross_kind_workspace_confirm
          : uiText.replace_workspace_confirm;
      if (!window.confirm(message)) {
        return;
      }
    }
    confirmedSubmit = true;
    pgnForm.requestSubmit(submitter);
  });

  async function restoreGameWorkspace() {
    const loaded = await loadWorkspace();
    if (!loaded.ok) {
      setWorkspaceStatus(uiText.workspace_corrupt, true);
      return;
    }
    const record = loaded.record;
    if (!record || record.kind !== "game") {
      workspaceRestore?.hide?.();
      return;
    }
    const sameGame =
      record.gameData?.start_fen === game.start_fen &&
      record.gameData?.moves?.length === game.moves.length &&
      record.gameData?.moves?.every(
        (move, index) => move.uci === game.moves[index].uci
      );
    if (!sameGame) {
      workspaceRestore.hidden = false;
      return;
    }
    if (
      !engineSettingsCompatible(
        record.engine,
        analysisSettings,
        engineCandidatesForSettings[0].runtime
      )
    ) {
      if (!window.confirm(
        uiText.engine_changed_game_confirm
      )) {
        return;
      }
      await clearWorkspace();
      setWorkspaceStatus(uiText.workspace_invalidated, true);
      return;
    }
    if (record.engine?.mixed) {
      mixedRuntime = true;
      engineRuntime = record.engine.runtime || "single-threaded";
      engine.mixedRuntime = true;
      paintRuntime(engineRuntime, true);
      setWorkspaceStatus(root.dataset.runtimeMixedMessage, false);
    } else if (record.engine?.fallback) {
      runtimeFellBack = true;
      engineRuntime = record.engine.runtime || "single-threaded";
      paintRuntime(engineRuntime, false);
      setWorkspaceStatus(root.dataset.runtimeFallbackMessage, false);
    }
    if (Array.isArray(record.engine?.completed)) {
      record.engine.completed.forEach((entry, index) => {
        if (index < completed.length && entry?.lines) {
          completed[index] = entry;
        }
      });
    }
    hasStarted = completed.some(Boolean);
    if (record.explanations && typeof record.explanations === "object") {
      Object.assign(explanations, record.explanations);
    }
    summaryExplanation = record.summaryExplanation || null;
    summaryMeta = record.summaryMeta || null;
    if (record.usageTotals) Object.assign(usageTotals, record.usageTotals);
    renderSummary();
    renderUsage();
    if (record.result) {
      await renderGameAnalysis(record.result, true);
      setBusy(false);
    } else if (hasStarted) {
      setBusy(false);
      status.textContent = uiText.restored_positions
        .replace(
          "{completed}",
          String(completed.filter(Boolean).length)
        )
        .replace("{total}", String(total));
    }
    workspaceRestore.hidden = true;
  }

  document.querySelector("[data-game-explanation-generate]")?.addEventListener(
    "click",
    async () => {
      if (explanationBusy || activePly <= 0 || !analysis) return;
      explanationBusy = true;
      try {
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
          const unlocked = await unlockLlm(explanationPassphrase.value, profile);
          if (!unlocked.ok) {
            explanationStatus.textContent =
              llmText.unlock_failed;
            return;
          }
        }
        explanationStatus.textContent = llmText.generating;
        const data = analysis.plies[activePly - 1];
        const result = await requestExplanation(profile, {
          fen: game.positions[activePly - 1],
          actual_move: game.moves[activePly - 1],
          facts: data.exact_facts,
          indicators: data.indicators,
          evaluation_before: data.evaluation_before,
          evaluation_after: data.evaluation_after,
          quality: data.quality,
          cpl: data.cpl,
          candidate_lines: activeCandidateLines,
        });
        if (!result.ok) {
          explanationStatus.textContent = failureMessage(
            result.code,
            uiText.explanation_failed_generic
          );
          return;
        }
        explanations[activePly] = {
          explanation: result.explanation,
          meta: {
            language: profile.language,
            audience: profile.audience,
            provider: profile.provider,
            model: profile.model,
          },
          usage: result.usage || null,
        };
        renderGameExplanation(activePly);
        await persistGame(analysis ? "complete" : "incomplete", analysis);
        explanationStatus.textContent = llmText.explanation_ready;
      } finally {
        explanationBusy = false;
      }
    }
  );
  document.querySelector("[data-game-explanation-lock]")?.addEventListener(
    "click",
    () => {
      lockLlm();
      explanationStatus.textContent = llmText.locked;
    }
  );

  summaryRetry?.addEventListener("click", async () => {
    if (!lastSummaryProfile) return;
    const profile = await ensureLlmReady();
    if (!profile) return;
    const result = await requestOverallSummary(
      profile,
      {
        game: {
          white: game.white,
          black: game.black,
          result: game.result,
          mainline: compactGame(),
        },
        key_plies: analysis.key_plies,
      }
    );
    if (!result.ok) {
      batchStatus.textContent = failureMessage(
        result.code,
        uiText.summary_retry_failed
      );
      return;
    }
    summaryExplanation = result.summary;
    summaryMeta = {
      language: profile.language,
      audience: profile.audience,
      provider: profile.provider,
      model: profile.model,
    };
    summaryRetry.hidden = true;
    addUsage(result.usage);
    renderSummary();
    await persistGame(analysis ? "complete" : "incomplete", analysis);
    batchStatus.textContent = uiText.overall_summary_complete;
  });
  batchPreview?.addEventListener("click", () => {
    void prepareBatch();
  });
  batchGenerate?.addEventListener("click", async () => {
    const profile = await ensureLlmReady();
    if (profile) await generateBatches(profile);
  });
  batchRetry?.addEventListener("click", async () => {
    if (pendingBatchIndex === null) return;
    const profile = await ensureLlmReady();
    if (profile) await generateBatches(profile, pendingBatchIndex);
  });
  batchCancel?.addEventListener("click", () => {
    batchController?.abort();
    batchStatus.textContent = uiText.summary_cancelling;
    batchWarning.hidden = false;
  });

  showBoard(game.start_fen).catch(() => showError(labels.board));
  root.dataset.gameReady = "true";
  void restoreGameWorkspace();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeGameAnalysis);
} else {
  initializeGameAnalysis();
}
