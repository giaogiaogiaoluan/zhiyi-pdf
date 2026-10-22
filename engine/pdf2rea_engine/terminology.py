"""Fix 2 -- terminology consistency across a document (and across runs).

Root cause (verified)::

    server.py:1081   ``# TODO: 增加术语表的地址`` -- the Zotero server never
        implemented ``--glossary-files``.  config.toml keeps ``glossaries = "null"``,
        so on this install no user glossary ever reaches the engine at all.
    il_translator_llm_only.py:592  a batch is at most 5 paragraphs / 200 tokens and
        every LLM request is otherwise independent --> the model has no memory of how
        it rendered a term fifteen batches ago.  That is "又是 2 又是 二".
    translation_config.py:130-140  ``get_glossaries_for_translation`` returns
        ``[auto_extracted_glossary]`` whenever auto-extraction produced one, silently
        DROPPING every user glossary.
    automatic_term_extractor.py:204-273 + translation_config.py:99-121  auto
        extraction accumulates (source, target) pairs and resolves them by majority
        vote -- then throws the result away when the process exits.

Four layers, cheapest first:
  1. inject user + built-in academic glossaries into every request, using babeldoc's
     own matching so the terms render as a prompt table (``_build_llm_prompt`` already
     supports that -- it just never receives the data);
  2. fix the upstream drop so user glossaries survive alongside the auto glossary;
  3. add explicit consistency rules to the prompt (one source term -> one target term,
     one numeric style per document, never localize units or acronyms);
  4. deterministic enforcement at the single write-back point
     ``ILTranslator.post_translate_paragraph`` (il_translator.py:991), covering both
     the LLM path and the fallback path: forbidden variants are replaced, numeric
     style is unified, and the run's resolved term pairs are persisted to
     ``~/.pdf2rea/ledger.json`` so the next run of the same paper is identical.
"""

from __future__ import annotations

import os
import re
import threading

from . import llm_hook, numeric_policy, registry

_LOCK = threading.Lock()
_STATE: dict = {"glossaries": [], "hard_terms": [], "ledger": None,
                "stats": {"enforced": 0, "normalized": 0, "violations": 0}}

_TAG_SPAN = re.compile(r"(<style[^>]*>.*?</style>|</style>|<style[^>]*>|\{[A-Za-z0-9_]+\})")

CONSISTENCY_RULES = """## Terminology and Style Consistency (PDF2REA)
- One source term maps to exactly one target term in this document. When a term \
appears in the glossary tables above, use that translation verbatim.
- Numeric style: {numeric_rule}
- Do not translate or localize: chemical formulas, units in Latin letters (mm, kPa, \
eV, wt%), gene/protein symbols, model and dataset names, acronyms inside \
parentheses, variables inside <style>...</style> tags, or {{vN}} placeholders.
- If two items in this request contain the same term, both must render it \
identically.
- Never add, drop, or reformat digits that are not present in the source.
{extra_rules}"""

NUMERIC_RULES = {
    "keep": "keep the source numeral style; never convert digits to words or back.",
    "auto": "use Arabic digits for all measured quantities and counts, consistently.",
    "arabic": "use Arabic digits (1, 2, 3) for every quantity; never write them out \
in Chinese characters.",
    "chinese": "write bare counting numbers up to 99 as Chinese numerals (一, 二, 三); \
keep Arabic digits for measurements with units, decimals and percentages.",
}

_TAG_SPAN = re.compile(r"(<style[^>]*>.*?</style>|</style>|<style[^>]*>|\{[A-Za-z0-9_]+\})")


# --------------------------------------------------------------------------- #
# glossary files
# --------------------------------------------------------------------------- #
def read_glossary_csv(path: str) -> list[dict]:
    """Parse babeldoc's schema, plus one PDF2REA extension column.

    ``source,target[,tgt_lng[,avoid]]``

    ``avoid`` (pipe separated) lists renderings the model must never emit for that
    source; those are the only strings the enforcement pass is allowed to rewrite.
    Restricting rewriting to declared variants is deliberate: silently "improving"
    arbitrary model output inside a sentence is how translators corrupt text.
    """
    import csv as _csv

    rows: list[dict] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for index, row in enumerate(_csv.reader(handle)):
                if not row or all(not cell.strip() for cell in row):
                    continue
                if index == 0 and row[0].strip().lower() in ("source", "src", "术语", "原文"):
                    continue
                source = row[0].strip()
                target = row[1].strip() if len(row) > 1 else ""
                language = row[2].strip() if len(row) > 2 and row[2].strip() else None
                avoid = ([v.strip() for v in row[3].split("|") if v.strip()]
                         if len(row) > 3 else [])
                if source and target:
                    rows.append({"source": source, "target": target,
                                 "language": language, "avoid": avoid})
    except OSError:
        return []
    return rows


def built_in_glossary_path() -> str | None:
    from pathlib import Path

    candidate = Path(__file__).parent / "glossary" / "academic_base.csv"
    return str(candidate) if candidate.is_file() else None


def collect_glossary_rows(options: dict, lang_out: str) -> list[tuple[str, list[dict]]]:
    from . import run_ledger

    sources: list[tuple[str, list[dict]]] = []
    paths: list[str] = []
    if options.get("use_builtin_glossary", True):
        builtin = built_in_glossary_path()
        if builtin:
            paths.append(builtin)
    for raw in options.get("glossary_files", []) or []:
        expanded = os.path.expanduser(str(raw))
        if os.path.isfile(expanded):
            paths.append(expanded)

    for path in paths:
        rows = [row for row in read_glossary_csv(path)
                if row["language"] in (None, lang_out)]
        if rows:
            sources.append((f"pdf2rea:{os.path.basename(path)}", rows))

    if options.get("reuse_ledger", True):
        remembered = run_ledger.load_pairs(lang_out)
        if remembered:
            sources.append(("pdf2rea:memory", [
                {"source": source, "target": target, "language": lang_out, "avoid": []}
                for source, target in remembered
            ]))
    return sources


# --------------------------------------------------------------------------- #
# deterministic enforcement
# --------------------------------------------------------------------------- #
def enforce_hard_terms(text: str, source_text: str, hard_terms: list[dict]) -> tuple[str, int, int]:
    """Replace declared-forbidden variants with the mandated target term.

    Returns ``(text, replacements, violations)``.  A violation is a glossary source
    term present in the source whose mandated target is absent from the translation;
    it is counted and logged, never silently rewritten, because we do not know what
    the model emitted instead.
    """
    if not text or not hard_terms:
        return text, 0, 0
    replaced = violations = 0
    segments = _TAG_SPAN.split(text)
    for index, segment in enumerate(segments):
        if not segment:
            continue
        if _TAG_SPAN.fullmatch(segment):
            continue  # never touch tags or placeholders
        for term in hard_terms:
            if term["source"] not in source_text:
                continue
            if term["target"] in segments[index]:
                continue
            violations += 1
            for forbidden in term["avoid"]:
                if forbidden and forbidden in segments[index]:
                    segments[index] = segments[index].replace(forbidden, term["target"], 1)
                    replaced += 1
    joined = "".join(segments)
    return joined, replaced, violations


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #
def normalize_preserving_terms(text, source_text, hard_terms, ledger, enforce_units=True):
    """Declared term spellings take priority over general numeric style."""
    protected = {}
    for term in sorted(hard_terms, key=lambda t: -len(t["target"])):
        if term["source"] not in source_text or term["target"] not in text:
            continue
        token = "{PDF2REATerm" + str(len(protected)) + "}"
        while token in text: token = token[:-1] + "X}"
        protected[token] = term["target"]
        text = text.replace(term["target"], token)
    result = numeric_policy.normalize(text, ledger, enforce_units)
    for token, target in protected.items(): result = result.replace(token, target)
    return result


def install(options: dict) -> None:
    from . import run_ledger

    ledger = numeric_policy.NumericLedger()
    ledger.policy = options.get("numeric_policy", "auto")
    ledger.min_evidence = int(options.get("numeric_min_evidence", 3))
    enforce_units = bool(options.get("enforce_units", True))
    _STATE["ledger"] = ledger
    run_ledger.set_enabled(bool(options.get("reuse_ledger", True)))

    # ---- 3. prompt rules ---------------------------------------------------
    if options.get("prompt_rules", True):
        def _terminology_hook(ctx) -> str:
            if not ctx.items:
                return ctx.prompt
            numeric_rule = NUMERIC_RULES.get(ledger.policy, NUMERIC_RULES["auto"])
            extra_rules = ""
            rules = options.get("custom_rules") or []
            if rules:
                extra_rules = "".join(f"- {rule}\n" for rule in rules if str(rule).strip())
            return llm_hook.insert_instructions(ctx.prompt, CONSISTENCY_RULES.format(
                numeric_rule=numeric_rule, extra_rules=extra_rules
            ))

        llm_hook.register_hook(llm_hook.PRIORITY_TERMS, _terminology_hook)

    # ---- 2. never drop user glossaries ------------------------------------
    def _patch_translation_config(module):
        if not options.get("keep_user_glossaries", True):
            return
        cls = module.SharedContextCrossSplitPart

        def _factory(original):
            def get_glossaries_for_translation(self, auto_extract_enabled):
                result = list(original(self, auto_extract_enabled))
                existing = {g.name for g in result}
                # Upstream returns [auto_extracted_glossary] only, discarding every
                # user glossary loaded from --glossary-files.  Put them back first.
                for glossary in list(getattr(self, "user_glossaries", []) or []):
                    if glossary.name not in existing:
                        result.append(glossary)
                        existing.add(glossary.name)
                for glossary in _STATE["glossaries"]:
                    if glossary.name not in existing:
                        result.append(glossary)
                        existing.add(glossary.name)
                return result

            return get_glossaries_for_translation

        if registry.wrap_method(cls, "get_glossaries_for_translation", _factory,
                                "terminology_merge"):
            registry.note_applied("SharedContext: user glossaries never dropped")

    registry.on_import("babeldoc.format.pdf.translation_config", _patch_translation_config)

    # ---- 1. inject glossaries ---------------------------------------------
    def _patch_llm_translator(module):
        cls = module.ILTranslatorLLMOnly

        def _init_factory(original):
            def __init__(self, translate_engine, translation_config, tokenizer=None):
                _prepare_glossaries(translation_config)
                original(self, translate_engine, translation_config, tokenizer)
                with _LOCK:
                    existing = {g.name for g in self._cached_glossaries}
                    for glossary in _STATE["glossaries"]:
                        if glossary.name not in existing:
                            self._cached_glossaries.append(glossary)

            return __init__

        if registry.wrap_method(cls, "__init__", _init_factory, "terminology_inject"):
            registry.note_applied("ILTranslatorLLMOnly glossary injection wired")

        def _translate_factory(original):
            def translate(self, docs):
                result = original(self, docs)
                _harvest_ledger(self)
                return result

            return translate

        registry.wrap_method(cls, "translate", _translate_factory, "terminology_harvest")

    registry.on_import(
        "babeldoc.format.pdf.document_il.midend.il_translator_llm_only", _patch_llm_translator
    )

    # ---- 4. enforcement at the write-back choke point ---------------------
    def _patch_base_translator(module):
        cls = module.ILTranslator

        def _factory(original):
            def post_translate_paragraph(self, paragraph, tracker, translate_input,
                                         translated_text):
                if not isinstance(translated_text, str) or not translated_text:
                    return original(self, paragraph, tracker, translate_input,
                                    translated_text)
                source_text = getattr(paragraph, "unicode", "") or ""
                text = translated_text
                try:
                    text, replaced, violations = enforce_hard_terms(
                        text, source_text, _STATE["hard_terms"]
                    )
                    unified = normalize_preserving_terms(text, source_text, _STATE["hard_terms"], ledger, enforce_units)
                    if unified != text:
                        _STATE["stats"]["normalized"] += 1
                    text = unified
                    with _LOCK:
                        _STATE["stats"]["enforced"] += replaced
                        _STATE["stats"]["violations"] += violations
                except Exception as error:
                    registry._errors.append(f"terminology enforcement: {error!r}")
                    text = translated_text
                return original(self, paragraph, tracker, translate_input, text)

            return post_translate_paragraph

        if registry.wrap_method(cls, "post_translate_paragraph", _factory,
                                "terminology_enforce"):
            registry.note_applied("ILTranslator post-translate glossary+numeric enforcement")

    registry.on_import(
        "babeldoc.format.pdf.document_il.midend.il_translator", _patch_base_translator
    )


def _prepare_glossaries(translation_config) -> None:
    """Build Glossary objects once per run (they compile hyperscan databases)."""
    from . import options as options_module

    options = _STATE.get("options") or options_module.DEFAULTS["terminology"]
    glossary_module = registry.require("babeldoc.glossary")
    if glossary_module is None:
        return
    lang_out = getattr(translation_config, "lang_out", "zh-CN")
    built, hard_terms = [], []
    for name, rows in collect_glossary_rows(options, lang_out):
        entries = [
            glossary_module.GlossaryEntry(row["source"], row["target"], row["language"])
            for row in rows
        ]
        if not entries:
            continue
        built.append(glossary_module.Glossary(name=name, entries=entries))
        for row in rows:
            if row["avoid"]:
                hard_terms.append(row)
    with _LOCK:
        _STATE["glossaries"] = built
        _STATE["hard_terms"] = hard_terms
    if built:
        registry.note_applied(
            f"terminology: {len(built)} glossaries / "
            f"{sum(len(g.entries) for g in built)} terms enforced"
        )


def _harvest_ledger(translator) -> None:
    from . import run_ledger

    try:
        config = translator.translation_config
        shared = getattr(config, "shared_context_cross_split_part", None)
        pairs = list(getattr(shared, "raw_extracted_terms", []) or [])
        if not pairs:
            auto = getattr(shared, "auto_extracted_glossary", None)
            if auto is not None:
                pairs = [(entry.source, entry.target) for entry in auto.entries]
        if pairs:
            run_ledger.record(pairs, config.lang_out)
    except Exception as error:
        registry._errors.append(f"ledger harvest: {error!r}")


def configure(options: dict) -> None:
    _STATE["options"] = options


def state() -> dict:
    with _LOCK:
        return {
            "glossaries": [g.name for g in _STATE["glossaries"]],
            "terms": sum(len(g.entries) for g in _STATE["glossaries"]),
            "hard_terms": len(_STATE["hard_terms"]),
            "stats": dict(_STATE["stats"]),
            "numeric": _STATE["ledger"].snapshot() if _STATE["ledger"] else {},
        }
