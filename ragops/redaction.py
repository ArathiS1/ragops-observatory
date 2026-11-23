"""PII redaction at ingestion time.

Built-in regex recognisers work with zero dependencies. If Microsoft Presidio is installed
(`pip install ragops-observatory[presidio]`) and RAGOPS_REDACTOR=presidio, it is used instead.
"""

from __future__ import annotations

import hashlib
import os
import re

_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "IP_ADDRESS": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
    "PHONE": re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3,5}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4}(?!\w)"),
}
# order matters: card numbers before phones so long digit runs are classified as cards
_ORDER = ["EMAIL", "SSN", "CREDIT_CARD", "IBAN", "IP_ADDRESS", "PHONE"]


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def redact(text: str | None, entities: list[str] | None = None) -> tuple[str | None, dict[str, int]]:
    """Return (redacted_text, counts_by_entity)."""
    if not text:
        return text, {}
    entities = entities or _ORDER
    if os.environ.get("RAGOPS_REDACTOR") == "presidio":
        return _presidio(text, entities)
    counts: dict[str, int] = {}
    out = text
    for name in _ORDER:
        if name not in entities:
            continue
        pat = _PATTERNS[name]

        def _sub(m: re.Match, _name: str = name) -> str:
            if _name == "CREDIT_CARD" and not _luhn_ok(m.group(0)):
                return m.group(0)
            if _name == "PHONE" and sum(c.isdigit() for c in m.group(0)) < 8:
                return m.group(0)
            counts[_name] = counts.get(_name, 0) + 1
            return f"<{_name}>"

        out = pat.sub(_sub, out)
    return out, counts


# RAGOps entity names <-> Presidio entity names. With Presidio you can also redact names and places.
_TO_PRESIDIO = {"EMAIL": "EMAIL_ADDRESS", "PHONE": "PHONE_NUMBER", "SSN": "US_SSN", "IBAN": "IBAN_CODE",
                "CREDIT_CARD": "CREDIT_CARD", "IP_ADDRESS": "IP_ADDRESS", "PERSON": "PERSON", "LOCATION": "LOCATION"}
_FROM_PRESIDIO = {v: k for k, v in _TO_PRESIDIO.items()}
_engines: dict = {}


def presidio_engines():
    """Analyzer + anonymizer, built once per process (building them loads a spaCy model and takes seconds).

    RAGOPS_PRESIDIO_MODEL picks the spaCy model (default: en_core_web_lg if installed, else en_core_web_sm).
    """
    if not _engines:
        import importlib.util

        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        if os.environ.get("RAGOPS_PRESIDIO_ONLINE", "false").lower() != "true":
            # Presidio's e-mail recogniser uses tldextract, which downloads the public suffix list on first use.
            # Use the snapshot bundled with tldextract instead: no network calls, works air-gapped.
            import tldextract

            tldextract.tldextract.TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
        model = os.environ.get("RAGOPS_PRESIDIO_MODEL") or (
            "en_core_web_lg" if importlib.util.find_spec("en_core_web_lg") else "en_core_web_sm")
        nlp = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": model}]}).create_engine()
        _engines["analyzer"] = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        _engines["anonymizer"] = AnonymizerEngine()
        _engines["model"] = model
    return _engines["analyzer"], _engines["anonymizer"]


def _presidio(text: str, entities: list[str]):
    from presidio_anonymizer.entities import OperatorConfig

    analyzer, anonymizer = presidio_engines()
    ents = [_TO_PRESIDIO.get(e, e) for e in entities]
    results = analyzer.analyze(text=text, entities=ents, language="en",
                               score_threshold=float(os.environ.get("RAGOPS_PRESIDIO_THRESHOLD", "0.4")))
    counts: dict[str, int] = {}
    for r in results:
        name = _FROM_PRESIDIO.get(r.entity_type, r.entity_type)
        counts[name] = counts.get(name, 0) + 1
    # same placeholders as the built-in redactor (<EMAIL>, <PHONE>, ...), so stored data looks the same either way
    ops = {pe: OperatorConfig("replace", {"new_value": f"<{_FROM_PRESIDIO.get(pe, pe)}>"}) for pe in set(ents)}
    return anonymizer.anonymize(text=text, analyzer_results=results, operators=ops).text, counts


def hash_text(text: str | None, salt: str) -> str | None:
    """Used when a project sets store_raw_text=False: keeps joinability without keeping content."""
    if text is None:
        return None
    return "sha256:" + hashlib.sha256((salt + text).encode()).hexdigest()
