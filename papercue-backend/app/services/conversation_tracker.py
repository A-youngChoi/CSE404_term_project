"""Conversation State Tracker.

* The local model classifies only the latest turn, given a bounded recent-turn window and
  a structured summary of older turns (never the whole conversation).
* Deterministic code merges that classification into the stored ConversationState.
* Turns leaving the window are compressed into searchable memory rows (Memoro-style).
"""

from __future__ import annotations

import numpy as np

from app.core.config import Settings
from app.core.utils import normalize, words
from app.llm.base import LocalLLMProvider
from app.models.conversation import ConversationState, MemoryHit, OlderContextSummary, Turn, UnresolvedIssue
from app.models.cue import RetrievedUnit
from app.models.enums import ListenerAct, PresenterAct, Speaker
from app.models.llm_outputs import ConversationStateOutput
from app.repositories.audience import AudienceRepository
from app.repositories.conversation import ConversationRepository
from app.safety.sensitive import characterization_hits, sensitive_hits
from app.services.embeddings import Embedder, cosine_scores

GIST_WORDS = 25
MEMORY_MIN_SCORE = 0.2


def compact_state(state: ConversationState) -> dict:
    return state.model_dump(mode="json", exclude={"recent_cues"})


class ConversationTracker:
    def __init__(self, settings: Settings, repo: ConversationRepository, audience_repo: AudienceRepository,
                 embedder: Embedder):
        self.settings = settings
        self.repo = repo
        self.audience_repo = audience_repo
        self.embedder = embedder

    # ------------------------------------------------------------ context
    def recent_window(self, session_id: str) -> list[Turn]:
        return self.repo.recent_turns(session_id, self.settings.recent_turn_window)

    def older_summary(self, session_id: str) -> OlderContextSummary:
        rows = self.repo.list_summaries(session_id)
        topics: list[str] = []
        concerns: list[str] = []
        explained: list[str] = []
        questions = 0
        for r in rows:
            if r["topic"] and r["topic"] not in topics:
                topics.append(r["topic"])
            for c in r["concerns"]:
                if c not in concerns:
                    concerns.append(c)
            if r["speaker"] == "listener" and r["dialogue_act"] in ("question", "clarification_request", "challenge"):
                questions += 1
        for s in self.repo.list_states(session_id)[-1:]:
            explained = s["state"].get("recently_explained_unit_ids", [])
        return OlderContextSummary(summarized_turns=len(rows), topics_covered=topics[-8:],
                                   concerns_raised=concerns, listener_questions=questions,
                                   explained_unit_ids=explained)

    # ------------------------------------------------------------ update
    def classify(self, provider: LocalLLMProvider, prev: ConversationState, turn: Turn,
                 candidates: list[RetrievedUnit]) -> ConversationStateOutput:
        window = [t for t in self.recent_window(turn.session_id) if t.id != turn.id]
        payload = {
            "previous_state": compact_state(prev),
            "recent_turns": [{"turn_index": t.turn_index, "speaker": t.speaker.value, "text": t.text[:300]}
                             for t in window],
            "older_summary": self.older_summary(turn.session_id).model_dump(),
            "candidate_topics": [{"title": c.title, "unit_type": c.unit_type, "keywords": c.keywords[:5]}
                                 for c in candidates[:3]],
            "latest_turn": {"turn_index": turn.turn_index, "speaker": turn.speaker.value, "text": turn.text},
        }
        return provider.generate_structured("conversation_state", payload, ConversationStateOutput)

    def merge(self, prev: ConversationState, turn: Turn, out: ConversationStateOutput,
              candidates: list[RetrievedUnit]) -> tuple[ConversationState, list[str]]:
        errors: list[str] = []
        state = prev.model_copy(deep=True)
        state.turn_count += 1
        state.turns_since_last_cue += 1
        state.last_speaker = turn.speaker
        state.phase = out.phase

        def safe(value: str | None, code: str) -> str | None:
            if value and (sensitive_hits(value) or characterization_hits(value, include_assertions=False)):
                errors.append(code)
                return None
            return value

        topic = safe(out.topic, "sensitive_topic_dropped")
        if topic:
            state.topic = topic

        if turn.speaker == Speaker.listener:
            state.listener_turn_count += 1
            state.last_listener_turn_id = turn.id
            state.listener_act = out.listener_act
            if out.listener_act in (ListenerAct.question, ListenerAct.clarification_request, ListenerAct.challenge):
                question = out.explicit_question or turn.text
                if normalize(question) not in normalize(turn.text):
                    errors.append("explicit_question_not_in_turn")
                    question = turn.text
                state.explicit_question = question[:300]
                state.explicit_question_turn_id = turn.id
            else:
                state.explicit_question = None
                state.explicit_question_turn_id = None
            concern = safe(out.detected_concern, "sensitive_concern_dropped")
            if concern:
                concern = concern.lower()
                state.detected_concerns = [c for c in state.detected_concerns if c != concern][-4:] + [concern]
            issue = safe(out.unresolved_issue, "sensitive_issue_dropped")
            if issue:
                issue = issue.lower()
                if not any(i.topic == issue for i in state.unresolved_issues):
                    state.unresolved_issues.append(UnresolvedIssue(topic=issue, raised_turn_id=turn.id))
                else:
                    for i in state.unresolved_issues:
                        if i.topic == issue:
                            i.status = "open"
            if out.issue_resolved and state.unresolved_issues:
                # The most recent open/addressed issue is considered settled by the listener.
                state.unresolved_issues.pop()
            state.possible_misunderstanding = safe(out.possible_misunderstanding, "sensitive_misunderstanding_dropped")
        else:
            state.presenter_act = out.presenter_act
            explained = [c.unit_id for c in candidates if c.similarity >= self.settings.explained_unit_threshold]
            if explained:
                merged = [u for u in state.recently_explained_unit_ids if u not in explained] + explained
                state.recently_explained_unit_ids = merged[-5:]
            if out.presenter_act in (PresenterAct.answer, PresenterAct.explanation, PresenterAct.example):
                for i in reversed(state.unresolved_issues):
                    if i.status == "open":
                        i.status = "addressed"
                        break
        state.unresolved_issues = state.unresolved_issues[-5:]
        return state, errors

    # ------------------------------------------------------------ memory
    def summarize_old_turns(self, session_id: str) -> int:
        """Move turns that left the recent window into structured, searchable memory."""
        turns = self.repo.unsummarized_outside_window(session_id, self.settings.recent_turn_window)
        if not turns:
            return 0
        evidence = self.audience_repo.list_evidence(session_id)
        gists = []
        for t in turns:
            gist = " ".join(t.text.split()[:GIST_WORDS])
            st = self.repo.state_for_turn(t.id)
            topic = st.topic if st else None
            gists.append(f"{topic or ''} {gist}")
        vectors = self.embedder.embed(gists)
        for t, vec in zip(turns, vectors):
            st = self.repo.state_for_turn(t.id)
            act = None
            if st:
                act = st.listener_act.value if t.speaker == Speaker.listener else st.presenter_act.value
            concerns = sorted({e.key for e in evidence if e.turn_id == t.id and e.dimension.value == "concern"})
            self.repo.add_summary(session_id=session_id, turn=t, topic=st.topic if st else None, dialogue_act=act,
                                  concerns=concerns, gist=" ".join(t.text.split()[:GIST_WORDS]),
                                  model_name=self.embedder.name, vector=vec)
        return len(turns)

    def search_memory(self, session_id: str, query: str, k: int = 2) -> list[MemoryHit]:
        rows = [r for r in self.repo.list_summaries(session_id)
                if r["vector"] is not None and r["model_name"] == self.embedder.name]
        if not rows or not words(query):
            return []
        sims = cosine_scores(self.embedder.embed([query])[0], np.vstack([r["vector"] for r in rows]))
        ranked = sorted(zip(sims, rows), key=lambda t: float(t[0]), reverse=True)[:k]
        return [
            MemoryHit(summary_id=r["id"], turn_id=r["turn_id"], turn_index=r["turn_index"], speaker=r["speaker"],
                      topic=r["topic"], gist=r["gist"], score=round(float(s), 4))
            for s, r in ranked
            if float(s) >= MEMORY_MIN_SCORE
        ]
