#!/usr/bin/env python3
"""Run the real engine through the same wrapper twice: with and without PDF2REA.

``PYTHONPATH`` alone does **not** activate the patch layer -- in production the
package is imported by ``pdf2rea.pth`` inside the engine venv, which we deliberately
do not install during testing.  This runner makes the activation explicit so the two
e2e runs differ in exactly one variable.

usage: engine_runner.py [--no-pdf2rea] <args passed to pdf2zh_next>
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

argv = sys.argv[1:]
enabled = True
if argv and argv[0] == "--no-pdf2rea":
    enabled = False
    argv = argv[1:]

sys.argv = ["pdf2zh_next"] + argv

if enabled:
    from pdf2rea_engine import apply

    report = apply()
    print(f"[engine_runner] PDF2REA enabled: {report.get('features')}", file=sys.stderr)
else:
    os.environ["PDF2REA_DISABLE"] = "1"
    print("[engine_runner] PDF2REA disabled (stock control run)", file=sys.stderr)

from pdf2zh_next.main import cli  # noqa: E402

if __name__ == "__main__":
    cli()
