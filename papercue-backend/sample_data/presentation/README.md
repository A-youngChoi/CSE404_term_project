# Synthetic presentation dataset

Everything here is **synthetic**: fictional papers, fictional presenters, scripted signals.
It exists to exercise the presentation-support pipeline end to end, not to measure real performance.

## Files

| Path | Content |
|---|---|
| `knowledge/kb_gazeflow.json` | English knowledge base: claim, background, method, results, limitations, related work, script notes, slide deck with key points (`essential`, `cue`, `keywords`), expected Q&A, glossary |
| `knowledge/kb_soundmap.json` | Korean knowledge base with the same structure |
| `presenters.json` | Six fictional presenters with uncertain priors (baseline speech rate, filler rate, preferred prompt length, earlier episodes, traits) |
| `sessions/*.json` | 14 sessions, each a time-ordered event stream |

## Sessions

| ID | Lang | Scenario | Presenter level |
|---|---|---|---|
| S01_en_stable | en | stable talk | expert |
| S02_ko_missing_point | ko | forgot the baseline on one slide | novice |
| S03_en_slow_pace | en | speech much slower than the slide plan | intermediate |
| S04_ko_too_fast | ko | rushing and skipping essential content | intermediate |
| S05_en_filler_repetition | en | repeated phrase and fillers | novice |
| S06_ko_silence_block | ko | short pause, then a long blank | novice |
| S07_en_audience_confusion | en | audience confusion about jargon | intermediate |
| S08_ko_qa_misunderstanding | ko | answering a different question in Q&A | expert |
| S09_en_prompt_helped | en | two prompts followed by recovery | novice |
| S10_ko_unnecessary_prompt | ko | deliberate pauses that should not be interrupted | expert |
| S11_en_suppress_after_support | en | follow-ups suppressed after one prompt | intermediate |
| S12_ko_time_shortage | ko | running out of time late in the talk | intermediate |
| S13_en_mixed_signals | en | edge case: rambling Q&A answer | expert |
| S14_ko_late_self_recovery | ko | edge case: gap filled one sentence later | intermediate |

S13 and S14 were written **after** the detector and Judge thresholds were fixed, as a blind check.
They are expected to contain disagreements (one false negative and one false positive with the
default configuration). The other sessions were written alongside the thresholds, so their
metrics are optimistic.

## Event fields

Authoring is compact. Omitted fields get defaults, and the loader (`app/presentation/dataset.py`)
derives the rest.

| Field | Source |
|---|---|
| `event_id`, `elapsed_time`, `current_slide`, `transcript_chunk` | authored |
| `event_type` | authored: `speech` (default), `silence`, `audience_question`, `qa_answer` |
| `speech_rate` | authored: wpm for English, syllables per minute for Korean (unit taken from the presenter) |
| `silence_duration`, `filler_count`, `repeated_phrase {text, count}` | authored (defaults 0.5 s, 0, none) |
| `audience_signal {attention, confusion, reaction}` | authored (mock audience sensor) |
| `presenter_state {arousal}` | authored (mock wearable). The label is derived. |
| `audience_question` | authored on `audience_question` events |
| `timestamp`, `language`, `slide_title`, `slide_expected_content`, `remaining_time` | derived from the session and the knowledge base |
| `previous_interventions` | filled by the engine from its own state (the dataset does not script system behaviour) |
| `ground_truth {issue, expected_intervention, expected_prompt_type, expected_decision, expected_outcome, severity, target, note}` | authored; **removed from the event** and stored separately for evaluation only |
