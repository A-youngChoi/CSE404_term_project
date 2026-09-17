import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ko } from "../src/i18n/ko.js";
import { ROUTES, parseHash } from "../src/main.js";
import { explainJudge, refKind } from "../src/lab/common.js";
import { LANES, timelineView } from "../src/lab/timeline.js";
import { mobileCard } from "../src/lab/mobileCard.js";
import { TABS, labView, renderLab } from "../src/pages/lab.js";
import { presentationOverview } from "../src/pages/presentation.js";
import { evaluationView, rowsTable } from "../src/pages/evaluation.js";
import { interpolateRemaining } from "../src/pages/mobile.js";
import { fixture, mockFetch, textOf } from "./helpers.js";

const run = fixture("pres_run");
const config = fixture("pres_config");
const chunks = Object.fromEntries(fixture("pres_knowledge")[0].chunks.map((c) => [c.chunk_id, c]));
const P = ko.pres;

function makeLab(stepNo, tab = "monitor", extra = {}) {
  const refs = [];
  const lab = {
    view: run, steps: run.steps, step: run.steps.find((x) => x.step === stepNo) ?? null, follow: false, playing: false,
    speed: 10, error: null, exportRows: null, config, chunks,
    ui: { tab, memoryType: null, memory: null, attr: null, chunk: null, prompt: null, ...extra },
    setTab: (t) => { lab.ui.tab = t; }, setUi: (patch) => Object.assign(lab.ui, patch),
    selectStep: (n) => refs.push(["step", n]), onRef: (r) => refs.push(["ref", r]),
  };
  return { lab, refs };
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("presentation routes", () => {
  it("adds the simulation pages and a bare, hidden mobile route", () => {
    const mobile = ROUTES.find((r) => r.key === "mobile");
    expect(mobile.bare && mobile.hidden).toBe(true);
    expect(parseHash("#/lab/S04_ko_too_fast/play")).toMatchObject({ route: { key: "lab" }, params: ["S04_ko_too_fast", "play"] });
    expect(ko.nav.lab).toBe("발표 시뮬레이션 실험실");
  });
});

describe("session overview", () => {
  it("lists every synthetic session with Korean labels, metrics and replay buttons", () => {
    const view = presentationOverview(fixture("pres_sessions"), fixture("pres_eval"));
    const rows = view.querySelectorAll("#pres-sessions tbody tr");
    expect(rows.length).toBe(fixture("pres_sessions").length);
    const text = textOf(view);
    for (const t of ["발표 이벤트 입력", "LLM Judge 개입 판단", "설명 누락", "추가 개입 억제", "후반 시간 부족", "경계 사례", "판단 정확도", "합성 더미데이터"]) {
      expect(text).toContain(t);
    }
    const s04 = view.querySelector('tr[data-session="S04_ko_too_fast"]');
    expect([...s04.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["#/lab/S04_ko_too_fast/play", "#/lab/S04_ko_too_fast"]);
  });
});

describe("simulation lab", () => {
  it("renders every tab for every step without errors", () => {
    for (const st of [null, ...run.steps]) {
      for (const tab of TABS) {
        const { lab } = makeLab(st?.step ?? 0, tab);
        const root = labView(lab);
        expect(root.querySelector(`#panel-${tab}`)).not.toBeNull();
      }
    }
  });

  it("shows the event → context → memory → user model → RAG → Judge → prompt flow for the selected step", () => {
    const { lab } = makeLab(2);
    const flow = labView(lab).querySelector(".pipeline-flow");
    expect([...flow.querySelectorAll(".flow-box")].map((b) => b.dataset.flow)).toEqual(
      ["event", "context", "memory", "usermodel", "rag", "judge", "prompt", "outcome"]);
    expect(textOf(flow)).toContain("즉시 개입");
    expect(textOf(flow)).toContain("전달: “");
  });

  it("explains a suppressed decision with cooldown evidence and the ground-truth comparison", () => {
    const { lab, refs } = makeLab(3, "judge");
    const root = labView(lab);
    const text = textOf(root);
    expect(text).toContain("최근 개입으로 억제");
    expect(text).toContain("쿨다운");
    expect(text).toContain("핵심 내용 누락");
    expect(text).toContain("정답과 일치");
    expect(text).toContain("연구자 전용");
    expect(text).toContain("시스템 기록(영문)");
    expect(root.querySelectorAll("#judge-scores tbody tr").length).toBe(8);
    const refBtn = root.querySelector('#judge-scores button.ref[data-ref^="um:"]');
    refBtn.click();
    expect(refs[0][0]).toBe("ref");
    expect(refs[0][1].startsWith("um:")).toBe(true);
  });

  it("builds Korean decision explanations from structured fields only", () => {
    const d = run.steps[1].decision;
    expect(explainJudge(d, config.judge_thresholds)).toContain("효용");
    expect(explainJudge({ ...d, decision: "WAIT_AND_OBSERVE", utility: 0.2 }, config.judge_thresholds)).toContain("지켜봅니다");
    expect(explainJudge({ detected_issue: "none", decision: "DO_NOT_INTERVENE" })).toContain("활성 문제가 없어");
    expect(refKind("S04_e03")).toBe("event");
    expect(refKind("mem_0003")).toBe("memory");
    expect(refKind("mm_kp_sm_3a")).toBe("knowledge");
  });

  it("memory inspector shows metadata, before/after diffs and retrieval scores", () => {
    const step = run.steps.find((x) => x.memory_changes.some((c) => c.op === "reinforced"));
    const { lab } = makeLab(step.step, "memory");
    const text = textOf(labView(lab));
    for (const t of ["중요도", "최신성", "신뢰도", "검색 횟수", "사용한 판단", "생성·수정 이유", "강화", "이전", "이후", "작업 기억", "개입 기억"]) {
      expect(text).toContain(t);
    }
    const withRetrieval = run.steps.find((x) => x.memory_retrieval);
    const r = labView(makeLab(withRetrieval.step, "memory").lab);
    expect(textOf(r)).toContain("관련도×0.5");
  });

  it("user model inspector explains why an attribute has its value and charts its change", () => {
    const { lab } = makeLab(run.steps.length, "usermodel", { attr: "prompt_responsiveness" });
    const root = labView(lab);
    const text = textOf(root);
    expect(text).toContain("‘프롬프트 반응성(효과 추정)’이(가) 현재 값이 된 이유");
    expect(text).toContain("Beta(1,1)");
    expect(text).toContain("프로필 가설");
    expect(root.querySelectorAll("svg.line-chart").length).toBeGreaterThanOrEqual(3);
  });

  it("RAG inspector separates used and unused chunks and shows what reached the prompt", () => {
    const { lab } = makeLab(4, "rag");
    const root = labView(lab);
    const rows = [...root.querySelectorAll("#rag-hits tbody tr")];
    expect(rows.some((r) => textOf(r).includes("사용"))).toBe(true);
    expect(rows.some((r) => !r.classList.contains("sel"))).toBe(true);
    expect(textOf(root)).toContain("반영");
    expect(textOf(root)).toContain("반드시 언급할 내용");
  });

  it("prompt inspector lists alternatives and why a suppressed prompt was not delivered", () => {
    const suppressed = run.prompts.find((p) => !p.delivered);
    const { lab } = makeLab(3, "prompts", { prompt: suppressed.prompt_id });
    const text = textOf(labView(lab));
    expect(text).toContain("전달하지 않은 이유");
    expect(text).toContain("선택되지 않은 이유");
    expect(text).toContain("개인화에 사용한 사용자 모델 속성");
    const delivered = run.prompts.find((p) => p.delivered);
    const t2 = textOf(labView(makeLab(run.steps.length, "prompts", { prompt: delivered.prompt_id }).lab));
    expect(t2).toContain("개입 후 관찰된 결과");
    expect(t2).toMatch(/회복|부분 회복/);
  });

  it("timeline has Korean lanes, one Judge mark per executed step and clickable columns", () => {
    const picked = [];
    const root = timelineView({ events: run.event_index, steps: run.steps.slice(0, 3), truth: run.ground_truth,
      prompts: run.prompts, selected: 2, onSelect: (n) => picked.push(n) });
    const text = textOf(root);
    for (const lane of LANES) expect(text).toContain(P.lanes[lane]);
    expect(root.querySelectorAll(".dec-mark").length).toBe(3);
    expect(root.querySelectorAll(".tl-hit.pending").length).toBe(run.event_index.length - 3);
    root.querySelectorAll(".tl-hit")[4].dispatchEvent(new Event("click"));
    expect(picked).toEqual([5]);
    expect(root.querySelector(".cursor")).not.toBeNull();
  });

  it("renders untrusted transcript text as text", () => {
    const steps = structuredClone(run.steps);
    steps[0].context.recent_transcript[0].text = '<img src=x onerror="alert(1)">';
    const { lab } = makeLab(1);
    lab.steps = steps;
    lab.step = steps[0];
    const root = labView(lab);
    expect(root.querySelector("img")).toBeNull();
    expect(textOf(root)).toContain("<img src=x");
  });

  it("drives the backend through step and playback requests", async () => {
    const empty = { ...run, steps: [], prompts: [], summary: { ...run.summary, cursor: 0, finished: false }, evaluation: null };
    const rid = run.summary.run_id;
    const calls = mockFetch({
      "POST /presentation/runs": { __status: 201, __body: empty },
      "GET /presentation/config": config,
      "GET /presentation/knowledge?kb_id=kb_soundmap": fixture("pres_knowledge"),
      [`POST /presentation/runs/${rid}/step`]: { ...run, since: 0, steps: run.steps.slice(0, 1), summary: { ...run.summary, cursor: 1, finished: false } },
      [`POST /presentation/runs/${rid}/playback`]: run.summary,
    });
    const root = document.createElement("div");
    document.body.append(root);
    const lab = await renderLab(root, ["S04_ko_too_fast"]);
    expect(root.querySelector("#lab-progress").textContent).toBe(`0 / ${run.summary.total} 단계`);
    root.querySelector("#lab-next").click();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(root.querySelector("#lab-progress").textContent).toBe(`1 / ${run.summary.total} 단계`);
    expect(lab.steps.length).toBe(1);
    root.querySelector("#lab-play").click();
    await new Promise((r) => setTimeout(r, 0));
    expect(root.querySelector("#lab-play").textContent).toContain("일시정지");
    root.querySelector("#lab-play").click();
    await new Promise((r) => setTimeout(r, 0));
    const paths = calls.map((c) => `${c.method} ${c.path}`);
    expect(paths).toContain(`POST /presentation/runs/${rid}/step`);
    expect(paths.filter((p) => p.endsWith("/playback")).length).toBe(2);
    root.remove();
  });
});

describe("deep link", () => {
  it("opens a run at a given step and tab", async () => {
    const rid = run.summary.run_id;
    const calls = mockFetch({
      "POST /presentation/runs": { __status: 201, __body: { ...run, steps: [], summary: { ...run.summary, cursor: 0 } } },
      "GET /presentation/config": config,
      "GET /presentation/knowledge?kb_id=kb_soundmap": fixture("pres_knowledge"),
      [`POST /presentation/runs/${rid}/seek`]: (body) => ({ ...run, steps: run.steps.slice(0, body.index),
        summary: { ...run.summary, cursor: body.index } }),
    });
    const root = document.createElement("div");
    document.body.append(root);
    const lab = await renderLab(root, ["S04_ko_too_fast", "3", "judge"]);
    expect(lab.steps.length).toBe(3);
    expect(root.querySelector("#panel-judge")).not.toBeNull();
    expect(textOf(root)).toContain("최근 개입으로 억제");
    expect(calls.some((c) => c.path.endsWith("/seek"))).toBe(true);
    root.remove();
  });
});

describe("autoplay", () => {
  afterEach(() => vi.useRealTimers());

  it("keeps stepping on a timer scaled by the playback speed until the session ends", async () => {
    vi.useFakeTimers();
    const rid = run.summary.run_id;
    const total = run.summary.total;
    let cursor = 0;
    const view = (since, steps) => ({ ...run, since, steps, summary: { ...run.summary, cursor, finished: cursor >= total } });
    const calls = mockFetch({
      "POST /presentation/runs": { __status: 201, __body: { ...view(0, []), prompts: [], evaluation: null } },
      "GET /presentation/config": config,
      "GET /presentation/knowledge?kb_id=kb_soundmap": fixture("pres_knowledge"),
      [`POST /presentation/runs/${rid}/step`]: () => { cursor += 1; return view(cursor - 1, run.steps.slice(cursor - 1, cursor)); },
      [`POST /presentation/runs/${rid}/playback`]: run.summary,
    });
    const root = document.createElement("div");
    document.body.append(root);
    const lab = await renderLab(root, ["S04_ko_too_fast", "play"]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cursor).toBe(total);
    expect(lab.steps.length).toBe(total);
    expect(lab.playing).toBe(false);
    expect(calls.filter((c) => c.path.endsWith("/step")).length).toBe(total);
    expect(root.querySelector("#lab-progress").textContent).toBe(`${total} / ${total} 단계`);
    root.remove();
  });
});

describe("mobile view", () => {
  it("shows only the prompt, its importance and a countdown", () => {
    const st = run.steps.find((x) => x.mobile.text).mobile;
    const card = mobileCard(st);
    expect(card.querySelector(".phone-text").textContent).toBe(st.text);
    expect(textOf(card)).toContain("중요도");
    expect(textOf(card)).toContain("초 후 사라짐");
    expect(card.querySelectorAll("button, input").length).toBe(0);
    expect(card.dataset.priority).toBe(st.priority);
  });

  it("uses English chrome for English talks and shows idle states", () => {
    const en = mobileCard({ run_id: "r", text: "Missed: baseline", language: "en", priority: "high", display_seconds: 10, remaining_seconds: 4 });
    expect(textOf(en)).toContain("Priority: High");
    expect(textOf(en)).toContain("hides in 4s");
    expect(textOf(mobileCard({ run_id: "r", text: null, language: "ko" }))).toContain("대기 중");
    expect(textOf(mobileCard({ run_id: null }))).toContain("실행 중인 시뮬레이션이 없습니다");
    expect(textOf(mobileCard({ run_id: "r", text: "x", priority: "low", remaining_seconds: 5 }, { remaining: 0 }))).toContain("대기 중");
  });

  it("advances the countdown locally only while playback runs", () => {
    const state = { remaining_seconds: 10, playing: true, speed: 5 };
    expect(interpolateRemaining(state, 0, 1000)).toBe(5);
    expect(interpolateRemaining({ ...state, playing: false }, 0, 1000)).toBe(10);
    expect(interpolateRemaining(state, 0, 5000)).toBe(0);
    expect(interpolateRemaining({ remaining_seconds: null }, 0, 1)).toBeUndefined();
  });
});

describe("evaluation view", () => {
  const ev = fixture("pres_eval");

  it("shows metrics, language and prompt-type slices, and the calculation method", () => {
    const root = evaluationView(ev);
    const text = textOf(root);
    for (const t of ["개입 정밀도(precision)", "개입 재현율(recall)", "F1 점수", "불필요한 끼어들기 비율", "중요 개입 누락 비율",
      "평균 판단 지연", "개입 후 회복률", "언어별 성능", "프롬프트 유형별 성능", "계산 방식", "TP / (TP + FP)", "한국어", "영어", "낙관적"]) {
      expect(text).toContain(t);
    }
    expect(root.querySelectorAll("details.session-eval").length).toBe(ev.sessions.length);
  });

  it("highlights false positives and negatives with mismatch notes", () => {
    const rows = ev.sessions.flatMap((s) => s.rows);
    const table = rowsTable(rows);
    const bad = table.querySelectorAll("tr.match-FP, tr.match-FN");
    expect(bad.length).toBe(ev.overall.counts.fp + ev.overall.counts.fn);
    expect(textOf(bad[0])).toMatch(/expected|Ground truth|System intervened/);
  });
});
