"use strict";

import {
  credentialMetadata,
  credentialSecurityAvailable,
  deleteCredential,
  saveCredential,
} from "/static/js/analysis-credential.js";
import {
  DEEPSEEK_COMPATIBILITY,
  KIMI_COMPATIBILITY,
  deepseekReasoningCapabilities,
  fetchModels,
  kimiReasoningCapabilities,
  llmUnlockedFor,
  loadLlmProfile,
  lockLlm,
  openaiReasoningCapabilities,
  profileRequestReady,
  providerRequestParameters,
  requestFingerprint,
  saveLlmProfile,
  unlockLlm,
  unlockLlmWithKey,
} from "/static/js/analysis-llm.js";
import { readMessages } from "/static/js/ui-messages.js";

const PROVIDER_BASE_URLS = {
  openai: "https://api.openai.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  deepseek: "https://api.deepseek.com",
};
const KIMI_BASE_URLS = {
  international: "https://api.moonshot.ai/v1",
  cn: "https://api.moonshot.cn/v1",
};

function initializeSettings() {
  const form = document.querySelector("[data-llm-form]");
  if (!form) return;
  const q = (name) => form.querySelector(`[data-llm-${name}]`);
  const provider = q("provider");
  const kimiRegion = q("kimi-region");
  const kimiRegionField = form.querySelector("[data-kimi-region-field]");
  const customDialect = q("custom-dialect");
  const customDialectField = form.querySelector("[data-custom-dialect-field]");
  const customBudgetField = q("custom-budget-field");
  const customBudgetFieldContainer = form.querySelector(
    "[data-custom-budget-field]"
  );
  const effortField = form.querySelector("[data-standard-effort-field]");
  const effortUnresolved = q("effort").querySelector(
    "[data-effort-unresolved]"
  );
  const baseUrl = q("base-url");
  const model = q("model");
  const models = q("models");
  const language = q("language");
  const audience = q("audience");
  const effort = q("effort");
  const reasoning = q("reasoning");
  const budget = q("budget");
  const consent = q("consent");
  const destination = q("destination");
  const confirm = q("confirm");
  const preview = q("preview");
  const capabilitiesText = q("capabilities");
  const compatibility = q("compatibility");
  const apiKey = document.querySelector("[data-llm-api-key]");
  const passphrase = document.querySelector("[data-llm-passphrase]");
  const passphraseConfirm = document.querySelector(
    "[data-llm-passphrase-confirm]"
  );
  const status = document.querySelector("[data-llm-status]");
  const meta = document.querySelector("[data-llm-credential-meta]");
  const insecure = document.querySelector("[data-llm-insecure]");
  const credentialState = document.querySelector(
    "[data-llm-credential-state]"
  );
  const unlockHint = document.querySelector("[data-llm-unlock-hint]");
  const actionHint = document.querySelector("[data-llm-action-hint]");
  const overviewProvider = document.querySelector("[data-overview-provider]");
  const overviewModel = document.querySelector("[data-overview-model]");
  const overviewProfile = document.querySelector("[data-overview-profile]");
  const overviewCredential = document.querySelector(
    "[data-overview-credential]"
  );
  const keyToggle = document.querySelector("[data-llm-key-toggle]");
  const keyToggleLabel = document.querySelector(
    "[data-llm-key-toggle-label]"
  );
  const unlockButton = document.querySelector("[data-llm-unlock]");
  const lockButton = document.querySelector("[data-llm-lock]");
  const deleteButton = document.querySelector("[data-llm-delete]");
  const stored = loadLlmProfile();
  let capabilityModel = stored?.reasoningCapabilities?.model || null;
  let capabilityMap = stored?.reasoningCapabilities
    ? { [capabilityModel]: stored.reasoningCapabilities }
    : {};
  const text = readMessages("llm-message");
  const uiText = readMessages("ui-message");
  let credentialInfo = { exists: false };
  window.addEventListener("chessfen:llm-lock", () => {
    status.textContent = text.locked_timeout;
    void refreshCredential();
  });

  if (!credentialSecurityAvailable()) {
    insecure.hidden = false;
    for (const control of document.querySelectorAll(
      "[data-llm-form] input, [data-llm-form] select, [data-llm-form] button, " +
        "[data-llm-passphrase], [data-llm-passphrase-confirm], " +
        "[data-llm-unlock], [data-llm-lock], [data-llm-delete]"
    )) {
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
      consentUrl: consent.checked ? normalizedUrl : null,
      kimiRegion: provider.value === "kimi" ? kimiRegion.value : null,
      customDialect:
        provider.value === "custom" ? customDialect.value : null,
      customBudgetField:
        provider.value === "custom" ? customBudgetField.value : null,
      reasoningCapabilities:
        provider.value === "openrouter" && capabilityModel === model.value.trim()
          ? capabilityMap[model.value.trim()] || null
          : null,
    };
    candidate.confirmedFingerprint = confirm.checked
      ? requestFingerprint(candidate)
      : null;
    return candidate;
  }

  function capabilitiesForModel() {
    if (provider.value === "openai") {
      return openaiReasoningCapabilities(model.value.trim());
    }
    if (provider.value === "deepseek") {
      return deepseekReasoningCapabilities(model.value.trim());
    }
    if (provider.value === "kimi") {
      return kimiReasoningCapabilities(model.value.trim(), kimiRegion.value);
    }
    return provider.value === "openrouter" &&
      capabilityModel === model.value.trim()
      ? capabilityMap[model.value.trim()] || null
      : null;
  }

  function applyProviderPresentation() {
    const deepseek =
      provider.value === "deepseek" &&
      !DEEPSEEK_COMPATIBILITY.browserDirectVerified;
    const kimi =
      provider.value === "kimi" &&
      !KIMI_COMPATIBILITY[kimiRegion.value]?.browserDirectVerified;
    const custom = provider.value === "custom";
    compatibility.hidden = !deepseek && !kimi && !custom;
    compatibility.textContent = deepseek
      ? text.deepseek_unverified
      : kimi
        ? text.kimi_unverified
        : custom
          ? text.custom_unverified
          : "";
    kimiRegionField.hidden = provider.value !== "kimi";
    customDialectField.hidden = !custom;
    customBudgetFieldContainer.hidden = !custom;
    effortUnresolved.hidden = !custom;
    effortUnresolved.disabled = !custom;
    if (!custom && effort.value === "") {
      effort.value = "max";
    }
  }

  function persistCapabilities() {
    const current = loadLlmProfile();
    if (
      current &&
      current.provider === "openrouter" &&
      current.model === model.value.trim()
    ) {
      current.reasoningCapabilities = capabilitiesForModel();
      saveLlmProfile(current);
    }
  }

  function appendCapabilityText(value) {
    capabilitiesText.textContent = [
      capabilitiesText.textContent,
      value,
    ]
      .filter(Boolean)
      .join(" ");
  }

  function applyCapabilities() {
    for (const option of effort.options) {
      option.disabled = false;
    }
    effort.disabled = false;
    reasoning.disabled = false;
    capabilitiesText.textContent = "";

    if (provider.value === "custom") {
      const dialect = customDialect.value;
      const needsEffort =
        reasoning.checked &&
        ["openai", "object", "deepseek"].includes(dialect);
      if (!dialect) {
        appendCapabilityText(text.custom_dialect_unresolved);
      }
      if (!customBudgetField.value) {
        appendCapabilityText(text.custom_budget_field_unresolved);
      }
      if (reasoning.checked && dialect === "none") {
        appendCapabilityText(text.reasoning_dialect_conflict);
      }
      effort.disabled = !needsEffort;
      if (needsEffort && !effort.value) {
        appendCapabilityText(text.reasoning_effort_unresolved);
      }
      return;
    }

    if (provider.value === "openai") {
      const caps = capabilitiesForModel();
      if (!caps.known) {
        capabilitiesText.textContent = text.reasoning_model_unknown;
        effort.disabled = true;
        return;
      }
      if (!caps.canDisableReasoning) {
        reasoning.checked = true;
        reasoning.disabled = true;
        appendCapabilityText(text.reasoning_always);
      }
      for (const option of effort.options) {
        option.disabled =
          option.value !== "max" &&
          !caps.supportedEfforts.includes(option.value);
      }
      appendCapabilityText(
        text.reasoning_supported.replace(
          "{values}",
          caps.supportedEfforts.join(", ")
        )
      );
      if (effort.value === "max") {
        appendCapabilityText(
          text.reasoning_max_mapped.replace("{value}", caps.maxEffort)
        );
      } else if (!caps.supportedEfforts.includes(effort.value)) {
        appendCapabilityText(text.reasoning_unsupported);
      }
      return;
    }

    if (provider.value === "kimi") {
      const caps = capabilitiesForModel();
      if (!caps.known) {
        capabilitiesText.textContent = text.kimi_model_unknown;
        effort.disabled = true;
        return;
      }
      if (!caps.canDisableReasoning) {
        reasoning.checked = true;
        reasoning.disabled = true;
        appendCapabilityText(text.reasoning_always);
      }
      if (!caps.effortApplicable) {
        effort.disabled = true;
        appendCapabilityText(text.reasoning_not_applicable);
        return;
      }
      for (const option of effort.options) {
        option.disabled = !caps.supportedEfforts.includes(option.value);
      }
      appendCapabilityText(
        text.reasoning_supported.replace(
          "{values}",
          caps.supportedEfforts.join(", ")
        )
      );
      if (!caps.supportedEfforts.includes(effort.value)) {
        appendCapabilityText(text.reasoning_unsupported);
      }
      return;
    }

    if (provider.value === "deepseek") {
      const caps = capabilitiesForModel();
      const efforts = caps.supportedEfforts;
      if (!Array.isArray(efforts) || !efforts.length) {
        capabilitiesText.textContent = text.reasoning_unknown;
        return;
      }
      for (const option of effort.options) {
        option.disabled = !efforts.includes(option.value);
      }
      appendCapabilityText(
        text.reasoning_supported.replace("{values}", efforts.join(", "))
      );
      if (!efforts.includes(effort.value)) {
        appendCapabilityText(text.reasoning_unsupported);
      }
      if (!reasoning.checked) {
        if (caps.known === false) {
          appendCapabilityText(text.reasoning_unverified);
        } else if (caps.canDisableReasoning === false) {
          appendCapabilityText(text.reasoning_mandatory);
        }
      }
      return;
    }

    if (provider.value === "openrouter") {
      const caps = capabilitiesForModel();
      if (!caps) {
        capabilitiesText.textContent = text.reasoning_unknown;
        return;
      }
      const efforts = caps.supportedEfforts;
      if (!Array.isArray(efforts) || !efforts.length) {
        capabilitiesText.textContent = text.reasoning_unknown;
        return;
      }
      for (const option of effort.options) {
        option.disabled = !efforts.includes(option.value);
      }
      appendCapabilityText(
        text.reasoning_supported.replace("{values}", efforts.join(", "))
      );
      if (!efforts.includes(effort.value)) {
        appendCapabilityText(text.reasoning_unsupported);
      }
    }
  }

  function paintDestination() {
    const value = baseUrl.value.trim().replace(/\/$/, "");
    destination.textContent = value
      ? text.destination.replace("{url}", value)
      : text.destination_missing;
  }

  function paintPreview() {
    const request = providerRequestParameters(readProfile());
    preview.textContent = JSON.stringify(
      request.ok ? request.body : { error: request.code },
      null,
      2
    );
  }

  function currentBaseUrl() {
    return baseUrl.value.trim().replace(/\/$/, "");
  }

  function paintOverview() {
    if (overviewProvider) {
      overviewProvider.textContent =
        provider.options[provider.selectedIndex]?.textContent ||
        provider.value;
    }
    if (overviewModel) {
      overviewModel.textContent = model.value.trim() || text.no_model_selected;
    }
    if (overviewProfile) {
      overviewProfile.textContent = loadLlmProfile()
        ? text.profile_saved_short
        : text.profile_not_saved;
    }
  }

  function paintCredentialState(info, unlocked) {
    const label = !info.exists
      ? text.credential_not_stored
      : unlocked
        ? text.credential_unlocked
        : text.credential_locked;
    if (credentialState) {
      credentialState.textContent = label;
      credentialState.dataset.state = !info.exists
        ? "none"
        : unlocked
          ? "unlocked"
          : "locked";
    }
    if (overviewCredential) {
      overviewCredential.textContent = label;
    }
    if (unlockButton) {
      unlockButton.disabled = !info.exists || unlocked;
    }
    if (lockButton) {
      lockButton.disabled = !unlocked;
    }
    if (deleteButton) {
      deleteButton.disabled = !info.exists;
    }
    const hint = apiKey.value.trim()
      ? text.credential_hint_draft
      : !info.exists
        ? text.credential_hint_none
        : unlocked
          ? text.credential_hint_unlocked
          : text.credential_hint_locked;
    if (unlockHint) {
      unlockHint.textContent = hint;
    }
    if (actionHint) {
      actionHint.textContent = hint;
    }
  }

  async function refreshCredential() {
    const info = await credentialMetadata();
    credentialInfo = info;
    meta.textContent = info.exists
      ? uiText.stored_ciphertext
          .replace("{provider}", info.provider)
          .replace("{target}", info.baseUrl || info.baseHost)
      : uiText.no_encrypted_credential;
    paintCredentialState(info, llmUnlockedFor(currentBaseUrl()));
    paintOverview();
    return info;
  }

  function clearModelList() {
    models.replaceChildren();
    capabilityModel = null;
    capabilityMap = {};
  }

  const storedRegion = stored?.kimiRegion;
  const inferredRegion = String(stored?.baseUrl || "").includes(
    "api.moonshot.cn"
  )
    ? "cn"
    : "international";
  const defaultKimiRegion = ["cn", "international"].includes(storedRegion)
    ? storedRegion
    : inferredRegion;
  const defaults = {
    provider: "openai",
    baseUrl: "https://api.openai.com/v1",
    model: "",
    language: document.documentElement.lang,
    audience: "club",
    reasoningEnabled: true,
    reasoningEffort: "max",
    maxTokens: 16384,
    consentUrl: null,
    confirmedFingerprint: null,
    kimiRegion: defaultKimiRegion,
    customDialect: "",
    customBudgetField: "",
    ...(stored || {}),
  };
  provider.value = defaults.provider;
  kimiRegion.value = ["cn", "international"].includes(defaults.kimiRegion)
    ? defaults.kimiRegion
    : defaultKimiRegion;
  customDialect.value = defaults.customDialect || "";
  customBudgetField.value = defaults.customBudgetField || "";
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
      Boolean(stored?.consentUrl) &&
      stored.consentUrl === defaults.baseUrl;
    confirm.checked = profileRequestReady(defaults);
  } catch (error) {
    consent.checked = false;
    confirm.checked = false;
  }
  applyProviderPresentation();
  applyCapabilities();
  paintDestination();
  paintPreview();
  void refreshCredential();
  status.textContent = stored ? text.profile_loaded : text.profile_missing;

  const invalidateBaseUrl = () => {
    consent.checked = false;
    confirm.checked = false;
    paintDestination();
    paintCredentialState(
      credentialInfo,
      llmUnlockedFor(currentBaseUrl())
    );
  };
  baseUrl.addEventListener("input", invalidateBaseUrl);
  baseUrl.addEventListener("change", invalidateBaseUrl);
  provider.addEventListener("change", () => {
    if (provider.value === "custom") {
      baseUrl.value = "";
      model.value = "";
      customDialect.value = "";
      customBudgetField.value = "";
      effort.value = "";
    } else {
      baseUrl.value =
        provider.value === "kimi"
          ? KIMI_BASE_URLS[kimiRegion.value] || KIMI_BASE_URLS.international
          : PROVIDER_BASE_URLS[provider.value] || PROVIDER_BASE_URLS.openai;
    }
    consent.checked = false;
    confirm.checked = false;
    clearModelList();
    applyProviderPresentation();
    applyCapabilities();
    paintDestination();
    paintPreview();
    void refreshCredential();
  });
  kimiRegion.addEventListener("change", () => {
    baseUrl.value =
      KIMI_BASE_URLS[kimiRegion.value] || KIMI_BASE_URLS.international;
    consent.checked = false;
    confirm.checked = false;
    clearModelList();
    paintDestination();
    paintPreview();
    void refreshCredential();
  });
  customDialect.addEventListener("change", () => {
    confirm.checked = false;
    applyCapabilities();
    paintPreview();
  });
  customBudgetField.addEventListener("change", () => {
    confirm.checked = false;
    applyCapabilities();
    paintPreview();
  });
  model.addEventListener("change", () => {
    capabilityModel = capabilityMap[model.value.trim()]
      ? model.value.trim()
      : null;
    confirm.checked = false;
    applyCapabilities();
    paintPreview();
  });
  model.addEventListener("input", () => {
    capabilityModel = capabilityMap[model.value.trim()]
      ? model.value.trim()
      : null;
    confirm.checked = false;
    applyCapabilities();
    paintPreview();
  });
  effort.addEventListener("change", applyCapabilities);
  reasoning.addEventListener("change", applyCapabilities);
  for (const control of [
    provider,
    baseUrl,
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

  function withConsent() {
    const nextProfile = readProfile();
    if (!profileRequestReady(nextProfile)) {
      status.textContent = text.consent_required;
      return null;
    }
    return nextProfile;
  }

  async function unlockStored() {
    const nextProfile = withConsent();
    if (!nextProfile) return false;
    if (!passphrase.value) {
      status.textContent = text.passphrase_required;
      await refreshCredential();
      return false;
    }
    const result = await unlockLlm(passphrase.value, nextProfile);
    status.textContent = result.ok
      ? text.unlock_ok
      : text.unlock_failed;
    await refreshCredential();
    return result.ok;
  }

  async function keyForRequest() {
    const nextProfile = withConsent();
    if (!nextProfile) return null;
    const draft = apiKey.value.trim();
    if (draft) {
      const result = unlockLlmWithKey(nextProfile, draft);
      if (!result.ok) {
        status.textContent = text.api_key_required;
        return null;
      }
      status.textContent = text.draft_key_ready;
      await refreshCredential();
      return nextProfile;
    }
    if (llmUnlockedFor(nextProfile.baseUrl)) {
      return nextProfile;
    }
    if (!passphrase.value) {
      status.textContent = text.passphrase_required;
      await refreshCredential();
      return null;
    }
    const result = await unlockLlm(passphrase.value, nextProfile);
    if (!result.ok) {
      status.textContent = text.unlock_failed;
      await refreshCredential();
      return null;
    }
    status.textContent = text.unlock_ok;
    await refreshCredential();
    return nextProfile;
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
        text.profile_incomplete;
      return;
    }
    let savedKey = false;
    if (apiKey.value.trim()) {
      if (passphrase.value !== passphraseConfirm.value) {
        status.textContent = text.passphrase_mismatch;
        return;
      }
      const saved = await saveCredential(apiKey.value, passphrase.value, next);
      if (!saved.ok) {
        status.textContent =
          saved.code === "passphrase_too_short"
            ? text.passphrase_too_short
            : text.credential_save_failed;
        return;
      }
      unlockLlmWithKey(next, apiKey.value);
      savedKey = true;
      apiKey.value = "";
      passphrase.value = "";
      passphraseConfirm.value = "";
    }
    saveLlmProfile(next);
    await refreshCredential();
    status.textContent = savedKey
      ? text.profile_saved_unlocked
      : text.profile_saved;
  });

  form.querySelector("[data-llm-models-button]").addEventListener("click", async () => {
    const profile = await keyForRequest();
    if (!profile) return;
    const result = await fetchModels(profile);
    if (!result.ok) {
      status.textContent = text.models_failed;
      return;
    }
    models.replaceChildren();
    capabilityMap = {};
    for (const item of result.models) {
      const option = document.createElement("option");
      option.value = item.id;
      models.append(option);
      capabilityMap[item.id] = item;
    }
    const selected = capabilityMap[model.value.trim()];
    if (selected) {
      capabilityModel = model.value.trim();
    }
    applyCapabilities();
    persistCapabilities();
    paintPreview();
    status.textContent = text.models_loaded.replace("{count}", String(result.models.length));
  });

  form.querySelector("[data-llm-test]").addEventListener("click", async () => {
    const profile = await keyForRequest();
    if (!profile) return;
    const result = await fetchModels(profile);
    status.textContent = result.ok
      ? text.connection_ok
      : text.connection_failed;
    await refreshCredential();
  });

  unlockButton.addEventListener("click", unlockStored);
  lockButton.addEventListener("click", async () => {
    lockLlm();
    passphrase.value = "";
    passphraseConfirm.value = "";
    status.textContent = text.locked;
    await refreshCredential();
  });
  deleteButton.addEventListener("click", async () => {
    if (!window.confirm(text.credential_delete_confirm)) return;
    const result = await deleteCredential();
    lockLlm();
    passphrase.value = "";
    passphraseConfirm.value = "";
    status.textContent = result.ok
      ? text.credential_deleted
      : text.credential_delete_failed;
    await refreshCredential();
  });

  keyToggle?.addEventListener("click", () => {
    const show = apiKey.type === "password";
    apiKey.type = show ? "text" : "password";
    if (keyToggleLabel) {
      keyToggleLabel.textContent = show
        ? keyToggleLabel.dataset.hide
        : keyToggleLabel.dataset.show;
    }
    keyToggle.setAttribute(
      "aria-label",
      show
        ? keyToggleLabel?.dataset.hide || "Hide"
        : keyToggleLabel?.dataset.show || "Show"
    );
  });
  for (const control of [apiKey, passphrase]) {
    control.addEventListener("input", () => {
      paintCredentialState(
        credentialInfo,
        llmUnlockedFor(currentBaseUrl())
      );
    });
  }
  model.addEventListener("input", paintOverview);
  model.addEventListener("change", paintOverview);
  provider.addEventListener("change", paintOverview);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeSettings);
} else {
  initializeSettings();
}
