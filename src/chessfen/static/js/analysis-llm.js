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
      models: (payload.data || []).map((model) => model.id).filter(Boolean),
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
  if (!unlockedKey || unlockedHost !== new URL(profile.baseUrl).host) {
    lockLlm();
    return { ok: false, code: "llm_locked" };
  }
  if (!profileRequestReady(profile)) {
    return { ok: false, code: "llm_profile_unconfirmed" };
  }
  const body = {
    model: profile.model,
    messages: explanationPrompt(payload, profile),
    response_format: { type: "json_object" },
    max_completion_tokens: profile.maxTokens,
  };
  if (profile.reasoningEnabled) {
    body.reasoning_effort = profile.reasoningEffort;
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
      }
    );
    if (!response.ok) {
      return { ok: false, code: "provider_error", status: response.status };
    }
    const raw = await response.json();
    touchLock();
    const content = raw?.choices?.[0]?.message?.content;
    if (typeof content !== "string") {
      return { ok: false, code: "invalid_llm_response" };
    }
    let parsed;
    try {
      parsed = JSON.parse(content);
    } catch (error) {
      return { ok: false, code: "invalid_llm_response" };
    }
    const fields = ["summary", "why", "alternatives", "uncertainty"];
    if (fields.some((field) => typeof parsed[field] !== "string")) {
      return { ok: false, code: "invalid_llm_response" };
    }
    return {
      ok: true,
      explanation: {
        summary: parsed.summary,
        why: parsed.why,
        alternatives: parsed.alternatives,
        uncertainty: parsed.uncertainty,
      },
      usage: raw.usage || null,
    };
  } catch (error) {
    return { ok: false, code: "provider_unreachable" };
  }
}
