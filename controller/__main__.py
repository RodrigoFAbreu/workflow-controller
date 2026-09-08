"""``python -m controller`` entry point. Delegates entirely to
``controller.cli.main`` -- no logic lives here, so there is only one place
the CLI's behaviour is defined."""

import sys

from controller.cli import main

if __name__ == "__main__":
    sys.exit(main())
