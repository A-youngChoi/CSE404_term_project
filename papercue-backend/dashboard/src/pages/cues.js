import { api } from "../api.js";
import { h, mount, fmtNum, fmtTime } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import {
  badge, confidenceBar, devJson, empty, errorNotice, finalStatusBadge, kv, lifecycleBadge, loading, notice, sectionTitle,
} from "../components.js";
import { dimLabel, explainDecision, explainStatus, keyLabel, valueLabel } from "../explain.js";

const WEIGHTS = { need: 0.3, relevance: 0.2, grounding: 0.15, model_confidence: 0.15, novelty: 0.1, timing: 0.1, interruption_cost: -0.2 };
const CHECK_GROUPS = {
  repetition: ["not_repeated"],
  sensitive: ["no_sensitive_inference", "audience_claims_supported", "no_manipulation"],
  unsupported: ["grounding_ids_valid", "no_unsupported_claims", "no_forbidden_claims"],
};

function countWords(text) {
  return (String(text ?? "").match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)?/gu) ?? []).length;
}

function groupResult(checks, names) {
  const relevant = checks.filter((c) => names.includes(c.name));
  if (!relevant.length) return badge("검사 안 함", "muted");
  const failed = relevant.filter((c) => !c.passed);
  return failed.length
    ? h("span", {}, badge(ko.cues.failed, "bad"), " ", failed.map((c) => label(ko.validationCodes, c.name)).join(", "))
    : badge(ko.cues.passed, "ok");
}

export function cueDetailView(d, { unitsById = {}, evidenceById = {} } = {}) {
  const c = ko.cues;
  const g = d.generated;
  const trace = d.trace ?? {};
  const state = trace.conversation_state ?? {};
  const checks = g?.filter_results ?? [];
  const status = d.final_status;
  const decisionForExplain = { reason_code: d.reason_code, reason_params: trace.reason_params ?? { target: d.target }, short_reason: d.short_reason };
  const noCue = !g?.cue;
  return h("div", { class: "cue-detail", dataset: { status } },
    g?.generator?.startsWith("mock") ? notice(ko.common.simulatedNotice, "mock") : null,
    h("section", { class: "card final" },
      h("h3", {}, c.finalStatus, " ", finalStatusBadge(status),
        d.mode === "auto_candidate" ? badge(c.evaluationOnly, "muted") : g?.delivered ? badge(c.delivered, "ok") : null,
        g?.fallback_used ? badge(c.fallback, "warn") : null),
      h("div", { class: "cue-big" }, g?.cue ? `“${g.cue}”` : c.noFinalCue),
      h("p", { class: noCue ? "no-cue-reason" : "" }, explainStatus(status))),
    h("section", { class: "card" },
      h("h3", {}, c.triggeringTurn),
      h("p", {}, d.trigger_turn_index ? ko.common.turnNo(d.trigger_turn_index) : "–", " ", h("q", { class: "quote" }, d.trigger_turn_text ?? "")),
      kv([
        [c.phase, label(ko.phases, state.phase)],
        [c.unresolved, (state.unresolved_issues ?? []).map((i) => `${keyLabel(i.topic)}(${valueLabel(i.status)})`).join(", ") || ko.common.none],
        ["요청 시각", fmtTime(d.created_at)],
      ])),
    h("section", { class: "card" },
      h("h3", {}, c.activeBeliefs),
      (trace.beliefs_used ?? []).length
        ? h("ul", {}, trace.beliefs_used.map((b) => h("li", {},
            lifecycleBadge(b.lifecycle), " ", `${dimLabel(b.dimension)}: ${keyLabel(b.key)} = ${valueLabel(b.value)} `, confidenceBar(b.effective_confidence))))
        : empty("특정 믿음을 사용하지 않았습니다(기본 확인 질문).")),
    h("section", { class: "card" },
      h("h3", {}, c.retrieved),
      (trace.retrieved_units ?? []).length
        ? h("table", { class: "table" },
            h("thead", {}, h("tr", {}, ["논문 단위", "유형", c.score, c.similarity].map((x) => h("th", {}, x)))),
            h("tbody", {}, trace.retrieved_units.map((u) => h("tr", {},
              h("td", {}, u.title), h("td", {}, label(ko.unitTypes, u.unit_type)),
              h("td", { class: "num" }, fmtNum(u.score, 3)), h("td", { class: "num" }, fmtNum(u.similarity, 3))))))
        : empty(ko.rationale.no_units_found),
      (trace.earlier_context ?? []).length
        ? h("p", {}, `${c.earlier}: `, trace.earlier_context.map((m) => badge(`${m.turn_index}번 발화 (${fmtNum(m.score)})`)))
        : null),
    h("section", { class: "card" },
      h("h3", {}, c.scores),
      h("table", { class: "table", id: "score-table" },
        h("thead", {}, h("tr", {}, ["항목", "값", c.weight, "기여"].map((x) => h("th", {}, x)))),
        h("tbody", {}, Object.entries(d.scores ?? {}).map(([k, v]) => h("tr", {},
          h("td", {}, label(ko.scoreNames, k)),
          h("td", { class: "num" }, fmtNum(v)),
          h("td", { class: "num" }, k in WEIGHTS ? fmtNum(WEIGHTS[k]) : "–"),
          h("td", { class: "num" }, k in WEIGHTS ? fmtNum(WEIGHTS[k] * v) : fmtNum(v))))))),
    h("section", { class: "card" },
      h("h3", {}, c.selectedAction),
      kv([
        [c.selectedAction, label(ko.actions, d.action)],
        [c.target, keyLabel(d.target)],
        [c.reason, explainDecision(decisionForExplain)],
        ["개입 여부", d.should_intervene ? "개입" : "개입하지 않음"],
      ])),
    g ? h("section", { class: "card" },
      h("h3", {}, c.initialCandidate),
      kv([
        [c.initialCandidate, g.candidate_cue ? `“${g.candidate_cue}”` : "형식 검증 실패로 후보 없음"],
        [c.finalCue, g.cue ? `“${g.cue}”` : c.noFinalCue],
        [c.wordCount, `${countWords(g.cue ?? g.candidate_cue)}단어 (최대 12)`],
        [c.groundingIds, g.grounding_unit_ids.length ? h("ul", {}, g.grounding_unit_ids.map((id) => h("li", {}, unitsById[id]?.title ?? "알 수 없는 단위", " ", h("code", {}, id.slice(0, 8))))) : ko.common.none],
        [c.evidenceIds, g.audience_evidence_ids.length ? h("ul", {}, g.audience_evidence_ids.map((id) => {
          const e = evidenceById[id];
          return h("li", {}, e ? `${dimLabel(e.dimension)}: ${keyLabel(e.key)} (${label(ko.evidenceTypes, e.evidence_type)})` : "알 수 없는 근거", " ", h("code", {}, id.slice(0, 8)));
        })) : ko.common.none],
        [c.confidence, confidenceBar(g.confidence)],
        [c.rationale, g.rationale ?? "–"],
        [c.generator, h("code", {}, g.generator)],
      ])) : null,
    g ? h("section", { class: "card" },
      h("h3", {}, c.checks),
      kv([
        [c.repetition, groupResult(checks, CHECK_GROUPS.repetition)],
        [c.sensitive, groupResult(checks, CHECK_GROUPS.sensitive)],
        [c.unsupported, groupResult(checks, CHECK_GROUPS.unsupported)],
      ]),
      h("table", { class: "table", id: "check-table" },
        h("thead", {}, h("tr", {}, ["검사", "결과", "세부"].map((x) => h("th", {}, x)))),
        h("tbody", {}, checks.map((ch) => h("tr", { class: ch.passed ? "" : "row-fail" },
          h("td", {}, label(ko.validationCodes, ch.name)),
          h("td", {}, ch.passed ? badge(c.passed, "ok") : badge(c.failed, "bad")),
          h("td", {}, ch.detail || "–")))))) : null,
    h("section", { class: "card" },
      h("h3", {}, c.latency),
      kv(Object.entries(d.latency_ms ?? {}).map(([k, v]) => [ko.stages[k] ?? (k === "total" ? "합계" : k), `${fmtNum(v, 2)} ms`]))),
    devJson(d));
}

export async function renderCues(root, params, ctx) {
  const sid = params[0] ?? ctx.sessionId;
  const c = ko.cues;
  if (!sid) return mount(root, sectionTitle(c.title), empty(ko.common.noSessionSelected));
  mount(root, loading());
  let decisions;
  let info;
  try {
    [decisions, info] = await Promise.all([api.cues(sid), api.session(sid)]);
  } catch (err) {
    return mount(root, sectionTitle(c.title),
      err.status === 404 ? notice(ko.common.sessionNotFound, "error") : errorNotice(err));
  }
  const [paper, evidence] = await Promise.all([
    api.paper(info.session.paper_id).catch(() => ({ units: [] })),
    api.evidence(sid).catch(() => []),
  ]);
  const unitsById = Object.fromEntries(paper.units.map((u) => [u.id, u]));
  const evidenceById = Object.fromEntries(evidence.map((e) => [e.id, e]));
  const selectedId = params[1] ?? decisions[decisions.length - 1]?.id;
  const list = decisions.length
    ? h("ol", { class: "list" }, decisions.map((d, i) => h("li", { class: d.id === selectedId ? "active" : "" },
        h("a", { href: `#/cues/${encodeURIComponent(sid)}/${encodeURIComponent(d.id)}` },
          `#${i + 1} ${d.mode === "auto_candidate" ? "(평가) " : ""}${d.generated?.cue ?? c.noFinalCue}`),
        " ", finalStatusBadge(d.final_status))))
    : empty(c.empty);
  const selected = decisions.find((d) => d.id === selectedId);
  mount(root, sectionTitle(c.title), h("p", { class: "intro" }, c.intro),
    h("div", { class: "split" },
      h("aside", {}, h("h3", {}, c.list), list),
      h("div", { class: "detail" }, selected ? cueDetailView(selected, { unitsById, evidenceById }) : empty(c.selectOne))));
}
