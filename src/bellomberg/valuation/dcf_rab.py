"""
dcf_rab.py - Motore di valutazione RETI REGOLATE / RAB — V7 Lotto 1
(G2 di audit/14, design audit/15 approvato dal PM 21/07/2026).

PRINCIPIO (Kairos n.1, audit/14): in una rete regolata la revenue NON e' una
assumption di crescita — e' la FORMULA del regolatore: rendimento ammesso x RAB
+ opex pass-through + D&A. Quindi niente DCF operating (strutturalmente falso):

  1. EV/RAB PREMIUM: EV = RAB x (1 + premio di mercato), equity = EV - net debt.
     Il premio viene dalla banda del profilo (banda dichiarata) o dall'analista.
  2. DDM REGOLATO (solo convenzione pre-tax, es. ARERA): utile ammesso ~
     RAB x WACC ammesso NOMINALE - oneri finanziari netti, tassato, a payout
     reale; terminale Gordon con g = inflazione attesa + crescita reale RAB.
  3. CROSS-CHECK PEER EV/RAB: mediana dei peer regolati (cablaggio nel Lotto 2
     col canale peer; qui il mirror la accetta come parametro).

GERARCHIA INPUT (decisione PM 21/07): ANALISTA > ANCORA REGOLATORIA DICHIARATA
(fonte + data + scadenza, STALE dichiarato a fine periodo) > RIFIUTO.
La RAB non esiste su Yahoo: senza `rab_base` dell'analista (dal piano
industriale/bilancio, [src] nella variant view) il modello si RIFIUTA con
messaggio esplicito — MAI stimata da total assets in silenzio (regola 14/07).

CONVENZIONE VALUTARIA/NOMINALE: tutto in termini NOMINALI e in MILIONI della
valuta di quotazione (come il motore banca). Le ancore reali (ARERA reale
pre-tax, Ofgem CPIH-real) vengono portate a nominale con l'inflazione attesa
DELLA STESSA delibera, conversione dichiarata in _sources.

Lotto 1 = spec + mirror Python + ancore + test offline.
Lotto 2 = workbook a formule vive + routing get_valuation + collaudo sandbox
(Terna/Snam) + review avversariale finanza+codice pre-commit.
"""
from bellomberg.core.language import scoped_language, text as _lt
from bellomberg.reporting.i18n_excel import label as _xt
from typing import Any, Dict, Optional

from bellomberg.valuation.dcf_bank import _real_payout_from_cashflow, _safe  # riuso, mai riscrivere

try:
    from openpyxl.styles import Font
except ImportError:
    Font = None  # il builder workbook e' comunque guarded sull'import openpyxl

N_YEARS = 10
NET_BETA_FLOOR = 0.30    # una rete regolata non ha beta 0.05 ne' 1.4: artefatti
NET_BETA_CAP = 1.00
NET_KE_FLOOR_SPREAD = 0.020   # Ke mai sotto rf + 2.0% (meno rischiosa di una banca)
DA_PCT_RAB_DEFAULT = 0.04     # vita utile regolatoria ~25 anni -> D&A ~4% RAB (dichiarato)

# ============================================================
# ANCORE REGOLATORIE (decisione PM 21/07: "ancore dichiarate + override").
# Numeri VERIFICATI alle fonti primarie il 21/07/2026 — non a memoria.
# Regole: fonte+periodo+scadenza SEMPRE nel dict; dopo valid_until il valore
# e' STALE DICHIARATO (warning), mai silenziosamente attuale; l'override
# dell'analista vince sempre. MAI riusare un'ancora scaduta senza dirlo.
# ============================================================
REG_ANCHORS: Dict[str, Dict[str, Any]] = {
    "ARERA": {
        "countries": ("italy",),
        "period": "2025-2027 (2PWACC sub-periodo 2; revisione 3PWACC dal 2028)",
        "valid_until": "2027-12-31",
        "source": ("ARERA delibera 513/2024/R/COM, Allegato A, Tabella 11 "
                   "(WACC REALI PRE-TAX; RAB rivalutata col deflatore) e Tabella 10 "
                   "(inflazione attesa ia 1,9%, iBoxx spot 3,60%, tc 24%) "
                   "[verificata sul PDF ufficiale arera.it, 21/07/2026]"),
        "convention": "real_pretax",
        # WACC reali pre-tax per servizio, Tabella 11, sub-periodo 2025-2027
        "wacc_real_by_service": {
            "trasmissione_elettrica": 0.0550,
            "distribuzione_elettrica": 0.0560,
            "trasporto_gas": 0.0550,
            "distribuzione_gas": 0.0590,
            "stoccaggio_gas": 0.0610,
            "rigassificazione_gnl": 0.0620,
        },
        # beta asset per servizio (stessa delibera, Parte II)
        "beta_asset": {
            "trasmissione_elettrica": 0.370,
            "distribuzione_elettrica": 0.400,
            "trasporto_gas": 0.384,
            "distribuzione_gas": 0.410,
            "stoccaggio_gas": 0.506,
            "rigassificazione_gnl": 0.524,
        },
        "expected_inflation": 0.019,   # parametro ia (Tab. 10, BCE Bollettino 7/2024)
        "kd_nominal": 0.0360,          # iBoxx spot BBB (Tab. 10): costo nuovo debito
        "tax_tc": 0.24,                # aliquota IRES per lo scudo fiscale (Tab. 10)
        # review V7 finanza (F4): il gross-up pre-tax ARERA sull'equity usa T
        # (IRES+IRAP), non la tc dello scudo debito — l'utile netto ammesso si
        # riporta con (1-T). Il residuo kd*ND*(T-tc) (~25 mln su Terna) resta
        # dentro, dichiarato: separarlo richiede lo split equity/debito (Lotto 3).
        "tax_T": 0.298,                # aliquota teorica T (Tab. 10, 2025-2027)
    },
    "OFGEM": {
        "countries": ("united kingdom",),
        "period": "RIIO-3 (1 aprile 2026 - 31 marzo 2031)",
        "valid_until": "2031-03-31",
        "source": ("Ofgem RIIO-3 Final Determinations, Finance Annex (4/12/2025), "
                   "Table 16: WACC allowance CPIH-real (e nominale) per NGET/SPT/SHET "
                   "e GD&GT; CoE 5,70% ET / 6,12% GD&GT; gearing nozionale 55%/60% "
                   "[verificata sul PDF ufficiale ofgem.gov.uk, 21/07/2026]"),
        "convention": "cpih_real_vanilla",
        # WACC allowance CPIH-real, Table 16 (trasmissione = NGET; SPT 4,58 / SHET 4,67)
        "wacc_real_by_service": {
            "trasmissione_elettrica": 0.0446,
            "trasporto_gas": 0.0428,
            "distribuzione_gas": 0.0428,
        },
        # Table 16 espone anche il nominale: si usa QUELLO (niente conversione nostra)
        "wacc_nominal_by_service": {
            "trasmissione_elettrica": 0.0665,
            "trasporto_gas": 0.0646,
            "distribuzione_gas": 0.0646,
        },
        "expected_inflation": 0.020,   # target CPIH implicito nella FD (nominale-reale)
        "kd_nominal": None,            # non estratto: DDM regolato n.d. su OFGEM (v. sotto)
        "tax_tc": None,
    },
}

# alias di servizio accettati dall'analista -> chiave canonica
SERVICE_ALIASES = {
    "trasmissione": "trasmissione_elettrica",
    "electricity transmission": "trasmissione_elettrica",
    "distribuzione": "distribuzione_elettrica",
    "electricity distribution": "distribuzione_elettrica",
    "trasporto gas": "trasporto_gas",
    "gas transmission": "trasporto_gas",
    "gas transport": "trasporto_gas",
    "distribuzione gas": "distribuzione_gas",
    "gas distribution": "distribuzione_gas",
    "stoccaggio": "stoccaggio_gas",
    "storage": "stoccaggio_gas",
    "rigassificazione": "rigassificazione_gnl",
    "lng": "rigassificazione_gnl",
}


def _canon_service(service):
    s = str(service or "").strip().lower().replace("-", " ")
    if not s:
        return None
    s_key = s.replace(" ", "_")
    for anchor in REG_ANCHORS.values():
        if s_key in anchor["wacc_real_by_service"]:
            return s_key
    return SERVICE_ALIASES.get(s)


def anchor_for(country, service, today=None):
    """(anchor_info | None, note). Ancora regolatoria per paese+servizio, con
    STALE DICHIARATO se oggi supera valid_until (mai silenziosamente attuale).
    Paese o servizio fuori mappa -> (None, motivo): il chiamante RIFIUTA se
    l'analista non ha fornito allowed_return (gerarchia audit/15)."""
    from datetime import date
    c = (country or "").strip().lower()
    svc = _canon_service(service)
    if not svc:
        return None, ("servizio regolato '%s' non riconosciuto (attesi: %s o alias)"
                      % (service, ", ".join(sorted(
                          REG_ANCHORS["ARERA"]["wacc_real_by_service"]))))
    for reg_name, a in REG_ANCHORS.items():
        if c in a["countries"]:
            real = a["wacc_real_by_service"].get(svc)
            if real is None:
                return None, (f"{reg_name}: servizio '{svc}' senza WACC in ancora "
                              f"({a['period']}) — serve allowed_return dell'analista")
            nominal = (a.get("wacc_nominal_by_service") or {}).get(svc)
            if nominal is None:
                nominal = real + a["expected_inflation"]
                conv = (" | nominale = reale %.2f%% + inflazione attesa %.1f%% della "
                        "stessa delibera (conversione dichiarata)"
                        % (real * 100, a["expected_inflation"] * 100))
            else:
                conv = " | nominale della stessa tabella (nessuna conversione nostra)"
            today = today or date.today()
            stale = today.isoformat() > a["valid_until"]
            note = (f"{reg_name} {svc}: WACC ammesso reale {real:.2%}, nominale "
                    f"{nominal:.2%} [{a['source']}]{conv}")
            if stale:
                note += (f" | ATTENZIONE: periodo regolatorio SCADUTO il "
                         f"{a['valid_until']} — valore STALE, verificare la delibera nuova")
            return {"regulator": reg_name, "service": svc, "wacc_real": real,
                    "wacc_nominal": nominal, "convention": a["convention"],
                    "expected_inflation": a["expected_inflation"],
                    "kd_nominal": a.get("kd_nominal"), "tax_tc": a.get("tax_tc"),
                    "tax_T": a.get("tax_T"),
                    "stale": stale, "note": note}, note
    return None, (f"paese '{country}' senza ancora regolatoria in codice "
                  "(coperti: Italia/ARERA, UK/Ofgem) — serve allowed_return dell'analista")


def build_rab_spec(ticker, info, wacc_inputs, rab=None, variant_view=None,
                   profile=None, terminal_growth=None, ticker_obj=None, today=None):
    """Spec rete regolata. `rab` = input dell'ANALISTA (dal piano industriale /
    bilancio, [src] nella variant view), tutto in MILIONI della valuta di
    quotazione salvo i tassi:
      rab_base (OBBLIGATORIO) · rab_base_year · service · allowed_return
      (nominale; se assente si usa l'ancora del regolatore) · capex_plan (lista
      annuale) o capex_pct_rab · da_pct_rab · net_debt · rab_premium · payout ·
      rab_growth_real_lt · cost_of_debt.
    Senza rab_base: {'error': ...} — RIFIUTO dichiarato, mai proxy zitti."""
    src: Dict[str, str] = {}
    r = dict(rab or {})
    _p = profile or {}

    rab_base = _safe(r.get("rab_base"))
    if not rab_base or rab_base <= 0:
        return {"error": (
            f"{ticker}: modello RAB RIFIUTATO — manca `rab_base` (RAB in milioni, "
            "dalla Relazione finanziaria o dal piano industriale, con [src] nella "
            "variant view). La RAB non e' su Yahoo e NON si stima da total assets "
            "in silenzio (regola 14/07). Passa: rab={rab_base, service, net_debt, "
            "capex_plan/capex_pct_rab, eventuale allowed_return}.")}
    src["rab_base"] = ("ANALISTA: RAB %.0f mln (anno %s) — fonte nella variant view"
                       % (rab_base, r.get("rab_base_year") or "n.d."))

    # --- rendimento ammesso: analista > ancora dichiarata > RIFIUTO ---
    from datetime import date as _date
    _today = today or _date.today()
    anchor, anchor_note = anchor_for(info.get("country"), r.get("service"), today=_today)
    # review V7 finanza (F1, ALTA): la convenzione ARERA e' tasso REALE x RAB
    # INDICIZZATA — applicare il NOMINALE a una base gia' indicizzata conta
    # l'inflazione DUE volte (misurato sul primo collaudo: +50% sull'utile
    # ammesso di Snam; la "divergenza 54%" era il bug, non un segnale). Nei
    # conti si usa quindi il tasso REALE (allowed_calc); il nominale resta nel
    # payload SOLO come riferimento. L'override dell'analista si applica TAL
    # QUALE alla RAB: con ancora presente (RAB indicizzata) va passato il
    # tasso REALE della delibera.
    allowed = _safe(r.get("allowed_return"))
    allowed_nominal = None
    if allowed is not None and 0.02 <= allowed <= 0.12:
        allowed_calc = allowed
        src["allowed_return"] = ("OVERRIDE ANALISTA: %.2f%% applicato TAL QUALE alla RAB "
                                 "(%s)" % (allowed * 100,
                                           "indicizzata: passare il tasso REALE della delibera"
                                           if anchor else "non indicizzata, ia=0"))
        if anchor:
            src["allowed_return"] += f" | ancora di confronto: {anchor_note}"
        convention = "analyst_pretax"
    elif allowed is not None:
        return {"error": (f"{ticker}: allowed_return {allowed} fuori bound 2%-12% "
                          "e nessun ripiego zitto: correggi l'input o lascialo "
                          "all'ancora del regolatore (service=...).")}
    elif anchor:
        allowed_calc = anchor["wacc_real"]
        allowed_nominal = anchor["wacc_nominal"]
        convention = anchor["convention"]
        src["allowed_return"] = ("ANCORA REGOLATORIA: tasso REALE %.2f%% sui conti (RAB "
                                 "indicizzata; il nominale %.2f%% e' solo riferimento — "
                                 "review V7 F1) | " % (allowed_calc * 100, allowed_nominal * 100)
                                 ) + anchor_note
    else:
        return {"error": (f"{ticker}: modello RAB RIFIUTATO — ne' allowed_return "
                          f"dell'analista ne' ancora regolatoria ({anchor_note}).")}

    ia = (anchor or {}).get("expected_inflation")
    if ia is None:
        ia = 0.0
        src["inflazione"] = ("nessuna ancora: RAB proiettata SENZA indicizzazione "
                             "(ipotesi conservativa dichiarata)")
    else:
        src["inflazione"] = "inflazione attesa dell'ancora regolatoria: %.1f%%" % (ia * 100)

    # --- roll-forward RAB nominale a 10 anni ---
    da_pct = _safe(r.get("da_pct_rab"))
    if da_pct is None or not 0.005 <= da_pct <= 0.10:
        if da_pct is not None:
            src["da_pct_note"] = f"da_pct_rab {da_pct} fuori bound 0,5%-10%: IGNORATO (dichiarato)"
        da_pct = DA_PCT_RAB_DEFAULT
        src["da_pct_rab"] = ("DEFAULT DICHIARATO %.0f%% della RAB (vita utile "
                             "regolatoria ~25 anni)" % (da_pct * 100))
    else:
        src["da_pct_rab"] = "ANALISTA: D&A regolatoria %.1f%% della RAB" % (da_pct * 100)

    capex_plan = r.get("capex_plan")
    capex_pct = _safe(r.get("capex_pct_rab"))
    plan = []
    if isinstance(capex_plan, (list, tuple)) and capex_plan:
        _raw = [_safe(x, 0.0) or 0.0 for x in capex_plan]
        # review V7 codice (C6): troncamenti e clamp del piano si DICHIARANO
        if len(_raw) > N_YEARS:
            src["capex_note_orizzonte"] = (f"piano capex di {len(_raw)} anni TRONCATO a "
                                           f"{N_YEARS} (orizzonte del modello): dichiarato")
        if any(x < 0 for x in _raw[:N_YEARS]):
            src["capex_note_negativi"] = ("valori capex NEGATIVI (dismissioni/contributi) "
                                          "clampati a 0: il roll-forward non modella le "
                                          "dismissioni — se materiali, correggere rab_base")
        plan = [max(x, 0.0) for x in _raw][:N_YEARS]
        if len(plan) < N_YEARS:
            src["capex_note"] = (f"piano capex di {len(plan)} anni: oltre si prosegue "
                                 "con l'ultimo valore (dichiarato)")
            plan += [plan[-1]] * (N_YEARS - len(plan))
        src["capex"] = "ANALISTA: piano capex annuale (mln)"
    elif capex_pct is not None and 0 <= capex_pct <= 0.20:
        src["capex"] = "ANALISTA: capex %.1f%% della RAB per anno" % (capex_pct * 100)
    else:
        capex_pct = da_pct
        src["capex"] = ("capex n.d.: assunto = D&A (RAB REALE costante, cresce solo "
                        "d'inflazione) — ipotesi conservativa DICHIARATA, il vero "
                        "piano capex e' il primo input da chiedere all'analista")
    rab_vec = []
    b = rab_base
    for t in range(N_YEARS):
        cap_t = plan[t] if plan else b * capex_pct
        b = _rab_close(b, ia, cap_t, b * da_pct, 0.0)
        rab_vec.append(round(b, 1))

    # --- net debt (mln): analista > yfinance contabile dichiarato > n.d. ---
    nd = _safe(r.get("net_debt"))
    if nd is not None:
        src["net_debt"] = "ANALISTA: net debt %.0f mln (fonte nella variant view)" % nd
    else:
        td = _safe(info.get("totalDebt"))
        tc_ = _safe(info.get("totalCash"))
        if td is not None:
            nd = (td - (tc_ or 0.0)) / 1e6
            src["net_debt"] = ("yfinance totalDebt-totalCash = %.0f mln — net debt "
                               "CONTABILE, non economico (ibridi/leases non separati: "
                               "dichiarato, G6 in coda)" % nd)
        else:
            src["net_debt"] = ("net debt N.D. (ne' analista ne' yfinance): metodo "
                               "EV/RAB SALTATO dal blend, dichiarato")

    # --- premio EV/RAB: analista > default profilo, banda dichiarata ---
    band = _p.get("ev_rab_band") or (0.7, 1.6)
    prem = _safe(r.get("rab_premium"))
    if prem is not None and 0.3 <= prem <= 2.5:
        src["rab_premium"] = "OVERRIDE ANALISTA: EV/RAB %.2fx" % prem
    else:
        if prem is not None:
            src["rab_premium_note"] = f"rab_premium {prem} fuori bound 0,3-2,5: IGNORATO (dichiarato)"
        prem = _safe(_p.get("rab_premium_default"), 1.15)
        src["rab_premium"] = ("default profilo %.2fx (banda plausibile %.1f-%.1fx, "
                              "dichiarata)" % (prem, band[0], band[1]))

    # --- payout: analista > cashflow reale > default dichiarato (con clamp profilo) ---
    payout = _safe(r.get("payout"))
    if payout is not None and 0.05 <= payout <= 1.2:
        src["payout"] = "OVERRIDE ANALISTA"
    else:
        if payout is not None:
            # review V7 codice (C7): anche il fuori-bound del payout si dichiara
            src["payout_note"] = f"payout analista {payout} fuori bound 0,05-1,2: IGNORATO (dichiarato)"
        payout = None
        if ticker_obj is not None:
            pdiv, ptot, psrc = _real_payout_from_cashflow(ticker_obj)
            if ptot and 0.05 <= ptot <= 1.2:
                payout, src["payout"] = ptot, "REALE (div+buyback): " + psrc
            elif pdiv and 0.05 <= pdiv <= 1.0:
                payout, src["payout"] = pdiv, "REALE (solo dividendi): " + psrc
        if payout is None:
            p_info = _safe(info.get("payoutRatio"))
            if p_info and 0.05 <= p_info <= 1.1:
                payout, src["payout"] = p_info, "yfinance payoutRatio (verificare)"
            else:
                payout, src["payout"] = 0.80, "DEFAULT 80% DICHIARATO (rete = dividend stock)"
    _pf, _pc = _safe(_p.get("payout_floor"), 0.05), _safe(_p.get("payout_cap"), 1.0)
    _pre = payout
    payout = min(max(payout, _pf), _pc)
    if payout != _pre:
        src["payout"] += "; clamp profilo [%.0f%%, %.0f%%]" % (_pf * 100, _pc * 100)

    # --- Ke nominale (CAPM, beta clampato di profilo) per scontare il DDM ---
    beta_raw = _safe(info.get("beta"))
    _bf = _safe(_p.get("net_beta_floor"), NET_BETA_FLOOR)
    _bc = _safe(_p.get("net_beta_cap"), NET_BETA_CAP)
    _bd = _safe(_p.get("net_beta_default"), 0.65)
    beta = min(max(beta_raw if beta_raw else _bd, _bf), _bc)
    src["beta"] = (f"yfinance {beta_raw} -> clamp profilo [{_bf},{_bc}]" if beta_raw
                   else f"default profilo {_bd} (beta yfinance assente)")
    rf = wacc_inputs["rf"]; erp = wacc_inputs["erp"]; crp = wacc_inputs.get("crp", 0)
    ke = max(rf + beta * erp + crp, rf + NET_KE_FLOOR_SPREAD)
    src["ke"] = (f"CAPM rf {rf:.2%} + beta {beta:.2f} x ERP {erp:.2%} + CRP {crp:.2%}, "
                 f"floor rf+{NET_KE_FLOOR_SPREAD:.1%}")

    # --- costo del debito e tasse per l'utile ammesso (solo convenzione pre-tax) ---
    kd = _safe(r.get("cost_of_debt"))
    if kd is not None and 0.005 <= kd <= 0.10:
        src["kd"] = "OVERRIDE ANALISTA: kd %.2f%%" % (kd * 100)
    elif anchor and anchor.get("kd_nominal"):
        kd = anchor["kd_nominal"]
        src["kd"] = "ancora regolatoria: iBoxx spot BBB %.2f%% (stessa delibera)" % (kd * 100)
    else:
        kd = None
        src["kd"] = "kd n.d. (ne' analista ne' ancora): DDM regolato SALTATO, dichiarato"
    # review V7 finanza (F4): l'utile netto ammesso si riporta con l'aliquota
    # TEORICA T del gross-up ARERA (29,8%, IRES+IRAP), NON con la tc=24% dello
    # scudo debito — usare tc sovrastimava la componente equity del +8,3%.
    tax = (anchor or {}).get("tax_T") or (anchor or {}).get("tax_tc")
    if tax is None:
        tax = 0.298 if (info.get("country") or "").lower() == "italy" else None
        if tax is not None:
            src["tax"] = "T 29,8% (aliquota teorica ARERA Tab.10, default Italia dichiarato)"
    else:
        src["tax"] = ("T %.1f%% (aliquota teorica del gross-up pre-tax dell'ancora, "
                      "non la tc dello scudo debito — review V7 F4)" % (tax * 100))

    # --- crescita terminale nominale: analista > ia + crescita reale RAB, cap a rf ---
    g_real = _safe(r.get("rab_growth_real_lt"), 0.0) or 0.0
    if terminal_growth is not None and 0 <= _safe(terminal_growth, -1) <= rf:
        g = float(terminal_growth)
        src["g"] = "OVERRIDE ANALISTA (terminal_growth)"
    else:
        g = min(ia + max(min(g_real, 0.02), -0.02), rf)
        src["g"] = ("inflazione attesa %.1f%% + crescita reale RAB lt %.1f%% "
                    "(cap a rf %.2f%%)" % (ia * 100, g_real * 100, rf * 100))
    g = min(g, ke - 0.005)  # Gordon definito: mai g >= Ke
    # review V7 finanza (F2a, pattern banca V2.5d): la crescita perpetua dei
    # dividendi non puo' superare cio' che la retention finanzia — ROE implicito
    # sull'equity regolatorio x utili trattenuti. Clamp DICHIARATO.
    if kd is not None and tax is not None and nd is not None and rab_base > nd:
        _ni1 = (rab_base * allowed_calc - nd * kd) * (1.0 - tax)
        if _ni1 > 0:
            _g_sust = (_ni1 / (rab_base - nd)) * max(1.0 - payout, 0.0)
            if g > _g_sust:
                src["g"] = (src.get("g", "") +
                            "; COERENZA (review V7 F2): g %.2f%% > ROE implicito x "
                            "retention = %.2f%%: clampata" % (g * 100, _g_sust * 100))
                g = round(max(0.0, _g_sust), 4)

    shares = (_safe(info.get("sharesOutstanding"), 0) or 0) / 1e6
    price = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
    if not shares:
        return {"error": (f"{ticker}: sharesOutstanding assente su Yahoo — il "
                          "per-share non e' calcolabile, modello RAB rifiutato "
                          "(dichiarato, non stimato)")}
    cur = info.get("currency") or "EUR"
    if cur in ("GBp", "GBX"):
        # review V7 codice (C1, ALTA): input analista in GBP mln, quotazione in
        # PENCE — senza conversione la sanity confronterebbe GBP con pence
        # (FV/prezzo ~1/100 -> FLAGGED con rimedio sbagliato). Prezzo a GBP,
        # fattore dichiarato; payload_currency GBP (contratto banca, caso HSBA.L).
        if price:
            src["prezzo"] = ("quotazione %s in pence: prezzo %.2f -> %.4f GBP "
                             "(fattore 100 dichiarato; input analista in GBP mln)"
                             % (cur, price, price / 100.0))
            price = price / 100.0
        cur = "GBP"

    return {
        "ticker": str(ticker).upper(), "company_name": info.get("longName") or ticker,
        "currency": cur,
        "country": info.get("country") or "n/d",
        "engine": "rab", "convention": convention,
        "rab_base": round(rab_base, 1), "rab_vec": rab_vec,
        "rab_base_year": r.get("rab_base_year"), "_today_year": _today.year,
        "service": (anchor or {}).get("service") or _canon_service(r.get("service")),
        # calc = tasso USATO nei conti (REALE con ancora, review V7 F1);
        # nominal = solo riferimento nel payload/foglio Thesis
        "allowed_return_calc": round(allowed_calc, 4),
        "allowed_return_nominal": round(allowed_nominal if allowed_nominal is not None
                                        else allowed_calc + ia, 4),
        "anchor_stale": bool((anchor or {}).get("stale")),
        "ia": ia, "da_pct_rab": da_pct,
        "net_debt": round(nd, 1) if nd is not None else None,
        "rab_premium": prem, "ev_rab_band": tuple(band),
        "payout": round(payout, 3), "kd": kd, "tax": tax,
        "beta": round(beta, 3), "ke": round(ke, 4), "rf": rf,
        "growth_lt": round(g, 4), "shares": round(shares, 1), "price": price,
        # per il foglio RAB Roll-forward: piano capex esplicito o % RAB (mai entrambi)
        "_capex_plan_mln": plan if plan else None,
        "_capex_pct_rab": None if plan else capex_pct,
        "variant_view": variant_view, "_sources": src,
    }


# ---------- fair value NUMERICI (mirror Python; il workbook arriva col Lotto 2) ----------
def _rab_close(opening, indexation, recognized_capex, depreciation, disposals):
    return opening * (1.0 + indexation) + recognized_capex - depreciation - disposals


def project_documented_rab(opening, periods):
    """Pure regulatory/cash ledger; caller binds and validates every supplied driver."""
    from .cash_math import cash_sweep
    rab,debt,cash=opening['rab'],opening['debt'],opening['cash']; wc=opening['working_capital']; rows=[]
    unrecognized=opening['unrecognized_investment']
    for p in periods:
        close_rab=_rab_close(rab,p['indexation'],p['recognized_capex'],p['regulatory_depreciation'],p['disposals_rab'])
        revenue=rab*p['allowed_return']+p['regulatory_depreciation']+p['allowed_opex']+p['incentives']+p['tax_allowance']
        ni=revenue-p['cash_opex']-p['book_depreciation']-p['interest_paid']+p['interest_received']-p['cash_tax']
        available=(cash+ni+p['book_depreciation']-p['cash_capex']-p['working_capital_change']+
                   p['disposal_cash']+p['debt_issued']-p['debt_repaid'])
        sweep=cash_sweep(available,p['minimum_cash']);distribution=sweep['distribution']
        close_debt=debt+p['debt_issued']-p['debt_repaid']
        close_wc=wc+p['working_capital_change']
        close_unrecognized=unrecognized+p['cash_capex']-p['recognized_capex']
        rows.append({'opening_rab':rab,'closing_rab':close_rab,'allowed_revenue':revenue,
            'income_after_cash_tax':ni,'cash_before_distribution':available,'shareholder_net_distribution':distribution,
            'opening_debt':debt,'closing_debt':close_debt,'opening_cash':cash,'closing_cash':p['minimum_cash'],
            'opening_working_capital':wc,'closing_working_capital':close_wc,
            'opening_unrecognized_investment':unrecognized,'closing_unrecognized_investment':close_unrecognized,
            'funding_required':sweep['funding_required'],**p})
        rab,debt,cash=close_rab,close_debt,p['minimum_cash']
        wc=close_wc
        unrecognized=close_unrecognized
    return rows


def _fv_ev_rab(spec):
    """EV = RAB x (1+premio... gia' dentro rab_premium come multiplo EV/RAB),
    equity = EV - net debt, per azione. Net debt n.d. -> None dichiarato."""
    nd = spec.get("net_debt")
    if nd is None:
        return None
    ev = spec["rab_base"] * spec["rab_premium"]
    return round((ev - nd) / spec["shares"], 2)


def _fv_ddm_regolato(spec):
    """Utile ammesso_t = RAB_{t-1} x WACC ammesso nominale - net debt x kd, poi
    tassato (tc) e distribuito a payout; TV Gordon su DIV_10. SOLO per
    convenzioni pre-tax (ARERA / override analista): su vanilla (Ofgem) il
    passaggio a utile netto richiede la separazione equity/debito della FD ->
    n.d. DICHIARATO fino alla review finanza del Lotto 2."""
    if spec.get('documented_inputs'):
        ke=spec['ke']; g=spec['growth_lt']; periods=spec['discount_periods']
        pv=sum(cash/(1+ke)**t for cash,t in zip(spec['cash_distributions'],periods))
        terminal=spec['terminal_distribution']/(ke-g)
        return (pv+terminal/(1+ke)**periods[-1])/spec['shares']
    if spec.get("convention") not in ("real_pretax", "analyst_pretax"):
        return None
    kd, tax, nd = spec.get("kd"), spec.get("tax"), spec.get("net_debt")
    if kd is None or tax is None or nd is None:
        return None
    ke, g, pay = spec["ke"], spec["growth_lt"], spec["payout"]
    if ke <= g:
        return None
    b_prev = spec["rab_base"]
    pv = 0.0
    div_t = 0.0
    df = 1.0
    for t, b_t in enumerate(spec["rab_vec"], start=1):
        # review V7 F1: tasso REALE su RAB indicizzata (i flussi escono NOMINALI
        # perche' la base cresce d'inflazione) — mai il nominale sulla base indicizzata
        ni = (b_prev * spec["allowed_return_calc"] - nd * kd) * (1.0 - tax)
        div_t = ni * pay
        df = 1.0 / (1.0 + ke) ** t
        pv += div_t * df
        b_prev = b_t
    tv = div_t * (1.0 + g) / (ke - g)
    return round((pv + tv * df) / spec["shares"], 2)


def _allowed_ni_year1(spec):
    """Utile netto ammesso ANNO 1 (mln): (RAB x WACC ammesso - ND x kd) x (1-tc).
    None se la convenzione non e' pre-tax o mancano kd/tax/net debt (dichiarato)."""
    if spec.get("convention") not in ("real_pretax", "analyst_pretax"):
        return None
    kd, tax, nd = spec.get("kd"), spec.get("tax"), spec.get("net_debt")
    if kd is None or tax is None or nd is None:
        return None
    return (spec["rab_base"] * spec["allowed_return_calc"] - nd * kd) * (1.0 - tax)


def compute_fair_values_rab(spec, peer_ev_rab_median=None, peer_pe_median=None) -> Dict[str, Any]:
    """Blend mediano dei metodi disponibili + warnings dichiarati (stesso
    contratto di compute_fair_values banca: sanity/scorer/F17 riusano tutto).
    peer_ev_rab_median: EV/RAB mediano fornito dall'ANALISTA (i peer non
    pubblicano la RAB su Yahoo). peer_pe_median: P/E mediano dei peer regolati
    dal canale auto (rab_peer_comps) applicato all'utile ammesso anno 1."""
    import statistics
    ev_rab = _fv_ev_rab(spec)
    ddm = _fv_ddm_regolato(spec)
    peer = None
    peer_label = "Peer EV/RAB"
    if peer_ev_rab_median and peer_ev_rab_median > 0 and spec.get("net_debt") is not None:
        peer = round((spec["rab_base"] * peer_ev_rab_median - spec["net_debt"])
                     / spec["shares"], 2)
    elif peer_pe_median and peer_pe_median > 0:
        ni1 = _allowed_ni_year1(spec)
        if ni1 is not None and ni1 > 0:
            peer_label = "Peer P/E"
            peer = round(ni1 * peer_pe_median / spec["shares"], 2)
    named = [(k, v) for k, v in (("EV/RAB", ev_rab), ("DDM regolato", ddm),
                                 (peer_label, peer)) if v and v > 0]
    warn = []
    if spec.get("anchor_stale"):
        warn.append("ANCORA REGOLATORIA STALE: periodo scaduto — rendimento ammesso "
                    "da riverificare sulla delibera nuova PRIMA di usare il numero.")
    lo, hi = spec.get("ev_rab_band") or (0.7, 1.6)
    if not lo <= spec["rab_premium"] <= hi:
        warn.append("Premio EV/RAB %.2fx FUORI dalla banda plausibile %.1f-%.1fx del "
                    "profilo: o crescita RAB straordinaria argomentata, o input rotto "
                    "— da spiegare nella variant view." % (spec["rab_premium"], lo, hi))
    if spec.get("net_debt") is None:
        warn.append("Net debt n.d.: metodi EV/RAB e peer SALTATI (dichiarato).")
    if ddm is None and spec.get("convention") == "cpih_real_vanilla":
        warn.append("DDM regolato n.d. su convenzione vanilla (Ofgem): richiede la "
                    "separazione equity/debito della FD — Lotto 2 (dichiarato).")
    # review V7 finanza (F2b): FINANZIABILITA' del piano — se capex - D&A supera
    # gli utili trattenuti (con ND e azioni COSTANTI nel modello), i dividendi
    # del DDM sono pagati con equity fantasma: si dichiara e si quantifica.
    kd, tax, nd = spec.get("kd"), spec.get("tax"), spec.get("net_debt")
    ni_series = None
    if (spec.get("convention") in ("real_pretax", "analyst_pretax")
            and kd is not None and tax is not None and nd is not None):
        ni_series = []
        _b = spec["rab_base"]
        for b_t in spec["rab_vec"]:
            ni_series.append((_b * spec["allowed_return_calc"] - nd * kd) * (1.0 - tax))
            _b = b_t
    if ni_series:
        _plan = spec.get("_capex_plan_mln")
        _pct = spec.get("_capex_pct_rab") or 0.0
        _da = spec.get("da_pct_rab") or 0.0
        _b = spec["rab_base"]
        _gap_max = 0.0
        for t, b_t in enumerate(spec["rab_vec"]):
            _cap = _plan[t] if _plan else _b * _pct
            _gap_max = max(_gap_max, _cap - _b * _da - ni_series[t] * (1.0 - spec["payout"]))
            _b = b_t
        if ni_series[0] > 0 and _gap_max > 0.05 * ni_series[0]:
            warn.append("PIANO NON FINANZIABILE con utili trattenuti a payout %.0f%%: "
                        "mancano fino a ~%.0f mln/anno di equity o nuovo debito NON "
                        "modellati (ND e azioni costanti) — il DDM SOVRASTIMA, leggere "
                        "il numero con l'EV/RAB accanto (review V7 F2)."
                        % (spec["payout"] * 100, _gap_max))
    # review V7 finanza (F3): leva implicita calante mai detta — non e' prudenza
    if spec.get("net_debt") and spec["rab_vec"]:
        _lev0 = spec["net_debt"] / spec["rab_base"]
        _lev9 = spec["net_debt"] / spec["rab_vec"][-1]
        if _lev0 > 0 and _lev9 < 0.7 * _lev0:
            warn.append("ND costante su RAB in crescita: leva implicita da %.0f%% a %.0f%% "
                        "in 10 anni — NI tardivi e terminale beneficiano di un de-leverage "
                        "NON finanziato (dichiarato; review V7 F3)." % (_lev0 * 100, _lev9 * 100))
    # review V7 finanza (F5a): la RAB invecchia come l'ancora — staleness dichiarata
    _rby, _ty = spec.get("rab_base_year"), spec.get("_today_year")
    if _rby and _ty and _rby < _ty - 1:
        warn.append("RAB base dell'anno %s (oggi %s): STALE — una rete che investe "
                    "aggiunge RAB ogni anno, aggiornare il dato dal bilancio (review V7 F5)."
                    % (_rby, _ty))
    vals = [v for _, v in named]
    blend = round(statistics.median(vals), 2) if vals else None
    div = round(max(vals) / min(vals) - 1.0, 2) if len(vals) >= 2 and min(vals) > 0 else None
    if div is not None and div > 0.4:
        warn.append(f"DIVERGENZA TRA METODI {div:+.0%}: rivedere premio/rendimento "
                    "ammesso/net debt PRIMA di usare il numero.")
    # review V7 finanza (F6): DDM e Peer P/E condividono l'utile ammesso — se la
    # mediana cade su uno dei due gemelli lontano dall'EV/RAB indipendente, NON
    # e' un voto 2-a-1 (stessa lezione dei gemelli RI/DDM del motore banca).
    if (len(named) == 3 and blend is not None and ev_rab
            and blend in (ddm, peer) and abs(blend / ev_rab - 1.0) > 0.25):
        warn.append("Il blend cade su un metodo basato sull'UTILE AMMESSO (DDM/Peer P/E, "
                    "input condiviso) e dista %+.0f%% dall'EV/RAB indipendente: non e' un "
                    "voto 2-a-1 — divergenza da spiegare, non da mediare (review V7 F6)."
                    % ((blend / ev_rab - 1.0) * 100))
    return {"fair_value_ev_rab": ev_rab, "fair_value_ddm_reg": ddm,
            "fair_value_peer": peer, "peer_method": peer_label if peer else None,
            "fair_value_blend": blend,
            "blend_methods": [k for k, _ in named],
            "methods_divergence": div, "warnings": warn}


# ============================================================
# PEER COMPS REGOLATI (Lotto 2) — best-effort, ONLINE (mai nei test)
# ============================================================
def rab_peer_comps(peers, exclude, pe_band=None, exclude_name=None):
    """P/E e dividend yield dei peer regolati via yfinance (best-effort, online).
    Come bank_peer_comps: P/E fuori dalla banda di plausibilita' del profilo
    esce dalla MEDIANA ma la riga resta nel foglio col motivo (niente scarti
    silenziosi, regola 14/07). Il P/E e' l'unico multiplo peer calcolabile da
    Yahoo: l'EV/RAB dei peer richiederebbe le loro RAB (non pubblicate li') —
    quello resta un input dell'analista. Ritorna (righe, nota)."""
    rows = []
    try:
        import yfinance as yf
    except ImportError:
        return rows, "yfinance non disponibile: peer comps regolati SALTATI (dichiarato)"
    lo, hi = pe_band or (5.0, 30.0)
    _ex_names = {str(x).strip().lower() for x in (exclude_name or []) if x}
    seen = set()
    for p in [str(x).upper() for x in (peers or [])][:8]:
        if p == str(exclude).upper() or p in seen:
            continue
        seen.add(p)
        try:
            pinfo = yf.Ticker(p).info or {}
        except Exception as e:
            rows.append({"ticker": p, "name": p, "pe": None, "divy": None,
                         "in_band": False, "note": f"fetch fallito ({type(e).__name__})"})
            continue
        nm = pinfo.get("shortName") or pinfo.get("longName") or p
        if str(nm).strip().lower() in _ex_names:
            continue  # cross-listing dello stesso emittente (lezione 17/07)
        pe = _safe(pinfo.get("trailingPE"))
        divy = _safe(pinfo.get("dividendYield"))
        # dividendYield yfinance in unita' PERCENTO (misura 17/07 su 25 ticker)
        divy = round(divy / 100.0, 4) if divy is not None else None
        in_band = pe is not None and lo <= pe <= hi
        note = ("ok" if in_band else
                ("P/E n.d." if pe is None else
                 f"P/E {pe:.1f} FUORI banda {lo:.0f}-{hi:.0f}: escluso dalla mediana, riga dichiarata"))
        rows.append({"ticker": p, "name": nm, "pe": round(pe, 2) if pe else None,
                     "divy": divy, "in_band": in_band, "note": note})
    n_ok = sum(1 for r in rows if r["in_band"])
    return rows, (f"{len(rows)} peer, {n_ok} con P/E in banda {lo:.0f}-{hi:.0f} "
                  "(mediana solo sugli in-banda)")


# ============================================================
# WORKBOOK RAB (Lotto 2) — formule VIVE, pattern dcf_bank
# ============================================================
_COLS = "CDEFGHIJKL"   # anni 1..10


def _sheet_thesis_rab(wb, spec, fv, peers_note=None):
    from bellomberg.valuation.dcf_bank import GREYTX, L, T
    from datetime import datetime as _dt
    ws = wb.create_sheet("Thesis & Assumptions", 0)
    T(ws, "A1", _lt(f"{spec['company_name']} ({spec['ticker']}) - Rete regolata (motore RAB)",f"{spec['company_name']} ({spec['ticker']}) - Regulated network (RAB engine)"))
    L(ws, "A2", _lt(f"Motore RAB V7 (audit/15) - {_dt.now():%d/%m/%Y %H:%M} - valuta {spec['currency']}",f"Motore RAB V7 (audit/15) - {_dt.now():%d/%m/%Y %H:%M} - currency {spec['currency']}"),
      italic=True, color=GREYTX)
    L(ws, "A4", _xt("PERCHE' NON C'E' UN DCF: in una rete regolata la revenue e' la FORMULA "
                "del regolatore (WACC ammesso x RAB + pass-through), non un'assumption di "
                "crescita. Metodi: EV/RAB premium, DDM sull'utile ammesso, peer."), italic=True)
    r = 6
    L(ws, f"A{r}", _xt("SERVIZIO REGOLATO: ") + str(spec.get("service") or "n.d."), bold=True); r += 1
    L(ws, f"A{r}", _xt("RENDIMENTO AMMESSO (nominale): %.2f%%") % (spec["allowed_return_nominal"] * 100),
      bold=True); r += 2
    L(ws, f"A{r}", _xt("TESI DELL'ANALISTA (variant view):"), bold=True); r += 1
    L(ws, f"A{r}", str(spec.get("variant_view") or _xt("(nessuna variant view fornita)"))); r += 2
    L(ws, f"A{r}", _xt("FONTI E SCELTE (ogni input ha la sua):"), bold=True); r += 1
    for k, v in (spec.get("_sources") or {}).items():
        L(ws, f"A{r}", f"- {k}: {v}"); r += 1
    if peers_note:
        L(ws, f"A{r}", f"- peer: {peers_note}"); r += 1
    r += 1
    warns = (fv or {}).get("warnings") or []
    if warns:
        L(ws, f"A{r}", _xt("ATTENZIONI (dal motore, da riportare nel report):"), bold=True); r += 1
        for w in warns:
            L(ws, f"A{r}", "! " + w); r += 1
    ws.column_dimensions["A"].width = 110


def _sheet_rab_rollforward(wb, spec):
    from bellomberg.valuation.dcf_bank import H, L, N, T, GOLD
    ws = wb.create_sheet("RAB Roll-forward")
    T(ws, "A1", _xt("Roll-forward della RAB (nominale, mln %s)") % spec["currency"])
    L(ws, "A3", _xt("RAB base (mln, anno %s)") % (spec.get("rab_base_year") or "n.d."))
    N(ws, "B3", spec["rab_base"], fmt="#,##0.0")
    L(ws, "A4", _xt("Indicizzazione annua (ia)"))
    N(ws, "B4", spec["ia"], fmt="0.00%")
    L(ws, "A5", _xt("D&A regolatoria (% RAB)"))
    N(ws, "B5", spec["da_pct_rab"], fmt="0.0%")
    plan = spec.get("_capex_plan_mln")
    if plan is None:
        L(ws, "A6", _xt("Capex (% RAB, piano annuale n.d.)"))
        N(ws, "B6", spec.get("_capex_pct_rab") or 0.0, fmt="0.0%")
    H(ws, "A8", _xt("VOCE"))
    for i, c in enumerate(_COLS):
        H(ws, f"{c}8", _lt(f'Anno {i + 1}',f'Year {i + 1}'))
        N(ws, f"{c}9", i + 1, fmt="0")
    L(ws, "A9", "t")
    L(ws, "A10", _xt("RAB inizio anno (BoP)"))
    L(ws, "A11", "Capex")
    L(ws, "A12", _xt("D&A regolatoria"))
    L(ws, "A13", _xt("RAB fine anno (EoP)"), bold=True)
    for i, c in enumerate(_COLS):
        ws[f"{c}10"] = "=$B$3" if i == 0 else f"={_COLS[i-1]}13"
        ws[f"{c}10"].number_format = "#,##0.0"
        if plan is not None:
            N(ws, f"{c}11", plan[i], fmt="#,##0.0")
        else:
            ws[f"{c}11"] = f"={c}10*$B$6"
            ws[f"{c}11"].number_format = "#,##0.0"
        ws[f"{c}12"] = f"={c}10*$B$5"
        ws[f"{c}12"].number_format = "#,##0.0"
        ws[f"{c}13"] = f"={c}10*(1+$B$4)+{c}11-{c}12"
        ws[f"{c}13"].number_format = "#,##0.0"
        ws[f"{c}13"].font = Font(bold=True, color=GOLD, size=9)
    ws.column_dimensions["A"].width = 34
    for c in "B" + _COLS:
        ws.column_dimensions[c].width = 11


def _sheet_ddm_regolato(wb, spec):
    from bellomberg.valuation.dcf_bank import GREYTX, H, L, N, T, GOLD
    ws = wb.create_sheet("DDM regolato")
    T(ws, "A1", _xt("DDM sull'utile ammesso (nominale, 10 anni + Gordon)"))
    ni1 = _allowed_ni_year1(spec)
    if ni1 is None:
        L(ws, "A3", _xt("DDM regolato N.D. DICHIARATO: ") +
          (_xt("convenzione vanilla (Ofgem) — serve la separazione equity/debito della FD "
           "(review finanza)") if spec.get("convention") == "cpih_real_vanilla" else
           _xt("mancano kd / aliquota / net debt (vedi Thesis)")), italic=True, color=GREYTX)
        ws.column_dimensions["A"].width = 90
        return
    for lbl, cell, val, fmt in (
            (_xt("WACC ammesso (REALE, su RAB indicizzata — review V7 F1)"), "B3",
             spec["allowed_return_calc"], "0.00%"),
            (_xt("Costo del debito kd"), "B4", spec["kd"], "0.00%"),
            (_xt("Aliquota (tc)"), "B5", spec["tax"], "0.0%"),
            ("Payout", "B6", spec["payout"], "0.0%"),
            ("Ke (CAPM)", "B7", spec["ke"], "0.00%"),
            (_xt("g terminale (nominale)"), "B8", spec["growth_lt"], "0.00%"),
            (_xt("Net debt (mln)"), "B9", spec["net_debt"], "#,##0.0"),
            (_xt("Azioni (mln)"), "B10", spec["shares"], "#,##0.0")):
        L(ws, "A" + cell[1:], lbl)
        N(ws, cell, val, fmt=fmt)
    H(ws, "A12", _xt("VOCE"))
    for i, c in enumerate(_COLS):
        H(ws, f"{c}12", _lt(f'Anno {i + 1}',f'Year {i + 1}'))
        N(ws, f"{c}13", i + 1, fmt="0")
    L(ws, "A13", "t")
    rows = ((_xt("RAB inizio anno (dal foglio RAB)"), 14, lambda c: f"='RAB Roll-forward'!{c}10", "#,##0.0"),
            (_xt("Utile ammesso lordo = RABxWACC - NDxkd"), 15, lambda c: f"={c}14*$B$3-$B$9*$B$4", "#,##0.0"),
            (_xt("Utile netto ammesso"), 16, lambda c: f"={c}15*(1-$B$5)", "#,##0.0"),
            (_xt("Dividendo (payout)"), 17, lambda c: f"={c}16*$B$6", "#,##0.0"),
            (_xt("Fattore di sconto"), 18, lambda c: f"=1/(1+$B$7)^{c}13", "0.000"),
            (_xt("PV dividendo"), 19, lambda c: f"={c}17*{c}18", "#,##0.0"))
    for lbl, rr, f, fmt in rows:
        L(ws, f"A{rr}", lbl)
        for c in _COLS:
            ws[f"{c}{rr}"] = f(c)
            ws[f"{c}{rr}"].number_format = fmt
    L(ws, "A21", _xt("Terminale Gordon su DIV anno 10"))
    ws["B21"] = f"={_COLS[-1]}17*(1+$B$8)/($B$7-$B$8)"
    ws["B21"].number_format = "#,##0.0"
    L(ws, "A22", _xt("FAIR VALUE PER AZIONE (DDM regolato)"), bold=True)
    ws["B22"] = f"=(SUM(C19:{_COLS[-1]}19)+B21*{_COLS[-1]}18)/$B$10"
    ws["B22"].number_format = "#,##0.00"
    ws["B22"].font = Font(bold=True, color=GOLD, size=11)
    ws.column_dimensions["A"].width = 40
    for c in "B" + _COLS:
        ws.column_dimensions[c].width = 11


def _sheet_ev_rab_peers(wb, spec, peers_data, peers_note=None):
    from bellomberg.valuation.dcf_bank import GREYTX, H, L, N, T, GOLD
    ws = wb.create_sheet("EV-RAB & Peers")
    T(ws, "A1", _xt("EV/RAB premium + cross-check peer"))
    L(ws, "A3", _xt("RAB base (mln)")); ws["B3"] = "='RAB Roll-forward'!B3"
    ws["B3"].number_format = "#,##0.0"
    L(ws, "A4", _xt("Premio EV/RAB")); N(ws, "B4", spec["rab_premium"], fmt="0.00")
    lo, hi = spec.get("ev_rab_band") or (0.7, 1.6)
    L(ws, "C4", _xt("banda plausibile %.1f-%.1fx (fuori banda = warning dichiarato)") % (lo, hi),
      italic=True, color=GREYTX)
    L(ws, "A5", _xt("Net debt (mln)"))
    if spec.get("net_debt") is not None:
        N(ws, "B5", spec["net_debt"], fmt="#,##0.0")
    else:
        L(ws, "B5", _xt("n.d. (dichiarato)"), color=GREYTX)
    L(ws, "A6", _xt("Azioni (mln)")); N(ws, "B6", spec["shares"], fmt="#,##0.0")
    L(ws, "A8", _xt("EV = RAB x premio")); ws["B8"] = "=B3*B4"; ws["B8"].number_format = "#,##0.0"
    if spec.get("net_debt") is not None:
        L(ws, "A9", "Equity = EV - net debt"); ws["B9"] = "=B8-B5"; ws["B9"].number_format = "#,##0.0"
        L(ws, "A10", _xt("FAIR VALUE PER AZIONE (EV/RAB)"), bold=True)
        ws["B10"] = "=B9/B6"; ws["B10"].number_format = "#,##0.00"
        ws["B10"].font = Font(bold=True, color=GOLD, size=11)
    else:
        L(ws, "A9", _xt("Equity n.d.: net debt mancante — metodo SALTATO, dichiarato"), color=GREYTX)
    # review V7 finanza (F5b): il perimetro del metodo va DETTO nel foglio
    L(ws, "A11", _xt("NB: EV = SOLO RAB x premio — partecipazioni/attivita' NON-RAB escluse "
                 "(Snam: TAP/TAG/Terega/ADNOC ~2 mld). Se materiali: correggere via "
                 "rab_premium con [src] nella variant view."), italic=True, color=GREYTX)
    r = 13
    H(ws, f"A{r}", _xt("PEER REGOLATI")); H(ws, f"B{r}", "P/E"); H(ws, f"C{r}", _xt("Div yield"))
    H(ws, f"D{r}", _xt("Nota"))
    if peers_note:
        L(ws, f"E{r}", peers_note, italic=True, color=GREYTX)
    r += 1
    _inband_cells = []
    for p in (peers_data or []):
        L(ws, f"A{r}", f"{p.get('name')} ({p.get('ticker')})")
        if p.get("pe") is not None:
            N(ws, f"B{r}", p["pe"], fmt="0.0")
            if p.get("in_band"):
                _inband_cells.append(f"B{r}")
        if p.get("divy") is not None:
            N(ws, f"C{r}", p["divy"], fmt="0.00%")
        L(ws, f"D{r}", p.get("note") or "")
        r += 1
    _fv_peer_cell = None
    if _inband_cells:
        L(ws, f"A{r}", _xt("MEDIANA P/E (solo in banda)"), bold=True)
        ws[f"B{r}"] = "=MEDIAN(" + ",".join(_inband_cells) + ")"
        ws[f"B{r}"].number_format = "0.0"
        _med_cell = f"B{r}"
        r += 1
        if _allowed_ni_year1(spec) is not None:
            L(ws, f"A{r}", _xt("FV PEER = utile ammesso anno 1 x P/E mediano / azioni"), bold=True)
            ws[f"B{r}"] = f"='DDM regolato'!C16*{_med_cell}/B6"
            ws[f"B{r}"].number_format = "#,##0.00"
            _fv_peer_cell = f"B{r}"   # review V7 codice (C4): il Summary lo referenzia VIVO
        else:
            L(ws, f"A{r}", _xt("FV peer n.d.: utile ammesso non calcolabile (v. foglio DDM)"),
              color=GREYTX)
    else:
        L(ws, f"A{r}", _xt("Nessun peer con P/E in banda: cross-check n.d. DICHIARATO"), color=GREYTX)
    ws.column_dimensions["A"].width = 42
    for c in "BCD":
        ws.column_dimensions[c].width = 12
    ws.column_dimensions["E"].width = 60
    return _fv_peer_cell


def _sheet_summary_rab(wb, spec, fv, peer_cell=None):
    from bellomberg.valuation.dcf_bank import GREYTX, H, L, N, T, GOLD
    ws = wb.create_sheet("Summary")
    T(ws, "A1", _xt("Summary - fair value per metodo e blend (mediana)"))
    H(ws, "A3", _xt("METODO")); H(ws, "B3", _xt("FV/azione"))
    # review V7 codice (C2): il foglio replica il filtro del mirror (solo metodi
    # POSITIVI nella mediana) — un FV <= 0 scritto vivo in B4/B5 entrerebbe nella
    # MEDIAN di Excel divergendo dal payload proprio sugli input rotti.
    L(ws, "A4", _xt("EV/RAB premium"))
    _ev = fv.get("fair_value_ev_rab")
    if _ev is not None and _ev > 0:
        ws["B4"] = "='EV-RAB & Peers'!B10"; ws["B4"].number_format = "#,##0.00"
    else:
        L(ws, "B4", "n.d." if _ev is None else _xt("escluso: <=0 (v. attenzioni)"), color=GREYTX)
    L(ws, "A5", _xt("DDM regolato"))
    _dd = fv.get("fair_value_ddm_reg")
    if _dd is not None and _dd > 0:
        ws["B5"] = "='DDM regolato'!B22"; ws["B5"].number_format = "#,##0.00"
    else:
        L(ws, "B5", "n.d." if _dd is None else _xt("escluso: <=0 (v. attenzioni)"), color=GREYTX)
    L(ws, "A6", str(fv.get("peer_method") or "Peer"))
    _pe = fv.get("fair_value_peer")
    if _pe is not None and _pe > 0:
        if peer_cell:
            # review V7 codice (C4): riferimento VIVO al foglio peer, mai un
            # valore morto dentro una MEDIAN di formule vive
            ws["B6"] = f"='EV-RAB & Peers'!{peer_cell}"
            ws["B6"].number_format = "#,##0.00"
        else:
            N(ws, "B6", _pe, fmt="#,##0.00")
            L(ws, "C6", _xt("(valore mirror, NON ricalcola)"), italic=True, color=GREYTX)
    else:
        L(ws, "B6", "n.d.", color=GREYTX)
    L(ws, "A7", _xt("BLEND (mediana dei metodi)"), bold=True)
    if fv.get("fair_value_blend") is not None:
        # MEDIAN ignora le celle di testo/vuote: restano solo i metodi vivi >0
        ws["B7"] = "=MEDIAN(B4:B6)"
        ws["B7"].number_format = "#,##0.00"
        ws["B7"].font = Font(bold=True, color=GOLD, size=12)
    else:
        # review V7 codice (C5): niente MEDIAN su celle vuote (#NUM! in vetrina)
        L(ws, "B7", _xt("n.d. — nessun metodo disponibile (v. attenzioni)"), color=GREYTX)
    L(ws, "A9", _xt("Prezzo di mercato"))
    if spec.get("price"):
        N(ws, "B9", spec["price"], fmt="#,##0.00")
        if fv.get("fair_value_blend") is not None:
            L(ws, "A10", _xt("Upside/downside vs blend"))
            ws["B10"] = "=B7/B9-1"; ws["B10"].number_format = "0.0%"
    r = 12
    for w in (fv.get("warnings") or []):
        L(ws, f"A{r}", "! " + w, color=GREYTX); r += 1
    _blend_txt = ("%.2f" % fv["fair_value_blend"]) if fv.get("fair_value_blend") is not None else "n.d."
    L(ws, f"A{r+1}", _xt("Mirror Python (payload F17): blend %s, metodi %s") %
      (_blend_txt, "/".join(fv.get("blend_methods") or []) or "nessuno"),
      italic=True, color=GREYTX)
    ws.column_dimensions["A"].width = 70
    ws.column_dimensions["B"].width = 14


@scoped_language
def build_rab_model(spec, output_path, peers_data=None, peers_note=None):
    """Workbook rete regolata multi-foglio (V7 Lotto 2). Ritorna anche i fair
    value numerici del mirror Python (payload = fonte di verita', i fogli li
    riproducono a formule vive; parita' misurata nel collaudo sandbox)."""
    try:
        import openpyxl
    except ImportError:
        return {"ok": False, "error": _xt("openpyxl non disponibile")}
    import statistics as _st
    _pes = [p.get("pe") for p in (peers_data or []) if p.get("in_band") and p.get("pe")]
    pe_med = _st.median(_pes) if _pes else None
    fv = compute_fair_values_rab(spec, peer_pe_median=pe_med)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _sheet_thesis_rab(wb, spec, fv, peers_note=peers_note)
    _sheet_rab_rollforward(wb, spec)
    _sheet_ddm_regolato(wb, spec)
    _peer_cell = _sheet_ev_rab_peers(wb, spec, peers_data, peers_note=peers_note)
    _sheet_summary_rab(wb, spec, fv, peer_cell=_peer_cell)
    wb.calculation.fullCalcOnLoad = True   # cintura anti-anteprima-vuota (P0 17/07)
    try:
        wb.save(output_path)
    except Exception as e:
        return {"ok": False, "error": f"save: {e}", **fv}
    from datetime import datetime as _dt
    out = {"ok": True, "path": output_path, "engine": "rab",
           "payload_currency": spec.get("currency"),
           "method": _xt("EV/RAB premium + DDM su utile ammesso + peer P/E (motore RAB V7)"),
           "peers_used": [p.get("name") for p in (peers_data or [])],
           "peer_note": peers_note,
           "rab_base": spec.get("rab_base"),
           "allowed_return_calc": spec.get("allowed_return_calc"),
           "allowed_return_nominal": spec.get("allowed_return_nominal"),
           "service": spec.get("service"), "convention": spec.get("convention"),
           "anchor_stale": spec.get("anchor_stale"),
           "assumptions": {"rab_base": spec["rab_base"], "service": spec.get("service"),
                           "allowed_return_calc": spec["allowed_return_calc"],
                           "allowed_return_nominal": spec["allowed_return_nominal"],
                           "rab_premium": spec["rab_premium"], "payout": spec["payout"],
                           "ke": spec["ke"], "g": spec["growth_lt"],
                           "sources": spec["_sources"]},
           "_timestamp": _dt.now().isoformat()}
    out.update(fv)
    return out
