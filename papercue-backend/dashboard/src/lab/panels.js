// Inspector panels of the simulation lab: flow strip, live monitor, Judge, events.
import { h, fmtNum } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import { badge, devJson, empty, kv, notice } from "../components.js";
import {
  P, decisionBadge, decisionLabel, explainJudge, issueLabel, meter, mmss, promptTypeLabel, refChip, refList,
  section, simpleTable, umValue,
} from "./common.js";
import { mobileCard } from "./mobileCard.js";

const J = P.judge;
const F = P.fields;

function snapshotAttr(step, lab, key) {
  const snap = step?.user_model_snapshot ?? lab.view.initial.user_model_snapshot;
  return snap?.[key];
}

// ------------------------------------------------------------------ flow strip
export const FLOW_TABS = {
  event: "events", context: "monitor", memory: "memory", usermodel: "usermodel", rag: "rag", judge: "judge",
  prompt: "prompts", outcome: "prompts",
};

export function flowStrip(lab) {
  const st = lab.step;
  const ops = {};
  for (const c of st?.memory_changes ?? []) ops[c.op] = (ops[c.op] ?? 0) + 1;
  const kr = st?.knowledge_retrieval;
  const items = {
    event: st ? `${label(P.eventTypes, st.event.event_type)} · #${st.event.current_slide} · ${mmss(st.elapsed_time)}` : "–",
    context: st ? `활성 문제 ${st.context.active_issues.length}개${st.context.new_issue_ids.length ? ` (신규 ${st.context.new_issue_ids.length})` : ""}` : "–",
    memory: st ? (Object.entries(ops).map(([k, v]) => `${label(P.memoryOps, k)} ${v}`).join(" · ") || "변경 없음") : "–",
    usermodel: st ? `속성 변경 ${st.user_model_changes.length}건` : "–",
    rag: kr ? `검색 ${kr.hits.filter((x) => x.selected).length} · 사용 ${kr.hits.filter((x) => x.used).length}` : "–",
    judge: st ? decisionBadge(st.decision.decision) : "–",
    prompt: st?.prompt ? (st.prompt.delivered ? `전달: “${st.prompt.text}”` : `억제: “${st.prompt.text}”`) : "프롬프트 없음",
    outcome: st?.outcome_updates?.length
      ? st.outcome_updates.map((o) => `${o.prompt_id} ${label(P.outcomes, o.outcome)}`).join(", ") : "변화 없음",
  };
  return h("ol", { class: "pipeline-flow", "aria-label": P.lab.flowTitle },
    Object.entries(items).map(([key, summary]) => h("li", {},
      h("button", { type: "button", class: `flow-box flow-${key}${lab.ui.tab === FLOW_TABS[key] ? " active" : ""}`,
        dataset: { flow: key }, onclick: () => lab.setTab(FLOW_TABS[key]) },
      h("span", { class: "flow-name" }, P.flow[key]), h("span", { class: "flow-summary" }, summary)))));
}

// ------------------------------------------------------------------ live monitor
export function monitorPanel(lab) {
  const st = lab.step;
  if (!st) return empty(P.lab.notStarted);
  const c = st.context;
  const ev = st.event;
  const a = c.audience;
  const tension = snapshotAttr(st, lab, "estimated_tension");
  const load = snapshotAttr(st, lab, "estimated_cognitive_load");
  const eventsSoFar = lab.steps.filter((x) => x.step <= st.step).slice(-8).reverse();
  return h("div", { class: "monitor-grid" },
    section(F.slide,
      h("p", { class: "slide-now" }, h("strong", {}, `#${c.current_slide} ${c.slide_title}`),
        " ", badge(`${F.timeOnSlide} ${mmss(c.time_on_slide)} / 계획 ${mmss(c.planned_slide_seconds)}`, "muted")),
      h("h4", {}, F.expected),
      c.expected_content.length
        ? h("ul", { class: "expected" }, c.expected_content.map((kp) => h("li", { class: kp.covered ? "covered" : "open" },
            h("span", { class: "tick", "aria-label": kp.covered ? "언급됨" : "아직 언급 안 됨" }, kp.covered ? "✓" : "○"), " ",
            kp.text, kp.essential ? badge("필수", "warn") : null, kp.covered_in ? h("small", { class: "muted" }, ` (${kp.covered_in})`) : null)))
        : empty("이 슬라이드에는 예상 내용이 없습니다(질의응답)."),
      c.missed_kp_ids.length ? notice(`누락된 필수 내용: ${c.missed_kp_ids.join(", ")}`, "warn") : null),
    section(F.transcript,
      h("ul", { class: "transcript" }, c.recent_transcript.slice().reverse().map((t) =>
        h("li", { class: t.event_id === ev.event_id ? "now" : "" },
          h("span", { class: "muted" }, `${mmss(t.elapsed)} · ${label(P.eventTypes, t.type)} `),
          t.text ? h("q", {}, t.text) : h("em", { class: "muted" }, "(발화 없음)")))),
      c.active_question ? kv([[F.question, `${c.active_question.text} → ${c.active_question.topic ?? "예상 질문과 매칭 안 됨"}${c.active_question.answered ? " (답변 완료)" : ""}`]]) : null),
    section("시간·발화 신호", kv([
      [F.remaining, `${mmss(c.remaining_time)} (${F.budget} ${fmtNum(c.time_budget_ratio)})`],
      [F.lag, `${fmtNum(c.schedule_lag_s, 0)}초`],
      [F.rate, `${ev.speech_rate || "–"} ${ev.speech_rate_unit} (평소 대비 ×${fmtNum(c.speech_rate_ratio)})`],
      [F.silence, `${fmtNum(ev.silence_duration, 1)}초`],
      [F.filler, `${ev.filler_count}회 (최근 창 ${c.filler_window}회)`],
      [F.phrase, c.repeated_phrase ? `‘${c.repeated_phrase.text}’ ×${c.repeated_phrase.window_count}` : ko.common.none],
    ])),
    section(`${F.audience} · ${F.presenterState}`, kv([
      [F.attention, meter(a.attention, { title: F.attention })],
      [F.confusion, meter(a.confusion, { kind: "warn", title: F.confusion })],
      [F.reaction, a.reaction],
      [F.arousal, meter(c.presenter_signal.arousal, { title: F.arousal })],
      [F.tension, tension ? h("span", {}, meter(tension.value), ` 신뢰도 ${fmtNum(tension.confidence)}`) : "–"],
      [F.load, load ? h("span", {}, meter(load.value), ` 신뢰도 ${fmtNum(load.confidence)}`) : "–"],
    ]), h("p", { class: "muted small" }, "청중 반응과 각성도는 더미 센서(Mock) 값입니다.")),
    section(F.issues, c.active_issues.length
      ? simpleTable(["ID", "문제", "심각도", "긴급도", "대상", "설명"], c.active_issues.map((i) => h("tr", {},
          h("td", {}, refChip(i.issue_id)), h("td", {}, issueLabel(i.type), c.new_issue_ids.includes(i.issue_id) ? badge("신규", "warn") : null),
          h("td", {}, meter(i.severity)), h("td", {}, meter(i.urgency)), h("td", {}, i.target_label ?? i.target ?? "–"),
          h("td", { class: "small" }, i.description))))
      : empty("감지된 문제가 없습니다."),
      c.resolved_issue_ids.length ? h("p", { class: "small" }, `이번 이벤트에서 해결: ${c.resolved_issue_ids.join(", ")}`) : null),
    section(F.prompt, mobileCard(st.mobile)),
    section(F.stream, h("ol", { class: "stream" }, eventsSoFar.map((x) => h("li", {},
      h("button", { type: "button", class: "btn-link", onclick: () => lab.selectStep(x.step) }, `${x.step}. ${x.event_id}`),
      ` ${mmss(x.elapsed_time)} `, decisionBadge(x.decision.decision),
      x.prompt?.delivered ? h("span", { class: "small" }, ` “${x.prompt.text}”`) : null)))),
  );
}

// ------------------------------------------------------------------ judge inspector
export function judgePanel(lab) {
  const st = lab.step;
  if (!st) return empty(P.lab.notStarted);
  const d = st.decision;
  const thresholds = lab.config?.judge_thresholds;
  const gt = lab.view.ground_truth?.[st.event_id];
  const row = lab.view.evaluation?.rows?.find((r) => r.event_id === st.event_id);
  const mem = Object.fromEntries((st.memory_snapshot ?? []).map((m) => [m.memory_id, m]));
  const evText = Object.fromEntries(lab.steps.map((x) => [x.event_id, x.event]));
  const onRef = lab.onRef;
  const comps = d.score_breakdown ?? [];
  return h("div", { class: "judge-panel" },
    h("section", { class: `card judge-final dec-card-${d.decision}` },
      h("h3", {}, J.final, " ", decisionBadge(d.decision), " ",
        d.is_mock ? badge(ko.common.simulated, "mock") : badge(d.judge, "real"),
        d.fallback_used ? badge(`${J.fallback} (${d.fallback_reason ?? ""})`, "warn") : null,
        d.policy_overrides?.length ? badge(J.override, "warn") : null),
      h("div", { class: "score-row" },
        h("div", {}, h("span", { class: "muted" }, "감지된 문제"), h("strong", {}, issueLabel(d.detected_issue))),
        h("div", {}, h("span", { class: "muted" }, P.scores.severity), meter(d.severity)),
        h("div", {}, h("span", { class: "muted" }, P.scores.urgency), meter(d.urgency)),
        h("div", {}, h("span", { class: "muted" }, ko.common.confidence), meter(d.confidence)),
        h("div", {}, h("span", { class: "muted" }, J.utility), h("strong", {}, fmtNum(d.utility)))),
      h("p", { class: "explain" }, h("strong", {}, `${J.explainTitle}: `), explainJudge(d, thresholds)),
      thresholds ? h("p", { class: "small muted" }, J.thresholds(thresholds)) : null,
      d.recommended_prompt_type ? h("p", {}, "추천 프롬프트: ", promptTypeLabel(d.recommended_prompt_type), " · ",
        label(P.lengths, d.recommended_prompt_length), ` · 쿨다운 ${fmtNum(d.cooldown_seconds, 0)}초`) : null,
      kv([["ID", h("code", {}, d.decision_id)], ["Judge", h("code", {}, d.judge)], ["판단 소요", `${fmtNum(d.latency_ms, 1)} ms`]])),
    comps.length ? section(J.scores,
      simpleTable(["항목", "값", "가중치", "기여", "근거", "설명"], comps.map((c) => h("tr", { dataset: { component: c.name } },
        h("td", {}, label(P.scores, c.name)), h("td", { class: "num" }, meter(c.value)),
        h("td", { class: "num" }, fmtNum(c.weight)), h("td", { class: `num ${c.contribution < 0 ? "neg" : "pos"}` }, fmtNum(c.contribution, 3)),
        h("td", {}, refList(c.evidence_refs, onRef)), h("td", { class: "small" }, c.explanation))), { id: "judge-scores" }),
      h("p", { class: "small" }, `합계(효용) = ${comps.map((c) => fmtNum(c.contribution, 3)).join(" + ")} = ${fmtNum(d.utility, 3)}`),
      d.cooldown_state?.confidence_parts ? h("p", { class: "small" }, `${J.confidenceParts}: 0.35×탐지 ${fmtNum(d.cooldown_state.confidence_parts.detector)} + 0.2×근거 수 ${fmtNum(d.cooldown_state.confidence_parts.evidence)} + 0.25×지식 ${fmtNum(d.cooldown_state.confidence_parts.knowledge)} + 0.2×사용자 모델 확신`) : null)
      : notice(J.noIssue, "info"),
    h("div", { class: "grid" },
      section(J.supporting, h("ul", {}, (d.supporting_reasons ?? []).map((r) => h("li", {}, r))), h("p", { class: "small muted" }, P.logNotice)),
      section(J.counter, (d.counter_reasons ?? []).length ? h("ul", {}, d.counter_reasons.map((r) => h("li", {}, r))) : empty(ko.common.none)),
      section(J.transcript, h("ul", { class: "transcript" }, (d.evidence_event_ids ?? []).map((id) => h("li", {},
        refChip(id, onRef), " ", evText[id] ? h("q", {}, evText[id].transcript_chunk || evText[id].audience_question || "(침묵)") : "")))),
      section(J.memories, (d.used_memory_ids ?? []).length ? h("ul", {}, d.used_memory_ids.map((id) => h("li", {},
        refChip(id, onRef), " ", mem[id] ? `[${label(P.memoryTypes, mem[id].memory_type)}] ${mem[id].content}` : ""))) : empty("사용한 메모리 없음")),
      section(J.userModel, (d.used_user_model_attributes ?? []).length ? h("ul", {}, d.used_user_model_attributes.map((k) => {
        const attr = st.user_model_snapshot?.[k];
        return h("li", {}, refChip(`um:${k}`, onRef), attr ? ` = ${umValue(attr.value)} (신뢰도 ${fmtNum(attr.confidence)})` : "");
      })) : empty("사용한 속성 없음")),
      section(J.knowledge, (d.used_knowledge_ids ?? []).length ? h("ul", {}, d.used_knowledge_ids.map((id) => h("li", {},
        refChip(id, onRef), " ", lab.chunks[id]?.title ?? ""))) : empty("판단에 사용한 지식 없음")),
      section(J.cooldown, kv([
        ["직전 프롬프트", d.cooldown_state?.last_prompt_id ? refChip(d.cooldown_state.last_prompt_id, onRef) : ko.common.none],
        ["경과 시간", d.cooldown_state?.seconds_since_last !== null && d.cooldown_state?.seconds_since_last !== undefined ? `${fmtNum(d.cooldown_state.seconds_since_last, 0)}초` : "–"],
        ["쿨다운", d.cooldown_state?.cooldown_seconds ? `${fmtNum(d.cooldown_state.cooldown_seconds, 0)}초 ${d.cooldown_state.in_cooldown ? "(진행 중)" : "(종료)"}` : "–"],
        ["중복 대상", d.cooldown_state?.redundant_with ? refChip(d.cooldown_state.redundant_with, onRef) : ko.common.none],
        ["정책 변경", (d.policy_overrides ?? []).join(" / ") || ko.common.none],
      ])),
      section(`${J.gt} `, badge(P.researcherOnly, "warn"),
        gt ? kv([
          ["정답 문제", issueLabel(gt.issue)],
          ["개입 필요", gt.expected_intervention ? ko.common.yes : ko.common.no],
          ["정답 판단", row?.gt_decision ? decisionLabel(row.gt_decision) : "–"],
          ["정답 프롬프트 유형", gt.expected_prompt_type ? promptTypeLabel(gt.expected_prompt_type) : "–"],
          ["심각도", label(P.severity, gt.severity)],
          ["메모", gt.note ?? "–"],
          ["비교 결과", row ? h("span", {}, badge(label(P.matches, row.match), row.match.startsWith("TP") || row.match === "TN" ? "ok" : "bad"), " ",
            row.decision_agrees === null ? J.noLabel : row.decision_agrees ? badge(J.agree, "ok") : badge(J.disagree, "bad")) : "–"],
        ]) : empty("라벨 없음"),
        row?.mismatch_notes?.length ? h("ul", { class: "mismatch" }, row.mismatch_notes.map((n) => h("li", {}, n))) : null)),
    section(J.considered, (d.considered_issues ?? []).length
      ? simpleTable(["ID", "문제", "심각도", "긴급도", "우선순위", "이미 알림"], d.considered_issues.map((ci, i) => h("tr", { class: i === 0 ? "top" : "" },
          h("td", {}, ci.issue_id), h("td", {}, issueLabel(ci.type)), h("td", { class: "num" }, fmtNum(ci.severity)),
          h("td", { class: "num" }, fmtNum(ci.urgency)), h("td", { class: "num" }, fmtNum(ci.priority, 3)),
          h("td", {}, ci.already_prompted ? "예(×0.6)" : "아니요"))))
      : empty(ko.common.none)),
    section(J.rawSignals, kv(Object.entries(d.raw_signals ?? {}).map(([k, v]) => [k, typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)]))),
    devJson(d),
  );
}

// ------------------------------------------------------------------ events & stages
export function eventsPanel(lab) {
  const st = lab.step;
  if (!st) return empty(P.lab.notStarted);
  const e = st.event;
  const gt = lab.view.ground_truth?.[e.event_id];
  return h("div", {},
    section(`관찰 이벤트 ${e.event_id}`, badge(P.synthetic, "mock"),
      kv([
        ["timestamp", e.timestamp], ["elapsed_time", mmss(e.elapsed_time)], ["language", e.language],
        ["event_type", label(P.eventTypes, e.event_type)], ["current_slide", `#${e.current_slide} ${e.slide_title}`],
        ["slide_expected_content", e.slide_expected_content.join(" / ") || "–"],
        ["transcript_chunk", e.transcript_chunk || "–"], ["audience_question", e.audience_question ?? "–"],
        ["speech_rate", `${e.speech_rate} ${e.speech_rate_unit}`], ["silence_duration", `${e.silence_duration}s`],
        ["filler_count", String(e.filler_count)],
        ["repeated_phrase", e.repeated_phrase ? `${e.repeated_phrase.text} ×${e.repeated_phrase.count}` : "–"],
        ["audience_signal", JSON.stringify(e.audience_signal)], ["remaining_time", mmss(e.remaining_time)],
        ["presenter_state", JSON.stringify(e.presenter_state)],
        ["previous_interventions", e.previous_interventions.length ? e.previous_interventions.map((p) => `${p.prompt_id}(${promptTypeLabel(p.prompt_type)}, ${label(P.outcomes, p.outcome)})`).join(", ") : ko.common.none],
        ["입력 출처(교체 지점)", Object.entries(e.sources).map(([k, v]) => `${k}: ${v}`).join(" · ")],
      ])),
    h("section", { class: "card card-warn" }, h("h3", {}, "정답 라벨 ", badge(P.researcherOnly, "warn")),
      notice(P.gtNotice, "info"),
      gt ? kv(Object.entries(gt).map(([k, v]) => [k, v === null ? "–" : String(v)])) : empty("라벨 없음")),
    section(P.lab.stages, simpleTable(["단계", "상태", "소요(ms)", "오류"], st.stages.map((x) => h("tr", { class: x.status === "error" ? "row-fail" : "" },
      h("td", {}, label(P.stageNames, x.name)), h("td", {}, badge(label(P.stageStatus, x.status), x.status === "ok" ? "ok" : "bad")),
      h("td", { class: "num" }, fmtNum(x.latency_ms, 2)), h("td", { class: "small" }, x.error ?? "–"))))),
    devJson(st),
  );
}
