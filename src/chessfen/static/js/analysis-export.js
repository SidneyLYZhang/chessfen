"use strict";

import { readMessages } from "/static/js/ui-messages.js";

export function collectExportText() {
  return readMessages("export-message");
}

export function downloadText(filename, content, mediaType) {
  const blob = new Blob([content], { type: mediaType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function exportPgn(endpoint, payload) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error("export_failed");
  }
  const result = await response.json();
  downloadText(
    result.filename,
    result.content,
    "application/x-chess-pgn;charset=utf-8"
  );
}

function codeFence(value, language = "") {
  const fence = value.includes("```") ? "~~~~" : "```";
  return `${fence}${language}\n${value.trimEnd()}\n${fence}`;
}

function tableCell(value) {
  return String(value ?? "").replaceAll("|", "\\|").replaceAll("\n", " ");
}

function explanationBlock(explanation, text) {
  if (!explanation) return [];
  return [
    `**${text.summary}:** ${explanation.summary}`,
    "",
    `**${text.why}:** ${explanation.why}`,
    "",
    `**${text.alternatives}:** ${explanation.alternatives}`,
    "",
    `**${text.uncertainty}:** ${explanation.uncertainty}`,
    "",
  ];
}

function candidateBlock(lines) {
  if (!Array.isArray(lines) || !lines.length) return [];
  const output = [];
  for (const line of lines) {
    const moves = (line.moves || []).map((move) => move.san).join(" ");
    output.push(
      `${line.rank}. ${line.evaluation?.text || ""} · ${moves}`
    );
  }
  return [output.join("\n"), ""];
}

export function buildGameMarkdown({
  text,
  qualityLabels,
  game,
  inputPgn,
  analysis,
  completed,
  explanations,
  summaryExplanation,
  includeExplanations,
  provenance,
}) {
  const sideLabel = (side) =>
    side === "white" ? provenance.white : provenance.black;
  const qualityCounts = Object.entries(analysis.summary || {})
    .filter(([, count]) => count > 0)
    .map(([quality, count]) => `${qualityLabels[quality] || quality}: ${count}`)
    .join(" · ");
  const lines = [
    `# ${text.game_title}`,
    "",
    `- **${provenance.white}:** ${game.white}`,
    `- **${provenance.black}:** ${game.black}`,
    `- **${text.result}:** ${game.result}`,
    "",
    `## ${text.input_pgn}`,
    "",
    codeFence(inputPgn, "pgn"),
    "",
    `## ${text.quality_summary}`,
    "",
    qualityCounts || text.not_available,
    "",
  ];
  if (includeExplanations && summaryExplanation) {
    lines.push(
      `## ${text.overall_summary}`,
      "",
      `**${text.summary}:** ${summaryExplanation.summary}`,
      "",
      `**${text.uncertainty}:** ${summaryExplanation.uncertainty}`,
      "",
    );
  }
  lines.push(
    `## ${text.mainline}`,
    "",
    `| ${text.move} | ${text.side} | ${text.san} | ${text.quality} | ` +
      `${text.evaluation_before} | ${text.evaluation_after} | ${text.cpl} |`,
    "| --- | --- | --- | --- | --- | --- | --- |",
  );
  for (const ply of analysis.plies || []) {
    lines.push(
      `| ${ply.number} | ${sideLabel(ply.side)} | ${tableCell(ply.san)} | ` +
        `${qualityLabels[ply.quality] || ply.quality} | ` +
        `${ply.evaluation_before?.text || ""} | ` +
        `${ply.evaluation_after?.text || ""} | ` +
        `${ply.cpl ?? "—"} |`
    );
  }
  lines.push("");
  if (includeExplanations) {
    lines.push(`## ${text.key_explanations}`, "");
    let wroteExplanation = false;
    for (const ply of analysis.plies || []) {
      const stored = explanations[ply.ply];
      if (!stored) continue;
      wroteExplanation = true;
      lines.push(
        `### ${ply.number}${ply.side === "white" ? "." : "..."} ${ply.san} — ` +
          `${qualityLabels[ply.quality] || ply.quality}`,
        "",
        ...explanationBlock(stored.explanation, text),
      );
    }
    if (!wroteExplanation) lines.push(text.not_available, "");
  }
  lines.push(`## ${text.candidate_lines}`, "");
  let wroteCandidates = false;
  for (const ply of analysis.plies || []) {
    const positionLines = completed[ply.ply - 1]?.lines;
    if (!Array.isArray(positionLines) || !positionLines.length) continue;
    wroteCandidates = true;
    lines.push(
      `### ${ply.number}${ply.side === "white" ? "." : "..."} ${ply.san}`,
      "",
      ...candidateBlock(positionLines)
    );
  }
  if (!wroteCandidates) lines.push(text.not_available, "");
  lines.push(
    `## ${text.provenance}`,
    "",
    `- **${text.engine}:** ${provenance.engine}`,
    `- **${text.preset}:** ${provenance.preset}`,
    `- **${text.multipv}:** ${provenance.multipv}`,
    `- **${text.resource}:** ${provenance.resource}`,
    `- **${text.provider}:** ${provenance.provider}`,
    `- **${text.model}:** ${provenance.model}`,
    `- **${text.explanations}:** ${includeExplanations ? text.included : text.omitted}`,
    ""
  );
  return lines.join("\n");
}

export function buildPositionMarkdown({
  text,
  qualityLabels,
  inputFen,
  result,
  assessment,
  summaryExplanation,
  includeExplanations,
  provenance,
}) {
  const lines = [
    `# ${text.position_title}`,
    "",
    `## ${text.input_fen}`,
    "",
    codeFence(inputFen, "text"),
    "",
    `## ${text.evaluation}`,
    "",
    result.lines?.[0]?.evaluation?.text || text.not_available,
    "",
  ];
  if (assessment) {
    lines.push(
      `## ${text.actual_move}`,
      "",
      `- **${text.actual_move}:** ${assessment.actual_move?.san || ""}`,
      `- **${text.best_move}:** ${assessment.best_move?.san || ""}`,
      `- **${text.quality}:** ${
        qualityLabels[assessment.quality] || assessment.quality
      }`,
      `- **${text.cpl}:** ${assessment.cpl ?? "—"}`,
      ""
    );
  }
  if (includeExplanations && result.explanation) {
    lines.push(
      `## ${text.explanation}`,
      "",
      ...explanationBlock(result.explanation, text),
    );
  }
  if (includeExplanations && summaryExplanation) {
    lines.push(
      `## ${text.position_summary}`,
      "",
      `**${text.summary}:** ${summaryExplanation.summary}`,
      "",
      `**${text.uncertainty}:** ${summaryExplanation.uncertainty}`,
      "",
    );
  }
  lines.push(
    `## ${text.candidate_lines}`,
    "",
    ...candidateBlock(result.lines || [])
  );
  lines.push(
    `## ${text.provenance}`,
    "",
    `- **${text.engine}:** ${provenance.engine}`,
    `- **${text.preset}:** ${provenance.preset}`,
    `- **${text.multipv}:** ${provenance.multipv}`,
    `- **${text.resource}:** ${provenance.resource}`,
    `- **${text.provider}:** ${provenance.provider}`,
    `- **${text.model}:** ${provenance.model}`,
    `- **${text.explanations}:** ${includeExplanations ? text.included : text.omitted}`,
    ""
  );
  return lines.join("\n");
}
