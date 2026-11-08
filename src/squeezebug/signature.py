"""Failure signatures: deciding whether two exceptions are "the same bug".

A shrinker is only useful if it preserves the *original* failure. Shrinking an
input until *anything* goes wrong usually lands on an unrelated ``TypeError``
caused by the shrinker itself. A :class:`FailureSignature` pins down what
"same failure" means, at a strictness the user picks.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class MatchMode(str, Enum):
    """How strictly a new exception must resemble the original one."""

    TYPE = "type"
    """Same exception class name only (loosest)."""

    LOCATION = "location"
    """Same exception class *and* raised from the same file and line (default)."""

    MESSAGE = "message"
    """Same class, same location, and identical ``str(exc)`` (strictest)."""


@dataclass(frozen=True)
class FailureSignature:
    """A compact, serialisable fingerprint of an exception.

    Attributes:
        exc_type: Qualified exception class name, e.g. ``"KeyError"``.
        filename: File of the innermost traceback frame (where it was raised).
        lineno: Line number of the innermost traceback frame.
        function: Function name of the innermost traceback frame.
        message: ``str(exc)`` at capture time.
    """

    exc_type: str
    filename: str
    lineno: int
    function: str
    message: str

    @classmethod
    def from_exception(cls, exc: BaseException) -> "FailureSignature":
        """Build a signature from a live exception object.

        Args:
            exc: The exception, ideally with its ``__traceback__`` attached.

        Returns:
            The signature of ``exc``.
        """
        frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        if frames:
            last = frames[-1]
            filename, lineno, function = last.filename, int(last.lineno or 0), last.name
        else:
            filename, lineno, function = "<unknown>", 0, "<unknown>"
        exc_cls = type(exc)
        module = exc_cls.__module__
        name = exc_cls.__qualname__ if module in ("builtins", "__main__") else f"{module}.{exc_cls.__qualname__}"
        return cls(
            exc_type=name,
            filename=os.path.normcase(os.path.abspath(filename)) if filename != "<unknown>" else filename,
            lineno=lineno,
            function=function,
            message=str(exc),
        )

    def matches(self, other: "FailureSignature", mode: MatchMode = MatchMode.LOCATION) -> bool:
        """Return True when ``other`` counts as the same failure under ``mode``.

        Args:
            other: Signature of a newly observed exception.
            mode: Strictness of the comparison.

        Returns:
            Whether the two signatures describe the same failure.
        """
        if self.exc_type != other.exc_type:
            return False
        if mode is MatchMode.TYPE:
            return True
        same_place = self.filename == other.filename and self.lineno == other.lineno
        if mode is MatchMode.LOCATION:
            return same_place
        return same_place and self.message == other.message

    def short(self) -> str:
        """Human-readable one-liner, e.g. ``KeyError('sku') at orders.py:42``."""
        where = f"{os.path.basename(self.filename)}:{self.lineno}"
        msg = self.message if len(self.message) <= 80 else self.message[:77] + "..."
        return f"{self.exc_type}({msg!r}) at {where} in {self.function}()"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureSignature":
        """Inverse of :meth:`to_dict`."""
        return cls(
            exc_type=str(data["exc_type"]),
            filename=str(data["filename"]),
            lineno=int(data["lineno"]),
            function=str(data["function"]),
            message=str(data.get("message", "")),
        )
