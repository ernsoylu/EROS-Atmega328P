"""Structured diagnostics + the validation sink.

Diagnostic is the UI-agnostic, UI-friendly record a future GUI renders in a
problem list (severity + machine-readable code + message + source location).

Diagnostics is the sink threaded through validation:
  * strict=True  (default, CLI): the first error raises ConfigError immediately,
    reproducing erosgen's historical fail-fast behavior and messages.
  * strict=False (collect_diagnostics / GUI): every problem is accumulated and
    validation continues, so the caller sees them all at once.
"""
from dataclasses import dataclass

from .errors import ConfigError


@dataclass(frozen=True)
class Diagnostic:
    severity: str        # "error" | "warning" | "info"
    code: str            # machine-readable, e.g. "PIN_CONFLICT" (assert on this)
    message: str         # human-readable (matches the historical fail() text)
    location: str = ""   # e.g. "tasks[1]" / "peripherals.uart" / "pin PB5"


class Diagnostics:
    def __init__(self, strict=True):
        self.strict = strict
        self.items = []

    def error(self, code, message, location=""):
        d = Diagnostic("error", code, message, location)
        self.items.append(d)
        if self.strict:
            raise ConfigError("erosgen: " + message, d)
        return d

    def warning(self, code, message, location=""):
        self.items.append(Diagnostic("warning", code, message, location))

    @property
    def errors(self):
        return [d for d in self.items if d.severity == "error"]

    @property
    def has_errors(self):
        return any(d.severity == "error" for d in self.items)
