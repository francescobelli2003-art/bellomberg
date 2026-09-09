"""
reflection.py — #210 Reflection outcome-grounded (Fase 2, loop che apprende).

Post-run: UNA lezione operativa sintetica (3-6 righe) generata da Sonnet e
ANCORATA agli esiti misurati dallo scorekeeper #190 — mai opinioni senza
numeri. La lezione si salva in data/reflection_lessons.json (runtime, niente
tabelle nuove) e viene iniettata nel PRIMING della run successiva (memoria
del Capo), mai lo storico integrale (pattern TradingAgents).

Modello: MODEL_SYNTHESIZER (Sonnet, config.py) — ok PM 14/07 ("prosegui").
Best-effort ovunque: un fallimento = nessuna lezione, log dichiarato, run
intatta.
"""
import json
import os
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional

from bellomberg.core.paths import DATA_DIR
from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason

LESSONS_PATH = str(DATA_DIR / "reflection_lessons.json")
MAX_LESSONS_KEPT = 24

REFLECTION_PROMPT = """Sei il COACH QUANTITATIVO del comitato Bellomberg. Dopo ogni run ricevi:
(a) il TRACK RECORD misurato in codice delle call passate (hit-rate, edge direzionale per azione/confidence/specialista, peggiori call), (b) l'ACTION TABLE del memo appena prodotto, (c) la lezione della run precedente.

Produci LA LEZIONE DELLA SETTIMANA: 3-6 righe numerate, in italiano, per il Capo della prossima run.

REGOLE FERREE:
- OGNI riga ancorata a un numero del track record (cita hit-rate/edge cosi' come sono, tag [src: scorekeeper]).
- OGNI riga si chiude con un'ISTRUZIONE operativa verificabile ("la prossima volta: ...").
- Se l'ACTION TABLE appena prodotta ripete un pattern che il track record boccia (es. nuovi BUY con hit-rate BUY sotto il 50%), dillo esplicitamente.
- VIETATO: genericita' ("essere piu' prudenti"), ottimismo di cortesia, ripetere la lezione precedente parola per parola (falla EVOLVERE: se e' stata seguita dillo, se e' stata ignorata dillo).
- Campioni piccoli: dichiarali ("n=9, poca significativita'"), non trarne certezze.
Rispondi SOLO con le righe numerate, niente preamboli."""


def _log(msg: str):
    try:
        print(f"[REFLECTION] {msg}", flush=True)
    except OSError:
        pass


def _load_lessons() -> List[Dict[str, Any]]:
    try:
        with open(LESSONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_lessons(lessons: List[Dict[str, Any]]) -> None:
    try:
        os.makedirs(os.path.dirname(LESSONS_PATH), exist_ok=True)
        tmp = LESSONS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lessons[-MAX_LESSONS_KEPT:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, LESSONS_PATH)
    except Exception as e:
        _log("save lessons failed: " + str(e))


def _extract_action_table(memo_markdown: str, max_chars: int = 1200) -> str:
    import re
    # stesso pattern (collaudato) di action_validator._parse_action_rows
    m = re.search(r"##\s*ACTION TABLE.*?\n(\|.*?\|.*?\n)+", memo_markdown or "",
                  re.IGNORECASE | re.DOTALL)
    return m.group(0)[:max_chars] if m else "(ACTION TABLE non trovata nel memo)"


def generate_lesson(memo_markdown: str = "", memo_id: Optional[int] = None,
                    usage_out: Optional[Dict[str, Any]] = None) -> str:
    """Genera e salva la lezione post-run. Ritorna la lezione ('' su fallimento).

    usage_out (opzionale, #44/finding 4): dict MUTATO in-place col consumo della
    chiamata Sonnet, cosi' il chiamante puo' registrarla con blackboard.record_usage
    (prima di oggi questa chiamata REALE non entrava nel conto della run: il totale
    si presentava completo mentendo per omissione). Firma retro-compatibile: chi non
    passa usage_out non vede alcuna differenza.
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
        from bellomberg.agents.scorekeeper import compute_scorecard, format_track_record_for_capo
        # 05/09 (ordine PM): modello dal .env (REFLECTION_MODEL); assente = errore col nome
        MODEL_SYNTHESIZER = _modello_llm("reflection")
    except Exception as e:
        _log("import skip: " + str(e))
        return ""
    if _u_out is not None:
        _u_out["model"] = MODEL_SYNTHESIZER

    try:
        sc = compute_scorecard()  # snapshot fresco dal pre-run
        track = format_track_record_for_capo(sc, max_chars=2000)
    except Exception as e:
        _log("scorecard non disponibile (" + str(e)[:80] + "): niente lezione — "
             "la reflection SENZA esiti misurati sarebbe opinione, non lezione")
        return ""
    # guardia sul DATO (overall.n), non sulla stringa: dal fix 26/07 il blocco
    # "TRACK RECORD n.d. — MISURA FALLITA" e' NON-vuoto per dichiarare il buco
    # al Capo, ma una lezione ancorata a una misura guasta sarebbe finzione.
    if sc.get("overall", {}).get("n", 0) == 0 or not track:
        _log("track record assente o misura fallita (n=0): niente lezione (dichiarato)")
        return ""

    prev = _load_lessons()
    prev_lesson = prev[-1]["lesson"] if prev else "(nessuna: prima reflection)"

    user_msg = (
        "=== TRACK RECORD MISURATO (scorekeeper #190) ===\n" + track
        + "\n\n=== ACTION TABLE DEL MEMO APPENA PRODOTTO ===\n"
        + _extract_action_table(memo_markdown)
        + "\n\n=== LEZIONE DELLA RUN PRECEDENTE (falla evolvere, non ripeterla) ===\n"
        + prev_lesson[:900])

    _t0 = _time.perf_counter()
    try:
        client = OpenRouterClient(timeout=120.0, max_retries=1)
        resp = client.messages.create(
            model=MODEL_SYNTHESIZER,
            # 26/07 pre-V6: 700 -> 1000. Stessa causa del red team, MISURATA con
            # count_tokens su questo prompt: REFLECTION_PROMPT = 373 token su
            # sonnet-4-6 e 515 su sonnet-5 (+38,1%). I 700 tarati su 4.6 valevano
            # ~507 token vecchi: la lezione (3-6 righe ancorate ai numeri) finiva a
            # meta' riga. 1000 ripristina il budget effettivo di prima. Tetto, non spesa.
            max_tokens=1000,
            # Sonnet 5 (26/07): omesso = adaptive acceso; SPENTO esplicito — con
            # un budget cosi' corto il thinking mangerebbe la lezione stessa
            thinking={"type": "disabled"},
            system=REFLECTION_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        if _u_out is not None:
            # la chiamata E' partita: da qui in poi status != "skipped" qualunque cosa
            # accada (i token sono gia' stati spesi e vanno dichiarati).
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
        lesson = "\n".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # 26/07 pre-V6: il rifiuto e' un HTTP 200 senza testo -> qui la lezione
        # usciva "" e la funzione tornava "" ZITTA (nessun log, nessuna riga): il
        # piu' silenzioso dei casi. La lezione NON si scrive col testo del rifiuto
        # (finirebbe nel priming della run successiva): si dichiara nel log della
        # run e nello status della riga di costo.
        _rif = _refusal_reason(resp, "REFLECTION")
        if _rif:
            _log(_rif[:220])
            if _u_out is not None:
                _u_out["status"] = "refusal"
            return ""
        if getattr(resp, "stop_reason", None) == "max_tokens":
            _log("lezione TRONCATA al limite token: non salvata e non usata nel priming")
            return ""
    except Exception as e:
        if _u_out is not None:
            _u_out["api_calls"] = 1
            _u_out["duration_s"] = round(_time.perf_counter() - _t0, 2)
            _u_out["status"] = "api_error"
        _log("API error (nessuna lezione, run intatta): " + str(e)[:120])
        return ""
    if not lesson:
        return ""

    prev.append({"date": datetime.now().isoformat(timespec="seconds"),
                 "memo_id": memo_id, "lesson": lesson})
    _save_lessons(prev)
    _log(f"lezione generata ({len(lesson)} char) e salvata per il priming della prossima run")
    return lesson


def get_latest_lesson_block(max_chars: int = 900) -> str:
    """Blocco per il priming della run SUCCESSIVA (memoria del Capo)."""
    lessons = _load_lessons()
    if not lessons:
        return ""
    last = lessons[-1]
    return ("--- LEZIONE DALL'ULTIMA RUN (#210 reflection, ancorata allo scorekeeper) ---\n"
            + "[" + str(last.get("date", ""))[:10] + "]\n"
            + str(last.get("lesson", "")))[:max_chars]


if __name__ == "__main__":
    # test standalone: genera una lezione dall'ultimo memo reale (chiamata Sonnet VERA)
    from bellomberg.storage.memory_db import MemoryDB
    db = MemoryDB()
    with db._conn() as conn:
        memo_id, md = conn.execute(
            "SELECT id, full_markdown FROM memos WHERE LENGTH(full_markdown) > 1000 "
            "ORDER BY id DESC LIMIT 1").fetchone()
    print(f"Genero lezione dal memo #{memo_id}...")
    lesson = generate_lesson(md, memo_id=memo_id)
    print()
    print(lesson or "(nessuna lezione)")
    print()
    print(get_latest_lesson_block())
