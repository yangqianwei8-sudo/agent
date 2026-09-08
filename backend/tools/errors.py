"""Tool-layer errors (no DB access)."""


class ToolError(Exception):
    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedFormatError(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNSUPPORTED_FORMAT")


class ParseToolError(ToolError):
    def __init__(self, message: str, *, code: str = "PARSE_FAILED") -> None:
        super().__init__(message, code=code)
