"""
dcf_engine.py - Orchestratore UNICO di valutazione (#165). Chiude il cerchio:
tassonomia sotto-settore + calibrazione fondamentali + motore giusto + comps omogenei + sanity.

generate_valuation(ticker) ->
  1. yfinance info -> industry/sector/country (iniettabile: fetch_info, per le prove offline)
  2. decidi_percorso() (05/09, classificazione lotto 2): decisione PURA dal negozio dei
     veicoli + sector_taxonomy.classify() -> natura etichettata + profilo granulare
     (engine, assumptions, PEERS veri, multiplo); ogni return porta profile_source e natura
  3. INSTRADA:
       - mnav         -> dcf_mnav (DAT / fondo chiuso dichiarati dal PM: NAV, mai DCF)
       - etf_passive  -> nota di esposizione (NO DCF: e' un paniere)
       - rifiuti DICHIARATI: negozio illeggibile, veicolo senza fonte NAV, profilo
                         SCONOSCIUTO (PRIMA delle cinture), le tre cinture del 13-15/07
       - bank/insurance -> dcf_bank.build_bank_model (residual income + P/TBV)
       - rab          -> dcf_rab (rete regolata)
       - operating    -> dcf_buyside_v3.build_model_v3 (DCF unlevered template-parity;
                         v3 KO = rifiuto dichiarato, NESSUN fallback v2 — audit/13 §4 n.1)
  4. COMPS omogenei: tira EV/EBITDA dei PEERS reali del sotto-settore (stesso settore, non a caso)
  5. SANITY: confronta fair value vs prezzo; warning se ratio estremo (<0.4 o >2.5)

Tutto guarded. Mai eccezioni propagate.
"""
import json
import os
from datetime import datetime
from typing import Dict, Any, List, Optional

from bellomberg.core.paths import PROJECT_ROOT, REPORT_DIR

# 05/09 (classificazione lotto 2): il contratto delle etichette e il negozio dei veicoli
# (solo stdlib: l'import non legge file ne' apre rete; DAT_TICKERS sotto legge il negozio).
import bellomberg.storage.classificazione as cl


def _peer_comps(peers: List[str], exclude: str) -> List[Dict[str, Any]]:
    """Tira EV/EBITDA/Sales dei peer reali (yfinance). Per Comps omogenei."""
    out = []
    try:
        import yfinance as yf
    except ImportError:
        return out
    for p in peers:
        if p.upper() == exclude.upper():
            continue
        try:
            info = yf.Ticker(p).info or {}
            ev = info.get("enterpriseValue")
            ebitda = info.get("ebitda")
            rev = info.get("totalRevenue")
            equity = info.get("marketCap")
            if ev and ebitda:
                out.append({"name": info.get("shortName") or p,
                            "equity": round((equity or 0)/1e6, 1), "ev": round(ev/1e6, 1),
                            "sales": round((rev or 0)/1e6, 1), "ebitda": round(ebitda/1e6, 1),
                            "ebit": round((ebitda or 0)*0.7/1e6, 1)})
        except Exception:
            continue
        if len(out) >= 6:
            break
    return out


def _damodaran_wacc(info, profile, spec, history=None):
    """WACC con rigore Damodaran: bottom-up beta dai peer + synthetic rating cost of debt.
    Ritorna (wacc_dict | None). Best-effort: se mancano dati, il chiamante usa il WACC base.
    Parita' A2 (13/07): EBIT e interest expense VERI dallo storico XBRL quando disponibili;
    ogni proxy residuo viene dichiarato in inputs_note (esposto nel foglio WACC)."""
    try:
        from bellomberg.market_data.damodaran_wacc import enhanced_wacc
        import yfinance as yf
        notes = {}

        def _last_hist(key):
            try:
                serie = ((history or {}).get("items") or {}).get(key)
                if isinstance(serie, dict):
                    ys = sorted(serie)
                    vals = [serie[y] for y in ys if serie[y] is not None]
                    return float(vals[-1]) if vals else None
                if isinstance(serie, (list, tuple)):
                    vals = [v for v in serie if v is not None]
                    return float(vals[-1]) if vals else None
            except Exception:
                return None
            return None

        # EBIT: XBRL operating_income reale, altrimenti proxy 0.8x EBITDA (dichiarato)
        ebit = _last_hist("operating_income")
        if ebit is not None:
            notes["ebit"] = "XBRL operating_income"
        else:
            ebitda = info.get("ebitda")
            ebit = (ebitda * 0.8) if ebitda else None
            notes["ebit"] = "PROXY 0.8 x EBITDA yfinance"
        # interest expense: XBRL reale, altrimenti proxy totalDebt x 5% (dichiarato)
        int_exp = _last_hist("interest_expense")
        if int_exp is not None and int_exp != 0:
            int_exp = abs(int_exp)
            notes["interest"] = "XBRL interest_expense"
        else:
            total_debt = info.get("totalDebt") or 0
            int_exp = total_debt * 0.05 if total_debt else None
            notes["interest"] = "PROXY totalDebt x 5%"
        if ebit and int_exp and int_exp > 0:
            coverage = ebit / int_exp
        else:
            coverage = 12.0
            notes["coverage"] = "PROXY 12x (dati insufficienti)"
        # ---- BETA (audit/13 V1.4): prior Damodaran + bottom-up + shrinkage + clamp ----
        wi = spec["wacc_inputs"]
        try:
            from bellomberg.market_data.damodaran_data import get_industry_field, region_for_country
        except Exception:
            get_industry_field = region_for_country = None
        _reg = region_for_country(info.get("country")) if region_for_country else "Global"
        dam_ind = profile.get("dam_industry")
        prior_b, prior_src = None, None
        if dam_ind and get_industry_field:
            pb = get_industry_field(dam_ind, "beta_u_cash", _reg)
            if pb is not None:
                prior_b = pb["value"]
                prior_src = pb["source"] + ((" | " + pb["note"]) if pb.get("note") else "")
        if prior_b is None:
            prior_b = _beta_u_da_profilo(profile, 1.0)
            prior_src = ("beta_u di tassonomia (override manuale del profilo: nessuna "
                         "ancora Damodaran%s)" % (" per '%s'" % dam_ind if dam_ind else ""))
        # bottom-up dai peer: de-lever col D/E e tax dell'industry Damodaran della
        # REGIONE DEL PEER (non piu' 0.3/0.25 fissi per tutti)
        peer_bus = []
        for p in (profile.get("peers") or [])[:5]:
            if p.upper() == spec["ticker"]:
                continue
            try:
                pinfo = yf.Ticker(p).info or {}
                b = pinfo.get("beta")
                if not (b and 0 < b < 4):
                    continue
                p_de, p_tax = 0.3, wi.get("tax", 0.25)
                if dam_ind and get_industry_field:
                    p_reg = region_for_country(pinfo.get("country"))
                    pd_ = get_industry_field(dam_ind, "de", p_reg)
                    pt_ = get_industry_field(dam_ind, "tax_eff", p_reg)
                    if pd_ is not None:
                        p_de = pd_["value"]
                    if pt_ is not None:
                        p_tax = pt_["value"]
                peer_bus.append(b / (1 + (1 - p_tax) * p_de))
            except Exception:
                continue
        n_peer = len(peer_bus)
        lam = n_peer / (n_peer + 4.0)
        bu_bottom = (sum(peer_bus) / n_peer) if n_peer else None
        beta_u = (lam * bu_bottom + (1 - lam) * prior_b) if bu_bottom is not None else prior_b
        lo_b, hi_b = 0.60 * prior_b, 1.50 * prior_b
        clamped = not (lo_b <= beta_u <= hi_b)
        beta_u = max(lo_b, min(hi_b, beta_u))
        notes["beta"] = ("bottom-up %s peer -> bu %.2f; shrinkage lambda=%.2f su prior %.2f "
                         "[%s] -> beta_u %.2f%s"
                         % (n_peer, bu_bottom if bu_bottom is not None else float("nan"),
                            lam, prior_b, prior_src, beta_u,
                            "; CLAMP [%.2f, %.2f] applicato" % (lo_b, hi_b) if clamped else "")
                         if bu_bottom is not None else
                         "nessun beta peer valido: prior puro %.2f [%s]" % (prior_b, prior_src))
        peer_betas = [beta_u]  # gia' shrunk: enhanced_wacc fa cassa + re-lever
        # ---- D/E (audit/13 V1.5a): reale SOLO a contratto di unita' verificato ----
        # totalDebt e' in financialCurrency, marketCap in valuta di quotazione: si
        # divide solo dopo conversione FX dichiarata; se il cambio manca -> target.
        de_target = wi.get("de", 0.3)
        de_used = de_target
        de_note = "target %s" % (wi.get("sources", {}).get("de") or "spec")
        mc = info.get("marketCap"); td = info.get("totalDebt")
        fin_cur = (info.get("financialCurrency") or "").upper()
        qte_cur = (info.get("currency") or "").upper()
        if mc and td and mc > 0:
            fxr = 1.0
            if fin_cur and qte_cur and fin_cur != qte_cur:
                fxr = _fx_pair_rate(fin_cur, qte_cur)
            if fxr is None:
                notes["de"] = ("D/E reale SALTATO: totalDebt in %s vs marketCap in %s e "
                               "cambio n.d. (contratto di unita' V0) -> %s"
                               % (fin_cur, qte_cur, de_note))
            else:
                de_real = (td * fxr) / mc
                if 0 <= de_real < 5:
                    de_used = 0.5 * de_real + 0.5 * de_target
                    notes["de"] = ("blend 50/50 reale %.2f (totalDebt%s/marketCap) e "
                                   "target %.2f -> %.2f"
                                   % (de_real,
                                      " conv. %s->%s" % (fin_cur, qte_cur)
                                      if fin_cur != qte_cur and fin_cur else "",
                                      de_target, de_used))
                else:
                    notes["de"] = ("D/E reale %.2f fuori bound [0,5): usato %s"
                                   % (de_real, de_note))
        if "de" not in notes:
            notes["de"] = de_note
        cash_pct = 0.0
        cash = info.get("totalCash")
        if mc and cash and mc > 0:
            cash_pct = min(0.5, cash / mc)
        # audit/13 V1.7b: le fonti di rf/erp/crp/tax/de (con data e staleness) viaggiano
        # in inputs_note fino al foglio WACC — il modello resta sfidabile riga per riga
        for k, v in (wi.get("sources") or {}).items():
            if v:
                notes.setdefault("src_" + k, v)
        res = enhanced_wacc(wi["rf"], wi["erp"], wi.get("crp", 0), peer_betas,
                            de=de_used, tax=wi.get("tax", 0.25),
                            interest_coverage=coverage, cash_pct=cash_pct)
        if isinstance(res, dict):
            if res.get("floor_note"):
                notes["floor"] = res["floor_note"]
            res["inputs_note"] = notes
            res["de_used"] = round(de_used, 3)
            res["interest_coverage_used"] = round(coverage, 2)
        return res
    except Exception:
        return None


def _mid_cycle_from_history(history):
    """audit/13 V2.2.4: growth e margine MID-CYCLE dallo storico XBRL (>=6 anni:
    un ciclo non si misura su 3). Ritorna None se lo storico non basta — il
    chiamante DICHIARA il buco, mai una normalizzazione inventata."""
    try:
        items = (history or {}).get("items") or {}
        rev = {y: v for y, v in (items.get("revenue") or {}).items() if v}
        yrs = sorted(rev)
        if len(yrs) < 6:
            return None
        n = yrs[-1] - yrs[0]
        r0, r1 = float(rev[yrs[0]]), float(rev[yrs[-1]])
        if r0 <= 0 or n <= 0:
            return None
        glt = (r1 / r0) ** (1.0 / n) - 1.0
        # bound di plausibilita' mid-cycle (dichiarato): un ciclico non "cresce
        # strutturalmente" oltre queste bande sul lungo periodo
        glt = max(-0.05, min(0.12, glt))
        op = items.get("operating_income") or {}
        margins = [float(op[y]) / float(rev[y]) for y in yrs
                   if op.get(y) is not None and rev.get(y)]
        op_mid = (sum(margins) / len(margins)) if len(margins) >= 4 else None
        # review ESEF 17/07: la fonte e' quella VERA dello storico (SEC o ESEF),
        # mai "SEC" hardcoded su dati che possono venire dal fallback ESEF
        _hsrc = str((history or {}).get("_source") or "storico XBRL")
        return {"growth_lt": round(glt, 4),
                "op_margin_mid": round(op_mid, 4) if op_mid is not None else None,
                "years": "%d-%d" % (yrs[0], yrs[-1]), "n_years": len(yrs),
                "source": "%s (mid-cycle su %d anni)" % (_hsrc, len(yrs))}
    except Exception:
        return None


# 16/07 notte: DAT = si valutano a mNAV (get_dat_metrics), mai a DCF (regola Capo #5).
# 05/09 (classificazione lotto 2): la lista vive nel negozio dei veicoli (tipo 'dat'), qui
# resta la VISTA calcolata all'import per i consumatori (scripts/rigenera_modelli,
# scripts/pulizia_tesi_invalide, specialist_scores); il motore decide per natura.
def dat_del_negozio(negozio=None) -> set:
    """I simboli di tipo 'dat' nel negozio dei veicoli (tesorerie digitali: mNAV, mai DCF).
    Negozio assente/illeggibile = set vuoto (il motivo sta in carica_veicoli()['motivo'])."""
    return set(cl.veicoli_per_tipo("dat", negozio)["tickers"])


DAT_TICKERS = dat_del_negozio()


def _beta_u_da_profilo(profile, default):
    """beta_u del profilo, o `default` se il profilo non lo porta O lo porta a None
    (veicolo_nav, sconosciuto): `.get("beta_u", X)` su una chiave presente-a-None tornava
    None e finiva nel WACC (scettico 04/09)."""
    v = (profile or {}).get("beta_u")
    return default if v is None else v


def _fetch_history(ticker, info):
    """Storico annuale: SEC XBRL -> fallback DICHIARATO ESEF con guardia STALENESS.
    V4 (§9-novies n.1): estratto dal corpo di generate_valuation cosi' lo consuma
    anche il ramo BANCA (prima il return del ramo banca stava PRIMA del fetch: il
    foglio Historical e la serie impairment IFRS9 non arrivavano mai al workbook).
    Logica INVARIATA rispetto al blocco inline (17/07): ritorna dict valido, oppure
    {'error': STALE...} (repository fermo, dati leggibili ma non calibranti), oppure
    None (nessuno storico, dichiarato dal chiamante)."""
    history = None
    try:
        from bellomberg.market_data.sec_xbrl import get_financial_history
        history = get_financial_history(ticker, years=10)
        if history.get("error"):
            history = None
    except Exception:
        history = None
    # V3 §9-sexies n.3 (17/07): fallback DICHIARATO all'ESEF (filings.xbrl.org) per
    # i nomi EU senza filing SEC (.MI del book) — stesso contratto di sec_xbrl, quindi
    # foglio Historical e mid-cycle lo consumano identico. Copertura ~FY2021+, dichiarata.
    if history is None:
        try:
            from bellomberg.market_data.esef import get_esef_history
            _h2 = get_esef_history(ticker, years=10, company_name=info.get("longName"))
            if not _h2.get("error"):
                # guardia STALENESS (17/07, ok PM — caso ciclico: repository fermo a un esercizio obsoleto,
                # il v3 prendeva la BASE ricavi dal 2022 e il FV crollava 4,7 -> 1,4):
                # se l'ULTIMO esercizio disponibile e' vecchio piu' di 2 anni, lo storico
                # NON guida base/driver/mid-cycle — STALE dichiarato (regola 14/07),
                # dati comunque leggibili via tool get_financial_history.
                _yrs2 = _h2.get("years") or []
                _stale_cut = datetime.now().year - 2
                if _yrs2 and max(_yrs2) < _stale_cut:
                    history = {"error": ("storico ESEF STALE: ultimo esercizio FY%d < FY%d "
                                         "(repository fermo) — NON usato per calibrazione e "
                                         "modello, dichiarato" % (max(_yrs2), _stale_cut)),
                               "_source": _h2.get("_source")}
                    print(f"[dcf_engine] storico ESEF {ticker} STALE (ultimo FY {max(_yrs2)}): "
                          "NON usato, dichiarato")
                else:
                    history = _h2
                    print(f"[dcf_engine] storico ESEF per {ticker}: anni {_h2.get('years')} "
                          f"({_h2.get('n_items')} voci)")
            else:
                print(f"[dcf_engine] storico n.d. (SEC e ESEF): {_h2.get('error')}")
        except Exception as _esef_err:
            print(f"[dcf_engine] fallback ESEF fallito ({type(_esef_err).__name__}): "
                  "storico n.d. dichiarato")
    return history


def _prepare_canonical_path(output_dir: str, ticker: str) -> str:
    """C1-v2 16/07 sera (decisione PM): UN SOLO MODELLO CANONICO PER TICKER, senza
    timestamp nel nome. La revisione SOVRASCRIVE il file; lo storico delle tesi vive
    in valuation_theses (DB). Prima di scrivere si spostano in archive/private/attic/val_superseded/
    (quarantena reversibile, review 16/07: mai cancellare — la pagina promette che
    restano scaricabili) la variante _FLAGGED del canonico e gli eventuali file
    stampati legacy dello stesso ticker. Estratto in helper per il ramo mnav V5
    (logica INVARIATA rispetto al blocco inline)."""
    _base = f"VAL_{ticker.replace('.','_')}"
    out_path = os.path.join(output_dir, _base + ".xlsx")
    import glob as _glob
    import shutil as _shutil
    _superseded = str(PROJECT_ROOT / "archive" / "private" / "attic" / "val_superseded")
    for _old in (_glob.glob(os.path.join(output_dir, _base + "_FLAGGED.xlsx"))
                 + _glob.glob(os.path.join(output_dir, _base + "_2*.xlsx"))):
        try:
            os.makedirs(_superseded, exist_ok=True)
            _dest = os.path.join(_superseded, os.path.basename(_old))
            if os.path.exists(_dest):
                os.remove(_dest)  # stessa revisione rigenerata: l'ultima copia vince
            _shutil.move(_old, _dest)
            # review 17/07 F6: il sidecar .payload.json segue il suo xlsx in attic
            _old_sc = os.path.splitext(_old)[0] + ".payload.json"
            if os.path.exists(_old_sc):
                _dest_sc = os.path.join(_superseded, os.path.basename(_old_sc))
                if os.path.exists(_dest_sc):
                    os.remove(_dest_sc)
                _shutil.move(_old_sc, _dest_sc)
        except Exception as _re_err:
            print(f"[dcf_engine] WARN: file precedente non spostabile ({os.path.basename(_old)}): {_re_err}")
    return out_path


# V6 Lotto 2 (audit/19, D1-D4 PM 23/07): lettura del registro guidance per la
# calibrazione. Singleton di processo (niente re-init chroma a ogni nome del batch).
_MEMDB_GUID = None


def _fetch_guidance(ticker):
    """Ritorna (payload get_guidance | None, nota_buco | None). Il DB si apre SOLO
    se il file esiste gia' al path atteso: MAI creare un DB fantasma vuoto
    (incidente app/data 23/07) — registro irraggiungibile = buco DICHIARATO nel
    payload, mai un modello senza guidance in silenzio (regola 14/07)."""
    global _MEMDB_GUID
    try:
        from bellomberg.storage.memory_db import MemoryDB, SQLITE_PATH, CHROMA_PATH
        if not os.path.exists(SQLITE_PATH):
            return None, ("registro guidance NON letto: DB '%s' assente (percorso "
                          "ancorato a memory_db / BELLOMBERG_DATA_DIR) — modello SENZA layer "
                          "guidance, dichiarato" % SQLITE_PATH)
        if _MEMDB_GUID is None:
            # path ESPLICITI (i default del costruttore sono catturati a def-time):
            # l'esistenza controllata sopra e il DB aperto sono SEMPRE lo stesso file
            _MEMDB_GUID = MemoryDB(db_path=SQLITE_PATH, chroma_path=CHROMA_PATH)
        g = _MEMDB_GUID.get_guidance(ticker)
        if not isinstance(g, dict) or g.get("error"):
            return None, ("registro guidance in errore (dichiarato): %s"
                          % (g.get("error") if isinstance(g, dict) else g))
        if not g.get("active"):
            return None, None   # nessuna guidance registrata: non e' un buco
        # review L2 (MEDIA-1): la "nota" di get_guidance conta le righe STALE —
        # si propaga nel payload (guidance_note), cosi' un registro tutto scaduto
        # non fa tornare il CAGR cieco in silenzio (regola 14/07)
        return g, g.get("nota")
    except Exception as e:
        return None, ("registro guidance NON letto (%s: %s) — modello senza layer "
                      "guidance, dichiarato" % (type(e).__name__, e))


def _guidance_vs_consensus(guidance, ticker, fin_currency=None, quote_currency=None,
                           today=None):
    """V6.5 (Lotto 2, payload): guidance mid vs consensus Yahoo su metrica/anno
    comparabili — il contesto beat/miss che l'analista deve commentare. Mappa
    FY corrente -> riga '0y', FY successivo -> '+1y' (convenzione da esercizio
    solare, DICHIARATA). Best-effort: consensus n.d./non comparabile = nota
    dichiarata, mai zitto e mai un numero inventato."""
    import re as _re
    from datetime import date
    rows = [r for r in ((guidance or {}).get("active") or [])
            if isinstance(r, dict) and not r.get("stale")
            and r.get("metric") in ("revenue_growth", "revenue_abs", "eps")]
    if not rows:
        return None
    try:
        from bellomberg.market_data.consensus_estimates import get_consensus
        cons = get_consensus(ticker)
    except Exception as e:
        return {"note": "consensus NON letto (%s): confronto n.d., dichiarato" % type(e).__name__}
    if not isinstance(cons, dict) or cons.get("error"):
        return {"note": "consensus in errore: confronto guidance/consensus n.d., dichiarato"}
    _year_now = (today or date.today()).year
    per_map = {_year_now: "0y", _year_now + 1: "+1y"}
    out = []
    for r in rows:
        m = _re.fullmatch(r"FY(\d{4})", str(r.get("period") or ""))
        cper = per_map.get(int(m.group(1))) if m else None
        if not cper:
            continue
        if r["metric"] == "revenue_abs" and str(fin_currency or "").upper() != "USD":
            out.append({"metric": "revenue_abs", "period": r.get("period"),
                        "note": ("guidance in musd ma consensus in %s: confronto "
                                 "NON fatto (niente cambio zitto)" % fin_currency)})
            continue
        # review L2 (MEDIA-4a): l'eps di guidance e' nella valuta dei FLUSSI, il
        # consensus Yahoo in quella di QUOTAZIONE (ADR: DKK vs USD) — se
        # divergono il rapporto e' spazzatura: confronto NON fatto, dichiarato
        if r["metric"] == "eps" and fin_currency and quote_currency and \
                str(fin_currency).upper() != str(quote_currency).upper():
            out.append({"metric": "eps", "period": r.get("period"),
                        "note": ("guidance eps in %s ma consensus in %s (quotazione): "
                                 "confronto NON fatto (niente cambio zitto)"
                                 % (fin_currency, quote_currency))})
            continue
        est = cons.get("revenue_estimates" if r["metric"] in ("revenue_growth", "revenue_abs")
                       else "eps_estimates")
        if not isinstance(est, list):
            continue
        crow = next((e for e in est if str(e.get("period")) == cper), None)
        if not crow:
            continue
        try:
            if r["metric"] == "revenue_growth":
                cv = float(crow.get("growth"))
                item = {"metric": "revenue_growth", "period": r["period"],
                        "guidance_mid": r.get("value_mid"), "consensus": round(cv, 4),
                        "delta_pp": round((float(r["value_mid"]) - cv) * 100, 1)}
            elif r["metric"] == "revenue_abs":
                cv = float(crow.get("avg")) / 1e6   # consensus in unita' -> musd
                item = {"metric": "revenue_abs (musd)", "period": r["period"],
                        "guidance_mid": r.get("value_mid"), "consensus": round(cv, 1),
                        "delta_pct": round((float(r["value_mid"]) / cv - 1) * 100, 1)}
            else:
                cv = float(crow.get("avg"))
                item = {"metric": "eps", "period": r["period"],
                        "guidance_mid": r.get("value_mid"), "consensus": cv}
                if cv > 0:
                    item["delta_pct"] = round((float(r["value_mid"]) / cv - 1) * 100, 1)
                else:
                    # review L2 (MEDIA-4b): consensus <= 0 inverte il segno del
                    # rapporto — delta ASSOLUTO dichiarato al posto del %
                    item["delta_abs"] = round(float(r["value_mid"]) - cv, 2)
                    item["note"] = "consensus eps <= 0: delta in valore assoluto (il % non e' significativo)"
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        # review L2 (BASSA-4): la convenzione del mapping esce nel payload, non
        # solo nel docstring (fiscal year sfasati possono centrare l'anno sbagliato)
        item["src"] = ("[src: get_guidance] vs [src: consensus Yahoo] | mappa "
                       "FY->0y/+1y da esercizio solare (convenzione dichiarata)")
        out.append(item)
    if not out:
        return {"note": ("guidance presente ma senza riga consensus comparabile "
                         "(periodo/copertura Yahoo): confronto n.d., dichiarato")}
    return out


PERCORSI = ("mnav", "etf_passive", "rifiuto_guasto", "rifiuto_veicolo", "rifiuto_sconosciuto",
            "rifiuto_cintura", "rab", "bank", "operating")


def decidi_percorso(ticker: str, info: Optional[Dict[str, Any]], negozio=None) -> Dict[str, Any]:
    """La decisione PURA di instradamento (zero rete, zero scritture): dato ticker, `.info`
    Yahoo e negozio dei veicoli dice dove va il titolo, con la natura etichettata.
    Torna {"percorso" (uno di PERCORSI), "natura" (Etichetta), "profilo" (classify),
    "motivo" (str per i rifiuti e l'etf, altrimenti None)}.

    Ordine: negozio ASSENTE/ILLEGGIBILE -> rifiuto_guasto (la
    voce del PM potrebbe dire «veicolo»: il dato da solo e' cio' che mando' un fondo chiuso
    al DCF); natura dat, o cef con fonte NAV -> mnav; profilo etf -> etf_passive; holding o
    cef senza fonte -> rifiuto_veicolo; profilo SCONOSCIUTO -> rifiuto_sconosciuto, PRIMA
    delle cinture (scettico 04/09: a valle non misurava niente); le tre cinture del
    13-15/07 intatte -> rifiuto_cintura; poi rab / bank / operating.
    I motivi non nominano MAI altri simboli del negozio: ieri il rifiuto VEICOLO elencava
    la copertura mNAV col book del PM (dcf_engine.py:593)."""
    from bellomberg.market_data.sector_taxonomy import classify
    info = info or {}
    n = negozio if negozio is not None else cl.carica_veicoli()
    t = (ticker or "").upper().strip()
    industry = info.get("industry") or ""
    sector = info.get("sector") or ""
    qt = (info.get("quoteType") or "").upper()
    name = info.get("shortName") or t
    profilo = classify(industry, sector, t, quote_type=qt, negozio=n)
    natura = profilo["_natura"]
    engine = profilo["engine"]
    voce = cl.voce(t, n)

    def _d(percorso, motivo=None):
        return {"percorso": percorso, "natura": natura, "profilo": profilo, "motivo": motivo}

    if n["origine"] in ("assente", "illeggibile"):
        return _d("rifiuto_guasto",
                  f"{name}: negozio dei veicoli {n['origine'].upper()} ({n['motivo']}). La natura del "
                  "titolo NON e' determinabile (potrebbe essere un veicolo dichiarato dal PM): "
                  "nessuna valutazione finche' il file non e' presente e valido, rifiuto dichiarato.")
    if natura.valore == "dat":
        return _d("mnav")
    if natura.valore == "cef":
        if voce is not None and voce["nav_fonte"]:
            return _d("mnav")
        return _d("rifiuto_veicolo",
                  f"{name}: VEICOLO dichiarato dal PM (fondo chiuso) SENZA fonte NAV configurata "
                  "(campo nav_fonte vuoto nel negozio dei veicoli). DCF VIETATO su un veicolo: "
                  "analizzalo come esposizione (get_fundamentals, holdings) o a NAV/sconto quando "
                  "una fonte ufficiale esiste — decisione PM, non un proxy.")
    if engine == "etf_passive":
        return _d("etf_passive",
                  f"{name} e' un ETF/ETN/paniere: non si valuta con DCF. Va analizzato come "
                  "ESPOSIZIONE (fattori, tema, geografia, holdings, TER). Usa l'analisi "
                  "fattoriale (portfolio_factors) e l'edge scan, non un modello DCF.")
    if natura.valore == "holding":
        return _d("rifiuto_veicolo",
                  f"{name}: VEICOLO dichiarato dal PM (holding) SENZA fonte NAV ufficiale. DCF "
                  "VIETATO su un veicolo: analizzalo come esposizione (get_fundamentals, holdings) "
                  "o a NAV/sconto se una fonte esiste; estendere la copertura mNAV = decisione PM "
                  "con fonte NAV nuova.")
    if engine == "sconosciuto":
        if natura.valore is not None:
            # una natura DICHIARATA (negozio) o misurata (quoteType) esiste: il motivo non
            # puo' negarla (review 05/09); senza industry/sector nessun motore parte comunque
            _chi = "il negozio dei veicoli" if natura.fonte == "registro_pm" else "il quoteType Yahoo"
            return _d("rifiuto_sconosciuto",
                      f"{name}: profilo di valutazione SCONOSCIUTO — {profilo['_etichetta'].evidenza}. "
                      f"{_chi} dice natura '{natura.valore}', ma senza industry ne' sector nessun "
                      "motore puo' partire: DCF rifiutato, non un ripiego. Analizzalo come "
                      "esposizione (get_fundamentals, holdings) o completa la voce nel negozio "
                      "dei veicoli (profilo_valutazione).")
        return _d("rifiuto_sconosciuto",
                  f"{name}: profilo di valutazione SCONOSCIUTO — {profilo['_etichetta'].evidenza}. "
                  "Nessuna fonte (negozio dei veicoli, quoteType, industry) dice che strumento "
                  "sia: DCF rifiutato, non un ripiego. Analizzalo come esposizione "
                  "(get_fundamentals, holdings) o dichiara il tipo nel negozio dei veicoli.")
    # fix 13/07: cintura extra oltre a quoteType (feedback PM: 17 VAL_*.xlsx in report/ =
    # DCF su ETF per settimane). Rifiuta quando il quoteType non e' EQUITY e ci sono
    # segnali da FONDO (fundFamily/navPrice/category, o totalAssets senza ricavi) oppure
    # industry+sector vuoti: meglio un rifiuto spiegato che un DCF spazzatura su un paniere.
    _fund_markers = (any(info.get(k) for k in ("fundFamily", "navPrice", "category"))
                     or (info.get("totalAssets") and not info.get("totalRevenue")))
    if qt != "EQUITY" and (_fund_markers or (not industry and not sector)):
        return _d("rifiuto_cintura",
                  f"{name}: quoteType='{qt or 'n/d'}' non-EQUITY con marker da fondo/paniere "
                  "(o senza industry/sector): NON e' una societa' operativa. "
                  "DCF rifiutato: analizzalo come ESPOSIZIONE (get_fundamentals, holdings, tema), "
                  "non con get_valuation.")
    # cintura 15/07: EQUITY "vuota" su Yahoo (niente industry, niente sector, niente
    # ricavi) = profilo da veicolo/holding non operativa, non da societa'. E se anche fosse
    # una societa' vera, senza questi dati il DCF uscirebbe spazzatura: rifiuto spiegato.
    # (Dal 05/09 il caso senza industry ne' sector e' gia' un profilo SCONOSCIUTO sopra:
    # questa cintura resta per gli override di profilo per ticker senza ricavi.)
    if not industry and not sector and not info.get("totalRevenue"):
        return _d("rifiuto_cintura",
                  f"{name}: quoteType EQUITY ma su Yahoo NON ha industry, sector ne' ricavi: "
                  "profilo da veicolo/holding non operativa. DCF rifiutato: analizzalo come "
                  "esposizione o a NAV/sconto, non con get_valuation.")
    if engine == "rab":
        return _d("rab")
    if engine in ("bank", "insurance"):
        return _d("bank")
    return _d("operating")


def generate_valuation(ticker: str, output_dir: str = None, growth_override=None, variant_view=None,
                       ebitda_margin_target=None, terminal_growth=None,
                       roe_path=None, target_payout=None, fade_years=None, cost_of_equity=None,
                       scenarios=None, equity_adjustments=None, stance=None,
                       diluted_shares_m=None, precedents=None, method_weights=None,
                       wacc_delta_bp=None, peers=None, rab=None, segments=None,
                       nav_target=None, fetch_info=None, negozio=None) -> Dict[str, Any]:
    """Punto di ingresso unico. Ritorna dict con path Excel + verdetto + sanity.
    fetch_info (05/09): callable ticker -> dict `.info` al posto di yfinance (prove offline);
    negozio: esito di classificazione.carica_veicoli, None = riletto ora."""
    output_dir = str(REPORT_DIR) if output_dir is None else output_dir
    # review 17/07: peers stringa nuda ("JPM") itererebbe i CARATTERI -> ticker
    # "J","P","M" scaricati come peer dell'analista. Vale per entrambi i motori.
    if isinstance(peers, str):
        peers = [peers]
    # V7 Lotto 3: un dict singolo al posto della lista (LLM che sbaglia formato) si
    # accetta. Un tipo NON-lista (es. stringa) NON si azzera qui: arriva al gancio
    # del ramo operating, che lo DICHIARA nel payload (review codice C1: mai scarti
    # muti, regola 14/07 — prima diventava None e spariva senza traccia).
    if isinstance(segments, dict):
        segments = [segments]
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "error": "yfinance non disponibile"}

    # --- 05/09 (classificazione lotto 2): UNA fetch del .info (iniettabile con
    # `fetch_info`, per le prove offline) e UNA decisione PURA (decidi_percorso) al posto
    # di lista DAT + CEF_SOURCES + classify + VEHICLE_TICKERS. Per il ramo mNAV il .info e'
    # best-effort come ieri (review V5 B2: serve solo per il nome, i numeri vengono dai tool
    # ufficiali); per gli altri rami un errore di fetch resta un errore dichiarato.
    _negozio = negozio if negozio is not None else cl.carica_veicoli()
    tk = yf.Ticker(ticker)          # nessuna rete finche' non si legge un attributo
    _fetch = fetch_info or (lambda _t: (yf.Ticker(_t).info or {}))
    try:
        info = _fetch(ticker) or {}
        _info_err = None
    except Exception as _ie:
        info, _info_err = {}, f"{type(_ie).__name__}: {_ie}"
    dec = decidi_percorso(ticker, info, negozio=_negozio)
    profile, natura = dec["profilo"], dec["natura"]
    # la provenienza viaggia in OGNI return da qui in poi, rifiuti compresi
    _prov = {"profile_source": str(profile["_etichetta"]), "natura": natura.as_dict()}

    # --- V5 mNAV canonici (audit/18, decisioni PM D1-D3 23/07): i veicoli a NAV con
    # fonte ufficiale (natura dat, o cef con nav_fonte, dal negozio dei veicoli) hanno il
    # loro canonico a mNAV/NAV — routing PRIMA dei rifiuti e PRIMA della calibrazione
    # operating (storia ricavi: al motore mnav non serve). Gli ALTRI veicoli restano
    # rifiutati come oggi. D3: un DAT sotto veto GENERA comunque il canonico (il veto
    # governa le decisioni, non l'informazione: il monitor del premio sorveglia il veto).
    if dec["percorso"] == "mnav":
        _minfo = info   # nome dal tool/spec: il canonico non dipende da .info
        # review Lotto 2 M1 (estensione M4, ok PM): una rigenerazione SENZA target
        # — batch, chiamata chat nuda, chiunque — NON cancella la view dichiarata:
        # il nav_target si riprende dal SIDECAR del canonico precedente (la foto
        # della tesi) e si DICHIARA in fonte; alla rilettura ripassa comunque
        # dalla validazione bande. Sidecar illeggibile = dichiarato, mai zitto.
        _navt, _navt_note = nav_target, None
        if _navt is None:
            _scp = os.path.join(output_dir,
                                "VAL_%s.payload.json" % ticker.replace(".", "_"))
            if os.path.exists(_scp):
                try:
                    with open(_scp, encoding="utf-8") as _sf:
                        _prev_sc = json.load(_sf)
                    if _prev_sc.get("nav_target") is not None:
                        _navt = _prev_sc["nav_target"]
                        _navt_note = ("RIPRESO dal canonico precedente (sidecar): una "
                                      "rigenerazione senza target non cancella la view "
                                      "dell'analista (M4, ok PM 23/07)")
                except Exception as _sre:
                    _navt_note = ("sidecar precedente NON leggibile (%s): eventuale "
                                  "nav_target NON ripreso — dichiarato"
                                  % type(_sre).__name__)
        from bellomberg.valuation.dcf_mnav import build_mnav_spec, build_mnav_model
        # lo STESSO negozio della decisione (review 05/09: il motore rileggeva il disco e con
        # un negozio iniettato il rifiuto portava i conteggi del negozio vero)
        mspec = build_mnav_spec(ticker, _minfo, nav_target=_navt,
                                variant_view=variant_view, negozio=_negozio)
        if mspec.get("error"):
            return {"ok": False, "engine": "mnav", "ticker": ticker.upper(),
                    "error": mspec["error"], **_prov}
        if _navt_note:
            mspec["_sources"]["nav_target"] = ((mspec["_sources"].get("nav_target") or "")
                                               + " | " + _navt_note)
            if "NON leggibile" in _navt_note:
                mspec["warnings_spec"].append(_navt_note + ": se avevi un target, "
                                              "rilancia get_valuation con nav_target.")
        os.makedirs(output_dir, exist_ok=True)
        m = build_mnav_model(mspec, _prepare_canonical_path(output_dir, ticker))
        if not m.get("ok"):
            return {"ok": False, "engine": "mnav", "ticker": ticker.upper(),
                    "error": m.get("error") or "workbook mnav non scritto", **_prov}
        m.update({"ticker": ticker.upper(),
                  "company": mspec.get("company_name") or ticker.upper(),
                  "engine": "mnav", "subsector": mspec.get("subsector"),
                  "profile_key": mspec.get("profile_key"),
                  "price": mspec.get("price"),
                  "price_note": mspec.get("price_note"),
                  **_prov})
        # sanity DEDICATA gia' nel payload del builder (D2: mai BLOCK su una view;
        # l'upside e' "alla convergenza del target", non un target price). Niente
        # _sanity_with_fx: FV e prezzo sono GIA' nella stessa valuta — per un fondo quotato in GBX
        # la conversione GBp->USD e' quella del tool cef_nav, stesso tasso del
        # payload live (mai due misure dello stesso cambio, lezione 23/07).
        return _write_payload_sidecar(_bake_values(m))

    if _info_err is not None:
        return {"ok": False, "error": f"yfinance: {_info_err}", **_prov}

    industry = info.get("industry") or ""
    sector = info.get("sector") or ""
    engine = profile["engine"]
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = info.get("shortName") or ticker

    # --- i rifiuti e l'ETF vengono dalla decisione pura (decidi_percorso): negozio
    # illeggibile, veicolo senza fonte NAV (ieri: lista VEHICLE_TICKERS del sizing e un
    # messaggio che elencava il book), profilo SCONOSCIUTO prima delle cinture, le tre
    # cinture del 13-15/07. Qui si consegnano col motivo e la provenienza.
    if dec["percorso"] in ("rifiuto_guasto", "rifiuto_veicolo", "rifiuto_cintura"):
        return {"ok": False, "engine": "unknown", "ticker": ticker.upper(),
                "error": dec["motivo"], **_prov}
    if dec["percorso"] == "rifiuto_sconosciuto":
        return {"ok": False, "engine": "sconosciuto", "ticker": ticker.upper(),
                "error": dec["motivo"], **_prov}

    # --- ETF/ETN/paniere: non valutabile con DCF ---
    if dec["percorso"] == "etf_passive":
        return {"ok": True, "engine": "etf_passive", "ticker": ticker.upper(),
                "valuation": "non applicabile", "message": dec["motivo"],
                "multiple_basis": profile.get("multiple_basis"), **_prov}

    # V6 Lotto 2 (audit/19 §5): la guidance la consuma SOLO il motore operating in
    # v1 — bank/rab hanno la loro variant view, mnav/etf sono gia' usciti sopra.
    _guid, _guid_note = (None, None)
    if engine not in ("bank", "insurance", "rab"):
        _guid, _guid_note = _fetch_guidance(ticker.upper())
        if _guid_note:
            print(f"[dcf_engine] guidance {ticker}: {_guid_note}")
    from bellomberg.valuation.dcf_calibration import build_spec_from_ticker
    spec = build_spec_from_ticker(ticker, growth_override=growth_override, variant_view=variant_view,
                                  ebitda_margin_target=ebitda_margin_target, terminal_growth=terminal_growth,
                                  profile=profile, guidance=_guid)  # audit/13 V1: porta dam_industry per D/E target (e driver in V2)
    if spec.get("error"):
        # review V7 codice (C3): una rete regolata puo' morire qui per la
        # calibrazione OPERATING (storia ricavi, seconda fetch) che al motore RAB
        # non serve — il rifiuto si tagga e si spiega, mai attribuito al motore
        # sbagliato (il RAB usa solo wacc_inputs e price della calibrazione).
        if engine == "rab":
            return {"ok": False, "engine": "rab", "ticker": ticker.upper(),
                    "error": ("calibrazione base fallita PRIMA del motore RAB (il "
                              "rifiuto NON viene dal motore rete regolata; spesso e' "
                              "un transiente yfinance — riprova): " + str(spec["error"])),
                    **_prov}
        return {"ok": False, "error": spec["error"], **_prov}

    # sovrascrivi col profilo granulare del sotto-settore (piu' fine del macro-settore);
    # 05/09: chiave presente-a-None (veicolo_nav/sconosciuto) = resta il beta calibrato
    spec["wacc_inputs"]["beta_u"] = _beta_u_da_profilo(profile, spec["wacc_inputs"]["beta_u"])
    spec["_subsector"] = {"matched": profile.get("_matched"), "industry": industry,
                          "multiple_basis": profile.get("multiple_basis"),
                          "notes": profile.get("notes")}

    # COMPS SEMPRE TROVATI (16/07, richiesta PM) — con GERARCHIA DI FIT (16/07 sera):
    # 1) peer scelti dall'ANALISTA nella chiamata (fittano per business model/margini/
    #    size: "Leonardo non si confronta con Fincantieri solo perche' e' difesa");
    # 2) fallback: lista curata del sub-settore (sector_taxonomy), DICHIARATA come auto.
    # Il motore banca ha gia' il suo canale (bank_peer_comps), qui gli operating.
    if engine not in ("bank", "insurance", "rab") and not spec.get("comps"):
        try:
            from bellomberg.valuation.peer_comps import fetch_peer_comps, select_peer_comps
            if peers:
                # override dell'analista: giudizio suo, fit dichiarato nella variant view
                _auto_comps, _peer_note = fetch_peer_comps(ticker, [str(p) for p in peers])
                _peer_note = "peer SCELTI DALL'ANALISTA; " + _peer_note
            else:
                # default (PM 16/07 sera: "meno prompt, piu' codice, come Bloomberg"):
                # universo dall'industry Yahoo + seed sub-settore, FIT calcolato
                # (size 0.1-10x, margine EBITDA +/-15pp), scarti dichiarati.
                _auto_comps, _peer_note = select_peer_comps(ticker, info, profile.get("peers") or [])
            spec["comps"] = _auto_comps
            spec["_peer_note"] = _peer_note
            print(f"[dcf_engine] comps {ticker}: {_peer_note}")
        except Exception as _pc_err:
            spec["_peer_note"] = f"comps auto falliti ({type(_pc_err).__name__}): buco dichiarato"
            print(f"[dcf_engine] WARN comps {ticker}: {_pc_err}")

    price = spec.get("price")
    # UN SOLO MODELLO CANONICO PER TICKER (C1-v2 16/07): path + quarantena dei
    # superati nell'helper _prepare_canonical_path (condiviso col ramo mnav V5).
    out_path = _prepare_canonical_path(output_dir, ticker)

    # --- RETE REGOLATA (V7 Lotto 2, design audit/15 ok PM 21/07): motore RAB ---
    # Stesso wiring del ramo banca: spec analista+ancora -> workbook -> sanity ->
    # bake -> sidecar. Errore dello spec (es. rab_base assente) = rifiuto spiegato.
    if engine == "rab":
        from bellomberg.valuation.dcf_rab import build_rab_model, build_rab_spec, rab_peer_comps
        rspec = build_rab_spec(ticker, info, spec["wacc_inputs"], rab=rab,
                               variant_view=variant_view, profile=profile,
                               terminal_growth=terminal_growth, ticker_obj=tk)
        if rspec.get("error"):
            return {"ok": False, "engine": "rab", "ticker": ticker.upper(),
                    "error": rspec["error"], **_prov}
        rspec["_subsector"] = spec["_subsector"]
        try:
            if peers:
                _plist = [str(p) for p in peers]
                _geo_note = "peer SCELTI DALL'ANALISTA (gerarchia 16/07)"
            else:
                _plist = list(profile.get("peers") or [])
                _geo_note = "lista seed del profilo regolato (auto, dichiarata)"
            peers_data, _pc_note = rab_peer_comps(
                _plist, ticker, pe_band=profile.get("peer_pe_band"),
                exclude_name=[info.get("longName"), info.get("shortName")])
            _rab_peer_note = _geo_note + " | " + _pc_note
        except Exception as _rp_err:
            peers_data = []
            _plist = [str(p) for p in (peers or [])] or list(profile.get("peers") or [])
            _rab_peer_note = ("peer comps regolati FALLITI (%s): buco dichiarato"
                              % type(_rp_err).__name__)
        print(f"[dcf_engine] peer regolati {ticker}: {_rab_peer_note}")
        r = build_rab_model(rspec, out_path, peers_data=peers_data,
                            peers_note=_rab_peer_note)
        r.update({"ticker": ticker.upper(), "company": name, "engine": "rab",
                  "subsector": profile.get("_matched"),
                  "profile_key": profile.get("_profile_key"),
                  "price": price, "peers": _plist, **_prov})
        if segments:
            # V7 Lotto 3: il layer SOTP e' collaudato sulle INTEGRATE (operating).
            # Sulla rete pura il multi-servizio (es. Snam trasporto+stoccaggio+GNL)
            # e' un residuo P3 dichiarato — mai ignorare l'input in silenzio.
            r["sotp_note"] = ("layer segmenti NON applicato al motore rab in V7 "
                              "(SOTP multi-servizio = residuo P3 dichiarato): "
                              "modello consolidato invariato")
        r = _sanity_with_fx(r, price, info)
        return _write_payload_sidecar(_bake_values(r))

    # --- BANCA / assicurazione (203: input agente + peers + sanity + fair value numerici) ---
    if engine in ("bank", "insurance"):
        from bellomberg.valuation.dcf_bank import build_bank_spec, build_bank_model, bank_peer_comps
        bspec = build_bank_spec(ticker, info, spec["wacc_inputs"],
                                roe_path=roe_path, target_payout=target_payout,
                                fade_years=fade_years, terminal_growth=terminal_growth,
                                cost_of_equity=cost_of_equity, variant_view=variant_view,
                                ticker_obj=tk, profile=profile)  # audit/13 V1.6
        bspec["_subsector"] = spec["_subsector"]
        # residuo (i) §9-quinquies n.5 (23/07): stessa cella upside mista del foglio
        # operating — scatta SOLO con valuta per-share rilevata != quotazione (oggi
        # nessun caso in book: ADR gia' in quotazione, banca domestica EUR/EUR). Contratto NON
        # verificato = niente tasso: il foglio scrive n.d. come il payload flaggato.
        _bfx_fin, _bfx_qt = bspec.get("currency"), info.get("currency")
        if _bfx_fin and _bfx_qt and _bfx_fin != _bfx_qt:
            if (bspec.get("fx_contract") or {}).get("verified"):
                bspec["fx_quote"] = _fx_quote_info(_bfx_fin, _bfx_qt)
            else:
                bspec["fx_quote"] = {"from": _bfx_fin, "to": _bfx_qt,
                                     "error": ("valuta dei per-share NON verificabile: "
                                               "upside cross-valuta n.d.")}
        # 17/07 §9-sexies n.1 (HSBC P/B 7,9 nel foglio della banca cross-listed): peer banca per GEOGRAFIA
        # del ticker (ADR giapponese -> megabanche JP, banca italiana -> banche IT/EU) con la gerarchia
        # 16/07 (analista > auto — prima l'argomento peers era IGNORATO dal ramo banca)
        # e sanity P/B a banda di profilo. Lista e guardie DICHIARATE nel foglio.
        try:
            if peers:
                _plist = [str(p) for p in peers]
                _geo_note = "peer SCELTI DALL'ANALISTA (gerarchia 16/07)"
            else:
                from bellomberg.market_data.sector_taxonomy import bank_peers_for
                _plist, _geo_note = bank_peers_for(info.get("country"),
                                                   profile.get("_profile_key"),
                                                   profile.get("peers"))
            peers_data, _pc_note = bank_peer_comps(
                _plist, ticker, pb_band=profile.get("peer_pb_band"),
                # dedup cross-listing (review 17/07): entrambi i nomi del soggetto
                exclude_name=[info.get("longName"), info.get("shortName")])
            _bank_peer_note = _geo_note + " | " + _pc_note
        except Exception as _bp_err:
            # review 17/07: se l'analista aveva scelto i peer, il payload dichiara
            # QUELLA lista (mai fetchata), non i seed del profilo
            peers_data = []
            _plist = [str(p) for p in (peers or [])] or list(profile.get("peers") or [])
            _bank_peer_note = ("peer comps banca FALLITI (%s): buco dichiarato"
                               % type(_bp_err).__name__)
        print(f"[dcf_engine] peer banca {ticker}: {_bank_peer_note}")
        # V4 (§9-novies n.1): lo storico SEC/ESEF arriva anche al workbook banca —
        # foglio Historical + costo del rischio through-the-cycle (impairment IFRS9).
        # Prima il return di questo ramo stava PRIMA del fetch: mai consumato.
        _bank_history = _fetch_history(ticker, info)
        r = build_bank_model(bspec, out_path, peers_data=peers_data, peers_note=_bank_peer_note,
                             history=_bank_history)
        r.update({"ticker": ticker.upper(), "company": name, "engine": engine,
                  "subsector": profile.get("_matched"),
                  "profile_key": profile.get("_profile_key"),  # audit/13 V1.7a
                  "price": price,
                  "peers": _plist, **_prov})
        if bspec.get("fx_quote"):
            r["fx_quote_sheet"] = bspec["fx_quote"]  # stesso tasso del foglio (residuo i)
        if segments:
            r["sotp_note"] = ("layer segmenti NON applicato al motore banca/assicurazione "
                              "in V7 (dichiarato): modello consolidato invariato")
        r = _sanity_with_fx(r, price, info)
        return _write_payload_sidecar(_bake_values(r))  # P0 17/07: dopo la sanity (rename _FLAGGED incluso)

    # --- OPERATIVA: comps gia' scelti sopra (FIT deterministico o analista) ---
    # audit/12 V0.7: questa riga SOVRASCRIVEVA incondizionatamente i comps del blocco
    # FIT (riga ~250) con la lista seed grezza: il peer FIT era dead code nel flusso
    # principale. Review V0 (finding ALTA): il fallback alla lista grezza scatta SOLO
    # se il selettore FIT e' andato in ERRORE — se il FIT ha esaminato e BOCCIATO tutti
    # i candidati, "comps n.d." E' l'esito onesto (rimettere dentro i seed riaprirebbe
    # self-peer e size 90x che il FIT ha appena scartato, sotto una nota che dice
    # il contrario). Il fallback dichiara i filtri NON applicati e deduplica l'emittente.
    if not spec.get("comps"):
        _pn = str(spec.get("_peer_note") or "")
        if not _pn or _pn.startswith("comps auto falliti"):
            _fb = _peer_comps(profile.get("peers", []), ticker)
            try:
                from bellomberg.valuation.peer_comps import _norm_issuer
                _tn = _norm_issuer(info.get("shortName") or info.get("longName"))
                if _tn:
                    _fb = [c for c in _fb if _norm_issuer(c.get("name")) != _tn]
            except Exception:
                pass
            if _fb:
                spec["comps"] = _fb
                spec["_peer_note"] = ((_pn + " | ") if _pn else "") + \
                    ("FALLBACK lista seed grezza: filtri FIT size/margine NON applicati "
                     "(dedup emittente si')")
    # parita' A2 (13/07): storico XBRL PRIMA del WACC, cosi' EBIT e interest expense
    # veri sostituiscono i proxy (EBIT=0.8xEBITDA, interest=debt*5%) quando esistono.
    # V4: blocco estratto in _fetch_history (condiviso col ramo banca), logica invariata.
    history = _fetch_history(ticker, info)
    # audit/13 V2.2.4 (ok PM): CICLICI — normalizzazione MID-CYCLE dallo storico XBRL
    # (fino a 10 anni, che prima arrivava fin qui e non veniva mai usato): growth =
    # riassorbimento verso il CAGR di lungo periodo, margine EBITDA spostato verso la
    # media di ciclo. Senza XBRL (es. .MI senza filing SEC): DICHIARATO, si resta sul
    # blend/prior di profilo — V3 (esef.py) alzera' il tetto sui titoli italiani.
    # review pre-commit (ALTA A1): il mid-cycle NON sovrascrive mai il growth_path
    # dell'analista — gerarchia V2.2.6: variant view > mid-cycle > profilo.
    # V6 Lotto 2: la gerarchia diventa analista > GUIDANCE > mid-cycle > blend >
    # prior — se la guidance ha guidato il growth, la normalizzazione di ciclo
    # resta come CONFRONTO dichiarato (la societa' conosce il proprio anno 1
    # meglio del CAGR di ciclo), stesso pattern del ramo analista.
    _guid_drove_growth = any(str(u.get("driver") or "").startswith("revenue_growth")
                             for u in (spec.get("_guidance_used") or []))
    _guid_drove_margin = any(("gross_margin" in str(u.get("driver") or "")
                              or "ebitda_margin" in str(u.get("driver") or ""))
                             for u in (spec.get("_guidance_used") or []))
    if profile.get("cyclical") and (growth_override or _guid_drove_growth):
        _mc = _mid_cycle_from_history(history)
        if _mc:
            # review L2 (BASSA-1): il label dice chi ha vinto DAVVERO — un
            # growth_override truthy ma invalido non deve firmare la vittoria
            _who = ("il path dell'analista"
                    if str(spec.get("_growth_source") or "").startswith("agent variant view")
                    else ("la guidance societaria, gerarchia V6" if _guid_drove_growth
                          else "il blend dichiarato (override analista NON valido)"))
            spec["_growth_source"] = ((spec.get("_growth_source") or "")
                                      + " | mid-cycle XBRL %s = %.1f%% (SOLO CONFRONTO: "
                                        "vince %s)"
                                      % (_mc["years"], _mc["growth_lt"] * 100, _who))
            spec.setdefault("_calibration", {})["mid_cycle"] = _mc
    elif profile.get("cyclical"):
        _mc = _mid_cycle_from_history(history)
        if _mc:
            _asm = spec.get("assumptions") or {}
            _tg = (spec.get("terminal") or {}).get("g") or 0.02
            _g_cur = (_asm.get("growth") or [None])[0]
            _glt = _mc["growth_lt"]
            _g0 = (0.5 * _g_cur + 0.5 * _glt) if _g_cur is not None else _glt
            _asm["growth"] = [round(_g0, 3), round(_glt, 3), round(_glt, 3),
                              round(_glt, 3), round((_glt + _tg) / 2.0, 3)]
            _note = ("MID-CYCLE XBRL %s (%d anni): growth path riassorbita verso il "
                     "CAGR di ciclo %.1f%%" % (_mc["years"], _mc["n_years"], _glt * 100))
            if _mc.get("op_margin_mid") is not None and _guid_drove_margin:
                # review L2 (MEDIA-5): un margine ancorato alla GUIDANCE (fonte
                # dichiarata, forward) non si shifta con la media di ciclo TTM —
                # il confronto resta, il numero della fonte non si tocca
                _note += ("; margine NON shiftato: driver di margine da GUIDANCE "
                          "societaria (mid-cycle solo confronto)")
            elif _mc.get("op_margin_mid") is not None:
                # sposta il margine del modello verso la media di ciclo (EBITDA ~ EBIT
                # operativo + D&A): shift sul GM, clampato +/-8pp, dichiarato
                _da0 = (_asm.get("da_tan") or [0.02])[0]
                _em_mid = _mc["op_margin_mid"] + _da0
                _em_now = None
                try:
                    _em_now = float(info.get("ebitdaMargins"))
                except (TypeError, ValueError):
                    pass
                if _em_now:
                    _shift = max(-0.08, min(0.08, _em_mid - _em_now))
                    _asm["gm"] = [round(x + _shift, 3) for x in (_asm.get("gm") or [])]
                    _note += ("; margine EBITDA %.1f%% TTM -> %.1f%% mid-cycle "
                              "(shift %.1fpp sul GM, clamp 8pp)"
                              % (_em_now * 100, min(_em_now + _shift, _em_mid) * 100, _shift * 100))
            spec["_growth_source"] = ((spec.get("_growth_source") or "") + " | " + _note)
            spec.setdefault("_calibration", {})["mid_cycle"] = _mc
        else:
            spec["_growth_source"] = ((spec.get("_growth_source") or "")
                                      + " | MID-CYCLE n.d. (storico XBRL assente o <6 anni): "
                                        "profilo ciclico SENZA normalizzazione, dichiarato")
    # WACC rigoroso Damodaran (bottom-up beta + synthetic rating); fallback al base
    dw = _damodaran_wacc(info, profile, spec, history=history)
    if dw and not dw.get("error"):
        spec["wacc_inputs"]["beta_u"] = dw.get("beta_unlevered") or spec["wacc_inputs"]["beta_u"]
        spec["_damodaran_wacc"] = dw
    # 204b: VAL v3 template-parity (storico XBRL + scenari dell'analista con commentary).
    # audit/13 §4 n.1 (16/07, ok PM): il fallback al builder v2 e' stato RIMOSSO — il
    # payload v2 non ha fair_value_*, quindi guard FV<=0/quasi-zero e sanity V0 venivano
    # SALTATE in silenzio e il foglio girava coi default WACC pre-istituzionali. Un
    # modello che il builder non sa costruire non degrada di nascosto: e' un rifiuto spiegato.
    r = None
    _v3_err = "motivo non catturato"
    try:
        from bellomberg.valuation.dcf_buyside_v3 import build_model_v3
        spec_v3 = dict(spec)
        wi = spec.get("wacc_inputs") or {}
        dw = spec.get("_damodaran_wacc") or {}
        _wacc = dw.get("wacc") or wi.get("wacc")
        if _wacc is None:
            # audit/13 V0-bis: il vecchio fallback era MUTO — ora la formula di riserva
            # (che ignora CRP/D-E/kd) viene dichiarata in sources e arriva al foglio
            _wacc = max((wi.get("rf") or 0.04) + (wi.get("beta_u") or 1.0) * (wi.get("erp") or 0.05),
                        (wi.get("rf") or 0.04) + 0.03)
            wi.setdefault("sources", {})["wacc"] = (
                "FALLBACK dichiarato: motore Damodaran n.d. -> wacc = max(rf+beta_u*erp, "
                "rf+300bp) SENZA CRP/D-E/kd (formula di riserva)")
        _sh = (info.get("sharesOutstanding") or 0) / 1e6
        _nd = ((info.get("totalDebt") or 0) - (info.get("totalCash") or 0)) / 1e6
        # 204b-FIX (bug fair value azionario molto sotto il prezzo): build_spec_from_ticker scrive i driver
        # ANNIDATI (assumptions/actuals/terminal), ma dcf_buyside_v3 li legge FLAT a livello spec.
        # Senza questo ponte il v3 ripiegava su growth 8% piatto + opex generici -> fair value
        # crollato. Guarded: se un driver manca resta None e il v3 usa i suoi default come prima.
        _asm = spec.get("assumptions") or {}
        def _a0(_key):
            _v = _asm.get(_key)
            if isinstance(_v, (list, tuple)) and _v:
                try:
                    return float(_v[0])
                except (TypeError, ValueError):
                    return None
            return None
        _gp = _asm.get("growth")
        if isinstance(_gp, (list, tuple)) and _gp:
            try:
                _gp = [float(x) for x in _gp if x is not None][:5] or None
            except (TypeError, ValueError):
                _gp = None
        else:
            _gp = None
        _sm = _a0("sm"); _ga = _a0("ga")
        _sga = (_sm + _ga) if (_sm is not None and _ga is not None) else (_sm if _sm is not None else _ga)
        _rev_hist = (spec.get("actuals") or {}).get("revenue")
        _rev_hist = _rev_hist if (isinstance(_rev_hist, (list, tuple)) and _rev_hist) else None
        _term_g = spec.get("_terminal_g") or (spec.get("terminal") or {}).get("g") or terminal_growth
        # parita' A7 (14/07, audit/09): bridge to equity parametrico. Voci dall'AGENTE
        # (value_m FIRMATO in milioni: negativo = debt-like, positivo = associates)
        # + auto da bilancio yfinance (minorities/preferred) se l'agente non le passa.
        # Lista vuota = comportamento invariato (solo net debt).
        # fix review B: un dict al posto della lista (LLM che sbaglia formato) faceva
        # esplodere lo slicing -> TUTTO il v3 degradava a v2 in silenzio. Coercizione.
        if not isinstance(equity_adjustments, list):
            equity_adjustments = None
        if not isinstance(precedents, list):
            precedents = None
        _adj = []
        for _a in (equity_adjustments or [])[:8]:
            if not isinstance(_a, dict):
                continue
            try:
                _v = float(_a.get("value_m"))
            except (TypeError, ValueError):
                continue
            if _v != _v or _v in (float("inf"), float("-inf")):
                continue
            _lab = str(_a.get("label") or "").strip()
            if not _lab:
                continue
            _adj.append({"label": _lab[:60], "value_m": round(_v, 1),
                         "commentary": str(_a.get("commentary") or "")[:220]})
        try:
            _bs = tk.balance_sheet
            if _bs is not None and len(_bs.columns):
                _have = " ".join(x["label"].lower() for x in _adj)
                # dedup su keyword EN+IT: se l'agente ha gia' passato la voce (in
                # qualunque lingua), niente auto-riga -> mai doppio conteggio.
                # 'noncontrol'/'non-control' = termine ufficiale ASC 810/IFRS 10 (NCI);
                # NON usare 'nci' nudo: e' substring di 'financing' e sopprimerebbe a torto.
                for _row, _lab, _kws in (("Minority Interest", "Minority interest",
                                          ("minorit", "minoranz", "noncontrol", "non-control")),
                                         ("Preferred Stock", "Preferred stock", ("preferred", "privilegiat"))):
                    if _row in _bs.index and not any(k in _have for k in _kws):
                        _serie = _bs.loc[_row].dropna()
                        if len(_serie) and float(_serie.iloc[0]) > 0:
                            _adj.append({"label": _lab, "value_m": round(-float(_serie.iloc[0]) / 1e6, 1),
                                         "commentary": "[src: yfinance balance_sheet] voce debt-like dal bilancio (auto)"})
        except Exception:
            pass
        try:
            _dil = float(diluted_shares_m) if diluted_shares_m else None
            _dil = _dil if (_dil and _dil > 0) else None
        except (TypeError, ValueError):
            _dil = None
        # sanity: fully diluted >= basic sempre (GAAP/IFRS escludono gli anti-diluitivi);
        # 5% di tolleranza per mismatch temporali basic-corrente vs diluted-weighted-avg
        if _dil:
            try:
                _base_sh = float(spec.get("shares_m") or spec.get("shares") or _sh or 0)
            except (TypeError, ValueError):
                _base_sh = 0
            if _base_sh > 0 and _dil < _base_sh * 0.95:
                print(f"[dcf_engine] diluted_shares_m {_dil:.1f} < shares base {_base_sh:.1f}: "
                      "anti-diluitivo (probabile errore), ignorato")
                _dil = None
        # parita' B9/B8/B14 (14/07, audit/09): precedents dell'agente (validati poi
        # da _valid_precedents nel builder), pesi metodi (devono sommare a 1),
        # delta WACC bear/bull in basis point (cap +/-500, il base resta headline)
        _prec = [t for t in (precedents or [])[:12] if isinstance(t, dict)]
        _mw = None
        if isinstance(method_weights, dict):
            try:
                _wd, _wc = float(method_weights.get("dcf")), float(method_weights.get("comps"))
                if _wd >= 0 and _wc >= 0 and abs(_wd + _wc - 1) < 0.01:
                    _mw = {"dcf": _wd, "comps": _wc}
                else:
                    print(f"[dcf_engine] method_weights ignorati (devono sommare a 1): {method_weights}")
            except (TypeError, ValueError):
                _mw = None
        _dbp = None
        if isinstance(wacc_delta_bp, dict):
            _tmp = {}
            for _k in ("bear", "bull"):
                try:
                    _v = float(wacc_delta_bp.get(_k))
                except (TypeError, ValueError):
                    continue
                if _v == _v and abs(_v) <= 500 and _v != 0:
                    _tmp[_k] = round(_v, 1)
            _dbp = _tmp or None
        # G4 audit/14 (17/07): input per l'IRR di holding period — entry e dividendo
        # per azione ALLINEATI alla valuta dei FLUSSI del modello (financialCurrency):
        # mai unita' miste (lezione ADR audit/12 V0.1). Cambio n.d. = IRR n.d. dichiarato.
        _fin_c = info.get("financialCurrency") or info.get("currency") or "USD"
        _quote_c = info.get("currency") or _fin_c
        _hp_price, _hp_div, _hp_note = None, None, None
        try:
            _hp_rate = _fx_pair_rate(_quote_c, _fin_c) if price else None
            if price and _hp_rate:
                _hp_price = round(float(price) * _hp_rate, 4)
                _hp_note = ("entry in %s (prezzo %s convertito @%.6f)" % (_fin_c, _quote_c, _hp_rate)
                            if _hp_rate != 1.0 else "entry/dividendi in %s (= valuta di quotazione)" % _fin_c)
                # review G4 17/07 (ALTA, misura 4/4 .L con dividendo): dividendRate dei
                # .L e' in STERLINE mentre il prezzo e' in pence — il tasso del prezzo
                # lo sbaglierebbe di 100x: tasso SEPARATO per il dividendo.
                _dr = info.get("dividendRate")
                if _dr:
                    _dr_rate = _fx_pair_rate("GBP" if _quote_c in ("GBp", "GBX") else _quote_c, _fin_c)
                    if _dr_rate:
                        _hp_div = round(float(_dr) * _dr_rate, 4)
                        if _dr_rate != _hp_rate:
                            _hp_note += " | dividendo convertito @%.6f (dividendRate in GBP, non pence)" % _dr_rate
                    else:
                        _hp_div = 0.0
                        _hp_note += " | dividendo NON convertibile (cambio n.d.): 0 DICHIARATO"
                else:
                    # review 17/07: mai uno 0 zitto al posto del dato (regola 14/07)
                    _hp_div = 0.0
                    _hp_note += " | dividendRate Yahoo n.d.: dividendi 0 nel holding IRR (dichiarato)"
            elif price:
                _hp_note = "IRR n.d.: cambio %s->%s non disponibile (dichiarato)" % (_quote_c, _fin_c)
            else:
                _hp_note = "IRR n.d.: prezzo corrente mancante (dichiarato)"
        except (TypeError, ValueError) as _hp_err:
            _hp_price, _hp_div = None, None
            _hp_note = "IRR n.d.: prezzo/dividendo non numerici (%s)" % _hp_err
        spec_v3.update({
            "company_name": name, "price": price, "ticker": ticker.upper(),
            "currency": info.get("financialCurrency") or info.get("currency") or "USD",
            "hp_entry_price": _hp_price, "hp_dividend_ps": _hp_div, "hp_note": _hp_note,
            # residuo (i) §9-quinquies n.5: senza prezzo non c'e' blocco upside nel
            # foglio -> niente fetch del cambio a vuoto
            "fx_quote": (_fx_quote_info(_fin_c, _quote_c) if price else None),
            "shares_m": spec.get("shares_m") or spec.get("shares") or _sh,
            "net_debt": spec.get("net_debt") if spec.get("net_debt") is not None else _nd,
            "gross_margin": spec.get("gross_margin") or _a0("gm") or info.get("grossMargins"),
            "wacc_inputs": {**wi, "wacc": _wacc},
            "terminal_g": _term_g,
            "_variant_view": variant_view,
            # --- driver FLAT letti da dcf_buyside_v3._default_scenarios / _scenario_numbers ---
            "growth_path": _gp,
            "rnd_pct": _a0("rd"),
            "sga_pct": _sga,
            "capex_pct": _a0("capex"),
            "nwc_pct": _a0("nwc"),
            "tax_rate": _a0("tax"),
            # audit/13 V2: driver D&A reale, spread scenari per profilo, ancora RONIC
            "da_tan_pct": _a0("da_tan"),
            "scenario_spread": spec.get("scenario_spread"),
            "ronic_anchor": spec.get("ronic_anchor"),
            "revenue_m": (_rev_hist[-1] if _rev_hist else None),
            "revenue_hist_m": _rev_hist,
            # A7: bridge to equity parametrico + stance + diluizione
            "equity_adjustments": _adj,
            "stance": (stance if stance in ("buy", "sell", "neutral") else None),
            "diluted_shares_m": _dil,
            # B9/B8/B14: precedents, pesi metodi, delta WACC per scenario
            "precedents": _prec,
            "method_weights": _mw,
            "wacc_delta_bp": _dbp,
        })
        r = build_model_v3(spec_v3, out_path, scenarios=scenarios, history=history)
        if not r.get("ok"):
            _v3_err = str(r.get("error") or "builder v3: ok=False senza motivo")
            r = None
    except Exception as _e:
        _v3_err = f"{type(_e).__name__}: {_e}"
        r = None
    if r is None:
        print(f"[dcf_engine] v3 fallito su {ticker}: {_v3_err} -> rifiuto dichiarato (no fallback v2)")
        return {"ok": False, "engine": engine, "ticker": ticker.upper(),
                "subsector": profile.get("_matched"), **_prov,
                "error": (f"{name}: builder v3 fallito ({_v3_err}). Nessun fallback silenzioso "
                          "al builder v2 (audit/13 §4 n.1): modello NON prodotto. Riprova "
                          "passando input espliciti (growth_path, ebitda_margin_target, peers) "
                          "o segnala il caso al PM.")}
    # residuo (i) §9-quinquies n.5: il tasso scritto nel foglio viaggia col payload,
    # cosi' _convert_fx_to_quote usa la STESSA misura (mai foglio e payload divergenti)
    if spec_v3.get("fx_quote"):
        r["fx_quote_sheet"] = spec_v3["fx_quote"]
    # --- V7 Lotto 3 (Mattone C audit/15, ok PM 21/07): layer SEGMENTI -> foglio SOTP ---
    # SENZA `segments` il comportamento e' IDENTICO a oggi (retrocompatibilita' totale).
    # Il foglio si aggiunge PRIMA di sanity/bake: fair_value_sotp inizia per "fair_value"
    # quindi la conversione FX payload->quotazione lo tratta come gli altri; l'headline
    # della sanity resta il CONSOLIDATO (la catena final/weighted/blend/base non cambia).
    if segments:
        try:
            _hfv = None
            for _k in ("fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base"):
                if isinstance(r.get(_k), (int, float)):
                    _hfv = r[_k]
                    break
            # review codice C7: un ebitda/ricavi Yahoo NON numerico non deve uccidere
            # il SOTP (serve solo alla guardia di copertura) — filtro _sotp_num qui
            _ceb = _sotp_num(info.get("ebitda"))
            _crev = _sotp_num(info.get("totalRevenue"))
            _sotp = _compute_sotp(
                segments, net_debt=spec_v3.get("net_debt"),
                adjustments=spec_v3.get("equity_adjustments"),
                shares_m=(spec_v3.get("diluted_shares_m") or spec_v3.get("shares_m")),
                headline_fv=_hfv,
                consol_ebitda_m=(_ceb / 1e6 if _ceb else None),
                consol_revenue_m=(_crev / 1e6 if _crev else None))
            if _sotp:
                _append_sotp_sheet(r["path"], _sotp, ticker.upper(), spec_v3.get("currency"))
                r.update({"fair_value_sotp": _sotp["fv_ps"],
                          "sotp_delta_pct": _sotp["delta_pct"],
                          "sotp_ev_total": _sotp["ev_total"],
                          "sotp_n_segments": _sotp["n_segments"],
                          "sotp_incomplete": _sotp["incomplete"] or None,
                          "sotp_note": _sotp["note"],
                          "sotp_warnings": _sotp["warnings"] or None})
                print(f"[dcf_engine] SOTP {ticker}: {_sotp['n_segments']} segmenti, "
                      f"FV {_sotp['fv_ps']}, delta {_sotp['delta_pct']}%")
            else:
                # review codice C1: formato inutilizzabile = buco DICHIARATO, mai un
                # payload identico a una chiamata senza segments
                r["sotp_note"] = ("segments NON interpretabili (attesa lista di oggetti "
                                  "{name, engine, ...}): SOTP NON prodotto — dichiarato")
        except Exception as _sotp_err:
            # il foglio SOTP e' un layer AFFIANCATO: se muore, il consolidato resta
            # valido ma il buco si DICHIARA nel payload (mai sparire in silenzio)
            r["sotp_note"] = (f"SOTP NON PRODOTTO ({type(_sotp_err).__name__}: {_sotp_err}) "
                              "— buco dichiarato, modello consolidato valido")
            print(f"[dcf_engine] WARN SOTP {ticker}: {r['sotp_note']}")
    r = _sanity_with_fx(r, price, info)
    # audit/13 V2.2.6 (review B9, ok PM): growth da SOLO profilo (storia proxy o
    # insufficiente) e NESSUNA variant view -> il numero non guida decisioni:
    # almeno WARN dichiarato (upside neutro in F17, come gli altri WARN).
    if ("PURO" in (spec.get("_growth_source") or "")) and not variant_view and r.get("sanity"):
        _s = dict(r["sanity"])
        if (_s.get("severity") or "OK") == "OK":
            _hl = ("growth da SOLO profilo (storia sottile/proxy) senza variant view "
                   "dell'analista: numero NON actionable")
            _s["severity"] = "WARN"
            _s["headline"] = (_s["headline"] + " | " + _hl) if _s.get("headline") else _hl
            r["sanity"] = _s
            # review pre-commit (M6): non-actionable = fuori dall'ACTION TABLE, non
            # solo WARN in pagina (audit/13 V2.2.6)
            r["exclude_from_action_table"] = True
    r.update({"ticker": ticker.upper(), "company": name, "engine": r.get("engine") or "operating",
              "subsector": profile.get("_matched"),
              "profile_key": profile.get("_profile_key"),  # audit/13 V1.7a
              "price": price,
              # 05/09: provenienza del profilo e natura del titolo nel payload
              "profile_source": _prov["profile_source"], "natura": _prov["natura"],
              "damodaran_wacc": spec.get("_damodaran_wacc"),
              # audit/13 V2: la PROVENIENZA di growth e driver esce anche nel payload
              # (non solo nel foglio): l'agente vede da dove nasce il numero
              "growth_source": spec.get("_growth_source"),
              # V6 Lotto 2: righe guidance consumate (fonte+vintage), buco del
              # registro dichiarato, confronto guidance vs consensus (V6.5)
              "guidance_used": spec.get("_guidance_used"),
              "guidance_note": _guid_note,
              # V6.5 (Lotto 3): deviazione analista vs guidance attiva -> nudge
              "guidance_deviation_pp": spec.get("_guidance_deviation_pp"),
              "guidance_vs_consensus": (_guidance_vs_consensus(
                  _guid, ticker.upper(), fin_currency=spec.get("currency"),
                  quote_currency=info.get("currency"))
                  if _guid else None),
              "calibration_notes": {k: (spec.get("_calibration") or {}).get(k)
                                    for k in ("driver_sources", "da_note", "mid_cycle",
                                              "prior_source", "cyclical", "currency_note")},
              "n_comps": len(spec["comps"]), "peers_used": [c["name"] for c in spec["comps"]],
              "multiple_basis": profile.get("multiple_basis"),
              "subsector_notes": profile.get("notes")})
    return _write_payload_sidecar(_bake_values(r))  # P0 17/07: dopo la sanity (rename _FLAGGED incluso)


def _fx_pair_rate(src, dst):
    """Cambio src->dst dall'ultimo close yfinance (pair =X). Gestisce GBp/GBX (pence).
    Ritorna 1.0 (o il solo fattore pence) se stessa valuta, None se il cambio non e'
    disponibile — MAI inventare un tasso."""
    if not src or not dst:
        return None
    mult = 1.0
    if src in ("GBp", "GBX"):
        src, mult = "GBP", mult / 100.0
    if dst in ("GBp", "GBX"):
        dst, mult = "GBP", mult * 100.0
    if src == dst:
        return mult
    try:
        import yfinance as yf
        h = yf.Ticker(f"{src}{dst}=X").history(period="5d")
        if h is not None and len(h):
            px = float(h["Close"].iloc[-1])
            if px == px and px > 0:
                return px * mult
    except Exception:
        pass
    return None


def _fx_quote_info(fin, quote):
    """Residuo (i) §9-quinquies n.5 (23/07, opzione A PM): tasso modello->quotazione
    calcolato PRIMA del builder, cosi' il foglio Thesis mostra il FV nelle DUE valute
    (cella viva) e l'upside usa SOLO la cella convertita; _convert_fx_to_quote riusa
    lo STESSO tasso via fx_quote_sheet — una sola misura, foglio e payload mai
    divergenti. None = stessa valuta (foglio invariato); cambio n.d. = errore
    dichiarato nel foglio al posto di un upside a valute miste (regola 14/07)."""
    if not fin or not quote or fin == quote:
        return None
    rate = _fx_pair_rate(fin, quote)
    if rate:
        # review 23/07 (BASSA-2): cifre SIGNIFICATIVE, non decimali — round(rate, 6)
        # su un tasso ~1e-4 terrebbe 2 cifre e uno <5e-7 diventerebbe uno 0 muto
        rate = float(f"{rate:.6g}")
    if not rate:
        return {"from": fin, "to": quote,
                "error": f"cambio {fin}->{quote} non disponibile: upside cross-valuta n.d."}
    # review 23/07 (BASSA-3): GBP<->pence e' una convenzione di unita', non un cambio
    # di mercato — dichiararla "yfinance" etichetterebbe male il proxy (regola 14/07)
    _nf = "GBP" if fin in ("GBp", "GBX") else fin
    _nq = "GBP" if quote in ("GBp", "GBX") else quote
    src = "fattore pence GBP<->GBp (unita', non FX)" if _nf == _nq else "yfinance"
    return {"from": fin, "to": quote, "rate": rate, "src": src}


# ============================================================
# V7 Lotto 3 (Mattone C audit/15, ok PM 21/07) — layer SEGMENTI -> SOTP
# ============================================================

def _sotp_num(v):
    """Float finito o None — mai coercizioni zitte di stringhe/NaN."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (f == f and f not in (float("inf"), float("-inf"))) else None


def _compute_sotp(segments, net_debt, adjustments, shares_m, headline_fv,
                  consol_ebitda_m=None, consol_revenue_m=None):
    """Mirror Python del foglio 'SOTP (segmenti)' — puro, offline-testabile.
    Segmenti DICHIARATI DALL'ANALISTA (segment notes di bilancio, [src] in variant
    view): {name, engine: rab|multiple|operating, ...input del metodo...}.
    Il CONSOLIDATO resta l'headline (decisione PM n.4): il SOTP esce AFFIANCATO con
    la riga 'Delta vs consolidato' (G5, stile Kairos). Un segmento senza input validi
    = riga n.d. e SOTP INCOMPLETO: mai somme parziali spacciate per totale (regola
    14/07). Bridge to equity = STESSO del modello consolidato (net debt + equity
    adjustments): il delta misura la differenza di EV, non differenze di bridge."""
    if isinstance(segments, dict):
        segments = [segments]
    if not isinstance(segments, list):
        return None
    segs = [s for s in segments if isinstance(s, dict)]
    if not segs:
        return None
    warnings, rows = [], []
    # review codice C1: gli item NON-oggetto (es. nomi nudi) non spariscono zitti
    if len(segs) < len(segments):
        warnings.append(f"{len(segments) - len(segs)} elementi di segments NON-oggetto "
                        "SCARTATI (atteso {name, engine, ...}) — dichiarato")
    if len(segs) > 12:
        warnings.append(f"{len(segs) - 12} segmenti oltre il tetto di 12 SCARTATI (dichiarato)")
        segs = segs[:12]
    # premio EV/RAB di default per i segmenti regolati: stessa fonte del motore rab
    try:
        from bellomberg.market_data.sector_taxonomy import SUBSECTORS
        _reg = SUBSECTORS.get("utilities - regulated") or {}
    except Exception:
        _reg = {}
    _prem_def = _reg.get("rab_premium_default") or 1.15
    _band = _reg.get("ev_rab_band") or (0.7, 1.6)
    import re as _re
    _eq_metric_rx = _re.compile(r"p\s*/?\s*e\b|utile|earning|net\s*income|book|equity", _re.I)
    for s in segs:
        nome = str(s.get("name") or "").strip()
        eng = str(s.get("engine") or "").strip().lower()
        basis = str(s.get("basis") or "ev").strip().lower()
        row = {"name": nome or "(senza nome)", "engine": eng or "n.d.", "basis": basis,
               "src": (str(s.get("src") or "").strip() or None),
               "ebitda": _sotp_num(s.get("ebitda")), "revenue": _sotp_num(s.get("revenue")),
               "metric": None, "value": None, "mult": None, "stake": None,
               "ev": None, "reason": None, "method_note": None}
        # review codice C5: stake null/assente = 100% (default), NON input insensato;
        # un valore PRESENTE ma invalido resta bocciato sotto.
        _raw_stake = s.get("stake")
        stk = 1.0 if _raw_stake is None else _sotp_num(_raw_stake)
        if not nome:
            row["reason"] = "name mancante"
        elif eng not in ("rab", "multiple", "operating"):
            row["reason"] = f"engine '{eng or 'n.d.'}' non valido (rab|multiple|operating)"
        elif basis not in ("ev", "equity"):
            row["reason"] = f"basis '{basis}' non valida (ev|equity)"
        elif eng == "rab" and basis == "equity":
            row["reason"] = "basis 'equity' non valida con engine rab (RAB x premio E' un EV)"
        elif stk is None or not (0.0 < stk <= 1.0):
            # niente clamp zitti: uno stake insensato e' un input da correggere
            row["reason"] = f"stake {s.get('stake')!r} fuori (0,1]: riga n.d."
        elif eng == "rab":
            rb = _sotp_num(s.get("rab_base"))
            pr = _sotp_num(s.get("rab_premium"))
            if rb is None or rb <= 0:
                row["reason"] = ("rab_base mancante/non valida: la RAB non si stima da "
                                 "total assets (regola 14/07) — riga n.d.")
            elif pr is not None and pr <= 0:
                # review codice C4: premio <=0 azzererebbe una divisione DENTRO un totale
                # "completo" — bocciatura come multiple<=0 (mai somme parziali travestite)
                row["reason"] = f"rab_premium {pr:g} <= 0: riga n.d. (per il default OMETTILO)"
            else:
                if pr is None:
                    pr = _prem_def
                    row["method_note"] = (f"premio EV/RAB DEFAULT del profilo regolato ({_prem_def:.2f}x — "
                                          "calibrato su reti QUOTATE INTERE, non su transazioni di quote: "
                                          "override con [src] deal comparabili raccomandato)")
                if not (_band[0] <= pr <= _band[1]):
                    # NB (review finanza B7): il motore rab su premio fuori banda ripiega
                    # sul default (dichiarato); qui il premio dell'analista si USA e si
                    # urla — convenzione DIVERSA, messa a verbale.
                    warnings.append(f"segmento '{nome}': EV/RAB {pr:.2f}x FUORI banda "
                                    f"{_band[0]:.1f}-{_band[1]:.1f}x (dichiarato, non clampato)")
                row.update({"metric": "RAB (mln)", "value": rb, "mult": pr, "stake": stk,
                            "ev": rb * pr * stk})
        else:
            v = _sotp_num(s.get("value"))
            m = _sotp_num(s.get("multiple"))
            if v is None or m is None or m <= 0:
                row["reason"] = "value/multiple mancanti o multiple<=0: riga n.d."
            else:
                row.update({"metric": str(s.get("metric") or "metrica n.d. (dichiarata)"),
                            "value": v, "mult": m, "stake": stk, "ev": v * m * stk})
                if eng == "operating":
                    row["method_note"] = ("V7: segmento operating valutato a MULTIPLO "
                                          "dichiarato (DCF di segmento fuori perimetro)")
                if basis == "equity":
                    # review finanza F2: un valore EQUITY (P/E, book, quota di associata)
                    # si somma DOPO il net debt, mai dentro la somma EV
                    row["method_note"] = (((row["method_note"] + " | ") if row["method_note"] else "")
                                          + "base EQUITY: sommato DOPO il net debt (fuori dalla somma EV)")
                elif _eq_metric_rx.search(row["metric"] or ""):
                    warnings.append(f"segmento '{nome}': metrica '{row['metric']}' sembra una misura "
                                    "EQUITY — se il multiplo produce un equity value usa basis:'equity', "
                                    "altrimenti il net debt viene contato DUE volte (review finanza F2)")
        if row["reason"] is None:
            # review finanza F1: stake<1 su base EV con net debt consolidato al 100% =
            # EV parziale ma debito intero -> FV sottostimato. Corretto SOLO per quote
            # NON consolidate; per le controllate stake=1 e NCI nel bridge.
            if row["stake"] is not None and row["stake"] < 1.0 and basis == "ev":
                warnings.append(f"segmento '{row['name']}': stake {row['stake']:.0%} su base EV — corretto "
                                "SOLO per quote NON consolidate (il cui debito NON e' nel net debt di "
                                "gruppo); per una controllata consolidata usa stake=1 e lascia il NCI "
                                "nel bridge, altrimenti EV parziale ma debito al 100% = FV sottostimato")
            if row["src"] is None:
                warnings.append(f"segmento '{row['name']}': fonte [src] n.d. (dichiarato)")
            if row["ev"] is not None and row["ev"] < 0:
                warnings.append(f"segmento '{row['name']}': EV negativo ({row['ev']:,.0f} mln) — "
                                "riga di aggiustamento dichiarata")
        rows.append(row)

    # guardia di copertura (design audit/15): somma segmenti vs consolidato Yahoo.
    # Review finanza F6: guardia sul SOTTOINSIEME dichiarato (esclusi elencati) invece
    # di spegnersi al primo buco; vintage etichettato (Yahoo = TTM, segmenti spesso
    # forward). Review codice C6: consolidato NEGATIVO non e' "n.d." — messaggio suo.
    def _cover(field, consol, label):
        decl = [r[field] for r in rows if r[field] is not None]
        missing = [r["name"] for r in rows if r[field] is None]
        if not decl:
            warnings.append(f"copertura {label}: nessun segmento la dichiara — guardia >10% "
                            "non applicabile (dichiarato)")
            return
        if consol is None:
            warnings.append(f"copertura {label}: consolidato Yahoo n.d. — guardia non "
                            "verificabile (dichiarato)")
            return
        if consol <= 0:
            warnings.append(f"copertura {label}: consolidato Yahoo {consol:,.0f} mln <= 0 — "
                            "guardia non applicabile (dichiarato)")
            return
        tot = sum(decl)
        gap = tot / consol - 1.0
        _part = f" (PARZIALE: senza {', '.join(missing)})" if missing else ""
        if abs(gap) > 0.10:
            warnings.append(f"somma {label} segmenti {tot:,.0f}{_part} vs consolidato Yahoo "
                            f"TTM {consol:,.0f} mln: scarto {gap:+.0%} (>10%) — perimetro o "
                            "vintage (forward vs TTM) da verificare")
        elif missing:
            warnings.append(f"copertura {label} PARZIALE (senza {', '.join(missing)}): scarto "
                            f"sul sottoinsieme dichiarato {gap:+.0%} (dichiarato)")
    _cover("ebitda", _sotp_num(consol_ebitda_m), "EBITDA")
    _cover("revenue", _sotp_num(consol_revenue_m), "ricavi")

    nd_rows = [r for r in rows if r["reason"]]
    adjs = [a for a in (adjustments or []) if isinstance(a, dict) and _sotp_num(a.get("value_m")) is not None]
    # review finanza F3: stesso dedup keyword del bridge v3 — un segmento "Minorities"
    # E la riga minority auto nel bridge = doppio conteggio possibile, dichiarato
    _min_kws = ("minorit", "minoranz", "noncontrol", "non-control")
    _seg_min = [r["name"] for r in rows if any(k in r["name"].lower() for k in _min_kws)]
    if _seg_min and any(any(k in str(a.get("label") or "").lower() for k in _min_kws) for a in adjs):
        warnings.append(f"possibile DOPPIO CONTEGGIO minorities: segmento ({', '.join(_seg_min)}) "
                        "E riga minority nel bridge del modello — togline uno (dichiarato)")
    out = {"rows": rows, "warnings": warnings, "n_segments": len(rows),
           "net_debt": _sotp_num(net_debt), "adjustments": adjs,
           "shares_m": _sotp_num(shares_m), "headline_fv": _sotp_num(headline_fv),
           "incomplete": bool(nd_rows), "ev_total": None, "equity_parts": None,
           "equity": None, "fv_ps": None, "delta_pct": None, "note": None}
    if nd_rows:
        out["note"] = ("SOTP INCOMPLETO (nessuna somma parziale spacciata per totale): "
                       + "; ".join(f"{r['name']}: {r['reason']}" for r in nd_rows))
        return out
    # review finanza F2: le righe a base EQUITY stanno FUORI dalla somma EV e si
    # aggiungono DOPO il net debt (P/E, book, valore della quota di un'associata)
    out["ev_total"] = round(sum(r["ev"] for r in rows if r["basis"] == "ev"), 1)
    out["equity_parts"] = round(sum(r["ev"] for r in rows if r["basis"] == "equity"), 1)
    if out["net_debt"] is None:
        out["note"] = "equity SOTP n.d.: net debt consolidato n.d. (dichiarato)"
        return out
    # review finanza F4: la definizione del net debt si DICHIARA (proxy contabile)
    warnings.append("net debt consolidato = proxy contabile del modello (totalDebt-cassa "
                    "Yahoo o spec): ibridi/leases/cash collateral NON rettificati — "
                    "correggili via equity_adjustments (dichiarato)")
    adj_sum = sum(_sotp_num(a.get("value_m")) for a in adjs)
    out["equity"] = round(out["ev_total"] - out["net_debt"] + adj_sum + out["equity_parts"], 1)
    if not out["shares_m"] or out["shares_m"] <= 0:
        out["note"] = "FV/azione SOTP n.d.: numero di azioni n.d. (dichiarato)"
        return out
    out["fv_ps"] = round(out["equity"] / out["shares_m"], 2)
    if out["fv_ps"] <= 0:
        warnings.append("FV SOTP <= 0: la somma delle parti non copre il debito — "
                        "verificare unita' (mln, valuta dei financials) e perimetro")
    if out["headline_fv"] and out["headline_fv"] > 0:
        out["delta_pct"] = round((out["fv_ps"] / out["headline_fv"] - 1) * 100, 1)
        if abs(out["delta_pct"]) > 25:
            warnings.append(f"Delta SOTP vs consolidato {out['delta_pct']:+.1f}%: i due approcci "
                            "raccontano storie diverse — da riconciliare nella tesi")
    else:
        out["note"] = "delta n.d.: fair value consolidato (headline) non disponibile"
    return out


def _append_sotp_sheet(path, sotp, ticker, currency):
    """Aggiunge il foglio 'SOTP (segmenti)' al workbook v3 GIA' salvato, a formule
    VIVE (il bake COM a valle scrive i valori). Round-trip openpyxl verificato: il
    builder v3 non usa chart/immagini, quindi il load+save non perde nulla. Le
    formule sono il MIRROR esatto di _compute_sotp (parita' foglio<->payload)."""
    import openpyxl
    from openpyxl.styles import Font
    from bellomberg.valuation.dcf_bank import GREYTX, GOLD, H, L, N, T
    wb = openpyxl.load_workbook(path)
    if "SOTP (segmenti)" in wb.sheetnames:
        wb.remove(wb["SOTP (segmenti)"])
    ws = wb.create_sheet("SOTP (segmenti)")
    T(ws, "A1", f"SOTP (segmenti) — {ticker}")
    L(ws, "A2", "Somma delle parti DICHIARATA DALL'ANALISTA (segment notes di bilancio/piano). "
                f"Valori in mln {currency}. Il fair value HEADLINE resta il CONSOLIDATO "
                "(decisione PM 21/07): qui il confronto con la riga delta (G5, stile Kairos). "
                "Bridge to equity = lo stesso del modello DCF (net debt + adjustments).",
      italic=True, color=GREYTX)
    def _txt(v):
        # review codice C8: un'etichetta che inizia con "=" verrebbe scritta come
        # FORMULA (e il bake COM la valuterebbe) — spazio davanti, resta testo
        s = str(v)
        return (" " + s) if s.startswith("=") else s

    for c, h in (("A", "Segmento"), ("B", "Motore"), ("C", "Metrica"), ("D", "Valore (mln)"),
                 ("E", "Multiplo/Premio"), ("F", "Stake"), ("G", "EV (mln)"), ("H", "Fonte / note")):
        H(ws, f"{c}4", h)
    r = 5
    ev_cells, eq_cells = [], []
    for row in sotp["rows"]:
        L(ws, f"A{r}", _txt(row["name"]))
        L(ws, f"B{r}", row["engine"] + (" (EQUITY)" if row.get("basis") == "equity" else ""))
        if row["reason"]:
            L(ws, f"G{r}", "n.d.")
            L(ws, f"H{r}", _txt(row["reason"]), color=GREYTX)
        else:
            L(ws, f"C{r}", _txt(row["metric"]))
            N(ws, f"D{r}", row["value"], fmt="#,##0.0")
            N(ws, f"E{r}", row["mult"], fmt="0.00")
            N(ws, f"F{r}", row["stake"], fmt="0%")
            ws[f"G{r}"] = f"=D{r}*E{r}*F{r}"
            ws[f"G{r}"].number_format = "#,##0.0"
            # review finanza F2: base EQUITY fuori dalla somma EV, dentro l'equity
            (eq_cells if row.get("basis") == "equity" else ev_cells).append(r)
            _note = " | ".join(x for x in (row["src"] or "fonte n.d. (dichiarato)",
                                           row["method_note"]) if x)
            if row["src"]:
                L(ws, f"H{r}", _txt(_note))
            else:
                L(ws, f"H{r}", _txt(_note), color=GREYTX)
        r += 1
    r += 1
    tot_row = r
    L(ws, f"A{r}", "SOMMA EV SEGMENTI (solo base EV)", bold=True)
    if sotp["incomplete"]:
        L(ws, f"G{r}", "n.d.", bold=True)
        L(ws, f"H{r}", "SOTP INCOMPLETO: vedi righe n.d. sopra (mai somme parziali)", bold=True)
        r += 2
    else:
        # somma enumerata (mirror di ev_total): le righe EQUITY non ci entrano
        ws[f"G{r}"] = ("=" + "+".join(f"G{n}" for n in ev_cells)) if ev_cells else "=0"
        ws[f"G{r}"].number_format = "#,##0.0"
        ws[f"G{r}"].font = Font(bold=True, size=9)
        r += 1
        if sotp["net_debt"] is None:
            L(ws, f"A{r}", "Net debt consolidato: n.d. — equity SOTP n.d. (dichiarato)", bold=True)
            r += 2
        else:
            nd_row = r
            L(ws, f"A{r}", "Net debt consolidato (stesso bridge del modello DCF)")
            N(ws, f"G{r}", -sotp["net_debt"], fmt="#,##0.0")
            # review finanza F4: definizione del proxy DICHIARATA in riga
            L(ws, f"H{r}", "[src: totalDebt-cassa Yahoo o spec del modello — proxy contabile: "
                           "ibridi/leases/cash collateral NON rettificati, correggere via "
                           "equity_adjustments]", color=GREYTX)
            r += 1
            for a in sotp["adjustments"]:
                L(ws, f"A{r}", _txt(f"Bridge: {a.get('label')}"))
                N(ws, f"G{r}", float(a.get("value_m")), fmt="#,##0.0")
                L(ws, f"H{r}", _txt(str(a.get("commentary") or "")[:120]), color=GREYTX)
                r += 1
            eq_row = r
            L(ws, f"A{r}", "EQUITY SOTP" + (" (incl. parti a base EQUITY)" if eq_cells else ""), bold=True)
            ws[f"G{r}"] = (f"=G{tot_row}+SUM(G{nd_row}:G{r - 1})"
                           + "".join(f"+G{n}" for n in eq_cells))
            ws[f"G{r}"].number_format = "#,##0.0"
            ws[f"G{r}"].font = Font(bold=True, size=9)
            r += 1
            if sotp["fv_ps"] is not None:
                sh_row = r
                L(ws, f"A{r}", "Azioni (mln — fully diluted se fornite dal modello)")
                N(ws, f"G{r}", sotp["shares_m"], fmt="#,##0.0")
                r += 1
                fv_row = r
                L(ws, f"A{r}", "FAIR VALUE / AZIONE (SOTP)", bold=True)
                ws[f"G{r}"] = f"=G{eq_row}/G{sh_row}"
                ws[f"G{r}"].number_format = "#,##0.00"
                ws[f"G{r}"].font = Font(bold=True, color=GOLD, size=9)
                r += 1
                if sotp["delta_pct"] is not None:
                    cons_row = r
                    L(ws, f"A{r}", "Fair value CONSOLIDATO (headline del modello)")
                    N(ws, f"G{r}", sotp["headline_fv"], fmt="#,##0.00")
                    L(ws, f"H{r}", "dal payload del modello (fair_value_final/weighted/base): "
                                   "l'headline resta QUESTO", color=GREYTX)
                    r += 1
                    L(ws, f"A{r}", "DELTA SOTP vs CONSOLIDATO", bold=True)
                    ws[f"G{r}"] = f"=G{fv_row}/G{cons_row}-1"
                    ws[f"G{r}"].number_format = "+0.0%;-0.0%"
                    ws[f"G{r}"].font = Font(bold=True, color=GOLD, size=9)
                    L(ws, f"H{r}", "G5 (stile Kairos 'Delta vs top down approach'): se grande, "
                                   "i due approcci vanno riconciliati nella tesi", color=GREYTX)
                    r += 1
                else:
                    L(ws, f"A{r}", "Delta vs consolidato: n.d. (headline mancante) — dichiarato")
                    r += 1
            else:
                L(ws, f"A{r}", str(sotp["note"] or "FV/azione SOTP n.d. (dichiarato)"), bold=True)
                r += 1
            r += 1
    if sotp["incomplete"] and sotp["note"]:
        L(ws, f"A{r}", sotp["note"], bold=True)
        r += 2
    if sotp["warnings"]:
        L(ws, f"A{r}", "ATTENZIONI (dichiarate, da riportare nel report):", bold=True)
        r += 1
        for w in sotp["warnings"]:
            L(ws, f"A{r}", "! " + w)
            r += 1
    ws.column_dimensions["A"].width = 48
    for c in "BCDEFG":
        ws.column_dimensions[c].width = 14
    ws.column_dimensions["H"].width = 78
    # cintura P0 17/07: se il bake COM fallisse, Excel ricalcola all'apertura
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)


def _convert_fx_to_quote(r: Dict[str, Any], info: Dict[str, Any]) -> Dict[str, Any]:
    """audit/11 §2.2: i fondamentali yfinance sono in financialCurrency ma il prezzo e'
    nella valuta di QUOTAZIONE (ADR/cross-listed: SAP bilanci EUR, prezzo USD): senza
    conversione l'upside e' sbagliato in silenzio. Converte i fair_value_* del payload
    nella valuta del prezzo PRIMA della sanity; il workbook resta in valuta di reporting
    (annotato in fx_conversion). Se il cambio manca, flagga senza inventare tassi."""
    try:
        # audit/12 V0.1 (caso ADR con valore quasi nullo): la valuta di partenza e' quella DICHIARATA dal
        # payload quando c'e' (motore banca: valuta rilevata dei per-share, per gli ADR
        # gia' quella di quotazione -> nessuna conversione), altrimenti financialCurrency
        # come da contratto storico (operating: ricavi da income_stmt, conversione giusta).
        if not isinstance(r, dict) or not r.get("ok"):
            return r
        fin = r.get("payload_currency") or info.get("financialCurrency")
        quote = info.get("currency")
        if not fin or not quote or fin == quote:
            return r
        _fxc = r.get("fx_contract")
        if _fxc is not None and not _fxc.get("verified"):
            # valuta degli input NON verificabile: convertire sarebbe un fallback
            # silenzioso (regola 14/07) — si flagga e si dichiara, mai inventare
            r["valuation_flagged"] = True
            r["fx_conversion"] = {"error": ("valuta dei per-share NON verificabile (%s): "
                                            "conversione %s->%s NON applicata, fair value non "
                                            "confrontabile col prezzo" % (_fxc.get("evidence"), fin, quote))}
            return r
        # residuo (i) §9-quinquies n.5: se il foglio ha gia' scritto un tasso
        # (fx_quote_sheet), il payload usa QUELLO — mai due misure dello stesso cambio
        _pre = r.get("fx_quote_sheet") or {}
        _pre_match = _pre.get("from") == fin and _pre.get("to") == quote
        if _pre.get("error") and _pre_match:
            # review 23/07 (MEDIA-1): il foglio ha gia' dichiarato il buco — niente
            # refetch qui: un retry riuscito darebbe foglio n.d. e payload numerico,
            # cioe' le due misure divergenti che questo fix elimina
            r["valuation_flagged"] = True
            r["fx_conversion"] = {"error": _pre["error"] + " (stesso esito dichiarato nel foglio)"}
            return r
        fx = (_pre["rate"] if (_pre.get("rate") and _pre_match)
              else _fx_pair_rate(fin, quote))
        if fx is None:
            r["valuation_flagged"] = True
            r["fx_conversion"] = {"error": f"cambio {fin}->{quote} non disponibile: fair value "
                                           f"in {fin} NON confrontabile col prezzo di quotazione"}
            return r
        keys = [k for k in r if k.startswith("fair_value") and isinstance(r.get(k), (int, float))]
        for k in keys:
            r[k] = round(r[k] * fx, 2)
        r["fx_conversion"] = {"from": fin, "to": quote, "rate": round(fx, 6), "src": "yfinance",
                              "keys_converted": keys,
                              "note": f"payload convertito in {quote} (valuta del prezzo); "
                                      f"il workbook Excel resta in {fin}"
                                      + (" | stesso tasso della riga cross-valuta nel foglio"
                                         if _pre.get("rate") else "")}
    except Exception:
        pass
    return r


def _sanity_with_fx(r: Dict[str, Any], price, info: Dict[str, Any]) -> Dict[str, Any]:
    """Conversione FX payload->valuta prezzo, poi sanity. Se il cambio manca la sanity
    viene SALTATA con headline esplicita (confrontare valute diverse = numero falso)."""
    r = _convert_fx_to_quote(r, info)
    if (r.get("fx_conversion") or {}).get("error"):
        r["sanity"] = {"status": "n/d", "severity": "WARN",
                       "headline": r["fx_conversion"]["error"]}
        # review audit/12 V0: senza rename il rifiuto era invisibile in F17 (il flag
        # in pagina deriva dal FILENAME) — file marcato e motivo nel payload
        r["sanity_headline"] = r["fx_conversion"]["error"]
        _flag_file(r)
        return r
    return _apply_sanity_flag(r, price)


def sanity_check(fair_value: Optional[float], price: Optional[float]) -> Dict[str, Any]:
    """Confronta fair value vs prezzo. 204b-FIX: ora URLA. Oltre al testo ritorna 'severity'
    (OK/WARN/BLOCK) e 'exclude_from_action_table': se |fair/price-1| supera il 40-50% il
    consumatore a valle (memo/Capo/ACTION TABLE) deve segnalare/escludere il nome. Prima i
    campi c'erano ma nessuno li consumava e le soglie (0.4/2.5) erano troppo larghe.
    Retro-compatibile: status/ratio/upside_pct/reading restano invariati."""
    if not fair_value or not price or price <= 0:
        return {"status": "n/d", "ratio": None, "severity": "OK",
                "exclude_from_action_table": False, "headline": None}
    ratio = fair_value / price
    divergence = abs(ratio - 1.0)
    if ratio < 0.4:
        flag = "FAIR VALUE MOLTO SOTTO il prezzo: o le assumptions sono troppo conservative, o il mercato sconta crescita non nel modello. Rivedere growth/terminal."
    elif ratio > 2.5:
        flag = "FAIR VALUE MOLTO SOPRA il prezzo: assumptions forse troppo ottimistiche, o vero deep value. Verificare margini/WACC."
    elif 0.7 <= ratio <= 1.4:
        flag = "Fair value coerente col prezzo (entro +/-40%): modello calibrato bene."
    else:
        flag = "Scostamento moderato: possibile sopra/sottovalutazione da approfondire."
    if divergence > 0.5:
        severity = "BLOCK"; exclude = True
        headline = ("VAL SOSPETTA: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). "
                    "Rivedere growth/margini/WACC/shares/net_debt PRIMA di fidarsi. "
                    "ESCLUSO dalla ACTION TABLE." % (fair_value, divergence * 100, price, ratio))
    elif divergence > 0.4:
        severity = "WARN"; exclude = False
        headline = ("VAL da verificare: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). "
                    "Trattare con cautela." % (fair_value, divergence * 100, price, ratio))
    else:
        severity = "OK"; exclude = False; headline = None
    return {"status": "ok", "ratio": round(ratio, 2),
            "upside_pct": round((ratio-1)*100, 1), "reading": flag,
            "severity": severity, "exclude_from_action_table": exclude, "headline": headline}


def _flag_file(r: Dict[str, Any]) -> None:
    """Rinomina il workbook in _FLAGGED (idempotente, guarded): F17 deduce il flag dal
    FILENAME, quindi ogni percorso che boccia un modello DEVE passare di qui — review
    audit/12 V0: il ramo fx error/contratto-non-verificato flaggava solo il payload e
    il rifiuto restava invisibile in pagina."""
    p = r.get("path")
    if p and os.path.exists(p) and "_FLAGGED" not in os.path.basename(p):
        base, ext = os.path.splitext(p)
        try:
            os.replace(p, base + "_FLAGGED" + ext)
            r["path"] = base + "_FLAGGED" + ext
        except Exception:
            pass


def _apply_sanity_flag(r: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    """Calcola la sanity sul fair value disponibile e, se scatta BLOCK, MARCA il file
    (rinomina con _FLAGGED) + setta valuation_flagged/sanity_headline. Guarded e idempotente.
    Cosi' memo/Capo/ACTION TABLE hanno tutto per segnalare ed escludere il nome."""
    try:
        # B8: se l'analista ha dichiarato il blend metodi, la sanity giudica QUEL
        # numero (e' il fair value che finisce nel memo), altrimenti la catena storica
        # audit/12 V0.2: loop esplicito, non catena `or` — un fair value di 0.0 esatto
        # veniva scambiato per "assente" e usciva senza flag ne' sanity.
        fv = None
        for _k in ("fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base"):
            if isinstance(r.get(_k), (int, float)):
                fv = r[_k]
                break
        if fv is None:
            return r
        # 16/07 notte (feedback PM: FV negativi su due titoli e quasi nullo su un ADR): un fair value
        # <= 0 per una quotata solvibile NON e' un numero — e' il modello girato nudo
        # su dati sottili (CAGR meccanico -> flussi negativi). Non si emette un numero
        # falso: si RIFIUTA e si chiedono le assumption dell'analista. Il file resta
        # (FLAGGED) come pezza d'appoggio, ma fair value e tesi NON si propagano.
        # audit/12 V0.2: stesso trattamento per il QUASI-zero (< 5% del prezzo) — lo
        # un valore di pochi centesimi passava il guard assoluto ed era arrivato in pagina come numero.
        _pr = price if isinstance(price, (int, float)) and price > 0 else None
        if fv <= 0 or (_pr and fv < 0.05 * _pr):
            _motivo = "FV<=0" if fv <= 0 else f"FV {fv:.2f} sotto il 5% del prezzo {_pr:.2f}"
            r["valuation_flagged"] = True
            r["sanity_headline"] = (f"{_motivo} dal calcolo meccanico: NON attendibile. Servono le "
                                    "assumption dell'analista (growth_path/roe_path + variant_view)")
            r["fair_value_rejected"] = fv
            for _k in ("fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base"):
                r.pop(_k, None)
            # review V7 L3 (finanza F5 + codice C2): il SOTP condivide net debt/azioni
            # col modello appena rifiutato e il suo delta punta a un headline che non
            # esiste piu' — NON resta come unico fair value del payload/sidecar
            _fs = r.pop("fair_value_sotp", None)
            _sd = r.pop("sotp_delta_pct", None)
            if _fs is not None or _sd is not None:
                r["sotp_note"] = (((str(r["sotp_note"]) + " | ") if r.get("sotp_note") else "")
                                  + "SOTP non fruibile: fair value consolidato RIFIUTATO "
                                    "(condividono net debt/azioni) — delta n.d.")
            r["error"] = (f"fair value meccanico {fv:.2f} rifiutato ({_motivo}: dati insufficienti "
                          "per un modello nudo, o unita'/valute incoerenti). Rilancia get_valuation "
                          "con growth_path/roe_path e variant_view motivate — il numero deve nascere "
                          "da assumption, non da un CAGR cieco.")
            _flag_file(r)
            return r
        sc = sanity_check(fv, price)
        r["sanity"] = sc
        if sc.get("exclude_from_action_table") or sc.get("severity") == "BLOCK":
            r["valuation_flagged"] = True
            r["sanity_headline"] = sc.get("headline")
            _flag_file(r)
    except Exception:
        pass
    return r


def _count_uncached_formulas(path: str) -> int:
    """Conta le celle formula SENZA valore cached — la misura del collaudo PM 16/07
    notte (decine nei due casi banca e centinaia nel caso industriale = 100% delle formule). Dopo il bake deve essere 0.
    Limiti DICHIARATI (review 17/07): una formula il cui risultato e' la stringa vuota
    "" conterebbe come 'senza valore' (falso positivo -> i builder non devono mai usare
    "" come ramo di una formula: oggi usano "n/m"/"n/a"/0, verificato); un valore cached
    di ERRORE (#DIV/0! ecc.) conta come presente: e' visibile e auto-dichiarante in cella."""
    import openpyxl
    wbf = openpyxl.load_workbook(path)
    wbv = openpyxl.load_workbook(path, data_only=True)
    n = 0
    for ws in wbf.worksheets:
        wsv = wbv[ws.title]
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    if wsv.cell(row=c.row, column=c.column).value is None:
                        n += 1
    wbf.close()
    wbv.close()
    return n


# chiavi del payload che alimentano il pannello dettaglio di F17 (opzione B, PM 17/07):
# sottoinsieme SLIM e json-serializzabile — mai l'intero r (niente path interni doppi).
_SIDECAR_KEYS = ("ticker", "engine", "profile_key", "subsector", "method", "price",
                 "fair_value_ri", "fair_value_ptbv", "fair_value_ddm", "fair_value_blend",
                 "fair_value_bear", "fair_value_base", "fair_value_bull",
                 "fair_value_weighted", "fair_value_comps_implied", "fair_value_final",
                 "methods_delta", "methods_divergence", "blend_methods",
                 "holding_irr", "peers", "peers_used", "peer_note",
                 "wacc_used", "terminal_g_used", "exit_multiple_used", "exit_multiple_source",
                 "implied_exit_multiple_gordon", "growth_source",
                 # 05/09 (classificazione lotto 2): da dove viene il profilo (etichetta)
                 "profile_source",
                 "values_baked", "bake_note", "bake_error", "payload_currency",
                 # V7 motore RAB: metodi e meta regolatori nel sidecar (il ramo
                 # isRab in FundamentalsPage e' del PROSSIMO lotto — dichiarato:
                 # oggi F17 mostra il modello rab col layout generico)
                 "fair_value_ev_rab", "fair_value_ddm_reg", "fair_value_peer",
                 "peer_method", "rab_base", "allowed_return_calc",
                 "allowed_return_nominal", "service",
                 "convention", "anchor_stale", "rab_premium",
                 # V7 Lotto 3: layer segmenti/SOTP (fair_value_sotp segue la conversione
                 # FX come gli altri fair_value_*; sotp_ev_total resta in valuta di
                 # bilancio come il workbook — dichiarato nel foglio)
                 "fair_value_sotp", "sotp_delta_pct", "sotp_ev_total",
                 "sotp_n_segments", "sotp_incomplete", "sotp_note", "sotp_warnings",
                 # V5 motore mNAV (audit/18): scheda veicolo nel sidecar (il ramo
                 # isMnav in F17 e' del Lotto 3 con mockup — dichiarato: fino ad
                 # allora la pagina mostra il modello mnav col layout generico)
                 "nav_per_share", "nav_per_share_dtl_addback", "mnav_equity", "mnav_ev",
                 "mnav", "mnav_dtl_addback", "discount_to_nav_pct", "nav_target",
                 "fair_value_nav", "fv_note", "nav_vintage", "btc_nav_usd",
                 "adjusted_nav_musd", "fd_shares_m", "hype_value_musd",
                 "price_quote", "price_quote_currency", "price_note",
                 # "warnings" entra nel sidecar per TUTTI i motori che la portano
                 # (mnav, rab, banca): DICHIARATO — review V5 M1; per il rab chiude
                 # la voce P3 "warnings rab in _SIDECAR_KEYS" (F17 v2)
                 "warnings",
                 # V6 Lotto 2 (audit/19): guidance consumata con fonte+vintage,
                 # buco del registro dichiarato, confronto vs consensus (V6.5)
                 "guidance_used", "guidance_note", "guidance_vs_consensus",
                 # V6.5 Lotto 3: deviazione analista vs guidance attiva (nudge)
                 "guidance_deviation_pp",
                 # decisioni PM 23/07 (pacchetto): la nota M7 sul margine base e il
                 # costo del rischio TTC banca arrivano a F17 (prima solo foglio)
                 "margin_sanity", "cost_of_risk_ttc")


def _write_payload_sidecar(r: Dict[str, Any]) -> Dict[str, Any]:
    """F17 opzione B (PM 17/07): il pannello dettaglio vuole metodi/peer/IRR che le
    tesi in DB non portano — si scrive un sidecar VAL_X.payload.json accanto al
    canonico a ogni generazione. Best-effort dichiarato: se fallisce il modello resta
    valido e la pagina mostra n.d. sul dettaglio. Il sidecar della variante opposta
    (_FLAGGED vs canonico) si rimuove per non servire dettagli stantii."""
    p = r.get("path") if isinstance(r, dict) else None
    if not p or not os.path.exists(p):
        return r
    try:
        slim = {k: r.get(k) for k in _SIDECAR_KEYS if r.get(k) is not None}
        san = r.get("sanity") or {}
        slim["sanity"] = {"severity": san.get("severity"), "headline": san.get("headline")}
        slim["_timestamp"] = datetime.now().isoformat(timespec="seconds")
        base = os.path.splitext(os.path.abspath(p))[0]
        # review 17/07 F4: scrittura ATOMICA (tmp + os.replace) — mai un json a meta'
        # letto dall'endpoint o lasciato corrotto da un crash
        _tmp = base + ".payload.json.tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, default=str)
        os.replace(_tmp, base + ".payload.json")
        # variante opposta: VAL_X <-> VAL_X_FLAGGED (mai due sidecar in disaccordo)
        other = (base[: -len("_FLAGGED")] if base.endswith("_FLAGGED") else base + "_FLAGGED")
        try:
            if os.path.exists(other + ".payload.json"):
                os.remove(other + ".payload.json")
        except Exception:
            pass
        r["payload_sidecar"] = os.path.basename(base + ".payload.json")
    except Exception as _sc_err:
        r["payload_sidecar_error"] = f"sidecar non scritto ({type(_sc_err).__name__}): dettaglio F17 n.d."
        print(f"[dcf_engine] WARN sidecar {os.path.basename(p)}: {r['payload_sidecar_error']}")
    return r


def _bake_values(r: Dict[str, Any]) -> Dict[str, Any]:
    """P0 17/07 'valori dentro i fogli' (collaudo PM 16/07 notte bocciato): openpyxl
    scrive le formule SENZA valori cached, quindi il workbook APPARE vuoto se Excel non
    ricalcola all'apertura o se si guarda un'anteprima. Post-process via Excel COM
    (apri -> ricalcola -> salva) con tools/ops/bake_xlsx_values.ps1: le formule restano
    VIVE, il ricalcolo aggiunge solo i valori. PowerShell in subprocess e NON pywin32:
    niente COM apartment/thread nel processo backend e timeout killabile dall'esterno.
    Fallback DICHIARATO (regola 14/07): se Excel/COM manca o fallisce il modello resta
    valido ma esce values_baked=False + bake_error nel payload — mai in silenzio.
    Va chiamato DOPO _sanity_with_fx: e' li' che il file puo' diventare _FLAGGED."""
    p = r.get("path") if isinstance(r, dict) else None
    if not p:
        return r
    if not os.path.exists(p):
        # review 17/07 B4: path nel payload ma file sparito (race/pulizia) — dichiarato
        r["values_baked"] = False
        r["bake_error"] = f"file non trovato al momento del bake: {p}"
        print(f"[dcf_engine] WARN bake: {r['bake_error']}")
        return r
    ps1 = str(PROJECT_ROOT / "tools" / "ops" / "bake_xlsx_values.ps1")
    import subprocess
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", ps1, "-Path", os.path.abspath(p)],
            capture_output=True, text=True, errors="replace", timeout=120,
            # review 17/07 M1: senza questo flag ogni bake apre una finestra console
            # visibile quando il backend e' spawnato dall'app Electron
            creationflags=subprocess.CREATE_NO_WINDOW)
        if proc.returncode != 0:
            out = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
            raise RuntimeError(f"exit {proc.returncode}: {out[:300]}")
    except subprocess.TimeoutExpired:
        # review 17/07 M2/F3: il timeout uccide SOLO powershell — l'EXCEL.EXE creato
        # via COM (figlio di DCOM, non di powershell) puo' restare orfano CON IL LOCK
        # sul file: i save successivi dello stesso ticker fallirebbero (dichiarati).
        # NIENTE taskkill d'ufficio: il PM puo' avere Excel aperto con lavoro suo.
        r["values_baked"] = False
        r["bake_error"] = ("ricalcolo Excel COM in TIMEOUT (120s): possibile EXCEL.EXE "
                           "orfano che tiene il file LOCKATO — se la prossima rigenerazione "
                           "di questo ticker fallisce al save, chiudere EXCEL.EXE dal Task Manager")
        print(f"[dcf_engine] WARN bake {os.path.basename(p)}: {r['bake_error']}")
        return r
    except Exception as e:
        r["values_baked"] = False
        r["bake_error"] = (f"ricalcolo Excel COM NON eseguito ({type(e).__name__}: {e}) — "
                           "celle calcolate senza valori cached; all'apertura in Excel il "
                           "ricalcolo parte comunque (fullCalcOnLoad)")
        print(f"[dcf_engine] WARN bake {os.path.basename(p)}: {r['bake_error']}")
        return r
    # verifica MISURATA, non presunta: 0 formule senza valore = bake riuscito.
    # review 17/07 B1: try separato — un errore qui non e' "bake fallito", e' "verifica
    # non riuscita": il COM ha risposto OK, lo si dice per quello che e'.
    try:
        n_left = _count_uncached_formulas(p)
    except Exception as _ve:
        r["values_baked"] = True
        r["bake_note"] = (f"bake COM riuscito ma VERIFICA openpyxl fallita "
                          f"({type(_ve).__name__}: {_ve}): esito non confermato dalla misura")
        print(f"[dcf_engine] WARN bake {os.path.basename(p)}: {r['bake_note']}")
        return r
    r["values_baked"] = (n_left == 0)
    r["bake_note"] = ("valori cached scritti via Excel COM; formule senza valore "
                      f"residue: {n_left}")
    if n_left:
        r["bake_error"] = (f"{n_left} formule ANCORA senza valore cached dopo il "
                           "ricalcolo COM: aprire il file in Excel (fullCalcOnLoad attivo)")
        print(f"[dcf_engine] WARN bake {os.path.basename(p)}: {r['bake_error']}")
    return r


if __name__ == "__main__":
    import json, sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    r = generate_valuation(t)
    print(json.dumps({k: v for k, v in r.items() if k != "warnings"}, indent=1, ensure_ascii=False, default=str))
