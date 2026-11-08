"""Allow ``python -m squeezebug``."""

import sys

from .cli import main

sys.exit(main())
