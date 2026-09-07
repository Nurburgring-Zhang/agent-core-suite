#!/usr/bin/env python3
"""Back-compatibility entry point for QwenWorkCN global verification.

v2.0 generalized the former qwenworkcn-only verifier into the multi-product engine
``acs_global_verify.py``. This thin shim preserves the historical
``qwenwork_global_verify.py`` CLI (and every doc / terminals.json / test that points at
it) by delegating to that engine with ``--product qwenworkcn``. It intentionally holds no
verification logic of its own, so there is exactly one implementation to maintain.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import acs_global_verify  # noqa: E402  (same directory; script dir is on sys.path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return acs_global_verify.main(["--product", "qwenworkcn", *argv])


if __name__ == "__main__":
    raise SystemExit(main())
