// Reusable UI pieces: badges, notices, developer JSON toggle, confirmation dialog, confidence chart.
import { h, s, fmtNum, clear } from "./dom.js";
import { ko, label } from "./i18n/ko.js";

export function badge(text, kind = "neutral", title) {
  return h("span", { class: `badge badge-${kind}`, title }, text);
}

export function statusBadge(status) {
  const kind = { success: "ok", failed: "bad", skipped: "muted", partial: "warn" }[status] ?? "neutral";
  return badge(label(ko.stageStatus, status), kind);
}

export function finalStatusBadge(code) {
  const kind = code === "deliverable" ? "ok" : code === "no_cue_needed" ? "muted" : code === "local_model_error" ? "bad" : "warn";
  return badge(label(ko.finalStatus, code), kind);
}

export function lifecycleBadge(lifecycle) {
  return badge(label(ko.lifecycle, lifecycle), `life-${lifecycle}`);
}

export function modeBadge(isMock, modelName) {
  return isMock
    ? badge(ko.common.simulated, "mock", ko.common.simulatedNotice)
    : badge(`${ko.common.realModel}${modelName ? ` · ${modelName}` : ""}`, "real");
}

export function notice(text, kind = "info") {
  return h("div", { class: `notice notice-${kind}`, role: kind === "error" ? "alert" : "status" }, text);
}

export function loading() {
  return h("p", { class: "muted", "aria-busy": "true" }, ko.common.loading);
}

export function empty(text) {
  return h("p", { class: "empty" }, text);
}

export function errorNotice(err) {
  const text = err?.koMessage ?? ko.errors.generic;
  const detail = err?.message && err.code !== "network" ? ` (${err.code}: ${err.message})` : "";
  return notice(text + detail, err?.code === "local_model_unavailable" ? "model" : "error");
}

/** Raw JSON panel, hidden by default behind an explicit toggle. */
export function devJson(data) {
  const pre = h("pre", { class: "devjson", hidden: true });
  const btn = h("button", { type: "button", class: "btn-link", "aria-expanded": "false" }, ko.common.devJson);
  btn.addEventListener("click", () => {
    const show = pre.hidden;
    if (show && !pre.textContent) pre.textContent = JSON.stringify(data, null, 2);
    pre.hidden = !show;
    btn.setAttribute("aria-expanded", String(show));
    btn.textContent = show ? ko.common.hideDevJson : ko.common.devJson;
  });
  return h("div", { class: "devjson-wrap" }, btn, pre);
}

export function kv(rows) {
  return h(
    "dl",
    { class: "kv" },
    rows.filter(Boolean).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "–")]),
  );
}

export function confidenceBar(value) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return h(
    "span",
    { class: "confbar", role: "img", "aria-label": `${ko.common.confidence} ${fmtNum(value)}` },
    h("span", { class: "confbar-fill", style: `width:${pct.toFixed(0)}%` }),
    h("span", { class: "confbar-num" }, fmtNum(value)),
  );
}

/**
 * Explicit, non-deceptive confirmation dialog. For destructive actions pass `typeWord`:
 * the confirm button stays disabled until the checkbox is ticked and the word is typed exactly.
 */
export function confirmDialog({ title, body, confirmLabel, typeWord, danger = false }) {
  return new Promise((resolve) => {
    const dialog = h("div", { class: "modal-backdrop", role: "presentation" });
    const confirmBtn = h("button", { type: "button", class: danger ? "btn btn-danger" : "btn", disabled: true }, confirmLabel);
    const cancelBtn = h("button", { type: "button", class: "btn btn-secondary" }, ko.common.cancel);
    const check = h("input", { type: "checkbox", id: "confirm-check" });
    const input = typeWord ? h("input", { type: "text", id: "confirm-word", autocomplete: "off", "aria-label": ko.session.deleteTypePrompt(typeWord) }) : null;
    const update = () => {
      confirmBtn.disabled = !(check.checked && (!typeWord || input.value.trim() === typeWord));
    };
    check.addEventListener("change", update);
    input?.addEventListener("input", update);
    const close = (result) => {
      dialog.remove();
      resolve(result);
    };
    confirmBtn.addEventListener("click", () => close(true));
    cancelBtn.addEventListener("click", () => close(false));
    const box = h(
      "div",
      { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "confirm-title" },
      h("h3", { id: "confirm-title" }, title),
      h("p", {}, body),
      h("label", { class: "check" }, check, " ", typeWord ? ko.session.deleteCheckbox : "위 내용을 확인했습니다."),
      typeWord ? h("label", { class: "block" }, ko.session.deleteTypePrompt(typeWord), input) : null,
      h("div", { class: "row end" }, cancelBtn, confirmBtn),
    );
    dialog.append(box);
    document.body.append(dialog);
    (input ?? check).focus();
  });
}

/** Line chart of confidence over turn number. Exact values are always shown in a table elsewhere. */
export function confidenceChart(series) {
  const W = 560;
  const H = 200;
  const pad = { l: 36, r: 12, t: 12, b: 28 };
  const maxTurn = Math.max(1, ...series.flatMap((sr) => sr.points.map((p) => p.turn)));
  const x = (t) => pad.l + (t / maxTurn) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - v) * (H - pad.t - pad.b);
  const palette = ["#1f6feb", "#2da44e", "#bf8700", "#8250df", "#cf222e", "#0969da", "#57606a"];
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": ko.audience.chartTitle });
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(s("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "grid" }));
    svg.append(s("text", { x: 4, y: y(v) + 4, class: "axis" }, v.toFixed(2)));
  }
  for (let t = 0; t <= maxTurn; t += Math.max(1, Math.ceil(maxTurn / 10))) {
    svg.append(s("text", { x: x(t) - 3, y: H - 8, class: "axis" }, String(t)));
  }
  series.forEach((sr, i) => {
    const color = palette[i % palette.length];
    const pts = sr.points.map((p) => `${x(p.turn)},${y(p.value)}`).join(" ");
    svg.append(s("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2, "stroke-dasharray": sr.dashed ? "5 4" : null }));
    for (const p of sr.points) svg.append(s("circle", { cx: x(p.turn), cy: y(p.value), r: 3, fill: color }));
  });
  const legend = h(
    "ul",
    { class: "legend" },
    series.map((sr, i) => h("li", {}, h("span", { class: "swatch", style: `background:${palette[i % palette.length]}` }), sr.name)),
  );
  return h("figure", { class: "chart-wrap" }, svg, legend);
}

export function sectionTitle(text, ...extra) {
  return h("div", { class: "section-head" }, h("h2", {}, text), ...extra);
}

export function replace(container, node) {
  clear(container);
  container.append(node);
}
