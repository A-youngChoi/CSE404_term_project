"""Evolving Audience Model Store and its deterministic update policy.

The local LLM may *propose* which belief a piece of evidence bears on. Only this module
writes beliefs, and every write produces an audit row in audience_belief_history.

Policy summary
--------------
* Evidence types carry different weights: explicit > behavioral > weak_inference > profile.
* New belief: confidence = initial weight of the evidence type (profile priors are capped).
* Supporting evidence: confidence moves toward the type's cap by a type-specific step,
  so repeated evidence accumulates and weak evidence changes confidence only gradually.
  Conversation evidence that supports a profile prior converts it into a conversation belief.
* Contradicting evidence:
    - explicit or behavioral conversation evidence overrides a profile prior immediately;
    - explicit evidence overrides a non-explicit conversation belief;
    - a newer explicit self-report reverses an older one (at reduced confidence);
    - otherwise the first contradiction only lowers confidence and marks the belief
      "contested"; a second contradiction toward the same value reverses it.
* Profile priors decay with every listener turn in which they are not reinforced.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.utils import clamp
from app.models.audience import AudienceModelView, Belief, BeliefChange, EvidenceItem
from app.models.enums import DIMENSION_VALUES, Dimension, EvidenceType
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository


@dataclass(frozen=True)
class EvidencePolicy:
    initial: float
    cap: float
    step: float


POLICIES: dict[EvidenceType, EvidencePolicy] = {
    EvidenceType.explicit: EvidencePolicy(initial=0.90, cap=0.97, step=0.5),
    EvidenceType.behavioral: EvidencePolicy(initial=0.50, cap=0.80, step=0.25),
    EvidenceType.weak_inference: EvidencePolicy(initial=0.30, cap=0.50, step=0.10),
}
PRIORITY = {EvidenceType.profile: 0, EvidenceType.weak_inference: 1, EvidenceType.behavioral: 2, EvidenceType.explicit: 3}
MIN_CONFIDENCE = 0.05
STALE_PROFILE_THRESHOLD = 0.3


class AudienceModelService:
    def __init__(self, settings: Settings, repo: AudienceRepository, conversation: ConversationRepository):
        self.settings = settings
        self.repo = repo
        self.conversation = conversation

    def policy(self, etype: EvidenceType) -> EvidencePolicy:
        if etype == EvidenceType.profile:
            cap = self.settings.profile_confidence_cap
            return EvidencePolicy(initial=cap, cap=cap, step=0.1)
        return POLICIES[etype]

    # ------------------------------------------------------------ update
    def apply(self, ev: EvidenceItem, *, key: str, value: str, reason: str, listener_turn: int) -> BeliefChange | None:
        dim = ev.dimension
        if value not in DIMENSION_VALUES[dim]:
            return None
        etype = ev.evidence_type
        pol = self.policy(etype)
        existing = self.repo.get_belief(ev.session_id, dim.value, key)

        if existing is None:
            conf = min(ev.confidence, pol.cap) if etype == EvidenceType.profile else pol.initial
            row = {
                "session_id": ev.session_id, "dimension": dim.value, "key": key, "value": value,
                "confidence": round(conf, 4), "status": "active", "source_type": ev.source_type,
                "source_id": ev.source_id, "evidence_ids": [ev.id], "support_count": 1, "contradict_count": 0,
                "pending_value": None, "last_evidence_type": etype.value, "reason": reason, "turn_index": listener_turn,
            }
            bid = self.repo.insert_belief(row)
            return self._history(ev, bid, key, "created", None, value, None, row["confidence"], reason)

        old_value, old_conf = existing["value"], existing["confidence"]
        old_type = EvidenceType(existing["last_evidence_type"])
        row = dict(existing)
        row["evidence_ids"] = [*existing["evidence_ids"], ev.id][-20:]

        if etype == EvidenceType.profile and existing["source_type"] != "profile":
            return None  # conversation evidence always outranks a profile prior

        if old_value == value:
            if existing["source_type"] == "profile" and etype != EvidenceType.profile:
                new_conf = max(pol.initial, old_conf + pol.step * (pol.cap - old_conf))
                change = "confirmed"
                row["source_type"] = "conversation"
            else:
                new_conf = old_conf + pol.step * (pol.cap - old_conf) if pol.cap > old_conf else old_conf
                change = "reinforced"
            row.update(confidence=round(clamp(new_conf), 4), status="active", pending_value=None,
                       support_count=existing["support_count"] + 1, turn_index=listener_turn)
            if etype != EvidenceType.profile:
                row["source_id"] = ev.source_id
            if PRIORITY[etype] >= PRIORITY[old_type]:
                row["last_evidence_type"] = etype.value
        else:
            row["contradict_count"] = existing["contradict_count"] + 1
            conversation_strong = etype in (EvidenceType.explicit, EvidenceType.behavioral)
            if existing["source_type"] == "profile" and conversation_strong:
                change, new_value, new_conf = "overridden", value, pol.initial
            elif etype == EvidenceType.explicit and old_type != EvidenceType.explicit:
                change, new_value, new_conf = "overridden", value, pol.initial
            elif etype == EvidenceType.explicit:
                change, new_value, new_conf = "reversed", value, pol.initial * 0.8
            elif existing["status"] == "contested" and existing["pending_value"] == value:
                weakened = old_conf * (1 - pol.step)
                if weakened < pol.initial:
                    change, new_value, new_conf = "reversed", value, pol.initial * 0.8
                else:
                    change, new_value, new_conf = "weakened", old_value, weakened
            else:
                change, new_value, new_conf = "weakened", old_value, old_conf * (1 - pol.step)

            if change == "weakened":
                row.update(status="contested", pending_value=value)
            else:
                row.update(value=new_value, status="active", pending_value=None, source_type=ev.source_type,
                           source_id=ev.source_id, support_count=1, contradict_count=0,
                           last_evidence_type=etype.value, turn_index=listener_turn)
            row["confidence"] = round(max(MIN_CONFIDENCE, clamp(new_conf)), 4)

        row["reason"] = reason
        self.repo.update_belief(existing["id"], row)
        return self._history(ev, existing["id"], key, change, old_value, row["value"], old_conf, row["confidence"], reason)

    def _history(self, ev: EvidenceItem, belief_id: str, key: str, change: str, old_value, new_value, old_conf,
                 new_conf, reason) -> BeliefChange:
        return self.repo.add_history(
            session_id=ev.session_id, belief_id=belief_id, dimension=ev.dimension.value, key=key, change_type=change,
            old_value=old_value, new_value=new_value, old_confidence=old_conf, new_confidence=new_conf,
            evidence_id=ev.id, source_type=ev.source_type, source_id=ev.source_id, reason=reason,
        )

    # ------------------------------------------------------------ read
    def beliefs(self, session_id: str, listener_turns: int | None = None) -> list[Belief]:
        if listener_turns is None:
            listener_turns = self.conversation.latest_state(session_id).listener_turn_count
        turn_index_by_id = {t.id: t.turn_index for t in self.conversation.list_turns(session_id)}
        out = []
        for r in self.repo.list_beliefs(session_id):
            conf = r["confidence"]
            if r["source_type"] == "profile":
                idle = max(0, listener_turns - r["turn_index"])
                effective = conf * (self.settings.profile_decay_per_turn ** idle)
            else:
                effective = conf
            if r["source_type"] == "profile":
                lifecycle = "stale" if effective < STALE_PROFILE_THRESHOLD else "profile_hypothesis"
            elif r["status"] == "contested":
                lifecycle = "conflicting"
            elif r["last_evidence_type"] == EvidenceType.explicit.value:
                lifecycle = "explicitly_confirmed"
            else:
                lifecycle = "conversation_supported"
            if r["status"] == "contested":
                lifecycle = "conflicting"
            out.append(
                Belief(
                    id=r["id"], session_id=r["session_id"], dimension=r["dimension"], key=r["key"], value=r["value"],
                    confidence=conf, effective_confidence=round(effective, 4), uncertainty=round(1 - effective, 4),
                    status=r["status"], lifecycle=lifecycle, source_type=r["source_type"], source_id=r["source_id"],
                    source_turn_index=turn_index_by_id.get(r["source_id"]), evidence_ids=r["evidence_ids"],
                    support_count=r["support_count"], contradict_count=r["contradict_count"],
                    pending_value=r["pending_value"], last_evidence_type=r["last_evidence_type"],
                    reason=r["reason"], created_at=r["created_at"], updated_at=r["updated_at"],
                )
            )
        return out

    def view(self, session_id: str) -> AudienceModelView:
        state = self.conversation.latest_state(session_id)
        beliefs = self.beliefs(session_id, state.listener_turn_count)
        dims: dict[str, list[Belief]] = {d.value: [] for d in Dimension}
        for b in beliefs:
            dims[b.dimension.value].append(b)
        unknown = [d for d, items in dims.items() if not items]
        # Unknown dimensions count as fully uncertain.
        per_dim = [1 - max(b.effective_confidence for b in items) if items else 1.0 for items in dims.values()]
        return AudienceModelView(
            session_id=session_id, dimensions=dims, unknown_dimensions=unknown,
            overall_uncertainty=round(sum(per_dim) / len(per_dim), 4),
            listener_turns_observed=state.listener_turn_count,
        )
