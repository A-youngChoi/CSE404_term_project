// Small line chart over simulation steps with a hover readout. Exact values are always in a table nearby.
import { h, s, fmtNum } from "../dom.js";

// Categorical slots in fixed order (validated reference palette, light mode).
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];

/**
 * @param series [{name, points: [{x, y, note}]}] - x is the step number
 * @param opts {title, yMin, yMax, xMax, selected, digits}
 */
export function lineChart(series, { title, yMin = 0, yMax = 1, xMax, selected, digits = 2 } = {}) {
  const W = 520;
  const H = 170;
  const pad = { l: 44, r: 12, t: 10, b: 24 };
  const maxX = Math.max(1, xMax ?? Math.max(...series.flatMap((sr) => sr.points.map((p) => p.x)), 1));
  const x = (v) => pad.l + (v / maxX) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - yMin) / ((yMax - yMin) || 1)) * (H - pad.t - pad.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart line-chart", role: "img", "aria-label": title ?? "" });
  for (let k = 0; k <= 4; k += 1) {
    const v = yMin + ((yMax - yMin) * k) / 4;
    svg.append(s("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "grid" }));
    svg.append(s("text", { x: pad.l - 6, y: y(v) + 3, class: "axis", "text-anchor": "end" }, fmtNum(v, yMax - yMin >= 10 ? 0 : 2)));
  }
  for (let t = 0; t <= maxX; t += Math.max(1, Math.ceil(maxX / 10))) {
    svg.append(s("text", { x: x(t), y: H - 6, class: "axis", "text-anchor": "middle" }, String(t)));
  }
  if (selected) svg.append(s("line", { x1: x(selected), x2: x(selected), y1: pad.t, y2: H - pad.b, class: "cursor" }));
  const readout = h("div", { class: "chart-readout", "aria-live": "polite" }, " ");
  series.forEach((sr, i) => {
    const color = SERIES[i % SERIES.length];
    if (!sr.points.length) return;
    const pts = sr.points.map((p) => `${x(p.x)},${y(p.y)}`).join(" ");
    svg.append(s("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "stroke-dasharray": sr.dashed ? "5 4" : null }));
    for (const p of sr.points) {
      const dot = s("circle", { cx: x(p.x), cy: y(p.y), r: 4, fill: color, class: "pt" });
      const text = `${sr.name} · ${p.x}단계: ${fmtNum(p.y, digits)}${p.note ? ` — ${p.note}` : ""}`;
      dot.append(s("title", {}, text));
      // Larger invisible hit target than the mark.
      const hit = s("circle", { cx: x(p.x), cy: y(p.y), r: 10, class: "pt-hit" });
      hit.addEventListener("mouseenter", () => { readout.textContent = text; });
      svg.append(dot, hit);
    }
  });
  const legend = series.length > 1 ? h("ul", { class: "legend" },
    series.map((sr, i) => h("li", {}, h("span", { class: "swatch", style: `background:${SERIES[i % SERIES.length]}` }), sr.name))) : null;
  return h("figure", { class: "chart-wrap" }, title ? h("figcaption", { class: "chart-title" }, title) : null, svg, legend, readout);
}
