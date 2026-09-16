import { api } from "../api.js";
import { h, mount, fmtNum } from "../dom.js";
import { ko, label } from "../i18n/ko.js";
import { badge, devJson, empty, errorNotice, loading, notice, sectionTitle } from "../components.js";

export const UNIT_TYPE_ORDER = [
  "problem", "motivation", "research_gap", "core_idea", "method", "evidence",
  "result", "contribution", "application", "limitation", "example", "connection",
];
const MAX_UPLOAD_KB = 1000;
const lines = (text) => text.split("\n").map((l) => l.trim()).filter(Boolean);

function originBadge(origin) {
  const kind = { presenter: "presenter", llm: "real", mock: "mock", manual_edit: "edited" }[origin] ?? "neutral";
  return badge(label(ko.papers.origin, origin), kind);
}

function unitCard(paper, unit, onSaved) {
  const p = ko.papers;
  const body = h("div", { class: "unit-body" },
    h("p", { class: "unit-content" }, unit.content),
    h("dl", { class: "kv" },
      h("dt", {}, p.shortExplanation), h("dd", {}, unit.short_explanation),
      h("dt", {}, p.keywords), h("dd", {}, unit.keywords.length ? unit.keywords.map((k) => badge(k, "kw")) : ko.common.none),
      h("dt", {}, p.sourceRef), h("dd", {}, unit.source_reference ?? "–"),
      h("dt", {}, p.priority), h("dd", {}, fmtNum(unit.presenter_priority)),
      h("dt", {}, p.indexStatus), h("dd", {}, unit.indexed ? badge(p.indexed, "ok") : badge(p.notIndexed, "warn")),
      h("dt", {}, "ID"), h("dd", {}, h("code", {}, unit.id)),
    ),
  );
  const editBtn = h("button", { type: "button", class: "btn-link" }, p.edit);
  editBtn.addEventListener("click", () => {
    const title = h("input", { type: "text", value: unit.title, maxlength: 200 });
    const short = h("textarea", { rows: 2, maxlength: 400 }, unit.short_explanation);
    const prio = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: unit.presenter_priority });
    const save = h("button", { type: "submit", class: "btn" }, ko.common.save);
    const form = h("form", { class: "form" },
      h("label", {}, p.unitTitle, title), h("label", {}, p.shortExplanation, short),
      h("label", {}, p.priority, prio), save);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await api.editUnit(paper.id, unit.id, {
          title: title.value, short_explanation: short.value, presenter_priority: Number(prio.value),
        });
        onSaved(p.editSaved);
      } catch (err) {
        form.append(errorNotice(err));
      }
    });
    mount(body, form);
  });
  return h("article", { class: `unit unit-${unit.unit_type}`, dataset: { unitType: unit.unit_type } },
    h("header", {},
      badge(label(ko.unitTypes, unit.unit_type), "type"), " ",
      h("strong", {}, unit.title), " ", originBadge(unit.origin), " ", editBtn),
    body);
}

export function paperDetailView(paper, { onReindex, onSaved, filter = "all", setFilter }) {
  const p = ko.papers;
  const units = filter === "all" ? paper.units : paper.units.filter((u) => u.unit_type === filter);
  const chips = h("div", { class: "chips", role: "group", "aria-label": "단위 유형 필터" },
    [["all", p.filterAll], ...UNIT_TYPE_ORDER.map((t) => [t, ko.unitTypes[t]])].map(([key, text]) => {
      const count = key === "all" ? paper.units.length : paper.units.filter((u) => u.unit_type === key).length;
      const btn = h("button", { type: "button", class: `chip ${filter === key ? "active" : ""}`, "aria-pressed": String(filter === key) }, `${text} ${count}`);
      btn.addEventListener("click", () => setFilter(key));
      return btn;
    }));
  const reindex = h("button", { type: "button", class: "btn" }, p.reindex);
  reindex.addEventListener("click", onReindex);
  return h("div", { class: "paper-detail" },
    h("h2", {}, paper.title),
    h("p", { class: "muted" }, p.unitCount(paper.unit_count, paper.indexed_unit_count), " · ", p.indexModel, ": ",
      h("code", {}, paper.index_model ?? "–"), " ", reindex),
    paper.abstract ? h("p", { class: "abstract" }, paper.abstract) : null,
    h("section", { class: "card card-forbidden" },
      h("h3", {}, p.forbidden),
      paper.forbidden_claims.length ? h("ul", {}, paper.forbidden_claims.map((c) => h("li", {}, badge("금지", "bad"), " ", c))) : empty(p.noForbidden)),
    Object.keys(paper.preferred_terminology).length
      ? h("section", { class: "card" }, h("h3", {}, p.terminology),
          h("ul", {}, Object.entries(paper.preferred_terminology).map(([k, v]) => h("li", {}, `${k} → ${v}`))))
      : null,
    h("h3", {}, p.units),
    chips,
    units.length ? h("div", { class: "units" }, units.map((u) => unitCard(paper, u, onSaved))) : empty(p.noUnitsForFilter),
    devJson(paper),
  );
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function ingestionForm(onCreated) {
  const f = ko.papers.form;
  const title = h("input", { type: "text", required: true, maxlength: 300 });
  const abstract = h("textarea", { rows: 3, maxlength: 10000 });
  const contributions = h("textarea", { rows: 2 });
  const limitations = h("textarea", { rows: 2 });
  const forbidden = h("textarea", { rows: 2 });
  const fullText = h("textarea", { rows: 6, maxlength: 400000 });
  const file = h("input", { type: "file", accept: ".txt,.md,text/plain,text/markdown" });
  const labeler = h("select", {}, h("option", { value: "mock" }, f.labelerMock), h("option", { value: "ollama" }, f.labelerOllama));
  const status = h("div", { class: "form-status", "aria-live": "polite" });
  const form = h("form", { class: "form card" },
    h("h3", {}, f.title),
    h("label", {}, f.paperTitle, title),
    h("label", {}, f.abstract, abstract),
    h("label", {}, f.contributions, contributions),
    h("label", {}, f.limitations, limitations),
    h("label", {}, f.forbidden, forbidden),
    h("label", {}, f.fullText, fullText),
    h("label", {}, f.file, file, h("small", { class: "muted" }, f.fileHint(MAX_UPLOAD_KB))),
    h("label", {}, f.labeler, labeler),
    h("button", { type: "submit", class: "btn" }, f.submit),
    status);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    mount(status);
    if (!title.value.trim()) return mount(status, notice(f.needTitle, "error"));
    const common = {
      title: title.value.trim(), abstract: abstract.value.trim(), key_contributions: lines(contributions.value),
      limitations: lines(limitations.value), forbidden_claims: lines(forbidden.value), unit_labeler: labeler.value,
    };
    try {
      let report;
      const picked = file.files?.[0];
      if (picked) {
        if (!/\.(txt|md|markdown)$/i.test(picked.name)) return mount(status, notice(f.badFile, "error"));
        if (picked.size > MAX_UPLOAD_KB * 1000) return mount(status, notice(f.fileTooLarge, "error"));
        report = await api.uploadPaper({ ...common, filename: picked.name, content_base64: await readFileBase64(picked) });
      } else {
        report = await api.createPaper({ ...common, full_text: fullText.value.trim() || null });
      }
      mount(status, notice(f.created(report.units_created), "ok"));
      onCreated(report.paper.id);
    } catch (err) {
      mount(status, errorNotice(err));
    }
  });
  return form;
}

export async function renderPapers(root, params, ctx) {
  mount(root, loading());
  let papers;
  try {
    papers = await api.papers();
  } catch (err) {
    return mount(root, sectionTitle(ko.papers.title), errorNotice(err));
  }
  const selectedId = params[0] ?? papers[0]?.id;
  const detail = h("div", { class: "detail" });
  const flash = h("div", { "aria-live": "polite" });
  let filter = params[1] ?? "all";

  async function showDetail(message) {
    if (!selectedId) return mount(detail, empty(ko.papers.empty));
    try {
      const paper = await api.paper(selectedId);
      mount(detail, message ? notice(message, "ok") : "", paperDetailView(paper, {
        filter,
        setFilter: (key) => { filter = key; showDetail(); },
        onSaved: (msg) => showDetail(msg),
        onReindex: async () => {
          try {
            const r = await api.reindex(selectedId);
            showDetail(ko.papers.reindexDone(r.indexed_units));
          } catch (err) {
            mount(flash, errorNotice(err));
          }
        },
      }));
    } catch (err) {
      mount(detail, errorNotice(err));
    }
  }

  const list = papers.length
    ? h("ul", { class: "list" }, papers.map((p) =>
        h("li", { class: p.id === selectedId ? "active" : "" },
          h("a", { href: `#/papers/${encodeURIComponent(p.id)}` }, p.title),
          h("small", { class: "muted" }, ` ${ko.papers.unitCount(p.unit_count, p.indexed_unit_count)}`))))
    : empty(ko.papers.empty);

  mount(root,
    sectionTitle(ko.papers.title),
    h("p", { class: "intro" }, ko.papers.intro),
    flash,
    h("div", { class: "split" },
      h("aside", {}, h("h3", {}, ko.papers.list), list, ingestionForm((id) => ctx.navigate("papers", id))),
      detail));
  await showDetail();
}
