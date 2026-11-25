"use strict";

const DB_NAME = "chessfen-credentials";
const STORE = "credentials";
const RECORD_ID = "llm";
const ITERATIONS = 210000;

function secureCryptoAvailable() {
  return Boolean(window.isSecureContext && window.crypto?.subtle);
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE)) {
        database.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("idb_open"));
  });
}

async function getRecord() {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE, "readonly");
      const request = transaction.objectStore(STORE).get(RECORD_ID);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error || new Error("idb_read"));
    });
  } finally {
    database.close();
  }
}

async function putRecord(record) {
  const database = await openDatabase();
  try {
    await new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).put(record);
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error || new Error("idb"));
    });
  } finally {
    database.close();
  }
}

function deriveKey(passphrase, salt, iterations = ITERATIONS) {
  const material = new TextEncoder().encode(passphrase);
  return window.crypto.subtle
    .importKey("raw", material, "PBKDF2", false, ["deriveKey"])
    .then((base) =>
      window.crypto.subtle.deriveKey(
        {
          name: "PBKDF2",
          hash: "SHA-256",
          salt,
          iterations,
        },
        base,
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"]
      )
    );
}

export function credentialSecurityAvailable() {
  return secureCryptoAvailable();
}

export async function credentialMetadata() {
  if (!secureCryptoAvailable()) {
    return { exists: false, secure: false };
  }
  try {
    const record = await getRecord();
    return {
      exists: Boolean(record),
      secure: true,
      provider: record?.provider || null,
      baseHost: record?.baseHost || null,
      updatedAt: record?.updatedAt || null,
    };
  } catch (error) {
    return { exists: false, secure: true, error: "credential_read_failed" };
  }
}

export async function saveCredential(apiKey, passphrase, profile) {
  if (!secureCryptoAvailable()) {
    return { ok: false, code: "secure_context_required" };
  }
  if (passphrase.length < 12) {
    return { ok: false, code: "passphrase_too_short" };
  }
  if (!apiKey.trim()) {
    return { ok: false, code: "api_key_required" };
  }
  try {
    const salt = window.crypto.getRandomValues(new Uint8Array(16));
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(passphrase, salt);
    const ciphertext = await window.crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      new TextEncoder().encode(apiKey.trim())
    );
    await putRecord({
      id: RECORD_ID,
      version: 1,
      iterations: ITERATIONS,
      salt,
      iv,
      ciphertext,
      provider: profile.provider,
      baseHost: new URL(profile.baseUrl).host,
      updatedAt: Date.now(),
    });
    return { ok: true };
  } catch (error) {
    return { ok: false, code: "credential_save_failed" };
  }
}

export async function unlockCredential(passphrase, profile) {
  if (!secureCryptoAvailable()) {
    return { ok: false, code: "secure_context_required" };
  }
  try {
    const record = await getRecord();
    if (!record) {
      return { ok: false, code: "credential_missing" };
    }
    if (
      record.provider !== profile.provider ||
      record.baseHost !== new URL(profile.baseUrl).host
    ) {
      return { ok: false, code: "credential_provider_mismatch" };
    }
    const key = await deriveKey(
      passphrase,
      record.salt,
      record.iterations || ITERATIONS
    );
    const plaintext = await window.crypto.subtle.decrypt(
      { name: "AES-GCM", iv: record.iv },
      key,
      record.ciphertext
    );
    return { ok: true, key: new TextDecoder().decode(plaintext) };
  } catch (error) {
    return { ok: false, code: "credential_unlock_failed" };
  }
}

export async function deleteCredential() {
  try {
    const database = await openDatabase();
    try {
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE, "readwrite");
        transaction.objectStore(STORE).delete(RECORD_ID);
        transaction.oncomplete = resolve;
        transaction.onerror = () =>
          reject(transaction.error || new Error("idb"));
      });
    } finally {
      database.close();
    }
    return { ok: true };
  } catch (error) {
    return { ok: false, code: "credential_delete_failed" };
  }
}
