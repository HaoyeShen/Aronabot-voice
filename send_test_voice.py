"""Source checkout convenience entry point; real sends are restricted to test."""
from __future__ import annotations

import sys

from scripts.verify_shared_voice import main


if __name__ == '__main__':
    sys.argv[1:] = ['voice']
    raise SystemExit(main())
