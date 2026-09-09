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


def _fetch_companyfacts(cik: str) -> Optional[Dict[str, Any]]:
    """companyfacts con cache disco 7gg (file pesante, gentilezza verso la SEC)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = _cache_path(cik)
    try:
        if os.path.exists(cp) and (time.time() - os.path.getmtime(cp)) < CACHE_TTL_S:
            with open(cp, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    try:
        import requests
        from bellomberg.market_data.sec_edgar import _headers   # 02/09 (B2): HEADERS non esiste piu; contatto letto a chiamata
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        r = requests.get(url, headers=_headers(), timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        try:
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
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


def get_financial_history(ticker: str, years: int = 10) -> Dict[str, Any]:
    """Storico annuale riga-per-riga dai filing SEC (10-K/20-F). ~30 voci canoniche + derivate."""
    try:
        from bellomberg.market_data.sec_edgar import lookup_cik
        cik = lookup_cik(ticker)
    except Exception as e:
        return {"error": f"lookup CIK: {type(e).__name__}: {e}"}
    if not cik:
        return {"error": f"{ticker}: nessun CIK SEC (societa' senza filing US/ADR). "
                         "Per i nomi EU senza ADR usare get_fundamentals (yfinance, 4 anni)."}
    data = _fetch_companyfacts(cik)
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

    return {"ticker": ticker.upper(), "cik": cik,
            "entity": data.get("entityName"),
            "years": all_years, "n_items": len(out_items),
            "items": out_items, "derived": derived,
            "tags_used": tags_used, "units": unit_used,
            "_source": "SEC XBRL companyfacts (10-K/20-F/40-F, fp=FY)",
            "_timestamp": datetime.now().isoformat(timespec="seconds")}


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
