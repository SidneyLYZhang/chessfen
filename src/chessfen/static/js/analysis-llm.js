"use strict";

import { unlockCredential } from "/static/js/analysis-credential.js";

const PROFILE_KEY = "chessfen.analysis.llm.profile.v1";
const configuredLockMs = Number(window.CHESSFEN_LLM_LOCK_MS);
const LOCK_MS =
  Number.isFinite(configuredLockMs) && configuredLockMs > 0
    ? configuredLockMs
    : 30 * 60 * 1000;
let unlockedKey = null;
let unlockedHost = null;
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
  });
}

export function profileRequestReady(profile) {
  const host = new URL(profile.baseUrl).host;
  return (
    profile.consentHost === host &&
    profile.confirmedFingerprint === requestFingerprint(profile)
  );
}

export function lockLlm() {
  unlockedKey = null;
  unlockedHost = null;
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
  unlockedHost = new URL(profile.baseUrl).host;
  document.documentElement.dataset.llmLocked = "false";
  touchLock();
  return { ok: true };
}

export async function fetchModels(profile) {
  if (!unlockedKey || unlockedHost !== new URL(profile.baseUrl).host) {
    lockLlm();
    return { ok: false, code: "llm_locked" };
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

export async function requestExplanation(profile, payload) {
  const result = await chatJson(
    profile,
    explanationPrompt(payload, profile)
  );
  if (!result.ok) return result;
  const explanation = proseShape(result.parsed);
  if (!explanation) {
    return { ok: false, code: "invalid_llm_response" };
  }
  return { ok: true, explanation, usage: result.usage };
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

export function reasoningFailureText(code, messages) {
  const key = {
    reasoning_mandatory: "reasoning_mandatory",
    reasoning_unverified: "reasoning_unverified",
    reasoning_effort_unsupported: "reasoning_unsupported",
    reasoning_effort_unknown: "reasoning_unsupported",
    reasoning_capabilities_unknown: "reasoning_unknown",
  }[code];
  return key && messages ? messages[key] : null;
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
    max_tokens: profile.maxTokens,
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
      },
    };
  }
  const body = {
    model: profile.model,
    response_format: { type: "json_object" },
    max_completion_tokens: profile.maxTokens,
  };
  if (profile.reasoningEnabled) {
    body.reasoning_effort = profile.reasoningEffort;
  }
  return { ok: true, body };
}

async function chatJson(profile, messages, signal = null) {
  if (!unlockedKey || unlockedHost !== new URL(profile.baseUrl).host) {
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
