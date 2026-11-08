"""Structure-aware shrinking of Python values that trigger a failure.

The algorithm is a greedy, hierarchical variant of delta debugging
(Zeller & Hildebrandt, 2002) that works on Python object trees rather than
lines of text:

1. Walk the current input tree, outermost nodes first.
2. For each node, propose simpler replacements: an empty container, the
   container with a chunk of items removed (halving chunk sizes like ``ddmin``),
   a shorter string, a number closer to zero, ``False`` for ``True``...
3. Accept the first replacement that is strictly *smaller* and still
   reproduces the original :class:`~squeezebug.signature.FailureSignature`.
4. Repeat until a full pass makes no progress or the call budget runs out.

Because every accepted step strictly decreases :func:`size`, the loop always
terminates, and the result is 1-minimal with respect to the transformations
tried (no single proposed simplification still fails).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional, Union

from .runner import CallInput, run_once
from .signature import FailureSignature, MatchMode

PathKey = Union[int, str]
Path = tuple[PathKey, ...]

#: Above this many characters, strings are shrunk by chunks only (no per-char pass).
_STR_FINE_LIMIT = 64


def size(value: Any) -> int:
    """Return a structural size used to rank inputs (smaller is simpler).

    Containers count one unit plus their contents, strings and bytes count
    their length, and numbers grow with their number of digits so that
    ``0`` is simpler than ``123456``.

    Args:
        value: Any Python value.

    Returns:
        A non-negative integer size.
    """
    if isinstance(value, bool) or value is None:
        return 1
    if isinstance(value, int):
        return 1 + len(str(abs(value))) - (1 if value == 0 else 0)
    if isinstance(value, float):
        return 1 + (0 if value == 0.0 else len(repr(abs(value))))
    if isinstance(value, (str, bytes, bytearray)):
        return 1 + len(value)
    if isinstance(value, dict):
        return 1 + sum(size(k) + size(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return 1 + sum(size(v) for v in value)
    return 1


def iter_paths(value: Any, prefix: Path = ()) -> Iterator[Path]:
    """Yield the path of every node in ``value``, parents before children."""
    yield prefix
    if isinstance(value, dict):
        for key in list(value.keys()):
            yield from iter_paths(value[key], prefix + (key,))
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            yield from iter_paths(item, prefix + (idx,))


def get_at(value: Any, path: Path) -> Any:
    """Return the node of ``value`` at ``path``."""
    for key in path:
        value = value[key]
    return value


def replace_at(value: Any, path: Path, new: Any) -> Any:
    """Return a copy of ``value`` with the node at ``path`` replaced by ``new``.

    Only the containers along ``path`` are copied; untouched siblings are shared.
    """
    if not path:
        return new
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        copied = dict(value)
        copied[head] = replace_at(value[head], rest, new)
        return copied
    if isinstance(value, list):
        copied_list = list(value)
        copied_list[int(head)] = replace_at(value[int(head)], rest, new)
        return copied_list
    if isinstance(value, tuple):
        items = list(value)
        items[int(head)] = replace_at(value[int(head)], rest, new)
        return tuple(items)
    raise TypeError(f"cannot descend into {type(value).__name__}")


def _chunk_removals(n: int) -> Iterator[tuple[int, int]]:
    """Yield ``(start, stop)`` ranges to delete, big chunks first (ddmin order)."""
    chunk = n // 2 if n > 1 else 1
    while chunk >= 1:
        for start in range(0, n, chunk):
            yield start, min(start + chunk, n)
        if chunk == 1:
            break
        chunk //= 2


def simplifications(value: Any, *, removable: bool = True) -> Iterator[Any]:
    """Yield candidate replacements for one node, simplest first.

    Args:
        value: The node to simplify.
        removable: Whether items may be removed when ``value`` is a container.
            The positional-argument tuple of a call is not removable.

    Yields:
        Candidate values; callers keep only those strictly smaller than ``value``.
    """
    if isinstance(value, bool):
        if value:
            yield False
        return
    if isinstance(value, int):
        if value != 0:
            yield 0
            if abs(value) > 1:
                yield 1 if value > 0 else -1
                yield value // 2 if value > 0 else -((-value) // 2)
            if value < 0:
                yield -value
        return
    if isinstance(value, float):
        if value != 0.0:
            yield 0.0
            if value != int(value) and abs(value) < 1e15:
                yield float(int(value))
                yield round(value, 1)
        return
    if isinstance(value, (str, bytes)):
        n = len(value)
        if n == 0:
            return
        yield value[:0]
        for start, stop in _chunk_removals(n):
            if stop - start == 1 and n > _STR_FINE_LIMIT:
                break
            yield value[:start] + value[stop:]
        if isinstance(value, str) and value.lower() != value:
            yield value.lower()
        return
    if isinstance(value, dict):
        if not removable:
            return
        keys = list(value.keys())
        if keys:
            yield {}
        for start, stop in _chunk_removals(len(keys)):
            drop = set(keys[start:stop])
            yield {k: v for k, v in value.items() if k not in drop}
        return
    if isinstance(value, (list, tuple)):
        if not removable:
            return
        kind = type(value)
        if value:
            yield kind()
        for start, stop in _chunk_removals(len(value)):
            yield kind(list(value[:start]) + list(value[stop:]))
        return
    if isinstance(value, (set, frozenset)):
        items = sorted(value, key=repr)
        if items:
            yield type(value)()
        for start, stop in _chunk_removals(len(items)):
            yield type(value)(items[:start] + items[stop:])


@dataclass
class ShrinkResult:
    """Outcome of a shrinking session."""

    original: CallInput
    minimized: CallInput
    signature: FailureSignature
    final_signature: FailureSignature
    mode: MatchMode
    calls: int
    accepted_steps: int
    seconds: float
    budget_exhausted: bool
    history: list[int] = field(default_factory=list)

    @property
    def original_size(self) -> int:
        """Structural size of the input before shrinking."""
        return size(self.original.as_tree())

    @property
    def minimized_size(self) -> int:
        """Structural size of the input after shrinking."""
        return size(self.minimized.as_tree())

    @property
    def reduction(self) -> float:
        """Fraction of the original size removed, between 0 and 1."""
        if self.original_size == 0:
            return 0.0
        return 1.0 - self.minimized_size / self.original_size


class NotFailingError(ValueError):
    """Raised when the starting input does not fail at all."""


class Shrinker:
    """Shrink the arguments of a failing call while preserving its failure.

    Args:
        func: The callable that fails.
        mode: How strictly a candidate's failure must match the original.
        max_calls: Upper bound on how many times ``func`` is invoked.
        timeout: Per-call time limit in seconds (POSIX main thread only).
        on_progress: Optional callback ``(calls, current_size)`` after each accepted step.
    """

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        mode: MatchMode = MatchMode.LOCATION,
        max_calls: int = 2000,
        timeout: Optional[float] = 2.0,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.func = func
        self.mode = mode
        self.max_calls = max_calls
        self.timeout = timeout
        self.on_progress = on_progress
        self.calls = 0
        self._seen: set[str] = set()

    def _test(self, tree: dict[str, Any], target: FailureSignature) -> Optional[FailureSignature]:
        key = repr(tree)
        if key in self._seen:
            return None
        self._seen.add(key)
        self.calls += 1
        outcome = run_once(self.func, CallInput.from_tree(tree), timeout=self.timeout)
        if outcome.signature is not None and target.matches(outcome.signature, self.mode):
            return outcome.signature
        return None

    def shrink(self, call: CallInput, target: Optional[FailureSignature] = None) -> ShrinkResult:
        """Minimise ``call`` so it still reproduces ``target``.

        Args:
            call: The original failing input.
            target: The failure to preserve. If omitted, ``call`` is executed
                once and its failure becomes the target.

        Returns:
            A :class:`ShrinkResult` describing the minimal input found.

        Raises:
            NotFailingError: If ``call`` does not raise (or no longer matches ``target``).
        """
        started = time.monotonic()
        first = run_once(self.func, call, timeout=self.timeout)
        self.calls += 1
        if first.signature is None:
            raise NotFailingError("the original input does not raise; nothing to shrink")
        if target is None:
            target = first.signature
        elif not target.matches(first.signature, self.mode):
            raise NotFailingError(
                f"original input now fails differently: {first.signature.short()} "
                f"(expected {target.short()})"
            )

        current = call.as_tree()
        self._seen.add(repr(current))
        last_sig = first.signature
        accepted = 0
        history = [size(current)]
        progress = True
        while progress and self.calls < self.max_calls:
            progress = False
            for path in list(iter_paths(current)):
                if self.calls >= self.max_calls:
                    break
                if not path:
                    continue  # the {"args", "kwargs"} wrapper itself is fixed
                node = get_at(current, path)
                removable = path != ("args",)
                node_size = size(node)
                for candidate in simplifications(node, removable=removable):
                    if self.calls >= self.max_calls:
                        break
                    if size(candidate) >= node_size:
                        continue
                    new_tree = replace_at(current, path, candidate)
                    sig = self._test(new_tree, target)
                    if sig is not None:
                        current, last_sig = new_tree, sig
                        accepted += 1
                        history.append(size(current))
                        if self.on_progress:
                            self.on_progress(self.calls, history[-1])
                        progress = True
                        break
                if progress:
                    break  # tree changed; recompute paths from the top

        return ShrinkResult(
            original=call,
            minimized=CallInput.from_tree(current),
            signature=target,
            final_signature=last_sig,
            mode=self.mode,
            calls=self.calls,
            accepted_steps=accepted,
            seconds=time.monotonic() - started,
            budget_exhausted=self.calls >= self.max_calls,
            history=history,
        )


def shrink_call(
    func: Callable[..., Any],
    *args: Any,
    squeeze_mode: Union[MatchMode, str] = MatchMode.LOCATION,
    squeeze_max_calls: int = 2000,
    squeeze_timeout: Optional[float] = 2.0,
    **kwargs: Any,
) -> ShrinkResult:
    """Convenience wrapper: shrink ``func(*args, **kwargs)``.

    Options are prefixed with ``squeeze_`` so they never collide with the
    target's own keyword arguments.

    Example:
        >>> def first_even(xs):
        ...     return [x for x in xs if x % 2 == 0][0]
        >>> shrink_call(first_even, [1, 3, 5, 7, 9]).minimized.args
        [[]]
    """
    shrinker = Shrinker(
        func, mode=MatchMode(squeeze_mode), max_calls=squeeze_max_calls, timeout=squeeze_timeout
    )
    return shrinker.shrink(CallInput(list(args), dict(kwargs)))
