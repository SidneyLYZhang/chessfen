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
import { EngineSupervisor } from "/static/js/analysis.js";

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
  let explanationBusy = false;
  const llmText = Object.fromEntries(
    [...document.querySelectorAll("[data-llm-message]")].map((node) => [
      node.dataset.llmMessage,
      node.textContent,
    ])
  );
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
  if (runtime) {
    runtime.textContent = `${stored.resource === "full" ? "Full" : "Lite"} · single-threaded · ${stored.preset || "standard"}`;
  }
  const engine = new EngineSupervisor(
    stored.resource === "full"
      ? "/static/vendor/stockfish/stockfish-19-single.js"
      : "/static/vendor/stockfish/stockfish-19-lite-single.js",
    (info) => {
      if (busy && info.depth !== null) {
        status.textContent =
          `${labels.analyzing} ${completed.filter(Boolean).length + 1}/${total}` +
          ` · depth ${info.depth}`;
      }
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
        ...engineSnapshot(analysisSettings),
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
        document.documentElement.lang === "de"
          ? "Analyseergebnisse werden nicht lokal gespeichert."
          : document.documentElement.lang === "zh"
            ? "分析结果未能保存到本地。"
            : "Analysis results are not being saved locally.",
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

  async function selectPly(ply) {
    activePly = ply;
    activeCandidateLines = [];
    renderGameExplanation(ply);
    const index = Math.max(0, ply);
    await showBoard(game.positions[index]);
    evaluation.textContent = analysis.evaluations[index].text;
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
      for (const line of normalized.lines) {
        const item = document.createElement("li");
        item.className = "candidate-line";
        const moves = document.createElement("p");
        moves.className = "candidate-line-moves";
        moves.textContent =
          `#${line.rank} ${line.evaluation.text} · ` +
          line.moves.map((move) => move.san).join(" ");
        item.append(moves);
        linesElement.append(item);
      }
      renderGameExplanation(ply);
    }
    for (const button of moveList.querySelectorAll("[data-ply]")) {
      button.setAttribute(
        "aria-current",
        button.dataset.ply === String(ply) ? "true" : "false"
      );
    }
  }

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
        `[${(qualityLabels[ply.quality] || ply.quality)}` +
        `${ply.is_key ? ` · ${labels.key}` : ""}]`;
      moveList.append(button);
    }
  }

  function usageText(usage) {
    if (!usage) return "";
    return (
      `Tokens · prompt ${usage.prompt} · completion ${usage.completion}` +
      ` · reasoning ${usage.reasoning} · total ${usage.total}`
    );
  }

  function renderUsage() {
    tokenUsage.textContent = usageText(usageTotals);
  }

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

  function gameTargets() {
    return analysis.plies
      .filter((ply) => ply.is_key)
      .map((ply) => ({
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
        candidate_lines: completed[ply.ply - 1]?.lines || [],
        neighbors: analysis.plies
          .filter((item) => Math.abs(item.ply - ply.ply) === 1)
          .map((item) => ({ ply: item.ply, san: item.san, quality: item.quality })),
      }));
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

  function prepareBatch() {
    const targets = gameTargets();
    if (!targets.length) {
      batchStatus.textContent = "No Key Plies were selected.";
      return;
    }
    batchBatches = [];
    for (let index = 0; index < targets.length; index += 5) {
      batchBatches.push(targets.slice(index, index + 5));
    }
    pendingBatchIndex = null;
    batchPlan.textContent =
      `${targets.length} Key Plies · ${batchBatches.length} batch requests + 1 summary request.`;
    batchGenerate.hidden = false;
    batchRetry.hidden = true;
    batchStatus.textContent = "Review the request count, then confirm generation.";
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
    const meta = { language: profile.language, audience: profile.audience };
    try {
      for (let index = startIndex; index < batchBatches.length; index += 1) {
        batchStatus.textContent = `Generating batch ${index + 1}/${batchBatches.length}…`;
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
            batchStatus.textContent = "LLM generation cancelled. Submitted requests may still finish.";
            return;
          }
          pendingBatchIndex = index;
          batchRetry.hidden = false;
          batchStatus.textContent = failureMessage(
            result.code,
            result.code === "budget_exhausted"
              ? "The generation budget was exhausted. Prior results were preserved."
              : `A batch failed (${result.code}). Retry this batch manually.`
          );
          return;
        }
        for (const [ply, explanation] of Object.entries(result.explanations)) {
          explanations[Number(ply)] = { explanation, meta };
        }
        addUsage(result.usage);
        await persistGame(analysis ? "complete" : "incomplete", analysis);
        renderGameExplanation(activePly);
        batchStatus.textContent = `Completed batch ${index + 1}/${batchBatches.length}.`;
      }
      batchStatus.textContent = "Generating overall summary…";
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
            ? "LLM generation cancelled. Submitted requests may still finish."
            : "Batches completed, but the overall summary failed. Retry it separately."
        );
        return;
      }
      summaryExplanation = summary.summary;
      summaryMeta = meta;
      summaryRetry.hidden = true;
      addUsage(summary.usage);
      renderSummary();
      await persistGame(analysis ? "complete" : "incomplete", analysis);
      batchStatus.textContent = "Key Ply explanations and summary complete.";
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
    explanationSection.hidden = false;
    renderSummary();
    renderUsage();
    status.textContent = restored
      ? "Restored from the local Analysis Workspace."
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

  async function runQueue(reset) {
    hasStarted = true;
    if (reset) {
      completed.fill(null);
      analysis = null;
      summaryExplanation = null;
      summaryMeta = null;
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
        status.textContent = `Analyzing Position ${index + 1}/${total}`;
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
      status.textContent = "Analysis stopped.";
    } finally {
      if (token === runToken) {
        setBusy(false);
      }
    }
  }

  startButton.addEventListener("click", () => runQueue(true));
  continueButton.addEventListener("click", () => runQueue(false));
  restartButton.addEventListener("click", async () => {
    if (!window.confirm("Restart analysis and clear saved engine results?")) {
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
        showError("Stockfish could not be stopped.");
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
  pgnForm?.addEventListener("submit", (event) => {
    if (hasStarted && !window.confirm("Replace the saved Analysis Workspace?")) {
      event.preventDefault();
    }
  });

  async function restoreGameWorkspace() {
    const loaded = await loadWorkspace();
    if (!loaded.ok) {
      setWorkspaceStatus("The saved Analysis Workspace could not be read.", true);
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
    if (!engineSettingsCompatible(record.engine, analysisSettings)) {
      if (!window.confirm(
        "Engine settings changed. Discard saved engine results and keep the Game?"
      )) {
        return;
      }
      await clearWorkspace();
      setWorkspaceStatus("Saved engine results were invalidated.", true);
      return;
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
      status.textContent =
        `Restored ${completed.filter(Boolean).length}/${total} Positions. Continue to resume.`;
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
            "Explanation could not be generated. Confirm the Provider and Reasoning Settings."
          );
          return;
        }
        explanations[activePly] = {
          explanation: result.explanation,
          meta: {
            language: profile.language,
            audience: profile.audience,
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
        "The summary retry failed."
      );
      return;
    }
    summaryExplanation = result.summary;
    summaryMeta = {
      language: profile.language,
      audience: profile.audience,
    };
    summaryRetry.hidden = true;
    addUsage(result.usage);
    renderSummary();
    await persistGame(analysis ? "complete" : "incomplete", analysis);
    batchStatus.textContent = "Overall summary complete.";
  });
  batchPreview?.addEventListener("click", prepareBatch);
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
    batchStatus.textContent =
      "Cancelling… submitted requests may still finish and incur charges.";
    batchWarning.hidden = false;
  });

  showBoard(game.start_fen).catch(() => showError(labels.board));
  void restoreGameWorkspace();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeGameAnalysis);
} else {
  initializeGameAnalysis();
}
