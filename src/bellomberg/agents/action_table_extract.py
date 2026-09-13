"""
action_table_extract.py — #200c STRUCTURED OUTPUTS per l'ACTION TABLE (15/07).

Il parsing regex dell'ACTION TABLE (memory_db.extract_and_save_decisions +
copia in action_validator) e' fragile: colonne fuori ordine, ticker in
grassetto, celle multilinea o un header leggermente diverso = righe perse o
sporcate IN SILENZIO. Qui la tabella viene TRASCRITTA da Sonnet con un tool
FORZATO (tool_choice) e schema esplicito: il modello non inventa, trascrive.

Contratto: le celle tornano VERBATIM (size_raw resta stringa: la conversione
in EUR la fa sempre memory_db._parse_eur_amount, identica a prima). Il
chiamante usa la regex come FALLBACK DICHIARATO se questa via fallisce.
Costo: 1 chiamata Sonnet ~5k token in / ~400 out per memo (centesimi).
"""
import re as _re
import time as _time
from bellomberg.core.language import prompt_for_language, scoped_language

EMIT_TOOL = {
    "name": "emit_action_table",
    "description": "Trascrivi in forma strutturata le righe DATI dell'ACTION TABLE del memo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string",
                                   "description": "cella azione VERBATIM in maiuscolo, es. BUY/ADD/TRIM/SELL/HOLD/RESEARCH/HEDGE/WATCH"},
                        "ticker": {"type": "string",
                                   "description": "SOLO il ticker, senza grassetto ne' nome esteso, es. ABCD.MI"},
                        "size_raw": {"type": "string",
                                     "description": "cella size/importo VERBATIM (es. '5.000€', '3k', '2-3%'): NON convertirla"},
                        "timing": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                    "required": ["action", "ticker", "size_raw", "timing", "confidence"],
                },
            },
            "table_found": {"type": "boolean",
                            "description": "false SOLO se nel testo non c'e' alcuna ACTION TABLE"},
        },
        "required": ["rows", "table_found"],
    },
}

_SYSTEM = (
    "Sei un estrattore di dati. Nel testo che ricevi c'e' (di solito) la sezione "
    "ACTION TABLE di un memo: una tabella con le decisioni (action, ticker, size, "
    "timing, confidence, eventuale rationale). Trascrivi OGNI riga dati della tabella "
    "chiamando il tool emit_action_table. Regole: trascrivi VERBATIM (niente "
    "conversioni, niente righe inventate, niente righe di header/separatore); ignora "
    "tutto cio' che non e' l'ACTION TABLE (testo, blocchi LINTER/VALIDATOR in coda); "
    "se una tabella non c'e', rows=[] e table_found=false."
)

# Fable 5 16/07 (findings run #45): la premessa "l'ACTION TABLE vive in coda" era FALSA —
# il prompt del Capo la impone in CIMA ("Prima riga: '## ACTION TABLE'", capo.py FORMATO
# FINALE). Sul memo #45 (33.780 char, tabella a char 74) la coda [-12000:] non la
# conteneva: Sonnet rispondeva table_found=false SENZA errore, quindi il chiamante
# (memory_db:858-864) segnava structured_ok=True con 0 righe e il fallback regex non
# partiva mai -> decisions vuota. Bug intermittente: coi memo <12k coda = memo intero.
# La finestra ora PARTE dal marker (stessa ricerca di action_validator._parse_action_rows);
# senza marker resta la coda: e' il comportamento storico, e il fallback regex a valle
# cerca lo stesso marker, quindi fallirebbe comunque.
_WINDOW_CHARS = 12000  # ampiezza invariata: e' il contratto di costo (~5k token in)


@scoped_language
def extract_rows_structured(memo_markdown: str, usage_out: dict = None) -> dict:
    """Ritorna {'rows': [...], 'table_found': bool} o {'error': str}. Non solleva.

    usage_out (opzionale, #44/finding 4): dict MUTATO in-place col consumo della
    chiamata Sonnet, cosi' il chiamante puo' registrarla con blackboard.record_usage
    (prima di oggi questa chiamata REALE non entrava nel conto della run). Firma
    retro-compatibile: chi non passa usage_out non vede alcuna differenza.
    Chiavi: in/out/cache_read/cache_write (schema di red_team.py e specialists/base.py)
    + model, api_calls, duration_s, status. status vale:
      "skipped"       -> NESSUNA chiamata API fatta: il chiamante NON deve registrare
      "ok"            -> token noti
      "usage_unknown" -> chiamata fatta ma la risposta non ha esposto usage (token IGNOTI)
      "api_error"     -> la chiamata e' fallita
    """
    _u_out = usage_out if isinstance(usage_out, dict) else None
    if _u_out is not None:
        _u_out.clear()
        _u_out.update({"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                       "model": None, "api_calls": 0, "duration_s": None,
                       "status": "skipped"})
    try:
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage
        from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
        # 05/09 (ordine PM): modello dal .env (ACTION_EXTRACTOR_MODEL); assente = errore col nome
        MODEL_SYNTHESIZER = _modello_llm("action_extractor")
    except Exception as e:
        return {"error": f"import: {e}"}
    if _u_out is not None:
        _u_out["model"] = MODEL_SYNTHESIZER
    _memo = memo_markdown or ""
    _mark = _re.search(r"##\s*ACTION TABLE", _memo, _re.IGNORECASE)
    text = (_memo[_mark.start():_mark.start() + _WINDOW_CHARS] if _mark
            else _memo[-_WINDOW_CHARS:])
    if not text.strip():
        return {"error": "memo vuoto"}
    _t0 = _time.perf_counter()
    try:
        client = OpenRouterClient(timeout=120.0, max_retries=1)
        resp = client.messages.create(
            model=MODEL_SYNTHESIZER,
            # 26/07 pre-V6: 1500 -> 2100 (+40%, dal +37/38% MISURATO col count_tokens
            # sui prompt di questa fascia). L'ultima run ha emesso 12 righe = 670
            # token di output su Sonnet 4.6; gli stessi diventano ~920 su Sonnet 5.
            # Qui il troncamento non accorcia un testo: ROMPE il JSON del tool, si
            # cade sul fallback regex e le decisioni strutturate si perdono in parte.
            max_tokens=2100,
            # Sonnet 5 (26/07): omesso = adaptive acceso; SPENTO esplicito — qui
            # c'e' tool_choice FORZATO (estrazione meccanica), il thinking non
            # serve e col budget corto lo eroderebbe
            thinking={"type": "disabled"},
            system=prompt_for_language(_SYSTEM),
            tools=[EMIT_TOOL],
            tool_choice={"type": "tool", "name": "emit_action_table"},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as e:
        if _u_out is not None:
            _u_out["api_calls"] = 1
            _u_out["duration_s"] = round(_time.perf_counter() - _t0, 2)
            _u_out["status"] = "api_error"
        return {"error": f"API: {type(e).__name__}: {str(e)[:150]}"}
    if _u_out is not None:
        # la chiamata E' partita: i token sono spesi e vanno dichiarati anche se poi
        # il payload del tool risulta inutilizzabile e si cade sul fallback regex.
        _u_out["api_calls"] = 1
        _u_out["duration_s"] = round(_time.perf_counter() - _t0, 2)
        try:
            _u = resp.usage
            if _u is None:
                raise ValueError("response.usage assente")
            _u_out.update(somma_usage(None, _u))
            _u_out["status"] = "usage_unknown" if _u_out["tokens_status"] == "parziale" else "ok"
        except Exception:
            # token IGNOTI, diverso da zero: si dichiara, non si finge lo 0
            _u_out["status"] = "usage_unknown"
    # 26/07 pre-V6: senza questo controllo un rifiuto dei safeguard (HTTP 200, content
    # vuoto) usciva dal fondo della funzione con "tool_choice ignorato?" — un errore
    # DICHIARATO ma con la CAUSA SBAGLIATA, che avrebbe mandato la diagnosi a caccia
    # di un bug di schema inesistente. Il fallback regex del chiamante resta identico.
    _rif = _refusal_reason(resp, "ACTION_TABLE")
    if _rif:
        if _u_out is not None:
            _u_out["status"] = "refusal"
        return {"error": _rif}
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return {"error": "ACTION TABLE troncata al limite token: estrazione incompleta, usare il fallback dichiarato"}
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "emit_action_table":
            inp = block.input or {}
            rows = inp.get("rows")
            if not isinstance(rows, list):
                return {"error": "payload senza lista rows"}
            clean = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                a, t = str(r.get("action") or "").strip(), str(r.get("ticker") or "").strip()
                if not a or not t:
                    continue
                clean.append({"action": a.upper(), "ticker": t.upper(),
                              "size_raw": str(r.get("size_raw") or ""),
                              "timing": str(r.get("timing") or ""),
                              "confidence": str(r.get("confidence") or "")})
            return {"rows": clean, "table_found": bool(inp.get("table_found", bool(clean)))}
    return {"error": "nessun tool_use nella risposta (tool_choice ignorato?)"}


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    demo = """
## ACTION TABLE
| Action | Ticker | Size | Timing | Confidence | Rationale |
|---|---|---|---|---|---|
| ADD | **BANCA.MI** (Banca sintetica) | 5.000€ | questa settimana | ALTA | SOPRA POLICY: +3pt, NII +18% |
| TRIM | FONDO.L | ~8k | entro venerdi | MEDIA | sconto NAV -33% ma peso 20,8% |
| RESEARCH | 9988.HK | 2-3% | prossima run | BASSA | Alibaba: consensus depresso |
"""
    print(json.dumps(extract_rows_structured(demo), indent=2, ensure_ascii=False))
