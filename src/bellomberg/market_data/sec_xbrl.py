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
import re
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


def giorno_di_riferimento(oggi: Any = None) -> str:
    """Data ISO del giorno di riferimento della run (`oggi`: str ISO, date o datetime). Assente: l'orologio di
    sistema, letto UNA volta da chi chiama (v4, Opus 5.5: riproducibilita' del look-ahead)."""
    if oggi is None or oggi == "":
        return datetime.now().date().isoformat()
    return (oggi.isoformat() if hasattr(oggi, "isoformat") else str(oggi))[:10]


def depositato_entro(filed, fino_al: Optional[str], oggi: Optional[str] = None,
                     limita_a_oggi: Optional[bool] = None) -> bool:
    """True se un deposito con data `filed` era noto alla run con cutoff `fino_al` (ISO).

    R-FONTI 10/10 v3 (Opus 5.5): le date di deposito SEC dei companyfacts e delle submissions non hanno l'ora.
    Con un cutoff STORICO (fino_al anteriore a oggi) un deposito dello STESSO giorno puo' essere successivo
    all'ora della decisione: per prudenza e' escluso (filed < fino_al). Con fino_al = oggi (run corrente)
    vale filed <= fino_al: cio' che la fonte riporta adesso e' gia' pubblicato. Data mancante: non noto.
    v4 (Opus 5.5, riserva D della terza revisione): `oggi` e' il giorno di riferimento della run (ISO, date o
    datetime); senza, l'orologio di sistema, e allora lo stesso cutoff puo' dare esiti diversi rieseguito un altro
    giorno: per una run riproducibile chi chiama passa il giorno in cui la run e' stata eseguita.
    v5 (ri-verifica di d487ac7): con `fino_al` E `oggi` DICHIARATO il cutoff effettivo e' min(fino_al, oggi) (un
    cutoff nel futuro non fa entrare depositi successivi al giorno della run); con il solo `oggi` dichiarato vale
    filed <= oggi. `limita_a_oggi` (default: `oggi` passato) lo dice esplicitamente a chi ha gia' fissato il giorno
    della chiamata dall'orologio: senza un giorno dichiarato il cutoff resta `fino_al` (com'era)."""
    if limita_a_oggi is None:
        limita_a_oggi = oggi is not None
    if not fino_al and not limita_a_oggi:
        return True
    filed = str(filed or "")[:10]
    if len(filed) != 10:
        return False
    oggi = giorno_di_riferimento(oggi)
    cutoff = (min(str(fino_al)[:10], oggi) if limita_a_oggi else str(fino_al)[:10]) if fino_al else oggi
    return filed < cutoff or (filed == cutoff and cutoff >= oggi)


def _facts_fino_al(facts: Dict[str, Any], fino_al: Optional[str], oggi: Any = None) -> Dict[str, Any]:
    """Copia di companyfacts con le sole osservazioni depositate entro il cutoff (`depositato_entro`).

    R-FONTI 10/10 v3 (Opus 5.5, riserva MEDIO della revisione v2): le serie annuali e l'ultimo esercizio del
    ripiego leggevano companyfacts INTERO anche con fino_al (FY2024 da un 20-F depositato dopo il cutoff
    compariva nello storico mentre latest_period diceva FY2023). Ora storico, ripiego e trimestri vedono lo
    stesso insieme di depositi. `oggi`: giorno di riferimento della run (`depositato_entro`)."""
    if not fino_al:
        return facts
    esplicito, oggi = oggi is not None, giorno_di_riferimento(oggi)
    out: Dict[str, Any] = {}
    for taxo, nodo in (facts or {}).items():
        if not isinstance(nodo, dict):
            continue
        tags = {}
        for tag, item in nodo.items():
            units = {u: [ob for ob in obs if isinstance(ob, dict) and depositato_entro(ob.get("filed"), fino_al, oggi,
                                                                                            esplicito)]
                     for u, obs in ((item or {}).get("units") or {}).items()}
            units = {u: obs for u, obs in units.items() if obs}
            if units:
                tags[tag] = {**item, "units": units}
        out[taxo] = tags
    return out


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


def quarterly_history(facts: Dict[str, Any], quarters: int = 8, fino_al: Optional[str] = None,
                      oggi: Any = None) -> Dict[str, Any]:
    """Trimestri dai 10-Q (fp Q1-Q3) piu' il Q4 derivato, e ultimo periodo depositato (10-Q o annuale).

    Si usano solo i fatti del periodo del deposito (mai i comparativi). `quarters`: {voce: {period_end:
    valore}} con flussi di 3 mesi (80-100 giorni) e saldi; il Q4 dei flussi e' FY - (Q1+Q2+Q3) e lo
    dichiara `q4` (derivati e non derivabili), mai una somma su trimestri incompleti.
    `latest_period`: il periodo piu' recente con period_end, filing_date, form, accession e i valori
    di quel periodo; le voci di cassa dei 10-Q sono cumulate da inizio esercizio e lo dicono.
    `fino_al` (ISO): solo depositi con filed <= fino_al (cutoff della Trade Idea, mai il futuro); `oggi`: giorno
    di riferimento della run per il deposito dello stesso giorno del cutoff (`depositato_entro`)."""
    esplicito, oggi = oggi is not None, giorno_di_riferimento(oggi)
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
                if fino_al and (end > fino_al or not depositato_entro(ob.get("filed"), fino_al, oggi, esplicito)):
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
                            "source": (fonte_ixbrl(ob.get("accn")) if ob.get("fonte") == ORIGINE_IXBRL
                                       else "SEC companyfacts")}}
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


# RIPIEGO iXBRL (R-FONTI 10/10, Opus 5.5): companyfacts puo' restare indietro di mesi rispetto a un deposito
# gia' pubblicato (prova reale su un emittente del book: 20-F FY2025 depositato l'08/04/2026, companyfacts fermo al FY2024 il 10/10).
# Se il deposito e' inline XBRL, i suoi fatti si leggono dal documento (ixbrl_oim, convertitore del progetto)
# con le stesse voci e le stesse guardie: SOLO l'emittente del CIK, solo fatti senza assi con unita', periodo
# esatto, valori incoerenti nello stesso documento tolti e dichiarati. La fonte e' sempre dichiarata
# («iXBRL del deposito <accession> convertito in locale», origine `ixbrl_locale`), mai «companyfacts».
# Sui periodi presenti in entrambe vale companyfacts e una divergenza e' un CONFLITTO dichiarato.
ORIGINE_IXBRL = "ixbrl_locale"
SCHEMA_CIK = "http://www.sec.gov/CIK"
_IX_NS = b"http://www.xbrl.org/2013/inlineXBRL"
_ACCESSION_URL = re.compile(r"/Archives/edgar/data/\d{1,10}/(\d{10})(\d{2})(\d{6})/")
_DIM_BASE = {"concept", "entity", "period", "unit", "language"}
MAX_DOCUMENTO_IXBRL = 80 * 1024 * 1024
RIPIEGO_TTL_S = 24 * 3600          # elenco depositi annuali (submissions) riletto al piu' una volta al giorno
GIORNI_ESERCIZIO_ATTESO = 400      # oltre la chiusura dell'ultimo esercizio: il successivo puo' esistere
MAX_DEPOSITI_RIPIEGO = 2


def accession_da_url(url) -> Optional[str]:
    """«0001292814-26-002166» dall'URL EDGAR del documento, None se non e' un percorso di deposito."""
    m = _ACCESSION_URL.search(str(url or ""))
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def fonte_ixbrl(accession) -> str:
    return f"iXBRL del deposito {accession or 'n.d.'} convertito in locale"


def e_ixbrl(path, testa: int = 512 * 1024) -> bool:
    """Il file dichiara il namespace inline XBRL nella testa (i 6-K senza XBRL no)."""
    try:
        with open(path, "rb") as fh:
            return _IX_NS in fh.read(testa)
    except OSError:
        return False


_MESI_ABBREVIATI = {m[:3]: m for m in ("january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december")}
_MESI_ABBREVIATI["sept"] = "september"


def _data_iso(valore) -> str:
    """Data ISO da un fatto dei (il convertitore lascia il testo visualizzato: «December 31, 2025»).

    v4 (Opus 5.5, audit di generalita' punto 6): anche «Dec. 31, 2025», «31 Dec 2025», «December 31st, 2025»,
    «31.12.2025» (giorno.mese.anno). La barra («12/31/2025») resta testo: l'ordine giorno/mese non e' nel
    valore; chi legge il fatto usa allora la fine del periodo del contesto (`fatti_ixbrl`)."""
    testo = " ".join(str(valore or "").split())
    try:
        return datetime.fromisoformat(testo).date().isoformat()
    except ValueError:
        pass
    from bellomberg.market_data.filing_verifica import _data
    esteso = re.sub(r"\b(sept|[a-z]{3})\.?(?=\s|$)",
                    lambda m: _MESI_ABBREVIATI.get(m[1].lower(), m[0]), testo, flags=re.I)
    for t in (testo, esteso):
        try:
            return _data(t).isoformat()
        except (ValueError, TypeError):
            continue
    return testo


def fatti_ixbrl(raw: Dict[str, Any], cik, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fatti numerici di un xBRL-JSON (ixbrl_oim) nella forma companyfacts {tassonomia: {concetto: {units}}}.

    Guardie: emittente = CIK `cik` con schema SEC (le altre entita' contate in `altre_entita`); solo fatti
    us-gaap/ifrs-full senza assi, con unita' e valore numerico; stesso concetto/unita'/periodo con valori
    diversi nel documento -> tolto e dichiarato in `incoerenti`. Ogni riga porta `fonte: ixbrl_locale` e i
    campi di `meta` (accn, form, fp, filed). `periodo_documento`: dei:DocumentPeriodEndDate (insieme)."""
    from bellomberg.market_data.filing_esef import _istante, _periodo
    ns = ((raw or {}).get("documentInfo") or {}).get("namespaces") or {}

    def tassonomia(prefisso):
        uri = str(ns.get(prefisso) or "")
        if prefisso == "ifrs-full" or "xbrl.ifrs.org" in uri:
            return "ifrs-full"
        if "fasb.org/us-gaap" in uri:
            return "us-gaap"
        if "xbrl.sec.gov/dei" in uri:
            return "dei"
        return None
    cik_n = int(str(cik))
    righe: Dict[Any, set] = {}
    altre, periodo_doc, periodo_contesto = 0, set(), set()
    for f in ((raw or {}).get("facts") or {}).values():
        dims = (f or {}).get("dimensions") or {}
        schema, _, ident = str(dims.get("entity") or "").partition(":")
        if ns.get(schema) != SCHEMA_CIK or not ident.strip().isdigit() or int(ident) != cik_n:
            altre += 1
            continue
        prefisso, _, nome = str(dims.get("concept") or "").partition(":")
        tax = tassonomia(prefisso)
        if set(dims) - _DIM_BASE:
            continue  # scomposizione per assi: non il totale dell'emittente
        if tax == "dei" and nome == "DocumentPeriodEndDate":
            # v4 (audit di generalita' punto 6): valore in un formato non convertibile -> fine del periodo del
            # CONTESTO del fatto dei (dichiarato come tale), mai il testo grezzo confrontato con la reportDate
            data = _data_iso(f.get("value"))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
                periodo = str(dims.get("period") or "")
                fine = (_periodo(periodo) if "/" in periodo else (None, _istante(periodo)))[1] if periodo else None
                data = fine or data
                if fine:
                    periodo_contesto.add(fine)
            periodo_doc.add(data)
            continue
        if tax not in ("us-gaap", "ifrs-full") or not dims.get("unit"):
            continue
        try:
            valore = float(f.get("value"))
        except (TypeError, ValueError):
            continue
        periodo = str(dims.get("period") or "")
        chiave = _periodo(periodo) if "/" in periodo else ((None, _istante(periodo)) if periodo else None)
        if not chiave or not chiave[1]:
            continue
        unita = "/".join(u.split(":", 1)[-1] for u in str(dims["unit"]).split("/"))
        decimali = f.get("decimals")
        righe.setdefault((tax, nome, unita, chiave), set()).add(
            (valore, decimali if isinstance(decimali, int) and not isinstance(decimali, bool) else None))
    facts: Dict[str, Dict[str, Any]] = {}
    incoerenti, arrotondati = [], 0
    for (tax, nome, unita, (inizio, fine)), valori in sorted(righe.items(), key=lambda kv: str(kv[0])):
        scelto = valore_coerente(valori)
        if scelto is None:
            incoerenti.append({"concetto": f"{tax}:{nome}", "unita": unita, "inizio": inizio, "fine": fine,
                               "valori": sorted({v for v, _ in valori})})
            continue
        arrotondati += len({v for v, _ in valori}) > 1
        riga = {**(meta or {}), "end": fine, "val": scelto[0], "fonte": ORIGINE_IXBRL}
        if scelto[1] is not None:
            riga["decimals"] = scelto[1]
        if inizio:
            riga["start"] = inizio
        facts.setdefault(tax, {}).setdefault(nome, {"units": {}})["units"].setdefault(unita, []).append(riga)
    return {"facts": facts, "altre_entita": altre, "incoerenti": incoerenti,
            "coerenti_per_arrotondamento": arrotondati, "periodo_documento": sorted(periodo_doc),
            **({"periodo_documento_dal_contesto": sorted(periodo_contesto)} if periodo_contesto else {})}


def tolleranza(decimali) -> float:
    """Mezza unita' dell'ultima cifra dichiarata (decimals=-3: 500); 0 se la precisione non e' dichiarata."""
    return 0.5 * 10.0 ** (-decimali) if isinstance(decimali, int) and not isinstance(decimali, bool) else 0.0


def valore_coerente(valori):
    """(valore, decimals) del fatto piu' preciso se TUTTI i valori dello stesso fatto (stesso concetto, unita' e
    periodo) coincidono entro l'arrotondamento dichiarato di ciascuno (BASSI v2: «1.234.567» con decimals=0 e
    «1.235.000» con decimals=-3 sono lo stesso fatto); None se almeno uno diverge oltre l'arrotondamento."""
    ordinati = sorted(valori, key=lambda vd: (vd[1] is not None, vd[1] if vd[1] is not None else 0), reverse=True)
    # precisione non dichiarata = esatta (prima); poi decimals piu' alto
    ordinati = [vd for vd in ordinati if vd[1] is None] + [vd for vd in ordinati if vd[1] is not None]
    migliore = ordinati[0]
    for v, d in ordinati[1:]:
        if abs(v - migliore[0]) > tolleranza(d) + 1e-9 * max(abs(v), abs(migliore[0]), 1.0):
            return None
    return migliore


def converti_istanza_xbrl(path) -> Dict[str, Any]:
    """xBRL-JSON (dict, stessa forma di ixbrl_oim.converti_xhtml) da un'ISTANZA XBRL (.xml) di un deposito EDGAR.

    v4 (Opus 5.5, audit di generalita' punto 5): per i depositi inline EDGAR pubblica anche l'istanza estratta
    («<documento>_htm.xml»), che riunisce i fatti di TUTTI i documenti dell'insieme inline (prospetti in un altro
    file del deposito, contesti dichiarati in un altro documento). Stesse regole del convertitore del progetto
    (contesti, unita', periodo OIM a fine esclusiva); nessuna rete, niente DTD ne' entita' esterne."""
    from lxml import etree
    from bellomberg.market_data import ixbrl_oim as oim
    import html as _html
    conv = oim._Convertitore()
    try:
        radice = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                                                        huge_tree=True, remove_comments=True,
                                                        remove_pis=True)).getroot()
    except etree.XMLSyntaxError as exc:
        raise oim.PacchettoNonValido(f"istanza XBRL non ben formata: {exc}") from exc
    for el in radice:
        if not isinstance(el.tag, str):
            continue
        chiave = (oim._ns(el.tag), oim._locale(el.tag))
        if chiave == (oim.XBRLI, "context"):
            conv.contesto(el)
        elif chiave == (oim.XBRLI, "unit"):
            conv.unita_xbrl(el)
        elif chiave == (oim.LINK, "schemaRef"):
            conv.tassonomia.append(el.get(f"{{{oim.XLINK}}}href"))
        elif el.get("contextRef"):
            testo = (el.text or "").strip()
            segno = "-" if el.get("unitRef") and testo.startswith("-") else None
            conv.grezzi.append({
                "tipo": "nonFraction" if el.get("unitRef") else "nonNumeric", "id": el.get("id"),
                "concept": f"{conv.prefissi.prefisso(chiave[0], el.prefix)}:{chiave[1]}",
                "contesto": el.get("contextRef"), "unita": el.get("unitRef"),
                "markup": _html.escape(testo.lstrip("-") if segno else testo, quote=False), "lingua": None,
                "nil": el.get(f"{{{oim.XSI}}}nil") in ("true", "1"), "escape": False, "segue": None,
                "formato": None, "scala": None, "segno": segno, "decimali": el.get("decimals"),
                "precisione": el.get("precision")})
    out = conv.risultato()
    out["conversione"]["origine"] = "istanza XBRL del deposito convertita localmente"
    return out


def url_istanza_xbrl(url_documento) -> Optional[str]:
    """«.../nvo-20251231.htm» -> «.../nvo-20251231_htm.xml» (istanza estratta da EDGAR), None se non .htm."""
    m = re.match(r"(.+/[^/]+)\.htm$", str(url_documento or ""), re.I)
    return f"{m[1]}_htm.xml" if m else None


def fatti_ixbrl_documento(path, cik, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """fatti_ixbrl del documento convertito in locale; ValueError se nessun fatto e' dell'emittente."""
    from bellomberg.market_data import ixbrl_oim
    raw = converti_istanza_xbrl(path) if str(path).lower().endswith(".xml") else ixbrl_oim.converti_xhtml(path)
    out = fatti_ixbrl(raw, cik, meta=meta)
    if not out["facts"]:
        raise ValueError(f"iXBRL senza fatti numerici dell'emittente CIK {str(cik).zfill(10)} "
                         f"({out['altre_entita']} fatti di altre entita')")
    out["conversione"] = {k: (raw.get("conversione") or {}).get(k) for k in ("fatti", "limiti")}
    return out


def _chiave_riga(r):
    return (r.get("start"), r.get("end"))


def unisci_fatti(base: Dict[str, Any], aggiunta: Dict[str, Any], *, fonte_base: str, fonte_aggiunta: str,
                 concetti: Optional[set] = None):
    """(fatti uniti, rideterminazioni). Le righe di `aggiunta` (deposito PIU' RECENTE di `base`) entrano per i
    periodi che `base` non ha; sullo stesso periodo, concetto e unita' un valore diverso OLTRE l'arrotondamento
    dichiarato (decimals del deposito) e' un comparativo RIDETERMINATO: vale il deposito piu' recente, come
    nello storico ESEF e come fa `_series_from_tag` (vince il «filed» piu' recente), e si dichiara
    (riserva MEDIO-2 v2: prima vinceva companyfacts e la serie mescolava due basi, +257% falso sui crediti di
    un emittente del book). `concetti`: solo questi «tassonomia:concetto» nella dichiarazione. `base` intatto."""
    unito = {tax: dict(nodo) for tax, nodo in (base or {}).items()}
    rideterminazioni = []
    for tax, nodo in (aggiunta or {}).items():
        dest = unito.setdefault(tax, {})
        for nome, item in nodo.items():
            vecchio = dest.get(nome) or {"units": {}}
            unita = {u: list(r) for u, r in (vecchio.get("units") or {}).items()}
            for u, righe in (item.get("units") or {}).items():
                presenti: Dict[Any, List[Any]] = {}
                for r in unita.get(u, []):
                    presenti.setdefault(_chiave_riga(r), []).append(r.get("val"))
                for r in righe:
                    k = _chiave_riga(r)
                    if k not in presenti:
                        unita.setdefault(u, []).append(r)
                        presenti[k] = [r.get("val")]
                        continue
                    tol = tolleranza(r.get("decimals"))
                    diversi = sorted({v for v in presenti[k] if not isinstance(v, (int, float)) or not isinstance(
                        r.get("val"), (int, float)) or abs(v - r["val"]) > tol + 1e-9 * max(abs(v), 1.0)})
                    if not diversi:
                        continue  # stesso valore entro l'arrotondamento dichiarato: vale la riga gia' presente
                    unita[u] = [x for x in unita.get(u, []) if _chiave_riga(x) != k] + [r]
                    presenti[k] = [r.get("val")]
                    if concetti is None or f"{tax}:{nome}" in concetti:
                        rideterminazioni.append({
                            "concetto": f"{tax}:{nome}", "unita": u, "inizio": k[0], "fine": k[1],
                            "valore_precedente": diversi[0] if len(diversi) == 1 else diversi,
                            "fonte_precedente": fonte_base, "valore_usato": r.get("val"),
                            "fonte_usata": fonte_aggiunta,
                            "nota": "comparativo rideterminato nel deposito piu' recente: vale il suo valore"})
            dest[nome] = {**vecchio, "units": unita}
    return unito, rideterminazioni


def _concetti_canonici(chiavi=None) -> set:
    out = set()
    for canon, (tg, ti) in CANONICAL.items():
        if chiavi is None or canon in chiavi:
            out.update(f"us-gaap:{t}" for t in tg)
            out.update(f"ifrs-full:{t}" for t in ti)
    return out


def _ultima_fine_annuale(facts: Dict[str, Any]) -> Optional[str]:
    """Fine dell'ultimo esercizio depositato (10-K/20-F/40-F) fra le voci canoniche di companyfacts."""
    fine = None
    for _canon, (tg, ti) in CANONICAL.items():
        for _tag, _unit, obs in _osservazioni(facts, tg, ti):
            for ob in obs:
                e = ob.get("end") or ""
                if ob.get("form") in ANNUAL_FORMS and len(e) == 10 and (fine is None or e > fine):
                    fine = e
    return fine


def _submissions_sec(cik: str) -> Dict[str, Any]:
    """submissions EDGAR dell'emittente (rete SEC nel ritmo condiviso). Eccezione se non disponibile."""
    import requests
    from bellomberg.market_data import sec_edgar
    from bellomberg.market_data.sec_edgar import _headers
    sec_edgar.attendi_sec()
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=_headers(), timeout=30)
    if r.status_code != 200:
        raise ValueError(f"submissions SEC: HTTP {r.status_code}")
    dati = r.json()
    if not isinstance(dati, dict) or not isinstance((dati.get("filings") or {}).get("recent"), dict):
        raise ValueError("submissions SEC senza elenco filings.recent")
    return dati


def _scarica_documento_sec(url: str, cartella: str) -> Dict[str, Any]:
    """Snapshot del documento primario (solo www.sec.gov, HTTPS, tetto MAX_DOCUMENTO_IXBRL)."""
    from bellomberg.market_data.lettore_trimestrali import scarica_documento
    return scarica_documento(url, cartella, host_consentiti={"www.sec.gov"}, public_only=True,
                             max_bytes=MAX_DOCUMENTO_IXBRL, solo_https=True)


def _annuali_ixbrl_dopo(submissions: Dict[str, Any], cik: str, dopo: Optional[str], fino_al: Optional[str]):
    """Depositi annuali (10-K/20-F/40-F, rettifiche escluse) con reportDate > `dopo` e depositati entro
    `fino_al` (cutoff della run: MAI un deposito successivo, nessun look-ahead). `dopo`/`fino_al` None: tutti."""
    recent = submissions["filings"]["recent"]

    def col(nome, i):
        v = recent.get(nome) or []
        return v[i] if i < len(v) else None
    out = []
    for i, form in enumerate(recent.get("form") or []):
        report, filed = str(col("reportDate", i) or ""), str(col("filingDate", i) or "")
        if form not in ("10-K", "20-F", "40-F") or not report:
            continue
        acc, doc = str(col("accessionNumber", i) or ""), str(col("primaryDocument", i) or "")
        if not acc or not doc:
            continue
        out.append({"accn": acc, "form": form, "filed": filed, "report_date": report,
                    "inline": bool(col("isInlineXBRL", i)),
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"})
    return _filtra_depositi(out, dopo, fino_al)


def _filtra_depositi(elenco, dopo: Optional[str], fino_al: Optional[str], oggi: Any = None):
    """Solo reportDate > `dopo` (esercizi che companyfacts non ha) e depositati entro `fino_al` (cutoff, regola
    di `depositato_entro`: lo stesso giorno di un cutoff storico e' escluso; `oggi` = giorno di riferimento)."""
    esplicito = oggi is not None and bool(fino_al)  # senza cutoff l'elenco resta intero (cache)
    oggi = giorno_di_riferimento(oggi)
    out = [d for d in elenco if isinstance(d, dict) and (not dopo or str(d.get("report_date") or "") > dopo)
           and depositato_entro(d.get("filed"), fino_al, oggi, esplicito)]
    return sorted(out, key=lambda d: (d["report_date"], d["filed"]), reverse=True)


def _ripiego_path(cik: str) -> str:
    return os.path.join(CACHE_DIR, f"ixbrl_CIK{cik}.json")


# Riserva MEDIO-3 v2: memoria dei fallimenti. Il documento di un deposito EDGAR e' immutabile: una conversione
# fallita (iXBRL senza fatti dell'emittente, periodo diverso, file rotto) si ricorda per accession + sha256 e
# non si riscarica (fino a 80 MB, ~22 chiamate per run) per GIORNI_DEPOSITO_INVALIDO giorni; un download o
# l'elenco submissions non riusciti (rete, SEC) si ricordano per RIPIEGO_ERRORE_TTL_S. Sempre dichiarato.
GIORNI_DEPOSITO_INVALIDO = 30
RIPIEGO_ERRORE_TTL_S = 3600


def _leggi_cache_ripiego(cik: str) -> Dict[str, Any]:
    try:
        with open(_ripiego_path(cik), "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) and cache.get("versione") == 2 else {}


def _fresco(quando, ttl_s) -> bool:
    try:
        return 0 <= time.time() - float(quando) < ttl_s
    except (TypeError, ValueError):
        return False


def ripiego_ixbrl_annuale(cik: str, facts: Dict[str, Any], fino_al: Optional[str] = None,
                          oggi: Any = None) -> Dict[str, Any]:
    """Esercizi annuali depositati ma assenti da companyfacts, letti dall'iXBRL del deposito.

    {"stato": "non_necessario" | "nessun_deposito" | "ok" | "parziale" | "errore", "motivo", "depositi":
    [{accn, form, filed, report_date, url, sha256, fonte, facts, conversione, ...}]}. Rete solo se
    companyfacts e' fermo da oltre GIORNI_ESERCIZIO_ATTESO dalla chiusura dell'ultimo esercizio. Cache per
    emittente: elenco COMPLETO dei depositi annuali (letto al piu' ogni RIPIEGO_TTL_S, filtrato per `fino_al`
    a ogni chiamata: una run storica non vede depositi successivi al suo cutoff), conversioni per accession
    (deposito immutabile), fallimenti per accession + sha256 ed errore dell'elenco (riserva MEDIO-3 v2).
    `oggi` (datetime, date o ISO): giorno di riferimento della run, anche per il filtro del cutoff (v4)."""
    ultima = _ultima_fine_annuale(facts)
    if isinstance(oggi, str) and oggi:
        oggi = datetime.fromisoformat(oggi[:10])
    elif oggi is not None and not isinstance(oggi, datetime):
        oggi = datetime(oggi.year, oggi.month, oggi.day)
    oggi_dichiarato, giorno = oggi is not None, giorno_di_riferimento(oggi)
    rif = datetime.fromisoformat(fino_al) if fino_al else (oggi or datetime.now())
    if not ultima:
        return {"stato": "non_necessario", "motivo": "companyfacts senza esercizi annuali", "depositi": []}
    if (rif - datetime.fromisoformat(ultima)).days <= GIORNI_ESERCIZIO_ATTESO:
        return {"stato": "non_necessario", "motivo": None, "depositi": []}
    cache = _leggi_cache_ripiego(cik) or {"versione": 2}
    motivi: List[str] = []

    def scrivi():
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(tmp, _ripiego_path(cik))
        except OSError as e:
            motivi.append(f"cache del ripiego non scritta ({type(e).__name__}): sara' riletta dalla rete")

    elenco_noto = cache.get("elenco")
    if isinstance(elenco_noto, list) and _fresco(cache.get("elenco_letto_at"), RIPIEGO_TTL_S):
        tutti = elenco_noto
    elif isinstance(cache.get("errore_elenco"), dict) and _fresco(cache["errore_elenco"].get("at"),
                                                                   RIPIEGO_ERRORE_TTL_S):
        err = cache["errore_elenco"]
        return {"stato": "errore", "depositi": [],
                "motivo": f"elenco depositi SEC non letto ({err.get('motivo')}; errore del "
                          f"{datetime.fromtimestamp(float(err['at'])).isoformat(timespec='minutes')}, non ritentato "
                          f"per {RIPIEGO_ERRORE_TTL_S // 60} minuti): esercizi dopo il {ultima} non verificati"}
    else:
        try:
            tutti = _annuali_ixbrl_dopo(_submissions_sec(cik), cik, None, None)
        except Exception as e:
            cache["errore_elenco"] = {"motivo": f"{type(e).__name__}: {str(e)[:160]}", "at": time.time()}
            scrivi()
            return {"stato": "errore", "depositi": [],
                    "motivo": f"elenco depositi SEC non letto ({type(e).__name__}: {str(e)[:160]}): esercizi dopo "
                              f"il {ultima} non verificati" + (f"; {motivi[0]}" if motivi else "")}
        cache.update(elenco=tutti, elenco_letto_at=time.time())
        cache.pop("errore_elenco", None)
    elenco = _filtra_depositi(tutti, ultima, fino_al, giorno if oggi_dichiarato else None)
    if not elenco:
        scrivi()
        return {"stato": "nessun_deposito", "depositi": [],
                "motivo": f"nessun deposito annuale dopo il {ultima}" + (f" depositato entro il {fino_al}"
                                                                         if fino_al else "") + " nell'elenco SEC"}
    convertiti = cache.setdefault("convertiti", {})
    falliti = cache.setdefault("falliti", {})
    concetti = _concetti_canonici()
    depositi = []
    for d in elenco[:MAX_DEPOSITI_RIPIEGO]:
        etichetta = f"{d['form']} {d['accn']} ({d['report_date']})"
        if not d.get("inline"):
            motivi.append(f"{etichetta}: non inline XBRL, nessun fatto da convertire")
            continue
        voce = convertiti.get(d["accn"])
        if isinstance(voce, dict) and voce.get("facts"):
            depositi.append(voce)
            continue
        noto = falliti.get(d["accn"])
        if isinstance(noto, dict) and _fresco(noto.get("at"), GIORNI_DEPOSITO_INVALIDO * 86400
                                               if noto.get("sha256") else RIPIEGO_ERRORE_TTL_S):
            motivi.append(f"{etichetta}: gia' fallito il "
                          f"{datetime.fromtimestamp(float(noto['at'])).isoformat(timespec='minutes')} "
                          + (f"(sha256 {str(noto['sha256'])[:12]}, deposito immutabile)" if noto.get("sha256")
                             else "(download)") + f": {noto.get('motivo')}; non riscaricato")
            continue
        os.makedirs(CACHE_DIR, exist_ok=True)
        cartella = tempfile.mkdtemp(dir=CACHE_DIR, prefix="ixbrl_")
        sha = None
        try:
            meta = {"accn": d["accn"], "form": d["form"], "fp": "FY", "filed": d["filed"]}

            scaricati = {}

            def leggi(url):
                snap = _scarica_documento_sec(url, cartella)
                if snap.get("stato") != "ok":
                    raise ValueError(snap.get("motivo") or "documento non scaricato")
                scaricati[url] = snap.get("sha256")
                letto = fatti_ixbrl_documento(snap["path"], cik, meta=meta)
                if d["report_date"] not in letto["periodo_documento"]:
                    raise ValueError(f"periodo del documento {letto['periodo_documento'] or 'non dichiarato'} "
                                     f"diverso dalla reportDate SEC {d['report_date']}")
                return snap.get("sha256"), letto
            # v4 (audit di generalita' punto 5): prospetti in un altro file del deposito o contesti in un altro
            # documento dell'insieme inline -> il documento primario non basta; si legge l'istanza estratta da
            # EDGAR, dichiarata. Fallisce anche quella: fallimento dichiarato con ENTRAMBI i motivi e ricordato con
            # lo sha256 del documento primario (deposito immutabile: niente nuovo download per
            # GIORNI_DEPOSITO_INVALIDO). Primario non scaricato (rete): nessun tentativo sull'istanza.
            documento = d["url"]
            try:
                sha, letto = leggi(documento)
            except ValueError as primo:
                sha = scaricati.get(documento)
                istanza = url_istanza_xbrl(d["url"])
                if not istanza or sha is None:
                    raise
                try:
                    sha_istanza, letto = leggi(istanza)
                except Exception as secondo:
                    raise ValueError(f"documento primario: {primo}; istanza XBRL: "
                                     f"{type(secondo).__name__ + ': ' if not isinstance(secondo, ValueError) else ''}"
                                     f"{secondo}") from secondo
                sha, documento = sha_istanza, istanza
            voce = {**d, "sha256": sha, "fonte": fonte_ixbrl(d["accn"]) + (
                        " (istanza XBRL estratta da EDGAR: il documento primario non bastava)"
                        if documento != d["url"] else ""), "documento_letto": documento,
                    "convertito_il": datetime.now().isoformat(timespec="seconds"),
                    "conversione": letto["conversione"], "altre_entita": letto["altre_entita"],
                    "coerenti_per_arrotondamento": letto.get("coerenti_per_arrotondamento", 0),
                    "incoerenti": [x for x in letto["incoerenti"] if x["concetto"] in concetti],
                    "facts": {tax: {n: it for n, it in nodo.items() if f"{tax}:{n}" in concetti}
                              for tax, nodo in letto["facts"].items()}}
            convertiti[d["accn"]] = voce
            falliti.pop(d["accn"], None)
            depositi.append(voce)
        except Exception as e:
            motivo = f"{type(e).__name__}: {str(e)[:200]}"
            falliti[d["accn"]] = {"sha256": sha, "motivo": motivo, "at": time.time()}
            motivi.append(f"{etichetta}: {motivo}")
        finally:
            import shutil
            shutil.rmtree(cartella, ignore_errors=True)
    scrivi()
    stato = "ok" if depositi and not motivi else "parziale" if depositi else "errore"
    return {"stato": stato, "depositi": depositi, "motivo": "; ".join(motivi) or None}


def get_financial_history(ticker: str, years: int = 10, fino_al: Optional[str] = None,
                          oggi: Any = None) -> Dict[str, Any]:
    """Storico annuale riga-per-riga dai filing SEC (10-K/20-F). ~30 voci canoniche + derivate.

    `oggi` (v4, Opus 5.5): giorno di riferimento della run (ISO, date o datetime), UNO per tutta la chiamata
    (filtro di companyfacts, ripiego iXBRL e trimestri: `depositato_entro`, cutoff effettivo min(fino_al, oggi));
    assente = orologio di sistema solo per il deposito dello stesso giorno del cutoff (v5: nessun limite al giorno
    della run senza un giorno dichiarato). Serve a rieseguire una run storica con lo stesso esito."""
    oggi = giorno_di_riferimento(oggi) if oggi is not None else None
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
    # v3: con un cutoff, companyfacts senza i depositi successivi (serie annuali, ultimo esercizio del ripiego e
    # trimestri sullo stesso insieme: nessun look-ahead)
    facts = facts_cf = _facts_fino_al(data.get("facts") or {}, fino_al, oggi)
    # RIPIEGO iXBRL (R-FONTI 10/10): esercizi depositati ma non ancora in companyfacts, dal documento
    ripiego = ripiego_ixbrl_annuale(cik, facts_cf, fino_al=fino_al, oggi=oggi)
    rideterminati: List[Dict[str, Any]] = []
    # dal deposito MENO recente al piu' recente: sullo stesso periodo vale l'ultimo deposito (comparativo
    # rideterminato), come nello storico ESEF; ogni sostituzione si dichiara (riserva MEDIO-2 v2)
    for dep in sorted(ripiego.get("depositi") or [],
                      key=lambda d: (d.get("report_date") or "", d.get("filed") or "")):
        facts, c = unisci_fatti(facts, dep.get("facts") or {}, fonte_base="SEC companyfacts" if facts is facts_cf
                                else "SEC companyfacts o deposito iXBRL precedente",
                                fonte_aggiunta=dep["fonte"], concetti=_concetti_canonici())
        rideterminati.extend(c)
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
    da_ixbrl: Dict[str, List[int]] = {}
    if facts is not facts_cf:
        for canon, (tg, ti) in CANONICAL.items():
            ser_cf = _annual_series(facts_cf, tg, ti)[0] or {}
            anni = sorted(y for y in out_items.get(canon, {}) if y not in ser_cf)
            if anni:
                da_ixbrl[canon] = anni
    rideterminazioni, discontinuita = _voci_rideterminate(rideterminati, out_items, tags_used, unit_used)
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
    trimestri = quarterly_history(facts, fino_al=fino_al, oggi=oggi)   # cutoff della run: mai un deposito successivo

    fonte_testa = etichetta_ripiego(da_ixbrl, rideterminazioni, ripiego)
    out = {"ticker": ticker.upper(), "cik": cik,
            "quarters": trimestri["quarters"], "quarter_ends": trimestri["quarter_ends"], "q4": trimestri["q4"],
            "latest_period": trimestri["latest_period"],
            "entity": data.get("entityName"),
            "years": all_years, "n_items": len(out_items),
            "items": out_items, "derived": derived,
            "tags_used": tags_used, "units": unit_used,
            # la fonte del ripiego sta IN TESTA (riserva MEDIO-2 v2: in coda il tetto del tool la tagliava)
            "_source": ((fonte_testa + " + ") if fonte_testa else "")
                       + "SEC XBRL companyfacts (10-K/20-F/40-F fp=FY; trimestri dai 10-Q fp=Q1-Q3)",
            # RIPIEGO iXBRL dichiarato: stato, depositi letti, anni per voce, comparativi rideterminati
            "ripiego_ixbrl": {"stato": ripiego["stato"], "motivo": ripiego.get("motivo"),
                              "origine": ORIGINE_IXBRL,
                              "depositi": [{k: d.get(k) for k in ("accn", "form", "filed", "report_date", "url",
                                                                  "sha256", "fonte", "convertito_il", "conversione",
                                                                  "altre_entita", "incoerenti",
                                                                  "coerenti_per_arrotondamento", "documento_letto")}
                                           for d in ripiego["depositi"]],
                              "anni_per_voce": da_ixbrl, "rideterminazioni": rideterminazioni},
            "_timestamp": datetime.now().isoformat(timespec="seconds"),
            # REV_G2a R-4: eta' del dato (cache fino a 7 giorni) e cache nota come superata
            "da_cache": lettura.get("da_cache"), "companyfacts_letto_il": lettura.get("letto_il"),
            **({"index_note": "companyfacts in cache anteriore a un deposito gia' pubblicato ("
                              + str(superata.get("motivo")) + "): possibili dati recenti mancanti"}
               if (superata := cache_superata(cik)) and lettura.get("da_cache") else {})}
    if rideterminazioni:
        out["rideterminazioni"] = rideterminazioni  # stessa chiave dello storico ESEF
    if discontinuita:
        out["discontinuita"] = discontinuita
    return out


def _voci_rideterminate(rideterminati, out_items, tags_used, unit_used):
    """(rideterminazioni per voce, discontinuita') dai comparativi sostituiti da unisci_fatti.

    Una riga conta per una voce canonica solo se il suo concetto e' fra i tag USATI dalla voce, l'unita' e'
    quella della voce e il valore in `items` e' davvero quello del deposito piu' recente. Discontinuita':
    l'anno precedente al primo rideterminato resta sulla base vecchia (il deposito recente ridetermina solo i
    suoi comparativi): la variazione fra i due anni NON e' omogenea e si dichiara."""
    rideterminazioni, discontinuita = [], {}
    for r in rideterminati:
        tax, _, nome = str(r.get("concetto") or "").partition(":")
        anno = int(str(r.get("fine") or "0")[:4] or 0)
        for canon, usato in tags_used.items():
            if not usato or not usato.startswith(tax + ":") or nome not in usato.split(":", 1)[1].split("+"):
                continue
            if unit_used.get(canon) != r.get("unita") or out_items.get(canon, {}).get(anno) != r.get("valore_usato"):
                continue
            rideterminazioni.append({"voce": canon, "anno": anno, "concetto": r["concetto"],
                                     "valore_precedente": r["valore_precedente"],
                                     "fonte_precedente": r["fonte_precedente"], "valore_usato": r["valore_usato"],
                                     "fonte_usata": r["fonte_usata"], "nota": r["nota"]})
    for canon in sorted({x["voce"] for x in rideterminazioni}):
        anni = sorted({x["anno"] for x in rideterminazioni if x["voce"] == canon})
        prec = anni[0] - 1
        if prec in out_items.get(canon, {}):
            discontinuita[canon] = {
                "tra": [prec, anni[0]],
                "motivo": (f"FY{'/'.join(str(a) for a in anni)} rideterminato nel deposito piu' recente; FY{prec} e "
                           "precedenti restano sulla base del deposito originale: variazione "
                           f"FY{prec}->FY{anni[0]} non omogenea")}
    return rideterminazioni, discontinuita


def etichetta_ripiego(da_ixbrl, rideterminazioni, ripiego) -> Optional[str]:
    """Fonte del ripiego iXBRL da mettere IN TESTA a `_source` e alla ricevuta del tool, None se non usato."""
    if not da_ixbrl and not rideterminazioni:
        return None
    usati = sorted({x["fonte_usata"] for x in rideterminazioni}
                   | ({d["fonte"] for d in (ripiego or {}).get("depositi") or []} if da_ixbrl else set()))
    anni = sorted({y for a in (da_ixbrl or {}).values() for y in a})
    parti = []
    if anni:
        parti.append("FY" + ", FY".join(str(y) for y in anni) + " (companyfacts non ancora aggiornato)")
    if rideterminazioni:
        parti.append(f"{len(rideterminazioni)} comparativi rideterminati (vale il deposito piu' recente)")
    return " + ".join(usati) + ": " + "; ".join(parti)


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
