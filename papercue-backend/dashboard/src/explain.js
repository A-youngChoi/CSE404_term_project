// Pure functions that turn structured backend records into readable Korean explanations.
// They only restate stored inputs, outputs, scores and codes - never hidden model reasoning.
import { ko, label } from "./i18n/ko.js";
import { fmtNum } from "./dom.js";

export const keyLabel = (key) => (key ? ko.keys[key] ?? key : ko.common.none);
export const valueLabel = (v) => label(ko.values, v);
export const dimLabel = (d) => label(ko.dimensions, d);

function josa(word, withBatchim, without) {
  const last = String(word ?? "").trim().slice(-1);
  const code = last.charCodeAt(0);
  if (code < 0xac00 || code > 0xd7a3) return `${withBatchim}(${without})`;
  return (code - 0xac00) % 28 ? withBatchim : without;
}

const KNOWN_REASONS = {
  "Profile prior (uncertain hypothesis).": "프로필에서 온 사전 가설(불확실)",
};

/** Stored update reasons are short English strings written by the extractor; known ones are translated. */
export function reasonText(reason) {
  return KNOWN_REASONS[reason] ?? `추출기 기록(영문): ${reason}`;
}

export function sourceText(sourceType, turnIndex) {
  if (sourceType === "profile") return "수동으로 입력한 프로필 항목(불확실한 가설)";
  return turnIndex ? `${turnIndex}번 청중 발화` : "대화 발화";
}

export function explainEvidence(e, turnIndex) {
  const k = keyLabel(e.key);
  const type = label(ko.evidenceTypes, e.evidence_type);
  if (e.dimension === "concern" && e.value === "raised") {
    const how = e.evidence_type === "explicit" ? "직접 질문했기 때문에" : "언급했기 때문에";
    return `청중이 ‘${k}’ 관련 내용을 ${how} ‘우려 사항: ${k}’ 근거로 기록했습니다(${type}).`;
  }
  if (e.dimension === "familiarity" && e.value === "unfamiliar" && e.evidence_type === "explicit") {
    return `청중이 ‘${k}’에 익숙하지 않다고 직접 말했습니다(${type}). 프로필 정보보다 우선합니다.`;
  }
  const where = turnIndex ? `${turnIndex}번 청중 발화에서 ` : "";
  return `${where}‘${dimLabel(e.dimension)}: ${k} = ${valueLabel(e.value)}’ 근거를 추출했습니다(${type}).`;
}

export function explainBeliefChange(c, turnIndex) {
  const dim = dimLabel(c.dimension);
  const k = keyLabel(c.key);
  const oldC = c.old_confidence === null || c.old_confidence === undefined ? "없음" : fmtNum(c.old_confidence);
  const newC = fmtNum(c.new_confidence);
  const src = sourceText(c.source_type, turnIndex);
  switch (c.change_type) {
    case "created":
      if (c.dimension === "concern" && c.source_type === "conversation" && c.evidence_type === "explicit") {
        return `청중이 직접 질문했기 때문에 ‘${k}’${josa(k, "이", "가")} 현재 우려 사항으로 추가되었습니다(신뢰도 ${oldC} → ${newC}). 이 판단은 프로필이 아니라 ${src}에 근거합니다.`;
      }
      return `‘${dim}: ${k}’ 믿음이 새로 생겼습니다(값 ‘${valueLabel(c.new_value)}’, 신뢰도 ${newC}). 이 판단은 ${src}에 근거합니다.`;
    case "reinforced":
      return `같은 방향의 근거가 다시 나와 ‘${dim}: ${k}’의 신뢰도가 ${oldC} → ${newC}로 높아졌습니다.`;
    case "confirmed":
      return `프로필 가설이던 ‘${dim}: ${k}’${josa(k, "이", "가")} ${src}에서 확인되어 대화 근거로 바뀌었습니다(${oldC} → ${newC}).`;
    case "weakened":
      return `‘${dim}: ${k}’와 맞지 않는 근거가 나와 신뢰도를 ${oldC} → ${newC}로 낮췄습니다. 한 번의 반대 근거로는 값을 바로 뒤집지 않습니다.`;
    case "reversed":
      return `반대 방향 근거가 이어져 ‘${dim}: ${k}’의 값이 ‘${valueLabel(c.old_value)}’에서 ‘${valueLabel(c.new_value)}’로 바뀌었습니다(${oldC} → ${newC}).`;
    case "overridden":
      return `현재 대화 근거(${src})가 이전 가정 ‘${valueLabel(c.old_value)}’보다 우선하므로 ‘${dim}: ${k}’를 ‘${valueLabel(c.new_value)}’로 대체했습니다(${oldC} → ${newC}).`;
    default:
      return `‘${dim}: ${k}’ 변경(${c.change_type}): ${oldC} → ${newC}`;
  }
}

export function explainDecision(decision) {
  const fn = ko.reasonCodes[decision?.reason_code];
  const params = { ...(decision?.reason_params ?? {}) };
  if (params.target) params.target = keyLabel(params.target);
  return fn ? fn(params) : decision?.short_reason ?? "";
}

export function explainStatus(code) {
  return ko.finalStatusExplain[code] ?? code ?? "";
}

export function stageExplanation(stage, ctx = {}) {
  if (!stage) return "";
  if (stage.status === "skipped") return ko.rationale[stage.rationale_code] ?? stage.short_rationale ?? "";
  if (stage.status === "failed") {
    return ko.rationale[stage.rationale_code] ?? `이 단계가 실패했습니다: ${(stage.validation_errors ?? []).join(", ")}`;
  }
  const o = stage.output ?? {};
  switch (stage.stage_name) {
    case "input_turn":
      if (o.trigger_turn_index) return `${o.trigger_turn_index}번 청중 발화를 기준으로 ${o.mode === "on_demand" ? "단서를 요청" : "자동 개입 후보를 평가"}했습니다.`;
      return `${o.turn_index}번 ${o.speaker === "listener" ? "청중" : "발표자"} 발화(${o.word_count}단어)를 텍스트로 입력받아 로컬에 저장했습니다.`;
    case "recent_context": {
      const idx = o.window_turn_indices ?? [];
      return `최근 발화 ${idx.length}개(${idx.join(", ") || "없음"}번)와 이전 발화 ${o.summarized_turns ?? 0}개의 구조화된 요약만 맥락으로 사용했습니다. 전체 대화를 모델에 보내지 않습니다.`;
    }
    case "dialogue_act_analysis": {
      if (stage.rationale_code === "state_loaded") {
        return `현재 단계는 ‘${label(ko.phases, o.phase_after)}’, 최근 청중 행위는 ‘${label(ko.listenerActs, o.listener_act)}’입니다.`;
      }
      const act = o.listener_act ? `청중 행위: ${label(ko.listenerActs, o.listener_act)}` : `발표자 행위: ${label(ko.presenterActs, o.presenter_act)}`;
      const phase = o.phase_before === o.phase_after
        ? `대화 단계는 ‘${label(ko.phases, o.phase_after)}’로 유지됩니다.`
        : `대화 단계가 ‘${label(ko.phases, o.phase_before)}’에서 ‘${label(ko.phases, o.phase_after)}’로 바뀌었습니다.`;
      const concern = o.detected_concern ? ` 우려 사항 ‘${keyLabel(o.detected_concern)}’이(가) 감지되었습니다.` : "";
      return `${act}. ${phase}${concern}`;
    }
    case "evidence_extraction": {
      const items = o.items ?? [];
      if (!items.length) return ko.rationale.no_evidence;
      const rej = (o.rejected ?? []).length;
      const list = items.map((e) => `${dimLabel(e.dimension)}: ${keyLabel(e.key)}(${label(ko.evidenceTypes, e.evidence_type)})`).join(", ");
      return `근거 ${items.length}개를 채택했습니다 — ${list}.${rej ? ` 검증을 통과하지 못한 제안 ${rej}개는 버렸습니다.` : ""}`;
    }
    case "audience_update_proposal": {
      const n = (o.proposals ?? []).length;
      const who = o.strategy === "llm" ? "모델(또는 Mock 규칙)이" : "결정론적 규칙이";
      return `${who} 근거 ${n}개를 어떤 믿음에 연결할지 제안했습니다. 값과 신뢰도는 모델이 정하지 않고 갱신 규칙이 정합니다.`;
    }
    case "audience_update_applied": {
      if (o.beliefs) return `현재 청중 믿음 ${o.beliefs.length}개를 판단에 사용할 수 있습니다.`;
      const changes = o.changes ?? [];
      if (!changes.length) return ko.rationale.no_belief_change;
      return changes.map((c) => explainBeliefChange(c, ctx.turnIndex)).join(" ");
    }
    case "retrieval_query": {
      const parts = [];
      if (o.has_latest_question) parts.push("청중의 최근 질문");
      if (o.topic) parts.push(`현재 주제 ‘${keyLabel(o.topic)}’`);
      if (o.unresolved_issues?.length) parts.push(`해결되지 않은 질문(${o.unresolved_issues.map(keyLabel).join(", ")})`);
      if (o.concerns?.length) parts.push(`우려 사항(${o.concerns.map(keyLabel).join(", ")})`);
      if (o.action) parts.push(`의도한 행동 ‘${label(ko.actions, o.action)}’`);
      return `${parts.join(", ") || "사용 가능한 맥락"}을(를) 합쳐 검색 질의를 만들었습니다.`;
    }
    case "retrieval": {
      const units = o.units ?? [];
      if (!units.length) return ko.rationale.no_units_found;
      const top = units[0];
      return `논문 단위 ${units.length}개를 찾았습니다. 가장 관련 높은 단위는 ‘${top.title}’(점수 ${fmtNum(top.score, 3)})입니다. 전체 논문이 아닌 이 단위들만 다음 단계에 전달됩니다.`;
    }
    case "intervention_decision": {
      const verdict = o.should_intervene ? "개입이 필요하다고 판단했습니다" : "지금은 개입하지 않기로 판단했습니다";
      const shadow = o.evaluation === "shadow" ? " (발화 입력 시 자동으로 계산한 평가이며 단서는 만들지 않았습니다.)" : "";
      return `${verdict}. 선택된 행동: ${label(ko.actions, o.action)}(대상: ${keyLabel(o.target)}), 종합 점수 ${fmtNum(o.scores?.total)}. ${explainDecision(o)}${shadow}`;
    }
    case "cue_candidate":
      return `선택된 행동에 맞춰 후보 단서 “${o.cue}”(${o.word_count}단어, 신뢰도 ${fmtNum(o.confidence)})를 만들었습니다.`;
    case "safety_grounding_check": {
      const failed = (o.checks ?? []).filter((c) => !c.passed).map((c) => label(ko.validationCodes, c.name));
      if (!failed.length) return "모든 안전성·근거성 검사를 통과했습니다.";
      const fb = o.fallback_used ? " 대신 확인 질문 단서로 대체했습니다." : "";
      return `통과하지 못한 검사: ${failed.join(", ")}.${fb}`;
    }
    case "final_result": {
      const base = explainStatus(o.status);
      if (o.evaluation_only) return `${base} (평가 전용이므로 발표자에게 전달하지 않았습니다.)`;
      return o.delivered ? `${base} 최종 단서: “${o.final_cue}”` : base;
    }
    default:
      return ko.rationale[stage.rationale_code] ?? stage.short_rationale ?? "";
  }
}

export const NO_BROAD_CLAIM = {
  claim: "“이 청중은 개인정보 기술에 부정적이다.”",
  explanation:
    "근거는 ‘이 대화가 서버에 저장되는지’를 한 번 질문했다는 사실뿐입니다. 이것은 지금 이 대화에서의 우려(현재 대화 상태)를 뒷받침할 뿐, " +
    "사람의 지속적인 성향이나 태도를 뒷받침하지 않습니다. PaperCue는 한 번의 질문을 사람에 대한 폭넓은 판단으로 바꾸지 않으며, " +
    "성향·태도·심리 상태 같은 판단은 검증 단계에서 차단합니다(‘사람에 대한 성향 판단’, ‘청중에 대한 근거 없는 단정 없음’ 검사).",
};
