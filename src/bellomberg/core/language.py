"""One language per request/run, independent of model settings and old content."""
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect

SUPPORTED_LANGUAGES = ("it", "en")
ACTION_TABLE_HEADERS = ("action", "azione")
POLICY_OVERRIDE_MARKERS = ("SOPRA POLICY", "[OVER-POLICY]")
NEW_FACT_MARKERS = ("NOVITA", "[NEW-FACT]")
_LANGUAGE = ContextVar("bellomberg_language", default=None)


def validate_language(value):
    """Reject unsupported preferences/headers instead of guessing a language."""
    if not isinstance(value, str) or value not in SUPPORTED_LANGUAGES:
        raise ValueError("Unsupported language: expected 'it' or 'en'")
    return value


def capture_language(explicit=None):
    """Capture once at an execution boundary; the durable preference is last."""
    if explicit is not None:
        return validate_language(explicit)
    inherited = _LANGUAGE.get()
    if inherited is not None:
        return inherited
    from bellomberg.storage.preferences import get_language_preference
    return validate_language(get_language_preference())


def current_language():
    return capture_language()


@contextmanager
def language_context(language):
    """Scope resets even on exceptions; asynchronous tasks do not share mutations."""
    selected = validate_language(language)
    token = _LANGUAGE.set(selected)
    try:
        yield selected
    finally:
        _LANGUAGE.reset(token)


def text(it, en, *, language=None):
    """Both translations are explicit: no missing-key fallback to another language."""
    return it if capture_language(language) == "it" else en


def output_language_instruction(language=None):
    selected = capture_language(language)
    return text(
        "LINGUA DEL NUOVO OUTPUT: italiano professionale. Il materiale storico e le "
        "citazioni restano nella lingua originale. Non tradurre ticker, azioni, chiavi "
        "JSON, [src: tool], marker operativi o intestazioni BLUF/ACTION TABLE.",
        "NEW OUTPUT LANGUAGE: professional English. Keep historical material and "
        "verbatim quotations in their original language. Do not translate tickers, "
        "actions, JSON keys, [src: tool], operational markers or BLUF/ACTION TABLE headings.",
        language=selected)


def scoped_language(function):
    """Capture at entry, including async-generator execution and worker-local calls.

    An optional ``language=`` keyword selects this execution, without changing the
    stored preference. Other arguments and the existing function signature survive.
    """
    if inspect.isasyncgenfunction(function):
        @wraps(function)
        async def stream(*args, **kwargs):
            selected = capture_language(kwargs.pop("language", None))
            with language_context(selected):
                async with aclosing(function(*args, **kwargs)) as iterator:
                    async for item in iterator:
                        yield item
        return stream
    @wraps(function)
    def run(*args, **kwargs):
        selected = capture_language(kwargs.pop("language", None))
        with language_context(selected):
            return function(*args, **kwargs)
    return run


def prompt_for_language(template, language=None):
    """Localize authored output directives ONLY; never pass historical/user text.

    Economic instructions, numbers, tool schemas and stable markers are untouched.
    Italian remains the original authored template; English changes its language
    directives and narrative headings, with the same output structure.
    """
    selected = capture_language(language)
    if selected == "en":
        replacements = {
            "Scrivi in ITALIANO professionale": "Write in professional ENGLISH",
            "MAI inglese a meta' frase.": "Do not mix languages within a sentence.",
            "ma la frase che li contiene e' in italiano.": "but write the surrounding sentence in English.",
            "in italiano professionale e diretto": "in professional, direct English",
            "tutte in italiano scorrevole": "all in fluent English",
            "TUTTO IN ITALIANO scorrevole e leggibile": "ALL IN fluent, readable ENGLISH",
            "italiano, con tutte le sezioni": "English, with all the sections",
            "righe numerate, in italiano": "numbered lines, in English",
            "Rispondi in italiano": "Answer in English",
            "Italiano professionale, chiaro, tecnico": "Professional, clear, technical English",
            "Mai telegrafico, mai anglicizzato, mai abbreviato.": "Use complete prose without telegraphic fragments or unnecessary abbreviations.",
            "I VERBI e i CONNETTIVI sono SEMPRE in italiano": "Write VERBS and CONNECTIVES in English",
            "Gli header restano in italiano come sopra.": "Use the English narrative headings shown above.",
            "Accenti italiani consentiti e incoraggiati (apostrofo ammesso dove serve)": "Use standard English spelling and punctuation",
            "## CONTINUITA' DALLA SCORSA SETTIMANA": "## CONTINUITY FROM LAST WEEK",
            "## 1. Regime Macro": "## 1. Macro Regime",
            "## 2. Fotografia del Portafoglio e Salute delle Posizioni": "## 2. Portfolio Overview and Position Health",
            "## 3. Profilo Quantitativo": "## 3. Quantitative Profile",
            "## 4. Lettura Opzioni e Coperture": "## 4. Options and Hedges",
            "## 5. Pipeline Nuove Posizioni": "## 5. New Position Pipeline",
            "## 7. Cripto e DeFi": "## 7. Crypto and DeFi",
            "## 8. Mercati Politici e Predittivi": "## 8. Political and Prediction Markets",
            "## 9. Tesi Non di Consenso": "## 9. Non-Consensus Theses",
            "## 10. Tabella Scenari": "## 10. Scenario Table",
            "## 11. Watchlist della Settimana": "## 11. Weekly Watchlist",
            "## 12. Nota di Chiusura": "## 12. Closing Note",
            "I 3 RISCHI CHE IL CAPO NON DEVE IGNORARE": "THE 3 RISKS THE CAPO MUST NOT IGNORE",
        }
        for original, translated in replacements.items():
            template = template.replace(original, translated)
    return template + "\n\n" + output_language_instruction(selected)
