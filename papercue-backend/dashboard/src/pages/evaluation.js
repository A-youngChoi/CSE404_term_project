import { pres } from "../api.js";
import { h, mount, fmtNum } from "../dom.js";
import { label } from "../i18n/ko.js";
import { badge, devJson, empty, errorNotice, kv, loading, notice, sectionTitle } from "../components.js";
import { P, decisionBadge, decisionLabel, issueLabel, mmss, pct, promptTypeLabel, section, simpleTable } from "../lab/common.js";

const E = P.evaluation;
const HEADLINE = ["precision", "recall", "f1", "unnecessary_interruption_rate", "missed_critical_rate", "mean_delay_s",
  "recovery_rate", "prompt_type_accuracy", "decision_agreement", "event_accuracy", "mean_confidence"];
const RATE_METRICS = new Set(["precision", "recall", "f1", "unnecessary_interruption_rate", "missed_critical_rate",
  "recovery_rate", "prompt_type_accuracy", "decision_agreement", "event_accuracy"]);

function metricValue(key, v) {
  if (v === null || v === undefined) return "해당 없음";
  if (RATE_METRICS.has(key)) return pct(v);
  return fmtNum(v, key === "mean_delay_s" ? 1 : 2);
}

export function metricTiles(metrics, counts) {
  return h("div", { class: "tiles" },
    HEADLINE.map((k) => h("div", { class: "tile", dataset: { metric: k } },
      h("div", { class: "tile-label" }, label(P.metrics, k)),
      h("div", { class: "tile-value" }, metricValue(k, metrics[k])))),
    counts ? h("div", { class: "tile" },
      h("div", { class: "tile-label" }, "TP / FP / FN / TN"),
      h("div", { class: "tile-value" }, `${counts.tp} / ${counts.fp} / ${counts.fn} / ${counts.tn}`)) : null);
}

function sliceTable(slices, keyLabel, keyFn) {
  return simpleTable([keyLabel, "세션", "TP", "FP", "FN", "정밀도", "재현율", "F1", "회복률", "판단 일치율", "평균 신뢰도"],
    Object.entries(slices).map(([k, v]) => h("tr", {},
      h("td", {}, keyFn(k)), h("td", { class: "num" }, String(v.sessions)),
      h("td", { class: "num" }, String(v.counts.tp)), h("td", { class: "num" }, String(v.counts.fp)), h("td", { class: "num" }, String(v.counts.fn)),
      h("td", { class: "num" }, metricValue("precision", v.metrics.precision)), h("td", { class: "num" }, metricValue("recall", v.metrics.recall)),
      h("td", { class: "num" }, metricValue("f1", v.metrics.f1)), h("td", { class: "num" }, metricValue("recovery_rate", v.metrics.recovery_rate)),
      h("td", { class: "num" }, metricValue("decision_agreement", v.metrics.decision_agreement)),
      h("td", { class: "num" }, fmtNum(v.metrics.mean_confidence)))));
}

export function rowsTable(rows, onSelect) {
  return simpleTable(["이벤트", "시각", "정답 문제", "정답 판단", "정답 프롬프트", "시스템 판단", "감지 문제", "시스템 프롬프트", "결과", "비교", "차이 설명"],
    rows.map((r) => h("tr", { class: `match-${r.match}`, dataset: { eventId: r.event_id } },
      h("td", {}, onSelect ? h("button", { type: "button", class: "btn-link", onclick: () => onSelect(r.step) }, r.event_id) : r.event_id),
      h("td", {}, mmss(r.elapsed)),
      h("td", {}, issueLabel(r.gt_issue), r.gt_severity !== "none" ? h("small", { class: "muted" }, ` (${label(P.severity, r.gt_severity)})`) : null),
      h("td", {}, r.gt_decision ? decisionLabel(r.gt_decision) : "–"),
      h("td", {}, r.gt_prompt_type ? promptTypeLabel(r.gt_prompt_type) : "–"),
      h("td", {}, decisionBadge(r.decision)),
      h("td", {}, issueLabel(r.detected_issue)),
      h("td", {}, r.prompt_type ? promptTypeLabel(r.prompt_type) : "–"),
      h("td", {}, r.prompt_outcome ? label(P.outcomes, r.prompt_outcome) : "–"),
      h("td", {}, badge(label(P.matches, r.match), r.match === "FP" || r.match === "FN" ? "bad" : r.match === "TN" ? "muted" : "ok"),
        r.decision_agrees === false ? badge("판단 불일치", "warn") : null),
      h("td", { class: "small" }, (r.mismatch_notes ?? []).join(" ")))));
}

export function evaluationView(res, { onRecompute, params = {} } = {}) {
  const o = res.overall;
  const seed = h("input", { type: "number", id: "eval-seed", value: String(params.seed ?? res.config.seed), "aria-label": P.lab.seed });
  const judge = h("select", { id: "eval-judge", "aria-label": P.lab.judge },
    ["mock", "ollama"].map((v) => h("option", { value: v, selected: v === (params.judge_provider ?? res.config.judge_provider) }, v === "mock" ? "규칙 기반(Mock)" : "Ollama(로컬 LLM)")));
  const retriever = h("select", { id: "eval-retriever", "aria-label": P.lab.retriever },
    ["keyword", "embedding"].map((v) => h("option", { value: v, selected: v === (params.retriever ?? res.config.retriever) }, v === "keyword" ? "키워드" : "임베딩")));
  const btn = h("button", { type: "button", class: "btn", onclick: () => onRecompute?.({ seed: seed.value, judge_provider: judge.value, retriever: retriever.value }) }, E.recompute);
  const outcomes = o.counts;
  return h("div", { class: "evaluation" },
    sectionTitle(E.title),
    h("p", { class: "intro" }, E.intro),
    notice(P.mockNotice, "mock"),
    notice(E.optimistic, "warn"),
    h("div", { class: "row filters" }, h("label", { class: "inline" }, `${P.lab.seed} `, seed),
      h("label", { class: "inline" }, `${P.lab.judge} `, judge), h("label", { class: "inline" }, `${P.lab.retriever} `, retriever), btn,
      badge(`Judge: ${res.judge ?? "–"}`, "neutral")),
    section(E.overall, metricTiles(o.metrics, o.counts),
      kv([
        ["이벤트 · 정답 양성 · 전달된 프롬프트", `${outcomes.events} · ${outcomes.positives} · ${outcomes.delivered}`],
        ["억제 · 대기 판단", `${outcomes.suppressed} · ${outcomes.waits}`],
        [E.outcomes, `회복 ${outcomes.recovered} · 부분 회복 ${outcomes.partially_recovered} · 회복 안 됨 ${outcomes.not_recovered} · 관찰 중 ${outcomes.pending}`],
        ["평균 판단 지연", `${metricValue("mean_delay_s", o.metrics.mean_delay_s)}초 · ${metricValue("mean_delay_events", o.metrics.mean_delay_events)}이벤트`],
      ])),
    h("div", { class: "grid wide" },
      section(E.byLanguage, sliceTable(res.by_language, "언어", (k) => label(P.languages, k))),
      section(E.byType, simpleTable(["프롬프트 유형", "정답 수", "시스템 전달 수", "유형까지 맞은 수", "정밀도", "재현율", "F1"],
        Object.entries(res.by_prompt_type).map(([k, v]) => h("tr", {},
          h("td", {}, promptTypeLabel(k)), h("td", { class: "num" }, String(v.expected)), h("td", { class: "num" }, String(v.predicted)),
          h("td", { class: "num" }, String(v.correct)), h("td", { class: "num" }, metricValue("precision", v.precision)),
          h("td", { class: "num" }, metricValue("recall", v.recall)), h("td", { class: "num" }, metricValue("f1", v.f1))))))),
    section(E.byScenario, sliceTable(res.by_scenario, "시나리오", (k) => label(P.scenarios, k))),
    section(E.sessions, res.sessions.map((sess) => h("details", { class: "session-eval", dataset: { session: sess.session_id } },
      h("summary", {}, h("strong", {}, sess.title_ko), ` (${sess.session_id}) `,
        badge(label(P.languages, sess.language), "type"), " ",
        badge(`TP ${sess.counts.tp} · FP ${sess.counts.fp} · FN ${sess.counts.fn}`, sess.counts.fp || sess.counts.fn ? "bad" : "ok"), " ",
        h("a", { href: `#/lab/${encodeURIComponent(sess.session_id)}` }, "실험실에서 열기")),
      metricTiles(sess.metrics),
      h("h4", {}, E.rows),
      rowsTable(sess.rows)))),
    section(E.methodTitle, simpleTable(["지표", "계산 방식"], Object.entries(res.method).map(([k, v]) => h("tr", {},
      h("td", {}, h("code", {}, k)), h("td", {}, P.method[k] ?? String(v)))))),
    devJson({ config: res.config, overall: res.overall }),
  );
}

export async function renderEvaluation(root) {
  const load = async (params = {}) => {
    mount(root, loading());
    try {
      const res = await pres.evaluation(params);
      mount(root, evaluationView(res, { onRecompute: load, params }));
    } catch (err) {
      mount(root, sectionTitle(E.title), errorNotice(err));
    }
  };
  await load();
}

export function runEvaluationPanel(lab) {
  const ev = lab.view.evaluation;
  if (!ev) return empty(P.lab.notStarted);
  return h("div", {},
    notice(P.gtNotice, "info"),
    !ev.complete ? notice("세션이 아직 끝나지 않아 지금까지 재생한 이벤트만 평가했습니다.", "warn") : null,
    metricTiles(ev.metrics, ev.counts),
    rowsTable(ev.rows, lab.selectStep));
}
