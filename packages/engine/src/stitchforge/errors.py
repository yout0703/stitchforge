from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Diagnostic:
    rule_id: str
    message: str
    path: str = "$"
    severity: str = "error"
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


class DocumentValidationError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        summary = "; ".join(
            f"{item.rule_id} at {item.path}: {item.message}" for item in diagnostics
        )
        super().__init__(summary)


class ProjectFormatError(ValueError):
    pass
