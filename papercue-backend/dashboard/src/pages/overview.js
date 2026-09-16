import { api } from "../api.js";
import { h, mount, fmtTime, fmtNum } from "../dom.js";
import { ko } from "../i18n/ko.js";
import { badge, devJson, empty, errorNotice, kv, loading, notice, sectionTitle } from "../components.js";

function card(title, body, kind = "") {
  return h("section", { class: `card ${kind}` }, h("h3", {}, title), body);
}

export function overviewView(st) {
  const o = ko.overview;
  const warnings = (st.warnings ?? []).map((w) =>
    notice(ko.warnings[w] ?? w, w === "default_mock_mode" ? "info" : "warn"),
  );
  const llm = st.llm;
  const emb = st.embeddings;
  const cards = h(
    "div",
    { class: "grid" },
    card(o.systemStatus, kv([
      [o.backend, badge(o.backendOk, "ok")],
      [o.pipelineVersion, st.backend.pipeline_version],
      [o.promptVersion, st.backend.prompt_version],
      [o.schemaVersion, st.backend.schema_version],
    ])),
    card(o.database, kv([
      [o.database, st.database.ok ? badge(o.dbOk, "ok") : badge(o.dbFail, "bad")],
      [o.dbLocation, h("code", {}, st.database.location)],
      [o.papers, String(st.database.papers)],
      [o.activeSessions, String(st.database.active_sessions)],
    ])),
    card(o.localLlm, kv([
      [o.defaultProvider, llm.default_provider === "mock" ? badge(ko.common.simulated, "mock") : "Ollama"],
      ["모델", h("code", {}, llm.ollama_model)],
      [o.ollamaConnection, llm.ollama_available ? badge(o.connected, "ok") : badge(o.disconnected, "bad")],
      ["모델 설치", llm.ollama_model_installed ? badge(o.modelInstalled, "ok") : badge(o.modelMissing, "warn")],
      ["온도(temperature)", fmtNum(llm.temperature, 1)],
      ["형식 오류 재시도", String(llm.max_retries)],
    ]), llm.ollama_available ? "" : "card-warn"),
    card(o.embedding, kv([
      ["모델", h("code", {}, emb.model)],
      ["상태", emb.simulated ? badge(o.embeddingSimulated, "mock") : emb.loaded ? badge(o.embeddingLoaded, "ok") : o.embeddingNotLoaded],
      ["모델 다운로드 허용", emb.allow_download ? ko.common.yes : ko.common.no],
    ])),
    card(o.localOnly, kv([
      [o.binding, h("code", {}, `${st.network.host}:${st.network.port}`)],
      ["", st.network.loopback_only ? badge(o.loopbackYes, "ok") : badge(o.loopbackNo, "bad")],
      [o.noExternal, st.network.external_services.length === 0 ? badge(o.noExternalDetail, "ok") : st.network.external_services.join(", ")],
      ["허용된 브라우저 출처(CORS)", st.network.cors_origins.join(", ")],
    ]), st.network.loopback_only ? "" : "card-bad"),
    card(`${o.retention} · ${o.debug}`, kv([
      [o.retention, o.days(st.privacy.retention_days)],
      [o.debug, st.privacy.debug_store_prompts ? badge(o.debugOn, "bad") : badge(o.debugOff, "ok")],
      ["업로드 최대 크기", `${Math.round(st.privacy.max_upload_bytes / 1024)}KB`],
      ["발화 최대 길이", `${st.privacy.max_turn_chars}자`],
    ]), st.privacy.debug_store_prompts ? "card-bad" : ""),
    card(o.gitCheck, !st.git.checked
      ? h("p", {}, o.gitUnchecked)
      : st.git.files.length
        ? h("ul", {}, st.git.files.map((f) => h("li", {}, h("code", {}, f))))
        : badge(o.gitClean, "ok"), st.git.files?.length ? "card-bad" : ""),
    card(o.parameters, kv(Object.entries(st.parameters).map(([k, v]) => [k, String(v)]))),
  );
  const errors = st.recent_errors ?? [];
  const errorTable = errors.length
    ? h("table", { class: "table" },
        h("thead", {}, h("tr", {}, [o.errorTime, o.errorStage, o.errorCode, o.errorMessage].map((t) => h("th", {}, t)))),
        h("tbody", {}, errors.map((e) => h("tr", {},
          h("td", {}, fmtTime(e.created_at)),
          h("td", {}, ko.stages[e.stage] ?? e.stage),
          h("td", {}, h("code", {}, e.error_code)),
          h("td", {}, ko.validationCodes[e.error_code] ?? e.message)))))
    : empty(o.noErrors);
  return h("div", {},
    sectionTitle(o.title),
    h("p", { class: "intro" }, o.intro),
    ...warnings,
    cards,
    sectionTitle(o.recentErrors),
    errorTable,
    devJson(st),
  );
}

export async function renderOverview(root) {
  mount(root, loading());
  try {
    const st = await api.status();
    mount(root, overviewView(st));
  } catch (err) {
    mount(root, sectionTitle(ko.overview.title), errorNotice(err));
  }
}
