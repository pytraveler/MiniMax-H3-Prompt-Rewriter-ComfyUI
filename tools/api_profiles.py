"""Repository convenience wrapper; the shipped CLI is api_admin.py."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from minimax_h3_rewriter.api_admin import main


if __name__ == "__main__":
    raise SystemExit(main())
