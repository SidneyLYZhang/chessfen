"use strict";

import { unlockCredential } from "/static/js/analysis-credential.js";

const PROFILE_KEY = "chessfen.analysis.llm.profile.v1";
const configuredLockMs = Number(window.CHESSFEN_LLM_LOCK_MS);
const LOCK_MS =
  Number.isFinite(configuredLockMs) && configuredLockMs > 0
    ? configuredLockMs
    : 30 * 60 * 1000;
let unlockedKey = null;
let unlockedBaseUrl = null;
let lockTimer = null;

export function loadLlmProfile() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(PROFILE_KEY) || "null");
    return stored && typeof stored === "object" ? stored : null;
  } catch (error) {
    return null;
  }
}

export function saveLlmProfile(profile) {
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
}

export function clearLlmProfile() {
  window.localStorage.removeItem(PROFILE_KEY);
  lockLlm();
}

export function requestFingerprint(profile) {
  return JSON.stringify({
    provider: profile.provider,
    baseUrl: profile.baseUrl,
    model: profile.model,
    reasoningEnabled: profile.reasoningEnabled,
    reasoningEffort: profile.reasoningEffort,
    maxTokens: profile.maxTokens,
    ...(profile.provider === "custom"
      ? {
          customDialect: profile.customDialect,
          customBudgetField: profile.customBudgetField,
        }
      : {}),
  });
}

export function profileRequestReady(profile) {
  try {
    const url = new URL(profile.baseUrl);
    if (url.protocol !== "https:" || !url.host) {
      return false;
    }
  } catch (error) {
    return false;
  }
  return (
    profile.consentUrl === profile.baseUrl &&
    profile.confirmedFingerprint === requestFingerprint(profile)
  );
}

export function lockLlm() {
  unlockedKey = null;
  unlockedBaseUrl = null;
  document.documentElement.dataset.llmLocked = "true";
  window.dispatchEvent(new CustomEvent("chessfen:llm-lock"));
  if (lockTimer !== null) {
    clearTimeout(lockTimer);
    lockTimer = null;
  }
}

function touchLock() {
  if (lockTimer !== null) {
    clearTimeout(lockTimer);
  }
  lockTimer = setTimeout(lockLlm, LOCK_MS);
}

export function llmUnlocked() {
  return Boolean(unlockedKey);
}

export async function unlockLlm(passphrase, profile) {
  const result = await unlockCredential(passphrase, profile);
  if (!result.ok) {
    return result;
  }
  unlockedKey = result.key;
  unlockedBaseUrl = profile.baseUrl;
  document.documentElement.dataset.llmLocked = "false";
  touchLock();
  return { ok: true };
}

export async function fetchModels(profile) {
  if (
    !unlockedKey ||
    unlockedBaseUrl !== profile.baseUrl ||
    !profileRequestReady(profile)
  ) {
    lockLlm();
    return { ok: false, code: "llm_profile_unconfirmed" };
  }
  try {
    const response = await fetch(`${profile.baseUrl.replace(/\/$/, "")}/models`, {
      headers: { Authorization: `Bearer ${unlockedKey}` },
    });
    if (!response.ok) {
      return { ok: false, code: "provider_error", status: response.status };
    }
    const payload = await response.json();
    touchLock();
    return {
      ok: true,
      models: (payload.data || [])
        .filter((model) => model?.id)
        .map((model) => ({
          id: model.id,
          model: model.id,
          ...modelReasoningCapabilities(model),
        })),
    };
  } catch (error) {
    return { ok: false, code: "provider_unreachable" };
  }
}

function explanationPrompt(payload, profile) {
  return [
    {
      role: "system",
      content:
        "Explain chess analysis in strict JSON with keys summary, why, alternatives, uncertainty. " +
        "Use only supplied engine facts and Candidate Lines; never invent numbers or override them. " +
        `Explanation language: ${profile.language}. Audience: ${profile.audience}.`,
    },
    {
      role: "user",
      content:
        "Structured analysis follows. Explain this Position for a chess player.\n" +
        JSON.stringify(payload),
    },
  ];
}

export async function requestExplanation(profile, payload, signal = null) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await chatJson(
      profile,
      explanationPrompt(payload, profile),
      signal
    );
    if (!result.ok) {
      if (result.code === "invalid_llm_response" && attempt === 0) continue;
      return result;
    }
    const explanation = proseShape(result.parsed);
    if (!explanation) {
      if (attempt === 0) continue;
      return { ok: false, code: "invalid_llm_response" };
    }
    return { ok: true, explanation, usage: result.usage };
  }
  return { ok: false, code: "invalid_llm_response" };
}

function usageSummary(usage) {
  if (!usage || typeof usage !== "object") return null;
  const prompt = Number(usage.prompt_tokens) || 0;
  const completion = Number(usage.completion_tokens) || 0;
  const reasoning =
    Number(usage.completion_tokens_details?.reasoning_tokens) || 0;
  return {
    prompt,
    completion,
    reasoning,
    total: Number(usage.total_tokens) || prompt + completion,
  };
}

export function modelReasoningCapabilities(model) {
  const efforts =
    model?.reasoning?.supported_efforts ||
    model?.capabilities?.reasoning?.supported_efforts ||
    null;
  const supportedParameters = model?.supported_parameters || [];
  const supportsReasoning =
    Boolean(model?.supports_reasoning) ||
    (Array.isArray(efforts) && efforts.length > 0) ||
    supportedParameters.includes("reasoning") ||
    supportedParameters.includes("reasoning_effort");
  return {
    supportsReasoning,
    supportedEfforts: Array.isArray(efforts) ? efforts : null,
  };
}

const OPENAI_CAPABILITY_SOURCE =
  "https://platform.openai.com/docs/api-reference/chat/create";
const OPENAI_CAPABILITY_VERIFIED = "2026-11-26";
const OPENAI_MAX_EFFORT = "high";

export function openaiReasoningCapabilities(model) {
  const normalized = String(model || "").trim();
  let supportedEfforts = [];
  let canDisableReasoning = true;
  let disableEffort = null;
  if (/^gpt-5\.1(?:[.-]|$)/i.test(normalized)) {
    supportedEfforts = ["low", "medium", "high"];
    disableEffort = "none";
  } else if (/^gpt-5(?:[.-]|$)/i.test(normalized)) {
    supportedEfforts = ["minimal", "low", "medium", "high"];
    canDisableReasoning = false;
  } else if (/^o[134](?:[.-]|$)/i.test(normalized)) {
    supportedEfforts = ["low", "medium", "high"];
    canDisableReasoning = false;
  }
  return {
    model: normalized,
    known: supportedEfforts.length > 0,
    supportedEfforts,
    canDisableReasoning,
    disableEffort,
    maxEffort: OPENAI_MAX_EFFORT,
    source: OPENAI_CAPABILITY_SOURCE,
    verified: OPENAI_CAPABILITY_VERIFIED,
  };
}

const DEEPSEEK_EFFORTS = ["low", "high", "max"];
const DEEPSEEK_KNOWN_MODELS = new Set(["deepseek-flash", "deepseek-v4-pro"]);
const DEEPSEEK_CAPABILITY_SOURCE =
  "https://api-docs.deepseek.com/api/create-chat-completion/";
const DEEPSEEK_CAPABILITY_VERIFIED = "2026-11-25";

export const DEEPSEEK_COMPATIBILITY = {
  browserDirectVerified: false,
  source: "https://api-docs.deepseek.com/",
  checked: "2026-11-25",
};

const KIMI_K3_CAPABILITIES = {
  supportedEfforts: ["low", "high", "max"],
  effortApplicable: true,
  thinkingField: false,
  canDisableReasoning: false,
};
const KIMI_K27_CAPABILITIES = {
  supportedEfforts: [],
  effortApplicable: false,
  thinkingField: false,
  canDisableReasoning: false,
};
const KIMI_K26_CAPABILITIES = {
  supportedEfforts: [],
  effortApplicable: false,
  thinkingField: true,
  canDisableReasoning: true,
};
const KIMI_MODELS = {
  "kimi-k3": KIMI_K3_CAPABILITIES,
  "kimi-k3.1-pro": KIMI_K3_CAPABILITIES,
  "kimi-k3.1-pro-highspeed": KIMI_K3_CAPABILITIES,
  "kimi-k2.7-code": KIMI_K27_CAPABILITIES,
  "kimi-k2.7-code-highspeed": KIMI_K27_CAPABILITIES,
  "kimi-k2.6": KIMI_K26_CAPABILITIES,
};
const KIMI_CAPABILITY_SOURCES = {
  international: "https://platform.kimi.ai/docs/api/models-overview",
  cn: "https://platform.kimi.com/docs/api/models-overview",
};
const KIMI_CAPABILITY_VERIFIED = "2026-11-26";

export const KIMI_COMPATIBILITY = {
  international: {
    browserDirectVerified: false,
    source: "https://platform.kimi.ai/docs/api/overview",
    checked: "2026-11-26",
  },
  cn: {
    browserDirectVerified: false,
    source: "https://platform.kimi.com/docs/api/overview",
    checked: "2026-11-26",
  },
};

export function kimiReasoningCapabilities(
  model, region = "international"
) {
  const normalized = String(model || "").trim();
  const known = KIMI_MODELS[normalized];
  return {
    model: normalized,
    known: Boolean(known),
    supportedEfforts: known ? [...known.supportedEfforts] : [],
    effortApplicable: known?.effortApplicable || false,
    thinkingField: known?.thinkingField || false,
    canDisableReasoning: known?.canDisableReasoning || false,
    source:
      KIMI_CAPABILITY_SOURCES[region] ||
      KIMI_CAPABILITY_SOURCES.international,
    verified: KIMI_CAPABILITY_VERIFIED,
  };
}

export function deepseekReasoningCapabilities(model) {
  const normalized = String(model || "").trim();
  const known = DEEPSEEK_KNOWN_MODELS.has(normalized);
  return {
    model: normalized,
    known,
    supportedEfforts: [...DEEPSEEK_EFFORTS],
    // DeepSeek's documented catalog supports non-thinking mode. An unknown ID
    // is never assumed to support it, so ChessFEN cannot silently disable Reasoning.
    canDisableReasoning: known,
    source: DEEPSEEK_CAPABILITY_SOURCE,
    verified: DEEPSEEK_CAPABILITY_VERIFIED,
  };
}

export function llmFailureMessage(code, messages) {
  const key = {
    reasoning_mandatory: "reasoning_mandatory",
    reasoning_unverified: "reasoning_unverified",
    reasoning_model_unknown: "reasoning_model_unknown",
    reasoning_effort_unsupported: "reasoning_unsupported",
    reasoning_effort_unknown: "reasoning_unsupported",
    reasoning_capabilities_unknown: "reasoning_unknown",
    reasoning_effort_unresolved: "reasoning_effort_unresolved",
    reasoning_dialect_conflict: "reasoning_dialect_conflict",
    custom_dialect_unresolved: "custom_dialect_unresolved",
    custom_budget_field_unresolved: "custom_budget_field_unresolved",
    kimi_model_unknown: "kimi_model_unknown",
    provider_unreachable: "provider_unreachable",
  }[code];
  return key && messages ? messages[key] : null;
}

const CUSTOM_DIALECTS = new Set([
  "openai",
  "object",
  "deepseek",
  "thinking",
  "none",
]);
const CUSTOM_BUDGET_FIELDS = new Set([
  "max_tokens",
  "max_completion_tokens",
]);

function resolveCustomRequest(profile) {
  if (!CUSTOM_DIALECTS.has(profile.customDialect)) {
    return { ok: false, code: "custom_dialect_unresolved" };
  }
  if (!CUSTOM_BUDGET_FIELDS.has(profile.customBudgetField)) {
    return { ok: false, code: "custom_budget_field_unresolved" };
  }
  const enabled = Boolean(profile.reasoningEnabled);
  const effort = profile.reasoningEffort;
  const reasoning =
    profile.customDialect === "openai" ||
    profile.customDialect === "object" ||
    profile.customDialect === "deepseek";
  if (enabled && reasoning && !effort) {
    return { ok: false, code: "reasoning_effort_unresolved" };
  }
  if (enabled && profile.customDialect === "none") {
    return { ok: false, code: "reasoning_dialect_conflict" };
  }
  const body = {
    model: profile.model,
    response_format: { type: "json_object" },
    [profile.customBudgetField]: profile.maxTokens,
  };
  if (profile.customDialect === "openai") {
    if (enabled) {
      body.reasoning_effort = effort;
    }
  } else if (profile.customDialect === "object") {
    body.reasoning = enabled
      ? { enabled: true, effort }
      : { enabled: false };
  } else if (profile.customDialect === "deepseek") {
    body.thinking = { type: enabled ? "enabled" : "disabled" };
    if (enabled) {
      body.reasoning_effort = effort;
    }
  } else if (profile.customDialect === "thinking") {
    body.thinking = { type: enabled ? "enabled" : "disabled" };
  }
  return { ok: true, body };
}

function resolveKimiReasoning(profile) {
  const capabilities = kimiReasoningCapabilities(profile.model);
  if (!capabilities.known) {
    return { ok: false, code: "kimi_model_unknown" };
  }
  if (!profile.reasoningEnabled) {
    if (!capabilities.canDisableReasoning) {
      return { ok: false, code: "reasoning_mandatory" };
    }
    return { ok: true, parameters: { thinking: { type: "disabled" } } };
  }
  if (capabilities.effortApplicable) {
    if (!capabilities.supportedEfforts.includes(profile.reasoningEffort)) {
      return { ok: false, code: "reasoning_effort_unsupported" };
    }
    return {
      ok: true,
      parameters: { reasoning_effort: profile.reasoningEffort },
    };
  }
  if (capabilities.thinkingField) {
    return { ok: true, parameters: { thinking: { type: "enabled" } } };
  }
  return { ok: true, parameters: {} };
}

function resolveDeepSeekReasoning(profile) {
  const capabilities = deepseekReasoningCapabilities(profile.model);
  if (!profile.reasoningEnabled) {
    if (!capabilities.known) {
      return { ok: false, code: "reasoning_unverified" };
    }
    if (!capabilities.canDisableReasoning) {
      return { ok: false, code: "reasoning_mandatory" };
    }
    return { ok: true, parameters: { thinking: { type: "disabled" } } };
  }
  if (!capabilities.supportedEfforts.includes(profile.reasoningEffort)) {
    return { ok: false, code: "reasoning_effort_unsupported" };
  }
  return {
    ok: true,
    parameters: {
      thinking: { type: "enabled" },
      reasoning_effort: profile.reasoningEffort,
    },
  };
}

function resolveOpenAiReasoning(profile) {
  const capabilities = openaiReasoningCapabilities(profile.model);
  if (!capabilities.known) {
    return { ok: false, code: "reasoning_model_unknown" };
  }
  if (!profile.reasoningEnabled) {
    if (!capabilities.canDisableReasoning) {
      return { ok: false, code: "reasoning_mandatory" };
    }
    return {
      ok: true,
      parameters: capabilities.disableEffort
        ? { reasoning_effort: capabilities.disableEffort }
        : {},
    };
  }
  const effort =
    profile.reasoningEffort === "max"
      ? capabilities.maxEffort
      : profile.reasoningEffort;
  if (!capabilities.supportedEfforts.includes(effort)) {
    return { ok: false, code: "reasoning_effort_unsupported" };
  }
  return { ok: true, parameters: { reasoning_effort: effort } };
}

function resolveReasoningForProfile(profile) {
  if (profile.provider !== "openrouter") {
    return { ok: true };
  }
  const capabilities = profile.reasoningCapabilities;
  if (!profile.reasoningEnabled) {
    return { ok: true, reasoning: { enabled: false } };
  }
  if (!capabilities || !capabilities.supportsReasoning) {
    return { ok: false, code: "reasoning_capabilities_unknown" };
  }
  if (
    Array.isArray(capabilities.supportedEfforts) &&
    !capabilities.supportedEfforts.includes(profile.reasoningEffort)
  ) {
    return { ok: false, code: "reasoning_effort_unsupported" };
  }
  if (
    !Array.isArray(capabilities.supportedEfforts) &&
    profile.reasoningEffort !== "none"
  ) {
    return { ok: false, code: "reasoning_effort_unknown" };
  }
  return {
    ok: true,
    reasoning: {
      enabled: true,
      effort: profile.reasoningEffort,
      exclude: true,
    },
  };
}

export function providerRequestParameters(profile) {
  const common = {
    model: profile.model,
    response_format: { type: "json_object" },
  };
  if (profile.provider === "openrouter") {
    const reasoning = resolveReasoningForProfile(profile);
    if (!reasoning.ok) {
      return { ok: false, code: reasoning.code };
    }
    return {
      ok: true,
      body: {
        ...common,
        reasoning: reasoning.reasoning,
        provider: { require_parameters: true },
        max_tokens: profile.maxTokens,
      },
    };
  }
  if (profile.provider === "deepseek") {
    const reasoning = resolveDeepSeekReasoning(profile);
    if (!reasoning.ok) {
      return { ok: false, code: reasoning.code };
    }
    return {
      ok: true,
      body: {
        ...common,
        ...reasoning.parameters,
        max_tokens: profile.maxTokens,
      },
    };
  }
  if (profile.provider === "kimi") {
    const reasoning = resolveKimiReasoning(profile);
    if (!reasoning.ok) {
      return { ok: false, code: reasoning.code };
    }
    return {
      ok: true,
      body: {
        ...common,
        ...reasoning.parameters,
        max_completion_tokens: profile.maxTokens,
      },
    };
  }
  if (profile.provider === "custom") {
    return resolveCustomRequest(profile);
  }
  const reasoning = resolveOpenAiReasoning(profile);
  if (!reasoning.ok) {
    return { ok: false, code: reasoning.code };
  }
  return {
    ok: true,
    body: {
      model: profile.model,
      response_format: { type: "json_object" },
      max_completion_tokens: profile.maxTokens,
      ...reasoning.parameters,
    },
  };
}

async function chatJson(profile, messages, signal = null) {
  if (!unlockedKey || unlockedBaseUrl !== profile.baseUrl) {
    lockLlm();
    return { ok: false, code: "llm_locked" };
  }
  if (!profileRequestReady(profile)) {
    return { ok: false, code: "llm_profile_unconfirmed" };
  }
  const parameters = providerRequestParameters(profile);
  if (!parameters.ok) {
    return { ok: false, code: parameters.code, retryable: false };
  }
  const body = { ...parameters.body, messages };
  let abortedByTimeout = false;
  let requestSignal = signal;
  if (window.AbortSignal?.any && window.AbortSignal?.timeout) {
    requestSignal = window.AbortSignal.any([
      signal || new AbortController().signal,
      window.AbortSignal.timeout(120000),
    ]);
  }
  try {
    const response = await fetch(
      `${profile.baseUrl.replace(/\/$/, "")}/chat/completions`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${unlockedKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: requestSignal,
      }
    );
    if (!response.ok) {
      return {
        ok: false,
        code: "provider_error",
        status: response.status,
        retryable: true,
      };
    }
    const raw = await response.json();
    touchLock();
    const choice = raw?.choices?.[0];
    const finishReason = choice?.finish_reason;
    if (finishReason === "length") {
      return { ok: false, code: "budget_exhausted", retryable: false };
    }
    if (
      finishReason === "content_filter" ||
      finishReason === "insufficient_system_resource" ||
      finishReason === "aborted"
    ) {
      return {
        ok: false,
        code: "provider_incomplete",
        retryable: finishReason !== "content_filter",
      };
    }
    const content = choice?.message?.content;
    if (typeof content !== "string" || !content.trim()) {
      return { ok: false, code: "invalid_llm_response", retryable: false };
    }
    let parsed;
    try {
      parsed = JSON.parse(content);
    } catch (error) {
      return { ok: false, code: "invalid_llm_response", retryable: false };
    }
    return { ok: true, parsed, usage: usageSummary(raw.usage) };
  } catch (error) {
    if (error?.name === "AbortError") {
      if (signal?.aborted) return { ok: false, code: "cancelled" };
      abortedByTimeout = true;
    }
    return {
      ok: false,
      code: abortedByTimeout ? "provider_timeout" : "provider_unreachable",
      retryable: true,
    };
  }
}

function proseShape(parsed) {
  const fields = ["summary", "why", "alternatives", "uncertainty"];
  if (fields.some((field) => typeof parsed[field] !== "string")) return null;
  return {
    summary: parsed.summary,
    why: parsed.why,
    alternatives: parsed.alternatives,
    uncertainty: parsed.uncertainty,
  };
}

export async function requestKeyPlyBatch(profile, payload, signal = null) {
  const targetPlyIds = new Set(payload.targets.map((target) => target.ply));
  const messages = [
    {
      role: "system",
      content:
        "Return strict JSON: {\"explanations\":[{\"ply\":number,\"summary\":string,\"why\":string,\"alternatives\":string,\"uncertainty\":string}]}. " +
        "Explain only the supplied target Ply IDs, using only supplied facts and Candidate Lines. Never replace engine numbers. " +
        "State insufficient evidence in uncertainty. " +
        `Language: ${profile.language}. Audience: ${profile.audience}.`,
    },
    { role: "user", content: JSON.stringify(payload) },
  ];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await chatJson(profile, messages, signal);
    if (!result.ok) {
      if (result.code === "invalid_llm_response" && attempt === 0) continue;
      return result;
    }
    if (!Array.isArray(result.parsed.explanations)) {
      if (attempt === 0) continue;
      return { ok: false, code: "invalid_llm_response" };
    }
    const explanations = {};
    let invalid = false;
    for (const item of result.parsed.explanations) {
      if (!targetPlyIds.has(item.ply) || explanations[item.ply]) {
        invalid = true;
        break;
      }
      const prose = proseShape(item);
      if (!prose) {
        invalid = true;
        break;
      }
      explanations[item.ply] = prose;
    }
    if (invalid || Object.keys(explanations).length !== targetPlyIds.size) {
      if (attempt === 0) continue;
      return { ok: false, code: "invalid_llm_response" };
    }
    return { ok: true, explanations, usage: result.usage };
  }
  return { ok: false, code: "invalid_llm_response" };
}

export async function requestOverallSummary(profile, payload, signal = null) {
  const messages = [
    {
      role: "system",
      content:
        "Return strict JSON: {\"summary\":string,\"uncertainty\":string}. Summarize the supplied Game or Position analysis. " +
        "Use only supplied facts; do not invent strategy or change numbers. State insufficient evidence explicitly. " +
        `Language: ${profile.language}. Audience: ${profile.audience}.`,
    },
    { role: "user", content: JSON.stringify(payload) },
  ];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await chatJson(profile, messages, signal);
    if (!result.ok) {
      if (result.code === "invalid_llm_response" && attempt === 0) continue;
      return result;
    }
    if (
      typeof result.parsed.summary !== "string" ||
      typeof result.parsed.uncertainty !== "string"
    ) {
      if (attempt === 0) continue;
      return { ok: false, code: "invalid_llm_response" };
    }
    return {
      ok: true,
      summary: {
        summary: result.parsed.summary,
        uncertainty: result.parsed.uncertainty,
      },
      usage: result.usage,
    };
  }
  return { ok: false, code: "invalid_llm_response" };
}
