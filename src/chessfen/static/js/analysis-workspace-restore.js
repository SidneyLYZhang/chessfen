"use strict";

import {
  clearWorkspace,
  loadWorkspace,
} from "/static/js/analysis-workspace.js";

async function initializeWorkspaceRestore() {
  const banner = document.querySelector("[data-workspace-restore]");
  if (!banner || document.querySelector("[data-game-root]")) {
    return;
  }
  const status = document.querySelector("[data-workspace-status]");
  const loaded = await loadWorkspace();
  if (!loaded.ok) {
    if (status) {
      status.textContent = "The saved Analysis Workspace could not be read.";
      status.hidden = false;
      status.dataset.error = "true";
    }
    return;
  }
  const record = loaded.record;
  if (!record || record.kind !== "game" || !record.input?.pgn) {
    return;
  }
  banner.hidden = false;
  banner.querySelector("[data-workspace-restore-button]")?.addEventListener(
    "click",
    () => {
      const form = document.createElement("form");
      form.method = "post";
      form.action = "/tools/analysis/pgn";
      form.enctype = "multipart/form-data";
      form.hidden = true;
      for (const [name, value] of [
        ["pgn", record.input.pgn],
        ["game_index", record.input.gameIndex],
        ["mode", "open"],
      ]) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.append(input);
      }
      document.body.append(form);
      form.submit();
    }
  );
  banner.querySelector("[data-workspace-discard-button]")?.addEventListener(
    "click",
    async () => {
      await clearWorkspace();
      banner.hidden = true;
    }
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeWorkspaceRestore);
} else {
  initializeWorkspaceRestore();
}
