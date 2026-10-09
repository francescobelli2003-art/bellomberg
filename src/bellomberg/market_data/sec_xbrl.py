"""
sec_xbrl.py (#204a) - STORICO FONDAMENTALE riga-per-riga dai filing XBRL SEC.

Fonte: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json (gratuita, ufficiale).
Copre: filer US (10-K) E foreign private issuers con ADR (20-F: emittenti esteri quotati negli USA...)
-> tassonomie us-gaap E ifrs-full mappate sulle STESSE righe canoniche del modello.
Cache su disco 7 giorni (il file companyfacts pesa MB e cambia coi filing).

API:
    get_financial_history(ticker, years=10) -> dict con serie annuali per ~30 voci
    core di IS/BS/CF + derivate (margini, payout, capex/ricavi).
"""
import json
import tempfile
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from bellomberg.core.paths import DATA_DIR

CACHE_DIR = str(DATA_DIR / "xbrl_cache")
CACHE_TTL_S = 7 * 24 * 3600

# riga canonica -> (tag us-gaap alternativi, tag ifrs-full alternativi)
CANONICAL = {
    # INCOME STATEMENT
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                 "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
                ["Revenue", "RevenueFromContractsWithCustomers"]),
    "cost_of_revenue": (["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
                        ["CostOfSales"]),
    "gross_profit": (["GrossProfit"], ["GrossProfit"]),
    "rnd_expense": (["ResearchAndDevelopmentExpense"], ["ResearchAndDevelopmentExpense"]),
    "sga_expense": (["SellingGeneralAndAdministrativeExpense"],
                    ["SellingGeneralAndAdministrativeExpense", "AdministrativeExpense"]),
    "operating_income": (["OperatingIncomeLoss"], ["ProfitLossFromOperatingActivities"]),
    "interest_expense": (["InterestExpense", "InterestExpenseDebt"],
                         ["FinanceCosts", "InterestExpense"]),
    "pretax_income": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                       "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
                      ["ProfitLossBeforeTax"]),
    "tax_expense": (["IncomeTaxExpenseBenefit"], ["IncomeTaxExpenseContinuingOperations"]),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"]),
    "eps_diluted": (["EarningsPerShareDiluted"], ["DilutedEarningsLossPerShare"]),
    "shares_diluted": (["WeightedAverageNumberOfDilutedSharesOutstanding"],
                       ["WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares"]),
    # BALANCE SHEET
    "total_assets": (["Assets"], ["Assets"]),
    "current_assets": (["AssetsCurrent"], ["CurrentAssets"]),
    "cash": (["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
             ["CashAndCashEquivalents"]),
    "inventory": (["InventoryNet"], ["Inventories"]),
    "receivables": (["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
                    ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"]),
    "ppe_net": (["PropertyPlantAndEquipmentNet"], ["PropertyPlantAndEquipment"]),
    "goodwill": (["Goodwill"], ["Goodwill"]),
    "total_liabilities": (["Liabilities"], ["Liabilities"]),
    "current_liabilities": (["LiabilitiesCurrent"], ["CurrentLiabilities"]),
    "lt_debt": (["LongTermDebtNoncurrent", "LongTermDebt"],
                ["NoncurrentBorrowingsAndOtherFinancialLiabilities", "LongtermBorrowings", "NoncurrentBorrowings"]),
    "equity": (["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
               ["EquityAttributableToOwnersOfParent", "Equity"]),
    # CASH FLOW
    "cfo": (["NetCashProvidedByUsedInOperatingActivities",
             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
            ["CashFlowsFromUsedInOperatingActivities"]),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
              ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
               "PurchaseOfPropertyPlantAndEquipment", "AcquisitionsOfPropertyPlantAndEquipment"]),
    "dep_amort": (["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"],
                  ["DepreciationAndAmortisationExpense"]),
    "dividends_paid": (["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
                       ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid"]),
    "buyback": (["PaymentsForRepurchaseOfCommonStock"],
                ["PaymentsToAcquireOrRedeemEntitysShares"]),
    "sbc": (["ShareBasedCompensation"], ["ShareBasedPayments", "ExpenseFromSharebasedPaymentTransactions"]),
}
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F")


def _cache_path(cik: str) -> str:
    return os.path.join(CACHE_DIR, f"CIK{cik}.json")


def _superata_path(cik: str) -> str:
    return _cache_path(cik)[:-len(".json")] + ".superata.json"


def segna_cache_superata(cik: str, motivo: str) -> None:
    """La cache companyfacts e' anteriore a un deposito gia' pubblicato e la rilettura e'
    fallita (REV_G2a R-4): il DCF lo legge in `index_note` finche' una rilettura non riesce."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"motivo": motivo, "quando": datetime.now().isoformat(timespec="seconds")}, f)
        os.replace(tmp, _superata_path(cik))
    except OSError as e:
        print(f"[sec_xbrl] segno di cache superata CIK{cik} non scritto: {type(e).__name__}: {e}")


def cache_superata(cik: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_superata_path(cik), "r", encoding="utf-8") as f:
            dati = json.load(f)
        return dati if isinstance(dati, dict) else {"motivo": "segno illeggibile"}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"motivo": "segno di cache superata illeggibile"}


def _fetch_companyfacts(cik: str, *, forza: bool = False, esito: Optional[Dict[str, Any]] = None
                        ) -> Optional[Dict[str, Any]]:
    """companyfacts con cache disco 7gg (file pesante, gentilezza verso la SEC).

    `forza`: salta la cache in lettura (filing_numeri, revisione 04/10). La cache si
    sostituisce solo con una risposta valida (dict con `facts`), in modo atomico
    (temporaneo + os.replace), mai cancellata: la usa anche il DCF (REV_G2a R-4).
    `esito` (dict opzionale) riceve {"da_cache", "letto_il"}: l'eta' del dato si dichiara."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = _cache_path(cik)
    try:
        if not forza and os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < CACHE_TTL_S:
            letto_il = datetime.fromtimestamp(os.path.getmtime(cp)).isoformat(timespec="seconds")
            with open(cp, "r", encoding="utf-8") as f:
                dati = json.load(f)
            if isinstance(dati, dict) and isinstance(dati.get("facts"), dict):
                if esito is not None:
                    esito.update(da_cache=True, letto_il=letto_il)
                return dati
    except Exception:
        pass
    try:
        import requests
        from bellomberg.market_data.sec_edgar import _headers   # 02/09 (B2): HEADERS non esiste piu; contatto letto a chiamata
        from bellomberg.market_data import sec_edgar
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        sec_edgar.attendi_sec()  # revisione 04/10: anche companyfacts dentro il tetto totale SEC
        r = requests.get(url, headers=_headers(), timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get("facts"), dict):
            print(f"[sec_xbrl] companyfacts CIK{cik}: risposta senza 'facts' (non usata, cache invariata)")
            return None
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, cp)
            tmp = None
            try:
                os.remove(_superata_path(cik))  # rilettura riuscita: la cache non e' piu' anteriore
            except FileNotFoundError:
                pass
        except Exception as e:
            print(f"[sec_xbrl] cache companyfacts CIK{cik} non scritta: {type(e).__name__}: {e}")
        finally:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if esito is not None:
            esito.update(da_cache=False, letto_il=datetime.now().isoformat(timespec="seconds"))
        return data
    except Exception as e:
        # review 02/09: prima l'except era muto e un ImportError (HEADERS sparito)
        # diventava «companyfacts non disponibile» accusando la SEC
        print(f"[sec_xbrl] companyfacts CIK{cik}: {type(e).__name__}: {e}")
        return None


def _series_from_tag(item) -> Dict[str, Dict[int, Dict[str, Any]]]:
    """{unit: {fy: osservazione}} per un singolo tag (form annuali, fp FY o assente, durata ~1y)."""
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for unit_key, obs in (item.get("units") or {}).items():
        series: Dict[int, Dict[str, Any]] = {}
        for ob in obs:
            if ob.get("form") not in ANNUAL_FORMS:
                continue
            if ob.get("fp") not in ("FY", None, ""):  # fix verificatore: fp mancante tollerato
                continue
            end = ob.get("end") or ""
            try:
                fy = int(end[:4])
            except (ValueError, TypeError):
                continue
            st = ob.get("start")
            if st:
                try:
                    days = (datetime.fromisoformat(end) - datetime.fromisoformat(st)).days
                    if days < 300 or days > 400:
                        continue
                except Exception:
                    pass
            prev = series.get(fy)
            if (prev is None) or (str(ob.get("filed", "")) > str(prev.get("filed", ""))):
                series[fy] = ob
        if series:
            out[unit_key] = series
    return out


def _annual_series(facts: Dict[str, Any], tags_gaap: List[str], tags_ifrs: List[str]):
    """Serie {fy: val}. Fix verificatore: FONDE i tag alternativi (il primo comanda,
    i successivi riempiono SOLO gli anni mancanti - caso revenue pre/post ASC606) e
    sceglie l'unita' con PIU' osservazioni (tie-break: USD)."""
    for taxo, tags in (("us-gaap", tags_gaap), ("ifrs-full", tags_ifrs)):
        node = facts.get(taxo) or {}
        merged: Dict[int, Any] = {}
        used_tags: List[str] = []
        unit_pick = None
        for tag in tags:
            item = node.get(tag)
            if not item:
                continue
            per_unit = _series_from_tag(item)
            if not per_unit:
                continue
            if unit_pick is None:
                unit_pick = max(per_unit.keys(),
                                key=lambda u: (len(per_unit[u]), 1 if u == "USD" else 0))
            series = per_unit.get(unit_pick)
            if not series:
                continue
            added = False
            for fy, ob in series.items():
                if fy not in merged:
                    merged[fy] = ob.get("val")
                    added = True
            if added:
                used_tags.append(tag)
        if merged:
            return (dict(sorted(merged.items())), f"{taxo}:" + "+".join(used_tags), unit_pick)
    return None, None, None


# FRESCHEZZA (06/10/2026, Opus 5.5): i TRIMESTRI dei 10-Q. Prima get_financial_history dava solo
# fp=FY e un desk non vedeva mai l'ultimo trimestre depositato (richiesta PM «dati aggiornati, non al 2024»).
QUARTER_FORMS = ("10-Q", "10-Q/A")
INSTANT_ITEMS = {"total_assets", "current_assets", "cash", "inventory", "receivables", "ppe_net",
                 "goodwill", "total_liabilities", "current_liabilities", "lt_debt", "equity"}
# voci di cassa: nei 10-Q il rendiconto finanziario e' CUMULATO da inizio esercizio (6 o 9 mesi)
YTD_ITEMS = {"cfo", "capex", "dep_amort", "dividends_paid", "buyback", "sbc"}
QUARTER_CORE = ("revenue", "operating_income", "net_income", "eps_diluted", "cash", "lt_debt", "equity")


def _giorni(ob) -> Optional[int]:
    try:
        return (datetime.fromisoformat(ob["end"]) - datetime.fromisoformat(ob["start"])).days
    except (KeyError, TypeError, ValueError):
        return None


def _osservazioni(facts: Dict[str, Any], tags_gaap: List[str], tags_ifrs: List[str]):
    """(tassonomia:tag, unita', osservazioni) di ogni tag presente, nell'ordine di CANONICAL."""
    for taxo, tags in (("us-gaap", tags_gaap), ("ifrs-full", tags_ifrs)):
        node = facts.get(taxo) or {}
        for tag in tags:
            units = (node.get(tag) or {}).get("units") or {}
            if not units:
                continue
            unit = max(units.keys(), key=lambda u: (len(units[u]), 1 if u == "USD" else 0))
            yield f"{taxo}:{tag}", unit, units[unit]


def _fine_per_accession(facts: Dict[str, Any], fino_al: Optional[str]) -> Dict[str, str]:
    """{accession: fine del periodo del deposito} = la fine piu' recente fra i suoi fatti (le voci
    canoniche). Un 10-Q riporta anche i comparativi (fine esercizio precedente, stesso trimestre
    dell'anno prima): sono fatti di QUEL deposito ma non del suo periodo (R-CASCATA, 07/10)."""
    fine: Dict[str, str] = {}
    for _canon, (tg, ti) in CANONICAL.items():
        for _tag, _unit, obs in _osservazioni(facts, tg, ti):
            for ob in obs:
                a, e = ob.get("accn"), ob.get("end") or ""
                if a and len(e) == 10 and (not fino_al or e <= fino_al) and e > fine.get(a, ""):
                    fine[a] = e
    return fine


REGOLA_Q4 = ("Q4 = esercizio (10-K) meno i tre trimestri dei 10-Q dello stesso esercizio (derivato, "
             "non depositato come trimestre); saldi a fine esercizio dal 10-K")
Q4_DERIVABILI = ("revenue", "operating_income", "net_income")   # l'EPS non si somma: Q4 dichiarato mancante


def quarterly_history(facts: Dict[str, Any], quarters: int = 8, fino_al: Optional[str] = None) -> Dict[str, Any]:
    """Trimestri dai 10-Q (fp Q1-Q3) piu' il Q4 derivato, e ultimo periodo depositato (10-Q o annuale).

    Si usano solo i fatti del periodo del deposito (mai i comparativi). `quarters`: {voce: {period_end:
    valore}} con flussi di 3 mesi (80-100 giorni) e saldi; il Q4 dei flussi e' FY - (Q1+Q2+Q3) e lo
    dichiara `q4` (derivati e non derivabili), mai una somma su trimestri incompleti.
    `latest_period`: il periodo piu' recente con period_end, filing_date, form, accession e i valori
    di quel periodo; le voci di cassa dei 10-Q sono cumulate da inizio esercizio e lo dicono.
    `fino_al` (ISO): solo depositi con filed <= fino_al (cutoff della Trade Idea, mai il futuro)."""
    fine_accn = _fine_per_accession(facts, fino_al)
    per_end: Dict[str, Dict[str, Any]] = {}
    serie: Dict[str, Dict[str, Any]] = {}
    valori_per_end: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for canon, (tg, ti) in CANONICAL.items():
        # tutti i tag alternativi: il primo comanda a parita' di periodo, i successivi riempiono
        # i periodi mancanti (un emittente che cambia tag non perde l'ultimo trimestre)
        for priorita, (tag, unit, obs) in enumerate(_osservazioni(facts, tg, ti)):
            for ob in obs:
                form, end = ob.get("form"), ob.get("end") or ""
                trimestrale = form in QUARTER_FORMS and ob.get("fp") in ("Q1", "Q2", "Q3")
                annuale = form in ANNUAL_FORMS and ob.get("fp") in ("FY", None, "")
                if not (trimestrale or annuale) or len(end) != 10:
                    continue
                if fino_al and (end > fino_al or str(ob.get("filed") or "9999") > fino_al):
                    continue
                if fine_accn.get(ob.get("accn"), end) != end:
                    continue                      # comparativo di un altro periodo
                durata = _giorni(ob)
                if canon in INSTANT_ITEMS:
                    if ob.get("start"):
                        continue
                elif durata is None:
                    continue
                elif trimestrale and not (80 <= durata <= 100) and not (canon in YTD_ITEMS and durata <= 290):
                    continue
                elif annuale and not (300 <= durata <= 400):
                    continue
                meta = per_end.get(end)
                if meta is None or str(ob.get("filed", "")) > str(meta.get("filing_date", "")):
                    per_end[end] = {"period_end": end, "filing_date": ob.get("filed"), "form": form,
                                    "fp": ob.get("fp"), "fy": ob.get("fy"), "accession": ob.get("accn"),
                                    "tipo": "trimestre" if trimestrale else "esercizio"}
                cella = valori_per_end.setdefault(end, {}).get(canon)
                # a parita' di fine periodo vince il flusso di 3 mesi, poi il deposito piu' recente
                chiave = (0 if trimestrale and (durata or 0) > 100 else 1, -priorita, str(ob.get("filed", "")))
                if cella is None or chiave > cella["_chiave"]:
                    valori_per_end[end][canon] = {"_chiave": chiave, "valore": ob.get("val"), "unita": unit,
                                                  "tag": tag, "durata_giorni": durata,
                        "metadata": {"period_start": ob.get("start"), "period_end": end,
                            "duration_days": durata, "fiscal_year_label": (
                                "FY" + str(ob["fy"]) if ob.get("fy") is not None else None),
                            "unit": unit, "currency": unit.split('/')[0] if unit in
                                ("USD", "EUR", "GBP", "USD/shares", "EUR/shares", "GBP/shares") else None,
                            "definition": tag, "form": form, "accession": ob.get("accn"),
                            "filing_date": ob.get("filed"), "fiscal_period": ob.get("fp"),
                            "source": "SEC companyfacts"}}
                if trimestrale and (canon in INSTANT_ITEMS or (durata is not None and 80 <= durata <= 100)):
                    prev = serie.setdefault(canon, {}).get(end)
                    if prev is None or (-priorita, str(ob.get("filed", ""))) >= prev[1]:
                        serie[canon][end] = (ob.get("val"), (-priorita, str(ob.get("filed", ""))))
    # Q4: per ogni esercizio con i tre 10-Q dello stesso esercizio (R-CASCATA, 07/10)
    q4_derivati: Dict[str, List[str]] = {}
    q4_mancanti: Dict[str, List[str]] = {}
    for fine_fy, meta in per_end.items():
        if meta["tipo"] != "esercizio":
            continue
        d_fy = datetime.fromisoformat(fine_fy)
        celle = valori_per_end.get(fine_fy, {})
        for canon in QUARTER_CORE:
            cella = celle.get(canon)
            if cella is None:
                continue
            if canon in INSTANT_ITEMS:
                serie.setdefault(canon, {})[fine_fy] = (cella["valore"], (0, "fy"))
                continue
            tre = [v[0] for e, v in (serie.get(canon) or {}).items()
                   if e != fine_fy and 0 < (d_fy - datetime.fromisoformat(e)).days < 300]
            if (canon in Q4_DERIVABILI and len(tre) == 3 and all(isinstance(x, (int, float)) for x in tre)
                    and isinstance(cella["valore"], (int, float))):
                serie.setdefault(canon, {})[fine_fy] = (cella["valore"] - sum(tre), (0, "q4"))
                q4_derivati.setdefault(fine_fy, []).append(canon)
            else:
                q4_mancanti.setdefault(fine_fy, []).append(canon)
    fine_trimestri = sorted({e for s in serie.values() for e in s})[-quarters:]
    # R-CASCATA (07/10): solo le voci core e al massimo 8 trimestri: la ricevuta del tool ha un tetto
    out_q = {k: {e: v[0] for e, v in sorted(s.items()) if e in fine_trimestri}
             for k, s in serie.items() if k in QUARTER_CORE}
    out_q = {k: v for k, v in out_q.items() if v}
    q4 = {"regola": REGOLA_Q4,
          "derivati": {e: v for e, v in sorted(q4_derivati.items()) if e in fine_trimestri},
          "non_derivabili": {e: v for e, v in sorted(q4_mancanti.items()) if e in fine_trimestri}}
    latest = None
    if per_end:
        end = max(per_end)
        latest = dict(per_end[end])
        celle = sorted(valori_per_end.get(end, {}).items())
        latest["values"] = {k: c["valore"] for k, c in celle}
        latest["metric_metadata"] = {k: dict(c["metadata"]) for k, c in celle}
        unita = {k: c["unita"] for k, c in celle if c["unita"] != "USD"}
        if unita:
            latest["units_non_usd"] = unita
        cumulati = {k: c["durata_giorni"] for k, c in celle
                    if latest["tipo"] == "trimestre" and (c.get("durata_giorni") or 0) > 100}
        if cumulati:
            latest["cumulati_da_inizio_esercizio_giorni"] = cumulati
    return {"quarters": out_q, "quarter_ends": fine_trimestri, "q4": q4, "latest_period": latest}


def get_financial_history(ticker: str, years: int = 10, fino_al: Optional[str] = None) -> Dict[str, Any]:
    """Storico annuale riga-per-riga dai filing SEC (10-K/20-F). ~30 voci canoniche + derivate."""
    try:
        from bellomberg.market_data.sec_edgar import lookup_cik, ticker_ambiguo_per_cik
        # Revisione G1 (04/10/2026): con un suffisso di listino senza alias verificato il CIK non si
        # risolve. Prima un .MI agganciava il fondo USA con le stesse lettere e rispondeva
        # «tassonomia atipica» (falso); chi chiama ripiega sull'ESEF dichiarandolo.
        ambiguo = ticker_ambiguo_per_cik(ticker)
        if ambiguo:
            return {"error": f"{ticker}: CIK SEC non risolto: {ambiguo}"}
        cik = lookup_cik(ticker)
    except Exception as e:
        return {"error": f"lookup CIK: {type(e).__name__}: {e}"}
    if not cik:
        return {"error": f"{ticker}: nessun CIK SEC (societa' senza filing US/ADR). "
                         "Per i nomi EU senza ADR usare get_fundamentals (yfinance, 4 anni)."}
    lettura: Dict[str, Any] = {}
    data = _fetch_companyfacts(cik, esito=lettura)
    if not data:
        return {"error": f"companyfacts non disponibile per CIK {cik}"}
    facts = data.get("facts") or {}
    out_items: Dict[str, Dict[int, Any]] = {}
    tags_used: Dict[str, str] = {}
    unit_used: Dict[str, str] = {}
    for canon, (tg, ti) in CANONICAL.items():
        ser, tag, unit = _annual_series(facts, tg, ti)
        if ser:
            yrs = sorted(ser.keys())[-years:]
            out_items[canon] = {y: ser[y] for y in yrs}
            tags_used[canon] = tag
            unit_used[canon] = unit
    if not out_items:
        return {"error": f"{ticker}: companyfacts presente ma nessuna voce mappata (tassonomia atipica)"}
    all_years = sorted({y for s in out_items.values() for y in s.keys()})[-years:]

    def g(item, y):
        v = out_items.get(item, {}).get(y)
        return float(v) if isinstance(v, (int, float)) else None

    derived: Dict[str, Dict[int, Any]] = {"gross_margin": {}, "operating_margin": {}, "net_margin": {},
                                          "capex_pct_revenue": {}, "payout_total": {}, "fcf": {}}
    for y in all_years:
        rev, gp, oi, ni = g("revenue", y), g("gross_profit", y), g("operating_income", y), g("net_income", y)
        cfo, capex = g("cfo", y), g("capex", y)
        div, bb = g("dividends_paid", y), g("buyback", y)
        if rev:
            if gp is not None: derived["gross_margin"][y] = round(gp / rev, 4)
            if oi is not None: derived["operating_margin"][y] = round(oi / rev, 4)
            if ni is not None: derived["net_margin"][y] = round(ni / rev, 4)
            if capex is not None: derived["capex_pct_revenue"][y] = round(abs(capex) / rev, 4)
        if ni and ni > 0:
            tot = (abs(div) if div else 0) + (abs(bb) if bb else 0)
            if tot: derived["payout_total"][y] = round(tot / ni, 3)
        if cfo is not None and capex is not None:
            derived["fcf"][y] = cfo - abs(capex)
    derived = {k: v for k, v in derived.items() if v}
    # FRESCHEZZA: trimestri dei 10-Q e ultimo periodo depositato con period_end/filing_date (ricevuta attestabile)
    trimestri = quarterly_history(facts, fino_al=fino_al)   # cutoff della run: mai un deposito successivo

    return {"ticker": ticker.upper(), "cik": cik,
            "quarters": trimestri["quarters"], "quarter_ends": trimestri["quarter_ends"], "q4": trimestri["q4"],
            "latest_period": trimestri["latest_period"],
            "entity": data.get("entityName"),
            "years": all_years, "n_items": len(out_items),
            "items": out_items, "derived": derived,
            "tags_used": tags_used, "units": unit_used,
            "_source": "SEC XBRL companyfacts (10-K/20-F/40-F fp=FY; trimestri dai 10-Q fp=Q1-Q3)",
            "_timestamp": datetime.now().isoformat(timespec="seconds"),
            # REV_G2a R-4: eta' del dato (cache fino a 7 giorni) e cache nota come superata
            "da_cache": lettura.get("da_cache"), "companyfacts_letto_il": lettura.get("letto_il"),
            **({"index_note": "companyfacts in cache anteriore a un deposito gia' pubblicato ("
                              + str(superata.get("motivo")) + "): possibili dati recenti mancanti"}
               if (superata := cache_superata(cik)) and lettura.get("da_cache") else {})}


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "MSTR"
    r = get_financial_history(t)
    if r.get("error"):
        print("ERRORE:", r["error"])
    else:
        print(f"{r['entity']} (CIK {r['cik']}) - {r['n_items']} voci, anni {r['years']}")
        for k in ("revenue", "operating_income", "net_income", "equity", "cfo", "capex", "dividends_paid"):
            s = r["items"].get(k)
            if s:
                print(f"  {k:18}", {y: (f"{v/1e9:.1f}bn" if abs(v) > 1e8 else v) for y, v in s.items()})
        for k, s in (r.get("derived") or {}).items():
            print(f"  [{k}]", {y: v for y, v in list(s.items())[-5:]})
