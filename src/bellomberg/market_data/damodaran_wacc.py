"""
damodaran_wacc.py - Rigore Damodaran su cost of capital e terminal value (#182).

Implementa le metodologie di Aswath Damodaran (NYU Stern) per rendere WACC e
terminal value DIFENDIBILI davanti a un comitato, non semplici stime:

  1. SYNTHETIC RATING (cost of debt): da interest coverage ratio (EBIT/interessi)
     -> rating sintetico -> default spread -> Kd = rf + default_spread.
     Molto piu' preciso di "rf + 2%" fisso.

  2. BOTTOM-UP BETA: media dei beta unlevered di un peer set, rilevered col D/E
     della societa'. Stabile, vs il beta storico (regressione rumorosa).

  3. TERMINAL VALUE DISCIPLINATO (regole d'oro Damodaran):
     - g (crescita perpetua) <= risk-free rate (non puoi crescere all'infinito > economia)
     - reinvestment rate terminale = g / ROIC (coerenza crescita-reinvestimento)
     - ROIC terminale converge verso il cost of capital (no rendita infinita)

Riferimento: uploads/dcfallOld.pdf (Damodaran, "DCF Valuation", 260 pag).
Robusto, numpy non richiesto.
"""
from typing import Dict, Any, List, Optional

# Tabella synthetic rating Damodaran (interest coverage -> rating -> default spread).
# Valori tipici large-cap (aggiornabili da damodaran.com/ratings).
# (coverage_min, rating, default_spread)
RATING_TABLE = [
    (8.50, "Aaa/AAA", 0.0069),
    (6.50, "Aa2/AA",  0.0085),
    (5.50, "A1/A+",   0.0107),
    (4.25, "A2/A",    0.0118),
    (3.00, "A3/A-",   0.0133),
    (2.50, "Baa2/BBB",0.0171),
    (2.25, "Ba1/BB+", 0.0242),
    (2.00, "Ba2/BB",  0.0313),
    (1.75, "B1/B+",   0.0409),
    (1.50, "B2/B",    0.0515),
    (1.25, "B3/B-",   0.0650),
    (0.80, "Caa/CCC", 0.0897),
    (0.50, "Ca2/CC",  0.1200),
    (-1e9, "D",       0.1800),
]


def synthetic_rating(interest_coverage: Optional[float]) -> Dict[str, Any]:
    """Da interest coverage (EBIT/interessi) al rating sintetico + default spread."""
    if interest_coverage is None:
        return {"rating": "n/d", "default_spread": 0.02, "interest_coverage": None,
                "note": "interest coverage non disponibile: spread default 2% prudenziale"}
    for cov_min, rating, spread in RATING_TABLE:
        if interest_coverage >= cov_min:
            return {"rating": rating, "default_spread": spread,
                    "interest_coverage": round(interest_coverage, 2)}
    return {"rating": "D", "default_spread": 0.18, "interest_coverage": round(interest_coverage, 2)}


def cost_of_debt(interest_coverage: Optional[float], rf: float,
                 tax: float = 0.25) -> Dict[str, Any]:
    """Kd pre-tax = rf + default_spread (da synthetic rating); after-tax = Kd*(1-tax)."""
    sr = synthetic_rating(interest_coverage)
    kd_pre = rf + sr["default_spread"]
    return {"kd_pretax": round(kd_pre, 4), "kd_aftertax": round(kd_pre * (1 - tax), 4),
            "rating": sr["rating"], "default_spread": sr["default_spread"],
            "interest_coverage": sr.get("interest_coverage")}


def bottom_up_beta(peer_unlevered_betas: List[float], de: float, tax: float = 0.25,
                   cash_pct: float = 0.0) -> Dict[str, Any]:
    """Beta bottom-up: media beta unlevered peer, rilevered col D/E della societa'.
    Opzionale: correzione per cassa (beta della cassa = 0)."""
    betas = [b for b in (peer_unlevered_betas or []) if b is not None and 0 < b < 4]
    if not betas:
        return {"beta_unlevered": None, "beta_levered": None,
                "note": "nessun beta peer valido; usare beta di settore"}
    bu = sum(betas) / len(betas)
    # correzione cassa: beta business = beta_u / (1 - cash%)
    bu_business = bu / (1 - cash_pct) if 0 <= cash_pct < 0.9 else bu
    bl = bu_business * (1 + (1 - tax) * de)
    return {"beta_unlevered": round(bu, 3), "beta_unlevered_business": round(bu_business, 3),
            "beta_levered": round(bl, 3), "n_peers": len(betas),
            "de": de, "note": "media peer rilevered (Damodaran bottom-up)"}


def disciplined_terminal(g_input: float, rf: float, roic: Optional[float],
                         wacc: float) -> Dict[str, Any]:
    """Applica le regole Damodaran al terminal value.
    - g_cap = min(g_input, rf): la crescita perpetua non supera il risk-free
    - reinvestment_rate = g/ROIC (se ROIC noto), altrimenti g/WACC (ROIC->WACC nel terminale)
    - flag se l'input violava le regole.
    """
    warnings = []
    g = g_input
    if g_input > rf:
        g = rf
        warnings.append(f"g input {g_input:.1%} > risk-free {rf:.1%}: cappato al risk-free "
                        f"(crescita perpetua non puo' superare la crescita dell'economia).")
    # ROIC terminale: se non noto o < WACC, Damodaran suggerisce ROIC -> WACC (no excess return perpetuo)
    roic_term = roic if (roic and roic > wacc) else wacc
    if roic and roic > wacc * 1.5:
        roic_term = wacc * 1.2  # excess return modesto e calante, non perpetuo pieno
        warnings.append(f"ROIC {roic:.1%} molto sopra WACC {wacc:.1%}: nel terminale converge "
                        f"verso {roic_term:.1%} (no rendita infinita).")
    reinvestment_rate = (g / roic_term) if roic_term and roic_term > 0 else g / wacc
    reinvestment_rate = max(0.0, min(1.0, reinvestment_rate))
    return {"g_disciplined": round(g, 4), "g_input": g_input,
            "roic_terminal": round(roic_term, 4),
            "reinvestment_rate": round(reinvestment_rate, 3),
            "fcff_conversion": round(1 - reinvestment_rate, 3),
            "warnings": warnings,
            "note": "g<=rf, reinvestment=g/ROIC, ROIC->WACC (regole terminal Damodaran)"}


def enhanced_wacc(rf: float, erp: float, crp: float, peer_unlevered_betas: List[float],
                  de: float, tax: float, interest_coverage: Optional[float],
                  cash_pct: float = 0.0,
                  ke_floor_spread: float = 0.025,
                  wacc_floor_spread: float = 0.020) -> Dict[str, Any]:
    """WACC completo con rigore Damodaran: bottom-up beta + synthetic-rating Kd.

    audit/13 V1.4e: cintura d'uscita DICHIARATA — Ke >= rf + ke_floor_spread e
    WACC >= rf + wacc_floor_spread; quando un floor morde, la nota 'floor_note'
    finisce nel foglio WACC (il floor sul beta cura la causa tipica, questo copre
    le combinazioni residue rf basso + CRP 0 + D/E alto)."""
    bb = bottom_up_beta(peer_unlevered_betas, de, tax, cash_pct)
    beta_l = bb.get("beta_levered")
    if beta_l is None:
        return {"error": "beta non calcolabile", **bb}
    floor_notes = []
    ke = rf + beta_l * erp + crp
    if ke < rf + ke_floor_spread:
        floor_notes.append("Ke %.2f%% sotto floor rf+%.0fbp: alzato a %.2f%%"
                           % (ke * 100, ke_floor_spread * 1e4, (rf + ke_floor_spread) * 100))
        ke = rf + ke_floor_spread
    cd = cost_of_debt(interest_coverage, rf, tax)
    we = 1 / (1 + de); wd = de / (1 + de)
    wacc = we * ke + wd * cd["kd_aftertax"]
    if wacc < rf + wacc_floor_spread:
        floor_notes.append("WACC %.2f%% sotto floor rf+%.0fbp: alzato a %.2f%%"
                           % (wacc * 100, wacc_floor_spread * 1e4, (rf + wacc_floor_spread) * 100))
        wacc = rf + wacc_floor_spread
    return {
        "wacc": round(wacc, 4), "cost_of_equity": round(ke, 4),
        "beta_levered": beta_l, "beta_unlevered": bb.get("beta_unlevered"),
        "kd_aftertax": cd["kd_aftertax"], "synthetic_rating": cd["rating"],
        "default_spread": cd["default_spread"], "equity_weight": round(we, 3),
        "debt_weight": round(wd, 3),
        "floor_note": ("; ".join(floor_notes) or None),
        "method": "Damodaran: bottom-up beta + synthetic rating cost of debt",
    }


if __name__ == "__main__":
    import json
    print("=== Synthetic rating ===")
    for cov in [12, 5, 3, 1.6, 0.6]:
        print(f"  coverage {cov}x ->", synthetic_rating(cov))
    print("\n=== Bottom-up beta (peer solar) ===")
    print(" ", bottom_up_beta([1.1, 1.25, 1.3, 1.15], de=0.30, tax=0.25, cash_pct=0.15))
    print("\n=== Terminal disciplinato (g input 4% > rf 2.5%) ===")
    print(" ", disciplined_terminal(0.04, 0.025, roic=0.18, wacc=0.09))
    print("\n=== Enhanced WACC (solar-like) ===")
    print(" ", json.dumps(enhanced_wacc(0.045, 0.05, 0.0, [1.1,1.25,1.3,1.15],
          de=0.10, tax=0.25, interest_coverage=8.0, cash_pct=0.20), indent=1))
