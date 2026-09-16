import { api } from "../api.js";
import { h, mount, fmtNum } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import { badge, devJson, empty, errorNotice, kv, loading, notice, sectionTitle, statusBadge } from "../components.js";
import { dimLabel, explainBeliefChange, keyLabel, stageExplanation, valueLabel } from "../explain.js";

export const STAGE_ORDER = [
  "input_turn", "recent_context", "dialogue_act_analysis", "evidence_extraction", "audience_update_proposal",
  "audience_update_applied", "retrieval_query", "retrieval", "intervention_decision", "cue_candidate",
  "safety_grounding_check", "final_result",
];
const TURN_STAGES = new Set(STAGE_ORDER.slice(0, 6));

export function runLabel(trace, index) {
  if (trace.kind === "turn") return ko.trace.turnRun;
  return trace.kind === "cue" ? ko.trace.cueRun(index) : ko.trace.autoRun(index);
}

/** Stages 1-6 come from the turn trace; stages 7-12 from the selected cue run (or the turn's shadow evaluation). */
export function mergeStages(view, runId) {
  const turnTrace = view.traces.find((t) => t.kind === "turn");
  const runs = view.traces.filter((t) => t.kind !== "turn");
  const run = runs.find((t) => t.trace_id === runId) ?? runs[runs.length - 1] ?? null;
  return STAGE_ORDER.map((name, i) => {
    const source = TURN_STAGES.has(name) || !run ? turnTrace : run;
    const stage = source?.stages.find((s) => s.stage_name === name);
    return {
      number: i + 1,
      name,
      stage: stage ?? null,
      source,
      sourceLabel: source ? runLabel(source, runs.indexOf(source) + 1) : null,
    };
  });
}

function table(headers, rows) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}

function stateTable(before, after) {
  const row = (name, fn) => [name, fn(before), fn(after)];
  const issues = (st) => (st?.unresolved_issues ?? []).map((i) => `${keyLabel(i.topic)}(${valueLabel(i.status)})`).join(", ") || "–";
  return table(["항목", ko.trace.stateBefore, ko.trace.stateAfter], [
    row("대화 단계", (st) => label(ko.phases, st?.phase)),
    row("현재 주제", (st) => keyLabel(st?.topic)),
    row("청중 행위", (st) => label(ko.listenerActs, st?.listener_act)),
    row("발표자 행위", (st) => label(ko.presenterActs, st?.presenter_act)),
    row("해결되지 않은 질문", issues),
    row("감지된 우려", (st) => (st?.detected_concerns ?? []).map(keyLabel).join(", ") || "–"),
    row("오해 가능성", (st) => keyLabel(st?.possible_misunderstanding)),
  ]);
}

function inputSummary(stage, view) {
  const t = ko.trace;
  const refs = stage.input_reference_ids?.length ?? 0;
  switch (stage.stage_name) {
    case "input_turn":
      return h("q", { class: "quote" }, view.turn.text);
    case "dialogue_act_analysis":
      return "이전 대화 상태, 최근 발화 창, 이전 대화 요약, 후보 주제(논문 단위 제목), 최신 발화";
    case "evidence_extraction":
      return "최신 청중 발화와 현재 대화 상태, 논문 용어 목록";
    case "audience_update_proposal":
      return `채택된 근거 ${refs}개와 현재 믿음 목록`;
    case "intervention_decision":
      return `현재 대화 상태, 청중 믿음 ${refs}개, 검색된 논문 단위`;
    default:
      return refs ? t.inputRefs(refs) : "–";
  }
}

function outputView(stage, view) {
  const o = stage.output ?? {};
  const unit = (id) => view.units?.[id]?.title ?? id.slice(0, 8);
  switch (stage.stage_name) {
    case "input_turn":
      return kv([["화자", o.speaker === "listener" ? ko.session.listenerTurn : o.speaker === "presenter" ? ko.session.presenterTurn : "–"],
        ["발화 번호", String(o.turn_index ?? o.trigger_turn_index ?? "–")], ["단어 수", String(o.word_count ?? "–")]]);
    case "recent_context":
      return (view.recent_turns ?? []).length
        ? h("ol", { class: "mini-turns" }, view.recent_turns.map((t) =>
            h("li", {}, `${t.turn_index}. ${t.speaker === "listener" ? "청중" : "발표자"}: `, t.text.slice(0, 120))))
        : empty("이전 발화가 없습니다.");
    case "dialogue_act_analysis":
      return stateTable(view.state_before, view.state_after ?? o);
    case "evidence_extraction": {
      const items = view.evidence ?? [];
      return h("div", {},
        items.length ? table(["차원", "키", "값", "근거 유형", "신뢰도", "인용"], items.map((e) => [
          dimLabel(e.dimension), keyLabel(e.key), valueLabel(e.value), label(ko.evidenceTypes, e.evidence_type),
          fmtNum(e.confidence), e.quote ?? "–",
        ])) : empty(ko.rationale.no_evidence),
        (o.rejected ?? []).length
          ? h("p", {}, "버린 제안: ", o.rejected.map((r) => badge(`${dimLabel(r.dimension)} · ${label(ko.validationCodes, r.code)}`, "warn")))
          : null);
    }
    case "audience_update_proposal":
      return (o.proposals ?? []).length
        ? table(["근거", "차원", "키", "값"], o.proposals.map((p) => [p.evidence_id.slice(0, 8), dimLabel(p.dimension), keyLabel(p.key), valueLabel(p.value)]))
        : empty("제안이 없습니다.");
    case "audience_update_applied":
      if (o.beliefs) {
        return table(["차원", "키", "값", "적용 신뢰도", "상태"], o.beliefs.map((b) => [
          dimLabel(b.dimension), keyLabel(b.key), valueLabel(b.value), fmtNum(b.effective_confidence), label(ko.lifecycle, b.lifecycle)]));
      }
      return (o.changes ?? []).length
        ? table(["믿음", "변경 유형", "값", "신뢰도(이전 → 현재)", "설명"], o.changes.map((c) => [
            `${dimLabel(c.dimension)}: ${keyLabel(c.key)}`, label(ko.changeTypes, c.change_type),
            `${c.old_value ? valueLabel(c.old_value) : "–"} → ${valueLabel(c.new_value)}`,
            `${c.old_confidence === null ? "없음" : fmtNum(c.old_confidence)} → ${fmtNum(c.new_confidence)}`,
            explainBeliefChange(c, view.turn.turn_index)]))
        : empty(ko.rationale.no_belief_change);
    case "retrieval_query":
      return kv([
        ["최근 질문 포함", o.has_latest_question ? ko.common.yes : ko.common.no],
        ["현재 주제", keyLabel(o.topic)],
        ["해결되지 않은 질문", (o.unresolved_issues ?? []).map(keyLabel).join(", ") || "–"],
        ["우려 사항", (o.concerns ?? []).map(keyLabel).join(", ") || "–"],
        ["의도한 행동", label(ko.actions, o.action)],
        ["대상", keyLabel(o.target)],
      ]);
    case "retrieval":
      return h("div", {},
        (o.units ?? []).length
          ? table(["논문 단위", "유형", "종합 점수", "코사인 유사도"], o.units.map((u) => [u.title, label(ko.unitTypes, u.unit_type), fmtNum(u.score, 3), fmtNum(u.similarity, 3)]))
          : empty(ko.rationale.no_units_found),
        (o.memory_hits ?? []).length
          ? h("p", {}, `${ko.cues.earlier}: `, o.memory_hits.map((m) => badge(`${m.turn_index}번 발화 (${fmtNum(m.score)})`, "neutral")))
          : null);
    case "intervention_decision":
      return h("div", {},
        kv([["개입 여부", o.should_intervene ? "개입" : "개입하지 않음"], ["선택된 행동", label(ko.actions, o.action)],
          ["대상", keyLabel(o.target)], ["신뢰도", fmtNum(o.confidence)]]),
        o.scores ? table(["점수 항목", "값"], Object.entries(o.scores).map(([k, v]) => [label(ko.scoreNames, k), fmtNum(v)])) : null);
    case "cue_candidate":
      return kv([
        ["후보 단서", o.cue ? `“${o.cue}”` : "–"], ["행동", label(ko.actions, o.action)], ["단어 수", String(o.word_count ?? "–")],
        ["논문 근거", (o.grounding_unit_ids ?? []).map(unit).join(", ") || "–"],
        ["청중 근거", (o.audience_evidence_ids ?? []).map((id) => id.slice(0, 8)).join(", ") || "–"],
        ["신뢰도", fmtNum(o.confidence)], ["짧은 근거 설명", o.rationale ?? "–"],
      ]);
    case "safety_grounding_check":
      return table(["검사", "결과", "세부"], (o.checks ?? []).map((c) => [
        label(ko.validationCodes, c.name), c.passed ? badge(ko.cues.passed, "ok") : badge(ko.cues.failed, "bad"), c.detail || "–"]));
    case "final_result":
      return kv([["최종 상태", label(ko.finalStatus, o.status)], ["최종 단서", o.final_cue ? `“${o.final_cue}”` : ko.cues.noFinalCue],
        ["전달 여부", o.delivered ? ko.cues.delivered : o.evaluation_only ? ko.cues.evaluationOnly : "전달 안 함"]]);
    default:
      return devJson(o);
  }
}

export function stageView(entry, view) {
  const t = ko.trace;
  const { number, name, stage } = entry;
  const title = `${number}. ${ko.stages[name]}`;
  if (!stage) {
    return h("details", { class: "stage stage-missing", dataset: { stage: name } },
      h("summary", {}, title, " ", badge(t.notRun, "muted")),
      h("p", { class: "muted" }, t.notRunDetail));
  }
  const errors = stage.validation_errors ?? [];
  return h("details", { class: `stage stage-${stage.status}`, dataset: { stage: name }, open: stage.status === "failed" },
    h("summary", {},
      h("span", { class: "stage-title" }, title), " ",
      statusBadge(stage.status), " ",
      badge(label(ko.components, stage.component_type), stage.component_type === "mock_rules" ? "mock" : stage.component_type === "local_llm" ? "real" : "neutral"), " ",
      h("span", { class: "muted" }, `${fmtNum(stage.duration_ms, 1)} ${ko.common.ms}`), " ",
      entry.sourceLabel ? h("small", { class: "muted" }, `· ${entry.sourceLabel}`) : null),
    h("div", { class: "stage-body" },
      h("p", { class: "explain" }, stageExplanation(stage, { turnIndex: view.turn.turn_index })),
      kv([
        [t.status, statusBadge(stage.status)],
        [t.component, label(ko.components, stage.component_type)],
        stage.model_name ? [t.model, h("code", {}, stage.model_name)] : null,
        stage.prompt_version ? [t.promptVersion, h("code", {}, stage.prompt_version)] : null,
        [t.elapsed, `${fmtNum(stage.duration_ms, 2)} ${ko.common.ms}`],
        [t.validation, errors.length ? errors.map((e) => badge(label(ko.validationCodes, e), "warn")) : badge(t.validationOk, "ok")],
        [t.inputSummary, inputSummary(stage, view)],
      ]),
      h("h4", {}, t.output),
      outputView(stage, view),
      devJson(stage)));
}

export function traceView(view, runId, onSelectRun) {
  const t = ko.trace;
  const runs = view.traces.filter((x) => x.kind !== "turn");
  const entries = mergeStages(view, runId);
  const selector = runs.length
    ? h("label", { class: "inline" }, `${t.runSelector}: `,
        (() => {
          const sel = h("select", {}, runs.map((r, i) => h("option", { value: r.trace_id, selected: r.trace_id === (runId ?? runs[runs.length - 1].trace_id) }, runLabel(r, i + 1))));
          sel.addEventListener("change", () => onSelectRun(sel.value));
          return sel;
        })())
    : h("p", { class: "muted" }, "이 발화에 대한 단서 요청이 없어, 7~9단계는 발화 입력 시 계산된 평가를 보여 줍니다.");
  const mock = view.traces.some((x) => x.stages.some((s) => s.component_type === "mock_rules"));
  return h("div", { class: "trace" },
    h("h3", {}, `${ko.common.turnNo(view.turn.turn_index)} · ${view.turn.speaker === "listener" ? ko.session.listenerTurn : ko.session.presenterTurn}`),
    h("q", { class: "quote big" }, view.turn.text),
    mock ? notice(ko.common.simulatedNotice, "mock") : null,
    selector,
    h("div", { class: "stages" }, entries.map((e) => stageView(e, view))),
    h("p", { class: "muted" }, `파이프라인 버전: ${view.traces[0]?.pipeline_version ?? "–"}`),
    devJson(view));
}

export async function renderTrace(root, params, ctx) {
  const sid = params[0] ?? ctx.sessionId;
  if (!sid) return mount(root, sectionTitle(ko.trace.title), empty(ko.common.noSessionSelected));
  mount(root, loading());
  let turns;
  try {
    turns = await api.turns(sid);
  } catch (err) {
    return mount(root, sectionTitle(ko.trace.title),
      err.status === 404 ? notice(ko.common.sessionNotFound, "error") : errorNotice(err));
  }
  const turnId = params[1] ?? turns[turns.length - 1]?.id;
  const detail = h("div", { class: "detail" });
  const list = turns.length
    ? h("ol", { class: "list" }, turns.map((t) => h("li", { class: t.id === turnId ? "active" : "" },
        h("a", { href: `#/trace/${encodeURIComponent(sid)}/${encodeURIComponent(t.id)}` },
          `${t.turn_index}. ${t.speaker === "listener" ? "청중" : "발표자"}: ${t.text.slice(0, 40)}${t.text.length > 40 ? "…" : ""}`),
        t.analysis_status === "failed" ? badge(ko.session.analysisFailed, "bad") : null)))
    : empty(ko.session.noTurns);
  mount(root, sectionTitle(ko.trace.title), h("p", { class: "intro" }, ko.trace.intro),
    h("div", { class: "split" }, h("aside", {}, h("h3", {}, ko.trace.turnList), list), detail));
  if (!turnId) return mount(detail, empty(ko.trace.selectTurn));
  try {
    const view = await api.turnTrace(sid, turnId);
    const show = (runId) => mount(detail, traceView(view, runId, show));
    show(null);
  } catch (err) {
    mount(detail, errorNotice(err));
  }
}
