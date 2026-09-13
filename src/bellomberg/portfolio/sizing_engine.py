"""
sizing_engine.py (#184) — Motore di sizing & fattibilita' DETERMINISTICO.

BASE = CAPITALE INVESTITO (valore di mercato del book, cash ESCLUSO).
I pesi sono % dell'investito (come la dashboard: la posizione piu' grande resta al suo
20,8%, non diluita dal cash). Il cash e' un CUSCINETTO / dry powder separato, dispiegabile entro i
limiti ma NON spalmato nelle percentuali e nelle metriche.

Per ogni nome calcola:
  - size massima aggiustata per VOLATILITA' x CORRELAZIONE (% dell'investito)
  - capacita' residua (€) per aggiungere capitale SENZA sforare il limite
    (formula corretta: aggiungere d fa crescere anche il denominatore)
  - quanto TAGLIARE sui nomi sopra il limite
e dimensiona NUOVE idee candidate.

Nessun LLM: numeri auditabili. Il Capo riceve questo come contesto e dispiega il
cuscinetto di cash scegliendo tra mosse GIA' dimensionate entro il rischio.
Ispirato a Risk Manager + Portfolio Manager di virattt/ai-hedge-fund.
"""
from __future__ import annotations

# --- propensione al rischio: NON piu' qui (A2, criterio 5, 06/09) ---
# Fino a ieri i tetti stavano in questo file: baseline 10% / cap 12% single-stock, 30/33 sui
# veicoli, 30 di settore, pavimento 2%, soglia minima 500 EUR. Erano la propensione al rischio
# di UNA persona scritta nel codice di tutti: chi clonava il repo riceveva il dimensionamento
# costruito sul mandato di un altro (audit 26 §3). Ora vengono da `mandato_pm.sizing_params`,
# riletti DAL DISCO a ogni chiamata di `compute_sizing` — e senza mandato il motore NON
# dimensiona: dichiara (regola 14/07, mai i tetti di ieri come ripiego zitto).
# `tests/test_sizing_dal_mandato.py` lo prova con un controllo AST su questo file.
#
# Questi due RESTANO: non sono preferenze del PM, sono ripieghi del MOTORE quando manca la
# vol o la correlazione di un nome, e ogni riga che li usa lo dichiara (`vol_estimated`,
# `corr_estimated`).
DEFAULT_VOL  = 0.30    # vol annua di ripiego (dichiarata riga per riga)
DEFAULT_CORR = 0.50    # correlazione media di ripiego, neutra (dichiarata riga per riga)

# 05/09 (classificazione lotto 2): la CLASSE di sizing (veicolo diversificato: cap alto ed
# esente dal cap di settore / single-stock) e il SETTORE di policy vengono dal negozio dei
# veicoli (data/veicoli.json, campi classe_size e settore_policy), non piu' da due liste
# cablate col book del PM. VEHICLE_TICKERS e SECTOR_OF restano come VISTE calcolate
# all'import (set e dict: specialist_scores e scripts/pulizia_tesi_invalide fanno `set | ...`,
# portfolio_sectors legge SECTOR_OF); compute_sizing rilegge il negozio a ogni chiamata e
# scrive per ogni riga classe e provenienza. Un nome senza classe riceve lo STESSO limite
# prudente single-stock di ieri, ma etichettato come RIPIEGO e contato nel testo per il Capo.
import bellomberg.storage.classificazione as cl
# A2 (06/09): il mandato del PM. Solo stdlib, non ricalcola e non va in rete: importarlo qui
# non ha effetti collaterali, e `carica()` legge il file a OGNI chiamata (nessuna cache).
from bellomberg.core import mandato_pm


def veicoli_del_negozio(negozio=None) -> set:
    """I simboli con classe_size «veicolo» nel negozio (negozio assente/illeggibile = set
    vuoto: chi vuole il motivo legge `carica_veicoli()['origine']`)."""
    n = negozio if negozio is not None else cl.carica_veicoli()
    return {t for t, v in n["veicoli"].items() if v["classe_size"] == "veicolo"}


def settori_del_negozio(negozio=None) -> dict:
    """simbolo -> settore_policy dichiarato nel negozio (il cap di settore vale sui soli
    single-stock: lo applica compute_sizing per classe)."""
    n = negozio if negozio is not None else cl.carica_veicoli()
    return {t: v["settore_policy"] for t, v in n["veicoli"].items() if v["settore_policy"]}


VEHICLE_TICKERS = veicoli_del_negozio()
SECTOR_OF = settori_del_negozio()

# --- BUDGET VaR/STRESS (#187, 14/07) — dal MANDATO del PM da A2 (06/09) ---
# Budget sul NAV (investito + cash): "in un replay 2008 il NAV non puo' perdere piu' di X%";
# "il VaR99 1d non puo' superare Y% del NAV". Sono TETTI dichiarati dal PM (`stress_gfc_pct`,
# `var99_1g_pct` del mandato), non misure del book: il motore li confronta col valore misurato
# e dice SFORATO/OK. Se replay o VaR non sono disponibili il buco e' DICHIARATO e il vincolo
# NON viene applicato (regola no-fallback: niente numeri di ripiego zitti).


def _stress_var_budget(invested, cash, risk_data, stress_data, p):
    """Budget di rischio sulle code (#187): quanto capitale si puo' ancora
    dispiegare senza sforare i budget di stress/VaR sul NAV.

    Ipotesi DICHIARATA per la capacita' aggiuntiva: il capitale nuovo perde al
    tasso del book (replay/VaR correnti); il cash non investito non e' stressato.
    """
    nav = invested + max(0.0, cash)
    out = {"budget_gfc_replay_nav_pct": p["budget_stress_nav_pct"],
           "budget_var99_1d_nav_pct": p["budget_var99_nav_pct"],
           "assumption": "capitale nuovo perde al tasso del book; cash non stressato"}
    rooms = []

    # replay GFC 2008 (dal Monte Carlo: perdita deterministica della finestra)
    loss_pct = None
    sd = stress_data or {}
    if (sd.get("stress_scenario") == "gfc_2008" and not sd.get("stress_fallback")):
        loss_pct = (sd.get("stress_meta") or {}).get("window_loss_pct")
    if loss_pct is None:
        out["gfc_replay"] = "n.d. (replay 2008 non disponibile: buco dichiarato, vincolo NON applicato)"
    else:
        loss_nav_pct = loss_pct * invested / nav if nav > 0 else loss_pct
        out["gfc_replay_book_pct"] = loss_pct
        out["gfc_replay_nav_pct"] = round(loss_nav_pct, 2)
        # Audit 11/09 (Fable 5.1): il replay era presentato al Capo come il numero che
        # vincola tutto senza dire quanti nomi del book sono PROXY beta x SPY (storia
        # assente nella finestra 2008) e quanti giorni della finestra sono stati replicati.
        # Le misure esistono in stress_meta: si propagano, dichiarate.
        _meta = sd.get("stress_meta") or {}
        _real = _meta.get("real_history")
        _prox = _meta.get("proxied")
        try:
            out["gfc_n_real"] = len(_real) if _real is not None else None
            out["gfc_n_proxy"] = len(_prox) if _prox is not None else None
        except TypeError:
            out["gfc_n_real"], out["gfc_n_proxy"] = None, None
        _win = _meta.get("window") if isinstance(_meta.get("window"), dict) else {}
        out["gfc_window_days"] = _win.get("trading_days")
        out["gfc_replaced_days"] = _meta.get("replaced_days")
        _zf = _meta.get("zero_filled_days")
        out["gfc_zero_filled_names"] = len(_zf) if isinstance(_zf, dict) else None
        # review 14/07: il replay MC e' in base valuta LOCALE (dichiarato); per la
        # finestra GFC (USD in apprezzamento) sovrastima la perdita EUR = conservativo
        out["gfc_basis_note"] = "replay in valuta locale per-asset (dichiarato, direzione conservativa per GFC)"
        if loss_pct < 0:
            d_max = nav * (p["budget_stress_nav_pct"] / loss_pct) - invested
            rooms.append(max(0.0, d_max))
            out["gfc_room_eur"] = round(max(0.0, d_max), 0)
        out["gfc_status"] = "SFORATO" if loss_nav_pct < p["budget_stress_nav_pct"] else "OK"

    # VaR99 1d (dal risk engine: storico, VaR ufficiale)
    v99 = ((risk_data or {}).get("portfolio") or {}).get("var_99_1d_pct")
    if v99 is None:
        out["var99"] = "n.d. (risk engine non disponibile: buco dichiarato, vincolo NON applicato)"
    else:
        v99_nav = v99 * invested / nav if nav > 0 else v99
        out["var99_1d_book_pct"] = v99
        out["var99_1d_nav_pct"] = round(v99_nav, 2)
        if v99 < 0:
            d_max = nav * (p["budget_var99_nav_pct"] / v99) - invested
            rooms.append(max(0.0, d_max))
            out["var99_room_eur"] = round(max(0.0, d_max), 0)
        out["var99_status"] = "SFORATO" if v99_nav < p["budget_var99_nav_pct"] else "OK"

    out["additional_capacity_eur"] = round(min(rooms), 0) if rooms else None
    return out


def _is_vehicle(ticker) -> bool:
    """Vista all'import (VEHICLE_TICKERS), per i consumatori esterni; il sizing classifica
    per riga con `_classifica`, che porta la provenienza."""
    return (ticker or "").upper() in VEHICLE_TICKERS


def _sector_of(ticker):
    return SECTOR_OF.get((ticker or "").upper())


def _limits_for_classe(classe, p):
    """(base, cap) per CLASSE di sizing dal MANDATO del PM: veicolo diversificato vs
    single-stock. `p` non ha default DI PROPOSITO: un punto di chiamata dimenticato
    dev'essere un TypeError subito, non un tetto di ripiego applicato in silenzio."""
    if classe == "veicolo":
        return p["base_veicolo"], p["cap_veicolo"]
    if classe == "single":
        return p["base_single"], p["cap_single"]
    raise ValueError("classe di sizing %r fuori vocabolario %s" % (classe, cl.CLASSI_SIZE))


def _classifica(ticker, negozio, p) -> cl.Etichetta:
    """La classe di sizing del simbolo CON provenienza: dal negozio (registro_pm) oppure,
    se il negozio non la dice (voce assente, voce senza classe, negozio rotto), il limite
    prudente come RIPIEGO dichiarato. Il regime settoriale resta single-stock, ma base
    e cap sono il minimo fra le classi: un mandato puo' avere tetti invertiti."""
    e = cl.classe_size(ticker, negozio)
    if e.valore is not None:
        return e
    return cl.ripiego("classe_size", "single",
                      "%s: regime prudente single-stock, base e cap al minimo fra le classi "
                      "(base %.2f%%, cap %.0f%% dell'investito; il cap prevale sul pavimento)"
                      % (e.evidenza, min(p["base_single"], p["base_veicolo"]) * 100,
                         min(p["cap_single"], p["cap_veicolo"]) * 100))


def _limits_for_etichetta(et, p):
    if et.fonte == "ripiego":
        return (min(p["base_single"], p["base_veicolo"]),
                min(p["cap_single"], p["cap_veicolo"]))
    return _limits_for_classe(et.valore, p)


def _limits_for(ticker, p, negozio=None):
    """(base, cap) per simbolo: wrapper sulla classe dal negozio (o ripiego single)."""
    return _limits_for_etichetta(_classifica(ticker, negozio, p), p)


def _settore_di_policy(ticker, negozio):
    """(settore_policy | None, provenienza) dalla voce del negozio: il cap di settore vincola
    solo i nomi che lo dichiarano; l'assenza e' detta, non taciuta."""
    v = cl.voce(ticker, negozio)
    if v is not None and v["settore_policy"]:
        return v["settore_policy"], str(cl.etichetta(
            "settore_policy", v["settore_policy"], "registro_pm",
            "voce %s del negozio dei veicoli: settore_policy dichiarato" % ticker,
            verificato_il=v["verificato_il"]))
    if negozio["origine"] in ("assente", "illeggibile"):
        return None, ("negozio dei veicoli %s: nessun cap settoriale vincola questo nome "
                      "(dichiarato)" % negozio["origine"])
    return None, "nessun cap settoriale vincola questo nome (dichiarato: settore_policy non nel negozio)"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _vol_multiplier(vol_annual: float) -> float:
    """vol_annual in frazione (0.30 = 30%). Piu' vol -> limite piu' basso."""
    if vol_annual < 0.15:
        return 1.30
    if vol_annual < 0.25:
        return 1.30 - (vol_annual - 0.15) / 0.10 * 0.30   # 1.30 -> 1.00
    if vol_annual < 0.40:
        return 1.00 - (vol_annual - 0.25) / 0.15 * 0.40   # 1.00 -> 0.60
    if vol_annual < 0.60:
        return 0.60 - (vol_annual - 0.40) / 0.20 * 0.20   # 0.60 -> 0.40
    return 0.35


def _corr_multiplier(avg_corr: float) -> float:
    if avg_corr >= 0.80:
        return 0.70
    if avg_corr >= 0.60:
        return 0.85
    if avg_corr >= 0.40:
        return 1.00
    if avg_corr >= 0.20:
        return 1.05
    return 1.10


def _build_corr_lookup(risk_data):
    corr = {}
    try:
        c = (risk_data or {}).get("correlation") or {}
        tks = c.get("tickers") or []
        M = c.get("matrix") or []
        for i, ti in enumerate(tks):
            for j, tj in enumerate(tks):
                if i < len(M) and j < len(M[i]):
                    corr.setdefault(ti, {})[tj] = M[i][j]
    except Exception:
        corr = {}

    def avg_corr(ticker, others):
        row = corr.get(ticker)
        if not row:
            return None
        vals = [row[o] for o in others if o in row and o != ticker and row[o] is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    return avg_corr


def _vol_of(ticker, risk_data):
    try:
        pa = (risk_data or {}).get("per_asset") or {}
        v = pa.get(ticker, {}).get("vol_annual_pct")
        if v is not None:
            return float(v) / 100.0
    except Exception:
        pass
    return None


def compute_sizing(portfolio_data, risk_data=None, candidates=None, stress_data=None,
                   negozio=None, parametri=None):
    """
    BASE = capitale investito (Σ valore di mercato delle posizioni). Cash escluso
    dai pesi, trattato come cuscinetto dispiegabile.
    stress_data (#187): payload del Monte Carlo con replay gfc_2008 — alimenta il
    budget VaR/stress che puo' RIDURRE la capacita' di aggiunta (dichiarato).
    negozio (05/09): esito di classificazione.carica_veicoli; None = riletto ora. Ogni riga
    (posizioni E candidati) porta class / class_source / class_fallback, e le posizioni
    sector_policy / sector_source; il summary conta i non classificati (`unclassified`,
    `classification_note`). I numeri non cambiano: il ripiego e' il limite di ieri.
    """
    # A2 (06/09): i tetti vengono dal MANDATO del PM, riletto dal disco a OGNI chiamata
    # (come il negozio). Senza mandato il motore NON dimensiona: dichiara e si ferma QUI,
    # prima di ogni calcolo — mai i tetti di ieri come ripiego zitto (regola 14/07).
    if parametri is None:
        try:
            par = mandato_pm.sizing_params(mandato_pm.carica())
        except mandato_pm.MandatoMancante as e:
            return {"error": "mandato non dichiarato: %s" % e}
    else:
        par = parametri

    pd = portfolio_data or {}
    positions = pd.get("positions") or []
    if pd.get("fx_incomplete"):
        return {"error": "FX incompleto: sizing su valori EUR n.d. (" +
                ", ".join(pd["fx_incomplete"]) + ")"}
    cash = float(pd.get("cash_disponibile_eur") or 0.0)
    n = negozio if negozio is not None else cl.carica_veicoli()

    invested = 0.0
    for p in positions:
        invested += float(p.get("valore_mercato_eur") or p.get("valore_mercato") or 0.0)
    if invested <= 0:
        return {"error": "capitale investito non disponibile"}

    # soglia sotto cui un taglio o uno spazio non vale una mossa: erano 500 EUR CABLATI,
    # ora e' `posizione_minima_pct` del NAV, cioe' SEGUE il patrimonio invece di restare
    # ferma mentre il book cresce. NAV = investito + cassa, come nei budget di coda.
    soglia_minima = (invested + max(0.0, cash)) * par["posizione_minima_pct"] / 100.0

    book_tickers = [p.get("ticker") for p in positions if p.get("ticker")]
    avg_corr_fn = _build_corr_lookup(risk_data)

    per_position = []
    total_room = 0.0
    total_trim = 0.0
    over_limit = []

    for p in positions:
        tk = p.get("ticker")
        cur_eur = float(p.get("valore_mercato_eur") or p.get("valore_mercato") or 0.0)
        cur_pct = cur_eur / invested * 100.0  # % dell'INVESTITO (come dashboard)

        vol = _vol_of(tk, risk_data)
        vol_used = vol if vol is not None else DEFAULT_VOL
        ac = avg_corr_fn(tk, book_tickers)
        ac_used = ac if ac is not None else DEFAULT_CORR

        vmul = _vol_multiplier(vol_used)
        cmul = _corr_multiplier(ac_used)
        et = _classifica(tk, n, par)
        _base, _cap = _limits_for_etichetta(et, par)
        L = _clamp(_base * vmul * cmul, min(par["limite_minimo"], _cap), _cap)
        max_eur_now = L * invested  # € che il limite rappresenta sull'investito attuale
        sp, sp_src = _settore_di_policy(tk, n)

        # capacita' di AGGIUNTA: aggiungere d fa crescere anche il denominatore
        #   (v+d)/(I+d) <= L  ->  d <= (L*I - v)/(1 - L)
        if L < 1.0 and cur_eur < L * invested:
            room_eur = (L * invested - cur_eur) / (1.0 - L)
        else:
            room_eur = 0.0
        # TAGLIO se sopra: (v-t)/(I-t) <= L  ->  t >= (v - L*I)/(1 - L)
        if cur_eur > L * invested and L < 1.0:
            trim_eur = (cur_eur - L * invested) / (1.0 - L)
        else:
            trim_eur = 0.0

        total_room += room_eur
        total_trim += trim_eur

        if trim_eur > soglia_minima:
            verdict = "TAGLIARE ~{:,.0f}€ (sopra il limite)".format(trim_eur)
            over_limit.append(tk)
        elif room_eur > soglia_minima:
            verdict = "spazio +{:,.0f}€ entro il limite".format(room_eur)
        else:
            verdict = "in linea col limite"

        per_position.append({
            "ticker": tk,
            "current_eur": round(cur_eur, 0),
            "current_pct": round(cur_pct, 2),
            "vol_annual_pct": round(vol_used * 100, 1),
            "vol_estimated": vol is None,
            "avg_corr_book": round(ac_used, 2),
            "corr_estimated": ac is None,
            "vol_multiplier": round(vmul, 2),
            "corr_multiplier": round(cmul, 2),
            "max_position_pct": round(L * 100, 2),
            "max_position_eur": round(max_eur_now, 0),
            "remaining_capacity_eur": round(room_eur, 0),
            "trim_eur": round(trim_eur, 0),
            "verdict": verdict,
            # 05/09: la classe con la sua provenienza (mai un limite applicato in silenzio)
            "class": et.valore,
            "class_source": str(et),
            "class_fallback": et.fonte == "ripiego",
            "sector_policy": sp,
            "sector_source": sp_src,
        })

    # --- CAP DI SETTORE (25/06): la somma dei SINGLE-STOCK dello stesso settore non
    # supera il cap di settore DEL MANDATO. Veicoli (ETF/holding) esenti. Riduce solo la capacita' di
    # AGGIUNTA (room), non forza tagli sulle posizioni esistenti. (05/09: settore e
    # classe dalla riga, cioe' dal negozio; un nome a ripiego e' single anche qui.)
    _sec_cur = {}
    for pp in per_position:
        sec = pp["sector_policy"]
        if sec and pp["class"] == "single":
            _sec_cur[sec] = _sec_cur.get(sec, 0.0) + pp["current_eur"]
    _sec_room = {sec: max(0.0, par["cap_settore"] * invested - cur) for sec, cur in _sec_cur.items()}
    sector_capped = []
    _new_total_room = 0.0
    for pp in per_position:
        sec = pp["sector_policy"]
        if sec and pp["class"] == "single" and pp["remaining_capacity_eur"] > 0:
            avail = _sec_room.get(sec, 0.0)
            capped = min(pp["remaining_capacity_eur"], avail)
            _sec_room[sec] = max(0.0, avail - max(0.0, capped))
            if capped < pp["remaining_capacity_eur"] - 1:
                pp["remaining_capacity_eur"] = round(capped, 0)
                if capped > soglia_minima:
                    pp["verdict"] = "spazio +{:,.0f} EUR (cap settore {} {:.0f}%)".format(capped, sec, par["cap_settore"] * 100)
                else:
                    pp["verdict"] = "in linea (settore {} al cap {:.0f}%)".format(sec, par["cap_settore"] * 100)
                if sec not in sector_capped:
                    sector_capped.append(sec)
        _new_total_room += max(0.0, pp["remaining_capacity_eur"])
    total_room = _new_total_room

    # --- BUDGET VaR/STRESS (#187): se il budget sulle code lascia meno spazio
    # dei limiti vol x corr, la capacita' di aggiunta si riduce (dichiarato).
    budget = _stress_var_budget(invested, cash, risk_data, stress_data, par)
    _bud_cap = budget.get("additional_capacity_eur")
    budget["binding"] = False
    if _bud_cap is not None and _bud_cap < total_room:
        budget["binding"] = True
        _scale = (_bud_cap / total_room) if total_room > 0 else 0.0
        for pp in per_position:
            if pp["remaining_capacity_eur"] > 0:
                pp["remaining_capacity_eur"] = round(pp["remaining_capacity_eur"] * _scale, 0)
        budget["room_before_budget_eur"] = round(total_room, 0)
        total_room = _bud_cap

    deployable_from_cash = min(cash, total_room)
    cash_pct_of_invested = cash / invested * 100.0

    cand_out = []
    for c in (candidates or []):
        tk = c.get("ticker")
        vol = c.get("vol_annual_pct")
        vol_used = float(vol) / 100.0 if vol is not None else DEFAULT_VOL
        ac = c.get("avg_corr")
        ac_used = float(ac) if ac is not None else DEFAULT_CORR
        vmul = _vol_multiplier(vol_used)
        cmul = _corr_multiplier(ac_used)
        et = _classifica(tk, n, par)
        _base, _cap = _limits_for_etichetta(et, par)
        L = _clamp(_base * vmul * cmul, min(par["limite_minimo"], _cap), _cap)
        # nuovo nome: d/(I+d) <= L -> d <= L*I/(1-L)
        max_add = (L * invested) / (1.0 - L) if L < 1.0 else cash
        starter = min(max_add * 0.5, cash)
        cand_out.append({
            "ticker": tk,
            "vol_annual_pct": round(vol_used * 100, 1),
            "vol_estimated": vol is None,
            "avg_corr_assumed": round(ac_used, 2),
            "max_position_pct": round(L * 100, 2),
            "max_add_eur": round(max_add, 0),
            "suggested_starter_eur": round(starter, 0),
            "class": et.valore,
            "class_source": str(et),
            "class_fallback": et.fonte == "ripiego",
        })

    # 05/09: quanti (e quali) nomi del book non hanno una classe dichiarata. Negozio
    # assente/illeggibile = TUTTI a ripiego, e la nota dice origine e motivo.
    _non_class = sorted(pp["ticker"] for pp in per_position if pp["class_fallback"])
    if n["origine"] in ("assente", "illeggibile"):
        # i tagli/spazi restano numeri (niente nascosto), ma il Capo deve leggere che il
        # limite applicato NON e' una policy del PM (review 05/09: un file rotto faceva
        # comparire un veicolo in «SOPRA IL LIMITE (valuta taglio)»)
        _class_note = ("negozio dei veicoli %s (%s): %d nomi su %d senza classe di sizing, "
                       "limite prudente single-stock (cap %.0f%%) applicato per ripiego — i tagli "
                       "e gli spazi di questi nomi NON sono policy: %s prima di agire"
                       % (n["origine"], n["motivo"], len(_non_class), len(per_position),
                          min(par["cap_single"], par["cap_veicolo"]) * 100,
                          "correggi il file" if n["origine"] == "illeggibile"
                          else "dichiara i veicoli nel negozio"))
    else:
        _class_note = ("%d nomi su %d senza classe di sizing nel negozio dei veicoli: limite "
                       "prudente single-stock (cap %.0f%%) applicato per ripiego%s"
                       % (len(_non_class), len(per_position), min(par["cap_single"], par["cap_veicolo"]) * 100,
                          (": " + ", ".join(_non_class)) if _non_class else ""))

    summary = {
        "invested_capital_eur": round(invested, 0),
        "cash_buffer_eur": round(cash, 0),
        "cash_pct_of_invested": round(cash_pct_of_invested, 1),
        "n_positions": len(positions),
        "total_room_existing_eur": round(total_room, 0),
        "deployable_from_cash_eur": round(deployable_from_cash, 0),
        "total_trim_eur": round(total_trim, 0),
        "names_over_limit": over_limit,
        "n_names_with_room": sum(1 for x in per_position if x["remaining_capacity_eur"] > soglia_minima),
        "params": {"single_base_pct": par["base_single"] * 100, "single_cap_pct": par["cap_single"] * 100,
                   "vehicle_base_pct": par["base_veicolo"] * 100, "vehicle_cap_pct": par["cap_veicolo"] * 100,
                   "sector_cap_pct": par["cap_settore"] * 100,
                   "stress_gfc_nav_budget_pct": par["budget_stress_nav_pct"],
                   "var99_nav_budget_pct": par["budget_var99_nav_pct"],
                   # A2 (06/09): chiavi NUOVE, dichiarate in CHIAVI_NUOVE_PARAMS della prova A
                   "posizione_minima_pct": par["posizione_minima_pct"],
                   "posizione_minima_eur": round(soglia_minima, 0),
                   "impronta": par.get("impronta")},
        "sectors_at_cap": sector_capped,
        "stress_var_budget": budget,
        "unclassified": _non_class,
        "classification_note": _class_note,
    }

    return {"summary": summary, "positions": per_position, "candidates": cand_out}


def _params_di_esempio():
    """I parametri del PROFILO DI ESEMPIO, per la demo del `__main__`.

    Sta in una funzione e non nel blocco `if __name__` DI PROPOSITO: il controllo AST di
    `tests/test_mandato_pm.py` vieta di chiamare `mandato_pm` a livello di modulo, e il blocco
    `__main__` e' livello di modulo per l'AST anche se all'import non gira. Meglio spostare la
    chiamata che allentare la guardia — quella guardia esiste perche' un mandato congelato
    all'import non seguirebbe piu' il file quando il PM lo cambia.
    """
    return mandato_pm.sizing_params(mandato_pm.profilo_esempio())


def format_for_capo(sizing: dict) -> str:
    if not sizing or sizing.get("error"):
        return ""
    s = sizing["summary"]
    # A2 (06/09): i numeri della policy vengono dai `params` del sommario — cioe' dal MANDATO
    # del PM che ha prodotto QUESTO sizing — non da costanti del modulo. Si chiama `par` e non
    # `p` perche' piu' sotto `p` e' la posizione del ciclo di stampa.
    par = s.get("params") or {}
    L = []
    L.append("=== MOTORE DI SIZING (limiti vol x correlazione, base = capitale INVESTITO) ===")
    L.append("NOTA: i pesi sono % dell'investito (cash ESCLUSO). Il cash e' un cuscinetto "
             "dispiegabile, non incluso nei pesi ne' nelle metriche di rischio/rendimento.")
    # A2 (06/09): la riga POLICY resta IDENTICA a ieri parola per parola — cambiano solo le
    # FONTI dei tre numeri (dal mandato, non da costanti). Il vincolo del PM e' che il testo
    # dei prompt non si muova senza una sua decisione, e la prova A ammette righe NUOVE o
    # suffissi appesi, mai righe cambiate: la provenienza va quindi su una riga a se'.
    L.append("POLICY (bande di riferimento, non gabbia): single-stock {:.0f}% | veicoli/ETF/holding {:.0f}% | settore {:.0f}% (sui single-stock).".format(
        par.get("single_cap_pct", 0), par.get("vehicle_cap_pct", 0), par.get("sector_cap_pct", 0)))
    _riga_mandato = ("Questi tetti sono DICHIARATI dal PM nel suo mandato (impronta {}), non regole "
                     "del programma; sotto {:,.0f} EUR ({}% del NAV) un taglio o uno spazio non vale "
                     "una mossa.".format(par.get("impronta", "n.d."),
                                         par.get("posizione_minima_eur", 0),
                                         par.get("posizione_minima_pct", "n.d.")))
    # 05/09 (classificazione lotto 2): quanti nomi hanno una classe dichiarata dal PM e quanti
    # ricevono il limite prudente per ripiego — in testa, cosi' il Capo sa quali limiti sono
    # policy e quali sono un buco del negozio dei veicoli.
    _nv = sum(1 for p in sizing["positions"] if p.get("class") == "veicolo")
    _ns = sum(1 for p in sizing["positions"]
              if p.get("class") == "single" and not p.get("class_fallback"))
    _nc = s.get("unclassified") or []
    _riga_cl = "CLASSIFICAZIONE: {} VEICOLO (negozio PM), {} azione singola, {} NON CLASSIFICATI".format(
        _nv, _ns, len(_nc))
    if _nc:
        _riga_cl += " — limite prudente single-stock {:.0f}% per ripiego: {}".format(
            par.get("single_cap_pct", 0), ", ".join(_nc))
    _nota = s.get("classification_note") or ""
    if _nota.startswith("negozio dei veicoli"):
        _riga_cl += " | " + _nota
    L.append(_riga_cl)
    # la provenienza dei tetti va DOPO la sintesi della classificazione: quella deve restare
    # attaccata alla POLICY (lotto 2 del 05/09, provato da test_sizing_classe.py)
    L.append(_riga_mandato)
    b = s.get("stress_var_budget") or {}
    if b:
        if b.get("gfc_replay_nav_pct") is not None:
            # audit 11/09: la base del replay (nomi veri vs proxy, giorni replicati) va detta
            # accanto al numero, altrimenti la perdita sembra misurata su storie tutte vere
            _base = ""
            if b.get("gfc_n_proxy") is not None and b.get("gfc_n_real") is not None:
                _base = " — base: {} nomi con storia 2008 vera, {} PROXY beta x SPY".format(
                    b["gfc_n_real"], b["gfc_n_proxy"])
                if b.get("gfc_replaced_days") is not None and b.get("gfc_window_days") is not None:
                    _base += ", finestra replicata {}/{} giorni".format(
                        b["gfc_replaced_days"], b["gfc_window_days"])
                if b.get("gfc_zero_filled_names"):
                    _base += ", {} nomi con buchi riempiti a 0".format(b["gfc_zero_filled_names"])
            L.append("BUDGET STRESS (#187, sul NAV): replay GFC 2008 = {:.1f}% del NAV "
                     "(budget {:.0f}%, {}; base valuta locale dichiarata, conservativa){} "
                     "[src: get_portfolio_montecarlo replay]".format(
                         b["gfc_replay_nav_pct"], b["budget_gfc_replay_nav_pct"],
                         b.get("gfc_status", "?"), _base))
        else:
            L.append("BUDGET STRESS: " + str(b.get("gfc_replay", "n.d.")))
        if b.get("var99_1d_nav_pct") is not None:
            L.append("BUDGET VaR: VaR99 1d = {:.2f}% del NAV (budget {:.0f}%, {}) "
                     "[src: portfolio_risk storico]".format(
                         b["var99_1d_nav_pct"], b["budget_var99_1d_nav_pct"],
                         b.get("var99_status", "?")))
        else:
            L.append("BUDGET VaR: " + str(b.get("var99", "n.d.")))
        if b.get("binding"):
            L.append("!! IL BUDGET DI RISCHIO VINCOLA IL SIZING: capacita' di aggiunta ridotta "
                     "da {:,.0f}€ a {:,.0f}€ (ipotesi dichiarata: il capitale nuovo perde al "
                     "tasso del book).".format(b.get("room_before_budget_eur", 0),
                                               b.get("additional_capacity_eur", 0)))
    if s.get("sectors_at_cap"):
        L.append("SETTORI AL CAP (niente nuove aggiunte single-stock): " + ", ".join(s["sectors_at_cap"]))
    L.append(
        "Investito {:,.0f}€ | cuscinetto cash {:,.0f}€ ({:.0f}% dell'investito) | "
        "capacita' residua entro i limiti {:,.0f}€ | cash dispiegabile negli esistenti {:,.0f}€".format(
            s["invested_capital_eur"], s["cash_buffer_eur"], s["cash_pct_of_invested"],
            s["total_room_existing_eur"], s["deployable_from_cash_eur"]))
    if s["names_over_limit"]:
        L.append("SOPRA IL LIMITE (valuta taglio): " + ", ".join(s["names_over_limit"]))
    _soglia = par.get("posizione_minima_eur", 0)
    L.append("Per nome (peso ora | vol | size max | capacita'/taglio):")
    for p in sizing["positions"]:
        if p["trim_eur"] > _soglia:
            tag = "TAGLIA ~{:,.0f}€".format(p["trim_eur"])
        elif p["remaining_capacity_eur"] > _soglia:
            tag = "+{:,.0f}€ di spazio".format(p["remaining_capacity_eur"])
        else:
            tag = "in linea"
        est = " (vol stim.)" if p.get("vol_estimated") else ""
        # 05/09: correlazione stimata e classe a ripiego in CODA alla riga (suffissi: la prova
        # A del prima/dopo ammette solo righe nuove o suffissi appesi — la maggior parte delle
        # righe del book ha la correlazione stimata, in mezzo alla riga sarebbero righe cambiate)
        coda = ((" (corr stim.)" if p.get("corr_estimated") else "")
                + (" | classe n.d. (ripiego)" if p.get("class_fallback") else ""))
        L.append("  {:<10} ora {:>5.1f}% | vol {:>4.1f}%{} | max {:>4.1f}% | {}{}".format(
            p["ticker"], p["current_pct"], p["vol_annual_pct"], est, p["max_position_pct"], tag, coda))
    if sizing.get("candidates"):
        L.append("Nuove idee dimensionate (starter prudente):")
        for c in sizing["candidates"]:
            L.append("  {:<10} max {:.1f}% dell'investito | max aggiunta {:,.0f}€ | starter ~{:,.0f}€{}".format(
                c["ticker"], c["max_position_pct"], c["max_add_eur"], c["suggested_starter_eur"],
                " | classe n.d. (ripiego)" if c.get("class_fallback") else ""))
    L.append("POLICY D'USO (15/07, scelta PM): di default dispiega il cash entro queste size "
             "massime (% dell'investito), preferendo vol/correlazione basse. Le DEROGHE sono "
             "consentite SOLO dichiarate nella riga dell'ACTION TABLE ('SOPRA POLICY: +X pt' + "
             "motivazione quantificata): mai sforare in silenzio.")
    return "\n".join(L)


if __name__ == "__main__":
    # mock realistico sui simboli dell'ESEMPIO TRACCIATO dei veicoli (05/09, lotto 5b: prima
    # c'erano dieci simboli del book), stessi pesi: il fondo chiuso ~20,8% dell'INVESTITO
    # (come dashboard), cash separato. La classe di sizing viene dall'esempio, passato come
    # negozio: due simboli senza classe mostrano il ripiego single ETICHETTATO.
    positions = [
        {"ticker": "FONDO.L",   "valore_mercato_eur": 30400},
        {"ticker": "ACME.MI", "valore_mercato_eur": 13300},
        {"ticker": "GAMMA",    "valore_mercato_eur": 11100},
        {"ticker": "ETFX.MI",  "valore_mercato_eur": 9800},
        {"ticker": "NOTA.FRA", "valore_mercato_eur": 8900},
        {"ticker": "TESORO",    "valore_mercato_eur": 5800},
        {"ticker": "BETA.DE",  "valore_mercato_eur": 5600},
        {"ticker": "DEDOTTO","valore_mercato_eur": 4600},
        {"ticker": "IGNOTO.X",   "valore_mercato_eur": 4100},
        {"ticker": "METALLO",     "valore_mercato_eur": 52500},
    ]
    pdj = {"positions": positions, "cash_disponibile_eur": 125000}
    risk = {"per_asset": {"FONDO.L": {"vol_annual_pct": 14}, "ACME.MI": {"vol_annual_pct": 38},
        "GAMMA": {"vol_annual_pct": 52}, "ETFX.MI": {"vol_annual_pct": 28}, "NOTA.FRA": {"vol_annual_pct": 33},
        "TESORO": {"vol_annual_pct": 78}, "BETA.DE": {"vol_annual_pct": 30}, "DEDOTTO": {"vol_annual_pct": 40},
        "IGNOTO.X": {"vol_annual_pct": 34}, "METALLO": {"vol_annual_pct": 13}},
        "correlation": {"tickers": ["GAMMA", "ETFX.MI", "NOTA.FRA", "TESORO", "DEDOTTO"],
            "matrix": [[1,.55,.35,.40,.50],[.55,1,.45,.30,.62],[.35,.45,1,.25,.38],
                       [.40,.30,.25,1,.28],[.50,.62,.38,.28,1]]}}
    risk["portfolio"] = {"var_99_1d_pct": -2.8}
    stress_mock = {"stress_scenario": "gfc_2008", "stress_fallback": False,
                   "stress_meta": {"window_loss_pct": -32.0, "window_loss_eur": -47000}}
    # A2 (06/09): parametri dal PROFILO DI ESEMPIO passati esplicitamente, non dal mandato di
    # chi lancia — cosi' la demo gira anche su un clone senza `data/` (dove il mandato non
    # esiste e compute_sizing dichiarerebbe, giustamente, «mandato non dichiarato») e non
    # stampa mai i tetti veri di nessuno.
    out = compute_sizing(pdj, risk, [{"ticker": "IGNOTO.X", "vol_annual_pct": 34}],
                         stress_data=stress_mock,
                         negozio=cl.carica_veicoli(cl.ESEMPIO_VEICOLI),
                         parametri=_params_di_esempio())
    import json
    print(json.dumps(out["summary"], indent=2)); print()
    print(format_for_capo(out))
