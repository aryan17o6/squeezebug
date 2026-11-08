"""Run a target callable on a candidate input and classify the outcome."""

from __future__ import annotations

import contextlib
import copy
import io
import signal
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .signature import FailureSignature, MatchMode


class CallTimeout(Exception):
    """Raised inside a call that exceeded its time budget."""


@dataclass
class CallInput:
    """Positional and keyword arguments for one call of the target.

    Positional arguments are treated as a fixed-length tuple by the shrinker
    (their count is part of the function's contract); keyword arguments may be
    dropped entirely if the call still fails the same way.
    """

    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)

    def as_tree(self) -> dict[str, Any]:
        """Return the input as a plain tree ``{"args": [...], "kwargs": {...}}``."""
        return {"args": list(self.args), "kwargs": dict(self.kwargs)}

    @classmethod
    def from_tree(cls, tree: dict[str, Any]) -> "CallInput":
        """Inverse of :meth:`as_tree`."""
        return cls(args=list(tree.get("args", [])), kwargs=dict(tree.get("kwargs", {})))


@dataclass
class Outcome:
    """Result of running the target once."""

    signature: Optional[FailureSignature]
    exception: Optional[BaseException] = None
    timed_out: bool = False

    @property
    def failed(self) -> bool:
        """True when the call raised (including a timeout)."""
        return self.signature is not None or self.timed_out


def _can_use_alarm() -> bool:
    return hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()


def run_once(
    func: Callable[..., Any],
    call: CallInput,
    *,
    timeout: Optional[float] = 2.0,
    quiet: bool = True,
) -> Outcome:
    """Call ``func`` with a deep copy of ``call`` and record what happened.

    The input is deep-copied so a target that mutates its arguments cannot
    corrupt the shrinker's state. Output written to stdout/stderr is swallowed
    when ``quiet`` is set. On POSIX main threads a wall-clock ``timeout`` is
    enforced with ``SIGALRM``; elsewhere it is ignored.

    Args:
        func: The callable under test.
        call: Arguments to pass.
        timeout: Seconds before the call is abandoned, or None for no limit.
        quiet: Suppress the target's printed output.

    Returns:
        The :class:`Outcome` of the call.
    """
    args = copy.deepcopy(call.args)
    kwargs = copy.deepcopy(call.kwargs)
    use_alarm = bool(timeout) and _can_use_alarm()
    previous: Any = None

    def _on_alarm(signum: int, frame: Any) -> None:  # pragma: no cover - timing dependent
        raise CallTimeout(f"call exceeded {timeout}s")

    sink = io.StringIO()
    redirect_out = contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext()
    redirect_err = contextlib.redirect_stderr(sink) if quiet else contextlib.nullcontext()
    try:
        if use_alarm:
            previous = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, float(timeout))  # type: ignore[arg-type]
        with redirect_out, redirect_err:
            func(*args, **kwargs)
        return Outcome(signature=None)
    except CallTimeout:
        return Outcome(signature=None, timed_out=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - we classify every failure
        return Outcome(signature=FailureSignature.from_exception(exc), exception=exc)
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def still_fails(
    func: Callable[..., Any],
    call: CallInput,
    target: FailureSignature,
    mode: MatchMode,
    *,
    timeout: Optional[float] = 2.0,
) -> bool:
    """Return True when ``call`` reproduces the ``target`` failure under ``mode``."""
    outcome = run_once(func, call, timeout=timeout)
    return outcome.signature is not None and target.matches(outcome.signature, mode)
