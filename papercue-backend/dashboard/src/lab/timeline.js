// Decision timeline: one swimlane per pipeline layer, one column per event.
import { h, s } from "../dom.js";
import { label } from "../i18n/ko.js";
import { P, decisionLabel, issueLabel, mmss, promptTypeLabel } from "./common.js";

export const LANES = ["slide", "speech", "issue", "memory", "retrieval", "judge", "intervention", "outcome", "gt"];
const W = 1100;
const LEFT = 150;
const RIGHT = 16;
const ROW = 26;
const TOP = 8;

function shape(kind, x, y, cls, title) {
  const g = s("g", { class: `tl-mark ${cls}` });
  if (kind === "circle") g.append(s("circle", { cx: x, cy: y, r: 6 }));
  else if (kind === "ring") g.append(s("circle", { cx: x, cy: y, r: 6, class: "hollow" }));
  else if (kind === "dot") g.append(s("circle", { cx: x, cy: y, r: 3 }));
  else if (kind === "half") {
    g.append(s("circle", { cx: x, cy: y, r: 6, class: "hollow" }));
    g.append(s("path", { d: `M ${x} ${y - 6} A 6 6 0 0 1 ${x} ${y + 6} Z` }));
  } else if (kind === "slash") {
    g.append(s("circle", { cx: x, cy: y, r: 6, class: "hollow" }));
    g.append(s("line", { x1: x - 4, y1: y + 4, x2: x + 4, y2: y - 4, class: "stroke" }));
  } else if (kind === "square") g.append(s("rect", { x: x - 6, y: y - 6, width: 12, height: 12, rx: 2 }));
  else if (kind === "square-hollow") g.append(s("rect", { x: x - 6, y: y - 6, width: 12, height: 12, rx: 2, class: "hollow" }));
  else if (kind === "diamond") g.append(s("path", { d: `M ${x} ${y - 7} L ${x + 7} ${y} L ${x} ${y + 7} L ${x - 7} ${y} Z` }));
  else if (kind === "triangle") g.append(s("path", { d: `M ${x} ${y - 7} L ${x + 7} ${y + 5} L ${x - 7} ${y + 5} Z` }));
  else if (kind === "text") g.append(s("text", { x, y: y + 4, "text-anchor": "middle" }, cls.includes("ok") ? "✓" : cls.includes("bad") ? "✗" : "~"));
  if (title) g.append(s("title", {}, title));
  return g;
}

const DECISION_SHAPE = {
  INTERVENE_NOW: "circle",
  WAIT_AND_OBSERVE: "half",
  DO_NOT_INTERVENE: "dot",
  SUPPRESS_DUE_TO_RECENT_INTERVENTION: "slash",
};

/**
 * @param events  run.event_index (all events of the session, executed or not)
 * @param steps   executed StepRecords
 * @param truth   ground-truth map (researcher-only lane)
 */
export function timelineView({ events, steps, truth = {}, prompts = [], selected, onSelect, showTruth = true }) {
  const lanes = showTruth ? LANES : LANES.filter((l) => l !== "gt");
  const H = TOP + lanes.length * ROW + 24;
  const t0 = Math.min(...events.map((e) => e.elapsed));
  const t1 = Math.max(...events.map((e) => e.elapsed));
  const span = Math.max(1, t1 - t0);
  const x = (t) => LEFT + 10 + ((t - t0) / span) * (W - LEFT - RIGHT - 20);
  const y = (lane) => TOP + lanes.indexOf(lane) * ROW + ROW / 2;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "timeline", role: "img", "aria-label": P.lab.timelineTitle });

  lanes.forEach((lane, i) => {
    svg.append(s("rect", { x: 0, y: TOP + i * ROW, width: W, height: ROW, class: i % 2 ? "lane-bg alt" : "lane-bg" }));
    svg.append(s("text", { x: 8, y: y(lane) + 4, class: "lane-label" }, P.lanes[lane]));
  });

  // executed region and selection cursor
  const executed = steps.length;
  if (executed) {
    const last = steps[executed - 1].elapsed_time;
    svg.append(s("rect", { x: LEFT, y: TOP, width: Math.max(0, x(last) - LEFT + 8), height: lanes.length * ROW, class: "executed" }));
  }

  // slide segments (the slide plan is observable input, so it is shown for the whole session)
  events.forEach((e, i) => {
    const prev = events[i - 1];
    if (prev && prev.slide === e.slide) return;
    const nextChange = events.slice(i + 1).find((n) => n.slide !== e.slide);
    const xe = nextChange ? x(nextChange.elapsed) : W - RIGHT;
    const xs = i === 0 ? LEFT + 2 : x(e.elapsed);
    const g = s("g", { class: `tl-slide ${e.slide % 2 ? "odd" : "even"}` },
      s("rect", { x: xs, y: y("slide") - 8, width: Math.max(2, xe - xs - 2), height: 16, rx: 3 }),
      s("text", { x: xs + 4, y: y("slide") + 4 }, `#${e.slide}`),
      s("title", {}, `슬라이드 ${e.slide} (${mmss(e.elapsed)}부터)`));
    svg.append(g);
  });

  const stepByEvent = Object.fromEntries(steps.map((st) => [st.event_id, st]));
  const promptById = Object.fromEntries(prompts.map((p) => [p.prompt_id, p]));
  events.forEach((e, i) => {
    const cx = x(e.elapsed);
    const st = stepByEvent[e.event_id];
    // hit target for the whole column
    const prevX = i ? x(events[i - 1].elapsed) : LEFT;
    const nextX = i < events.length - 1 ? x(events[i + 1].elapsed) : W - RIGHT;
    const hit = s("rect", {
      x: (prevX + cx) / 2, y: TOP, width: Math.max(6, (nextX - prevX) / 2), height: lanes.length * ROW,
      class: `tl-hit${st && selected === st.step ? " selected" : ""}${st ? "" : " pending"}`,
      dataset: undefined, tabindex: 0, role: "button",
      "aria-label": `${i + 1}단계 ${e.event_id} ${mmss(e.elapsed)}`, "data-index": i,
    });
    hit.addEventListener("click", () => onSelect?.(i + 1));
    hit.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") onSelect?.(i + 1); });
    svg.append(hit);
    if (showTruth) {
      const gt = truth[e.event_id];
      if (gt?.expected_intervention) {
        svg.append(shape("ring", cx, y("gt"), "gt-pos", `정답: ${promptTypeLabel(gt.expected_prompt_type)} (${issueLabel(gt.issue)})`));
      } else if (gt?.expected_decision && gt.expected_decision !== "DO_NOT_INTERVENE") {
        svg.append(shape("dot", cx, y("gt"), "gt-other", `정답 판단: ${decisionLabel(gt.expected_decision)}`));
      }
    }
    if (!st) {
      svg.append(shape("dot", cx, y("speech"), "pending", `${e.event_id} (아직 실행 안 됨)`));
      return;
    }
    const evKind = e.type === "silence" ? "ring" : e.type === "audience_question" ? "diamond" : "circle";
    svg.append(shape(evKind, cx, y("speech"), `ev ev-${e.type}`,
      `${e.event_id} · ${label(P.eventTypes, e.type)} · ${mmss(e.elapsed)}`));
    for (const m of st.timeline) {
      if (m.lane === "issue") svg.append(shape("triangle", cx, y("issue"), "issue", `새 문제: ${issueLabel(m.kind)} (${m.ref})`));
      if (m.lane === "retrieval") svg.append(shape("dot", cx, y("retrieval"), "retr", `지식 검색: ${m.label}`));
      if (m.lane === "memory") {
        const n = st.memory_changes.length;
        const hgt = Math.min(18, 4 + n * 2);
        svg.append(s("g", { class: "tl-mark mem" }, s("rect", { x: cx - 3, y: y("memory") + 9 - hgt, width: 6, height: hgt, rx: 2 }),
          s("title", {}, `메모리 변경 ${n}건: ${m.label}`)));
      }
      if (m.lane === "judge") {
        svg.append(shape(DECISION_SHAPE[m.kind] ?? "dot", cx, y("judge"), `dec-mark dec-${m.kind}`,
          `${decisionLabel(m.kind)} · ${issueLabel(st.decision.detected_issue)} · 효용 ${st.decision.utility.toFixed(2)}`));
      }
      if (m.lane === "intervention") {
        const p = promptById[m.ref];
        svg.append(shape(m.kind === "delivered" ? "square" : "square-hollow", cx, y("intervention"),
          m.kind === "delivered" ? "int-delivered" : "int-suppressed",
          `${m.kind === "delivered" ? "전달" : "억제(미전달)"}: “${m.label}”${p ? ` · ${promptTypeLabel(p.prompt_type)}` : ""}`));
      }
      if (m.lane === "outcome") {
        const cls = m.kind === "recovered" ? "out-ok" : m.kind === "not_recovered" ? "out-bad" : "out-mid";
        svg.append(shape("text", cx, y("outcome"), cls, `${m.ref}: ${label(P.outcomes, m.kind)}`));
      }
    }
    if (selected === st.step) {
      svg.append(s("line", { x1: cx, x2: cx, y1: TOP, y2: TOP + lanes.length * ROW, class: "cursor" }));
    }
  });

  // time axis
  const ticks = 6;
  for (let k = 0; k <= ticks; k += 1) {
    const t = t0 + (span * k) / ticks;
    svg.append(s("text", { x: x(t), y: H - 6, class: "axis", "text-anchor": "middle" }, mmss(t)));
  }
  const legend = h("ul", { class: "legend tl-legend" },
    ["INTERVENE_NOW", "WAIT_AND_OBSERVE", "DO_NOT_INTERVENE", "SUPPRESS_DUE_TO_RECENT_INTERVENTION"].map((d) =>
      h("li", {}, h("span", { class: `swatch-shape dec-${d}` }, P.decisionIcons[d]), decisionLabel(d))),
    h("li", {}, h("span", { class: "swatch-shape int-delivered" }, "■"), "프롬프트 전달"),
    h("li", {}, h("span", { class: "swatch-shape int-suppressed" }, "□"), "억제(미전달)"),
    h("li", {}, h("span", { class: "swatch-shape out-ok" }, "✓"), "회복 / ~ 부분 / ✗ 회복 안 됨"),
    showTruth ? h("li", {}, h("span", { class: "swatch-shape gt-pos" }, "◯"), "정답상 개입 필요") : null);
  return h("figure", { class: "timeline-wrap" }, h("div", { class: "table-wrap" }, svg), legend);
}
