"use strict";

import {
  clearWorkspace,
  loadWorkspace,
} from "/static/js/analysis-workspace.js";
import { readMessages } from "/static/js/ui-messages.js";

async function initializeWorkspaceRestore() {
  const banner = document.querySelector("[data-workspace-restore]");
  if (!banner || document.querySelector("[data-game-root]")) {
    return;
  }
  const status = document.querySelector("[data-workspace-status]");
  const uiText = readMessages("ui-message");
  const loaded = await loadWorkspace();
  if (!loaded.ok) {
    if (status) {
      status.textContent = uiText.workspace_corrupt;
      status.hidden = false;
      status.dataset.error = "true";
    }
    return;
  }
  const record = loaded.record;
  if (!record) {
    return;
  }
  if (record.kind === "position") {
    document.querySelector(".pgn-form")?.addEventListener(
      "submit",
      (event) => {
        const submitter = event.submitter;
        if (submitter?.name !== "mode" || submitter.value !== "open") {
          return;
        }
        if (!window.confirm(uiText.cross_kind_workspace_confirm)) {
          event.preventDefault();
        }
      }
    );
    return;
  }
  if (record.kind !== "game" || !record.input?.pgn) {
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
