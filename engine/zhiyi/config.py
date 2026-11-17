"""Run configuration."""

from __future__ import annotations

from dataclasses import dataclass, field, fields

LANGUAGES = {
    "zh-CN": "Simplified Chinese", "zh": "Simplified Chinese", "zh-TW": "Traditional Chinese",
    "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German",
    "es": "Spanish", "ru": "Russian", "it": "Italian", "pt": "Portuguese",
}


@dataclass
class Config:
    lang_in: str = "en"
    lang_out: str = "zh-CN"

    # translation service
    service: str = "openai"            # openai | google | pseudo | file
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.2
    threads: int = 4
    qps: float = 4.0
    translations_file: str = ""        # service == "file"

    # what to translate
    pages: list[int] | None = None     # zero-based; None = all
    translate_tables: bool = True
    translate_captions: bool = True
    translate_references: bool = False
    glossary_path: str = ""
    extra_terms: dict[str, str] = field(default_factory=dict)
    auto_glossary: bool = True

    # typesetting
    font_scale: float = 1.0            # global multiplier on source font sizes
    min_font_scale: float = 0.8        # floor for the rare paragraph that cannot fit

    # output
    mono: bool = True
    dual: bool = True
    dual_mode: str = "LR"              # LR (side by side) | TB (alternating pages)
    cache: bool = True

    @property
    def lang_out_name(self) -> str:
        return LANGUAGES.get(self.lang_out, self.lang_out)

    @property
    def lang_in_name(self) -> str:
        return LANGUAGES.get(self.lang_in, self.lang_in)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known and v is not None}
        terms = values.get("extra_terms")
        if isinstance(terms, str):                    # "source = target" per line
            parsed = {}
            for line in terms.splitlines():
                for sep in ("=>", "=", "\t", ",", "，"):
                    if sep in line:
                        source, target = (part.strip() for part in line.split(sep, 1))
                        if source and target:
                            parsed[source] = target
                        break
            values["extra_terms"] = parsed
        config = cls(**values)
        config.threads = max(1, min(16, int(config.threads)))
        config.font_scale = max(0.6, min(1.2, float(config.font_scale)))
        return config
