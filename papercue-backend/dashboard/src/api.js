// Same-origin client for the local backend. Relative URLs only: the dashboard never
// contacts any host other than the one serving it (127.0.0.1).
import { ko } from "./i18n/ko.js";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get koMessage() {
    const map = {
      local_model_unavailable: ko.errors.localModel,
      malformed_model_output: ko.errors.malformed,
      consent_required: ko.errors.consent,
      not_found: ko.errors.notFound,
      invalid_session_state: ko.errors.invalidState,
      invalid_input: ko.errors.invalidInput,
      invalid_request: ko.errors.invalidInput,
      payload_too_large: ko.errors.tooLarge,
      conflict: ko.errors.conflict,
      network: ko.errors.backendDown,
    };
    return map[this.code] ?? ko.errors.generic;
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      referrerPolicy: "no-referrer",
    });
  } catch {
    throw new ApiError(0, "network", "backend unreachable");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(res.status, err.code ?? `http_${res.status}`, err.message ?? res.statusText, err.details);
  }
  return data;
}

export const api = {
  health: () => request("GET", "/health"),
  status: () => request("GET", "/config/status"),
  papers: () => request("GET", "/papers"),
  paper: (id) => request("GET", `/papers/${encodeURIComponent(id)}`),
  createPaper: (body) => request("POST", "/papers", body),
  uploadPaper: (body) => request("POST", "/papers/upload", body),
  editUnit: (pid, uid, body) => request("PATCH", `/papers/${encodeURIComponent(pid)}/units/${encodeURIComponent(uid)}`, body),
  reindex: (id) => request("POST", `/papers/${encodeURIComponent(id)}/reindex`),
  sessions: () => request("GET", "/sessions"),
  session: (id) => request("GET", `/sessions/${encodeURIComponent(id)}`),
  createSession: (body) => request("POST", "/sessions", body),
  deleteSession: (id) => request("DELETE", `/sessions/${encodeURIComponent(id)}`),
  resetSession: (id) => request("POST", `/sessions/${encodeURIComponent(id)}/reset`),
  exportSession: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/export`),
  setProfile: (id, body) => request("POST", `/sessions/${encodeURIComponent(id)}/profile`, body),
  profile: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/profile`),
  addTurn: (id, body) => request("POST", `/sessions/${encodeURIComponent(id)}/turns`, body),
  turns: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/turns`),
  audience: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/audience-model`),
  history: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/audience-model/history`),
  evidence: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/evidence`),
  state: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/conversation-state`),
  requestCue: (id) => request("POST", `/sessions/${encodeURIComponent(id)}/cue`, {}),
  autoCandidate: (id) => request("POST", `/sessions/${encodeURIComponent(id)}/auto-candidate`),
  cues: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/cues`),
  traces: (id) => request("GET", `/sessions/${encodeURIComponent(id)}/traces`),
  turnTrace: (sid, tid) => request("GET", `/sessions/${encodeURIComponent(sid)}/turns/${encodeURIComponent(tid)}/trace`),
};
