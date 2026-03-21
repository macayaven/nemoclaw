"""Entry point for ``python -m orchestrator``.

Delegates unconditionally to :func:`orchestrator.cli.main`.
"""

from __future__ import annotations

from orchestrator.cli import main

if __name__ == "__main__":
    main()
