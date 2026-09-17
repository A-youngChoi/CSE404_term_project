"""Input and output adapters for the presentation simulation.

Today every observation comes from the synthetic dataset (`DatasetEventSource`). Real channels
replace the edges only; the engine consumes `ObservedEvent`s and emits `MobileState`s:

    microphone ─► TranscriptSource (local STT) ──┐
    clicker/slides ─► SlideTracker ───────────────┤
    camera/room ─► AudienceSignalSource ──────────┼─► EventAssembler ─► ObservedEvent ─► SimulationEngine
    wearable ─► PresenterSignalSource ────────────┘                                          │
    phone ◄─ PromptDeliverer ◄─ MobileState ◄────────────────────────────────────────────────┘

Real implementations must keep the project's local-only guarantees (no raw audio or video
persisted, no remote services, loopback transport unless separately reviewed).
"""

from __future__ import annotations

from typing import Iterator, Protocol

from app.presentation.schemas import AudienceSignal, MobileState, ObservedEvent, PresentationSession, PresenterSignal


class EventSource(Protocol):
    """Yields observations in time order."""

    def events(self) -> Iterator[ObservedEvent]: ...


class TranscriptSource(Protocol):
    """TODO(stt): local speech-to-text returning (text, speech_rate, silence_duration, filler_count)."""

    def next_chunk(self) -> tuple[str, float, float, int]: ...


class SlideTracker(Protocol):
    """TODO(slides): current slide number from the presentation software or a clicker."""

    def current_slide(self) -> int: ...


class AudienceSignalSource(Protocol):
    """TODO(audience): aggregate attention/confusion estimate. Must not identify individuals."""

    def sample(self) -> AudienceSignal: ...


class PresenterSignalSource(Protocol):
    """TODO(wearable): presenter arousal estimate from a consented wearable."""

    def sample(self) -> PresenterSignal: ...


class PromptDeliverer(Protocol):
    """TODO(mobile): push the minimal mobile state to the presenter's paired phone."""

    def deliver(self, state: MobileState) -> None: ...


class DatasetEventSource:
    """Replays a synthetic session. The engine reads events by index so it can seek and step."""

    name = "dataset:synthetic"

    def __init__(self, session: PresentationSession):
        self.session = session

    def __len__(self) -> int:
        return len(self.session.events)

    def get(self, index: int) -> ObservedEvent:
        return self.session.events[index].model_copy(deep=True)

    def events(self) -> Iterator[ObservedEvent]:
        for i in range(len(self)):
            yield self.get(i)
