# PaperCue privacy notes

**PaperCue is a research prototype for consented research testing. It must not be used for covert listening.**

## Local-only data flow

```text
browser (127.0.0.1) ──► FastAPI (127.0.0.1:8000) ──► SQLite file (data/papercue.db)
                                   │
                                   ├──► Ollama (127.0.0.1:11434, local model)
                                   └──► sentence-transformer (in-process, local cache)
```

- The API binds to `127.0.0.1` and refuses other hosts unless explicitly overridden.
- CORS allows only loopback dashboard origins; wildcards are rejected.
- There are no remote AI, embedding, analytics, crash-reporting, or telemetry services. Hugging Face telemetry is disabled, and the embedding model loads offline by default.
- The dashboard loads no external scripts, fonts, or images, and it keeps no participant data in browser storage.
- **Exception:** FastAPI's optional Swagger page (`/docs`) fetches its own JS/CSS from a CDN. No data is sent. Set `API_DOCS_ENABLED=false` to disable it.
- The only intentional download is the one-time embedding-model weight download (`scripts/download_embedding_model.py`), which sends no participant data.

## What is stored (SQLite, owner-only file permissions, `secure_delete` on)

| Data | Table | Notes |
|---|---|---|
| Paper text and units | `papers`, `paper_units`, `paper_embeddings` | the presenter's own material |
| Session metadata and consent notice | `sessions` | includes the retention expiry |
| Manually supplied profile | `listener_profiles` | sensitive items are rejected and not stored |
| Typed turns | `conversation_turns` | the only place full utterances are stored |
| Older-turn memory | `conversation_summaries` | topic, act, concerns, gist (first 25 words), embedding |
| Conversation state snapshots | `conversation_states` | includes the listener's latest explicit question |
| Evidence | `evidence_items` | a short verbatim quote and a neutral observation |
| Beliefs and history | `audience_beliefs`, `audience_belief_history` | value, confidence, source, reason, timestamps |
| Cue decisions and cues | `cue_decisions`, `generated_cues` | reference IDs, scores, check results; no utterance text |
| Traces and error codes | `pipeline_traces`, `processing_errors` | IDs, statuses, durations, error codes |
| Audit | `audit_events` | event type, session ID, counts only |
| Debug prompts | `llm_debug_prompts` | **only** if `DEBUG_STORE_PROMPTS=true`. Never enable with participant data. |

Not stored: audio, hidden model reasoning, full prompts (by default), and environment configuration.

Uploaded paper files are saved as inert text in `PAPERS_DIR` with owner-only permissions. Exports are downloaded by the browser. Nothing is written server-side unless you save it yourself.

## Retention and deletion

- `RETENTION_DAYS` (default 7) sets each session's `expires_at`.
- Expired sessions reject new turns. They are removed by `POST /api/v1/retention/purge`, by `scripts/purge_expired.py --delete`, or at startup when `PURGE_EXPIRED_ON_STARTUP=true`. There is no background scheduler yet.
- `DELETE /api/v1/sessions/{id}` permanently removes the session and every derived record (the foreign keys cascade, followed by an explicit sweep). The dashboard requires a checkbox and the typed word `삭제`.
- `POST /reset` clears conversation-derived data but keeps the session and profile.
- To wipe everything, stop the server and delete the `data/` directory.

## Consent requirement

Session creation fails with HTTP 403 unless `consent_confirmed` is `true`. The consent notice is stored with the session and shown in the dashboard. Researchers remain responsible for obtaining and documenting real informed consent under their ethics approval. The flag records the researcher's confirmation; it is not a consent record.

## Prohibited inferences

The audience model is limited to familiarity, knowledge level, interests, goals, research connections, concerns, preferred explanation level, engagement, and unresolved issues. The system rejects:

- political beliefs, religion, ethnicity, sexuality, health conditions, disability, and psychological or personality judgments;
- trait words such as "skeptical" or "incompetent";
- sentences asserting what the listener *is*;
- manipulative strategies.

These checks run on profile fields, evidence keys and observations, state topics, update proposals, and cue text (`app/safety/sensitive.py`). A single question is never generalised into a stable trait. For example, a storage question produces a *current concern*, not "this person distrusts technology".

## Risks of conversation data

Conversations can reveal third parties, unpublished work, affiliations, and opinions. Even derived data (concerns, familiarity) is personal data about the listener. Model outputs can be wrong. Typed notes can contain more than intended.

## Safe research deployment practices

- Use mock mode and synthetic data for development. Never commit `data/`, `.env`, or exports (`.gitignore` and `scripts/security_check.py` enforce this).
- Keep `DEBUG_STORE_PROMPTS=false`, `PAPERCUE_HOST=127.0.0.1`, and `OLLAMA_ALLOW_REMOTE=false`.
- Use full-disk encryption and a locked user account on the research laptop.
- Enter only what the study protocol requires. Avoid names and identifying details in turns and labels.
- Export only when needed, store exports according to your data management plan, and delete sessions at the end of each study session or retention period.
- Tell participants what is recorded, how long it is kept, and how to request deletion.
