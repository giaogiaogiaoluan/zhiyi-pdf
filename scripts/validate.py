#!/usr/bin/env python3
"""PDF2REA build validator.

Fails the build on anything that would only surface as a broken install in Zotero:
malformed manifest, prefs drift, a plugin that references an engine file the XPI does
not ship, or Python that cannot even compile.

    python3 scripts/validate.py [--xpi dist/PDF2REA-1.0.0.xpi]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
ENGINE = ROOT / "engine" / "pdf2rea_engine"

REQUIRED_PLUGIN_FILES = [
    "manifest.json",
    "bootstrap.js",
    "prefs.js",
    "content/scripts/pdf2rea.js",
    "content/preferences.xhtml",
    "content/runtime/service_control.py",
    "content/runtime/server_compat.py",
    "content/icons/favicon.svg",
]
FORBIDDEN_IN_XPI = ("__pycache__", ".DS_Store", ".pyc", ".git")

issues: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    issues.append(message)


def note(message: str) -> None:
    notes.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def check_json(path: Path) -> dict:
    try:
        return json.loads(read(path))
    except (OSError, ValueError) as error:
        fail(f"{path.name}: {error}")
        return {}


def check_manifest() -> None:
    manifest = check_json(PLUGIN / "manifest.json")
    zotero = (manifest.get("applications") or {}).get("zotero") or {}
    addon_id = zotero.get("id", "")
    if addon_id != "pdf2rea@qoder.dev":
        fail(f"manifest: unexpected addon id {addon_id!r}")
    if not manifest.get("version"):
        fail("manifest: missing version")
    if manifest.get("manifest_version") != 2:
        note(f"manifest_version={manifest.get('manifest_version')}")
    for key in ("strict_min_version", "strict_max_version", "update_url"):
        if not zotero.get(key):
            fail(f"manifest: applications.zotero.{key} missing")
    if not zotero.get("update_url", "").startswith("https://"):
        fail("manifest: update_url must use HTTPS or Zotero disables the add-on")
    for icon, path in (manifest.get("icons") or {}).items():
        if not (PLUGIN / path).is_file():
            fail(f"manifest: icon {path} not found (declared for {icon}px)")


def check_plugin_references() -> None:
    script = PLUGIN / "content/scripts/pdf2rea.js"
    if not script.is_file():
        fail("pdf2rea.js missing")
        return
    text = read(script)
    for token, path in [
        ("preferences.xhtml", PLUGIN / "content/preferences.xhtml"),
        ("engine_manifest.json", PLUGIN / "content/engine/engine_manifest.json"),
    ]:
        if token in text and not path.is_file():
            fail(f"pdf2rea.js references {token} but {path} does not exist")
    if "pdf2rea.pth" not in text:
        fail("pdf2rea.js no longer writes the .pth activation file")
    # Zotero 7+ removed the `OS` (osfile) module; pdf2zh itself uses PathUtils only.
    if re.search(r"\bOS\.Path\b", text):
        fail("pdf2rea.js uses OS.Path, which does not exist in Zotero 7/10 -- use PathUtils")
    # every pref read by the script must exist in prefs.js or DEFAULT_PREFS
    prefs_js = read(PLUGIN / "prefs.js")
    defaults = re.search(r"const DEFAULT_PREFS = \{(.*?)\n  \};", text, re.S)
    declared = set(re.findall(r'pref\("extensions\.zotero\.pdf2rea\.([A-Za-z0-9_-]+)"', prefs_js))
    used = set(re.findall(r'getPref\("([A-Za-z0-9_-]+)"', text))
    used |= set(re.findall(r'asString\("([A-Za-z0-9_-]+)"', text))
    used |= set(re.findall(r'setPref\("([A-Za-z0-9_-]+)"', text))
    if defaults:
        used |= set(re.findall(r'^\s*"?([A-Za-z0-9_-]+)"?:', defaults.group(1), re.M))
    missing = sorted(used - declared)
    if missing:
        fail(f"prefs.js is missing defaults for: {', '.join(missing)}")
    unused = sorted(declared - used)
    if unused:
        note(f"prefs.js declares prefs the script never reads: {', '.join(unused)}")
    # a comment block containing '*/' would terminate itself early -- node catches it
    # too, but this pinpoints the line
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("*") and "*/" in line[:-2]:
            fail(f"pdf2rea.js:{number} comment line closes the block early: {line.strip()[:70]}")


def check_xhtml() -> None:
    path = PLUGIN / "content/preferences.xhtml"
    if not path.is_file():
        fail("preferences.xhtml missing")
        return
    text = read(path)
    for prefix in re.findall(r"</?([A-Za-z][\w-]*):", text):
        if prefix not in ("html",):
            fail(f"preferences.xhtml uses undeclared namespace prefix {prefix!r}")
    if "Zotero.pdf2rea.hooks.onPrefsEvent" not in text:
        fail("preferences.xhtml does not hand its window to the plugin (pane stays dead)")
    if 'preference="extensions.zotero.pdf2rea.' not in text:
        fail("preferences.xhtml binds no preferences")
    for pref in set(re.findall(r'preference="extensions\.zotero\.pdf2rea\.([A-Za-z0-9_-]+)"', text)):
        if not re.search(rf'pref\("extensions\.zotero\.pdf2rea\.{re.escape(pref)}"',
                         read(PLUGIN / "prefs.js")):
            fail(f"preferences.xhtml binds {pref} but prefs.js has no default for it")
    if "data-l10n-id" in text:
        fail("preferences.xhtml uses data-l10n-id without shipping a .ftl bundle")


def check_engine() -> None:
    import ast

    required = ["__init__.py", "bootstrap.py", "options.py", "registry.py", "llm_hook.py",
                "cross_page.py", "formula_guard.py", "table_text.py", "terminology.py",
                "numeric_policy.py", "run_ledger.py", "status.py", "__main__.py"]
    for name in required:
        path = ENGINE / name
        if not path.is_file():
            fail(f"engine/{name} missing")
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            fail(f"engine/{name} syntax error line {error.lineno}: {error.msg}")
    glossary = ENGINE / "glossary" / "academic_base.csv"
    if not glossary.is_file():
        fail("built-in glossary missing")
    else:
        rows = [line for line in glossary.read_text(encoding="utf-8").splitlines() if line.strip()]
        bad = [row for row in rows[1:] if len(row.split(",")) < 3]
        if bad:
            fail(f"glossary rows missing columns: {bad[:2]}")
        note(f"built-in glossary: {len(rows) - 1} terms")
    # the patch targets must stay pinned to real babeldoc symbols
    source = (ENGINE / "formula_guard.py").read_text(encoding="utf-8")
    for symbol in ("create_composition", "is_translatable_formula",
                   "process_translatable_formulas", "is_formulas_start_char"):
        if symbol not in source:
            fail(f"formula_guard no longer patches {symbol}")


def check_manifest_files_cover_engine() -> None:
    manifest_path = PLUGIN / "content/engine/engine_manifest.json"
    if not manifest_path.is_file():
        note("engine_manifest.json not generated yet (run scripts/build_xpi.sh)")
        return
    listed = {entry["path"] for entry in check_json(manifest_path).get("files", [])}
    on_disk = set()
    for path in (PLUGIN / "content/engine").rglob("*"):
        if path.is_file() and path.name != "engine_manifest.json":
            rel = str(path.relative_to(PLUGIN / "content/engine"))
            if "__pycache__" not in rel:
                on_disk.add(rel)
    for missing in sorted(on_disk - listed):
        fail(f"engine file present in XPI but not listed in engine_manifest.json: {missing}")
    for ghost in sorted(listed - on_disk):
        fail(f"engine_manifest.json lists {ghost} but the file is not in the XPI")


def check_xpi(path: Path) -> None:
    if not path.is_file():
        fail(f"xpi not found: {path}")
        return
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            fail("xpi has no manifest.json at the root")
        if "bootstrap.js" not in names:
            fail("xpi has no bootstrap.js at the root")
        if not [name for name in names if name.startswith("content/scripts/")]:
            fail("xpi has no content/scripts/")
        for junk in FORBIDDEN_IN_XPI:
            hits = [name for name in names if junk in name]
            if hits:
                fail(f"xpi contains {junk}: {hits[:3]}")
        entries = [name for name in names if name.startswith("content/engine/pdf2rea_engine/")]
        if len(entries) < 12:
            fail(f"xpi ships only {len(entries)} engine files")
        # Validate the packed manifest against packed entries: staging removes
        # macOS metadata, so comparing only the source tree misses dangling refs.
        engine_manifest_name = "content/engine/engine_manifest.json"
        if engine_manifest_name not in names:
            fail("xpi has no engine_manifest.json")
        else:
            engine_manifest = json.loads(archive.read(engine_manifest_name))
            for entry in engine_manifest.get("files", []):
                packed_name = "content/engine/" + entry["path"]
                if any(junk in entry["path"] for junk in FORBIDDEN_IN_XPI):
                    fail(f"packed engine manifest references forbidden metadata: {entry['path']}")
                if packed_name not in names:
                    fail(f"packed engine manifest references missing file: {entry['path']}")
                elif len(archive.read(packed_name)) != entry["bytes"]:
                    fail(f"packed engine file size differs from manifest: {entry['path']}")
        if archive.testzip(): fail("xpi has a CRC failure")
        packed_manifest = json.loads(archive.read("manifest.json"))
        if not packed_manifest.get("applications", {}).get("zotero", {}).get("update_url"):
            fail("packed manifest: applications.zotero.update_url missing")
        for name in REQUIRED_PLUGIN_FILES:
            if archive.read(name) != (PLUGIN / name).read_bytes():
                fail(f"stale XPI file: {name}")
        # the .pth content the plugin writes must import a module the xpi ships
        script = archive.read("content/scripts/pdf2rea.js").decode("utf-8")
        if "pdf2rea.pth" not in script:
            fail("packed pdf2rea.js lost the pth writer")
        note(f"xpi: {len(names)} entries, {path.stat().st_size / 1024:.0f} KB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xpi", default=None)
    args = parser.parse_args()

    for relative in REQUIRED_PLUGIN_FILES:
        if not (PLUGIN / relative).is_file():
            fail(f"missing plugin file: {relative}")

    check_manifest()
    check_plugin_references()
    check_xhtml()
    check_engine()
    check_manifest_files_cover_engine()
    if args.xpi:
        check_xpi(ROOT / args.xpi)

    for line in notes:
        print("  note  " + line)
    if issues:
        for line in issues:
            print("  FAIL  " + line)
        print(f"\nvalidation failed: {len(issues)} issue(s)")
        return 1
    print("\nvalidation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
