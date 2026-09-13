"""
DCF Model generator stile JPM equity research.

Genera un file .xlsx con 6 fogli:
1. Cover - ticker, fair value, current price, upside, recommendation
2. Income Statement - 5y hist + 5y projection
3. FCF Bridge - EBITDA -> FCF
4. DCF - FCF projected + Terminal Value + WACC + PV -> Fair Value
5. Sensitivity - matrice WACC x perpetual growth
6. Multiples Cross-Check - EV/EBITDA, P/E, EV/Sales vs peer comparables

Uso:
    from dcf_modeler import build_dcf_excel
    path = build_dcf_excel("NEM", assumptions={"wacc": 0.09, "g_perpetual": 0.025})
"""
from bellomberg.core.language import scoped_language, text as _lt
from bellomberg.reporting.i18n_excel import label as _xt
from bellomberg.reporting.i18n import date_label
import os
from datetime import datetime

from bellomberg.core.paths import MODELS_DIR as _MODELS_DIR

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import ColorScaleRule
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


MODELS_DIR = str(_MODELS_DIR)


# ============================================================
# STYLING (JPM equity research look)
# ============================================================

JPM_NAVY = "1A3A6E"
JPM_GOLD = "B8860B"
JPM_LIGHT_BG = "F0F4F8"
JPM_HEADER_BG = "1A3A6E"
JPM_SUBHEADER_BG = "D5DCE4"
JPM_PROJ_BG = "FFF8E7"  # giallino per anni proiettati

HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
SECTION_FONT = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
NORMAL_FONT = Font(name="Calibri", size=10)
BOLD_FONT = Font(name="Calibri", size=10, bold=True)
TITLE_FONT = Font(name="Calibri", size=18, bold=True, color="1A3A6E")
SUBTITLE_FONT = Font(name="Calibri", size=11, italic=True, color="555555")

HEADER_FILL = PatternFill("solid", fgColor=JPM_HEADER_BG)
SUBHEADER_FILL = PatternFill("solid", fgColor=JPM_SUBHEADER_BG)
PROJ_FILL = PatternFill("solid", fgColor=JPM_PROJ_BG)
INPUT_FILL = PatternFill("solid", fgColor="FFF59D")  # giallo input modificabili

THIN_BORDER = Border(left=Side(style="thin", color="999999"),
                      right=Side(style="thin", color="999999"),
                      top=Side(style="thin", color="999999"),
                      bottom=Side(style="thin", color="999999"))
THICK_BOTTOM = Border(bottom=Side(style="medium", color="1A3A6E"))


def _set_col_widths(ws, widths):
    """widths = dict {col_letter: width}"""
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _header_row(ws, row, headers, fill=HEADER_FILL):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = HEADER_FONT
        c.fill = fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = THIN_BORDER


def _label_row(ws, row, label):
    c = ws.cell(row=row, column=1, value=label)
    c.font = BOLD_FONT
    c.alignment = Alignment(horizontal="left", indent=1)


# ============================================================
# DATA FETCH
# ============================================================

def _fetch_fundamentals(ticker):
    """Recupera storici e proiezioni base da yfinance. Ritorna dict."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        # Income statement annual (last 4y), balance sheet, cash flow
        income = tk.financials  # DataFrame
        balance = tk.balance_sheet
        cashflow = tk.cashflow

        # Estrai metriche chiave
        data = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "currency": info.get("currency", "USD"),
            "current_price": info.get("regularMarketPrice") or info.get("currentPrice"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "trailing_eps": info.get("trailingEps"),
            "forward_eps": info.get("forwardEps"),
            "revenue_ttm": info.get("totalRevenue"),
            "gross_margin": info.get("grossMargins"),
            "ebitda_margin": info.get("ebitdaMargins"),
            "op_margin": info.get("operatingMargins"),
            "net_margin": info.get("profitMargins"),
            "roe": info.get("returnOnEquity"),
            "roa": info.get("returnOnAssets"),
            "debt_to_equity": info.get("debtToEquity"),
            "total_cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),
            "beta": info.get("beta", 1.0),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "target_low": info.get("targetLowPrice"),
            "n_analysts": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey", "N/A"),
            "free_cashflow": info.get("freeCashflow"),
            "rev_growth_yoy": info.get("revenueGrowth"),
            "earnings_growth_yoy": info.get("earningsGrowth"),
        }

        # Storia 5y di revenue (se disponibile)
        try:
            if income is not None and not income.empty and "Total Revenue" in income.index:
                rev_series = income.loc["Total Revenue"].dropna().sort_index()
                data["historical_revenue"] = {str(d)[:4]: float(v) for d, v in rev_series.items()}
        except Exception:
            data["historical_revenue"] = {}

        try:
            if income is not None and not income.empty and "Net Income" in income.index:
                ni_series = income.loc["Net Income"].dropna().sort_index()
                data["historical_net_income"] = {str(d)[:4]: float(v) for d, v in ni_series.items()}
        except Exception:
            data["historical_net_income"] = {}

        return data
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


# ============================================================
# SHEET BUILDERS
# ============================================================

def _build_cover_sheet(wb, data, assumptions, dcf_result):
    """Foglio 1 - Cover: recommendation summary."""
    ws = wb.create_sheet("Cover", 0)
    _set_col_widths(ws, {"A": 28, "B": 22, "C": 22, "D": 22})

    ws.cell(row=1, column=1, value=_xt("EQUITY RESEARCH | DCF VALUATION")).font = TITLE_FONT
    ws.cell(row=2, column=1, value=data.get("name", data["ticker"])
            + " (" + data["ticker"] + ")").font = Font(name="Calibri", size=14, bold=True, color="1A3A6E")
    ws.cell(row=3, column=1, value=data.get("sector", "") + " / " + data.get("industry", "")).font = SUBTITLE_FONT
    ws.cell(row=4, column=1, value=_xt("Generated ") + date_label() + datetime.now().strftime(", %H:%M")).font = SUBTITLE_FONT

    # Box recommendation
    ws.cell(row=6, column=1, value=_xt("VALUATION SUMMARY")).font = SECTION_FONT
    ws.cell(row=6, column=1).fill = HEADER_FILL

    rows = [
        (_xt("Current Price"), data.get("current_price"), "${:.2f}".format(data.get("current_price") or 0)),
        (_xt("DCF Fair Value"), dcf_result.get("fair_value_per_share"),
         "${:.2f}".format(dcf_result.get("fair_value_per_share") or 0)),
        (_xt("Upside / Downside"), dcf_result.get("upside_pct"),
         "{:+.1f}%".format(dcf_result.get("upside_pct") or 0)),
        (_xt("Analyst Mean Target"), data.get("target_mean"),
         "${:.2f}".format(data.get("target_mean") or 0) if data.get("target_mean") else "N/A"),
        (_xt("Recommendation (Street)"), None, str(data.get("recommendation", "N/A")).upper()),
        (_xt("# Analysts Covering"), data.get("n_analysts"), str(data.get("n_analysts") or "N/A")),
    ]
    for i, (lbl, _val, disp) in enumerate(rows, start=7):
        ws.cell(row=i, column=1, value=lbl).font = BOLD_FONT
        c = ws.cell(row=i, column=2, value=disp)
        c.alignment = Alignment(horizontal="right")
        if _xt("Upside") in lbl and dcf_result.get("upside_pct") is not None:
            color = "00C853" if dcf_result["upside_pct"] > 0 else "D32F2F"
            c.font = Font(name="Calibri", size=11, bold=True, color=color)

    # Assumptions sintesi
    ws.cell(row=15, column=1, value=_xt("KEY ASSUMPTIONS")).font = SECTION_FONT
    ws.cell(row=15, column=1).fill = HEADER_FILL
    a_rows = [
        ("WACC", "{:.2%}".format(assumptions["wacc"])),
        (_xt("Perpetual Growth Rate"), "{:.2%}".format(assumptions["g_perpetual"])),
        (_xt("Projection Horizon"), _xt("{} years").format(assumptions["horizon_years"])),
        ("Beta", "{:.2f}".format(data.get("beta") or 1.0)),
        (_xt("Risk-Free Rate (10Y T)"), "{:.2%}".format(assumptions["risk_free"])),
        (_xt("Equity Risk Premium"), "{:.2%}".format(assumptions["erp"])),
    ]
    for i, (lbl, val) in enumerate(a_rows, start=16):
        ws.cell(row=i, column=1, value=lbl).font = BOLD_FONT
        ws.cell(row=i, column=2, value=val).alignment = Alignment(horizontal="right")

    # Quick fundamentals
    ws.cell(row=24, column=1, value=_xt("SNAPSHOT FUNDAMENTALS (TTM)")).font = SECTION_FONT
    ws.cell(row=24, column=1).fill = HEADER_FILL
    f_rows = [
        (_xt("Revenue TTM"), "${:,.0f}M".format((data.get("revenue_ttm") or 0)/1e6)),
        ("Trailing PE", "{:.1f}x".format(data.get("trailing_pe")) if data.get("trailing_pe") else "N/A"),
        ("Forward PE", "{:.1f}x".format(data.get("forward_pe")) if data.get("forward_pe") else "N/A"),
        (_xt("EBITDA Margin"), "{:.1%}".format(data.get("ebitda_margin") or 0)),
        (_xt("Operating Margin"), "{:.1%}".format(data.get("op_margin") or 0)),
        (_xt("Net Margin"), "{:.1%}".format(data.get("net_margin") or 0)),
        ("ROE", "{:.1%}".format(data.get("roe") or 0)),
        (_xt("Debt / Equity"), "{:.2f}".format((data.get("debt_to_equity") or 0)/100)),
        ("FCF (TTM)", "${:,.0f}M".format((data.get("free_cashflow") or 0)/1e6)),
        (_xt("Market Cap"), "${:,.0f}M".format((data.get("market_cap") or 0)/1e6)),
        (_xt("Enterprise Value"), "${:,.0f}M".format((data.get("enterprise_value") or 0)/1e6)),
    ]
    for i, (lbl, val) in enumerate(f_rows, start=25):
        ws.cell(row=i, column=1, value=lbl).font = NORMAL_FONT
        ws.cell(row=i, column=2, value=val).alignment = Alignment(horizontal="right")

    ws.cell(row=37, column=1, value=_xt("DISCLAIMER: Model generated automatically. Assumptions in yellow cells are editable. WACC and perpetual growth on Sensitivity tab drive DCF valuation.")).font = Font(size=8, italic=True, color="888888")


def _build_income_statement(wb, data, assumptions):
    """Foglio 2 - Income Statement: 5y hist + 5y projection."""
    ws = wb.create_sheet("Income Statement")
    _set_col_widths(ws, {"A": 28, "B": 12, "C": 12, "D": 12, "E": 12, "F": 12, "G": 12, "H": 12, "I": 12, "J": 12, "K": 12, "L": 12})

    ws.cell(row=1, column=1, value=_xt("INCOME STATEMENT - Historical + Projected (USD millions)")).font = TITLE_FONT

    current_year = datetime.now().year
    hist_years = [current_year - 5, current_year - 4, current_year - 3, current_year - 2, current_year - 1]
    proj_years = list(range(current_year, current_year + assumptions["horizon_years"]))
    all_years = hist_years + proj_years

    # Header
    headers = [_xt("Line Item ($M)")] + [str(y) + ("E" if y in proj_years else "A") for y in all_years]
    _header_row(ws, 3, headers)

    # Highlight years projected
    for i, y in enumerate(all_years, start=2):
        if y in proj_years:
            ws.cell(row=3, column=i).fill = PROJ_FILL
            ws.cell(row=3, column=i).font = Font(name="Calibri", size=11, bold=True, color="000000")

    # Revenue history
    hist_rev = data.get("historical_revenue", {})
    # Crea sequenza revenue history per i hist_years (in millions)
    rev_row = []
    last_rev = None
    for y in hist_years:
        v = hist_rev.get(str(y))
        if v:
            rev_row.append(v / 1e6)
            last_rev = v
        else:
            rev_row.append(None)
    if last_rev is None and data.get("revenue_ttm"):
        # fallback: usa TTM come ultimo hist year
        last_rev = data["revenue_ttm"]
        rev_row[-1] = last_rev / 1e6

    # Revenue proj: applica growth assumption decrescente (alta -> stabilizza a perpetual)
    growth_start = data.get("rev_growth_yoy") or 0.05
    if growth_start < 0: growth_start = 0.03
    if growth_start > 0.30: growth_start = 0.20
    g_perp = assumptions["g_perpetual"]
    # Linear taper da growth_start a perpetual
    proj_growth = []
    for i in range(assumptions["horizon_years"]):
        g = growth_start + (g_perp - growth_start) * (i + 1) / assumptions["horizon_years"]
        proj_growth.append(g)

    rev_proj = []
    prev = (last_rev or 0) / 1e6
    for g in proj_growth:
        prev = prev * (1 + g)
        rev_proj.append(prev)

    all_revs = rev_row + rev_proj

    # Riga 4: Revenue
    _label_row(ws, 4, _xt("Revenue"))
    for i, v in enumerate(all_revs, start=2):
        c = ws.cell(row=4, column=i, value=(round(v, 1) if v else None))
        c.number_format = "#,##0.0"
        if i - 2 >= len(hist_years):
            c.fill = PROJ_FILL

    # Riga 5: % growth
    _label_row(ws, 5, _xt("% YoY Growth"))
    for i in range(2, len(all_revs) + 2):
        if i == 2:
            continue
        c = ws.cell(row=5, column=i,
                    value="=IF({prev}=0,\"\",({curr}/{prev}-1))".format(
                        curr=ws.cell(row=4, column=i).coordinate,
                        prev=ws.cell(row=4, column=i-1).coordinate))
        c.number_format = "0.0%"
        if i - 2 >= len(hist_years):
            c.fill = PROJ_FILL

    # Margini (uso ttm come baseline e stabilizzo)
    gm = data.get("gross_margin") or 0.35
    em = data.get("ebitda_margin") or 0.20
    om = data.get("op_margin") or 0.15
    nm = data.get("net_margin") or 0.10

    # Riga 7: Gross Profit
    _label_row(ws, 7, _xt("Gross Profit"))
    ws.cell(row=8, column=1, value=_xt("  Gross Margin %")).font = NORMAL_FONT
    for i in range(2, len(all_revs) + 2):
        rev_ref = ws.cell(row=4, column=i).coordinate
        gm_ref = ws.cell(row=8, column=i).coordinate
        # Margin: hist usa medie, proj usa stesso valore base
        c_gm = ws.cell(row=8, column=i, value=gm)
        c_gm.number_format = "0.0%"
        c_gm.fill = INPUT_FILL
        c_gp = ws.cell(row=7, column=i, value="={}*{}".format(rev_ref, gm_ref))
        c_gp.number_format = "#,##0.0"
        if i - 2 >= len(hist_years):
            c_gp.fill = PROJ_FILL

    # Riga 10: EBITDA
    _label_row(ws, 10, "EBITDA")
    ws.cell(row=11, column=1, value=_xt("  EBITDA Margin %")).font = NORMAL_FONT
    for i in range(2, len(all_revs) + 2):
        rev_ref = ws.cell(row=4, column=i).coordinate
        em_ref = ws.cell(row=11, column=i).coordinate
        c_em = ws.cell(row=11, column=i, value=em)
        c_em.number_format = "0.0%"; c_em.fill = INPUT_FILL
        c_eb = ws.cell(row=10, column=i, value="={}*{}".format(rev_ref, em_ref))
        c_eb.number_format = "#,##0.0"
        if i - 2 >= len(hist_years):
            c_eb.fill = PROJ_FILL

    # Riga 13: Operating Income (EBIT)
    _label_row(ws, 13, "EBIT")
    ws.cell(row=14, column=1, value=_xt("  EBIT Margin %")).font = NORMAL_FONT
    for i in range(2, len(all_revs) + 2):
        rev_ref = ws.cell(row=4, column=i).coordinate
        om_ref = ws.cell(row=14, column=i).coordinate
        c_om = ws.cell(row=14, column=i, value=om)
        c_om.number_format = "0.0%"; c_om.fill = INPUT_FILL
        c_ebit = ws.cell(row=13, column=i, value="={}*{}".format(rev_ref, om_ref))
        c_ebit.number_format = "#,##0.0"
        if i - 2 >= len(hist_years):
            c_ebit.fill = PROJ_FILL

    # Riga 16: Net Income
    _label_row(ws, 16, _xt("Net Income"))
    ws.cell(row=17, column=1, value=_xt("  Net Margin %")).font = NORMAL_FONT
    for i in range(2, len(all_revs) + 2):
        rev_ref = ws.cell(row=4, column=i).coordinate
        nm_ref = ws.cell(row=17, column=i).coordinate
        c_nm = ws.cell(row=17, column=i, value=nm)
        c_nm.number_format = "0.0%"; c_nm.fill = INPUT_FILL
        c_ni = ws.cell(row=16, column=i, value="={}*{}".format(rev_ref, nm_ref))
        c_ni.number_format = "#,##0.0"
        if i - 2 >= len(hist_years):
            c_ni.fill = PROJ_FILL

    # Footer note
    ws.cell(row=20, column=1,
            value=_xt("Note: Yellow cells (margins) are EDITABLE - modify to test scenarios. "
                  "Projected revenue uses linear-taper growth from current YoY rate to perpetual growth.")).font = Font(size=9, italic=True, color="555555")


def _build_fcf_bridge(wb, data, assumptions):
    """Foglio 3 - FCF Bridge: EBITDA -> FCF"""
    ws = wb.create_sheet("FCF Bridge")
    _set_col_widths(ws, {"A": 28, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14})

    ws.cell(row=1, column=1, value=_xt("FREE CASH FLOW BRIDGE - Projected ($M)")).font = TITLE_FONT

    current_year = datetime.now().year
    proj_years = list(range(current_year, current_year + assumptions["horizon_years"]))
    headers = [_xt("Line Item")] + [str(y) + "E" for y in proj_years]
    _header_row(ws, 3, headers)

    # Link EBITDA da Income Statement
    rows_def = [
        (_xt("EBITDA (from IS)"), "=", "'Income Statement'!{col}10"),
        (_xt("- D&A (% of revenue)"), "input", 0.04),
        ("EBIT", "calc", "=EBITDA - D&A"),
        (_xt("- Taxes (% of EBIT)"), "input", 0.25),
        ("NOPAT", "calc", "=EBIT * (1-tax)"),
        (_xt("+ D&A (add back)"), "link", "= D&A"),
        (_xt("- CapEx (% of revenue)"), "input", 0.06),
        (_xt("- Change in Working Capital (% of revenue)"), "input", 0.02),
        (_xt("Free Cash Flow"), "calc", "FCF"),
    ]

    # Riga EBITDA - link da IS!
    # Le colonne di IS corrispondenti agli anni proj sono: hist_years(5) + col B is hist_years[0] => col 7 e' first proj year (current_year)
    # IS row 10 = EBITDA. Col 7 = first proj
    ebitda_is_cols = []
    for i in range(assumptions["horizon_years"]):
        is_col = get_column_letter(2 + 5 + i)  # B=2, hist=5 cols, then proj
        ebitda_is_cols.append("'Income Statement'!{}10".format(is_col))

    revenue_is_cols = []
    for i in range(assumptions["horizon_years"]):
        is_col = get_column_letter(2 + 5 + i)
        revenue_is_cols.append("'Income Statement'!{}4".format(is_col))

    # EBITDA row
    _label_row(ws, 4, "EBITDA")
    for i, ref in enumerate(ebitda_is_cols, start=2):
        c = ws.cell(row=4, column=i, value="=" + ref); c.number_format = "#,##0.0"; c.fill = PROJ_FILL

    # D&A as % of revenue
    _label_row(ws, 5, _xt("(-) D&A (% rev)"))
    ws.cell(row=5, column=1).font = BOLD_FONT
    for i, ref in enumerate(revenue_is_cols, start=2):
        c = ws.cell(row=5, column=i, value=0.04); c.number_format = "0.0%"; c.fill = INPUT_FILL

    _label_row(ws, 6, "D&A")
    for i in range(2, len(revenue_is_cols)+2):
        rev = revenue_is_cols[i-2]
        c = ws.cell(row=6, column=i, value="=" + rev + "*" + ws.cell(row=5, column=i).coordinate)
        c.number_format = "#,##0.0"

    _label_row(ws, 7, "EBIT")
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=7, column=i, value="={}-{}".format(ws.cell(row=4, column=i).coordinate, ws.cell(row=6, column=i).coordinate))
        c.number_format = "#,##0.0"

    _label_row(ws, 8, _xt("Tax Rate"))
    ws.cell(row=8, column=1).font = BOLD_FONT
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=8, column=i, value=0.25); c.number_format = "0.0%"; c.fill = INPUT_FILL

    _label_row(ws, 9, "NOPAT (EBIT * (1-tax))")
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=9, column=i, value="={}*(1-{})".format(ws.cell(row=7, column=i).coordinate, ws.cell(row=8, column=i).coordinate))
        c.number_format = "#,##0.0"

    _label_row(ws, 10, _xt("(+) D&A (add back)"))
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=10, column=i, value="=" + ws.cell(row=6, column=i).coordinate)
        c.number_format = "#,##0.0"

    _label_row(ws, 11, _xt("(-) CapEx (% rev)"))
    ws.cell(row=11, column=1).font = BOLD_FONT
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=11, column=i, value=0.06); c.number_format = "0.0%"; c.fill = INPUT_FILL

    _label_row(ws, 12, "CapEx")
    for i in range(2, len(revenue_is_cols)+2):
        rev = revenue_is_cols[i-2]
        c = ws.cell(row=12, column=i, value="=" + rev + "*" + ws.cell(row=11, column=i).coordinate)
        c.number_format = "#,##0.0"

    _label_row(ws, 13, _xt("(-) Change in NWC (% rev)"))
    ws.cell(row=13, column=1).font = BOLD_FONT
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=13, column=i, value=0.02); c.number_format = "0.0%"; c.fill = INPUT_FILL

    _label_row(ws, 14, _xt("Change in NWC"))
    for i in range(2, len(revenue_is_cols)+2):
        rev = revenue_is_cols[i-2]
        c = ws.cell(row=14, column=i, value="=" + rev + "*" + ws.cell(row=13, column=i).coordinate)
        c.number_format = "#,##0.0"

    # FCF
    _label_row(ws, 16, _xt("FREE CASH FLOW"))
    ws.cell(row=16, column=1).font = Font(name="Calibri", size=11, bold=True, color="1A3A6E")
    for i in range(2, len(revenue_is_cols)+2):
        c = ws.cell(row=16, column=i, value="={}+{}-{}-{}".format(
            ws.cell(row=9, column=i).coordinate,
            ws.cell(row=10, column=i).coordinate,
            ws.cell(row=12, column=i).coordinate,
            ws.cell(row=14, column=i).coordinate))
        c.number_format = "#,##0.0"
        c.font = BOLD_FONT
        c.fill = PatternFill("solid", fgColor="FFE082")


def _build_dcf(wb, data, assumptions):
    """Foglio 4 - DCF: PV FCF + Terminal -> Fair Value."""
    ws = wb.create_sheet("DCF")
    _set_col_widths(ws, {"A": 32, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 14})

    ws.cell(row=1, column=1, value=_xt("DCF VALUATION")).font = TITLE_FONT

    current_year = datetime.now().year
    proj_years = list(range(current_year, current_year + assumptions["horizon_years"]))

    # WACC input
    ws.cell(row=3, column=1, value=_xt("WACC INPUTS")).font = SECTION_FONT
    ws.cell(row=3, column=1).fill = HEADER_FILL
    rows_inputs = [
        (_xt("Risk-Free Rate (10Y T)"), assumptions["risk_free"], "0.00%"),
        (_xt("Equity Risk Premium"), assumptions["erp"], "0.00%"),
        ("Beta", data.get("beta") or 1.0, "0.00"),
        (_xt("Cost of Equity (Ke)"), "={}+{}*{}".format("B4","B5","B6"), "0.00%"),
        (_xt("Cost of Debt (after tax)"), 0.04, "0.00%"),
        (_xt("Debt Weight"), 0.30, "0.00%"),
        (_xt("Equity Weight"), 0.70, "0.00%"),
        ("WACC", "=B10*B7+B9*B8", "0.00%"),
        (_xt("Perpetual Growth"), assumptions["g_perpetual"], "0.00%"),
    ]
    for i, (lbl, val, fmt) in enumerate(rows_inputs, start=4):
        ws.cell(row=i, column=1, value=lbl).font = BOLD_FONT
        c = ws.cell(row=i, column=2, value=val)
        c.number_format = fmt
        if isinstance(val, (int, float)):
            c.fill = INPUT_FILL

    # Periodi DCF
    ws.cell(row=15, column=1, value=_xt("DCF CALCULATION")).font = SECTION_FONT
    ws.cell(row=15, column=1).fill = HEADER_FILL

    headers = [_xt("Year")] + [str(y) + "E" for y in proj_years]
    _header_row(ws, 16, headers)

    # FCF link da FCF Bridge
    _label_row(ws, 17, _xt("FCF (from FCF Bridge)"))
    for i, _y in enumerate(proj_years, start=2):
        fcf_col = get_column_letter(i)
        c = ws.cell(row=17, column=i, value="='FCF Bridge'!{}16".format(fcf_col))
        c.number_format = "#,##0.0"

    # Discount factor
    _label_row(ws, 18, _xt("Discount Factor"))
    for i, _y in enumerate(proj_years, start=2):
        period = i - 1
        c = ws.cell(row=18, column=i, value="=1/(1+$B$11)^{}".format(period))
        c.number_format = "0.0000"

    # PV FCF
    _label_row(ws, 19, _xt("PV of FCF"))
    for i in range(2, len(proj_years)+2):
        c = ws.cell(row=19, column=i, value="={}*{}".format(
            ws.cell(row=17, column=i).coordinate,
            ws.cell(row=18, column=i).coordinate))
        c.number_format = "#,##0.0"
        c.font = BOLD_FONT

    # Sum of PV
    last_proj_col = get_column_letter(1 + len(proj_years))
    ws.cell(row=21, column=1, value=_xt("Sum of PV (FCF)")).font = BOLD_FONT
    ws.cell(row=21, column=2, value="=SUM(B19:{}19)".format(last_proj_col)).number_format = "#,##0.0"

    # Terminal Value
    ws.cell(row=22, column=1, value=_xt("Terminal Value = FCFn * (1+g) / (WACC-g)")).font = BOLD_FONT
    last_fcf = ws.cell(row=17, column=1+len(proj_years)).coordinate
    ws.cell(row=22, column=2, value="={}*(1+$B$12)/($B$11-$B$12)".format(last_fcf)).number_format = "#,##0.0"

    ws.cell(row=23, column=1, value=_xt("PV of Terminal Value")).font = BOLD_FONT
    ws.cell(row=23, column=2, value="=B22*{}".format(ws.cell(row=18, column=1+len(proj_years)).coordinate)).number_format = "#,##0.0"

    # Enterprise Value -> Equity -> Fair Value/share
    ws.cell(row=25, column=1, value=_xt("Enterprise Value")).font = SECTION_FONT
    ws.cell(row=25, column=1).fill = HEADER_FILL
    ws.cell(row=25, column=2, value="=B21+B23").number_format = "#,##0.0"
    ws.cell(row=25, column=2).font = HEADER_FONT
    ws.cell(row=25, column=2).fill = HEADER_FILL

    ws.cell(row=26, column=1, value=_xt("(-) Net Debt ($M)")).font = BOLD_FONT
    net_debt = ((data.get("total_debt") or 0) - (data.get("total_cash") or 0)) / 1e6
    ws.cell(row=26, column=2, value=net_debt).number_format = "#,##0.0"
    ws.cell(row=26, column=2).fill = INPUT_FILL

    ws.cell(row=27, column=1, value=_xt("Equity Value")).font = BOLD_FONT
    ws.cell(row=27, column=2, value="=B25-B26").number_format = "#,##0.0"

    ws.cell(row=28, column=1, value=_xt("Shares Outstanding (M)")).font = BOLD_FONT
    shares_m = (data.get("shares_outstanding") or 0) / 1e6
    ws.cell(row=28, column=2, value=shares_m).number_format = "#,##0.0"
    ws.cell(row=28, column=2).fill = INPUT_FILL

    ws.cell(row=29, column=1, value=_xt("FAIR VALUE PER SHARE")).font = Font(name="Calibri", size=12, bold=True, color="1A3A6E")
    c_fv = ws.cell(row=29, column=2, value="=B27/B28")
    c_fv.number_format = "$#,##0.00"
    c_fv.font = Font(name="Calibri", size=14, bold=True, color="00C853")
    c_fv.fill = PatternFill("solid", fgColor="E8F5E9")

    ws.cell(row=30, column=1, value=_xt("Current Price")).font = BOLD_FONT
    ws.cell(row=30, column=2, value=data.get("current_price") or 0).number_format = "$#,##0.00"

    ws.cell(row=31, column=1, value=_xt("Upside / Downside %")).font = BOLD_FONT
    c_up = ws.cell(row=31, column=2, value="=(B29-B30)/B30")
    c_up.number_format = "0.0%"
    c_up.font = Font(name="Calibri", size=12, bold=True)

    # Calcola fair value statico per Cover
    try:
        # Stima approssimativa per il Cover
        wacc = assumptions["wacc"]
        g = assumptions["g_perpetual"]
        rev_growth = data.get("rev_growth_yoy") or 0.05
        if rev_growth < 0: rev_growth = 0.03
        if rev_growth > 0.30: rev_growth = 0.20
        last_rev = data.get("revenue_ttm") or 0
        em = data.get("ebitda_margin") or 0.20
        tax = 0.25
        capex_pct = 0.06
        nwc_pct = 0.02
        da_pct = 0.04
        sum_pv = 0
        prev_rev = last_rev
        last_fcf_val = 0
        for i in range(assumptions["horizon_years"]):
            g_step = rev_growth + (g - rev_growth) * (i + 1) / assumptions["horizon_years"]
            prev_rev = prev_rev * (1 + g_step)
            ebitda = prev_rev * em
            da = prev_rev * da_pct
            ebit = ebitda - da
            nopat = ebit * (1 - tax)
            fcf = nopat + da - prev_rev * capex_pct - prev_rev * nwc_pct
            disc = (1 + wacc) ** (i + 1)
            sum_pv += fcf / disc
            last_fcf_val = fcf
        tv = (last_fcf_val * (1 + g)) / (wacc - g)
        pv_tv = tv / ((1 + wacc) ** assumptions["horizon_years"])
        ev = sum_pv + pv_tv
        net_debt_total = (data.get("total_debt") or 0) - (data.get("total_cash") or 0)
        equity = ev - net_debt_total
        shares = data.get("shares_outstanding") or 1
        fair_value = equity / shares
        current = data.get("current_price") or 0
        upside = (fair_value / current - 1) * 100 if current else None
        return {"fair_value_per_share": fair_value, "upside_pct": upside, "ev": ev, "equity": equity}
    except Exception:
        return {"fair_value_per_share": None, "upside_pct": None}


def _build_sensitivity(wb, data, assumptions):
    """Foglio 5 - Sensitivity Table WACC x Perpetual Growth."""
    ws = wb.create_sheet("Sensitivity")
    _set_col_widths(ws, {"A": 22, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 14, "H": 14})

    ws.cell(row=1, column=1, value=_xt("SENSITIVITY ANALYSIS - Fair Value per Share")).font = TITLE_FONT
    ws.cell(row=2, column=1, value=_xt("Matrix: WACC (rows) x Perpetual Growth (columns)")).font = SUBTITLE_FONT

    wacc_values = [0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13]
    g_values = [0.01, 0.02, 0.025, 0.03, 0.035, 0.04]

    # Header row (growth %)
    ws.cell(row=4, column=1, value="WACC \\ g").font = BOLD_FONT
    ws.cell(row=4, column=1).fill = HEADER_FILL
    ws.cell(row=4, column=1).font = HEADER_FONT
    for i, g in enumerate(g_values, start=2):
        c = ws.cell(row=4, column=i, value=g)
        c.number_format = "0.0%"
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="center")

    # Compute matrix
    last_rev = data.get("revenue_ttm") or 1e9
    rev_growth = data.get("rev_growth_yoy") or 0.05
    if rev_growth < 0: rev_growth = 0.03
    if rev_growth > 0.30: rev_growth = 0.20
    em = data.get("ebitda_margin") or 0.20
    horizon = assumptions["horizon_years"]
    tax = 0.25
    capex_pct = 0.06
    nwc_pct = 0.02
    da_pct = 0.04
    net_debt = (data.get("total_debt") or 0) - (data.get("total_cash") or 0)
    shares = data.get("shares_outstanding") or 1

    for i, wacc in enumerate(wacc_values, start=5):
        c_label = ws.cell(row=i, column=1, value=wacc)
        c_label.number_format = "0.0%"
        c_label.fill = HEADER_FILL
        c_label.font = HEADER_FONT
        c_label.alignment = Alignment(horizontal="center")

        for j, g in enumerate(g_values, start=2):
            if wacc <= g:
                ws.cell(row=i, column=j, value="N/A").alignment = Alignment(horizontal="center")
                continue
            try:
                sum_pv = 0
                prev_rev = last_rev
                last_fcf_val = 0
                for k in range(horizon):
                    g_step = rev_growth + (g - rev_growth) * (k + 1) / horizon
                    prev_rev = prev_rev * (1 + g_step)
                    ebitda = prev_rev * em
                    da = prev_rev * da_pct
                    ebit = ebitda - da
                    nopat = ebit * (1 - tax)
                    fcf = nopat + da - prev_rev * capex_pct - prev_rev * nwc_pct
                    disc = (1 + wacc) ** (k + 1)
                    sum_pv += fcf / disc
                    last_fcf_val = fcf
                tv = (last_fcf_val * (1 + g)) / (wacc - g)
                pv_tv = tv / ((1 + wacc) ** horizon)
                ev = sum_pv + pv_tv
                equity = ev - net_debt
                fv = equity / shares
                c = ws.cell(row=i, column=j, value=round(fv, 2))
                c.number_format = "$#,##0.00"
                c.alignment = Alignment(horizontal="center")
            except Exception:
                ws.cell(row=i, column=j, value="ERR")

    # Color scale conditional
    rng = "B5:" + get_column_letter(1 + len(g_values)) + str(4 + len(wacc_values))
    rule = ColorScaleRule(start_type="min", start_color="FFCDD2",
                          mid_type="percentile", mid_value=50, mid_color="FFF59D",
                          end_type="max", end_color="A5D6A7")
    ws.conditional_formatting.add(rng, rule)

    # Current price reference
    cp = data.get("current_price")
    if cp:
        ws.cell(row=14, column=1, value=_xt("Current Price")).font = BOLD_FONT
        ws.cell(row=14, column=2, value=cp).number_format = "$#,##0.00"
        ws.cell(row=15, column=1, value=_xt("Color: red = downside, green = upside vs current price")).font = Font(size=9, italic=True, color="555555")


def _build_multiples(wb, data, assumptions):
    """Foglio 6 - Multiples Cross-Check."""
    ws = wb.create_sheet("Multiples")
    _set_col_widths(ws, {"A": 28, "B": 18, "C": 18, "D": 18})

    ws.cell(row=1, column=1, value=_xt("MULTIPLES CROSS-CHECK")).font = TITLE_FONT
    ws.cell(row=2, column=1, value=_xt("Current company multiples vs typical sector ranges")).font = SUBTITLE_FONT

    _header_row(ws, 4, [_xt("Multiple"), _xt("Company"), _xt("Sector Typical Range"), _xt("Implied Fair Value")])

    rows = [
        # audit/11 §4: la chiave enterprise_value esiste SEMPRE (spesso None) -> il default
        # di .get non scattava mai e None/float dava TypeError: ora e' nel guard.
        ("EV / EBITDA (TTM)",
         (data["enterprise_value"] / (data["revenue_ttm"] * data["ebitda_margin"]))
         if (data.get("enterprise_value") and data.get("revenue_ttm") and data.get("ebitda_margin")) else None,
         "10-14x"),
        (_xt("EV / Sales (TTM)"),
         (data["enterprise_value"] / data["revenue_ttm"])
         if (data.get("enterprise_value") and data.get("revenue_ttm")) else None,
         "1.5-3.5x"),
        ("Trailing P/E", data.get("trailing_pe"), "15-22x"),
        ("Forward P/E", data.get("forward_pe"), "13-18x"),
        (_xt("Price / Sales"), None, "1-4x"),  # avere market_cap / revenue
        ("ROE", data.get("roe"), _xt(">15% = quality")),
    ]
    for i, (lbl, val, sect) in enumerate(rows, start=5):
        ws.cell(row=i, column=1, value=lbl).font = NORMAL_FONT
        c = ws.cell(row=i, column=2, value=val)
        if isinstance(val, (int, float)):
            if "ROE" in lbl:
                c.number_format = "0.0%"
            elif "EBITDA" in lbl or _xt("Sales") in lbl or "P/E" in lbl or _xt("Sales") in lbl:
                c.number_format = "0.0\"x\""
        c.alignment = Alignment(horizontal="right")
        ws.cell(row=i, column=3, value=sect).alignment = Alignment(horizontal="center")
        # Implied FV column placeholder
        ws.cell(row=i, column=4, value=_xt("See DCF tab"))

    ws.cell(row=13, column=1, value=_xt("Note: Multiples are TTM. Sector ranges are illustrative - verify vs actual peer set.")).font = Font(size=9, italic=True, color="555555")


# ============================================================
# MAIN ENTRY
# ============================================================

@scoped_language
def build_dcf_excel(ticker, assumptions=None, output_path=None):
    """Genera l'Excel DCF completo. Ritorna il path del file."""
    if not OPENPYXL_AVAILABLE:
        return {"error": _xt("openpyxl non installato")}
    if not YFINANCE_AVAILABLE:
        return {"error": _xt("yfinance non installato")}

    os.makedirs(MODELS_DIR, exist_ok=True)

    # Default assumptions
    default_assumptions = {
        "wacc": 0.10,
        "g_perpetual": 0.025,
        "horizon_years": 5,
        "risk_free": 0.0457,  # 10Y Treasury
        "erp": 0.055,         # equity risk premium
    }
    if assumptions:
        default_assumptions.update(assumptions)
    assumptions = default_assumptions

    print(_xt("[DCF] Fetching ") + ticker)
    data = _fetch_fundamentals(ticker)
    if not data or "error" in data:
        return {"error": _xt("Fetch failed for ") + ticker + ": " + str(data.get("error") if data else _xt("no data"))}

    if not output_path:
        output_path = os.path.join(MODELS_DIR,
                                    "DCF_" + ticker + "_" + datetime.now().strftime("%Y%m%d_%H%M") + ".xlsx")

    print(_xt("[DCF] Building Excel workbook"))
    wb = Workbook()
    # Default sheet creato in automatico - lo rimuovo
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # Build sheets nell'ordine
    dcf_result = _build_dcf(wb, data, assumptions)
    _build_cover_sheet(wb, data, assumptions, dcf_result)
    _build_income_statement(wb, data, assumptions)
    _build_fcf_bridge(wb, data, assumptions)
    _build_sensitivity(wb, data, assumptions)
    _build_multiples(wb, data, assumptions)

    # Cover deve essere prima - sposta
    wb.move_sheet("Cover", offset=-(wb.sheetnames.index("Cover")))

    wb.save(output_path)
    print(_xt("[DCF] Saved: ") + output_path)
    return {"path": output_path, "ticker": ticker,
            "fair_value": dcf_result.get("fair_value_per_share"),
            "upside_pct": dcf_result.get("upside_pct")}
