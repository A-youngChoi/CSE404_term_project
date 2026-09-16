import "./styles.css";
import { api } from "./api.js";
import { h, mount, fmtTime, shortId } from "./dom.js";
import { ko } from "./i18n/ko.js";
import { errorNotice, modeBadge } from "./components.js";
import { renderOverview } from "./pages/overview.js";
import { renderPapers } from "./pages/papers.js";
import { renderSession } from "./pages/session.js";
import { renderAudience } from "./pages/audience.js";
import { renderTrace } from "./pages/trace.js";
import { renderCues } from "./pages/cues.js";
import { renderGuide } from "./pages/guide.js";

export const ROUTES = [
  { key: "overview", render: renderOverview },
  { key: "papers", render: renderPapers },
  { key: "session", render: renderSession },
  { key: "audience", render: renderAudience, needsSession: true },
  { key: "trace", render: renderTrace, needsSession: true },
  { key: "cues", render: renderCues, needsSession: true },
  { key: "guide", render: renderGuide },
];

// Only the selected session ID is kept, in memory and in the URL hash - no participant data in browser storage.
const state = { sessionId: null, sessions: [] };

export function parseHash(hash) {
  const parts = (hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const route = ROUTES.find((r) => r.key === parts[0]) ?? ROUTES[0];
  return { route, params: parts.slice(1) };
}

export function navigate(key, ...params) {
  const hash = `#/${[key, ...params].map(encodeURIComponent).join("/")}`;
  if (location.hash === hash) render();
  else location.hash = hash;
}

function setSession(id) {
  state.sessionId = id;
}

async function refreshSessions() {
  try {
    state.sessions = await api.sessions();
  } catch {
    state.sessions = [];
  }
  if (state.sessionId && !state.sessions.some((s) => s.session.id === state.sessionId)) state.sessionId = null;
  return state.sessions;
}

function sessionSelector() {
  const select = h(
    "select",
    { id: "session-select", "aria-label": ko.common.selectSession },
    h("option", { value: "" }, `— ${ko.common.selectSession} —`),
    state.sessions.map(({ session: s }) =>
      h(
        "option",
        { value: s.id, selected: s.id === state.sessionId },
        `${s.label || shortId(s.id)} · ${s.mock_mode ? "Mock" : "Ollama"} · ${fmtTime(s.created_at)}`,
      ),
    ),
  );
  select.addEventListener("change", () => {
    setSession(select.value || null);
    const { route } = parseHash(location.hash);
    navigate(route.key === "overview" || route.key === "papers" || route.key === "guide" ? "session" : route.key, ...(select.value ? [select.value] : []));
  });
  const current = state.sessions.find((s) => s.session.id === state.sessionId)?.session;
  return h("div", { class: "session-select" }, select, current ? modeBadge(current.mock_mode, current.llm_model) : null);
}

function layout(activeKey) {
  const nav = h(
    "nav",
    { class: "nav", "aria-label": "주요 메뉴" },
    ROUTES.map((r) => {
      const params = r.needsSession || r.key === "session" ? (state.sessionId ? [state.sessionId] : []) : [];
      const href = `#/${[r.key, ...params].map(encodeURIComponent).join("/")}`;
      return h("a", { href, class: r.key === activeKey ? "active" : "", "aria-current": r.key === activeKey ? "page" : null }, ko.nav[r.key]);
    }),
  );
  const header = h(
    "header",
    { class: "topbar" },
    h("div", {}, h("h1", {}, ko.appTitle), h("p", { class: "subtitle" }, ko.appSubtitle)),
    sessionSelector(),
  );
  const main = h("main", { id: "page", tabindex: "-1" });
  return { shell: h("div", { class: "shell" }, header, nav, main), main };
}

let renderToken = 0;

export async function render() {
  const token = ++renderToken;
  const { route, params } = parseHash(location.hash);
  if ((route.needsSession || route.key === "session") && params[0]) setSession(params[0]);
  await refreshSessions();
  if (token !== renderToken) return;
  const root = document.getElementById("app");
  const { shell, main } = layout(route.key);
  mount(root, shell);
  const ctx = { sessionId: state.sessionId, setSession, navigate, refreshSessions, params, rerender: render };
  try {
    await route.render(main, params, ctx);
  } catch (err) {
    mount(main, errorNotice(err));
  }
}

if (typeof window !== "undefined" && document.getElementById("app")) {
  window.addEventListener("hashchange", render);
  render();
}
