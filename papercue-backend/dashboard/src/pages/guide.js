import walkthrough from "../../../sample_data/ko_walkthrough.json";
import { api } from "../api.js";
import { h, mount } from "../dom.js";
import { ko } from "../i18n/ko.js";
import { badge, errorNotice, notice, sectionTitle } from "../components.js";
import { NO_BROAD_CLAIM } from "../explain.js";

export const MODULES = [
  {
    name: "논문 수집 서비스", file: "app/services/paper_ingestion.py", kind: "혼합",
    purpose: "논문을 검색 가능한 PaperUnit(논문 단위)으로 나눕니다.",
    input: "제목, 초록, 본문 또는 로컬 .txt/.md 파일, 발표자가 적은 기여·한계·금지 주장",
    output: "유형·제목·짧은 설명·키워드가 붙은 논문 단위",
    how: "본문 분할은 결정론적 코드, 각 조각의 유형·제목 분류는 로컬 LLM(또는 Mock 규칙). 원문 내용은 모델이 바꾸지 않습니다.",
    store: "papers, paper_units 테이블", inspect: "논문 지식 구조 페이지",
    failures: "Ollama 미실행(503), 파일 이름·크기·인코딩 오류(422/413), 모델 분류 형식 오류(502)",
    memoro: "대화 중 찾아볼 ‘지속 기억’을 미리 구조화해 두는 단계", spa: "적응(adapt) 단계에서 쓸 지식 기반",
  },
  {
    name: "로컬 임베딩·인덱스", file: "app/services/embeddings.py", kind: "결정론적(로컬 모델)",
    purpose: "논문 단위를 벡터로 바꿔 유사도 검색을 가능하게 합니다.",
    input: "논문 단위 텍스트", output: "단위별 벡터", how: "로컬 sentence-transformer 또는 Mock 해싱 임베더. 원격 API 없음.",
    store: "paper_embeddings 테이블", inspect: "논문 지식 구조의 인덱스 상태, ‘로컬 인덱스 다시 생성’",
    failures: "모델이 로컬 캐시에 없음, 인덱스 모델 불일치(다시 생성 필요)",
    memoro: "검색 가능한 기억 저장소", spa: "적응 단계의 지식 접근",
  },
  {
    name: "초기 청중 모델(프로필)", file: "app/services/profile_service.py", kind: "결정론적",
    purpose: "수동으로 입력한 프로필을 불확실한 사전 가설로 바꿉니다.",
    input: "연구 주제, 최근 키워드, 익숙한 방법, 적용 분야, 연결 메모", output: "신뢰도 상한(기본 0.65) 아래의 프로필 가설",
    how: "고정 규칙. 민감한 항목은 제외하고 웹 검색은 하지 않습니다.",
    store: "listener_profiles, evidence_items, audience_beliefs", inspect: "청중 모델 페이지의 회색 점선 카드",
    failures: "민감 항목 제외(rejected_fields)", memoro: "—", spa: "감지 이전의 사전 믿음(prior)",
  },
  {
    name: "대화 상태 추적기", file: "app/services/conversation_tracker.py", kind: "LLM + 결정론적 병합",
    purpose: "대화 단계, 주제, 대화 행위, 질문, 해결되지 않은 질문을 추적합니다.",
    input: "최신 발화, 최근 발화 창(기본 6개), 이전 대화의 구조화된 요약, 후보 주제", output: "갱신된 대화 상태",
    how: "모델은 최신 발화만 분류하고, 병합·민감어 제거·오래된 발화 요약은 코드가 합니다.",
    store: "conversation_states, conversation_summaries", inspect: "턴별 처리 과정 1~3단계",
    failures: "모델 형식 오류, 원문에 없는 질문 제시(원문으로 대체)",
    memoro: "제한된 최근 맥락 + 오래된 대화의 검색 가능한 기억", spa: "감지(sense)",
  },
  {
    name: "근거 추출기", file: "app/services/evidence_extractor.py", kind: "LLM 제안 + 결정론적 검증",
    purpose: "청중의 말로 뒷받침되는 근거만 기록합니다.",
    input: "최신 청중 발화, 현재 대화 상태, 논문 용어", output: "명시적/행동/약한 추론으로 구분된 근거",
    how: "인용문이 실제 발화에 있어야 하고, 허용 값·민감어·성향 판단을 검사합니다. 신뢰도는 근거 유형으로 코드가 정합니다.",
    store: "evidence_items", inspect: "턴별 처리 과정 4단계, 청중 모델 카드의 ‘뒷받침 근거’",
    failures: "인용 불일치·민감 키로 인한 제외", memoro: "맥락 해석 단계", spa: "감지(sense)",
  },
  {
    name: "청중 모델 저장소·갱신 규칙", file: "app/services/audience_model.py", kind: "결정론적",
    purpose: "근거를 받아 믿음을 만들고, 강화·약화·대체합니다.",
    input: "검증된 근거와 (선택적으로) 모델의 키 매핑 제안", output: "믿음과 변경 이력",
    how: "명시적 > 행동 > 약한 추론 > 프로필. 반복 근거는 누적, 첫 반대 근거는 약화만, 대화 근거는 프로필을 대체, 프로필 가설은 시간이 지나며 감쇠.",
    store: "audience_beliefs, audience_belief_history", inspect: "진화하는 청중 모델 페이지, 5~6단계",
    failures: "허용되지 않은 값/차원 불일치 제안 거부", memoro: "—", spa: "예측(predict)을 위한 사용자 모델 갱신",
  },
  {
    name: "로컬 검색 서비스", file: "app/services/retrieval.py", kind: "결정론적(로컬 임베딩)",
    purpose: "현재 필요한 논문 단위 몇 개만 찾습니다.",
    input: "최근 질문, 주제, 해결되지 않은 질문, 우려 사항, 의도한 단서 행동", output: "점수가 붙은 논문 단위(기본 최대 4개)",
    how: "코사인 유사도 + 키워드 일치 + 행동별 유형 가산점 + 발표자 우선순위",
    store: "저장하지 않음(처리 기록에 ID와 점수만)", inspect: "7~8단계, 단서 생성 분석의 검색 표",
    failures: "인덱스 없음(409), 기준을 넘는 단위 없음 → 확인 질문",
    memoro: "현재 맥락과 관련된 정보만 검색", spa: "적응(adapt)을 위한 근거 선택",
  },
  {
    name: "개입 판단 엔진", file: "app/services/cue_decision.py", kind: "결정론적",
    purpose: "단서를 줄지, 어떤 행동(쉽게 설명, 우려 대응 등)을 할지 정합니다.",
    input: "대화 상태, 믿음, 검색 결과, 최근 단서", output: "개입 여부, 행동, 대상, 점수, 짧은 이유",
    how: "필요성·관련성·근거 강도·모델 신뢰도·새로움·시간·끼어들기 비용의 가중합. 근거가 약하면 확인 질문으로 바꿉니다.",
    store: "cue_decisions", inspect: "9단계, 단서 생성 분석의 점수 표",
    failures: "점수 미달 → 단서 불필요", memoro: "해석·검색과 분리된 결정", spa: "예측(predict)",
  },
  {
    name: "단서 생성기", file: "app/services/cue_generator.py", kind: "로컬 LLM(또는 Mock)",
    purpose: "선택된 행동을 2~7단어의 짧은 단서로 표현합니다. 완성된 답변은 만들지 않습니다.",
    input: "행동·대상, 검색된 단위 요약, 관련 근거, 최근 단서, 선호 용어, 금지 주장", output: "단서 후보 JSON",
    how: "모델은 표현만 담당합니다. 전달 여부는 다음 필터가 정합니다.",
    store: "generated_cues", inspect: "10단계, 단서 생성 분석",
    failures: "JSON 형식 오류 → 단서 없음", memoro: "간결하고 방해가 적은 출력", spa: "적응(adapt)",
  },
  {
    name: "안전성·근거성 필터", file: "app/safety/cue_filter.py", kind: "결정론적",
    purpose: "단어 수, 답변 형태, 근거 ID, 새로운 수치·주장, 청중 단정, 민감 속성, 조작, 반복, 신뢰도를 검사합니다.",
    input: "단서 후보, 검색된 단위, 세션 근거 ID, 최근 단서", output: "검사별 결과와 최종 상태",
    how: "근거·신뢰도 문제면 고정된 확인 질문으로 대체, 그 외에는 단서를 내보내지 않습니다.",
    store: "generated_cues.filter_results", inspect: "11~12단계, 단서 생성 분석의 검사 표",
    failures: "근거 부족, 중복, 민감 추론 위험", memoro: "최소한으로 방해하는 출력 보장", spa: "적응 결과의 안전 확인",
  },
  {
    name: "파이프라인·처리 기록", file: "app/services/pipeline.py, tracing.py", kind: "결정론적",
    purpose: "모든 단계를 순서대로 실행하고 단계별 기록을 남깁니다.",
    input: "API 요청", output: "처리 결과와 PipelineTrace",
    how: "기록에는 ID, 상태, 점수, 짧은 이유, 소요 시간만 저장하고 원문 발화는 복제하지 않습니다.",
    store: "pipeline_traces, processing_errors", inspect: "턴별 처리 과정 페이지",
    failures: "로컬 모델 오류가 기록되고 시스템 개요에 표시됨", memoro: "해석·검색·생성의 분리", spa: "감지→예측→적응 루프 전체",
  },
  {
    name: "세션·개인정보 관리", file: "app/services/session_service.py", kind: "결정론적",
    purpose: "동의 확인, 내보내기, 초기화, 영구 삭제, 보관 기간을 관리합니다.",
    input: "세션 요청", output: "세션 정보, 내보내기 JSON, 감사 기록",
    how: "동의가 없으면 세션을 만들 수 없고, 삭제 시 모든 파생 데이터가 연쇄 삭제됩니다.",
    store: "sessions, audit_events", inspect: "세션 작업 공간의 세션 관리",
    failures: "동의 없음(403), 보관 기간 만료(409)", memoro: "—", spa: "—",
  },
];

export const CONCEPTS = [
  ["근거(evidence)", "청중이 실제로 한 말에서 뽑은 관찰 한 건. 인용문, 근거 유형(명시적/행동/약한 추론), 출처 발화를 가집니다. 예: “이 대화가 서버에 저장되는 건가요?” → 우려 사항: 개인정보 보호(명시적)."],
  ["믿음(belief)", "여러 근거를 모아 만든 청중 모델의 항목. 값, 신뢰도, 출처, 갱신 이유, 변경 이력을 가집니다. 예: 우려 사항 ‘개인정보 보호’ = 제기됨, 신뢰도 0.90."],
  ["대화 상태(conversation state)", "지금 대화가 어디쯤인지에 대한 정보. 단계, 주제, 최근 대화 행위, 해결되지 않은 질문 등. 사람에 대한 정보가 아니라 대화에 대한 정보입니다."],
  ["검색된 논문 단위(retrieved unit)", "현재 질문과 관련해 찾아낸 논문 조각. 단서는 이 단위들에만 근거할 수 있습니다."],
  ["단서 행동(cue action)", "무엇을 할지에 대한 결정. 예: 우려 사항에 직접 대응, 쉽게 설명하기. 개입 판단 엔진이 정합니다."],
  ["생성된 단서(generated cue)", "행동을 발표자가 한눈에 읽을 수 있는 짧은 말로 표현한 결과. 예: “개인정보 먼저—로컬 처리”. 필터를 통과해야 전달됩니다."],
];

const FLOW = [
  "논문 지식", "프로필 가설", "대화 입력", "근거 추출", "대화 상태 갱신", "청중 모델 갱신",
  "논문 단위 검색", "개입 판단", "안전성·근거성 검사", "최종 단서 또는 단서 없음",
];

function diagram() {
  return h("figure", { class: "flow", "aria-label": "PaperCue 처리 흐름도" },
    h("ol", {}, FLOW.map((step, i) => h("li", { class: i < 2 ? "flow-prep" : i < 6 ? "flow-sense" : i < 8 ? "flow-predict" : "flow-adapt" }, step))),
    h("figcaption", {},
      badge("준비", "muted"), " 논문·프로필  ",
      badge("감지(sense)", "neutral"), " 근거·상태·모델 갱신  ",
      badge("예측(predict)", "real"), " 검색·개입 판단  ",
      badge("적응(adapt)", "ok"), " 단서 표현·검사"));
}

export async function runWalkthrough() {
  const report = await api.createPaper(walkthrough.paper);
  const session = await api.createSession({
    paper_id: report.paper.id, consent_confirmed: true, mode: "on_demand", llm_provider: "mock",
    cue_language: "ko", label: "예시 워크스루(Mock)",
  });
  await api.setProfile(session.id, walkthrough.profile);
  const results = [];
  for (const turn of walkthrough.turns) results.push(await api.addTurn(session.id, turn));
  const cue = await api.requestCue(session.id);
  return { sessionId: session.id, lastTurnId: results[results.length - 1].turn.id, cue };
}

function walkthroughSection(ctx) {
  const g = ko.guide;
  const out = h("div", { "aria-live": "polite" });
  const btn = h("button", { type: "button", class: "btn btn-primary" }, g.runWalkthrough);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    mount(out, g.walkthroughRunning);
    try {
      const r = await runWalkthrough();
      ctx.setSession(r.sessionId);
      const sid = encodeURIComponent(r.sessionId);
      mount(out, notice(g.walkthroughDone, "ok"),
        h("p", {}, "최종 단서: ", h("strong", {}, r.cue.cue ? `“${r.cue.cue}”` : ko.cues.noFinalCue)),
        h("ul", {},
          h("li", {}, h("a", { href: `#/trace/${sid}/${encodeURIComponent(r.lastTurnId)}` }, "턴별 처리 과정에서 청중 발화 확인")),
          h("li", {}, h("a", { href: `#/audience/${sid}` }, "진화하는 청중 모델 확인")),
          h("li", {}, h("a", { href: `#/cues/${sid}` }, "단서 생성 분석 확인"))));
    } catch (err) {
      mount(out, errorNotice(err));
    } finally {
      btn.disabled = false;
    }
  });
  const unit = walkthrough.paper.units[0];
  return h("section", { class: "card walkthrough" },
    h("h3", {}, "개인정보 질문 예시로 보는 전체 흐름"),
    h("pre", { class: "example" }, [
      "논문 근거:",
      `“${unit.content.split(". ")[0]}.”`,
      "",
      "초기 프로필:",
      "“청중은 웨어러블 센싱 연구를 수행한다.” (연구 주제: 웨어러블 센싱)",
      "",
      "청중 발화:",
      `“${walkthrough.turns[1].text}”`,
    ].join("\n")),
    h("ol", { class: "steps" },
      h("li", {}, h("strong", {}, "추출된 근거: "), "청중이 데이터 저장 위치를 명시적으로 질문함 → 우려 사항: 개인정보 보호(명시적 대화 근거). 인용문 “서버에 저장되는 건가요”가 실제 발화에 있는지 코드가 확인합니다."),
      h("li", {}, h("strong", {}, "대화 상태: "), "청중 행위 = 질문, 해결되지 않은 질문 = 개인정보 보호."),
      h("li", {}, h("strong", {}, "청중 모델 변화: "), "우려 사항: 개인정보 보호, 신뢰도 없음 → 0.90(명시적 근거의 초기 신뢰도). 이전 믿음이 있었다면 예: 0.20 → 0.90처럼 표시됩니다. 웨어러블 센싱 관련 믿음은 여전히 ‘프로필 가설’로 남습니다."),
      h("li", {}, h("strong", {}, "검색된 논문 단위: "), `‘${unit.title}’ — 질문·우려 사항과 키워드(서버, 저장)가 일치하여 가장 높은 점수를 받습니다.`),
      h("li", {}, h("strong", {}, "개입 행동: "), "우려 사항에 직접 대응(필요성 0.90, 끼어들기 비용 낮음 — 청중이 방금 질문했기 때문)."),
      h("li", {}, h("strong", {}, "안전성·근거성 검사: "), "단어 수 4개, 근거 단위 ID 유효, 새로운 수치 없음, 청중에 대한 단정 없음, 최근 단서와 중복 없음."),
      h("li", {}, h("strong", {}, "최종 단서: "), `“${walkthrough.expected.cue}”`)),
    h("div", { class: "card card-forbidden" },
      h("h4", {}, "시스템이 결론 내리지 않은 것"),
      h("p", {}, h("s", {}, NO_BROAD_CLAIM.claim)),
      h("p", {}, NO_BROAD_CLAIM.explanation)),
    btn, out);
}

export function guideView(ctx) {
  const g = ko.guide;
  return h("div", { class: "guide" },
    sectionTitle(g.title),
    h("p", { class: "intro" }, g.intro),
    h("section", { class: "card" },
      h("h3", {}, "한눈에 보는 구조"),
      diagram(),
      h("p", {},
        "PaperCue는 발표자 대신 답을 만들지 않습니다. 청중의 말에서 근거를 ‘감지’하고, 근거를 모아 청중 모델을 갱신한 뒤, ",
        "지금 필요한 설명 방식을 ‘예측’하고, 논문에 근거한 아주 짧은 단서로 ‘적응’합니다. 로컬 LLM은 분류와 표현만 맡고, ",
        "청중 모델의 값·신뢰도, 개입 여부, 최종 전달 여부는 모두 결정론적 코드가 정합니다."),
      h("p", {},
        "Memoro에서 가져온 원칙: 최근 대화는 몇 개만 유지하고 오래된 대화는 검색 가능한 요약으로 바꾸며, 현재 맥락과 관련된 정보만 검색하고, ",
        "해석·검색·생성을 분리하며, 방해가 적은 짧은 출력을 내고, 먼저 ‘요청 시에만’ 돕습니다(자동 개입은 평가만 합니다).")),
    h("section", { class: "card" },
      h("h3", {}, "헷갈리기 쉬운 개념"),
      h("dl", { class: "kv concepts" }, CONCEPTS.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
    walkthroughSection(ctx),
    h("section", { class: "card" },
      h("h3", {}, "모듈별 설명"),
      MODULES.map((m) => h("details", { class: "module" },
        h("summary", {}, h("strong", {}, m.name), " ", badge(m.kind, m.kind.includes("LLM") ? "real" : "neutral"), " ", h("code", {}, m.file)),
        h("dl", { class: "kv" },
          h("dt", {}, "목적"), h("dd", {}, m.purpose),
          h("dt", {}, "들어오는 정보"), h("dd", {}, m.input),
          h("dt", {}, "만들어 내는 것"), h("dd", {}, m.output),
          h("dt", {}, "처리 방식"), h("dd", {}, m.how),
          h("dt", {}, "저장 위치"), h("dd", {}, m.store),
          h("dt", {}, "확인 방법"), h("dd", {}, m.inspect),
          h("dt", {}, "흔한 실패"), h("dd", {}, m.failures),
          h("dt", {}, "Memoro와의 관계"), h("dd", {}, m.memoro),
          h("dt", {}, "감지–예측–적응과의 관계"), h("dd", {}, m.spa))))),
  );
}

export async function renderGuide(root, params, ctx) {
  mount(root, guideView(ctx));
}
