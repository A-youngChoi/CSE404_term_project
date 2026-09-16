-- PaperCue local SQLite schema (v2).
-- All IDs are UUID strings. All timestamps are ISO-8601 UTC strings.
-- Every session-scoped table references sessions(id) ON DELETE CASCADE so that
-- deleting a session removes all of its derived data.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- papers
CREATE TABLE IF NOT EXISTS papers (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    abstract              TEXT NOT NULL DEFAULT '',
    full_text             TEXT,
    key_contributions     TEXT NOT NULL DEFAULT '[]',  -- JSON list
    evidence_results      TEXT NOT NULL DEFAULT '[]',  -- JSON list
    limitations           TEXT NOT NULL DEFAULT '[]',  -- JSON list
    preferred_terminology TEXT NOT NULL DEFAULT '{}',  -- JSON object
    forbidden_claims      TEXT NOT NULL DEFAULT '[]',  -- JSON list
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_units (
    id                 TEXT PRIMARY KEY,
    paper_id           TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    unit_type          TEXT NOT NULL,
    title              TEXT NOT NULL,
    content            TEXT NOT NULL,
    short_explanation  TEXT NOT NULL,
    keywords           TEXT NOT NULL DEFAULT '[]',
    source_reference   TEXT,
    presenter_priority REAL NOT NULL DEFAULT 0.5,
    origin             TEXT NOT NULL,   -- presenter | heuristic | llm | mock
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_units_paper ON paper_units(paper_id);

CREATE TABLE IF NOT EXISTS paper_embeddings (
    unit_id     TEXT PRIMARY KEY REFERENCES paper_units(id) ON DELETE CASCADE,
    paper_id    TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    model_name  TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,          -- float32 little-endian
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emb_paper ON paper_embeddings(paper_id);

-- ---------------------------------------------------------------- sessions
CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    paper_id          TEXT NOT NULL REFERENCES papers(id) ON DELETE RESTRICT,
    mode              TEXT NOT NULL CHECK (mode IN ('on_demand', 'auto_candidate')),
    llm_provider      TEXT NOT NULL CHECK (llm_provider IN ('ollama', 'mock')),
    cue_language      TEXT NOT NULL DEFAULT 'en' CHECK (cue_language IN ('en', 'ko')),
    consent_confirmed INTEGER NOT NULL CHECK (consent_confirmed = 1),
    consent_note      TEXT NOT NULL,
    label             TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    turn_count        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listener_profiles (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    fields      TEXT NOT NULL,          -- JSON of manually supplied fields
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index       INTEGER NOT NULL,
    speaker          TEXT NOT NULL CHECK (speaker IN ('listener', 'presenter')),
    text             TEXT NOT NULL,
    spoken_at        TEXT NOT NULL,
    input_source     TEXT NOT NULL DEFAULT 'text',
    analysis_status  TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | failed
    summarized       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    UNIQUE (session_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id, turn_index);

-- Older turns that left the recent window, compressed into structured, searchable memory.
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id      TEXT NOT NULL REFERENCES conversation_turns(id) ON DELETE CASCADE,
    turn_index   INTEGER NOT NULL,
    speaker      TEXT NOT NULL,
    topic        TEXT,
    dialogue_act TEXT,
    concerns     TEXT NOT NULL DEFAULT '[]',
    gist         TEXT NOT NULL,
    model_name   TEXT,
    vector       BLOB,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summ_session ON conversation_summaries(session_id);

CREATE TABLE IF NOT EXISTS conversation_states (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id     TEXT REFERENCES conversation_turns(id) ON DELETE CASCADE,
    state       TEXT NOT NULL,          -- JSON ConversationState
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_states_session ON conversation_states(session_id, created_at);

CREATE TABLE IF NOT EXISTS evidence_items (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id       TEXT REFERENCES conversation_turns(id) ON DELETE CASCADE,
    source_type   TEXT NOT NULL,        -- conversation | profile
    source_id     TEXT NOT NULL,        -- turn id or profile field reference
    dimension     TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    evidence_type TEXT NOT NULL,        -- explicit | behavioral | weak_inference | profile
    confidence    REAL NOT NULL,
    quote         TEXT,
    observation   TEXT NOT NULL,
    extractor     TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence_items(session_id);

CREATE TABLE IF NOT EXISTS audience_beliefs (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    dimension         TEXT NOT NULL,
    key               TEXT NOT NULL,
    value             TEXT NOT NULL,
    confidence        REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',  -- active | contested
    source_type       TEXT NOT NULL,                   -- profile | conversation
    source_id         TEXT NOT NULL,
    evidence_ids      TEXT NOT NULL DEFAULT '[]',
    support_count     INTEGER NOT NULL DEFAULT 1,
    contradict_count  INTEGER NOT NULL DEFAULT 0,
    pending_value     TEXT,
    last_evidence_type TEXT NOT NULL,
    reason            TEXT NOT NULL,
    turn_index        INTEGER NOT NULL DEFAULT 0,       -- listener turn count at last support
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (session_id, dimension, key)
);

CREATE TABLE IF NOT EXISTS audience_belief_history (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    belief_id       TEXT NOT NULL REFERENCES audience_beliefs(id) ON DELETE CASCADE,
    change_type     TEXT NOT NULL,      -- created | reinforced | weakened | reversed | overridden
    old_value       TEXT,
    new_value       TEXT NOT NULL,
    old_confidence  REAL,
    new_confidence  REAL NOT NULL,
    evidence_id     TEXT,
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    reason          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hist_session ON audience_belief_history(session_id);

CREATE TABLE IF NOT EXISTS cue_decisions (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    trigger_turn_id    TEXT REFERENCES conversation_turns(id) ON DELETE CASCADE,
    mode               TEXT NOT NULL,
    should_intervene   INTEGER NOT NULL,
    action             TEXT,
    target             TEXT,
    confidence         REAL NOT NULL,
    short_reason       TEXT NOT NULL,
    reason_code        TEXT NOT NULL,
    final_status       TEXT NOT NULL,
    scores             TEXT NOT NULL,   -- JSON
    trace              TEXT NOT NULL,   -- JSON: state, beliefs, evidence, retrieval, memory
    latency_ms         TEXT NOT NULL,   -- JSON
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_session ON cue_decisions(session_id);

CREATE TABLE IF NOT EXISTS generated_cues (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    decision_id           TEXT NOT NULL REFERENCES cue_decisions(id) ON DELETE CASCADE,
    cue                   TEXT,
    candidate_cue         TEXT,
    action                TEXT,
    grounding_unit_ids    TEXT NOT NULL DEFAULT '[]',
    audience_evidence_ids TEXT NOT NULL DEFAULT '[]',
    confidence            REAL NOT NULL DEFAULT 0,
    rationale             TEXT,
    passed_filter         INTEGER NOT NULL,
    filter_results        TEXT NOT NULL,   -- JSON list of checks
    fallback_used         INTEGER NOT NULL DEFAULT 0,
    delivered             INTEGER NOT NULL DEFAULT 0,
    final_status          TEXT NOT NULL,
    generator             TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cues_session ON generated_cues(session_id);

-- Only populated when DEBUG_STORE_PROMPTS=true. Never enable with participant data.
CREATE TABLE IF NOT EXISTS llm_debug_prompts (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    response    TEXT,
    created_at  TEXT NOT NULL
);

-- Structured processing traces. Stages hold reference IDs, statuses, scores and short
-- rationales only; raw conversation text is not duplicated here.
CREATE TABLE IF NOT EXISTS pipeline_traces (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id           TEXT REFERENCES conversation_turns(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL,          -- turn | cue | auto_candidate
    decision_id       TEXT,
    pipeline_version  TEXT NOT NULL,
    status            TEXT NOT NULL,          -- success | failed
    stages            TEXT NOT NULL,          -- JSON list of TraceStage
    started_at        TEXT NOT NULL,
    completed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON pipeline_traces(session_id, started_at);

-- Local processing errors: codes and IDs only.
CREATE TABLE IF NOT EXISTS processing_errors (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,
    error_code  TEXT NOT NULL,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Minimal audit trail. Contains IDs and counts only, never content.
CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,   -- session_deleted | session_reset | paper_deleted | retention_purge | session_exported
    subject_id  TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
