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
