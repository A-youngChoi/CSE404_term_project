import { api } from "../api.js";
import { h, mount, fmtNum, fmtTime, shortId } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import {
  badge, confidenceBar, confidenceChart, devJson, empty, errorNotice, lifecycleBadge, loading, notice, sectionTitle,
} from "../components.js";
import { dimLabel, explainBeliefChange, explainEvidence, keyLabel, reasonText, valueLabel } from "../explain.js";

const DIM_ORDER = [
  "knowledge", "familiarity", "interest", "goal", "connection", "concern", "explanation_level", "engagement", "unresolved_issue",
];

function evidenceLine(ev, turnIndexById) {
  if (!ev) return null;
  const where = ev.source_type === "profile"
    ? `${ko.common.profileField} ${ev.source_id.replace(/^profile:/, "")}`
    : ko.common.turnNo(turnIndexById[ev.turn_id] ?? "?");
  return h("li", {},
    badge(label(ko.evidenceTypes, ev.evidence_type), ev.source_type === "profile" ? "life-profile_hypothesis" : `ev-${ev.evidence_type}`), " ",
    h("span", { class: "muted" }, `${where}: `),
    explainEvidence(ev),
    ev.quote ? h("q", { class: "quote" }, ev.quote) : null,
    h("small", { class: "muted", title: "추출기가 남긴 원문 관찰(영문)" }, ` [${ev.observation}]`));
}

export function beliefCard(b, { history = [], evidenceById = {}, turnIndexById = {} } = {}) {
  const a = ko.audience;
  const own = history.filter((x) => x.belief_id === b.id);
  const startedFromProfile = own[0]?.source_type === "profile" || b.source_type === "profile";
  const replaced = [...own].reverse().find((x) => x.change_type === "overridden" || x.change_type === "reversed");
  const sourceTurn = b.source_type === "profile"
    ? `${ko.common.profileField} ${b.source_id.replace(/^profile:/, "")}`
    : b.source_turn_index ? ko.common.turnNo(b.source_turn_index) : shortId(b.source_id);
  return h("article", { class: `belief life-${b.lifecycle}`, dataset: { beliefId: b.id, lifecycle: b.lifecycle } },
    h("header", {},
      h("strong", {}, keyLabel(b.key)), " ",
      lifecycleBadge(b.lifecycle), " ",
      badge(startedFromProfile ? a.fromProfile : a.fromConversation, startedFromProfile ? "muted" : "neutral")),
    replaced
      ? h("div", { class: "state-change" },
          h("span", { class: "prev" }, badge(a.previous, "muted"), " ", h("s", {}, valueLabel(replaced.old_value)), ` (${fmtNum(replaced.old_confidence)})`),
          " → ",
          h("span", { class: "curr" }, badge(a.current, "ok"), " ", valueLabel(b.value)))
      : null,
    h("dl", { class: "kv" },
      h("dt", {}, a.currentValue), h("dd", {}, valueLabel(b.value)),
      h("dt", {}, a.currentConfidence), h("dd", {}, confidenceBar(b.effective_confidence),
        b.effective_confidence !== b.confidence ? h("small", { class: "muted" }, ` (${a.storedConfidence} ${fmtNum(b.confidence)})`) : null),
      h("dt", {}, a.sourceType), h("dd", {}, label(ko.sourceTypes, b.source_type), " · ", label(ko.evidenceTypes, b.last_evidence_type)),
      h("dt", {}, a.sourceTurn), h("dd", {}, sourceTurn),
      h("dt", {}, a.updatedAt), h("dd", {}, fmtTime(b.updated_at)),
      h("dt", {}, a.reason), h("dd", {}, reasonText(b.reason))),
    b.pending_value ? notice(a.pendingValue(valueLabel(b.pending_value)), "warn") : null,
    h("details", {},
      h("summary", {}, `${a.supportingEvidence} (${b.evidence_ids.length})`),
      h("ul", { class: "evidence-list" }, b.evidence_ids.map((id) => evidenceLine(evidenceById[id], turnIndexById)))));
}

export function historyTable(history) {
  const t = ko.audience.table;
  if (!history.length) return empty(ko.audience.historyEmpty);
  return h("div", { class: "table-wrap" }, h("table", { class: "table", id: "belief-history" },
    h("thead", {}, h("tr", {}, [t.turn, t.belief, t.change, t.value, t.confidence, t.evidenceType, t.reason, t.time].map((x) => h("th", {}, x)))),
    h("tbody", {}, history.map((row) => h("tr", { dataset: { changeType: row.change_type } },
      h("td", {}, row.turn_index ? String(row.turn_index) : row.source_type === "profile" ? "프로필" : "–"),
      h("td", {}, `${dimLabel(row.dimension)}: ${keyLabel(row.key)}`),
      h("td", {}, label(ko.changeTypes, row.change_type)),
      h("td", {}, `${row.old_value ? valueLabel(row.old_value) : "–"} → ${valueLabel(row.new_value)}`),
      h("td", { class: "num" }, h("span", { class: "old-conf" }, row.old_confidence === null ? "–" : fmtNum(row.old_confidence)),
        " → ", h("span", { class: "new-conf" }, fmtNum(row.new_confidence))),
      h("td", {}, label(ko.evidenceTypes, row.evidence_type ?? row.source_type)),
      h("td", {}, explainBeliefChange(row, row.turn_index)),
      h("td", {}, fmtTime(row.created_at))))),
  ));
}

export function chartSeries(history, beliefs) {
  const byBelief = new Map();
  let lastTurn = 0;
  for (const row of history) {
    const turn = row.turn_index ?? (row.source_type === "profile" ? lastTurn : lastTurn);
    lastTurn = Math.max(lastTurn, row.turn_index ?? 0);
    if (!byBelief.has(row.belief_id)) byBelief.set(row.belief_id, []);
    byBelief.get(row.belief_id).push({ turn, value: row.new_confidence });
  }
  return beliefs
    .filter((b) => byBelief.has(b.id))
    .slice(0, 7)
    .map((b) => ({
      name: `${dimLabel(b.dimension)}: ${keyLabel(b.key)}`,
      dashed: b.lifecycle === "profile_hypothesis" || b.lifecycle === "stale",
      points: byBelief.get(b.id),
    }));
}

export function audienceView(view, history, evidence, turns) {
  const a = ko.audience;
  const evidenceById = Object.fromEntries(evidence.map((e) => [e.id, e]));
  const turnIndexById = Object.fromEntries(turns.map((t) => [t.id, t.turn_index]));
  const all = DIM_ORDER.flatMap((d) => view.dimensions[d] ?? []);
  const legend = h("section", { class: "card legend-card" }, h("h3", {}, a.legend),
    h("ul", {}, Object.entries(ko.lifecycleHelp).map(([k, text]) => h("li", {}, lifecycleBadge(k), " ", text))));
  const uncertainty = h("section", { class: "card dim-card", dataset: { dimension: "uncertainty" } },
    h("h3", {}, ko.dimensions.uncertainty),
    h("p", {}, a.overallUncertainty, ": ", confidenceBar(view.overall_uncertainty)),
    h("p", { class: "muted" }, a.listenerTurns(view.listener_turns_observed)),
    h("p", {}, a.unknownDims, ": ", view.unknown_dimensions.length ? view.unknown_dimensions.map(dimLabel).join(", ") : ko.common.none));
  const cards = DIM_ORDER.map((d) => h("section", { class: "card dim-card", dataset: { dimension: d } },
    h("h3", {}, dimLabel(d)),
    (view.dimensions[d] ?? []).length
      ? (view.dimensions[d]).map((b) => beliefCard(b, { history, evidenceById, turnIndexById }))
      : empty(a.noBeliefs)));
  const series = chartSeries(history, all);
  return h("div", {},
    sectionTitle(a.title),
    h("p", { class: "intro" }, a.intro),
    legend,
    h("div", { class: "grid dims" }, uncertainty, ...cards),
    sectionTitle(a.history),
    series.length ? h("section", { class: "card" }, h("h3", {}, a.chartTitle), confidenceChart(series), h("p", { class: "muted" }, a.chartNote)) : null,
    historyTable(history),
    devJson({ view, history }));
}

export async function renderAudience(root, params, ctx) {
  const sid = params[0] ?? ctx.sessionId;
  if (!sid) return mount(root, sectionTitle(ko.audience.title), empty(ko.common.noSessionSelected));
  mount(root, loading());
  try {
    const [view, history, evidence, turns] = await Promise.all([
      api.audience(sid), api.history(sid), api.evidence(sid), api.turns(sid),
    ]);
    mount(root, audienceView(view, history, evidence, turns));
  } catch (err) {
    mount(root, sectionTitle(ko.audience.title),
      err.status === 404 ? notice(ko.common.sessionNotFound, "error") : errorNotice(err));
  }
}
