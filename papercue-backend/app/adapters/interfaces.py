"""Adapter interfaces for future input/output channels. NOT implemented in this MVP.

The reasoning core only needs `TurnIn` objects (speaker + text) and returns short cue
strings, so later channels plug in at the edges without changing the pipeline:

    microphone/phone ──> SpeechRecognizer ──> SpeakerDiarizer ──> TurnSource ──┐
                                                                               ▼
                                                     PipelineService.process_turn(...)
                                                     PipelineService.request_cue(...)
                                                                               │
    earbud <── CueDeliverer (e.g. local TTS) <─────────────────────────────────┘

Every implementation must keep the local-only guarantees: no raw audio persisted,
no remote ASR/TTS services, loopback-only transport unless a separate security review
approves otherwise.
"""

from __future__ import annotations

from typing import Iterator, Protocol

from app.models.conversation import TurnIn


class SpeechRecognizer(Protocol):
    """TODO(asr): local speech-to-text (e.g. a local Whisper build). Must not store audio."""

    def transcribe(self, audio_chunk: bytes, sample_rate: int) -> str: ...


class SpeakerDiarizer(Protocol):
    """TODO(diarization): map transcribed segments to 'presenter' or 'listener'."""

    def label(self, text: str, audio_chunk: bytes | None = None) -> str: ...


class TurnSource(Protocol):
    """TODO(mobile): yields typed turns from a paired device; today the REST API is the only source."""

    def turns(self) -> Iterator[TurnIn]: ...


class CueDeliverer(Protocol):
    """TODO(tts/earbud): privately deliver an approved cue (e.g. local TTS to an earbud)."""

    def deliver(self, cue: str, language: str) -> None: ...
