// Inspector panels of the simulation lab: memory, user model, RAG, prompts.
import { h, fmtNum } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import { badge, devJson, empty, kv, notice } from "../components.js";
import {
  P, decisionBadge, issueLabel, meter, mmss, promptTypeLabel, refChip, refList, section, simpleTable, umValue,
} from "./common.js";
import { lineChart } from "./charts.js";

const MEMORY_TYPES = ["working", "episodic", "semantic", "intervention", "reflection"];

function diffRows(before, after) {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])];
  return keys.filter((k) => JSON.stringify(before?.[k]) !== JSON.stringify(after?.[k])).map((k) => h("tr", {},
    h("td", {}, k),
    h("td", { class: "diff-old" }, before ? fmtVal(before[k]) : "–"),
    h("td", { class: "diff-new" }, after ? fmtVal(after[k]) : "–")));
}

function fmtVal(v) {
  if (v === undefined || v === null) return "–";
  if (typeof v === "number") return fmtNum(v, 3);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ------------------------------------------------------------------ memory inspector
export function memoryPanel(lab) {
  const st = lab.step;
  const snapshot = st?.memory_snapshot ?? lab.view.initial.memory_snapshot;
  const upTo = st?.step ?? 0;
  const filter = lab.ui.memoryType;
  const items = snapshot.filter((m) => !filter || m.memory_type === filter);
  const changes = st ? st.memory_changes : lab.view.initial.memory_changes;
  const highlight = lab.ui.memory;
  const history = highlight ? lab.view.memory_log.filter((c) => c.memory_id === highlight && c.step <= upTo) : [];
  const counts = Object.fromEntries(MEMORY_TYPES.map((t) => [t, snapshot.filter((m) => m.memory_type === t).length]));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "메모리 유형 필터" },
    [null, ...MEMORY_TYPES].map((t) => h("button", {
      type: "button", class: `chip${filter === t ? " active" : ""}`, onclick: () => lab.setUi({ memoryType: t }),
    }, t ? `${label(P.memoryTypes, t)} ${counts[t]}` : `전체 ${snapshot.length}`)));
  const mr = st?.memory_retrieval;
  const opsByStep = lab.steps.filter((x) => x.step <= upTo).map((x) => {
    const ops = {};
    for (const c of x.memory_changes) ops[c.op] = (ops[c.op] ?? 0) + 1;
    return { step: x.step, ops };
  });
  return h("div", {},
    chips,
    section(`메모리 상태 (${upTo}단계 시점)`,
      simpleTable(["ID", "유형", "내용", "생성", "출처 이벤트", "중요도", "최신성", "신뢰도", "검색 점수", "검색 횟수", "활성", "사용한 판단", "생성·수정 이유"],
        items.map((m) => h("tr", {
          class: `${m.memory_id === highlight ? "highlight" : ""} ${m.origin === "prior" ? "prior" : ""}`,
          dataset: { memoryId: m.memory_id }, onclick: () => lab.setUi({ memory: m.memory_id }),
        },
          h("td", {}, h("code", {}, m.memory_id)), h("td", {}, label(P.memoryTypes, m.memory_type), m.origin === "prior" ? badge("이전 발표", "muted") : null),
          h("td", { class: "mem-content" }, m.content), h("td", {}, `${m.created_step}단계 · ${mmss(m.created_elapsed)}`),
          h("td", {}, refList(m.source_event_ids, lab.onRef)), h("td", { class: "num" }, meter(m.importance)),
          h("td", { class: "num" }, fmtNum(m.recency)), h("td", { class: "num" }, meter(m.confidence)),
          h("td", { class: "num" }, fmtNum(m.retrieval_score)), h("td", { class: "num" }, String(m.retrieval_count)),
          h("td", {}, m.active ? "예" : "아니요"), h("td", {}, refList(m.used_in_decisions)),
          h("td", { class: "small" }, m.reason)))),
      h("p", { class: "small muted" }, "행을 클릭하면 아래에 그 메모리의 변경 이력이 표시됩니다. 최신성 = 0.995^(마지막 접근 이후 초).")),
    highlight ? section(`선택한 메모리 ${highlight}의 변경 이력`,
      history.length ? history.map((c) => h("div", { class: "diff" },
        h("p", {}, badge(label(P.memoryOps, c.op), c.op === "weakened" ? "bad" : c.op === "retrieved" ? "muted" : "ok"),
          ` ${c.step}단계 `, c.event_id ? refChip(c.event_id, lab.onRef) : "", ` — ${c.reason}`),
        c.after ? simpleTable(["필드", "이전", "이후"], diffRows(c.before, c.after)) : null))
        : empty("이 시점까지 변경 기록이 없습니다.")) : null,
    section(`이 단계의 변경 (${changes.length}건, before → after)`,
      changes.length ? changes.filter((c) => c.op !== "retrieved").map((c) => h("details", { class: "diff", open: c.op !== "revised" },
        h("summary", {}, badge(label(P.memoryOps, c.op), c.op === "weakened" ? "bad" : "ok"), " ",
          h("button", { type: "button", class: "btn-link", onclick: () => lab.setUi({ memory: c.memory_id }) }, c.memory_id), ` — ${c.reason}`),
        simpleTable(["필드", "이전", "이후"], diffRows(c.before, c.after))))
        : empty("변경 없음"),
      changes.some((c) => c.op === "retrieved") ? h("p", { class: "small" }, `검색되어 접근 시각·횟수가 갱신된 메모리: ${changes.filter((c) => c.op === "retrieved").map((c) => c.memory_id).join(", ")}`) : null),
    section("메모리 검색(retrieval)", mr ? h("div", {},
      kv([["검색 ID", h("code", {}, mr.retrieval_id)], ["질의", mr.query], ["질의 단어", mr.query_terms.join(", ")],
        ["점수식", `관련도×${mr.weights.relevance} + 중요도×${mr.weights.importance} + 최신성×${mr.weights.recency}`]]),
      simpleTable(["메모리", "점수", "관련도", "중요도", "최신성", "선택", "사유"], mr.hits.map((x) => h("tr", { class: x.selected ? "sel" : "" },
        h("td", {}, refChip(x.id, lab.onRef)), h("td", { class: "num" }, fmtNum(x.score, 3)),
        h("td", { class: "num" }, fmtNum(x.components.relevance)), h("td", { class: "num" }, fmtNum(x.components.importance)),
        h("td", { class: "num" }, fmtNum(x.components.recency)), h("td", {}, x.selected ? "✓" : ""), h("td", { class: "small" }, x.reason)))))
      : empty("이 단계에는 활성 문제가 없어 메모리를 검색하지 않았습니다.")),
    section("시간에 따른 메모리 연산", simpleTable(["단계", ...Object.values(P.memoryOps)], opsByStep.map((r) => h("tr", {},
      h("td", {}, h("button", { type: "button", class: "btn-link", onclick: () => lab.selectStep(r.step) }, String(r.step))),
      ...Object.keys(P.memoryOps).map((op) => h("td", { class: "num" }, String(r.ops[op] ?? ""))))))),
  );
}

// ------------------------------------------------------------------ user model inspector
const NUMERIC_ATTRS = new Set(["baseline_speech_rate", "baseline_filler_rate", "prompt_responsiveness", "estimated_cognitive_load", "estimated_tension"]);

export function userModelPanel(lab) {
  const st = lab.step;
  const upTo = st?.step ?? 0;
  const snap = st?.user_model_snapshot ?? lab.view.initial.user_model_snapshot;
  const attrKey = lab.ui.attr ?? "baseline_speech_rate";
  const allChanges = [...lab.view.initial.user_model_changes, ...lab.view.user_model_history.filter((c) => c.step > 0)]
    .filter((c) => c.step <= upTo);
  const history = allChanges.filter((c) => c.attribute === attrKey);
  const attr = snap[attrKey];
  const steps = lab.steps.filter((x) => x.step <= upTo);
  const valueAt = (key) => steps.map((x) => ({ x: x.step, y: Number(x.user_model_snapshot[key]?.value) }));
  const confAt = (key) => steps.map((x) => ({ x: x.step, y: x.user_model_snapshot[key]?.confidence ?? 0 }));
  const xMax = lab.view.summary.total;
  const unit = lab.view.presenter.speech_rate_unit;
  const rates = steps.map((x) => x.event.speech_rate).filter((v) => v > 0);
  const rateMax = Math.max(100, ...rates, ...steps.map((x) => x.user_model_snapshot.baseline_speech_rate?.value ?? 0)) * 1.1;
  return h("div", {},
    section(`사용자 모델 (${upTo}단계 시점) — 발표자 ${lab.view.presenter.display_name}`,
      simpleTable(["속성", "현재 값", "신뢰도", "출처", "마지막 갱신", ""], Object.entries(snap).map(([k, a]) => h("tr", {
        class: k === attrKey ? "highlight" : "", dataset: { attr: k },
      },
        h("td", {}, label(P.umAttrs, k)), h("td", {}, umValue(a.value)), h("td", {}, meter(a.confidence)),
        h("td", {}, badge(label(P.umSource, a.source), a.source === "prior" ? "muted" : "real")),
        h("td", {}, `${a.updated_step}단계`),
        h("td", {}, h("button", { type: "button", class: "btn-link", onclick: () => lab.setUi({ attr: k }) }, "왜 이 값인가?")))))),
    section(`‘${label(P.umAttrs, attrKey)}’이(가) 현재 값이 된 이유`,
      attr ? kv([["현재 값", umValue(attr.value)], ["신뢰도", meter(attr.confidence)], ["출처", label(P.umSource, attr.source)],
        ["최근 근거", h("ul", { class: "small" }, attr.evidence.slice().reverse().map((e) => h("li", {}, refChip(e.ref, lab.onRef), ` ${e.note}`)))]]) : empty("–"),
      simpleTable(["변경 ID", "단계", "이벤트", "이전 값 → 새 값", "신뢰도", "갱신 규칙", "근거"], history.slice().reverse().map((c) => h("tr", {},
        h("td", {}, h("code", {}, c.change_id)), h("td", {}, String(c.step)), h("td", {}, c.event_id ? refChip(c.event_id, lab.onRef) : "프로필"),
        h("td", {}, `${umValue(c.old_value)} → ${umValue(c.new_value)}`),
        h("td", {}, `${c.old_confidence === null ? "–" : fmtNum(c.old_confidence)} → ${fmtNum(c.new_confidence)}`),
        h("td", { class: "small" }, c.reason), h("td", {}, refList(c.evidence_refs, lab.onRef))))),
      steps.length ? h("div", { class: "grid" },
        NUMERIC_ATTRS.has(attrKey)
          ? lineChart([{ name: label(P.umAttrs, attrKey), points: valueAt(attrKey) }],
            attrKey === "baseline_speech_rate" ? { title: `값 변화(${unit})`, yMin: 0, yMax: rateMax, xMax, selected: upTo, digits: 1 }
              : attrKey === "baseline_filler_rate" ? { title: "값 변화(이벤트당 군말)", yMin: 0, yMax: 3, xMax, selected: upTo }
                : { title: "값 변화(0–1)", xMax, selected: upTo })
          : null,
        // Confidence has its own 0-1 axis; never share an axis with a differently scaled value.
        lineChart([{ name: "신뢰도", points: confAt(attrKey) }], { title: "신뢰도 변화(0–1)", xMax, selected: upTo })) : null),
    steps.length ? h("div", { class: "grid" },
      lineChart([
        { name: `관찰 발화 속도(${unit})`, points: steps.filter((x) => x.event.speech_rate > 0).map((x) => ({ x: x.step, y: x.event.speech_rate, note: x.event_id })) },
        { name: "평소 속도 추정", points: valueAt("baseline_speech_rate"), dashed: true },
      ], { title: "발화 속도 추정치 변화", yMin: 0, yMax: rateMax, xMax, selected: upTo, digits: 0 }),
      lineChart([
        { name: "추정 긴장도", points: valueAt("estimated_tension") },
        { name: "추정 인지부하", points: valueAt("estimated_cognitive_load") },
      ], { title: "긴장도·인지부하 추정 변화", xMax, selected: upTo }),
      lineChart([
        { name: "프롬프트 반응성", points: valueAt("prompt_responsiveness") },
        { name: "반응성 신뢰도", points: confAt("prompt_responsiveness"), dashed: true },
      ], { title: "개입 효과 추정 변화", xMax, selected: upTo }),
      section("반복 문제·선호 길이 추정", kv([
        ["자주 나타나는 어려움", umValue(snap.frequent_difficulties?.value)],
        ["반복적으로 놓치는 내용", umValue(snap.often_missed_content?.value)],
        ["선호 프롬프트 길이", `${umValue(snap.preferred_prompt_length?.value)} (신뢰도 ${fmtNum(snap.preferred_prompt_length?.confidence)})`],
        ["효과적이었던 상황", umValue(snap.effective_contexts?.value)],
        ["방해가 된 상황", umValue(snap.disruptive_contexts?.value)],
      ]))) : null,
  );
}

// ------------------------------------------------------------------ RAG inspector
export function ragPanel(lab) {
  const st = lab.step;
  const kr = st?.knowledge_retrieval;
  if (!kr) return empty(st ? "이 단계에서 지식 검색이 실행되지 않았습니다(오류 또는 대체 처리)." : P.lab.notStarted);
  const promptKnowledge = new Set(st.prompt?.knowledge_ids ?? []);
  const highlight = lab.ui.chunk;
  return h("div", {},
    section("검색 질의", kv([
      ["검색 ID", h("code", {}, kr.retrieval_id)], ["검색기", h("code", {}, kr.retriever)], ["질의", kr.query],
      ["질의 단어", kr.query_terms.join(", ")],
      ["점수 구성", "키워드 일치(최대 0.6) + 단어 겹침×0.4 + 현재 슬라이드 +0.2 + 감지된 문제의 대상 +0.45 + 문제 유형 관련 범주 +0.1 (임베딩 검색기는 앞 두 항목 대신 코사인 유사도×0.7)"],
      ["검색 기준", "상위 5개, 점수 ≥ 0.30"],
    ])),
    section("검색된 지식 조각", simpleTable(["조각", "제목", "범주", "원본 문서", "슬라이드 관계", "점수", "점수 구성", "선택", "판단에 사용", "프롬프트 반영", "사유"],
      kr.hits.map((x) => {
        const c = lab.chunks[x.id] ?? {};
        const rel = c.slide == null ? "슬라이드 무관" : c.slide === st.event.current_slide ? "현재 슬라이드" : `슬라이드 #${c.slide}`;
        return h("tr", { class: `${x.used ? "sel" : ""} ${x.id === highlight ? "highlight" : ""}`, dataset: { chunk: x.id } },
          h("td", {}, h("code", {}, x.id)), h("td", {}, c.title ?? "–", c.essential ? badge("필수", "warn") : null),
          h("td", {}, label(P.kbCategories, c.category)), h("td", { class: "small" }, c.source_document ?? "–"),
          h("td", {}, rel), h("td", { class: "num" }, meter(x.score)),
          h("td", { class: "small" }, Object.entries(x.components).map(([k, v]) => `${k} ${fmtNum(v)}`).join(" · ")),
          h("td", {}, x.selected ? "✓" : ""), h("td", {}, x.used ? badge("사용", "ok") : ""),
          h("td", {}, promptKnowledge.has(x.id) ? badge("반영", "real") : ""), h("td", { class: "small" }, x.reason));
      }), { id: "rag-hits" })),
    st.prompt ? section("검색 결과가 프롬프트에 반영된 방식", kv([
      ["프롬프트", `“${st.prompt.text}”`],
      ["사용한 지식", refList(st.prompt.knowledge_ids)],
      ["채운 슬롯", Object.entries(st.prompt.context_used?.slots ?? {}).map(([k, v]) => `${k}=${v}`).join(" · ")],
    ])) : null,
    highlight && lab.chunks[highlight] ? section(`지식 조각 ${highlight}`, kv(Object.entries(lab.chunks[highlight])
      .filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length)).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)]))) : null,
    h("details", { class: "card" }, h("summary", {}, `지식 베이스 전체 (${Object.keys(lab.chunks).length}개 조각, 합성 데이터)`),
      simpleTable(["ID", "범주", "제목", "내용", "슬라이드"], Object.values(lab.chunks).map((c) => h("tr", {},
        h("td", {}, h("code", {}, c.chunk_id)), h("td", {}, label(P.kbCategories, c.category)), h("td", {}, c.title),
        h("td", { class: "small" }, c.text), h("td", {}, c.slide ?? "–"))))),
  );
}

// ------------------------------------------------------------------ prompt inspector
export function promptsPanel(lab) {
  const upTo = lab.step?.step ?? 0;
  const prompts = lab.view.prompts.filter((p) => p.step <= upTo);
  if (!prompts.length) return empty("이 시점까지 생성된 프롬프트가 없습니다.");
  const selectedId = lab.ui.prompt && prompts.some((p) => p.prompt_id === lab.ui.prompt)
    ? lab.ui.prompt : (lab.step?.prompt?.prompt_id ?? prompts[prompts.length - 1].prompt_id);
  const p = prompts.find((x) => x.prompt_id === selectedId);
  const later = lab.view.summary.cursor > upTo;
  return h("div", {},
    section("생성된 프롬프트", simpleTable(["ID", "단계", "시각", "유형", "문구", "우선순위", "전달", "결과"], prompts.map((x) => h("tr", {
      class: x.prompt_id === selectedId ? "highlight" : "", dataset: { promptId: x.prompt_id }, onclick: () => lab.setUi({ prompt: x.prompt_id }),
    },
      h("td", {}, h("code", {}, x.prompt_id)), h("td", {}, String(x.step)), h("td", {}, mmss(x.created_elapsed)),
      h("td", {}, promptTypeLabel(x.prompt_type)), h("td", {}, `“${x.text}”`), h("td", {}, label(P.priorities, x.priority)),
      h("td", {}, x.delivered ? badge("전달", "ok") : badge("미전달", "muted")),
      h("td", {}, x.delivered ? label(P.outcomes, x.outcome) : "–"))))),
    h("section", { class: `card prompt-detail${p.delivered ? "" : " withheld"}` },
      h("h3", {}, `프롬프트 ${p.prompt_id} `, p.delivered ? badge("모바일에 전달됨", "ok") : badge("전달하지 않음", "muted"),
        p.generator === "template" ? badge("템플릿", "neutral") : badge(p.generator, "real"),
        p.fallback_used ? badge("LLM 실패 → 템플릿 대체", "warn") : null),
      h("div", { class: "cue-big", lang: p.language }, `“${p.text}”`),
      !p.delivered ? notice(`전달하지 않은 이유: ${p.not_delivered_reason ?? "–"}`, "warn") : null,
      kv([
        ["생성 시점", `${p.step}단계 · ${mmss(p.created_elapsed)} (${p.event_id})`],
        ["유형 · 언어 · 길이", `${promptTypeLabel(p.prompt_type)} · ${label(P.languages, p.language)} · ${label(P.lengths, p.length)}`],
        ["우선순위", `${label(P.priorities, p.priority)} (0.6×심각도 + 0.4×긴급도 = ${fmtNum(p.priority_score)})`],
        ["표시 시간 · 만료", `${fmtNum(p.display_seconds, 0)}초 · ${mmss(p.expires_at_elapsed)}에 사라짐`],
        ["예상 목적", p.purpose],
        ["선택 이유", p.selection_reason],
        ["대상 문제", `${issueLabel(p.issue_type)} (${p.issue_id ?? "–"}, 대상 ${p.target ?? "–"})`],
        ["판단", h("span", {}, refChip(p.decision_id), " ", lab.steps.find((x) => x.step === p.step) ? decisionBadge(lab.steps.find((x) => x.step === p.step).decision.decision) : "")],
        ["생성 근거(이벤트)", refList(p.context_used?.evidence_event_ids, lab.onRef)],
        ["생성 근거(메모리)", refList(p.context_used?.memory_ids, lab.onRef)],
        ["사용한 지식", refList(p.knowledge_ids, lab.onRef)],
        ["채운 슬롯", Object.entries(p.context_used?.slots ?? {}).map(([k, v]) => `${k}=${v}`).join(" · ")],
      ]),
      h("h4", {}, "개인화에 사용한 사용자 모델 속성"),
      simpleTable(["속성", "값", "신뢰도", "효과"], p.personalization.map((x) => h("tr", {},
        h("td", {}, refChip(`um:${x.attribute}`, lab.onRef)), h("td", {}, umValue(x.value)), h("td", {}, fmtNum(x.confidence)), h("td", { class: "small" }, x.effect)))),
      h("h4", {}, "대안 후보 (생성되었지만 선택되지 않은 문구 포함)"),
      simpleTable(["후보", "문구", "유형", "길이", "점수", "점수 구성", "선택", "선택되지 않은 이유"], p.alternatives.map((c) => h("tr", { class: c.selected ? "sel" : "" },
        h("td", {}, h("code", {}, c.candidate_id)), h("td", { lang: p.language }, `“${c.text}”`), h("td", {}, promptTypeLabel(c.prompt_type)),
        h("td", {}, label(P.lengths, c.length)), h("td", { class: "num" }, fmtNum(c.score)),
        h("td", { class: "small" }, Object.entries(c.score_parts).map(([k, v]) => `${k} ${fmtNum(v)}`).join(" · ")),
        h("td", {}, c.selected ? "✓" : ""), h("td", { class: "small" }, c.not_selected_reason ?? "")))),
      h("h4", {}, "개입 후 관찰된 결과"),
      p.delivered ? kv([
        ["결과", h("span", {}, badge(label(P.outcomes, p.outcome), p.outcome === "recovered" ? "ok" : p.outcome === "pending" ? "muted" : "warn"),
          later ? h("small", { class: "muted" }, " (재생된 마지막 단계 기준)") : null)],
        ["판정 규칙", p.outcome_detail?.rule ?? "–"],
        ["판정 사유", p.outcome_detail?.reason ?? p.outcome_detail?.partial ?? "–"],
        ["관찰한 이벤트", refList(p.outcome_detail?.events_observed, lab.onRef)],
        ["주의 분산 가능성", p.outcome_detail?.possible_disruption ? "있음(직후 침묵·군말 증가)" : "관찰되지 않음"],
      ]) : empty("전달되지 않았으므로 결과를 추정하지 않습니다."),
      devJson(p)),
  );
}

