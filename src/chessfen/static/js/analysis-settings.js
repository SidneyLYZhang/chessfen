"use strict";

import { workspaceIsActive } from "/static/js/analysis-workspace.js";
import { loadLlmProfile } from "/static/js/analysis-llm.js";
import { readMessages } from "/static/js/ui-messages.js";

const STORAGE_KEY = "chessfen.analysis.settings.v1";
const PRESETS = {
  fast: { depth: 14, movetime: 1000 },
  standard: { depth: 18, movetime: 2000 },
  deep: { depth: 22, movetime: 4000 },
};
const DEFAULTS = { preset: "standard", multipv: 3, resource: "lite" };

function readStoredSettings() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch (error) {
    return {};
  }
}

export function loadAnalysisSettings() {
  const stored = readStoredSettings();
  return {
    preset: Object.hasOwn(PRESETS, stored.preset)
      ? stored.preset
      : DEFAULTS.preset,
    multipv:
      Number.isInteger(stored.multipv) && stored.multipv >= 1 && stored.multipv <= 5
        ? stored.multipv
        : DEFAULTS.multipv,
    resource: stored.resource === "full" ? "full" : "lite",
  };
}

function saveSettings(settings) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch (error) {
    /* Settings persistence is optional. */
  }
}

export function searchOptions(settings) {
  const preset = Object.hasOwn(PRESETS, settings.preset)
    ? PRESETS[settings.preset]
    : PRESETS.standard;
  return {
    multipv: settings.multipv,
    depth: preset.depth,
    movetime: preset.movetime,
  };
}

export function bindAnalysisSettings(root, onChange, beforeChange = null) {
  if (!root) {
    return null;
  }
  const preset = root.querySelector("[data-setting-preset]");
  const multipv = root.querySelector("[data-setting-multipv]");
  const resource = root.querySelector("[data-setting-resource]");
  const warning = root.querySelector("[data-setting-resource-warning]");
  const provenance = root.querySelector("[data-engine-provenance]");
  let settings = loadAnalysisSettings();

  function paint() {
    preset.value = settings.preset;
    multipv.value = String(settings.multipv);
    resource.value = settings.resource;
    warning.hidden = settings.resource !== "full";
    provenance.textContent =
      "Stockfish 19 · " + (settings.resource === "full" ? "Full" : "Lite");
  }

  function changed() {
    const next = {
      preset: preset.value,
      multipv: Number.parseInt(multipv.value, 10),
      resource: resource.value === "full" ? "full" : "lite",
    };
    if (beforeChange && !beforeChange(next)) {
      paint();
      return;
    }
    settings = next;
    saveSettings(settings);
    paint();
    onChange?.(settings);
  }

  preset.addEventListener("change", changed);
  multipv.addEventListener("change", changed);
  resource.addEventListener("change", changed);
  paint();
  return { current: () => settings };
}



function initializeAnalysisSettings() {
  const root = document.querySelector("[data-analysis-settings]");
  if (!root) return;
  const uiText = readMessages("ui-message");
  const isPositionPage = Boolean(document.querySelector("[data-analysis-root]"));
  const profileSummary = document.querySelector(
    "[data-llm-config-summary]"
  );
  const emptySummary = document.querySelector(
    "[data-llm-summary-empty]"
  );
  const profile = loadLlmProfile();
  if (profileSummary) {
    profileSummary.textContent = profile
      ? `${profile.provider} · ${profile.model} · ` +
        `${profile.language}/${profile.audience}`
      : emptySummary?.textContent || "";
  }
  bindAnalysisSettings(
    root,
    () => {
      if (isPositionPage) {
        window.location.reload();
      } else {
        const runtime = document.querySelector("[data-engine-runtime]");
        if (runtime) {
          runtime.textContent = uiText.settings_saved_reopen;
        }
      }
    },
    () =>
      !workspaceIsActive() ||
      window.confirm(uiText.settings_reopen_confirm)
  );
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeAnalysisSettings);
} else {
  initializeAnalysisSettings();
}
