"""Domain errors and their HTTP mapping.

Error messages never include conversation text.
"""

from __future__ import annotations


class PaperCueError(Exception):
    status_code = 400
    code = "papercue_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(PaperCueError):
    status_code = 404
    code = "not_found"


class ConsentRequiredError(PaperCueError):
    status_code = 403
    code = "consent_required"


class InvalidSessionStateError(PaperCueError):
    status_code = 409
    code = "invalid_session_state"


class ConflictError(PaperCueError):
    status_code = 409
    code = "conflict"


class InvalidInputError(PaperCueError):
    status_code = 422
    code = "invalid_input"


class LocalConfigurationError(PaperCueError):
    """A required local component (Ollama, embedding model) is missing or misconfigured."""

    status_code = 503
    code = "local_model_unavailable"


class ModelOutputError(PaperCueError):
    """The local model returned output that failed schema validation after retries."""

    status_code = 502
    code = "malformed_model_output"


class PayloadTooLargeError(PaperCueError):
    status_code = 413
    code = "payload_too_large"
