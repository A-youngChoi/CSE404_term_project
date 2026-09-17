// Shared helpers for the presentation-simulation pages.
import { h, fmtNum } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import { badge } from "../components.js";

export const P = ko.pres;

export function mmss(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "–";
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function pct(v) {
  return typeof v === "number" ? `${Math.round(v * 100)}%` : "–";
}

export const decisionLabel = (d) => label(P.decisions, d);
export const issueLabel = (i) => label(P.issues, i);
export const promptTypeLabel = (t) => label(P.promptTypes, t);

export function decisionBadge(decision) {
  return h("span", { class: `badge dec dec-${decision}`, dataset: { decision } },
    `${P.decisionIcons[decision] ?? ""} ${decisionLabel(decision)}`);
}

export function mockBadge(isMock) {
  return isMock ? badge(ko.common.simulated, "mock", P.mockNotice) : badge("로컬 LLM(Ollama)", "real");
}

export function langBadge(lang) {
  return badge(label(P.languages, lang), "type");
}

/** 0-1 value as a labelled horizontal meter. */
export function meter(value, { kind = "", title } = {}) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  return h("span", { class: `meter ${kind}`, role: "img", "aria-label": `${title ?? ""} ${fmtNum(value)}` },
    h("span", { class: "meter-track" }, h("span", { class: "meter-fill", style: `width:${(v * 100).toFixed(0)}%` })),
    h("span", { class: "meter-num" }, fmtNum(value)));
}

/** Values stored in the user model are mostly English enums; show Korean where we know them. */
export function umValue(value) {
  if (value === null || value === undefined) return "–";
  if (Array.isArray(value)) return value.length ? value.map(umValue).join(", ") : ko.common.none;
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  const s = String(value);
  if (s.includes("->")) {
    const [issue, ptype] = s.split("->");
    return `${issueLabel(issue)} → ${promptTypeLabel(ptype)}`;
  }
  return P.umValues[s] ?? P.levels[s] ?? P.languages[s] ?? P.lengths[s] ?? P.issues[s] ?? s;
}

/**
 * Evidence reference as a clickable chip. Kinds: event IDs, memory IDs (mem_), knowledge chunk IDs,
 * user-model attributes (um:), prompt IDs (prm_), issue IDs (iss_).
 */
export function refKind(ref) {
  if (!ref) return "other";
  if (ref.startsWith("um:")) return "usermodel";
  if (ref.startsWith("mem_")) return "memory";
  if (ref.startsWith("prm_")) return "prompt";
  if (ref.startsWith("iss_")) return "issue";
  if (/^S\d+_e\d+$/.test(ref)) return "event";
  if (ref.startsWith("prior:") || ref.startsWith("profile:")) return "prior";
  if (/^kp_/.test(ref)) return "keypoint";
  return "knowledge";
}

export function refChip(ref, onRef) {
  const kind = refKind(ref);
  const text = kind === "usermodel" ? label(P.umAttrs, ref.slice(3)) : ref;
  if (!onRef || kind === "prior" || kind === "other") return h("span", { class: `ref ref-${kind}` }, text);
  return h("button", { type: "button", class: `ref ref-${kind}`, dataset: { ref }, onclick: () => onRef(ref) }, text);
}

export function refList(refs, onRef, max = 6) {
  const list = (refs ?? []).filter(Boolean);
  if (!list.length) return "–";
  // Long lists (e.g. working memory sources) show the most recent entries; the full list is in the tooltip.
  const shown = list.length > max ? list.slice(-max) : list;
  return h("span", { class: "refs", title: list.length > max ? list.join(", ") : null },
    list.length > max ? h("span", { class: "refs-more" }, `…+${list.length - max} `) : null,
    shown.map((r) => refChip(r, onRef)));
}

/** Korean explanation of a Judge decision built only from structured fields. */
export function explainJudge(d, thresholds) {
  if (!d) return "";
  const t = thresholds ?? { intervene: 0.36, wait: 0.12, min_confidence: 0.5 };
  const issue = issueLabel(d.detected_issue);
  if (d.detected_issue === "none" || !d.issue_id) {
    return "현재 맥락에 활성 문제가 없어 개입하지 않았습니다. 불필요한 알림으로 발표자의 주의를 빼앗지 않기 위함입니다.";
  }
  const head = `‘${issue}’을(를) 감지했습니다(심각도 ${fmtNum(d.severity)}, 긴급도 ${fmtNum(d.urgency)}). `;
  const cs = d.cooldown_state ?? {};
  switch (d.decision) {
    case "INTERVENE_NOW":
      return head + `효용 ${fmtNum(d.utility)} ≥ 개입 기준 ${t.intervene}, 신뢰도 ${fmtNum(d.confidence)} ≥ ${t.min_confidence}이므로 ` +
        `‘${promptTypeLabel(d.recommended_prompt_type)}’ 프롬프트를 ${label(P.lengths, d.recommended_prompt_length)} 보내기로 했습니다.`;
    case "WAIT_AND_OBSERVE":
      return head + (d.utility >= t.intervene
        ? `효용 ${fmtNum(d.utility)}은 충분하지만 신뢰도 ${fmtNum(d.confidence)}가 ${t.min_confidence}보다 낮아 다음 이벤트를 지켜봅니다.`
        : `효용 ${fmtNum(d.utility)}이 개입 기준 ${t.intervene}보다 낮아 발표자가 스스로 회복하는지 지켜봅니다.`);
    case "SUPPRESS_DUE_TO_RECENT_INTERVENTION":
      if (cs.in_cooldown) {
        return head + `직전 프롬프트(${cs.last_prompt_id})가 ${fmtNum(cs.seconds_since_last, 0)}초 전에 표시되어 쿨다운(${fmtNum(cs.cooldown_seconds, 0)}초) 중이므로 억제했습니다.`;
      }
      return head + `같은 문제에 대해 이미 프롬프트(${cs.redundant_with ?? "–"})를 보냈기 때문에 반복 알림을 억제했습니다.`;
    default:
      return head + `효용 ${fmtNum(d.utility)}이 대기 기준 ${t.wait}보다 낮아 개입하지 않았습니다.`;
  }
}

export function section(title, ...children) {
  return h("section", { class: "card" }, h("h3", {}, title), ...children);
}

export function simpleTable(headers, rows, attrs = {}) {
  return h("div", { class: "table-wrap" }, h("table", { class: "table", ...attrs },
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows)));
}
