# PaperCue architecture

## Pipeline

```text
Presenter's paper ──► PaperService ──► PaperUnits ──► local embeddings (paper_embeddings)
Manual profile ─────► ProfileService ─► profile evidence ─► uncertain priors (audience_beliefs)

Typed turn ─► ConversationTracker ─► EvidenceExtractor ─► update proposal ─► AudienceModelService
  (sense)      state + memory          validated evidence    (LLM: key mapping)   (deterministic policy)
                                                                                         │
on-demand request ─► RetrievalService ─► CueDecisionEngine ─► CueGenerator ─► cue_filter ─► one cue / no cue
                      (predict)            (deterministic)      (adapt, LLM)    (deterministic)
```

Every run writes a `PipelineTrace` with twelve stages: input turn, recent context, dialogue-act analysis, evidence extraction, update proposal, applied update, retrieval query, retrieval, intervention decision, cue candidate, safety/grounding check, and final result.

| Module | File | Deterministic? |
|---|---|---|
| Paper ingestion | `app/services/paper_ingestion.py` | splitting yes; chunk labelling uses the local LLM (content is never rewritten, keywords must occur in the chunk) |
| Embeddings / index | `app/services/embeddings.py` | local model or hashing mock |
| Profile priors | `app/services/profile_service.py` | yes |
| Conversation state | `app/services/conversation_tracker.py` | LLM classifies the latest turn; merge, sanitising, and memory are deterministic |
| Evidence extraction | `app/services/evidence_extractor.py` | LLM proposes; code validates quote, vocabulary, and sensitivity, and assigns confidence |
| Audience model | `app/services/audience_model.py` | yes (LLM may only map evidence to belief keys) |
| Retrieval | `app/services/retrieval.py` | yes |
| Decision | `app/services/cue_decision.py` | yes |
| Cue wording | `app/services/cue_generator.py` | local LLM (or mock) |
| Safety/grounding filter | `app/safety/cue_filter.py`, `app/safety/sensitive.py` | yes |
| Orchestration + traces | `app/services/pipeline.py`, `app/services/tracing.py` | yes |
| Prompts | `app/prompts/v1.py` | versioned, one per task |
| Providers | `app/llm/ollama.py`, `app/llm/mock.py` | Ollama = LLM; mock = rules |

## How Memoro's principles were adapted

- **Bounded recent context.** The tracker passes only the last `RECENT_TURN_WINDOW` turns (default 6) to the model.
- **Older content becomes searchable memory.** Turns leaving the window become `conversation_summaries` rows (topic, act, concerns, short gist, and an embedding). A structured digest is passed instead of the transcript, and `search_memory` finds related earlier turns for the cue trace.
- **Retrieve only what is relevant.** The retrieval query combines the latest question, the topic, open issues, concerns, and the intended action. Only the top few units (default 4) reach the generator, never the full paper.
- **Separate interpretation, retrieval, and generation.** These are distinct services with distinct prompts and trace stages.
- **Concise, minimally disruptive output.** Cues are 2–7 words, with a hard cap of 12. Interruption cost is part of the intervention score.
- **On-demand before proactive.** `on_demand` is the default. `auto_candidate` scores a would-be cue but never delivers it. A per-turn "shadow" evaluation exists only for inspection.

## How the sense → predict → adapt loop was adapted

This follows Janarthanam & Lemon's dynamic user modelling.

1. **Sense.** The conversation state records dialogue acts, questions, concerns, and misunderstandings. Evidence items are typed as `explicit`, `behavioral`, or `weak_inference`, and each must quote the listener's words.
2. **Update beliefs.** The deterministic policy works as follows:
   - Explicit evidence outranks behavioral, which outranks weak inference, which outranks profile priors.
   - Repeated support accumulates toward a type-specific cap.
   - Weak evidence moves confidence slowly.
   - Strong conversational evidence overrides profile priors immediately. Other contradictions first weaken a belief (status `contested`) and reverse it only on repetition.
   - Profile priors decay each listener turn they are not reinforced.
   - Each change writes an `audience_belief_history` row.
3. **Predict.** `CueDecisionEngine` turns state and beliefs into candidate actions (address concern, recover, acknowledge limitation/reframe, simplify, emphasize, connect, elaborate, close, verify). It picks the highest-need candidate and scores it with the weights in `WEIGHTS`. If grounding is weak, it switches to a verification action.
4. **Adapt.** The generator words the action for this listener and paper. The filter enforces grounding and safety. If grounding or confidence fails, a fixed verification cue replaces the candidate; otherwise no cue is returned.

## Why the audience model and the cue generator are separate

The audience model is long-lived, auditable state; the cue is a momentary rendering. Keeping them apart means:

- The LLM never writes beliefs.
- Each belief can be traced to evidence.
- Cue wording can be swapped (another model, another language, or TTS later) without touching modelling.
- Failures stay contained. A bad cue is filtered; it does not corrupt the model.

## Why profile information is a prior

A profile describes what someone *has worked on*, not what they know or want *now*. It is manually supplied, may be stale, and can encode assumptions. Profile items therefore:

- enter as `profile` evidence capped below `PROFILE_CONFIDENCE_CAP`;
- decay over listener turns;
- are overridden by explicit or behavioral conversational evidence;
- are shown with dashed/grey styling in the dashboard.

## Adding mobile, ASR and TTS later

`app/adapters/interfaces.py` defines the `SpeechRecognizer`, `SpeakerDiarizer`, `TurnSource`, and `CueDeliverer` protocols. A future adapter produces `TurnIn(speaker, text)` and calls `PipelineService.process_turn(...)` with `input_source="asr"`, then calls `request_cue(...)` and passes approved cue text to a `CueDeliverer`. The reasoning core does not change. Constraints that still apply:

- no raw audio persisted;
- local ASR/TTS only;
- mobile transport needs its own authentication and threat review before any non-loopback binding.

## Dashboard

`dashboard/` is a Vite + vanilla JavaScript app, built into `dashboard/dist` and served by FastAPI at `/dashboard/` under a strict CSP.

- Korean strings live in `src/i18n/ko.js`.
- `src/explain.js` converts stored structured records into Korean sentences. It never uses model reasoning.
- All data is rendered with `textContent`.

## Presentation-support simulation (`app/presentation/`)

A separate pipeline for proactive support during a talk. It shares configuration, the Ollama provider, the embedder,
logging and the dashboard shell with the listener-cue pipeline, but none of its state.

| Module | File | Role |
|---|---|---|
| Schemas | `schemas.py` | observed events, ground truth (separate), issues, context, memory items and changes, user-model attributes and changes, Judge decision, prompts, mobile state, step record |
| Dataset | `dataset.py` | loads `sample_data/presentation`, derives timestamps / slide content / remaining time, strips ground truth |
| Input adapters | `inputs.py` | `EventSource`, `TranscriptSource`, `SlideTracker`, `AudienceSignalSource`, `PresenterSignalSource`, `PromptDeliverer`; today only `DatasetEventSource` |
| Context | `context.py` | coverage of must-mention key points, slide timing and schedule lag, time budget, speech rate vs the user model, fillers, silence, audience signals, Q&A matching; persistent issues keyed by type + target (`DETECTOR_PARAMS`) |
| Memory | `memory.py` | working / episodic / semantic / intervention / reflection items with metadata; retrieval score = 0.5 relevance + 0.25 importance + 0.25 recency (0.995^s); rule-based reflections; every change logged with before/after |
| User model | `user_model.py` | profile priors → observed values with confidence, evidence and history (robust speech-rate EMA, tension and load, difficulties, missed content, prompt length, responsiveness Beta(1,1), effective/disruptive contexts) |
| Retrieval | `knowledge.py` | `KnowledgeRetriever` protocol; `KeywordRetriever` and `EmbeddingRetriever`; slide/target/category boosts; marks used vs unused chunks |
| Judge | `judge.py` | `rank_issues`, weighted utility (`WEIGHTS`), thresholds, cooldown and redundancy policy; `RuleBasedJudge` (mock) and `OllamaJudge` (validated, policy-enforced, rule fallback) |
| Prompts | `prompt_generator.py` | Korean/English templates filled from knowledge cues, three scored candidates, personalisation from the user model, optional local-LLM rewording with validation |
| Outcome | `outcome.py` | infers recovery from later events only (rules in `RULES_DOC`) |
| Engine | `engine.py` | per-event stages with timing and error isolation, delivery to the mobile state, timeline marks; deterministic reset/seek |
| Evaluation | `evaluation.py` | tolerance-window matching, precision/recall/F1, interruption and missed-critical rates, delay, recovery, prompt-type and decision agreement, language/scenario/type slices (`METHOD`) |
| Service / API | `service.py`, `app/api/routes_presentation.py` | in-memory run registry (LRU), playback state shared with the mobile view, batch evaluation cache, JSONL export |

Replacing the synthetic inputs: implement the protocols in `inputs.py`, assemble `ObservedEvent`s (the loader shows
which fields are derived), and feed them to `SimulationRun` through an `EventSource` that yields events as they arrive
instead of by index. Detectors in `context.py` assume the current signal semantics (speech rate in wpm/spm, silence in
seconds, audience attention/confusion in 0-1); a real sensor needs calibration of `DETECTOR_PARAMS`.
