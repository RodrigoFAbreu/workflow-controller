"""The sandbox's stand-in for `codex` (refuses and logs; see the package docstring)."""

import sys

from controller.sandbox_fakes import refuse

if __name__ == "__main__":
    sys.exit(refuse("codex"))
