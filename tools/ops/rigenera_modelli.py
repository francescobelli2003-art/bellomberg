# -*- coding: utf-8 -*-
"""rigenera_modelli.py — rigenera i modelli CANONICI (VAL_TICKER.xlsx) col motore
ATTUALE per tutte le posizioni attive non-veicolo (decisione PM 16/07: un solo
modello fatto bene per ogni stock; i vecchi file erano di motori di giugno).
V5 Lotto 2: i veicoli con fonte NAV ufficiale dichiarata dal negozio entrano nel giro
col motore mnav; il nav_target dell'analista si riprende dal sidecar precedente
(M4, mai cancellare la view in silenzio). Gli altri veicoli restano fuori.

Per ogni ticker: generate_valuation() -> file canonico in report/ (sovrascrive;
_FLAGGED se il sanity check blocca) + tesi registrata in valuation_theses (cosi'
la pagina F17 mostra fair value/prezzo/data revisione). I ticker rifiutati dal
motore (veicoli, dati insufficienti) vengono DICHIARATI, non saltati in silenzio.

USO (dalla radice del repo, backend CHIUSO — scrive anche sul DB):
  python scripts\\rigenera_modelli.py
Durata: ~1-3 min per ticker (yfinance/SEC). Nessuna chiamata LLM: costo zero API Anthropic.
"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")
# review Lotto 2 B2: path ancorati alla root del progetto, non alla CWD (lanciato
# da scripts/ il recupero M4 del nav_target non troverebbe i sidecar, in silenzio)
from bellomberg.core.paths import REPORT_DIR


def backend_alive() -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8765))
        s.close()
        return True
    except Exception:
        return False


def main():
    if backend_alive():
        print("ABORT: backend acceso sulla 8765 — chiudi app/backend e rilancia "
              "(questo script scrive anche sul DB: valuation_theses).")
        return
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.valuation.dcf_engine import generate_valuation, DAT_TICKERS
    try:
        from bellomberg.portfolio.sizing_engine import VEHICLE_TICKERS
    except Exception:
        VEHICLE_TICKERS = set()
    # V5 Lotto 2 (audit/18, ok PM): i veicoli con fonte NAV ufficiale (DAT +
    # CEF_SOURCES) ENTRANO nel giro col motore mnav; gli altri veicoli (ETF,
    # holding senza fonte) restano fuori come prima, dichiarati.
    try:
        from bellomberg.valuation.cef_nav import CEF_SOURCES
    except Exception:
        CEF_SOURCES = {}
    mnav_set = {t.upper() for t in DAT_TICKERS} | {t.upper() for t in CEF_SOURCES}

    db = MemoryDB()
    with db._conn() as conn:
        tickers = [r[0] for r in conn.execute(
            "SELECT ticker FROM positions WHERE is_active=1 ORDER BY quantita*prezzo_medio DESC")]
    lavorabili = [t for t in tickers
                  if t.upper() not in VEHICLE_TICKERS or t.upper() in mnav_set]
    saltati = [t for t in tickers if t not in lavorabili]
    print("Posizioni attive: %d | da modellare: %d (mnav: %s) | veicoli senza fonte NAV (fuori): %s"
          % (len(tickers), len(lavorabili),
             ", ".join(t for t in lavorabili if t.upper() in mnav_set) or "-",
             ", ".join(saltati) or "-"))

    ok, flagged, rifiutati = [], [], []
    for tk in lavorabili:
        print("\n--- %s ---" % tk)
        # M4 (review V5, ok PM): per i ticker mnav il nav_target dell'analista viene
        # ripreso dal sidecar del canonico precedente DENTRO generate_valuation
        # (vale per ogni chiamata nuda, non solo per il batch) — qui si dichiara
        # l'esito leggendo il payload. Senza target: canonico informativo (D2).
        try:
            r = generate_valuation(tk, output_dir=REPORT_DIR)
        except Exception as e:
            rifiutati.append((tk, "%s: %s" % (type(e).__name__, str(e)[:100])))
            print("  ERRORE: %s" % (rifiutati[-1][1],))
            continue
        if not isinstance(r, dict) or (not r.get("ok") and r.get("error")):
            rifiutati.append((tk, str((r or {}).get("error", "?"))[:140]))
            print("  RIFIUTATO (dichiarato): %s" % rifiutati[-1][1])
            continue
        if r.get("engine") == "etf_passive":
            rifiutati.append((tk, "ETF: si analizza come esposizione, non a DCF"))
            print("  ETF: nessun modello (corretto)")
            continue
        fv = (r.get("fair_value_final") or r.get("fair_value_weighted")
              or r.get("fair_value_blend") or r.get("fair_value_base"))
        fname = os.path.basename(str(r.get("path") or "?"))
        is_flag = "FLAGGED" in fname.upper() or bool(r.get("valuation_flagged"))
        (flagged if is_flag else ok).append(tk)
        print("  %s | fair value: %s | prezzo: %s | file: %s"
              % ("FLAGGED" if is_flag else "OK", fv, r.get("price"), fname))
        if r.get("engine") == "mnav":
            if r.get("nav_target") is not None:
                print("  nav_target %s (ripreso dal canonico precedente se non passato — M4)"
                      % r["nav_target"])
            if not fv:
                # review Lotto 2 B1: il motivo VERO dal payload, mai cablato
                print("  mnav informativo: FV n.d. DICHIARATO (%s): tesi non salvata, "
                      "la scheda NAV/mNAV vive nel sidecar"
                      % (r.get("fv_note") or "nessun nav_target — D2"))
        # tesi registrata: senza variant_view (batch), DICHIARATO come tale.
        # audit/12 V0.3: i modelli FLAGGED (BLOCK sanity) NON salvano tesi — il numero
        # bocciato non deve diventare quello ufficiale di F17 (caso valore quasi nullo replicato nel DB).
        if fv and is_flag:
            print("  tesi NON salvata (modello FLAGGED dalla sanity): F17 mostra n.d. + motivo")
        elif fv:
            try:
                san = r.get("sanity") or {}
                _vv = ("(rigenerazione batch - nav_target %s ripreso dal canonico "
                       "precedente, M4)" % r["nav_target"]) \
                    if (r.get("engine") == "mnav" and r.get("nav_target") is not None) \
                    else ("(rigenerazione batch col motore corrente - "
                          "senza variant view: la view arriva alla prossima revisione)")
                db.save_valuation_thesis(
                    ticker=tk, variant_view=_vv,
                    growth_path=None, price=r.get("price"), fair_value=fv,
                    ebitda_margin_target=None, terminal_growth=None,
                    engine=r.get("engine"), subsector=r.get("subsector"),
                    sanity_severity=san.get("severity"), sanity_headline=san.get("headline"),
                    profile_key=r.get("profile_key"))  # audit/13 V1.7a: chiave pulita
            except Exception as e:
                print("  NB: tesi NON registrata (%s) — il modello c'e' comunque" % e)

    print("\n================ ESITO ================")
    print("OK: %s" % (", ".join(ok) or "-"))
    print("FLAGGED (aprire con scetticismo): %s" % (", ".join(flagged) or "-"))
    for tk, why in rifiutati:
        print("RIFIUTATO %s: %s" % (tk, why))
    print("Riavvia backend/app e apri F17 per vedere i modelli.")


if __name__ == "__main__":
    main()
