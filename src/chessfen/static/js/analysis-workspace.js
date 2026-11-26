"use strict";

const DB_NAME = "chessfen-analysis";
const STORE = "workspaces";
const RECORD_ID = "current";
const ACTIVE_MARKER = "chessfen.analysis.workspace.active";
const VERSION = 1;

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

async function withStore(mode, action) {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE, mode);
      const store = transaction.objectStore(STORE);
      let result;
      try {
        result = action(store);
      } catch (error) {
        reject(error);
        return;
      }
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error || new Error("idb"));
      transaction.onabort = () => reject(transaction.error || new Error("idb"));
    });
  } finally {
    database.close();
  }
}

export async function saveWorkspace(record) {
  const payload = {
    ...record,
    id: RECORD_ID,
    version: VERSION,
    updatedAt: Date.now(),
  };
  try {
    await withStore("readwrite", (store) => {
      store.put(payload);
      return payload;
    });
    window.localStorage.setItem(ACTIVE_MARKER, "1");
    return { ok: true };
  } catch (error) {
    return { ok: false, code: "workspace_save_failed" };
  }
}

export async function loadWorkspace() {
  try {
    const database = await openDatabase();
    try {
      const record = await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE, "readonly");
        const request = transaction.objectStore(STORE).get(RECORD_ID);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () =>
          reject(request.error || new Error("idb_read"));
      });
      if (!record) {
        return { ok: true, record: null };
      }
      if (
        record.version !== VERSION ||
        !["position", "game"].includes(record.kind) ||
        record.input === null ||
        typeof record.input !== "object"
      ) {
        return { ok: false, code: "workspace_corrupt", record: null };
      }
      return { ok: true, record };
    } finally {
      database.close();
    }
  } catch (error) {
    return { ok: false, code: "workspace_read_failed", record: null };
  }
}

export async function clearWorkspace() {
  try {
    await withStore("readwrite", (store) => store.delete(RECORD_ID));
  } catch (error) {
    return { ok: false, code: "workspace_clear_failed" };
  }
  window.localStorage.removeItem(ACTIVE_MARKER);
  return { ok: true };
}

export function workspaceIsActive() {
  return window.localStorage.getItem(ACTIVE_MARKER) === "1";
}

export const SEARCH_PRESETS = {
  fast: { depth: 14, movetime: 1000 },
  standard: { depth: 18, movetime: 2000 },
  deep: { depth: 22, movetime: 4000 },
};

export function engineSnapshot(
  settings,
  runtime = "single-threaded",
  mixed = false,
  fallback = false
) {
  const presets = {
    fast: { depth: 14, movetime: 1000 },
    standard: { depth: 18, movetime: 2000 },
    deep: { depth: 22, movetime: 4000 },
  };
  const preset = Object.hasOwn(presets, settings.preset)
    ? settings.preset
    : "standard";
  return {
    name: "Stockfish",
    version: "19.0.1",
    resource: settings.resource === "full" ? "full" : "lite",
    runtime,
    mixed: Boolean(mixed),
    fallback: Boolean(fallback),
    preset,
    multipv: settings.multipv,
    depth: presets[preset].depth,
    movetime: presets[preset].movetime,
  };
}

export function engineSettingsCompatible(
  saved,
  settings,
  runtime = "single-threaded"
) {
  if (!saved) {
    return true;
  }
  const current = engineSnapshot(settings, runtime);
  return (
    saved.version === current.version &&
    saved.resource === current.resource &&
    (saved.mixed === true ||
      saved.fallback === true ||
      saved.runtime === current.runtime) &&
    saved.preset === current.preset &&
    saved.multipv === current.multipv &&
    saved.depth === current.depth &&
    saved.movetime === current.movetime
  );
}
