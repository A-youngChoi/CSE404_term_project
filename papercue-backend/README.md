# PaperCue local backend and researcher dashboard

PaperCue is a speaker-side augmentation research prototype. A researcher explains their own paper face to face; PaperCue keeps an evolving, evidence-backed model of the listener and gives the presenter **one very short cue** (for example `privacy first—local processing`), never a full answer.

> **Research prototype. For consented research testing only - not for covert listening.**
> Every session requires `consent_confirmed: true`. All processing and storage stay on this computer.

Korean documentation: [`docs/KO_IMPLEMENTATION_GUIDE.md`](docs/KO_IMPLEMENTATION_GUIDE.md) · changelog [`docs/KO_CHANGELOG.md`](docs/KO_CHANGELOG.md)
Design: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`PRIVACY.md`](PRIVACY.md) · [`THREAT_MODEL.md`](THREAT_MODEL.md)

## What this MVP does

- Ingests a paper (title, abstract, pasted text or a local `.txt`/`.md` file, presenter-written contributions, results, limitations, preferred terms, forbidden claims) into typed `PaperUnit`s and indexes them with local embeddings.
- Turns a manually supplied listener profile into **uncertain priors** (confidence capped at `PROFILE_CONFIDENCE_CAP`, default 0.65).
- Accepts **typed** conversation turns, tracks conversation state with a bounded recent window plus structured memory of older turns, extracts validated evidence, and updates an auditable audience model with a deterministic policy.
- On explicit request (`on_demand`), retrieves a few relevant paper units, decides an action deterministically, words a cue with a local LLM (Ollama) or the explicit mock provider, and runs deterministic grounding/safety checks. `auto_candidate` only evaluates and never delivers.
- Records a structured trace for every turn and cue request (IDs, statuses, scores, short rationales, latencies; no hidden reasoning, no duplicated utterances).
- Ships a Korean researcher dashboard to inspect all of the above.

## What it deliberately does not do

No mobile app, microphone, ASR, diarization, TTS, earbud control, web scraping, Google Scholar, cloud deployment, internet authentication, always-on listening, reinforcement learning, or psychological profiling. Adapter interfaces for later mobile/ASR/TTS work are in `app/adapters/interfaces.py`.

## Requirements

- Python 3.11+
- Node.js 20.19+ (only to build/test the dashboard)
- Optional: [Ollama](https://ollama.com) for real local LLM output
- Optional: `sentence-transformers` for real local embeddings (pulls in PyTorch)

## Setup

```bash
cd papercue-backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"                 # core + pytest
pip install -e ".[embeddings]"          # optional: real local embeddings
cp .env.example .env                    # then edit as needed

cd dashboard && npm install && npm run build && cd ..
```

### Local embedding model

The embedding model is loaded **from the local cache only** (`EMBEDDING_ALLOW_DOWNLOAD=false`). Download it once. This transfers model weights only, never paper or conversation data:

```bash
python scripts/download_embedding_model.py
```

For mock/demo use without PyTorch, set `EMBEDDING_PROVIDER=hashing`. This is a deterministic hashing embedder, not semantic search.

### Ollama

```bash
# install from https://ollama.com, then:
ollama serve                            # listens on 127.0.0.1:11434
ollama pull qwen2.5:7b-instruct         # or set OLLAMA_MODEL to another local model
```

`OLLAMA_BASE_URL` must be a loopback address. Remote hosts are refused unless `OLLAMA_ALLOW_REMOTE=true`, which is not recommended. There is no fallback to any remote service. If Ollama is down, Ollama sessions return HTTP 503 `local_model_unavailable`.

### Mock mode (no model needed)

Mock mode is **explicit** and always labelled `mock:deterministic-rules` / `시뮬레이션(Mock)`. It is not genuine LLM output.

```bash
# .env
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=hashing
```

Sessions can also choose per session: `"llm_provider": "mock"` or `"ollama"`.

## Run

```bash
python -m app.main                      # http://127.0.0.1:8000
```

- Dashboard (built): http://127.0.0.1:8000/dashboard/
- Dashboard dev server: `cd dashboard && npm run dev` → http://127.0.0.1:5173/dashboard/ (proxies `/api` to the backend)
- Swagger UI: http://127.0.0.1:8000/docs. Note that FastAPI's Swagger page loads its JS/CSS from a public CDN; no data is sent, but set `API_DOCS_ENABLED=false` if you want zero external requests.

The server refuses to start on a non-loopback host unless `PAPERCUE_ALLOW_NON_LOCAL_BIND=true`.

## Tests

```bash
python -m pytest                        # backend: 73 tests
cd dashboard && npm test                # dashboard: 27 tests (includes a production build)
npm run build                           # production build into dashboard/dist
python scripts/security_check.py        # staged-diff secret/data scan (uses gitleaks if installed)
```

To regenerate dashboard fixtures from the real backend: `python scripts/export_dashboard_fixtures.py`.

## Demonstration

```bash
python scripts/demo.py                  # English walkthrough, mock mode, temporary DB
python scripts/demo.py --lang ko        # Korean walkthrough → "개인정보 먼저—로컬 처리"
python scripts/demo.py --ollama         # real local model + local sentence-transformer
```

It creates a paper, a consented session, and a profile, enters turns, prints the audience model after every listener turn, and then requests a cue. It prints the retrieved units, evidence, decision, scores, filter result, final cue, and per-stage latency. In the dashboard, the **시스템 동작 이해하기** page has a button that runs the same Korean walkthrough.

## Example API requests

```bash
API=http://127.0.0.1:8000/api/v1
PID=$(curl -s -X POST $API/papers -H 'Content-Type: application/json' \
      -d @sample_data/papercue_paper.json | python -c 'import sys,json;print(json.load(sys.stdin)["paper"]["id"])')
SID=$(curl -s -X POST $API/sessions -H 'Content-Type: application/json' \
      -d "{\"paper_id\":\"$PID\",\"consent_confirmed\":true,\"mode\":\"on_demand\",\"llm_provider\":\"mock\"}" \
      | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s -X POST $API/sessions/$SID/profile -H 'Content-Type: application/json' -d @sample_data/listener_profile.json
curl -s -X POST $API/sessions/$SID/turns -H 'Content-Type: application/json' \
     -d '{"speaker":"listener","text":"Does this send our conversation to a server?"}'
curl -s -X POST $API/sessions/$SID/cue                      # on-demand cue
curl -s -X POST $API/sessions/$SID/auto-candidate           # evaluation only
curl -s $API/sessions/$SID/audience-model
curl -s $API/sessions/$SID/turns/<turn_id>/trace
```

Main endpoints: `/health`, `/config/status`, `/data/inventory`, `/retention/expired`, `/retention/purge`, `/audit`, `/papers` (+ `/upload`, `/{id}`, `/{id}/units`, `/{id}/units/{unit_id}`, `/{id}/reindex`), `/sessions` (+ `/{id}`, `/reset`, `/export`, `/profile`, `/turns`, `/audience-model`, `/audience-model/history`, `/conversation-state`, `/evidence`, `/cue`, `/cues`, `/auto-candidate`, `/traces`, `/turns/{turn_id}/trace`).

## Deleting participant data

| Action | How |
|---|---|
| Inspect everything stored | `GET /api/v1/data/inventory`, dashboard **세션 작업 공간** |
| Export one session | `GET /api/v1/sessions/{id}/export` or **세션 내보내기** |
| Reset one session (keeps session + profile) | `POST /api/v1/sessions/{id}/reset` or **세션 초기화** |
| Permanently delete one session | `DELETE /api/v1/sessions/{id}` or **세션 영구 삭제** (typed confirmation) |
| Delete sessions past `RETENTION_DAYS` | `POST /api/v1/retention/purge` or `python scripts/purge_expired.py --delete` |
| Delete everything | stop the server and delete `data/` (database, uploaded papers, exports) |

Deletion cascades to the profile, turns, summaries, evidence, beliefs and history, states, cue decisions, cues, traces, error records, and debug prompts. An audit row keeps only the session ID and record counts.

## Common local-model errors

| Symptom | Fix |
|---|---|
| `503 local_model_unavailable`: "Ollama is not reachable" | run `ollama serve` |
| `503` "model … is not installed" | `ollama pull <OLLAMA_MODEL>` |
| `503` timeout | use a smaller model or raise `OLLAMA_TIMEOUT_SECONDS` |
| `502 malformed_model_output` on a turn | the model's JSON failed validation after retries; the turn is stored with `analysis_status=failed` |
| cue status `format_invalid` | cue JSON was invalid; no cue is delivered |
| `503` "embedding model could not be loaded" | `python scripts/download_embedding_model.py`, or `EMBEDDING_PROVIDER=hashing` for demos |
| `503` "index was built with a different embedding model" | `POST /papers/{id}/reindex` or **로컬 인덱스 다시 생성** |

## Limitations of local LLM output

Small local models can misclassify dialogue acts, miss or over-extract evidence, and word cues awkwardly. PaperCue limits the damage: evidence must quote the listener verbatim, values come from fixed vocabularies, confidence is assigned by code, the model cannot write beliefs directly, and every cue passes deterministic grounding/safety checks. It cannot guarantee *good* cues, though. The mock provider is a keyword rule set for testing the architecture only. Quality with a real model has not been evaluated.

## Assumptions

- Text input only. Timestamps default to the server's UTC time.
- The profile is replaced as a whole on each `POST /profile`. Earlier profile priors with no conversational support are dropped.
- **Reset** keeps the session and profile and rebuilds the profile priors. **Delete** removes everything.
- A paper cannot be deleted while sessions reference it.
- Retention is enforced by a manual purge (endpoint/script) or `PURGE_EXPIRED_ON_STARTUP=true`. There is no scheduler.
- Cue language is per session (`en` or `ko`). Internal identifiers and stored rationales are English; the dashboard presents Korean.
- After each listener turn, a "shadow" intervention evaluation (retrieval and scoring only, no generation, no delivery) is stored in the turn trace for inspection (`SHADOW_DECISION_ON_TURN`).
