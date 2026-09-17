// Presentation simulation lab: playback controls + event → context → memory/user model → RAG → Judge → prompt.
import { pres } from "../api.js";
import { h, mount } from "../dom.js";
import { label } from "../i18n/ko.js";
import { badge, devJson, errorNotice, loading, notice, sectionTitle } from "../components.js";
import { P, langBadge, mmss, mockBadge, refKind, section } from "../lab/common.js";
import { timelineView } from "../lab/timeline.js";
import { eventsPanel, flowStrip, judgePanel, monitorPanel } from "../lab/panels.js";
import { memoryPanel, promptsPanel, ragPanel, userModelPanel } from "../lab/panels2.js";
import { mobileCard } from "../lab/mobileCard.js";
import { runEvaluationPanel } from "./evaluation.js";

const L = P.lab;
export const TABS = ["monitor", "judge", "memory", "usermodel", "rag", "prompts", "events", "evaluation"];
const PANELS = {
  monitor: monitorPanel, judge: judgePanel, memory: memoryPanel, usermodel: userModelPanel, rag: ragPanel,
  prompts: promptsPanel, events: eventsPanel, evaluation: runEvaluationPanel,
};
export const SPEEDS = [1, 2, 5, 10, 30];

/** Pure view of the lab for a given state (used by tests and by the controller). */
export function labView(lab, actions = {}) {
  const v = lab.view;
  const s = v.summary;
  const meta = v.session;
  const speed = h("select", { id: "lab-speed", "aria-label": L.speed, onchange: (e) => actions.setSpeed?.(Number(e.target.value)) },
    SPEEDS.map((x) => h("option", { value: String(x), selected: x === lab.speed }, `${x}×`)));
  const slider = h("input", { type: "range", id: "lab-seek", min: "0", max: String(s.total), value: String(lab.step?.step ?? s.cursor),
    "aria-label": L.seek, onchange: (e) => actions.seek?.(Number(e.target.value)) });
  const seed = h("input", { type: "number", id: "lab-seed", value: String(s.config.seed), "aria-label": L.seed });
  const judge = h("select", { id: "lab-judge", "aria-label": L.judge },
    ["mock", "ollama"].map((x) => h("option", { value: x, selected: x === s.config.judge_provider }, x === "mock" ? "규칙 기반(Mock)" : "Ollama(로컬 LLM)")));
  const retriever = h("select", { id: "lab-retriever", "aria-label": L.retriever },
    ["keyword", "embedding"].map((x) => h("option", { value: x, selected: x === s.config.retriever }, x === "keyword" ? "키워드" : "임베딩")));
  const follow = h("input", { type: "checkbox", id: "lab-follow", checked: lab.follow, onchange: (e) => actions.setFollow?.(e.target.checked) });
  const btn = (text, fn, attrs = {}) => h("button", { type: "button", class: "btn btn-secondary", onclick: fn, ...attrs }, text);
  const Panel = PANELS[lab.ui.tab] ?? monitorPanel;
  return h("div", { class: "lab" },
    sectionTitle(L.title),
    h("div", { class: "lab-head card" },
      h("div", {},
        h("h3", {}, meta.title_ko, " ", h("small", { class: "muted" }, meta.session_id)),
        h("p", { class: "small" }, meta.description),
        h("p", {}, langBadge(meta.language), " ", badge(label(P.scenarios, meta.scenario_type), "type"), " ",
          badge(`${v.presenter.display_name} · ${label(P.levels, v.presenter.expertise_level)}`, "neutral"), " ",
          mockBadge(s.is_mock), " ", badge(P.synthetic, "mock"), " ",
          badge(`Judge ${s.judge}`, "neutral"), " ", badge(`검색기 ${s.retriever}`, "neutral"), " ",
          badge(`프롬프트 ${s.prompt_generator}`, "neutral"),
          s.fallbacks ? badge(`대체 처리 ${s.fallbacks}회`, "warn") : null,
          s.stage_errors ? badge(`단계 오류 ${s.stage_errors}회`, "bad") : null),
        h("p", { class: "small muted" }, `실행 ID ${s.run_id} · 논문: ${v.knowledge_base.paper_title}`)),
      h("div", { class: "lab-progress" },
        h("strong", { id: "lab-progress" }, L.stepOf(s.cursor, s.total)),
        h("div", { class: "muted" }, lab.step ? L.selected(lab.step.step, `${lab.step.event_id} · ${mmss(lab.step.elapsed_time)}`) : ""))),
    lab.error ? errorNotice(lab.error) : null,
    s.is_mock ? notice(P.mockNotice, "mock") : null,
    h("div", { class: "controls card", role: "toolbar", "aria-label": "재생 제어" },
      btn(`⏮ ${L.first}`, actions.first, { id: "lab-first" }),
      btn(`◀ ${L.prev}`, actions.prev, { id: "lab-prev", disabled: (lab.step?.step ?? 0) <= 1 }),
      h("button", { type: "button", class: "btn", id: "lab-play", onclick: actions.togglePlay, disabled: s.finished },
        lab.playing ? `⏸ ${L.pause}` : `▶ ${L.play}`),
      btn(`${L.next} ▶`, actions.next, { id: "lab-next", disabled: s.finished }),
      btn(`${L.end} ⏭`, actions.end, { id: "lab-end", disabled: s.finished }),
      h("label", { class: "inline" }, `${L.speed} `, speed),
      h("label", { class: "inline seek" }, `${L.seek} `, slider, h("span", { class: "muted" }, `${lab.step?.step ?? s.cursor}`)),
      h("label", { class: "inline" }, follow, L.follow),
      h("span", { class: "spacer" }),
      h("label", { class: "inline" }, `${L.seed} `, seed),
      h("label", { class: "inline" }, `${L.judge} `, judge),
      h("label", { class: "inline" }, `${L.retriever} `, retriever),
      btn(L.rerun, () => actions.rerun?.({ seed: Number(seed.value), judge_provider: judge.value, retriever: retriever.value }), { id: "lab-rerun" }),
      h("a", { href: "#/mobile", target: "_blank", rel: "noopener", class: "btn btn-secondary" }, `📱 ${L.openMobile}`)),
    s.finished ? notice(L.finished, "ok") : null,
    section(L.flowTitle, flowStrip(lab)),
    section(L.timelineTitle, timelineView({
      events: v.event_index, steps: lab.steps, truth: v.ground_truth, prompts: v.prompts,
      selected: lab.step?.step, onSelect: lab.selectStep,
    })),
    h("div", { class: "lab-body" },
      h("div", { class: "lab-main" },
        h("div", { class: "tabs", role: "tablist" }, TABS.map((t) => h("button", {
          type: "button", role: "tab", class: `tab${lab.ui.tab === t ? " active" : ""}`, "aria-selected": String(lab.ui.tab === t),
          dataset: { tab: t }, onclick: () => lab.setTab(t),
        }, L.tabs[t]))),
        h("div", { class: "tab-panel", role: "tabpanel", id: `panel-${lab.ui.tab}` }, Panel(lab))),
      h("aside", { class: "lab-side" },
        section(L.mobilePreview, lab.step ? mobileCard(lab.step.mobile) : mobileCard({ run_id: s.run_id, language: meta.language, is_mock: s.is_mock })),
        section("실행 기록 내보내기", h("p", { class: "small" }, "이벤트별 특징값·시스템 판단·결과·정답 라벨을 한 줄씩 담은 기록입니다(향후 개입 시점 예측기 학습용). 세션이 끝나면 data/presentation_logs/에 JSONL로도 저장됩니다."),
          btn("기록 보기", actions.showExport, { id: "lab-export" }), lab.exportRows ? devJson(lab.exportRows) : null))),
  );
}

export async function renderLab(root, params) {
  const sessionId = params[0];
  if (!sessionId) {
    mount(root, loading());
    const sessions = await pres.sessions();
    mount(root, sectionTitle(L.title), h("p", {}, L.pick),
      h("ul", { class: "list" }, sessions.map((s) => h("li", {},
        h("a", { href: `#/lab/${encodeURIComponent(s.session_id)}` }, `${s.title_ko} (${s.session_id})`), " ", langBadge(s.language)))));
    return;
  }
  mount(root, loading(), h("p", { class: "muted" }, L.creating));
  const lab = {
    view: null, steps: [], step: null, follow: true, playing: false, speed: 10, error: null, exportRows: null,
    ui: { tab: "monitor", memoryType: null, memory: null, attr: null, chunk: null, prompt: null },
    config: null, chunks: {}, runOptions: {},
  };
  let queue = Promise.resolve();
  let timer = null;
  const alive = () => root.isConnected;

  const render = () => {
    if (!alive()) return;
    lab.step = lab.follow || !lab.selected ? lab.steps[lab.steps.length - 1] ?? null
      : lab.steps.find((x) => x.step === lab.selected) ?? lab.steps[lab.steps.length - 1] ?? null;
    const scrollY = typeof window !== "undefined" ? window.scrollY : 0;
    mount(root, labView(lab, actions));
    if (typeof window !== "undefined" && window.scrollTo) {
      try { window.scrollTo(0, scrollY); } catch { /* jsdom */ }
    }
  };
  const applyFull = (view) => {
    lab.view = view;
    lab.steps = view.steps;
    lab.exportRows = null;
  };
  const applyDelta = (view) => {
    const steps = lab.steps.slice(0, view.since).concat(view.steps);
    lab.view = { ...view, steps };
    lab.steps = steps;
  };
  const run = (fn) => {
    queue = queue.then(async () => {
      try {
        lab.error = null;
        await fn();
      } catch (err) {
        if (err?.status === 404 && lab.view) {
          applyFull(await pres.createRun({ session_id: sessionId, ...lab.runOptions }));  // server restarted: recreate
        }
        lab.error = err;
        stopPlaying();
      }
      render();
    });
    return queue;
  };
  const rid = () => lab.view.summary.run_id;
  function stopPlaying() {
    lab.playing = false;
    clearTimeout(timer);
  }
  function schedule() {
    clearTimeout(timer);
    if (!lab.playing || !alive()) return;
    const cur = lab.steps[lab.steps.length - 1];
    const next = lab.view.event_index[lab.steps.length];
    if (!next) {
      stopPlaying();
      run(() => pres.playback(rid(), false, lab.speed));
      return;
    }
    const gap = cur ? next.elapsed - cur.elapsed_time : 1;
    const delay = Math.max(400, Math.min(6000, (gap / lab.speed) * 1000));
    timer = setTimeout(() => {
      if (!alive()) return stopPlaying();
      run(async () => {
        applyDelta(await pres.step(rid(), 1));
        if (lab.view.summary.finished) lab.playing = false;
      }).then(schedule);
    }, delay);
  }

  lab.setTab = (tab) => { lab.ui.tab = tab; render(); };
  lab.setUi = (patch) => { Object.assign(lab.ui, patch); render(); };
  lab.selectStep = (n) => {
    if (!lab.view) return;
    lab.selected = n;
    if (n > lab.view.summary.cursor) {
      lab.follow = true;  // seeking forward executes up to n, so n becomes the latest step
      run(async () => applyFull(await pres.seek(rid(), n)));
    } else {
      lab.follow = n === lab.view.summary.cursor && lab.follow;
      render();
    }
  };
  lab.onRef = (ref) => {
    const kind = refKind(ref);
    if (kind === "event") {
      const st = lab.steps.find((x) => x.event_id === ref);
      if (st) lab.selectStep(st.step);
    } else if (kind === "memory") lab.setUi({ tab: "memory", memory: ref, memoryType: null });
    else if (kind === "usermodel") lab.setUi({ tab: "usermodel", attr: ref.slice(3) });
    else if (kind === "prompt") lab.setUi({ tab: "prompts", prompt: ref });
    else if (kind === "issue") lab.setUi({ tab: "monitor" });
    else lab.setUi({ tab: "rag", chunk: ref.startsWith("kp_") ? `mm_${ref}` : ref });
  };
  const actions = {
    first: () => { stopPlaying(); lab.follow = true; run(async () => applyFull(await pres.seek(rid(), 0))); },
    prev: () => {
      stopPlaying();
      const target = (lab.step?.step ?? 0) - 1;
      if (target >= 1) {
        lab.follow = false;
        lab.selectStep(target);
      }
    },
    next: () => {
      stopPlaying();
      if (lab.step && lab.step.step < lab.view.summary.cursor) {
        lab.selectStep(lab.step.step + 1);
        return;
      }
      lab.follow = true;
      run(async () => applyDelta(await pres.step(rid(), 1)));
    },
    end: () => { stopPlaying(); lab.follow = true; run(async () => applyFull(await pres.complete(rid()))); },
    togglePlay: () => {
      if (lab.playing) {
        stopPlaying();
        run(() => pres.playback(rid(), false, lab.speed));
        return;
      }
      lab.playing = true;
      lab.follow = true;
      run(() => pres.playback(rid(), true, lab.speed)).then(schedule);
    },
    setSpeed: (x) => {
      lab.speed = x;
      if (lab.playing) run(() => pres.playback(rid(), true, x)).then(schedule);
      else render();
    },
    seek: (n) => { stopPlaying(); lab.follow = true; lab.selected = n; run(async () => applyFull(await pres.seek(rid(), n))); },
    setFollow: (v) => { lab.follow = v; render(); },
    rerun: (opts) => {
      stopPlaying();
      lab.runOptions = opts;
      lab.follow = true;
      run(async () => applyFull(await pres.createRun({ session_id: sessionId, ...opts })));
    },
    showExport: () => run(async () => { lab.exportRows = await pres.exportRun(rid()); }),
  };

  try {
    const [view, config] = await Promise.all([pres.createRun({ session_id: sessionId }), pres.config()]);
    applyFull(view);
    lab.config = config;
    const kbs = await pres.knowledge(view.knowledge_base.kb_id);
    lab.chunks = Object.fromEntries((kbs[0]?.chunks ?? []).map((c) => [c.chunk_id, c]));
  } catch (err) {
    mount(root, sectionTitle(L.title), errorNotice(err));
    return;
  }
  if (TABS.includes(params[2])) lab.ui.tab = params[2];
  render();
  if (params[1] === "play") actions.togglePlay();
  else if (/^\d+$/.test(params[1] ?? "")) {
    // Deep link: #/lab/<session>/<step>/<tab> opens the run at that step.
    actions.seek(Number(params[1]));
    await queue;
  }
  return lab;
}
