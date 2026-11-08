"""squeezebug: shrink a real failing input to a minimal pytest repro.

Public API::

    from squeezebug import capture, shrink_call, Shrinker, MatchMode
"""

__version__ = "0.1.0"

from .capture import capture
from .codegen import TestStyle, render_test
from .runner import CallInput
from .shrink import NotFailingError, Shrinker, ShrinkResult, shrink_call, size
from .signature import FailureSignature, MatchMode

__all__ = [
    "CallInput",
    "FailureSignature",
    "MatchMode",
    "NotFailingError",
    "ShrinkResult",
    "Shrinker",
    "TestStyle",
    "__version__",
    "capture",
    "render_test",
    "shrink_call",
    "size",
]
