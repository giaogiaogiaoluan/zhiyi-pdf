#!/usr/bin/env python3
"""Deterministic standard ZIP packer. Archive field choices below provide
reproducible builds; they are not Zotero compatibility requirements. Zotero
10.0.6 rejected the old package because update_url was absent from its manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

# 2000-01-01: any fixed value works; determinism is the point.
FIXED_DATE_TIME = (2000, 1, 1, 0, 0, 0)
# Standard Unix file modes for reproducible archives.
FILE_MODE = 0o100644
DIR_MODE = 0o40755
# UTF-8 name flag, matching the working archives.
FLAG_UTF8 = 0x0800


def ordered_entries(root: Path) -> list[tuple[str, Path, bool]]:
    """manifest.json first, then everything else sorted, directories included."""
    files: list[tuple[str, Path, bool]] = []
    dirs: list[tuple[str, Path, bool]] = []
    for path in sorted(root.rglob("*")):
        if path.name in {".DS_Store", "__pycache__"} or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            dirs.append((relative + "/", path, True))
        else:
            files.append((relative, path, False))
    head = [entry for entry in files if entry[0] == "manifest.json"]
    rest = [entry for entry in files if entry[0] != "manifest.json"]
    return head + rest + dirs


def pack(root: Path, output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path, is_dir in ordered_entries(root):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_STORED if is_dir else zipfile.ZIP_DEFLATED
            info.create_system = 3          # unix, like every add-on here
            info.create_version = 20
            info.extract_version = 20       # reproducible build convention
            info.external_attr = (DIR_MODE if is_dir else FILE_MODE) << 16
            info.flag_bits |= FLAG_UTF8
            data = b"" if is_dir else path.read_bytes()
            archive.writestr(info, data)
    payload = output.read_bytes()
    return {
        "output": str(output),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest()[:16],
        "entries": len(zipfile.ZipFile(output).namelist()),
    }


def audit(path: Path) -> dict:
    """Re-check the fields that got us here, so a regression cannot sneak back in."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        return {
            "entries": len(infos),
            "extract_versions": sorted({i.extract_version for i in infos}),
            "create_systems": sorted({i.create_system for i in infos}),
            "flags": sorted({i.flag_bits for i in infos}),
            "first": infos[0].filename,
            "file_modes": sorted({oct(i.external_attr >> 16) for i in infos if not i.is_dir()}),
            "junk": [i.filename for i in infos
                     if "__pycache__" in i.filename or i.filename.startswith("._")
                     or "__MACOSX" in i.filename],
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="directory whose contents become the XPI root")
    parser.add_argument("output", type=Path)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--expect-manifest-first", action="store_true", default=True)
    args = parser.parse_args(argv)

    if args.audit_only:
        report = audit(args.root)
        print(json.dumps(report, indent=1))
        bad = (report["extract_versions"] != [20] or report["junk"]
               or report["first"] != "manifest.json")
        return 1 if bad else 0

    result = pack(args.root, args.output)
    report = audit(args.output)
    print(json.dumps({**result, **report}, indent=1))
    problems = []
    if report["extract_versions"] != [20]:
        problems.append(f"extract_version {report['extract_versions']} (must be [20])")
    if report["file_modes"] != ["0o100644"]:
        problems.append(f"file external_attr {report['file_modes']} (must be 0o100644, "
                        "i.e. S_IFREG set)")
    if report["junk"]:
        problems.append(f"junk entries {report['junk']}")
    if report["first"] != "manifest.json":
        problems.append(f"first entry is {report['first']}")
    if problems:
        for problem in problems:
            print("FAIL " + problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
