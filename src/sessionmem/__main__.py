"""Allow `python -m sessionmem` to run the command-line interface."""

import sys

from sessionmem.cli import main

if __name__ == "__main__":
    sys.exit(main())
