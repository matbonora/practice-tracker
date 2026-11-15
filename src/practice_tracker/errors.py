"""Domain errors. REST maps them to HTTP codes, MCP to tool errors."""


class DomainError(Exception):
    status = 400


class NotFound(DomainError):
    status = 404


class Conflict(DomainError):
    status = 409


class Invalid(DomainError):
    status = 422


class Ambiguous(DomainError):
    """A name matched several (or no) records; carries the candidates."""

    status = 409

    def __init__(self, message: str, candidates: list[dict]):
        super().__init__(message)
        self.candidates = candidates
