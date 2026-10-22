"""Imported by ``pdf2rea.pth`` at interpreter start-up.

This module runs inside *every* python process launched from the
``zotero-pdf2zh-next-venv`` interpreter, including ``pip``, ``uv`` and ad-hoc
maintenance scripts.  It therefore has exactly one job: register post-import hooks
without importing anything heavy, and never, ever raise.

If the environment says "this is not a translation run", it returns immediately:
  * ``PDF2REA_DISABLE=1``
  * an interpreter doing ``-m pip`` / ``-m uv`` / ``setup.py``
"""

from __future__ import annotations

import os
import sys

_RAN = False


def _looks_like_translation_run(argv: list[str]) -> bool:
    """Only pay the hook cost for the real engine entry point."""
    if os.environ.get("PDF2REA_APPLY_EAGERLY") == "1":
        return True
    joined = " ".join(argv)
    # macOS/Windows spawn a fresh interpreter before restoring the CLI argv.
    # Only children of an activated private engine inherit this marker.
    if os.environ.get("PDF2REA_ENGINE_ACTIVE") == "1" and (
        "--multiprocessing-fork" in argv or "spawn_main" in joined
    ):
        return True
    if "pdf2zh_next" in joined or "babeldoc" in joined:
        return True
    program = os.path.basename(argv[0]) if argv else ""
    if program in ("pdf2zh_next", "pdf2zh-next", "babeldoc", "main.py", "__main__.py"):
        return True
    # The server launches the CLI through a PTY with sys.argv[0] = the entry script.
    return any(token in joined for token in ("--config-file", "--lang-out", "-i", "--input"))


def run() -> None:
    global _RAN
    if _RAN:
        return
    _RAN = True
    if os.environ.get("PDF2REA_DISABLE") == "1":
        return
    if not _looks_like_translation_run(list(sys.argv)):
        return
    try:
        import pdf2rea_engine

        os.environ["PDF2REA_ENGINE_ACTIVE"] = "1"
        pdf2rea_engine.apply()
    except Exception as error:  # pragma: no cover - must never break the host
        print(f"[PDF2REA] patch layer disabled for this run: {error!r}", file=sys.stderr)


run()
