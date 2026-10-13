"""Typed errors of the enrollment domain. The domain raises these; it does not know HTTP."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class DomainError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class ValidationError(DomainError):
    """The shape of the input is invalid: one entry per field."""

    details: list[FieldError] = field(default_factory=list)

    def __init__(self, details: list[FieldError]) -> None:
        super().__init__("the request has invalid fields")
        self.details = details


class BusinessRuleViolation(DomainError):
    """An invariant forbids the operation (INV-ENR-001, INV-ENR-002, INV-ENR-005)."""


class InvalidStatusTransition(DomainError):
    """The state rule of an enrollment forbids the transition (INV-ENR-004)."""
