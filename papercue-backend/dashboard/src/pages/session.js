import { api } from "../api.js";
import { h, mount, fmtTime, shortId } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import {
  badge, confirmDialog, devJson, empty, errorNotice, finalStatusBadge, kv, loading, modeBadge, notice, sectionTitle,
} from "../components.js";
import { explainDecision, explainStatus } from "../explain.js";

const MAX_TURN_CHARS = 2000;
const csv = (text) => text.split(",").map((t) => t.trim()).filter(Boolean);

export function createSessionForm(papers, onCreated) {
  const s = ko.session;
  if (!papers.length) return h("section", { class: "card" }, h("h3", {}, s.create), empty(s.noPapers));
  const paper = h("select", { id: "new-session-paper" }, papers.map((p) => h("option", { value: p.id }, p.title)));
  const consent = h("input", { type: "checkbox", id: "new-session-consent" });
  const provider = h("select", { id: "new-session-provider" },
    h("option", { value: "mock" }, s.providerMock), h("option", { value: "ollama" }, s.providerOllama));
  const lang = h("select", {}, h("option", { value: "ko" }, s.langKo), h("option", { value: "en" }, s.langEn));
  const labelInput = h("input", { type: "text", maxlength: 100, placeholder: "예: 파일럿 테스트 1" });
  const submit = h("button", { type: "submit", class: "btn", disabled: true }, s.create);
  const status = h("div", { "aria-live": "polite" });
  consent.addEventListener("change", () => { submit.disabled = !consent.checked; });
  const form = h("form", { class: "form card", id: "create-session-form" },
    h("h3", {}, s.create),
    h("label", {}, s.paper, paper),
    h("fieldset", { class: "consent" },
      h("legend", {}, s.consent),
      h("label", { class: "check" }, consent, " ", s.consentText)),
    h("label", {}, s.provider, provider),
    h("label", {}, s.cueLanguage, lang),
    h("label", {}, s.label, labelInput),
    submit, status);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!consent.checked) return mount(status, notice(s.consentRequired, "error"));
    try {
      const created = await api.createSession({
        paper_id: paper.value, consent_confirmed: true, mode: "on_demand",
        llm_provider: provider.value, cue_language: lang.value, label: labelInput.value.trim() || null,
      });
      onCreated(created.id);
    } catch (err) {
      mount(status, errorNotice(err));
    }
  });
  return form;
}

function profileForm(sessionId, existing, onSaved) {
  const s = ko.session;
  const fields = [
    ["research_topics", s.researchTopics],
    ["recent_paper_keywords", s.recentKeywords],
    ["familiar_methods", s.familiarMethods],
    ["application_domains", s.domains],
    ["connection_notes", s.notes],
  ];
  const inputs = Object.fromEntries(fields.map(([k]) => [k, h("input", { type: "text", value: (existing?.[k] ?? []).join(", ") })]));
  const status = h("div", { "aria-live": "polite" });
  const form = h("form", { class: "form card" },
    h("h3", {}, s.profile),
    h("p", { class: "muted" }, s.profileHint),
    fields.map(([k, text]) => h("label", {}, text, inputs[k])),
    h("button", { type: "submit", class: "btn" }, s.saveProfile), status);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const body = Object.fromEntries(fields.map(([k]) => [k, csv(inputs[k].value)]));
      const r = await api.setProfile(sessionId, body);
      mount(status, notice(s.profileSaved(r.hypotheses_created, r.confidence_cap), "ok"),
        r.rejected_fields.length ? notice(s.profileRejected(r.rejected_fields.join(", ")), "warn") : "");
      onSaved?.();
    } catch (err) {
      mount(status, errorNotice(err));
    }
  });
  return form;
}

export function cueResultView(sessionId, result) {
  const s = ko.session;
  if (!result) return empty(s.noCueYet);
  const d = result.decision;
  return h("div", { class: `cue-result ${result.delivered ? "delivered" : "held"}` },
    result.mock_mode ? notice(ko.common.simulatedNotice, "mock") : null,
    result.evaluation_only ? notice(s.autoCandidateNote, "info") : null,
    h("div", { class: "cue-big" }, result.cue ? `“${result.cue}”` : ko.cues.noFinalCue),
    h("p", {}, finalStatusBadge(result.status), " ", explainStatus(result.status)),
    kv([
      [ko.cues.selectedAction, label(ko.actions, d.action)],
      [ko.cues.reason, explainDecision(d)],
      [ko.cues.score, d.scores.total.toFixed(2)],
    ]),
    h("a", { href: `#/cues/${encodeURIComponent(sessionId)}/${encodeURIComponent(d.id)}` }, s.viewCue),
    devJson(result));
}

export function turnList(sessionId, turns) {
  const s = ko.session;
  if (!turns.length) return empty(s.noTurns);
  return h("ol", { class: "turns" }, turns.map((t) =>
    h("li", { class: `turn turn-${t.speaker}` },
      h("span", { class: "turn-speaker" }, `${t.turn_index}. ${t.speaker === "listener" ? s.listenerTurn : s.presenterTurn}`),
      t.analysis_status === "failed" ? badge(s.analysisFailed, "bad") : null,
      h("p", {}, t.text),
      h("a", { href: `#/trace/${encodeURIComponent(sessionId)}/${encodeURIComponent(t.id)}` }, s.viewTrace))));
}

function conversationForm(sessionId, onDone) {
  const s = ko.session;
  let speaker = "listener";
  const presenterBtn = h("button", { type: "button", class: "seg", "aria-pressed": "false" }, s.presenterTurn);
  const listenerBtn = h("button", { type: "button", class: "seg active", "aria-pressed": "true" }, s.listenerTurn);
  const setSpeaker = (value) => {
    speaker = value;
    presenterBtn.classList.toggle("active", value === "presenter");
    listenerBtn.classList.toggle("active", value === "listener");
    presenterBtn.setAttribute("aria-pressed", String(value === "presenter"));
    listenerBtn.setAttribute("aria-pressed", String(value === "listener"));
  };
  presenterBtn.addEventListener("click", () => setSpeaker("presenter"));
  listenerBtn.addEventListener("click", () => setSpeaker("listener"));
  const text = h("textarea", { rows: 3, maxlength: MAX_TURN_CHARS, "aria-label": s.turnText });
  const counter = h("small", { class: "muted" }, s.chars(0, MAX_TURN_CHARS));
  text.addEventListener("input", () => { counter.textContent = s.chars(text.value.length, MAX_TURN_CHARS); });
  const submit = h("button", { type: "submit", class: "btn" }, s.submitTurn);
  const status = h("div", { "aria-live": "polite" });
  const form = h("form", { class: "form card" },
    h("h3", {}, s.conversation),
    h("div", { class: "segmented", role: "group" }, presenterBtn, listenerBtn),
    text, counter, submit, status);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!text.value.trim()) return;
    submit.disabled = true;
    mount(status, ko.common.loading);
    try {
      await api.addTurn(sessionId, { speaker, text: text.value });
      text.value = "";
      counter.textContent = s.chars(0, MAX_TURN_CHARS);
      onDone(notice(s.turnAdded, "ok"));
    } catch (err) {
      onDone(errorNotice(err));
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

export async function deleteSessionFlow(sessionId, { onDeleted }) {
  const s = ko.session;
  const ok = await confirmDialog({
    title: s.deleteTitle, body: s.deleteBody, confirmLabel: s.deleteConfirm, typeWord: s.deleteWord, danger: true,
  });
  if (!ok) return false;
  await api.deleteSession(sessionId);
  onDeleted?.();
  return true;
}

function dangerZone(sessionId, ctx, flash) {
  const s = ko.session;
  const reset = h("button", { type: "button", class: "btn btn-secondary" }, s.reset);
  const exportBtn = h("button", { type: "button", class: "btn btn-secondary" }, s.export);
  const del = h("button", { type: "button", class: "btn btn-danger", id: "delete-session" }, s.delete);
  reset.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: s.reset, body: s.resetConfirm, confirmLabel: s.reset });
    if (!ok) return;
    try {
      await api.resetSession(sessionId);
      ctx.rerender();
    } catch (err) {
      mount(flash, errorNotice(err));
    }
  });
  exportBtn.addEventListener("click", async () => {
    try {
      const data = await api.exportSession(sessionId);
      // Saved directly to this computer via a local Blob URL; nothing leaves the machine.
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: `papercue-session-${shortId(sessionId)}.json` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      mount(flash, errorNotice(err));
    }
  });
  del.addEventListener("click", async () => {
    try {
      await deleteSessionFlow(sessionId, {
        onDeleted: () => {
          ctx.setSession(null);
          ctx.navigate("session");
        },
      });
    } catch (err) {
      mount(flash, errorNotice(err));
    }
  });
  return h("section", { class: "card card-danger" },
    h("h3", {}, s.danger),
    h("p", { class: "muted" }, s.exportNote),
    h("div", { class: "row" }, reset, exportBtn, del));
}

export async function renderSession(root, params, ctx) {
  const s = ko.session;
  mount(root, loading());
  let papers = [];
  try {
    papers = await api.papers();
  } catch (err) {
    return mount(root, sectionTitle(s.title), errorNotice(err));
  }
  const sessions = await ctx.refreshSessions();
  const sessionId = params[0] ?? null;
  const createForm = createSessionForm(papers, (id) => {
    ctx.setSession(id);
    ctx.navigate("session", id);
  });
  const list = sessions.length
    ? h("ul", { class: "list" }, sessions.map(({ session: x, record_counts: c }) =>
        h("li", { class: x.id === sessionId ? "active" : "" },
          h("a", { href: `#/session/${encodeURIComponent(x.id)}` }, x.label || shortId(x.id)), " ",
          modeBadge(x.mock_mode), " ",
          h("small", { class: "muted" }, `${s.turnsCount(c.conversation_turns)} · ${fmtTime(x.created_at)}`))))
    : empty(s.empty);
  const left = h("aside", {}, createForm, h("h3", {}, s.list), list);
  if (!sessionId) {
    return mount(root, sectionTitle(s.title), h("p", { class: "intro" }, s.intro), h("div", { class: "split" }, left, h("div", {}, empty(ko.common.noSessionSelected))));
  }

  let info;
  try {
    info = await api.session(sessionId);
  } catch (err) {
    ctx.setSession(null);
    return mount(root, sectionTitle(s.title),
      err.status === 404 ? notice(ko.common.sessionNotFound, "error") : errorNotice(err),
      h("div", { class: "split" }, left, h("div", {})));
  }
  ctx.setSession(sessionId);
  const sess = info.session;
  const flash = h("div", { "aria-live": "polite" });
  const turnsBox = h("div", {});
  const cueBox = h("div", { id: "cue-result" }, empty(s.noCueYet));
  const profile = (await api.profile(sessionId).catch(() => null))?.profile;

  async function refreshTurns() {
    try {
      mount(turnsBox, turnList(sessionId, await api.turns(sessionId)));
    } catch (err) {
      mount(turnsBox, errorNotice(err));
    }
  }
  async function runCue(kind) {
    mount(cueBox, loading());
    try {
      const result = kind === "auto" ? await api.autoCandidate(sessionId) : await api.requestCue(sessionId);
      mount(cueBox, cueResultView(sessionId, result));
    } catch (err) {
      mount(cueBox, errorNotice(err));
    }
  }
  const cueBtn = h("button", { type: "button", class: "btn btn-primary", id: "request-cue" }, s.requestCue);
  const autoBtn = h("button", { type: "button", class: "btn btn-secondary" }, s.autoCandidate);
  cueBtn.addEventListener("click", () => runCue("cue"));
  autoBtn.addEventListener("click", () => runCue("auto"));

  const infoCard = h("section", { class: "card" },
    h("h3", {}, s.info, " ", modeBadge(sess.mock_mode, sess.llm_model)),
    sess.mock_mode ? notice(ko.common.simulatedNotice, "mock") : null,
    notice(s.consentNotice, "consent"),
    h("small", { class: "muted" }, `저장된 동의 문구(영문): ${sess.consent_notice}`),
    kv([
      ["ID", h("code", {}, sess.id)],
      [s.cueLanguage, sess.cue_language === "ko" ? s.langKo : s.langEn],
      [s.expiresAt, sess.is_expired ? badge(s.expired, "bad") : fmtTime(sess.expires_at)],
      ["저장된 기록 수", Object.entries(info.record_counts).filter(([, n]) => n).map(([k, n]) => `${k}: ${n}`).join(", ") || "0"],
    ]));

  mount(root,
    sectionTitle(s.title),
    h("p", { class: "intro" }, s.intro),
    flash,
    h("div", { class: "split" },
      left,
      h("div", {},
        infoCard,
        profileForm(sessionId, profile, () => {}),
        conversationForm(sessionId, (msg) => { mount(flash, msg); refreshTurns(); }),
        h("section", { class: "card" }, h("h3", {}, s.cueResult), h("div", { class: "row" }, cueBtn, autoBtn), cueBox),
        h("section", { class: "card" }, h("h3", {}, s.turns), turnsBox),
        dangerZone(sessionId, ctx, flash))));
  await refreshTurns();
}
