from __future__ import annotations


class CodedError(ValueError):
    """An error carrying a stable machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
