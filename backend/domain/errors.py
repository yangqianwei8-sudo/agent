"""Domain errors."""


class DomainError(Exception):
    """Base domain error."""

    def __init__(self, message: str, *, code: str = "DOMAIN_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFoundError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="NOT_FOUND")


class ValidationError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="VALIDATION_ERROR")


class ConflictError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFLICT")


class ImmutableError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="IMMUTABLE")
