"use strict";

import {
  credentialMetadata,
  credentialSecurityAvailable,
  deleteCredential,
  saveCredential,
} from "/static/js/analysis-credential.js";
import {
  fetchModels,
  loadLlmProfile,
  lockLlm,
  profileRequestReady,
  requestFingerprint,
  saveLlmProfile,
  unlockLlm,
} from "/static/js/analysis-llm.js";

function initializeSettings() {
  const form = document.querySelector("[data-llm-form]");
  if (!form) return;
  const q = (name) => form.querySelector(`[data-llm-${name}]`);
  const provider = q("provider");
  const baseUrl = q("base-url");
  const model = q("model");
  const models = q("models");
  const language = q("language");
  const audience = q("audience");
  const effort = q("effort");
  const reasoning = q("reasoning");
  const budget = q("budget");
  const consent = q("consent");
  const confirm = q("confirm");
  const preview = q("preview");
  const apiKey = document.querySelector("[data-llm-api-key]");
  const passphrase = document.querySelector("[data-llm-passphrase]");
  const passphraseConfirm = document.querySelector(
    "[data-llm-passphrase-confirm]"
  );
  const status = document.querySelector("[data-llm-status]");
  const meta = document.querySelector("[data-llm-credential-meta]");
  const insecure = document.querySelector("[data-llm-insecure]");
  const stored = loadLlmProfile();

  if (!credentialSecurityAvailable()) {
    insecure.hidden = false;
    for (const control of form.querySelectorAll("input, select, button")) {
      control.disabled = true;
    }
    return;
  }

  function readProfile() {
    const normalizedUrl = baseUrl.value.trim().replace(/\/$/, "");
    const candidate = {
      provider: provider.value,
      baseUrl: normalizedUrl,
      model: model.value.trim(),
      language: language.value,
      audience: audience.value,
      reasoningEnabled: reasoning.checked,
      reasoningEffort: effort.value,
      maxTokens: Number.parseInt(budget.value, 10),
      consentHost: consent.checked ? new URL(normalizedUrl).host : null,
    };
    candidate.confirmedFingerprint = confirm.checked
      ? requestFingerprint(candidate)
      : null;
    return candidate;
  }

  function paintPreview() {
    preview.textContent = JSON.stringify(
      {
        model: model.value.trim(),
        reasoning_effort: reasoning.checked ? effort.value : null,
        max_completion_tokens: Number.parseInt(budget.value, 10),
        response_format: { type: "json_object" },
      },
      null,
      2
    );
  }

  async function paintCredential() {
    const info = await credentialMetadata();
    meta.textContent = info.exists
      ? `Stored ciphertext: ${info.provider} · ${info.baseHost}`
      : "No encrypted credential stored.";
  }

  const defaults = {
    provider: "openai",
    baseUrl: "https://api.openai.com/v1",
    model: "",
    language: document.documentElement.lang,
    audience: "club",
    reasoningEnabled: true,
    reasoningEffort: "max",
    maxTokens: 16384,
    consentHost: null,
    confirmedFingerprint: null,
    ...(stored || {}),
  };
  provider.value = defaults.provider;
  baseUrl.value = defaults.baseUrl;
  model.value = defaults.model;
  language.value = ["zh", "en", "de"].includes(defaults.language)
    ? defaults.language
    : "en";
  audience.value = defaults.audience;
  reasoning.checked = defaults.reasoningEnabled;
  effort.value = defaults.reasoningEffort;
  budget.value = String(defaults.maxTokens);
  try {
    consent.checked =
      Boolean(stored?.consentHost) &&
      stored.consentHost === new URL(defaults.baseUrl).host;
    confirm.checked = profileRequestReady(defaults);
  } catch (error) {
    consent.checked = false;
    confirm.checked = false;
  }
  paintPreview();
  void paintCredential();
  status.textContent = stored ? "LLM Profile loaded." : "No LLM Profile saved.";

  baseUrl.addEventListener("change", () => {
    consent.checked = false;
    confirm.checked = false;
  });
  for (const control of [
    provider,
    baseUrl,
    model,
    language,
    audience,
    effort,
    reasoning,
    budget,
  ]) {
    control.addEventListener("change", () => {
      confirm.checked = false;
      paintPreview();
    });
    control.addEventListener("input", paintPreview);
  }

  async function unlockCurrent() {
    const nextProfile = readProfile();
    if (!profileRequestReady(nextProfile)) {
      status.textContent =
        "Confirm privacy consent and the exact request parameters before contacting the Provider.";
      return false;
    }
    const result = await unlockLlm(passphrase.value, nextProfile);
    status.textContent = result.ok
      ? "LLM credential unlocked for this tab."
      : "Unlock failed. Check the passphrase and Provider.";
    return result.ok;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    let next;
    try {
      next = readProfile();
      const url = new URL(next.baseUrl);
      if (url.protocol !== "https:" || !next.model || !url.host) {
        throw new Error("profile");
      }
      if (!consent.checked || !confirm.checked) {
        throw new Error("consent");
      }
    } catch (error) {
      status.textContent =
        "Provide an HTTPS Base URL, Model, consent, and confirmed parameters.";
      return;
    }
    if (apiKey.value.trim()) {
      if (passphrase.value !== passphraseConfirm.value) {
        status.textContent = "Passphrases do not match.";
        return;
      }
      const saved = await saveCredential(apiKey.value, passphrase.value, next);
      if (!saved.ok) {
        status.textContent =
          saved.code === "passphrase_too_short"
            ? "The passphrase must contain at least 12 characters."
            : "The credential could not be encrypted.";
        return;
      }
      apiKey.value = "";
      passphraseConfirm.value = "";
    }
    saveLlmProfile(next);
    await paintCredential();
    status.textContent = "LLM Profile saved locally.";
  });

  form.querySelector("[data-llm-models-button]").addEventListener("click", async () => {
    if (!(await unlockCurrent())) return;
    const result = await fetchModels(readProfile());
    if (!result.ok) {
      status.textContent = "Model list could not be retrieved.";
      return;
    }
    models.replaceChildren();
    for (const id of result.models) {
      const option = document.createElement("option");
      option.value = id;
      models.append(option);
    }
    status.textContent = `Loaded ${result.models.length} Models.`;
  });

  form.querySelector("[data-llm-test]").addEventListener("click", async () => {
    if (!(await unlockCurrent())) return;
    const result = await fetchModels(readProfile());
    status.textContent = result.ok
      ? "Connection test succeeded via the Model list."
      : "Connection test failed.";
  });

  document.querySelector("[data-llm-unlock]").addEventListener("click", unlockCurrent);
  document.querySelector("[data-llm-lock]").addEventListener("click", () => {
    lockLlm();
    status.textContent = "LLM credential locked.";
  });
  document.querySelector("[data-llm-delete]").addEventListener("click", async () => {
    if (!window.confirm("Delete the encrypted LLM credential?")) return;
    const result = await deleteCredential();
    lockLlm();
    status.textContent = result.ok
      ? "Encrypted credential deleted."
      : "Delete failed.";
    await paintCredential();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeSettings);
} else {
  initializeSettings();
}
