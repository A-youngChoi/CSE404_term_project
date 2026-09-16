"""Deterministic grounding and safety filter."""

from __future__ import annotations

from app.models.enums import CueAction
from app.models.llm_outputs import CueGenerationOutput
from app.safety.cue_filter import FilterContext, run_cue_filter

UNIT_TEXT = "Local processing and data storage. PaperCue processes conversation data locally and does not upload raw audio."


def ctx(**kw) -> FilterContext:
    base = dict(decided_action=CueAction.address_concern, retrieved_units={"u1": UNIT_TEXT},
                known_evidence_ids={"e1"}, recent_cues=[], forbidden_claims=["validated in a user study"])
    base.update(kw)
    return FilterContext(**base)


def cue(text: str, **kw) -> CueGenerationOutput:
    base = dict(cue=text, action=CueAction.address_concern, grounding_unit_ids=["u1"],
                audience_evidence_ids=["e1"], confidence=0.85, rationale="Addresses the storage question.")
    base.update(kw)
    return CueGenerationOutput(**base)


def failed(outcome) -> set[str]:
    return set(outcome.failed_names)


def test_good_cue_passes():
    out = run_cue_filter(cue("privacy first—local processing"), ctx())
    assert out.passed and out.status == "deliverable"


def test_word_limit_enforced():
    long = "privacy first then explain local processing and storage and deletion and export options now"
    out = run_cue_filter(cue(long), ctx())
    assert "word_limit" in failed(out) and out.status == "format_invalid"


def test_full_answer_rejected():
    out = run_cue_filter(cue("We process everything locally. Nothing is uploaded."), ctx())
    assert "cue_not_answer" in failed(out)


def test_unknown_grounding_ids_rejected():
    out = run_cue_filter(cue("privacy first—local processing", grounding_unit_ids=["u999"]), ctx())
    assert "grounding_ids_valid" in failed(out) and out.status == "insufficient_grounding"


def test_missing_grounding_rejected_for_grounded_action():
    out = run_cue_filter(cue("privacy first—local processing", grounding_unit_ids=[]), ctx())
    assert "grounding_ids_valid" in failed(out)


def test_invented_numbers_and_claims_rejected():
    out = run_cue_filter(cue("mention 95% accuracy result"), ctx())
    assert "no_unsupported_claims" in failed(out)
    out = run_cue_filter(cue("say it outperforms cloud tools"), ctx())
    assert "no_unsupported_claims" in failed(out)


def test_forbidden_claim_rejected():
    out = run_cue_filter(cue("say validated in a user study"), ctx())
    assert "no_forbidden_claims" in failed(out)


def test_sensitive_attribute_inference_rejected():
    for text in ("their anxiety explains the question", "avoid political topics with them", "우울한 청중 배려"):
        out = run_cue_filter(cue(text), ctx())
        assert "no_sensitive_inference" in failed(out), text
        assert out.status == "sensitive_risk"


def test_listener_characterization_rejected():
    for text in ("they are skeptical and technically weak", "listener seems confused", "청중은 회의적이다"):
        out = run_cue_filter(cue(text), ctx())
        assert "audience_claims_supported" in failed(out), text


def test_unknown_evidence_ids_rejected():
    out = run_cue_filter(cue("privacy first—local processing", audience_evidence_ids=["nope"]), ctx())
    assert "audience_claims_supported" in failed(out)


def test_manipulation_rejected():
    out = run_cue_filter(cue("convince them using their fear"), ctx())
    assert "no_manipulation" in failed(out)


def test_vague_cue_rejected():
    assert "not_vague" in failed(run_cue_filter(cue("explain better"), ctx()))


def test_repeated_cue_suppressed():
    out = run_cue_filter(cue("privacy first—local processing"), ctx(recent_cues=["Privacy first — local processing"]))
    assert "not_repeated" in failed(out) and out.status == "duplicate_recent"


def test_low_confidence_held():
    out = run_cue_filter(cue("privacy first—local processing", confidence=0.2), ctx())
    assert out.status == "held_low_confidence"


def test_action_mismatch_rejected():
    out = run_cue_filter(cue("privacy first—local processing", action=CueAction.simplify), ctx())
    assert "action_matches_decision" in failed(out)


def test_verification_cue_needs_no_grounding():
    v = cue("ask their main concern", action=CueAction.verify, grounding_unit_ids=[], audience_evidence_ids=[])
    out = run_cue_filter(v, ctx(decided_action=CueAction.verify))
    assert out.passed


def test_korean_cue_word_count():
    out = run_cue_filter(cue("개인정보 먼저—로컬 처리"), ctx(retrieved_units={"u1": "로컬 처리 개인정보"}))
    assert out.passed
    assert any(c.name == "word_limit" and c.detail.startswith("4 words") for c in out.checks)
