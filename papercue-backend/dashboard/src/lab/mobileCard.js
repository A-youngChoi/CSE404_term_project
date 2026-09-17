// The presenter's glanceable phone view. Only the prompt, its importance, a visual cue and a countdown.
import { h } from "../dom.js";
import { P } from "./common.js";

const PRIORITY_ICON = { high: "▲", medium: "◆", low: "●" };

export function mobileCard(state, { remaining } = {}) {
  const lang = state?.language === "en" ? "en" : "ko";
  const M = P.mobile;
  const secs = remaining ?? state?.remaining_seconds;
  const active = Boolean(state?.text) && (secs === undefined || secs === null || secs > 0);
  const frame = h("div", { class: `phone${active ? ` phone-${state.priority}` : " phone-idle"}`, lang,
    role: "status", "aria-live": "polite", dataset: { priority: active ? state.priority : "none" } });
  if (!state || state.run_id === null) {
    frame.append(h("div", { class: "phone-idle-text" }, M.noRun[lang]));
    return frame;
  }
  if (!active) {
    frame.append(h("div", { class: "phone-idle-text" }, M.waiting[lang]));
  } else {
    const fraction = state.display_seconds ? Math.max(0, Math.min(1, secs / state.display_seconds)) : 1;
    frame.append(
      h("div", { class: "phone-band" },
        h("span", { class: "phone-priority" }, `${PRIORITY_ICON[state.priority] ?? ""} ${M.priority[lang]}: ${M.priorities[lang][state.priority] ?? state.priority}`)),
      h("div", { class: "phone-text" }, state.text),
      h("div", { class: "phone-timer" },
        h("div", { class: "phone-timer-track" }, h("div", { class: "phone-timer-fill", style: `width:${(fraction * 100).toFixed(0)}%` })),
        h("span", { class: "phone-timer-num" }, M.seconds[lang](Math.ceil(secs ?? 0)))),
    );
  }
  if (state.is_mock) frame.append(h("span", { class: "phone-mock", title: P.mockNotice }, M.mock));
  return frame;
}
