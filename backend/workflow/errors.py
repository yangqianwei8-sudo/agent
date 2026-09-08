"""Workflow Runtime errors."""


class WorkflowError(Exception):
    def __init__(self, message: str, *, code: str = "WORKFLOW_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkflowConflictError(WorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="WORKFLOW_CONFLICT")


class WorkflowNotFoundError(WorkflowError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="WORKFLOW_NOT_FOUND")
