import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ko } from "../src/i18n/ko.js";
import { ROUTES, parseHash } from "../src/main.js";
import { devJson, modeBadge } from "../src/components.js";
import { overviewView } from "../src/pages/overview.js";
import { paperDetailView } from "../src/pages/papers.js";
import { audienceView, beliefCard, historyTable, renderAudience } from "../src/pages/audience.js";
import { STAGE_ORDER, mergeStages, traceView } from "../src/pages/trace.js";
import { cueDetailView } from "../src/pages/cues.js";
import { createSessionForm, cueResultView, deleteSessionFlow } from "../src/pages/session.js";
import { guideView } from "../src/pages/guide.js";
import { explainBeliefChange, explainEvidence } from "../src/explain.js";
import { fixture, mockFetch, textOf } from "./helpers.js";

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  document.querySelectorAll(".modal-backdrop").forEach((n) => n.remove());
});

describe("Korean labels", () => {
  it("renders the system overview with Korean labels", () => {
    const text = textOf(overviewView(fixture("status")));
    for (const label of ["시스템 상태", "로컬 LLM", "임베딩 모델", "데이터베이스", "로컬 전용 실행", "보관 기간",
      "디버그 기록", "활성 세션", "외부 전송 없음", "최근 로컬 처리 오류"]) {
      expect(text).toContain(label);
    }
    expect(text).toContain("localhost에만 바인딩됨");
  });

  it("has Korean navigation for every page", () => {
    const names = ROUTES.map((r) => ko.nav[r.key]);
    expect(names).toEqual(["시스템 개요", "논문 지식 구조", "세션 작업 공간", "진화하는 청중 모델", "턴별 처리 과정", "단서 생성 분석", "시스템 동작 이해하기"]);
    expect(parseHash("#/trace/abc/def")).toMatchObject({ route: { key: "trace" }, params: ["abc", "def"] });
  });

  it("shows Korean unit-type filters and prohibited claims on the paper page", () => {
    const view = paperDetailView(fixture("paper"), { setFilter() {}, onReindex() {}, onSaved() {} });
    const text = textOf(view);
    for (const t of ["문제", "동기", "연구 공백", "핵심 아이디어", "방법", "근거", "결과", "기여", "적용", "한계", "예시", "연결점"]) {
      expect(text).toContain(t);
    }
    expect(text).toContain("로컬 인덱스 다시 생성");
    expect(text).toContain("금지된 주장");
    expect(text).toContain("발표자 제공");
  });

  it("warns in Korean when debug prompts or remote endpoints are configured", () => {
    const status = { ...fixture("status"), warnings: ["debug_prompts_enabled", "non_local_bind", "remote_model_endpoint", "ollama_unavailable", "sensitive_files_tracked"] };
    const text = textOf(overviewView(status));
    expect(text).toContain("디버그 프롬프트 저장이 켜져 있습니다");
    expect(text).toContain("localhost가 아닌 주소");
    expect(text).toContain("원격 모델 주소");
    expect(text).toContain("Ollama에 연결할 수 없습니다");
    expect(text).toContain("Git에 추적");
  });

  it("guide explains modules, concepts and the privacy walkthrough", () => {
    const text = textOf(guideView({ setSession() {} }));
    for (const t of ["근거(evidence)", "믿음(belief)", "대화 상태", "검색된 논문 단위", "단서 행동", "생성된 단서",
      "개인정보 먼저—로컬 처리", "이 청중은 개인정보 기술에 부정적이다", "감지(sense)", "Memoro"]) {
      expect(text).toContain(t);
    }
  });
});

describe("turn trace", () => {
  it("displays all twelve stages in order with Korean names", () => {
    const view = fixture("turn_trace");
    const root = traceView(view, null, () => {});
    const stages = [...root.querySelectorAll(".stage")];
    expect(stages.map((s) => s.dataset.stage)).toEqual(STAGE_ORDER);
    const text = textOf(root);
    for (const name of ["입력 발화", "최근 대화 맥락", "대화 행위 분석", "추출된 근거", "청중 모델 변경 제안",
      "검증 후 적용된 변경", "검색 질의", "검색된 논문 단위", "개입 판단", "생성된 단서 후보", "안전성·근거성 검사", "최종 결과"]) {
      expect(text).toContain(name);
    }
    expect(text).toContain("프롬프트 버전");
    expect(text).toContain("evidence_extraction-v1");
    expect(text).toContain("현재 우려 사항으로 추가되었습니다");
    expect(text).toContain("프로필이 아니라 3번 청중 발화에 근거합니다");
  });

  it("uses the latest cue run for stages 7-12 and the turn run for 1-6", () => {
    const view = fixture("turn_trace");
    const merged = mergeStages(view, null);
    expect(merged.slice(0, 6).every((m) => m.source.kind === "turn")).toBe(true);
    expect(merged.slice(6).every((m) => m.source.kind === "cue")).toBe(true);
    const first = view.traces.find((t) => t.kind === "cue");
    const pick = mergeStages(view, first.trace_id);
    expect(pick[11].source.trace_id).toBe(first.trace_id);
    expect(pick[11].stage.output.final_cue).toBe("개인정보 먼저—로컬 처리");
  });

  it("marks stages missing from a run as not executed", () => {
    const view = fixture("turn_trace");
    const broken = { ...view, traces: [{ ...view.traces[0], stages: view.traces[0].stages.slice(0, 3) }] };
    const root = traceView(broken, null, () => {});
    expect(root.querySelectorAll(".stage-missing").length).toBe(9);
    expect(textOf(root)).toContain("실행되지 않음");
  });
});

describe("audience model", () => {
  const audience = fixture("audience");
  const all = Object.values(audience.dimensions).flat();

  it("distinguishes profile hypotheses from conversation evidence visually and textually", () => {
    const profile = all.find((b) => b.lifecycle === "profile_hypothesis");
    const explicit = all.find((b) => b.lifecycle === "explicitly_confirmed");
    const behavioral = all.find((b) => b.lifecycle === "conversation_supported");
    const p = beliefCard(profile);
    const e = beliefCard(explicit);
    const b = beliefCard(behavioral);
    expect(p.className).toContain("life-profile_hypothesis");
    expect(e.className).toContain("life-explicitly_confirmed");
    expect(b.className).toContain("life-conversation_supported");
    expect(textOf(p)).toContain("프로필 가설");
    expect(textOf(e)).toContain("명시적 확인");
    expect(textOf(b)).toContain("대화 근거");
  });

  it("shows the previous and current state of an overridden belief", () => {
    const history = fixture("history");
    const overridden = all.find((b) => b.key === "language models");
    const card = beliefCard(overridden, { history });
    const text = textOf(card);
    expect(text).toContain("이전 상태");
    expect(text).toContain("현재 상태");
    expect(card.querySelector(".state-change s").textContent).toBe("익숙함");
    expect(text).toContain("프로필에서 시작");
  });

  it("belief history table shows old and new confidence values", () => {
    const table = historyTable(fixture("history"));
    const row = table.querySelector('tr[data-change-type="overridden"]');
    expect(row.querySelector(".old-conf").textContent).toBe("0.55");
    expect(row.querySelector(".new-conf").textContent).toBe("0.90");
    expect(textOf(row)).toContain("무효화·대체");
    const created = table.querySelector('tr[data-change-type="created"] .old-conf');
    expect(created.textContent).toBe("–");
  });

  it("renders every Korean dimension, including uncertainty, and a chart with a numeric table", () => {
    const root = audienceView(audience, fixture("history"), fixture("evidence"), fixture("turns"));
    const text = textOf(root);
    for (const d of ["지식", "친숙도", "현재 관심사", "대화 목적", "연구 연결점", "우려 사항", "선호 설명 수준", "참여도", "해결되지 않은 질문", "불확실성"]) {
      expect(text).toContain(d);
    }
    expect(root.querySelector("svg.chart")).not.toBeNull();
    expect(root.querySelector("#belief-history")).not.toBeNull();
  });

  it("explains changes without claiming personal traits", () => {
    const change = { dimension: "concern", key: "privacy", change_type: "created", old_confidence: null,
      new_confidence: 0.9, source_type: "conversation", evidence_type: "explicit", new_value: "raised" };
    expect(explainBeliefChange(change, 4)).toBe(
      "청중이 직접 질문했기 때문에 ‘개인정보 보호’가 현재 우려 사항으로 추가되었습니다(신뢰도 없음 → 0.90). 이 판단은 프로필이 아니라 4번 청중 발화에 근거합니다.",
    );
    const ev = explainEvidence({ dimension: "concern", key: "privacy", value: "raised", evidence_type: "explicit" });
    expect(ev).toContain("명시적 대화 근거");
    expect(ev).not.toMatch(/부정적|회의적/);
  });

  it("shows a not-found state for a deleted session", async () => {
    mockFetch({});
    const root = document.createElement("div");
    await renderAudience(root, ["deleted-session"], { sessionId: null });
    expect(textOf(root)).toContain(ko.common.sessionNotFound);
  });
});

describe("cue inspector", () => {
  const cues = fixture("cues");
  const paper = fixture("paper");
  const unitsById = Object.fromEntries(paper.units.map((u) => [u.id, u]));

  it("shows action, scores, candidate, checks and final cue for a delivered cue", () => {
    const view = cueDetailView(cues[0], { unitsById });
    const text = textOf(view);
    expect(text).toContain("전달 가능");
    expect(text).toContain("우려 사항에 직접 대응");
    expect(text).toContain("“개인정보 먼저—로컬 처리”");
    expect(text).toContain("로컬 처리 및 데이터 저장 정책");
    expect(text).toContain("4단어");
    expect(text).toContain("반복 검사");
    expect(text).toContain("민감 추론 검사");
    expect(text).toContain("근거 없는 주장 검사");
    expect(view.querySelectorAll("#score-table tbody tr").length).toBe(8);
  });

  it("explains a duplicate no-cue decision in Korean", () => {
    const text = textOf(cueDetailView(cues[1], { unitsById }));
    expect(text).toContain("최근 단서와 중복");
    expect(text).toContain("전달된 단서 없음");
    expect(text).toContain("반복을 피하기 위해 전달하지 않았습니다");
  });

  it("explains missing grounding and not-needed decisions in Korean", () => {
    const noGrounding = { ...cues[1], final_status: "insufficient_grounding" };
    expect(textOf(cueDetailView(noGrounding))).toContain(
      "현재 질문과 직접 연결되는 논문 근거를 찾지 못해 단서를 생성하지 않았습니다.",
    );
    const notNeeded = { ...cues[1], final_status: "no_cue_needed", generated: null, should_intervene: false };
    const text = textOf(cueDetailView(notNeeded));
    expect(text).toContain("단서 불필요");
    expect(text).toContain("지금은 단서가 필요하지 않다고 판단했습니다");
  });
});

describe("developer JSON and mock marking", () => {
  it("keeps developer JSON hidden until explicitly toggled", () => {
    const wrap = devJson({ secret_like: "value" });
    const pre = wrap.querySelector("pre");
    const btn = wrap.querySelector("button");
    expect(pre.hidden).toBe(true);
    expect(pre.textContent).toBe("");
    expect(btn.textContent).toBe("개발자용 JSON 보기");
    btn.click();
    expect(pre.hidden).toBe(false);
    expect(pre.textContent).toContain("secret_like");
    btn.click();
    expect(pre.hidden).toBe(true);
  });

  it("hides developer JSON by default on the trace page", () => {
    const root = traceView(fixture("turn_trace"), null, () => {});
    const pres = [...root.querySelectorAll("pre.devjson")];
    expect(pres.length).toBeGreaterThan(0);
    expect(pres.every((p) => p.hidden)).toBe(true);
  });

  it("clearly marks mock mode as simulated", () => {
    expect(modeBadge(true).textContent).toBe("시뮬레이션(Mock)");
    expect(modeBadge(false, "ollama:qwen").textContent).toContain("로컬 LLM(Ollama)");
    const result = { mock_mode: true, delivered: true, evaluation_only: false, cue: "x", status: "deliverable",
      decision: { id: "d", action: "verify", reason_code: "no_clear_need", reason_params: {}, scores: { total: 0.5 } } };
    expect(textOf(cueResultView("s", result))).toContain("실제 언어 모델의 출력이 아닙니다");
    expect(textOf(traceView(fixture("turn_trace"), null, () => {}))).toContain("Mock 규칙(시뮬레이션)");
  });

  it("renders untrusted text as text, never as markup", () => {
    const view = fixture("turn_trace");
    view.turn = { ...view.turn, text: '<img src=x onerror="alert(1)">이 대화가 서버에 저장되나요?' };
    const root = traceView(view, null, () => {});
    expect(root.querySelector("img")).toBeNull();
    expect(textOf(root)).toContain("<img src=x");
  });
});

describe("session safety", () => {
  it("requires consent before a session can be created", () => {
    const form = createSessionForm([{ id: "p1", title: "Paper" }], () => {});
    const submit = form.querySelector('button[type="submit"]');
    expect(submit.disabled).toBe(true);
    const consent = form.querySelector("#new-session-consent");
    consent.checked = true;
    consent.dispatchEvent(new Event("change"));
    expect(submit.disabled).toBe(false);
    expect(textOf(form)).toContain("동의 확인");
    expect(form.querySelector("#new-session-provider").textContent).toContain("Mock(명시적 시뮬레이션)");
  });

  it("does not delete a session when the confirmation is cancelled", async () => {
    const calls = mockFetch({ "DELETE /sessions/s1": { deleted: true } });
    const pending = deleteSessionFlow("s1", {});
    const modal = document.querySelector(".modal");
    expect(textOf(modal)).toContain("세션을 영구 삭제합니다");
    [...modal.querySelectorAll("button")].find((b) => b.textContent === "취소").click();
    expect(await pending).toBe(false);
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("enables permanent deletion only after the checkbox and the typed Korean word", async () => {
    const calls = mockFetch({ "DELETE /sessions/s1": { deleted: true } });
    let deleted = false;
    const pending = deleteSessionFlow("s1", { onDeleted: () => { deleted = true; } });
    const modal = document.querySelector(".modal");
    const confirm = [...modal.querySelectorAll("button")].find((b) => b.textContent === "영구 삭제");
    const check = modal.querySelector("#confirm-check");
    const word = modal.querySelector("#confirm-word");
    expect(confirm.disabled).toBe(true);
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    expect(confirm.disabled).toBe(true);
    word.value = "삭 제";
    word.dispatchEvent(new Event("input"));
    expect(confirm.disabled).toBe(true);
    word.value = "삭제";
    word.dispatchEvent(new Event("input"));
    expect(confirm.disabled).toBe(false);
    confirm.click();
    expect(await pending).toBe(true);
    expect(deleted).toBe(true);
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/sessions/s1"]);
  });
});
