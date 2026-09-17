import { pres } from "../api.js";
import { h, mount, fmtNum } from "../dom.js";
import { label } from "../i18n/ko.js";
import { badge, errorNotice, loading, notice, sectionTitle } from "../components.js";
import { P, langBadge, mmss, pct, section, simpleTable } from "../lab/common.js";
import { metricTiles } from "./evaluation.js";

const O = P.overview;

export function presentationOverview(sessions, evaluation) {
  const evalById = Object.fromEntries((evaluation?.sessions ?? []).map((s) => [s.session_id, s]));
  const rows = sessions.map((s) => {
    const ev = evalById[s.session_id];
    const sid = encodeURIComponent(s.session_id);
    return h("tr", { dataset: { session: s.session_id } },
      h("td", {}, h("strong", {}, s.title_ko), h("br"), h("small", { class: "muted" }, `${s.session_id} · ${s.description}`)),
      h("td", {}, langBadge(s.language)),
      h("td", {}, label(P.scenarios, s.scenario_type)),
      h("td", {}, `${s.presenter.display_name} (${label(P.levels, s.presenter.expertise_level)})`),
      h("td", { class: "num" }, `${mmss(s.duration_s)}${s.excerpt_start_elapsed ? " (발췌)" : ""}`),
      h("td", { class: "num" }, String(s.gt_issues)),
      h("td", { class: "num" }, String(s.gt_positive)),
      h("td", { class: "num" }, ev ? String(ev.counts.delivered) : "–"),
      h("td", { class: "num" }, ev ? String(ev.counts.suppressed) : "–"),
      h("td", { class: "num" }, ev ? pct(ev.metrics.event_accuracy) : "–"),
      h("td", { class: `num ${ev?.counts.fp ? "bad" : ""}` }, ev ? String(ev.counts.fp) : "–"),
      h("td", { class: `num ${ev?.counts.fn ? "bad" : ""}` }, ev ? String(ev.counts.fn) : "–"),
      h("td", { class: "num" }, ev ? fmtNum(ev.metrics.mean_confidence) : "–"),
      h("td", { class: "actions" },
        h("a", { class: "btn btn-small", href: `#/lab/${sid}/play` }, `▶ ${O.autoplay}`),
        h("a", { class: "btn btn-small btn-secondary", href: `#/lab/${sid}` }, `⏭ ${O.stepwise}`)));
  });
  return h("div", { class: "pres-overview" },
    sectionTitle(O.title),
    h("p", { class: "intro" }, O.intro),
    notice(P.mockNotice, "mock"),
    section(O.pipelineTitle,
      h("ol", { class: "pipeline-flow static" }, O.pipeline.map((step) => h("li", {}, h("span", { class: "flow-box" }, step)))),
      h("p", { class: "small" }, "각 단계의 입력·출력·근거는 ‘발표 시뮬레이션 실험실’에서 시점별로 확인할 수 있습니다. 모바일 화면(",
        h("a", { href: "#/mobile", target: "_blank", rel: "noopener" }, "#/mobile"), ")은 가장 최근에 재생한 실행 상태를 공유합니다.")),
    evaluation ? section(`${O.totals} `, badge(`Judge: ${evaluation.judge}`, "neutral"), metricTiles(evaluation.overall.metrics, evaluation.overall.counts),
      h("p", { class: "small" }, h("a", { href: "#/evaluation" }, "평가 상세 보기 →"))) : null,
    section(`발표 세션 ${sessions.length}개`, simpleTable(O.columns, rows, { id: "pres-sessions" })),
  );
}

export async function renderPresentation(root) {
  mount(root, loading());
  try {
    const [sessions, evaluation] = await Promise.all([pres.sessions(), pres.evaluation().catch(() => null)]);
    mount(root, presentationOverview(sessions, evaluation));
  } catch (err) {
    mount(root, sectionTitle(O.title), errorNotice(err));
  }
}
