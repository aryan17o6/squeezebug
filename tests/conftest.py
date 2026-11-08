import os
import sys

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
if EXAMPLES not in sys.path:
    sys.path.insert(0, EXAMPLES)
