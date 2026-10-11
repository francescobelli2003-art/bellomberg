"""
specialist_scores.py (#186) — Scorer DETERMINISTICI per gli specialisti.

Pattern ispirato a virattt/ai-hedge-fund: il NUMERO sta nel codice (rubric a punti
con soglie esplicite, riproducibile e auditabile), l'LLM NARRA partendo dallo score.
Meno prompt, piu' codice. Ogni scorer ritorna {score, max_score, verdict, lines, metrics}
ed e' guarded: se i dati mancano ritorna None e lo specialista lavora come prima.

Convenzione 'risk score': PIU' ALTO = PIU' RISCHIO.
"""
from __future__ import annotations
from bellomberg.core.language import scoped_language
from bellomberg.reporting.i18n import label as _t

import bellomberg.storage.classificazione as cl
from bellomberg.core.paths import REPORT_DIR
# 10/10 (Opus 5.5): UNA sola fonte per le soglie condivise e calibrate (bande del cruscotto,
# VIX/VIX3M, prezzo della protezione, skew, freschezza, macro STRESS, funding, MOS, pavimenti)
from bellomberg.core import soglie_score as _S
import math
from numbers import Real


def _finite_number(value):
    """Un dato invalido e' assente, non una fascia di rischio (NaN confronta falso)."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value) if math.isfinite(value) else None


def _band(value, thresholds, points, reverse=False):
    """thresholds crescenti; ritorna i punti del primo bucket che contiene value.
    reverse=True per metriche dove ALTO=buono (es. Sharpe): si confronta al contrario."""
    value = _finite_number(value)
    if value is None:
        return None
    if not reverse:
        for th, pt in zip(thresholds, points):
            if value <= th:
                return pt
        return points[-1]
    else:
        for th, pt in zip(thresholds, points):
            if value >= th:
                return pt
        return points[-1]


def _interp(value, anchors, points=(0.0, 1.0, 2.0, 3.0)):
    """Punteggio CONTINUO (fix score 09/10, Opus 5.5): interpolazione lineare fra ancore
    crescenti, saturata agli estremi. Niente gradini: 0,01 in piu' sull'input sposta il
    punteggio di una frazione, non di un punto intero (il vecchio _band a scaglioni faceva
    BASSO->MEDIO per 0,01 di vol proprio sul target del PM). None se il dato e' invalido."""
    value = _finite_number(value)
    if value is None:
        return None
    if value <= anchors[0]:
        return float(points[0])
    for (a0, p0), (a1, p1) in zip(zip(anchors, points), zip(anchors[1:], points[1:])):
        if value <= a1:
            return round(p0 + (p1 - p0) * (value - a0) / (a1 - a0), 2)
    return float(points[-1])


def codice_guardrail_beta(beta_reconcile) -> str:
    """Codice STABILE del guardrail beta (uguale in ogni lingua) dal payload di
    advanced_metrics.reconcile_betas. Funzione pura: non calcola nulla, legge il payload.
    La usano quant_score (metrics.beta_guardrail) e la rotta /portfolio/metrics/beta_reconcile.
    RECONCILED solo con via libera (beta_per_decisioni True) e motore portfolio_risk_spy fra i
    riconciliati (review R-SEG 06/10, C1: la beta del punteggio e' QUELLA di portfolio_risk)."""
    rb = beta_reconcile
    fonte = "portfolio_risk_spy"
    if isinstance(rb, dict) and rb.get("error"):
        # un errore del motore vince SEMPRE, anche su un RECONCILED con via libera (main 06/10)
        return "NON_DISPONIBILE"
    if isinstance(rb, dict) and rb.get("verdict") == "RECONCILED" and rb.get("beta_per_decisioni") is True:
        return "RECONCILED" if fonte in (rb.get("betas") or {}) else "RECONCILED_SENZA_FONTE_RISCHIO"
    if rb is None:
        # review R-SEG 06/10 (C2): nessun guasto, il chiamante non l'ha calcolato
        return "NON_CALCOLATO"
    if isinstance(rb, dict) and rb.get("verdict"):   # `error` gia' escluso in testa
        verdetto = str(rb["verdict"])
        # payload incoerente: verdetto RECONCILED senza via libera
        return "RECONCILED_SENZA_VIA_LIBERA" if verdetto == "RECONCILED" else verdetto
    return "NON_DISPONIBILE"


def payload_rotta_beta_reconcile(threshold: float = 0.35) -> dict:
    """Payload di GET /portfolio/metrics/beta_reconcile: quello di reconcile_betas piu' il campo
    di primo livello `beta_guardrail` (stesso codice di quant_score, BG-ROTTA 06/10). Qui il
    calcolo si FA, quindi NON_CALCOLATO non esce mai: un guasto (eccezione, `error`, payload non
    valido o senza verdetto) e' NON_DISPONIBILE con `error` dichiarato."""
    from bellomberg.core.presentation import message as _message, error_text
    try:
        from bellomberg.portfolio.advanced_metrics import reconcile_betas
        rb = reconcile_betas(threshold=threshold)
    except Exception as e:
        return {"error": _message("reconcile_betas fallito ({tipo}: {motivo})",
                                  "reconcile_betas failed ({tipo}: {motivo})",
                                  tipo=type(e).__name__, motivo=error_text(e)),
                "beta_guardrail": "NON_DISPONIBILE"}
    if not isinstance(rb, dict):
        return {"error": _message("payload non valido ({tipo})", "invalid payload ({tipo})", tipo=type(rb).__name__),
                "beta_guardrail": "NON_DISPONIBILE"}
    if rb.get("error"):
        # l'errore del motore vince su ogni verdetto (anche un RECONCILED incoerente)
        return dict(rb, beta_guardrail="NON_DISPONIBILE")
    codice = codice_guardrail_beta(rb)
    if codice in ("NON_DISPONIBILE", "NON_CALCOLATO"):
        return dict(rb, beta_guardrail="NON_DISPONIBILE",
                    error=_message("payload senza verdetto", "payload without verdict"))
    return dict(rb, beta_guardrail=codice)


@scoped_language
def quant_score(portfolio_data=None, risk_data=None, beta_reconcile=None, *,
                stress_data=None, mandato=None, sector_data=None, negozio=None):
    """Rubric di RISCHIO del book. Ritorna dict o None se dati insufficienti.
    beta_reconcile: payload di advanced_metrics.reconcile_betas calcolato dal CHIAMANTE (la
    run lo calcola una volta nel priming). Decisione PM 06/10: la beta pesa SOLO con verdetto
    RECONCILED, via libera e motore portfolio_risk_spy fra i riconciliati; altrimenti ESCLUSA
    dal punteggio e lo si dichiara («beta esclusa: guardrail <verdetto>»). Qui non si calcola.

    Rifatto il 09/10 (Opus 5.5, decisione PM «correggi tutto», audit SCORE-VOL-QUANT §2):
    - UNA misura di dispersione: vol PREVISIONALE EWMA 0,94 rapportata al target di vol del
      mandato (vol storica 1a solo come ripiego ETICHETTATO). VaR95 storico, Sharpe e max DD
      trailing escono dal punteggio e restano INFORMATIVI: il VaR e' la stessa dispersione
      (x1,645/sqrt(252) in ipotesi normale), Sharpe e DD sono performance passata e rendevano
      il punteggio pro-ciclico (rischio basso dopo la corsa, alto dopo il crollo).
    - CODA: perdita del replay GFC 2008 sul NAV contro il budget di stress del mandato
      (`stress_data` = run_monte_carlo(stress_scenario='gfc_2008'), `mandato` = mandato_pm).
    - Beta di Dimson (+-1 seduta) se il payload la porta: book europeo vs SPY non sincroni.
    - CONCENTRAZIONE: primo nome singolo (veicoli diversificati esclusi via `negozio`), HHI,
      primo cluster economico (`sector_data` = compute_sector_exposure); pavimento: un nome
      singolo >= 50% o un cluster >= 70% portano il verdetto almeno a ELEVATO.
    - Punteggi CONTINUI (_interp): niente gradini attorno al target.
    Quello che il chiamante non passa e' DICHIARATO n.d. («non calcolato da questo percorso»),
    mai sostituito da un default.

    Seconda versione 10/10 (Opus 5.5, riserve della review): base NAV (cassa a vol 0) per pesi,
    vol, replay e cluster, con la base dichiarata e lettura sull'investito (piu' severa) quando
    il NAV non si calcola; HHI sui soli nomi singoli; Dimson pesa solo entro la soglia del
    guardrail dalla beta riconciliata; ancore di primo nome e cluster in multipli dei cap del
    mandato (cap_single_pct, cap_settore_pct); pavimento ELEVATO anche con replay/budget >= 1;
    FX incompleto = replay sul NAV n.d.; un valore di posizione n.d. non vale 0."""
    from bellomberg.core.presentation import message as _message
    if risk_data is None:
        try:
            from bellomberg.portfolio.portfolio_risk import compute_portfolio_risk
            risk_data = compute_portfolio_risk()
        except Exception:
            return None
    if not isinstance(risk_data, dict) or risk_data.get("error"):
        return None
    p = risk_data.get("portfolio") or {}
    vol_storica = _finite_number(p.get("vol_annual_pct"))
    vol_ewma = _finite_number(p.get("vol_ewma_annual_pct"))
    sharpe = _finite_number(p.get("sharpe"))
    beta_sincrona = _finite_number(p.get("beta_vs_spy"))
    beta_dimson = _finite_number(p.get("beta_vs_spy_dimson"))
    # la beta del PUNTEGGIO: Dimson se misurata, altrimenti la sincrona DICHIARATA come tale
    beta = beta_dimson if (beta_sincrona is not None and beta_dimson is not None) else beta_sincrona
    var95 = _finite_number(p.get("var_95_1d_pct"))
    maxdd = _finite_number(p.get("max_dd_1y_pct"))

    # BASE NAV (fix v2 10/10, Opus 5.5, riserva 6): pesi, vol, replay e cluster si misurano sul
    # PATRIMONIO (investito + cassa, cassa a vol 0), la stessa base del replay e del sizing.
    # Sull'investito un book al 90% in cassa con un titolo al 10% del NAV risultava «mono-titolo
    # al 100%». Base n.d. (cassa assente, valore di una posizione n.d., FX incompleto) =
    # lettura sull'INVESTITO, la piu' severa (quota investita <= 1), DICHIARATA riga per riga.
    _pd = portfolio_data if isinstance(portfolio_data, dict) else {}
    _positions = _pd.get("positions") or []
    _fx_inc = _pd.get("fx_incomplete")
    _valori = [_finite_number(x.get("valore_mercato_eur", x.get("valore_mercato"))) for x in _positions]
    _senza_valore = [str(x.get("ticker") or "?") for x, v in zip(_positions, _valori) if v is None]
    investito = nav = quota_inv = None
    base_nd_motivo = None   # perche' la base NAV non c'e' (testo localizzato)
    if _positions and _fx_inc:
        base_nd_motivo = _message("FX incompleto ({valute})", "incomplete FX ({valute})",
                                  valute=", ".join(str(v) for v in _fx_inc))
    elif _senza_valore:
        # riserva 10: un valore n.d. NON vale 0 nell'investito (sottostimava la base)
        base_nd_motivo = _message("valore di mercato n.d. per {t}", "market value n/a for {t}",
                                  t=", ".join(_senza_valore[:4]))
    elif _positions:
        investito = sum(_valori)
        _cassa = _finite_number(_pd.get("cash_disponibile_eur"))
        if _cassa is None:
            base_nd_motivo = _message("cassa n.d.", "cash n/a")
        elif investito > 0 and investito + _cassa > 0:
            nav = investito + _cassa
            quota_inv = investito / nav
    _base_txt = (_message("NAV", "NAV") if nav is not None else
                 _message("investito ({m})", "invested ({m})",
                          m=base_nd_motivo or _message("nessuna posizione dal portafoglio", "no position from the portfolio")))

    # concentrazione: top-name % e HHI dai pesi (sul NAV quando la base c'e')
    top_pct = None
    top_single_pct = None
    hhi = None
    weights = []
    pesi_motivo = None
    veicoli_nota = None
    n_veicoli_esclusi = 0
    try:
        positions = _positions
        if positions and _fx_inc:
            # audit 09/10 §2.1: con FX incompleto `valore_mercato` e' in valuta NATIVA (85.000
            # GBX contro 1.000 EUR = top 98,8%): pesi n.d., mai sommati fra valute diverse
            pesi_motivo = _message("FX incompleto ({valute}): pesi EUR delle posizioni n.d.",
                                   "incomplete FX ({valute}): EUR position weights unavailable",
                                   valute=", ".join(str(v) for v in _fx_inc))
            positions = []
        elif positions and _senza_valore:
            pesi_motivo = _message("valore di mercato n.d. per {t}: pesi n.d. (mai contato come 0)",
                                   "market value n/a for {t}: weights n/a (never counted as 0)",
                                   t=", ".join(_senza_valore[:4]))
            positions = []
        if positions:
            values = _valori
            if any(v < 0 for v in values):
                # posizioni corte: top e HHI su pesi netti non hanno senso (1000/-600 = top 250%)
                pesi_motivo = _message("posizioni corte nel book: concentrazione su pesi netti n.d.",
                                       "short positions in the book: net-weight concentration unavailable")
            elif sum(values) > 0:
                _den = nav if nav is not None else sum(values)
                weights = [v / _den for v in values]
                tickers = [str(x.get("ticker") or "").upper() for x in positions]
        if not weights and pesi_motivo is None:
            # nessuna posizione dal portafoglio: pesi di per_asset, che sono sull'INVESTITO
            pa = risk_data.get("per_asset") or {}
            ws = [_finite_number(v.get("weight_pct")) for v in pa.values()]
            if ws and all(w is not None and w >= 0 for w in ws):
                weights = [w / 100.0 for w in ws]
                tickers = [str(k).upper() for k in pa.keys()]
        if weights:
            top_pct = max(weights) * 100.0
            # nome SINGOLO maggiore: un ETF globale al 40% non e' il rischio idiosincratico di
            # un titolo al 40%. Veicoli dal negozio (classe_size 'veicolo'); negozio n.d. =
            # tutti trattati come nomi singoli (conservativo) e DICHIARATO.
            try:
                _neg = negozio if negozio is not None else cl.carica_veicoli()
                if _neg.get("origine") in ("assente", "illeggibile"):
                    raise ValueError("%s: %s" % (_neg.get("origine"), _neg.get("motivo")))
                _veh = {t.upper() for t, v in (_neg.get("veicoli") or {}).items()
                        if (v or {}).get("classe_size") == "veicolo"}
            except Exception as _e:
                _veh = set()
                veicoli_nota = _message("veicoli non distinguibili (negozio {motivo}): tutte le posizioni contate come nomi singoli",
                                        "vehicles not distinguishable (store {motivo}): every position counted as a single name",
                                        motivo=str(_e)[:80])
            singoli = [w for w, t in zip(weights, tickers) if t not in _veh]
            n_veicoli_esclusi = len(weights) - len(singoli)
            top_single_pct = (max(singoli) * 100.0) if singoli else 0.0
            # riserva 7: HHI sui soli NOMI SINGOLI, come il primo nome. Un ETF globale al 60%
            # pesava 3.600 punti di HHI («2 nomi equivalenti») pur essendo il veicolo piu'
            # diversificato del book. Somma dei quadrati dei pesi singoli sulla base (NAV).
            hhi = sum(w * w for w in singoli) * 10000  # 0..10000
    except Exception:
        pass

    lines = []
    pts = []
    unscored = []
    excluded = {}   # label -> motivo: misurate ma escluse per regola (non «dato mancante»)
    info = []       # (label, valore, motivo): informative, fuori punteggio (fix score 09/10)
    from bellomberg.core.presentation import message as _message

    # GUARDRAIL BETA (PM 06/10): fuori da RECONCILED la beta del book non pesa
    beta_guardrail = None        # testo localizzato (righe e blocco)
    beta_guardrail_codice = None # codice stabile, identico in ogni lingua (metrics)
    beta_esclusa = None
    # Dimson entro questa distanza dalla beta riconciliata: e' la soglia di reconcile_betas
    # (threshold=0.35), usata se il payload non porta la sua
    DIMSON_SOGLIA_RICONCILIAZIONE = 0.35
    dimson_stato = None   # USED / NOT_RECONCILED (solo con guardrail RECONCILED e Dimson misurata)
    dimson_nota = None
    if beta is not None:
        rb = beta_reconcile
        # BG-ROTTA 06/10: il codice viene dalla funzione pura condivisa con la rotta API;
        # qui si costruisce solo il testo localizzato di ogni codice
        _fonte = "portfolio_risk_spy"
        beta_guardrail_codice = codice_guardrail_beta(rb)
        if beta_guardrail_codice == "RECONCILED":
            beta_guardrail = "RECONCILED"
            if beta_dimson is not None and beta is beta_dimson:
                # riserva 3 (regola PM 06/10, review C1): il guardrail riconcilia la beta SINCRONA
                # di portfolio_risk; la Dimson pesa solo se sta entro la STESSA soglia del
                # guardrail da quella riconciliata, altrimenti pesa la riconciliata e lo si dice
                _ric = _finite_number((rb.get("betas") or {}).get(_fonte))
                if _ric is None:
                    _ric = beta_sincrona
                _soglia = _finite_number(rb.get("threshold"))
                if _soglia is None or _soglia <= 0:
                    _soglia = DIMSON_SOGLIA_RICONCILIAZIONE
                if abs(beta_dimson - _ric) <= _soglia:
                    dimson_stato = "USED"
                else:
                    dimson_stato = "NOT_RECONCILED"
                    beta = _ric
                    dimson_nota = _message("Dimson non riconciliata ({d:.2f} vs {r:.2f}, scarto oltre {s:.2f})",
                                           "Dimson not reconciled ({d:.2f} vs {r:.2f}, gap beyond {s:.2f})",
                                           d=beta_dimson, r=_ric, s=_soglia)
        elif beta_guardrail_codice == "RECONCILED_SENZA_FONTE_RISCHIO":
            _ins = (rb.get("sources_insufficient") or {}).get(_fonte)
            if _ins is not None:
                _perche = _message("insufficiente: {n}/{m} osservazioni", "insufficient: {n}/{m} observations",
                                   n=("n.d." if _ins.get("n_obs") is None else _ins.get("n_obs")),
                                   m=_ins.get("min_obs"))
            elif _fonte in (rb.get("sources_failed") or {}):
                _perche = _message("fallita", "failed")
            else:
                _perche = _message("assente", "missing")
            beta_guardrail = _message("RECONCILED senza la fonte della beta di rischio ({fonte} {perche})",
                                      "RECONCILED without the risk-beta source ({fonte} {perche})",
                                      fonte=_fonte, perche=_perche)
            beta_esclusa = _message("beta esclusa: guardrail {verdetto}", "beta excluded: guardrail {verdetto}",
                                    verdetto=beta_guardrail)
        else:
            if beta_guardrail_codice == "NON_CALCOLATO":
                # review R-SEG 06/10 (C2): nessun guasto, il chiamante non l'ha calcolato
                beta_guardrail = _message("non calcolato da questo percorso", "not computed by this path")
            elif beta_guardrail_codice == "RECONCILED_SENZA_VIA_LIBERA":
                # payload incoerente: verdetto senza via libera
                beta_guardrail = _message("RECONCILED senza via libera", "RECONCILED without clearance")
            elif beta_guardrail_codice != "NON_DISPONIBILE":
                beta_guardrail = beta_guardrail_codice      # verdetto del motore, verbatim
            else:
                errore = rb.get("error") if isinstance(rb, dict) else None
                beta_guardrail = (_message("non disponibile ({motivo})", "not available ({motivo})", motivo=str(errore))
                                  if errore else _message("non disponibile", "not available"))
            beta_esclusa = _message("beta esclusa: guardrail {verdetto}", "beta excluded: guardrail {verdetto}",
                                    verdetto=beta_guardrail)

    # fix 04/10 (A7, Opus 5.5, review RV-R): una metrica mancante NON sparisce piu' dal
    # rubric — riga «n.d.: motivo» SENZA punti e massimo ricalcolato sulle sole misurate
    # (dichiarato in `unscored`). Motivo dal payload se c'e', altrimenti lo si dice.
    def add(label, value, fmt, p_, motivo=None):
        if p_ is not None:
            lines.append((label, fmt, p_)); pts.append(p_)
        else:
            lines.append((label, _message("n.d.: {motivo}", "n/a: {motivo}",
                                          motivo=motivo or _message("dato non fornito dal tool",
                                                                    "value not provided by the tool")),
                          None))
            unscored.append(label)

    # ---- ANCORE (fix score 09/10, Opus 5.5). Punti 0..3 CONTINUI fra le ancore. ----
    # 1) Vol prevista / target del mandato: 0,75x -> 0 (margine ampio), 1,00x -> 1 (budget
    #    di vol interamente usato: attenzione, non allarme), 1,25x -> 2 (fuori budget di un
    #    quarto), 1,50x -> 3. Il target e' del PM: un book al 20% con target 20 non e' «rischio
    #    alto», lo stesso book con target 12 si'.
    _VOL_RATIO = (0.75, 1.00, 1.25, 1.50)
    # 2) Perdita replay GFC sul NAV / budget di stress del mandato: 0,50x -> 0, 0,75x -> 1,
    #    1,00x -> 2 (al tetto: il sizing lo marca SFORATO appena oltre), 1,25x -> 3.
    _STRESS_RATIO = (0.50, 0.75, 1.00, 1.25)
    # 3) Beta: 0,5 -> 0, 0,9 -> 1, 1,2 -> 2, 1,5 -> 3. Un book azionario long-only ha beta
    #    naturale ~1 (attenzione: il rischio e' il mercato); 1,5 e' esposizione da leva.
    _BETA = (0.5, 0.9, 1.2, 1.5)
    # 4) Primo nome SINGOLO (% NAV) in MULTIPLI del cap_single_pct del mandato (riserva 11):
    #    1x -> 0 (al tetto: in regola), 2x -> 1, 3x -> 2, 4x -> 3 (un nome che pesa quanto
    #    quattro posizioni piene). Col cap a 10 sono le ancore fisse di prima (10/20/30/40).
    #    Cap assente = ancore fisse 10/20/30/40 DICHIARATE nella riga. Lettura: un salto
    #    idiosincratico del -50% su un nome al 40% costa -20% del NAV, un budget di stress intero.
    _TOP_MULTIPLI = (1.0, 2.0, 3.0, 4.0)
    _TOP_FISSE = (10.0, 20.0, 30.0, 40.0)
    # 5) HHI dei soli nomi singoli (0-10000, pesi sul NAV): 1000 -> 0 (10 nomi equivalenti),
    #    2000 -> 1 (5), 3300 -> 2 (3), 5000 -> 3 (2 nomi equivalenti).
    _HHI = (1000.0, 2000.0, 3300.0, 5000.0)
    # 6) Primo cluster economico (% NAV, asse unico di portfolio_sectors) in MULTIPLI del
    #    cap_settore_pct del mandato: 1,0x -> 0, 1,6x -> 1, 2,2x -> 2, 2,8x -> 3; col cap a 25
    #    sono le ancore fisse di prima (25/40/55/70). Shock settoriale storico -50/-80% (tech
    #    2000-02, banche 2008): un cluster al 40% con -50% costa -20% del NAV.
    _CLUSTER_MULTIPLI = (1.0, 1.6, 2.2, 2.8)
    _CLUSTER_FISSE = (25.0, 40.0, 55.0, 70.0)
    # PAVIMENTI del verdetto (almeno ELEVATO): la media delle metriche non deve diluire un book
    # appeso a un nome o a un settore (mono-titolo 100% con vol bassa usciva «MEDIO», audit
    # §2.2e) ne' un book oltre il budget di stress (riserva 4: il PM a -34,2% contro il budget
    # usciva «RISCHIO MEDIO»). 10/10 (Opus 5.5): soglie LEGATE AL BUDGET DI STRESS del mandato
    # (non ai cap di sizing): nome singolo = budget / 0,60 (shock idiosincratico), cluster =
    # budget / 0,50 (shock settoriale) -> con budget 30% sono 50% e 60% del NAV. Senza budget
    # nel mandato 50/60 DICHIARATI (core/soglie_score.pavimenti_concentrazione). Il cluster
    # prima stava al 70% fisso. Calcolate sotto, dopo la lettura del mandato.
    # replay/budget >= 1,00: al tetto la capacita' di aggiunta del sizing e' zero e appena
    # oltre lo marca SFORATO (sizing_engine: gfc_status)
    PAVIMENTO_STRESS_RATIO = 1.0

    def _non_calcolato(cosa):
        return _message("{cosa} non calcolato da questo percorso", "{cosa} not computed by this path", cosa=cosa)

    # --- mandato: target di vol e budget di stress (nessun default: assenti = n.d.) ---
    vol_target = stress_budget = None
    mandato_motivo = None
    if mandato is None:
        mandato_motivo = _non_calcolato(_message("mandato", "mandate"))
    elif isinstance(mandato, dict) and mandato.get("error"):
        mandato_motivo = _message("mandato non disponibile ({e})", "mandate unavailable ({e})", e=str(mandato["error"])[:120])
    else:
        _r = (mandato.get("rischio") or {}) if isinstance(mandato, dict) else {}
        vol_target = _finite_number(_r.get("volatilita_target_pct"))
        stress_budget = _finite_number(_r.get("stress_gfc_pct"))
        if vol_target is not None and vol_target <= 0:
            vol_target = None
        if stress_budget is not None and stress_budget <= 0:
            stress_budget = None
    PAVIMENTO_NOME_PCT, PAVIMENTO_CLUSTER_PCT, _pav_dal_mandato = _S.pavimenti_concentrazione(stress_budget)
    # cap di concentrazione del mandato (sezione sizing, definiti «% dell'investito»; qui
    # applicati al peso sul NAV: rischio sul patrimonio, dichiarato nella riga)
    cap_single = cap_settore = None
    if isinstance(mandato, dict) and not mandato.get("error"):
        _s = mandato.get("sizing") or {}
        cap_single = _finite_number(_s.get("cap_single_pct"))
        cap_settore = _finite_number(_s.get("cap_settore_pct"))
        cap_single = cap_single if cap_single is not None and cap_single > 0 else None
        cap_settore = cap_settore if cap_settore is not None and cap_settore > 0 else None
    if cap_single is not None:
        _TOP = tuple(m * cap_single for m in _TOP_MULTIPLI)
        _top_ancore = _message("ancore 1-4x cap {c:g}%", "anchors 1-4x cap {c:g}%", c=cap_single)
    else:
        _TOP = _TOP_FISSE
        _top_ancore = _message("cap_single_pct n.d.: ancore fisse 10/20/30/40",
                               "cap_single_pct n/a: fixed anchors 10/20/30/40")
    if cap_settore is not None:
        _CLUSTER = tuple(m * cap_settore for m in _CLUSTER_MULTIPLI)
        _cl_ancore = _message("ancore 1-2,8x cap {c:g}%", "anchors 1-2.8x cap {c:g}%", c=cap_settore)
    else:
        _CLUSTER = _CLUSTER_FISSE
        _cl_ancore = _message("cap_settore_pct n.d.: ancore fisse 25/40/55/70",
                              "cap_settore_pct n/a: fixed anchors 25/40/55/70")

    # 1) DISPERSIONE: una sola misura
    if vol_ewma is not None:
        vol, vol_label = vol_ewma, _message("Vol prevista EWMA 0,94 vs target", "Forecast vol EWMA 0.94 vs target")
    elif vol_storica is not None:
        # ripiego ETICHETTATO (payload senza EWMA: serie corta o risk_data di un altro percorso)
        vol, vol_label = vol_storica, _message("Vol storica 1a vs target (EWMA n.d.)", "Historical 1y vol vs target (EWMA n/a)")
    else:
        vol, vol_label = None, _message("Vol prevista EWMA 0,94 vs target", "Forecast vol EWMA 0.94 vs target")
    vol_nav = None
    if vol is not None:
        # riserva 6: vol del PATRIMONIO = vol dell'investito x quota investita (cassa a vol 0)
        vol_nav = vol * quota_inv if quota_inv is not None else vol
        # valore NEUTRO (stesse cifre in ogni lingua, contratto A7): «NAV = investito x quota»
        _vol_txt = ("{:.1f}% ({:.1f}% x {:.1%})".format(vol_nav, vol, quota_inv) if quota_inv is not None
                    else "{:.1f}%".format(vol))
        vol_label = vol_label + (_message(" (sul NAV: investito x quota investita)", " (on NAV: invested x invested share)")
                                 if quota_inv is not None else " [" + _base_txt + "]")
    if vol is not None and vol_target is not None:
        _ratio = vol_nav / vol_target
        add(vol_label, vol_nav, "{} / {:.0f}% ({:.2f}x)".format(_vol_txt, vol_target, _ratio), _interp(_ratio, _VOL_RATIO))
    elif vol is not None:
        add(vol_label, vol_nav, None, None, motivo=_message("vol {v} misurata, target del mandato n.d. ({m})",
                                                            "vol {v} measured, mandate target n/a ({m})",
                                                            v=_vol_txt, m=mandato_motivo or _message("campo assente", "field missing")))
    else:
        add(vol_label, None, None, None)

    # 2) CODA: replay GFC 2008 vs budget di stress
    _stress_label = _message("Replay GFC 2008 vs budget di stress", "GFC 2008 replay vs stress budget")
    stress_nav = None
    stress_ratio = None
    _sd = stress_data if isinstance(stress_data, dict) else None
    if stress_data is None:
        add(_stress_label, None, None, None, motivo=_non_calcolato(_message("replay", "replay")))
    elif _sd is None or _sd.get("error"):
        add(_stress_label, None, None, None, motivo=_message("replay non disponibile ({e})", "replay unavailable ({e})",
                                                             e=str((_sd or {}).get("error") or type(stress_data).__name__)[:120]))
    elif _sd.get("stress_scenario") != "gfc_2008" or _sd.get("stress_fallback"):
        add(_stress_label, None, None, None, motivo=_message("replay GFC non applicato (scenario {s}, fallback {f})",
                                                             "GFC replay not applied (scenario {s}, fallback {f})",
                                                             s=_sd.get("stress_scenario"), f=bool(_sd.get("stress_fallback"))))
    elif _positions and _fx_inc:
        # riserva 10: con FX incompleto le posizioni sono in valuta NATIVA: investito e NAV non
        # si sommano (85.000 GBX + 1.000 EUR), quindi la perdita sul NAV non e' calcolabile
        add(_stress_label, None, None, None, motivo=_message("{m}: base NAV del replay non calcolabile (valute native non si sommano)",
                                                             "{m}: replay NAV basis cannot be computed (native currencies are not summed)",
                                                             m=base_nd_motivo))
    else:
        _meta = _sd.get("stress_meta") or {}
        _loss = _finite_number(_meta.get("window_loss_pct"))
        if _loss is None:
            add(_stress_label, None, None, None, motivo=_message("perdita della finestra assente nel replay", "window loss missing from the replay"))
        else:
            # budget sul NAV (cassa non stressata, stessa regola di sizing_engine); base NAV n.d.
            # (cassa o un valore di posizione n.d.) = perdita sull'INVESTITO, la lettura piu'
            # severa, dichiarata col motivo
            stress_nav = _loss * quota_inv if quota_inv is not None else _loss
            _base = _base_txt
            _stress_label = _stress_label + " [" + _base_txt + "]"
            _n_proxy = len(_meta.get("proxied") or {}) if isinstance(_meta.get("proxied"), (dict, list)) else None
            _proxy_txt = (_message(", {n} nomi proxy", ", {n} proxy names", n=_n_proxy) if _n_proxy else "")
            if stress_budget is None:
                add(_stress_label, stress_nav, None, None,
                    motivo=_message("replay {l:.1f}% ({b}{p}) misurato, budget di stress del mandato n.d. ({m})",
                                    "replay {l:.1f}% ({b}{p}) measured, mandate stress budget n/a ({m})",
                                    l=stress_nav, b=_base, p=_proxy_txt,
                                    m=mandato_motivo or _message("campo assente", "field missing")))
            else:
                stress_ratio = max(0.0, -stress_nav) / stress_budget
                add(_stress_label, stress_nav,
                    "{:.1f}%{} / -{:.0f}% ({:.2f}x)".format(stress_nav, _proxy_txt, stress_budget, stress_ratio),
                    _interp(stress_ratio, _STRESS_RATIO))

    # 3) BETA (sotto guardrail PM 06/10), metodo nell'etichetta
    _dimson_usata = beta is not None and beta is beta_dimson
    _beta_label = _t("Beta vs S&P 500") + (_message(" (Dimson ±1g)", " (Dimson ±1d)") if _dimson_usata
                                           else _message(" (giornaliera sincrona)", " (daily, synchronous)"))
    if beta_esclusa is None and beta is not None:
        # la beta resta quella dell'INVESTITO (il numero che il guardrail riconcilia e che
        # dimensiona una copertura sul nozionale investito): base e scarto Dimson dichiarati
        _beta_label += _message(" [investito]", " [invested]") + ((" [" + dimson_nota + "]") if dimson_nota else "")
    if beta_esclusa is not None:
        # misurata ma esclusa per regola: niente valore (non e' un argomento decisionale),
        # niente punti, motivo dichiarato; il massimo si ricalcola (format_score_block lo dice)
        lines.append((_beta_label, _message("n.d.: {motivo}", "n/a: {motivo}", motivo=beta_esclusa), None))
        excluded[_beta_label] = beta_esclusa
    else:
        add(_beta_label, beta, ("{:.2f}".format(beta) if beta is not None else None),
            _interp(beta, _BETA),
            motivo=risk_data.get("beta_error"))   # perche' SPY manca / serie corta (portfolio_risk)

    # 4-6) CONCENTRAZIONE (pesi sulla base NAV, ancore dai cap del mandato)
    _motivo_pesi = None if weights else (pesi_motivo or _message(
        "pesi delle posizioni non disponibili (né dal portafoglio né da per_asset)",
        "position weights unavailable (neither from the portfolio nor from per_asset)"))
    _base_pesi = _base_txt if (_positions and not _fx_inc and not _senza_valore) else _message("investito (per_asset)", "invested (per_asset)")
    _top_label = _message("Primo nome singolo", "Largest single name") + (
        " [{}; {}]".format(_base_pesi, _top_ancore) if weights else "")
    add(_top_label, top_single_pct,
        ("{:.1f}%".format(top_single_pct) + (" [" + veicoli_nota + "]" if veicoli_nota else "")
         if top_single_pct is not None else None),
        _interp(top_single_pct, _TOP), motivo=_motivo_pesi)
    _hhi_label = _t("Concentrazione (HHI)") + (
        _message(" [soli nomi singoli, {b}; {n} veicoli esclusi]", " [single names only, {b}; {n} vehicles excluded]",
                 b=_base_pesi, n=n_veicoli_esclusi) if weights else "")
    add(_hhi_label, hhi, ("{:.0f}".format(hhi) if hhi is not None else None),
        _interp(hhi, _HHI), motivo=_motivo_pesi)
    _cl_label = _message("Primo cluster economico", "Largest economic cluster")
    cluster_pct = cluster_nome = None
    if sector_data is None:
        add(_cl_label, None, None, None, motivo=_non_calcolato(_message("esposizione settoriale", "sector exposure")))
    elif not isinstance(sector_data, dict) or sector_data.get("error"):
        add(_cl_label, None, None, None, motivo=_message("esposizione settoriale non disponibile ({e})",
                                                         "sector exposure unavailable ({e})",
                                                         e=str((sector_data or {}).get("error") if isinstance(sector_data, dict) else type(sector_data).__name__)[:120]))
    else:
        _ax = sector_data.get("econ_axis") or {}
        # n.d. non e' un cluster (bucket dei nomi senza settore) e i panieri multi-settore
        # (paese/EM/holding) sono diversificati: fuori dalla misura, copertura dichiarata
        _bk = [b for b in (_ax.get("by_bucket") or [])
               if str(b.get("bucket")) != "n.d." and not str(b.get("bucket")).startswith("Multi")
               and _finite_number(b.get("weight_pct")) is not None]
        if not _bk:
            add(_cl_label, None, None, None, motivo=_message("nessun bucket economico classificato", "no classified economic bucket"))
        else:
            _b = max(_bk, key=lambda b: b["weight_pct"])
            # portfolio_sectors pesa sull'INVESTITO (weight_basis dichiarato): sul NAV x quota investita
            _cl_inv = float(_b["weight_pct"])
            cluster_pct, cluster_nome = (_cl_inv * quota_inv if quota_inv is not None else _cl_inv), str(_b.get("bucket"))
            _cov = _finite_number(_ax.get("coverage_pct"))
            _cl_label = _cl_label + " [{}; {}]".format(
                _message("% NAV (fra parentesi % investito)", "% NAV (invested % in brackets)") if quota_inv is not None
                else _base_txt, _cl_ancore)
            add(_cl_label, cluster_pct, "{:.1f}% {}{}{}".format(
                    cluster_pct, cluster_nome, (" ({:.1f}%)".format(_cl_inv) if quota_inv is not None else ""),
                    (_message(" (copertura {c:.0f}%)", " (coverage {c:.0f}%)", c=_cov) if _cov is not None and _cov < 100 else "")),
                _interp(cluster_pct, _CLUSTER))

    if not pts:
        return None
    score = round(sum(pts), 2)
    max_score = len(pts) * 3
    # PAVIMENTI: verdetto almeno ELEVATO, ognuno DICHIARATO (codici stabili in metrics.floors)
    pavimenti = []   # (codice, testo localizzato)
    # origine della soglia dichiarata nel testo del pavimento (mandato o valore di default)
    _pav_fonte = (_message("budget di stress {b:g}%", "stress budget {b:g}%", b=stress_budget) if _pav_dal_mandato
                  else _message("budget di stress n.d.: soglia di default", "stress budget n/a: default threshold"))
    if top_single_pct is not None and top_single_pct >= PAVIMENTO_NOME_PCT:
        pavimenti.append(("SINGLE_NAME", _message("nome singolo al {v:.0f}% (>= {s:g}%, {f} / shock -60%)",
                                                  "single name at {v:.0f}% (>= {s:g}%, {f} / -60% shock)",
                                                  v=top_single_pct, s=PAVIMENTO_NOME_PCT, f=_pav_fonte)))
    if cluster_pct is not None and cluster_pct >= PAVIMENTO_CLUSTER_PCT:
        pavimenti.append(("CLUSTER", _message("cluster {n} al {v:.0f}% (>= {s:g}%, {f} / shock -50%)",
                                              "cluster {n} at {v:.0f}% (>= {s:g}%, {f} / -50% shock)",
                                              n=cluster_nome, v=cluster_pct, s=PAVIMENTO_CLUSTER_PCT, f=_pav_fonte)))
    if stress_ratio is not None and stress_ratio >= PAVIMENTO_STRESS_RATIO:
        pavimenti.append(("STRESS_BUDGET", _message("replay GFC {l:.1f}% contro budget -{b:.0f}% ({r:.2f}x >= {s:.2f}x: al tetto il sizing non aggiunge, oltre lo marca SFORATO)",
                                                    "GFC replay {l:.1f}% against budget -{b:.0f}% ({r:.2f}x >= {s:.2f}x: at the cap sizing adds nothing, beyond it flags BREACHED)",
                                                    l=stress_nav, b=stress_budget, r=stress_ratio, s=PAVIMENTO_STRESS_RATIO)))
    pavimento_codice = next((c for c, _ in pavimenti if c in ("SINGLE_NAME", "CLUSTER")), None)
    if pavimenti and score < 0.5 * max_score:
        _alzo = round(0.5 * max_score - score, 2)
        score = round(0.5 * max_score, 2)
        info.append((_message("Pavimento del verdetto", "Verdict floor"),
                     _message("{p}: punteggio alzato di {a} a {s} (verdetto almeno ELEVATO)",
                              "{p}: score raised by {a} to {s} (verdict at least ELEVATED)",
                              p="; ".join(t for _, t in pavimenti), a=_alzo, s=score),
                     _message("regola di concentrazione/stress", "concentration/stress rule")))
    frac = score / max_score if max_score else 0
    # bande comuni (core/soglie_score.BANDE_INDICE): prima una quarta copia di 25/50/72
    verdict = (_t("RISCHIO BASSO"), _t("RISCHIO MEDIO"), _t("RISCHIO ELEVATO"),
               _t("RISCHIO CRITICO"))[_S.banda(frac)]

    # INFORMATIVE (fuori punteggio, con il motivo): stesse cifre, nessun punto
    _ridondante = _message("stessa dispersione della vol: fuori punteggio", "same dispersion as vol: not scored")
    _perf = _message("performance trailing, non rischio: fuori punteggio", "trailing performance, not risk: not scored")
    _nd = _message("n.d.: dato non fornito dal tool", "n/a: value not provided by the tool")
    # anche fuori punteggio una metrica mancante si DICHIARA (non sparisce: regola 04/10)
    info.append((_t("VaR 95% 1g"), "{:.2f}%".format(var95) if var95 is not None else _nd, _ridondante))
    info.append((_t("Sharpe ratio (trailing 1a)"), "{:.2f}".format(sharpe) if sharpe is not None else _nd, _perf))
    info.append((_t("Max Drawdown 1a"), "{:.1f}%".format(maxdd) if maxdd is not None else _nd, _perf))
    if vol_ewma is not None and vol_storica is not None:
        info.append((_t("Volatilita' annualizzata"), "{:.1f}%".format(vol_storica), _ridondante))
    if _dimson_usata and beta_sincrona is not None and beta_esclusa is None:
        info.append((_message("Beta giornaliera sincrona", "Daily synchronous beta"), "{:.2f}".format(beta_sincrona),
                     _message("distorta verso il basso fra borse non sincrone: si usa Dimson",
                              "biased low across non-synchronous markets: Dimson is used")))

    return {
        "domain": "quant",
        "score": score,
        "max_score": max_score,
        "verdict": verdict,
        "lines": lines,  # (label, value_str, points) — points None = n.d., fuori punteggio
        "unscored": unscored,  # metriche n.d.: max_score = 3 x le sole misurate
        "excluded": excluded,  # misurate ma escluse per regola (guardrail beta), col motivo
        "info": info,  # (label, valore, motivo): misurate, mostrate, MAI punteggiate
        "metrics": {"vol_annual_pct": vol_storica, "vol_ewma_annual_pct": vol_ewma,
                    "vol_target_pct": vol_target, "sharpe": sharpe,
                    "beta_vs_spy": beta if beta_esclusa is None else None,
                    "beta_method": (None if beta is None else ("dimson" if beta is beta_dimson else "daily_sync")),
                    "beta_guardrail": beta_guardrail_codice,
                    "var_95_1d_pct": var95, "max_dd_1y_pct": maxdd,
                    "stress_gfc_nav_pct": stress_nav, "stress_budget_pct": stress_budget,
                    "top_position_pct": top_pct, "top_single_name_pct": top_single_pct, "hhi": hhi,
                    "top_cluster_pct": cluster_pct, "top_cluster": cluster_nome,
                    "concentration_floor": pavimento_codice,
                    # 10/10: soglie dei pavimenti di concentrazione e la loro origine
                    "floor_thresholds": {"single_name_pct": PAVIMENTO_NOME_PCT, "cluster_pct": PAVIMENTO_CLUSTER_PCT,
                                         "from_mandate_stress_budget": _pav_dal_mandato},
                    # fix v2 10/10: tutti i pavimenti attivi, base dei pesi, vol sul NAV, Dimson
                    "floors": [c for c, _ in pavimenti], "stress_ratio": stress_ratio,
                    "weight_basis": "nav" if nav is not None else "invested",
                    "invested_share": quota_inv, "vol_nav_pct": vol_nav,
                    "cap_single_pct": cap_single, "cap_sector_pct": cap_settore,
                    "beta_dimson_status": dimson_stato},
    }


# ============================================================
# MACRO (fix 09/10, Opus 5.5 — audit AUDIT-SCORE-MACRO-CRYPTO-EVENTI D-M1..D-M7)
# Due SOTTO-INDICI e verdetto = il PIU' SEVERO dei due (non la somma). Curva, tasso reale,
# inflazione e mercato del lavoro descrivono il CICLO e anticipano di 12-18 mesi; VIX e
# spread di credito misurano lo STRESS presente. Sommati con lo stesso segno si annullavano
# proprio nelle crisi: a novembre 2008 la curva si irripidisce e il CPI crolla, e lo score
# usciva «REGIME NEUTRALE 44/100». Ogni riga senza dato, con data assente/futura o vecchia
# oltre il limite della sua frequenza esce DICHIARATA (n.d./STALE), senza punti e fuori dal
# massimo; sotto la copertura minima il sotto-indice non emette verdetto.
# Le soglie sono GIUDIZIO su ordini di grandezza storici, NON tarate su uno storico FRED
# (residuo dichiarato nel rapporto 09/10): ognuna porta la sua motivazione. 10/10 (Opus 5.5):
# il VIX dello STRESS e' CALIBRATO sui percentili 60/80/95 di FRED VIXCLS dal 1997; gli
# spread HY/IG restano di giudizio perche' FRED ne espone solo 3 anni (core/soglie_score).
# v2 10/10 (Opus 5.5, revisione avversariale, decisione PM «correggi tutto»): dis-inversione
# della curva, riga STRESS «decennale a 1 mese», regola di Sahm alimentata dalla dashboard,
# CPI core nel punteggio (headline informativa), bande TIPS 0,5/1,5/2,5, eta' mensile 80 gg.
# ============================================================
# Eta' massima dell'osservazione. Giornaliere FRED (VIX, spread, Treasury, TIPS): 6 giorni =
# weekend + un festivo + 1-2 giorni di ritardo di pubblicazione (stesso limite di
# core/freshness OBS_DEFAULT_DAYS). Mensili (CPI, disoccupazione), v2 10/10 (decisione PM
# «correggi tutto»): 80 giorni, dal CALENDARIO BLS. FRED data l'osservazione al PRIMO del
# mese M; il CPI di M esce fra il 10 e il 15 di M+1 e resta in vigore fino al CPI di M+1,
# che esce fra il 10 e il 15 di M+2: l'eta' massima REGOLARE e' quindi 72-76 giorni (dal 1/8
# al 14/10 sono 74). L'Employment Situation di M esce il primo venerdi' di M+1 e resta in
# vigore fino al primo venerdi' di M+2 (eta' massima ~68). Il vecchio 75 marcava STALE un
# CPI regolarmente in vigore nei mesi con release al 15; 80 = 76 + 4 giorni di slittamento
# del calendario. Oltre 80 una release e' stata saltata o ritardata davvero (shutdown 2025).
_MACRO_ETA_GIORNALIERA = _S.ETA_MAX_GIORNALIERA_GG   # 6, la stessa della volatilita'
_MACRO_ETA_MENSILE = 80
# Copertura minima: un sotto-indice parla solo se misura la MAGGIORANZA delle sue righe:
# 3 su 4 per il ciclo e, dalla v2 (riga «variazione del decennale a 1 mese»), 3 su 4 anche
# per lo stress. Prima bastava il solo VIX per «100/100».
_MACRO_MIN_CICLO = 3
_MACRO_MIN_STRESS = 3
# Dis-inversione (v2): finestra in cui un minimo NEGATIVO della curva 10y-2y (T10Y2Y) tiene
# la riga della curva ad almeno 2 punti anche se oggi la curva e' positiva. Le recessioni
# USA dal 1980 sono iniziate DOPO che la curva era gia' tornata positiva (1990, 2001,
# 2007, 2020): la dis-inversione arriva quando la Fed taglia, 0-18 mesi prima della
# recessione. Senza questa regola una curva +80 bp appena uscita da -100 bp valeva 0 punti
# «inizio ciclo», cioe' il contrario del suo significato storico.
_MACRO_DISINV_GIORNI = 548   # 18 mesi
# Variazione del decennale a 1 mese (v2): il riferimento e' l'ultima osservazione fra 28 e
# 37 giorni prima di quella corrente (la dashboard prende l'ultima a <= 30 giorni prima;
# weekend e festivi spostano fino a +4/+7). Fuori da questa finestra la riga e' n.d.
_MACRO_DELTA10_MIN_GIORNI = 28
_MACRO_DELTA10_MAX_GIORNI = 37
# Bear steepening (10/10, Opus 5.5, decisione PM «curva ripida per rialzo del decennale =
# rischio»). La lettura «curva >=75 bp = inizio ciclo = 0 punti» vale quando la curva e'
# ripida perche' la banca centrale ha tagliato il breve (bull steepening). Se invece si
# irripidisce perche' SALE il lungo, e' un rialzo di term premium/rischio fiscale che
# stringe le condizioni finanziarie sui tassi che contano per l'economia reale (mutui,
# credito corporate a lungo) senza che la Fed abbia fatto nulla: 1994, giugno 2013 (taper
# tantrum), agosto-ottobre 2023, ottobre 2026 (memo 73: 10y 5,31 -> 5,75, 2y fermo a 4,84,
# curva 47 -> 91 bp). Regola: variazione a 1 mese del 10 anni >= +25 bp (~1 deviazione
# standard storica della variazione mensile, la stessa soglia della riga STRESS) E curva
# 10y-2y in aumento nello stesso mese (per costruzione equivale a 10y salito PIU' del 2y)
# -> la riga curva del CICLO vale almeno 2 punti (tardo ciclo); con >= +40 bp (~1,6
# sigma, un mese raro) almeno 3. Il bull steepening (curva ripida perche' scende il 2
# anni, 10y fermo o in calo) resta come prima. Doppio conteggio con la riga STRESS
# «decennale a 1 mese»: voluto, i due sotto-indici non si sommano (governa il piu' severo).
_MACRO_BEAR_MIN_BPS = 25
_MACRO_BEAR_FORTE_BPS = 40


def _nd_copertura():
    """Verdetto dichiarato quando le metriche misurate non bastano (mai «rischio basso»)."""
    from bellomberg.core.presentation import message as _message
    # forma corta: la colonna Verdetto del PDF e' ~203 pt (Helvetica-Bold 8.5)
    return _message("n.d.: copertura insufficiente", "n/a: thin coverage")


def _banda(frac):
    """Indice di banda 0..3 sulle soglie comuni dell'indice 0-100 (core/soglie_score)."""
    return _S.banda(frac)


def _eta_osservazione(data_oss, oggi):
    """(giorni, None) o (None, motivo): data ISO assente, invalida o futura = freschezza n.d."""
    from datetime import date as _date
    s = str(data_oss or "").strip()
    if not s:
        return None, "data osservazione assente"
    try:
        d = _date.fromisoformat(s[:10])
    except ValueError:
        return None, "data osservazione invalida (%s)" % s[:20]
    eta = (oggi - d).days
    if eta < 0:
        return None, "data osservazione futura (%s)" % s[:10]
    return eta, None


def _sahm(storia, data_ultima=None):
    """Regola di Sahm (Sahm 2019): media mobile a 3 mesi della disoccupazione meno il minimo
    della stessa media nei 12 mesi PRECEDENTI. Serve una storia mensile di almeno 15 punti
    validi; altrimenti None (il chiamante lo dichiara).

    v2 (10/10): ritorna (valore, motivo_o_nota). Con `data_ultima` (la data della riga
    corrente) la storia deve FINIRE li': una storia che si ferma prima descriverebbe un altro
    mese. Mesi mancanti dentro la finestra di 15 (es. ottobre 2025, non pubblicato per lo
    shutdown) non bloccano il calcolo ma escono DICHIARATI nella nota."""
    from datetime import date as _date
    if not isinstance(storia, (list, tuple)):
        return None, "storia mensile assente dal payload"
    punti = []
    for o in storia:
        if not isinstance(o, dict):
            return None, "storia mensile malformata"
        v = _finite_number(o.get("value"))
        try:
            d = _date.fromisoformat(str(o.get("date") or "")[:10])
        except ValueError:
            return None, "storia mensile con data invalida"
        if v is None:
            return None, "storia mensile con valore non numerico"
        punti.append((d, v))
    punti.sort()
    if len(punti) < 15:
        return None, "storia %d mesi < 15" % len(punti)
    if data_ultima is not None and str(punti[-1][0]) != str(data_ultima)[:10]:
        return None, "storia ferma al %s, dato corrente %s" % (punti[-1][0], str(data_ultima)[:10])
    vals = [v for _, v in punti]
    ma3 = [sum(vals[i - 2:i + 1]) / 3.0 for i in range(2, len(vals))]
    primo, ultimo = punti[-15][0], punti[-1][0]
    mancanti = (ultimo.year - primo.year) * 12 + ultimo.month - primo.month + 1 - 15
    nota = "" if mancanti <= 0 else "%d mesi mancanti nella finestra" % mancanti
    return ma3[-1] - min(ma3[-13:-1]), nota


def _macro_minimo_curva(entry, oggi):
    """(minimo_bps, data, nota) dal campo `min_18m` che la dashboard aggiunge a
    `yield_curve_10y_2y` (v2). Minimo NEGATIVO con l'ultimo negativo entro 18 mesi da oggi =
    prova di inversione (anche se la storia e' parziale; la data restituita e' quella
    dell'ultimo negativo). L'ASSENZA di inversione si afferma solo con la storia completa e
    aggiornata, altrimenti (None, None, nota) dichiarata."""
    entry = entry if isinstance(entry, dict) else {}
    m = entry.get("min_18m")
    if not isinstance(m, dict):
        err = entry.get("error")
        return None, None, ("dis-inversione n.d.: " + ("errore fonte T10Y2Y: %s" % str(err)[:60] if err
                                                      else "storia T10Y2Y assente dal payload"))
    v = _finite_number(m.get("value"))
    if v is None:
        return None, None, "dis-inversione n.d.: minimo 18 mesi non numerico"
    bps = round(v * 100.0, 1)   # T10Y2Y e' in punti percentuali
    if bps < 0:
        # la prova e' il negativo PIU' RECENTE (non la data del minimo, che puo' essere in
        # fondo alla finestra della dashboard e uscirne il giorno dopo)
        rif = str(m.get("last_negative_date") or m.get("date") or "")
        eta, motivo = _eta_osservazione(rif, oggi)
        if eta is None:
            return None, None, "dis-inversione n.d.: data dell'inversione " + motivo
        if eta <= _MACRO_DISINV_GIORNI:
            return bps, rif[:10], ""
        return None, None, ("nessuna inversione negli ultimi 18 mesi (ultimo negativo il %s, "
                            "fuori dalla finestra)" % rif[:10])
    if m.get("copertura_completa") is not True:
        return None, None, "dis-inversione non verificabile: storia T10Y2Y piu' corta di 18 mesi"
    eta_fine, _ = _eta_osservazione(m.get("window_to"), oggi)
    if eta_fine is None or eta_fine > _MACRO_ETA_GIORNALIERA:
        return None, None, "dis-inversione non verificabile: storia T10Y2Y non aggiornata"
    return bps, str(m.get("date"))[:10], ""


def _macro_delta_10y(entry, y10, serie="DGS10"):
    """(delta_bps, (data_rif, valore_rif), motivo) della variazione a 1 mese del rendimento,
    dal campo `ref_1m` che la dashboard aggiunge a `10y_treasury` (v2) e, dal 10/10, anche a
    `2y_treasury` (`serie="DGS2"`, bear steepening)."""
    from datetime import date as _date
    entry = entry if isinstance(entry, dict) else {}
    if y10 is None:
        err = entry.get("error")
        return None, None, ("errore fonte %s: %s" % (serie, str(err)[:80]) if err
                            else "%s assente dal payload" % serie)
    ref = entry.get("ref_1m")
    if not isinstance(ref, dict):
        return None, None, "riferimento a 1 mese assente dal payload (storia %s)" % serie
    v0 = _finite_number(ref.get("value"))
    try:
        d0 = _date.fromisoformat(str(ref.get("date") or "")[:10])
        d1 = _date.fromisoformat(str(entry.get("date") or "")[:10])
    except ValueError:
        return None, None, "riferimento a 1 mese con data invalida"
    if v0 is None:
        return None, None, "riferimento a 1 mese non numerico"
    gap = (d1 - d0).days
    if not (_MACRO_DELTA10_MIN_GIORNI <= gap <= _MACRO_DELTA10_MAX_GIORNI):
        return None, None, "riferimento a %d giorni, atteso %d-%d" % (
            gap, _MACRO_DELTA10_MIN_GIORNI, _MACRO_DELTA10_MAX_GIORNI)
    # arrotondato a 0,1 bp: (5,31 - 5,06) * 100 in virgola mobile fa 24,99999 e cambierebbe banda
    return round((y10 - v0) * 100.0, 1), (d0.isoformat(), v0), None


def _macro_bear_steepening(y10e, delta10, rif10, motivo10, y2e, delta2, rif2, motivo2):
    """(esito, motivo): True = curva che si irripidisce per rialzo del decennale (v.
    _MACRO_BEAR_MIN_BPS), False = no, None = non decidibile (motivo dichiarato)."""
    if delta10 is None:
        return None, "variazione a 1 mese del decennale n.d. (%s)" % motivo10
    if delta10 < _MACRO_BEAR_MIN_BPS:
        return False, ""   # decennale non salito abbastanza: il 2 anni non serve
    if delta2 is None:
        return None, "variazione a 1 mese del 2 anni n.d. (%s)" % motivo2
    d10, d2 = str(y10e.get("date") or "")[:10], str(y2e.get("date") or "")[:10]
    if d10 != d2 or rif10[0] != rif2[0]:
        # due variazioni su finestre diverse non fanno una variazione della curva
        return None, "DGS10 e DGS2 su date diverse (%s/%s vs %s/%s)" % (rif10[0], d10, rif2[0], d2)
    # curva = 10y - 2y: delta curva > 0 equivale a delta 10y > delta 2y
    return round(delta10 - delta2, 1) > 0, ""


@scoped_language
def macro_score(macro_data=None, oggi=None):
    """Rubric REGIME macro in due sotto-indici, CICLO e STRESS; verdetto = il piu' severo.
    PIU' ALTO = piu' rischio. `oggi` (date) solo per i test: l'eta' delle osservazioni si
    misura contro la data di oggi. None se nessuna metrica e' presente; dict con score None
    e verdetto «n.d.: copertura insufficiente» se le metriche ci sono ma non bastano."""
    if macro_data is None:
        try:
            from bellomberg.agents.agent_tools import tool_get_macro_dashboard
            macro_data = tool_get_macro_dashboard()
        except Exception:
            return None
    if not isinstance(macro_data, dict):
        return None
    from datetime import date as _date
    from bellomberg.core.presentation import message as _message
    oggi = oggi or _date.today()
    ind = macro_data.get("indicators") or {}

    def _ind(k):
        e = ind.get(k) if isinstance(ind, dict) else None
        return e if isinstance(e, dict) else {}

    def _data_piu_vecchia(*chiavi):
        # un dato derivato vale quanto la sua componente PIU' VECCHIA
        ds = [str(_ind(k).get("date") or "") for k in chiavi]
        return None if any(not d for d in ds) else min(ds)

    lines = []
    unscored = []
    excluded = {}   # v2: righe INFORMATIVE (CPI headline), fuori dal punteggio per regola
    stale = []
    punti = {"ciclo": [], "stress": []}
    attese = {"ciclo": 0, "stress": 0}
    presenti = 0

    def valuta(sotto, label, valore, fmt, data_oss, limite, fn_punti, motivo_assente):
        nonlocal presenti
        attese[sotto] += 1
        if valore is None:
            lines.append((label, _message("n.d.: {m}", "n/a: {m}", m=motivo_assente), None))
            unscored.append(label)
            return
        presenti += 1
        eta, motivo = _eta_osservazione(data_oss, oggi)
        if eta is None:
            lines.append((label, _message("n.d.: {v} non verificabile ({m})", "n/a: {v} not verifiable ({m})",
                                          v=fmt, m=motivo), None))
            unscored.append(label)
            return
        if eta > limite:
            lines.append((label, _message("STALE: {v} osservato il {d}, {e} giorni fa (limite {l})",
                                          "STALE: {v} observed on {d}, {e} days ago (limit {l})",
                                          v=fmt, d=str(data_oss)[:10], e=eta, l=limite), None))
            unscored.append(label)
            stale.append(label)
            return
        p = fn_punti(valore)
        lines.append((label, fmt, p))
        punti[sotto].append(p)

    def _motivo(chiave, serie):
        err = _ind(chiave).get("error")
        if err:
            return "errore fonte %s: %s" % (serie, str(err)[:80])
        return "%s assente dal payload" % serie

    # ---------------- CICLO (anticipatori: dove va l'economia fra 12-18 mesi)
    curve = _finite_number(macro_data.get("yield_curve_10y_2y_bps"))
    min_curva, data_min, nota_min = _macro_minimo_curva(_ind("yield_curve_10y_2y"), oggi)
    disinv = curve is not None and curve >= 0 and min_curva is not None and min_curva < 0
    # variazioni a 1 mese di 10y e 2y: servono al bear steepening (qui) e allo STRESS (sotto)
    y10e, y2e = _ind("10y_treasury"), _ind("2y_treasury")
    y10 = _finite_number(y10e.get("value"))
    delta10, rif10, motivo10 = _macro_delta_10y(y10e, y10)
    delta2, rif2, motivo2 = _macro_delta_10y(y2e, _finite_number(y2e.get("value")), "DGS2")
    bear, nota_bear = _macro_bear_steepening(y10e, delta10, rif10, motivo10, y2e, delta2, rif2, motivo2)
    # Curva 10y-2y: invertita (<0) ha preceduto ogni recessione USA dal 1980 = 3; piatta
    # (0-25 bp) = tardo ciclo = 2; 25-75 bp = meta' ciclo = 1; >=75 bp (media storica dal
    # 1976 ~+90 bp) = curva ripida da inizio ciclo = 0. v2: DIS-INVERSIONE = curva oggi
    # positiva ma con un minimo < 0 negli ultimi 18 mesi -> almeno 2 punti (v.
    # _MACRO_DISINV_GIORNI): la curva che si irripidisce uscendo dall'inversione e' il tratto
    # che precede la recessione, non un inizio ciclo.

    def _punti_curva(c):
        base = 3 if c < 0 else 2 if c < 25 else 1 if c < 75 else 0
        base = max(base, 2) if disinv else base
        if bear:
            base = max(base, 3 if delta10 >= _MACRO_BEAR_FORTE_BPS else 2)
        return base

    # bear steepening non decidibile e riga sotto il massimo: NON si punteggia sul solo
    # livello (sarebbe proprio il «0 = inizio ciclo» che la regola corregge): n.d. dichiarata
    curva_nd = curve is not None and bear is None and _punti_curva(curve) < 3
    if curve is None:
        fmt_curva = ""
    elif disinv:
        fmt_curva = _message("{c:.0f} bps (dis-inversione: minimo {m:.0f} bps, ultimo negativo il {d})",
                             "{c:.0f} bps (un-inversion: low {m:.0f} bps, last negative on {d})",
                             c=curve, m=min_curva, d=data_min)
    elif min_curva is not None:
        fmt_curva = _message("{c:.0f} bps (minimo 18 mesi {m:+.0f} bps)", "{c:.0f} bps (18-month low {m:+.0f} bps)",
                             c=curve, m=min_curva)
    else:
        fmt_curva = "{:.0f} bps ({})".format(curve, nota_min)
    if bear:
        fmt_curva = _message("curva ripida per rialzo dei tassi lunghi (bear steepening): "
                             "10y {d10:+.0f} bps, 2y {d2:+.0f} bps in 1 mese; {f}",
                             "steep curve from rising long rates (bear steepening): "
                             "10y {d10:+.0f} bps, 2y {d2:+.0f} bps in 1 month; {f}",
                             d10=delta10, d2=delta2, f=fmt_curva)
    elif bear is None and curve is not None:
        fmt_curva = fmt_curva + _message(" [bear steepening n.d.: {m}]", " [bear steepening n/a: {m}]",
                                         m=nota_bear)
    valuta("ciclo", _message("CICLO | Curva 10y-2y", "CYCLE | 10y-2y curve"),
           None if curva_nd else curve, fmt_curva,
           _data_piu_vecchia("10y_treasury", "2y_treasury"), _MACRO_ETA_GIORNALIERA,
           _punti_curva,
           (_message("curva {c:.0f} bps, bear steepening non verificabile: {m}",
                     "curve {c:.0f} bps, bear steepening not verifiable: {m}", c=curve, m=nota_bear)
            if curva_nd else "curva 10y-2y assente dal payload (DGS10/DGS2)"))
    # Tasso reale EX-ANTE = rendimento TIPS 10 anni (DFII10). Sostituisce il real Fed funds
    # ex-post (FEDFUNDS medio mensile meno CPI realizzato) che ripeteva il CPI col segno
    # opposto: con la Fed ferma CPI e tasso reale si compensavano e l'estate 2022 usciva
    # «accomodante». Bande v2 (decisione PM «correggi tutto»): il TIPS a 10 anni neutrale =
    # r* (stime 0,8-1,3%: Holston-Laubach-Williams, mediana di lungo periodo dello SEP Fed
    # ~3% nominale meno 2% di inflazione) + un term premium reale di 0-0,5 punti, quindi
    # ~1-1,5%. <0,5% = sotto il neutrale, condizioni accomodanti (2012-13, 2020-21) = 0;
    # 0,5-1,5% = intorno al neutrale = 1; 1,5-2,5% = sopra il neutrale, restrittivo (2007,
    # fine 2022-2023) = 2; >=2,5% = molto restrittivo, sopra i massimi 2007-2023 = 3. Le
    # bande v1 (0/1/2) chiamavano «molto restrittivo» un TIPS al 2%, cioe' appena sopra il
    # neutrale con un term premium normale.
    tips = _finite_number(_ind("real_10y_rate").get("value"))
    valuta("ciclo", _message("CICLO | Tasso reale 10a (TIPS)", "CYCLE | 10y real yield (TIPS)"), tips,
           "{:.2f}%".format(tips) if tips is not None else "",
           _ind("real_10y_rate").get("date"), _MACRO_ETA_GIORNALIERA,
           lambda r: 0 if r < 0.5 else 1 if r < 1.5 else 2 if r < 2.5 else 3,
           _motivo("real_10y_rate", "DFII10 (TIPS 10a)")
           + "; il real Fed funds ex-post NON lo sostituisce (ripete il CPI col segno opposto)")
    # CPI v2: il punteggio usa il CORE (CPILFESL), l'headline resta in vista come
    # INFORMATIVA. Motivo: la banca centrale reagisce all'inflazione di fondo; l'headline
    # oscilla con l'energia in entrambe le direzioni e manda segnali di ciclo sbagliati
    # (luglio 2008 headline 5,6% -> «stretta» proprio mentre la Fed tagliava; 2015 headline
    # ~0% con core 2%). Il «massimo dei due» avrebbe importato ogni shock petrolifero nel
    # ciclo. Se manca il core la riga e' n.d. DICHIARATA: l'headline NON lo sostituisce.
    # Bande (target Fed 2% sul PCE core, che corre ~0,3-0,5 punti sotto il CPI core): <2,5% =
    # a target = 0; 2,5-3,5% = sopra target ma tollerato = 1; 3,5-5% = pressione che obbliga
    # la banca centrale = 2; >=5% = inflazione di fondo da stretta aggressiva (1990, 2022) = 3.
    cpi = _finite_number(_ind("us_cpi_yoy").get("yoy_pct"))
    core = _finite_number(_ind("us_core_cpi_yoy").get("yoy_pct"))
    valuta("ciclo", _message("CICLO | CPI core YoY", "CYCLE | Core CPI YoY"), core,
           "{:.1f}%".format(core) if core is not None else "",
           _ind("us_core_cpi_yoy").get("date"), _MACRO_ETA_MENSILE,
           lambda c: 0 if c < 2.5 else 1 if c < 3.5 else 2 if c < 5 else 3,
           _motivo("us_core_cpi_yoy", "CPI core YoY (CPILFESL)") + "; l'headline NON lo sostituisce")
    lab_head = _message("CICLO | CPI headline YoY (informativo)", "CYCLE | Headline CPI YoY (informational)")
    if cpi is None:
        val_head = _message("n.d.: {m}", "n/a: {m}", m=_motivo("us_cpi_yoy", "CPI YoY (CPIAUCSL)"))
    else:
        val_head = "{:.1f}% ({})".format(cpi, str(_ind("us_cpi_yoy").get("date") or "data n.d.")[:10])
    lines.append((lab_head, val_head, None))
    excluded[lab_head] = _message("informativa: il punteggio di ciclo usa il CPI core",
                                  "informational: the cycle score uses core CPI")
    # Disoccupazione: regola di Sahm se la storia c'e' (>=0,5 punti = segnale di recessione
    # in tempo reale, mai falso dal 1970 = 3; 0,3-0,5 = pre-allarme = 2; 0,15-0,3 = 1;
    # sotto = rumore = 0). v2: la dashboard porta la storia mensile (24 osservazioni UNRATE,
    # campo `history`). Se la storia manca o e' corta la riga e' un PROXY DICHIARATO
    # (variazione di 1 mese) con soglie anti-rumore (la variazione mensile di UNRATE ha un
    # intervallo di confidenza BLS di circa +-0,2 punti: +0,1 non e' un segnale).
    unemp = _ind("us_unemployment")
    sahm, nota_sahm = _sahm(unemp.get("history") or unemp.get("history_last_12"), unemp.get("date"))
    unemp_chg = _finite_number(unemp.get("change_vs_prev"))
    if sahm is not None:
        valuta("ciclo", _message("CICLO | Disoccup. (regola di Sahm)", "CYCLE | Unemployment (Sahm rule)"),
               sahm, "{:+.2f} pp".format(sahm) + (" (%s)" % nota_sahm if nota_sahm else ""),
               unemp.get("date"), _MACRO_ETA_MENSILE,
               lambda s: 0 if s < 0.15 else 1 if s < 0.3 else 2 if s < 0.5 else 3, "")
    else:
        valuta("ciclo", _message("CICLO | Disoccup. var. 1 mese (proxy: Sahm n.d.)",
                                 "CYCLE | Unemployment 1m change (proxy: Sahm n/a)"),
               unemp_chg, ("{:+.2f} pp [Sahm n.d.: {}]".format(unemp_chg, nota_sahm)
                           if unemp_chg is not None else ""),
               unemp.get("date"), _MACRO_ETA_MENSILE,
               lambda d: 0 if d < 0.15 else 1 if d < 0.25 else 2 if d < 0.45 else 3,
               _motivo("us_unemployment", "UNRATE") + "; Sahm n.d.: " + str(nota_sahm))

    # ---------------- STRESS (coincidenti: quanto e' teso il mercato OGGI)
    # VIX (10/10, CALIBRATO): soglie per 1/2/3 punti = percentili 60/80/95 di FRED VIXCLS dal
    # 1997 (core/soglie_score.MACRO_VIX_SOGLIE, oggi 20,5/25,0/34,1). Prima 20/25/30 di
    # giudizio, che cadevano al 58°, 80° e 91° percentile della stessa serie: il 3 pieno ora
    # resta al 5% piu' teso dei giorni (2008, 2011, 2015, 2018, 2020, 2022).
    vix = _finite_number(_ind("vix_close").get("value"))
    valuta("stress", _message("STRESS | VIX", "STRESS | VIX"), vix,
           "{:.1f}".format(vix) if vix is not None else "",
           _ind("vix_close").get("date"), _MACRO_ETA_GIORNALIERA,
           lambda v: _S.punti_gradino(v, _S.MACRO_VIX_SOGLIE),
           _motivo("vix_close", "VIX (VIXCLS)"))
    # Spread HY (OAS ICE BofA, in %), GIUDIZIO (calibrazione non eseguibile: FRED espone solo
    # 3 anni ICE): mediana dal 1997 ~4,5-5%. <4% = credito compiacente = 0; 4-5,5% = intorno
    # alla mediana = 1; 5,5-7% = stress (2011, 2016, 2022) = 2; >=7% = stress acuto (2001-02,
    # 2008-09, 2016 picco, 2020) = 3. Prima 3 punti gia' al 5%: 2008 al 16% e il 2022 al 5%
    # prendevano lo stesso punteggio.
    hy = _finite_number(_ind("high_yield_spread").get("value"))
    valuta("stress", _message("STRESS | Spread HY", "STRESS | HY spread"), hy,
           "{:.2f}%".format(hy) if hy is not None else "",
           _ind("high_yield_spread").get("date"), _MACRO_ETA_GIORNALIERA,
           lambda h: _S.punti_gradino(h, _S.MACRO_HY_SOGLIE),
           _motivo("high_yield_spread", "spread HY (BAMLH0A0HYM2)"))
    # Spread IG (OAS investment grade, in %), GIUDIZIO (stesso motivo): mediana ~1,3-1,5%.
    # <1,3% = 0; 1,3-1,7% = 1; 1,7-2,5% = 2; >=2,5% = stress sistemico (2008 ~6%, 2020 ~4%)
    # = 3. Terza riga perche' due sole metriche correlate non bastano a una copertura minima.
    ig = _finite_number(_ind("ig_credit_spread").get("value"))
    valuta("stress", _message("STRESS | Spread IG", "STRESS | IG spread"), ig,
           "{:.2f}%".format(ig) if ig is not None else "",
           _ind("ig_credit_spread").get("date"), _MACRO_ETA_GIORNALIERA,
           lambda g: _S.punti_gradino(g, _S.MACRO_IG_SOGLIE),
           _motivo("ig_credit_spread", "spread IG (BAMLC0A0CM)"))
    # Variazione del decennale a 1 mese (v2, decisione PM): |delta| >=25 bp = 1, >=40 = 2,
    # >=60 = 3. La deviazione standard storica della variazione mensile del 10 anni e' ~25
    # bp: 25 = un mese mosso, 40 = ~1,6 sigma, 60 = ~2,4 sigma (pochi mesi per decennio).
    # SIMMETRICA, in rialzo E in ribasso: in rialzo e' lo shock di tassi/term premium che
    # colpisce duration e multipli (1994, giugno 2013, ottobre 2022, ottobre 2023, ottobre
    # 2026 +53 bp col VIX a 15); in ribasso violento e' la fuga verso la qualita' delle crisi
    # (ottobre 2008, agosto 2011, marzo 2020). Contarla solo al rialzo avrebbe diluito lo
    # stress proprio nelle crisi, dove il decennale crolla e la riga varrebbe 0 su 3. Il
    # segno resta scritto nella riga.
    valuta("stress", _message("STRESS | Decennale var. 1 mese", "STRESS | 10y yield 1m change"), delta10,
           ("{:+.0f} bps ({:.2f}% il {} -> {:.2f}%)".format(delta10, rif10[1], rif10[0], y10)
            if delta10 is not None else ""),
           y10e.get("date"), _MACRO_ETA_GIORNALIERA,
           lambda d: 0 if abs(d) < 25 else 1 if abs(d) < 40 else 2 if abs(d) < 60 else 3,
           motivo10)

    if presenti == 0:
        return None

    minimi = {"ciclo": _MACRO_MIN_CICLO, "stress": _MACRO_MIN_STRESS}
    etichette = {
        "ciclo": [_t("REGIME ESPANSIVO (risk-on)"), _t("REGIME NEUTRALE"),
                  _t("REGIME RESTRITTIVO (late-cycle)"), _message("REGIME RECESSIVO", "RECESSIONARY REGIME")],
        "stress": [_message("STRESS BASSO", "LOW STRESS"), _message("STRESS MODERATO", "MODERATE STRESS"),
                   _message("STRESS ELEVATO", "ELEVATED STRESS"), _message("STRESS ACUTO", "ACUTE STRESS")],
    }
    sotto = {}
    for nome in ("ciclo", "stress"):
        pts = punti[nome]
        info = {"misurate": len(pts), "attese": attese[nome], "minimo": minimi[nome],
                "score": None, "max_score": None, "indice": None, "verdetto": _nd_copertura()}
        if len(pts) >= minimi[nome]:
            s, m = sum(pts), 3 * len(pts)
            info.update(score=s, max_score=m, indice=round(100.0 * s / m),
                        verdetto=etichette[nome][_banda(s / m)])
        sotto[nome] = info

    def _idx(nome):
        # v2: «n.d.» tradotto anche qui (in inglese usciva «[C n.d. / S n.d.]»)
        return _message("n.d.", "n/a") if sotto[nome]["indice"] is None else str(sotto[nome]["indice"])

    validi = [k for k in ("ciclo", "stress") if sotto[k]["score"] is not None]
    if not validi:
        score = max_score = None
        governa = None
        verdict = _nd_copertura()
    else:
        # il piu' severo: banda, poi frazione; a parita' esatta governa lo stress (e' presente)
        governa = max(validi, key=lambda k: (_banda(sotto[k]["score"] / sotto[k]["max_score"]),
                                             sotto[k]["score"] / sotto[k]["max_score"], k == "stress"))
        if (_banda(sotto[governa]["score"] / sotto[governa]["max_score"]) == 0 and "ciclo" in validi):
            governa = "ciclo"   # tutto in banda bassa: l'etichetta e' quella del ciclo (espansivo)
        score, max_score = sotto[governa]["score"], sotto[governa]["max_score"]
        verdict = sotto[governa]["verdetto"]
    # entrambi i sotto-indici in chiaro nel verdetto (forma corta: colonna PDF ~203 pt)
    verdict = verdict + " [C %s / S %s]" % (_idx("ciclo"), _idx("stress"))

    return {"domain": "macro", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines,
            "unscored": unscored,  # n.d./STALE: fuori dal massimo, dichiarate
            "excluded": excluded,  # informative per regola (CPI headline)
            "metrics": {"vix": vix, "cpi_yoy": cpi, "core_cpi_yoy": core, "hy_spread": hy, "ig_spread": ig,
                        "curve_10y2y_bps": curve, "real_10y_tips_pct": tips,
                        "curve_min_18m_bps": min_curva, "curve_min_18m_date": data_min,
                        "curve_disinversione": disinv, "delta_10y_1m_bps": delta10,
                        "delta_2y_1m_bps": delta2, "curve_bear_steepening": bear,
                        # ex-post: solo informativo, NON entra nel punteggio (D-M2)
                        "real_fed_funds_pct": _finite_number(macro_data.get("real_fed_funds_pct")),
                        "unemp_change": unemp_chg, "sahm": sahm, "sahm_nota": nota_sahm,
                        "sottoindici": sotto, "governa": governa, "stale": stale}}


def _verdict_bands(score, max_score, labels):
    # fix 09/10 (Opus 5.5): massimo 0 o assente = nessuna metrica misurata. Prima frac=0
    # usciva «BASSO» (la prima etichetta): un buco presentato come rassicurazione.
    score, max_score = _finite_number(score), _finite_number(max_score)
    if score is None or max_score is None or max_score <= 0:
        return _nd_copertura()
    return labels[_S.banda(score / max_score)]


# --- fundamentals: soglie (Opus 5.5 09/10, ordine PM "correggi tutto") -------------------
# UN solo asse: margine di sicurezza (MOS) del book = media dei MOS per nome PONDERATA sul
# peso, cioe' di quanto il fair value aggregato del valutato sta sopra/sotto il suo prezzo
# corrente. Niente secondo asse "n. sopravvalutati": misurava la stessa cosa (doppio conteggio).
# Troncamento per nome a +/-50%: un DCF che dista piu' della meta' dal prezzo e' piu' spesso
# un errore di modello che un vantaggio informativo; la sanity boccia solo i casi grossolani.
_MOS_CAP_PCT = 50.0
# Punteggio (10/10, Opus 5.5): CONTINUO, MOS +45% -> 0 punti ... -75% -> 3 punti, lineare
# (core/soglie_score.punti_mos), con i confini delle bande comuni a +15/-15/-45: zona neutra
# +-15% (errore tipico di un DCF). Prima tre gradini a +15/-15/-30: 14,9% e 15,1% davano due
# etichette diverse. Quote per NOME (righe informative «quota cara / a sconto»): +/-15%,
# lo stesso errore tipico (+/-1 pt di WACC o +/-0,5 pt di crescita terminale spostano il
# fair value del 15-25%).
_MOS_SCONTO_PCT = 15.0
_MOS_CARO_PCT = -15.0


def _peso_posizione(p):
    """Peso in % del book investito (peso_pct del DB); assente/invalido = None, mai 0."""
    w = _finite_number(p.get("peso_pct"))
    return w if w is not None and w > 0 else None


def _veicolo_dal_payload(v):
    """ETF/ETN/fondi riconosciuti dal motore: niente DCF (percorso etf_passive)."""
    if not isinstance(v, dict):
        return False
    decision = v.get("valuation_decision") if isinstance(v.get("valuation_decision"), dict) else {}
    return (v.get("engine") == "etf_passive" or decision.get("method_id") == "exposure_analysis"
            or decision.get("legacy_route") == "etf_passive")


# v2 10/10 (Opus 5.5, riserva ALTO 1): prezzo del book come seconda fonte del prezzo corrente.
# position_prices e' aggiornato dal PriceUpdater ogni ~15 minuti; il DB dichiara price_stale
# (a mercato aperto, oltre soglia) e l'eta'. A mercato CHIUSO un prezzo fermo non e' stale:
# si accetta fino a 5 giorni (weekend lungo + festivo), oltre e' n.d. anche se il DB non lo
# marca. Rapporto prezzo DB / prezzo del modello fuori da [1/4, 4]: piu' facile un errore di
# scala/valuta (pence contro sterline = x100) che un titolo quadruplicato in 30 giorni -> n.d.
_DB_PREZZO_ETA_MAX_MIN = 5 * 24 * 60
_DB_PREZZO_RAPPORTO_MAX = 4.0


def _unita_prezzo(valuta):
    """Unita' di quotazione normalizzata: GBp e GBX sono la stessa (pence)."""
    s = str(valuta or "").strip()
    return "GBX" if s in ("GBX", "GBp", "GBx") else (s.upper() or None)


def _prezzo_db(pos, v):
    """Prezzo corrente dal book (position_prices) nelle unita' del modello, o (None, motivo).

    Mai silenzioso: ritorna anche l'etichetta della fonte con l'eta' in minuti e la scala
    applicata (GBP<->GBX x100 DICHIARATA)."""
    if not isinstance(pos, dict) or pos.get("prezzo_live") is None:
        return None, "prezzo DB assente", None
    if pos.get("price_stale") is not False:
        return None, "prezzo DB stale", None
    prezzo = _finite_number(pos.get("prezzo_live"))
    eta = _finite_number(pos.get("price_age_minutes"))
    if prezzo is None or prezzo <= 0:
        return None, "prezzo DB non valido", None
    if eta is None or eta < 0 or eta > _DB_PREZZO_ETA_MAX_MIN:
        return None, "prezzo DB senza eta' o piu' vecchio di 5 gg", None
    mq_blk = v.get("market_quote") if isinstance(v.get("market_quote"), dict) else {}
    u_mod = _unita_prezzo(v.get("currency") or mq_blk.get("currency"))
    u_pos = _unita_prezzo(pos.get("valuta"))
    if u_mod is None or u_pos is None:
        return None, "valuta del modello o della posizione n.d.", None
    scala, nota_scala = 1.0, ""
    if u_mod != u_pos:
        if {u_mod, u_pos} != {"GBX", "GBP"}:
            return None, "valuta posizione %s diversa dal modello %s" % (u_pos, u_mod), None
        scala = 100.0 if u_mod == "GBX" else 0.01
        nota_scala = ", scala x%s %s->%s" % ("100" if scala > 1 else "0,01", u_pos, u_mod)
    prezzo *= scala
    pr_mod = _finite_number(v.get("price"))
    if pr_mod and pr_mod > 0 and not (1 / _DB_PREZZO_RAPPORTO_MAX <= prezzo / pr_mod
                                      <= _DB_PREZZO_RAPPORTO_MAX):
        return None, "prezzo DB incoerente col modello (rapporto %.2f)" % (prezzo / pr_mod), None
    return prezzo, None, "prezzo DB position_prices, eta' %d min%s" % (int(eta), nota_scala)


def _mos_corrente(v, fv, pos=None):
    """MOS sul prezzo CORRENTE, non su quello del modello. Ritorna (mos, motivo, origine).

    1. quota osservata del payload (market_quote_view: freschezza alla lettura, valuta,
       modello utilizzabile): upside_base_pct riportato al FV canonico col rapporto fv/fv_base
       (indipendente dalla valuta; vale anche per il contratto /2 ritradotto al cambio BCE);
       senza fair_value_base (payload bank/NAV) si usa il prezzo osservato del blocco /1.
       origine None = quota osservata.
    2. v2 10/10 (riserva MEDIO 6): quota fresca ma `fx_not_rolled` (ADR, valuta di bilancio
       diversa) -> FV al cambio STORICO del modello contro il prezzo osservato: proxy
       DICHIARATO «FX storico (proxy)». Il cambio BCE corrente esiste solo nel job di reprice
       del worker (rete), non nel payload che arriva allo scorer.
    3. v2 10/10 (riserva ALTO 1): quota non «ok» -> prezzo del book (position_prices) se
       fresco e nella stessa unita' del modello, etichettato con l'eta'.
    Modello non utilizzabile = n.d. sempre, qualunque prezzo ci sia.
    """
    from bellomberg.valuation.market_quote import market_quote_view, CONTRACT, _freshness
    from datetime import date as _date
    usable = (v.get("valuation_usability") or {}).get("usable") is True
    if not usable:
        return None, "modello non utilizzabile", None
    blk = v.get("market_quote") if isinstance(v.get("market_quote"), dict) else None
    view = market_quote_view(blk, usable=usable)
    stato = str(view.get("status_at_read") or "data_missing")
    ub = _finite_number(view.get("upside_base_pct"))
    fvb = _finite_number(v.get("fair_value_base"))
    if ub is not None and fvb is not None and fvb > 0:
        return _finite_number((fv / fvb * (1 + ub / 100.0) - 1) * 100), None, None
    p_oss = _finite_number((blk or {}).get("price"))
    if (stato == "ok" and (blk or {}).get("contract") == CONTRACT and p_oss and p_oss > 0):
        # payload senza fair_value_base (bank: _blend; NAV): stesso prezzo osservato e gia'
        # passato dai cancelli di valuta e freschezza, FV canonico nelle unita' della quota
        return _finite_number((fv / p_oss - 1) * 100), None, None
    fx_storico = stato == "fx_not_rolled"
    if (fx_storico and p_oss and p_oss > 0
            and _freshness((blk or {}).get("observed_local_date"), _date.today()) == "ok"):
        return _finite_number((fv / p_oss - 1) * 100), None, _t_fx_proxy()
    if stato == "ok" and fvb is None:
        stato = "fair_value_base n.d."
    prezzo, motivo_db, origine = _prezzo_db(pos, v)
    if prezzo is not None:
        if fx_storico:
            origine += "; " + _t_fx_proxy()
        return _finite_number((fv / prezzo - 1) * 100), None, origine
    return None, stato + ("" if pos is None else "; " + motivo_db), None


def _t_fx_proxy():
    from bellomberg.core.presentation import message as _message
    return _message("FX storico (proxy)", "historical FX (proxy)")


@scoped_language
def fundamentals_score(portfolio_data=None, valuations=None, max_names=None):
    """Valutazione del book dal DCF (margine di sicurezza). PIU' ALTO = piu' CARO/sopravvalutato.

    Opus 5.5 09/10: MOS sul prezzo corrente, media ponderata sul peso su TUTTI i nomi
    valutabili (max_names=None; un intero resta solo per chi lo chiede esplicitamente),
    copertura dichiarata in % del book, un solo asse con zona neutra +/-15%.
    """
    from bellomberg.core.presentation import message as _message
    positions = (portfolio_data or {}).get("positions") or []
    if portfolio_data is None or (not positions and valuations is None):
        try:
            from bellomberg.agents import agent_tools
            portfolio_data = agent_tools.tool_get_portfolio_live()
            positions = (portfolio_data or {}).get("positions") or []
        except Exception:
            positions = []
    ranked = sorted(positions, key=lambda x: -(x.get("peso_pct") or 0))
    # C1 16/07: i VEICOLI (fondi chiusi, ETF, ...) si valutano a NAV, non a DCF — dcf_engine li
    # rifiuta comunque, ma tenerli nei top-4 bruciava 3 slot su 4 e lo scorer valutava
    # UN SOLO nome a ogni run. Ora i top-4 sono i top-4 VALUTABILI (copertura reale).
    _negozio_buco = None
    try:
        # Una sola fotografia per tutto lo score: veicoli e DAT non possono
        # provenire da due versioni diverse del negozio durante la stessa run.
        _negozio = cl.carica_veicoli()
        if _negozio["origine"] in ("assente", "illeggibile"):
            _negozio_buco = ("negozio dei veicoli %s (%s): nessun veicolo/DAT escluso"
                             % (_negozio["origine"], _negozio["motivo"]))
        _veh = {t for t, v in _negozio["veicoli"].items()
                if v.get("classe_size") == "veicolo"}
        _dat = set(cl.veicoli_per_tipo("dat", _negozio)["tickers"])
    except Exception as e:
        _veh, _dat = set(), set()
        _negozio_buco = ("negozio dei veicoli illeggibile (%s: %s): "
                         "nessun veicolo/DAT escluso" % (type(e).__name__, e))
    _fuori = _veh | _dat
    names = [p.get("ticker") for p in ranked
             if p.get("ticker") and p.get("ticker").upper() not in _fuori]
    if max_names is not None:
        names = names[:max_names]
    # pesi in % del book investito (cassa esclusa: e' cosi' che il DB calcola peso_pct)
    _pesi = {}
    for p in ranked:
        if p.get("ticker"):
            _pesi.setdefault(p["ticker"], _peso_posizione(p))
    _peso_tot = sum(w for w in _pesi.values() if w is not None)
    vehicles = [p.get("ticker") for p in ranked
                if p.get("ticker") and p.get("ticker").upper() in _fuori]
    mos_list = []
    detail = []
    flagged_skipped = []
    no_model = []
    invalid_valuation = []
    quote_nd = {}
    weight_nd = []
    model_mos = {}
    mos_origine = {}   # v2 10/10: nome -> fonte del prezzo se NON e' la quota osservata
    _pos = {p.get("ticker"): p for p in ranked if p.get("ticker")}
    model_age = {}
    valuation_dates = {}
    observed_comparisons = {}
    for tk in names:
        upside = None
        try:
            if valuations is not None and tk in valuations:
                v = valuations.get(tk) or {}
                if _veicolo_dal_payload(v):
                    # Memo reali 09/10: un ETF non noto al negozio resta
                    # un veicolo: dichiarato come tale, mai "FV assente" ne' DCF.
                    vehicles.append(tk)
                    continue
                model_age[tk] = None   # valutazione consegnata dalla run in corso
                from bellomberg.valuation.dcf_quality import normalize_valuation_payload
                v = normalize_valuation_payload(v)
                if str(v.get("ticker") or "").upper() != tk.upper():
                    invalid_valuation.append(tk)
                    continue
                from bellomberg.reporting.valuation_quote import quote_comparison_text
                observed_comparisons[tk] = quote_comparison_text(v)
                fv = next((v[key] for key in ("fair_value", "fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base")
                           if v.get(key) is not None), None)
                fv, pr = _finite_number(fv), _finite_number(v.get("price"))
                if v.get("valuation_flagged"):
                    flagged_skipped.append(tk)
                elif fv is not None and pr is not None and pr > 0:
                    model_mos[tk] = _finite_number((fv / pr - 1) * 100)
                    if model_mos[tk] is not None:
                        upside, _motivo, _orig = _mos_corrente(v, fv, _pos.get(tk))
                        if _motivo:
                            quote_nd[tk] = _motivo
                        if _orig:
                            mos_origine[tk] = _orig
                    if v.get("valuation_date"):
                        valuation_dates[tk] = v["valuation_date"]
            else:
                # 21/07 (fix F17, causa vera dei "FV n.d."): PRIMA qui c'era
                # generate_valuation(tk), che a OGNI run riscriveva il modello SENZA
                # variant view e quindi AZZERAVA la tesi (guardia tesi-vs-file 17/07)
                # — nel run #46 ha invalidato tre tesi del book alle 13:56-13:58 senza
                # nessuna chiamata tool loggata. Lo scorer ora LEGGE il sidecar
                # dell'ultimo modello fatto dall'analista; senza modello (o piu'
                # vecchio di 30gg) il nome resta FUORI dal margine, dichiarato:
                # MAI rigenerare senza tesi (dottrina modello unico, PM 16/07).
                import os as _os
                import json as _json
                from datetime import date as _date
                import re as _re
                _safe = tk.replace(".", "_").replace("-", "_")
                _versioned_safe = _re.sub(r'[^A-Za-z0-9_-]', '_', tk)
                r = None
                _names = ["VAL_" + _safe + ".payload.json", "VAL_" + _safe + "_FLAGGED.payload.json"]
                _versioned = []
                if _os.path.isdir(REPORT_DIR):
                    _versioned = [name for name in _os.listdir(REPORT_DIR) if _re.fullmatch(
                        r"VAL_" + _re.escape(_versioned_safe) + r"_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.payload\.json", name)]
                if _versioned:
                    _names = sorted([name for name in _names + _versioned
                                     if _os.path.isfile(_os.path.join(str(REPORT_DIR), name))],
                                    key=lambda name: _os.path.getmtime(_os.path.join(str(REPORT_DIR), name)), reverse=True)
                for _name in _names:
                    _p = _os.path.join(str(REPORT_DIR), _name)
                    if _os.path.exists(_p):
                        try:
                            with open(_p, encoding="utf-8") as _f:
                                r = _json.load(_f)
                            from bellomberg.valuation.method_registry import is_record_method
                            if _name in _versioned and (not is_record_method(r.get('valuation_decision')) or
                                    _name != 'VAL_' + _versioned_safe + '_' + str(r.get('generation_id')) + '.payload.json'):
                                r = None
                            if "_FLAGGED" in _name:
                                r["valuation_flagged"] = True
                        except Exception:
                            r = None
                        break
                if r is not None and _veicolo_dal_payload(r):
                    # v2 10/10 (riserva BASSA): l'ETF letto da file (R0/R1) e' un veicolo come
                    # quello consegnato dalla run, non un "senza modello recente"
                    vehicles.append(tk)
                    continue
                _age_ok = False
                if r is not None:
                    try:
                        _eta = (_date.today() - _date.fromisoformat(
                            str(r.get("_timestamp", ""))[:10])).days
                        _age_ok = _eta <= 30
                        model_age[tk] = _eta   # modello letto da file: eta' dichiarata
                    except Exception:
                        _age_ok = False
                if r is None or not _age_ok:
                    no_model.append(tk)
                    continue
                from hashlib import sha256
                workbook_path = _p[:-len(".payload.json")] + ".xlsx"
                try:
                    with open(workbook_path, "rb") as workbook:
                        workbook_hash = sha256(workbook.read()).hexdigest()
                    if r.get("workbook_sha256") != workbook_hash:
                        invalid_valuation.append(tk)
                        continue
                except OSError:
                    invalid_valuation.append(tk)
                    continue
                if (r.get("sanity") or {}).get("severity") == "BLOCK":
                    r["valuation_flagged"] = True
                from bellomberg.valuation.dcf_quality import normalize_valuation_payload
                r = normalize_valuation_payload(r)
                if str(r.get("ticker") or "").upper() != tk.upper():
                    invalid_valuation.append(tk)
                    continue
                from bellomberg.reporting.valuation_quote import quote_comparison_text
                observed_comparisons[tk] = quote_comparison_text(r)
                # CATENA CANONICA del fair value (review 15/07): nessun engine emette
                # una chiave piatta "fair_value" (operating_v3 -> _weighted, bank -> _blend).
                # Zero e' un valore presente; un FV prioritario invalido non va
                # sostituito in silenzio da un campo subordinato.
                fv = next((r[key] for key in (
                    "fair_value_final", "fair_value_weighted", "fair_value_blend", "fair_value_base"
                ) if r.get(key) is not None), None)
                fv, pr = _finite_number(fv), _finite_number(r.get("price"))
                # audit/12 V0.5: un fair value FLAGGED dalla sanity non entra nel margine
                # di sicurezza del memo — prima un FV bocciato (0.08 su un ADR del book) contava come "-99.6%, sopravvalutato"
                if r.get("valuation_flagged"):
                    flagged_skipped.append(tk)
                elif fv is not None and pr is not None and pr > 0:
                    model_mos[tk] = _finite_number((fv / pr - 1) * 100)
                    if model_mos[tk] is not None:
                        upside, _motivo, _orig = _mos_corrente(r, fv, _pos.get(tk))
                        if _motivo:
                            quote_nd[tk] = _motivo
                        if _orig:
                            mos_origine[tk] = _orig
                    if r.get("valuation_date"):
                        valuation_dates[tk] = r["valuation_date"]
        except Exception:
            upside = None
            quote_nd.pop(tk, None)
        if upside is not None and _pesi.get(tk) is not None:
            mos_list.append(upside); detail.append((tk, upside))
        elif upside is not None:
            weight_nd.append(tk)
        elif tk not in flagged_skipped and tk not in quote_nd:
            invalid_valuation.append(tk)
    _nd = _message("n.d.: ", "n/a: ")
    _vehicles = list(dict.fromkeys(vehicles))
    _m_eleggibili = len([t for t in names if t not in _vehicles])
    # v2 10/10 (riserva BASSA): «% del book» coerente con «n/m nomi». Il denominatore della
    # copertura e' il peso dei nomi VALUTABILI a DCF (veicoli esclusi in entrambi); i veicoli
    # si dichiarano a parte con la loro quota del book.
    _peso_eleggibile = sum(_pesi.get(t) or 0 for t in dict.fromkeys(names) if t not in _vehicles)

    def _quota(tks):
        w = sum(_pesi.get(t) or 0 for t in tks)
        return "{:.1f}%".format(100.0 * w / _peso_tot) if _peso_tot else "n.d."

    # Buchi DICHIARATI: identici con e senza score (v2 10/10, riserva ALTO 1b: prima, senza
    # nessun nome misurabile, si ritornava None e il desk non vedeva NE' lo score NE' il perche').
    buchi = []; tag = []
    if _vehicles:
        buchi.append((_message("Veicoli (ETF/fondi), non valutati a DCF", "Vehicles (ETF/funds), not valued by DCF"),
                      _message("{t} ({q} del book)", "{t} ({q} of book)",
                               t=", ".join(_vehicles), q=_quota(_vehicles)), None))
    if flagged_skipped:
        # audit/12 V0.5: i modelli bocciati dalla sanity sono FUORI dal giudizio, dichiarati
        tag.append(_t(" [FLAGGED esclusi: {}]").format(", ".join(flagged_skipped)))
        buchi.append((_t("Modelli FLAGGED esclusi"), _nd + ", ".join(flagged_skipped), None))
    if no_model:
        # 21/07: buco DICHIARATO, non rigenerato alla cieca (il modello lo fa
        # l'analista con la sua variant view, non lo scorer)
        tag.append(_t(" [senza modello recente: {}]").format(", ".join(no_model)))
        buchi.append((_t("Senza modello recente (tocca all'analista)"),
                      _nd + ", ".join(no_model) + " (" + _quota(no_model) + ")", None))
    if quote_nd:
        # prezzo corrente assente/vecchio: il nome resta FUORI, mai il prezzo del modello zitto
        tag.append(_message(" [prezzo corrente n.d.: {t}]", " [current price n/a: {t}]",
                            t=", ".join(quote_nd)))
        buchi.append((_message("Prezzo corrente n.d. (MOS non calcolabile)", "Current price n/a (MOS not computable)"),
                      _nd + ", ".join("{} ({})".format(tk, m) for tk, m in quote_nd.items()), None))
    if weight_nd:
        tag.append(_message(" [peso n.d.: {t}]", " [weight n/a: {t}]", t=", ".join(weight_nd)))
        buchi.append((_message("Peso nel book n.d. (fuori dalla media)", "Book weight n/a (outside the average)"),
                      _nd + ", ".join(weight_nd), None))
    if invalid_valuation:
        tag.append(_t(" [FV/prezzo assenti o non validi: {}]").format(", ".join(invalid_valuation)))
        buchi.append((_t("FV/prezzo assenti o non validi"), _nd + ", ".join(invalid_valuation), None))
    if _negozio_buco:
        tag.append(_t(" [veicoli/DAT non esclusi: negozio non disponibile]"))
        buchi.append((_t("Negozio veicoli non disponibile"), _nd + _negozio_buco, None))

    if not mos_list:
        # n.d. DICHIARATO col motivo prevalente: il preambolo del desk (base.py `if sc:`) e il
        # cruscotto stampano la riga SCORE n.d. e le righe dei buchi
        cause = [(len(quote_nd), _message("prezzo corrente n.d.", "current price n/a")),
                 (len(no_model), _message("senza modello recente", "no recent model")),
                 (len(invalid_valuation), _message("FV/prezzo non validi", "invalid FV/price")),
                 (len(flagged_skipped), _message("modelli FLAGGED", "FLAGGED models")),
                 (len(weight_nd), _message("peso n.d.", "weight n/a"))]
        n_c, motivo = max(cause, key=lambda c: c[0])
        if n_c == 0:
            motivo = (_message("solo veicoli, nessun nome valutabile a DCF", "vehicles only, no DCF-valuable name")
                      if _vehicles else _message("portafoglio senza posizioni leggibili", "no readable positions"))
        verdict = _message("n.d. - {mo} su {k}/{m} nomi", "n/a - {mo} on {k}/{m} names",
                           mo=motivo, k=n_c, m=_m_eleggibili)
        lines = [(_message("Margine di sicurezza del book", "Book margin of safety"),
                  _message("n.d.: 0/{m} nomi con prezzo corrente e FV validi",
                           "n/a: 0/{m} names with current price and valid FV", m=_m_eleggibili), None)]
        return {"domain": "fundamentals", "score": None, "max_score": None, "verdict": verdict + "".join(tag),
                "lines": lines + buchi, "unscored": [lines[0][0]],
                "metrics": {"mos_book_pct": None, "n_valued": 0,
                            "copertura_book_pct": 0.0 if _peso_eleggibile else None,
                            "n_eleggibili": _m_eleggibili, "motivo_nd": motivo}}

    # Media PONDERATA sul peso del book (dove stanno i soldi), MOS troncato a +/-_MOS_CAP_PCT.
    _w_val = sum(_pesi[tk] for tk, _ in detail)
    clipped = {tk: max(-_MOS_CAP_PCT, min(_MOS_CAP_PCT, u)) for tk, u in detail}
    # arrotondato PRIMA delle bande: il numero stampato e' quello che decide i punti
    # (audit 09/10: 19,95 stampato "+20,0%" prendeva la banda sotto i 20)
    book_mos = round(sum(_pesi[tk] * clipped[tk] for tk, _ in detail) / _w_val, 1)
    troncati = [tk for tk, u in detail if clipped[tk] != u]
    quota_cara = 100.0 * sum(_pesi[tk] for tk, u in detail if u <= _MOS_CARO_PCT) / _w_val
    quota_sconto = 100.0 * sum(_pesi[tk] for tk, u in detail if u >= _MOS_SCONTO_PCT) / _w_val
    # v2 10/10 (riserva BASSA): cari o troncati (in entrambe le direzioni) = la parte del
    # valutato in cui la media ponderata nasconde di piu'
    quota_cari_tronc = 100.0 * sum(_pesi[tk] for tk, u in detail
                                   if u <= _MOS_CARO_PCT or tk in troncati) / _w_val
    copertura = 100.0 * _w_val / _peso_eleggibile if _peso_eleggibile else None
    # punteggio: piu' caro = piu' rischio (un solo asse), continuo fra +15% e -45%
    p_mos = _S.punti_mos(book_mos)
    lines = [(_message("Margine di sicurezza del book (ponderato, prezzo corrente)",
                       "Book margin of safety (weighted, current price)"),
              "{:+.1f}%".format(book_mos), p_mos),
             (_message("Copertura valutata (peso valutabile a DCF)", "Valued coverage (DCF-valuable weight)"),
              _message("{c} ({n}/{m} nomi)", "{c} ({n}/{m} names)",
                       c="{:.1f}%".format(copertura) if copertura is not None else "n.d.",
                       n=len(mos_list), m=_m_eleggibili), None),
             (_message("Quota cara (MOS<=-15%) / a sconto (MOS>=+15%) del valutato",
                       "Expensive (MOS<=-15%) / discounted (MOS>=+15%) share of valued"),
              "{:.1f}% / {:.1f}%".format(quota_cara, quota_sconto), None)]
    for tk, u in detail:
        _eta = model_age.get(tk)
        _orig = (_message("modello della run", "run model") if _eta is None else
                 _message("modello da file, {g} gg", "model from file, {g} days", g=_eta))
        _mod = model_mos.get(tk)
        _fonte = mos_origine.get(tk)
        lines.append((_message("  {tk} (peso {w:.1f}%)", "  {tk} (weight {w:.1f}%)", tk=tk, w=_pesi[tk]),
                      _message("{u} {f} | {m} al prezzo del modello ({d}) | {o}",
                               "{u} {f} | {m} at model price ({d}) | {o}",
                               u="{:+.1f}%".format(u),
                               f=(_message("corrente", "current") if _fonte is None
                                  else "(" + _fonte + ")"),
                               m="{:+.1f}%".format(_mod) if _mod is not None else "n.d.",
                               d=valuation_dates.get(tk) or "n.d.", o=_orig), None))
        if tk in observed_comparisons:
            lines.append((_t("quote.score_label") + tk, observed_comparisons[tk], None))
    if troncati:
        lines.append((_message("MOS troncati a +/-50% (outlier)", "MOS capped at +/-50% (outliers)"),
                      ", ".join("{} {:+.1f}%".format(tk, dict(detail)[tk]) for tk in troncati), None))
    score = p_mos; max_score = 3
    verdict = _verdict_bands(score, max_score, [_t("BOOK A SCONTO"), _t("VALUTAZIONE EQUA"), _t("BOOK CARO"), _t("BOOK MOLTO CARO")])
    # COPERTURA DICHIARATA in nomi E in peso: un giudizio sul 40% del book non si legge come
    # giudizio su tutto il portafoglio. Stesso universo per n/m e per la percentuale.
    verdict = _message("{v} ({n}/{m} nomi valutati, {c} del peso valutabile)",
                       "{v} ({n}/{m} names valued, {c} of valuable weight)", v=verdict, n=len(mos_list),
                       m=_m_eleggibili, c="{:.0f}%".format(copertura) if copertura is not None else "n.d.")
    if _vehicles:
        verdict += _message(" [veicoli {q} del book]", " [vehicles {q} of book]", q=_quota(_vehicles))
    if quota_cari_tronc >= 25.0:
        # la media ponderata puo' dire EQUA con un quarto del valutato molto caro o fuori scala
        verdict += _message(" [cari/troncati {q:.0f}% del valutato]", " [expensive/capped {q:.0f}% of valued]",
                            q=quota_cari_tronc)
    _da_file = [model_age[tk] for tk, _ in detail if model_age.get(tk) is not None]
    if _da_file:
        # la valutazione non e' della run: se ne dichiara l'eta'
        verdict += _message(" [modelli da file, eta' max {g} gg]", " [models from file, max age {g} days]",
                            g=max(_da_file))
    _px_db = [tk for tk, _ in detail if "position_prices" in str(mos_origine.get(tk) or "")]
    _px_fx = [tk for tk, _ in detail if "FX" in str(mos_origine.get(tk) or "")]
    if _px_db:
        verdict += _message(" [MOS su prezzo DB: {t}]", " [MOS on DB price: {t}]", t=", ".join(_px_db))
    if _px_fx:
        verdict += _message(" [FX storico (proxy): {t}]", " [historical FX (proxy): {t}]", t=", ".join(_px_fx))
    verdict += "".join(tag)
    lines += buchi
    return {"domain": "fundamentals", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "metrics": {"mos_book_pct": round(book_mos, 1), "n_valued": len(mos_list),
                                        "copertura_book_pct": round(copertura, 1) if copertura is not None else None,
                                        "quota_cara_pct": round(quota_cara, 1),
                                        "quota_sconto_pct": round(quota_sconto, 1),
                                        "n_troncati": len(troncati),
                                        "quota_cari_troncati_pct": round(quota_cari_tronc, 1),
                                        "n_proxy_prezzo": len(mos_origine)}}


@scoped_language
def options_score(proxy_ticker=None, portfolio_data=None, options_data=None, *, vix_ts=None, vol_surface=None):
    """Regime di VOLATILITA' del mercato (proxy SPY). PIU' ALTO = piu' stress/protezione cara.

    Rifatto il 09/10 (Opus 5.5, decisione PM «correggi tutto», audit SCORE-VOL-QUANT §1). Prima
    lo score era il put/call di open interest di UNA scadenza di SPY con soglie da azionario:
    33 -> 100 («PANICO») con VIX fermo a 15 in contango, e un crash con IV 60 valeva quanto un
    mercato placido con IV 12. Ora, punti CONTINUI fra ancore:
    - struttura a termine VIX/VIX3M (get_vix_term_structure), metrica PRINCIPALE a peso doppio
      (0..6): e' l'indicatore standard che separa backwardation da stress e contango placido;
    - IV ATM a ~30 giorni contro la realizzata a 21 sedute (finestra della superficie v2): stesso
      orizzonte, premio della protezione (0..3);
    - RR25 a 30-45 giorni dalla superficie (skew vero fra strike diversi, non put-call IV allo
      stesso strike, che per parita' e' rumore) (0..3);
    - put/call di open interest: SOLO INFORMATIVO (aggregato sulle scadenze >= 2 giorni della
      superficie, nessuno storico per un percentile; il P/C di una sola scadenza dipende dal
      calendario delle scadenze piu' che dal mercato). Chain parziale = n.d. dichiarato.
    Il livello del VIX non entra: lo punteggia gia' macro_score (niente doppio conteggio).
    Seconda versione 10/10 (Opus 5.5, riserve della review): il regime lo danno SOLO struttura
    VIX/VIX3M (0..6) e RR25 (0..3); IV-RV e' il sotto-verdetto informativo «prezzo della
    protezione» (nel crash IV < RV toglieva punti allo stress); pavimento IN STRESS con
    VIX/VIX3M >= l'ancora piena; etichette di verdetto corte, dettaglio nelle righe. 10/10:
    ancore, prezzo della protezione (IV/RV) e skew normalizzato da core/soglie_score.
    Percorso vivo (nessun dato passato): scarica VIX, superficie e opzioni di SPY. Se il
    chiamante passa SOLO options_data, VIX e superficie sono «non calcolati da questo percorso»."""
    from bellomberg.core.presentation import message as _message
    if not proxy_ticker:
        # audit/11 §4: soglie da INDICE, mai il primo ticker USA del book. SPY = proxy di regime.
        proxy_ticker = "SPY"
    vivo = options_data is None and vix_ts is None and vol_surface is None
    if vivo:
        try:
            from bellomberg.agents import agent_tools
            options_data = agent_tools.tool_get_options_data(proxy_ticker)
        except Exception as e:
            options_data = {"error": type(e).__name__ + ": " + str(e)[:120]}
        try:
            from bellomberg.portfolio.positioning_tools import get_vix_term_structure
            vix_ts = get_vix_term_structure()
        except Exception as e:
            vix_ts = {"error": type(e).__name__ + ": " + str(e)[:120]}
        try:
            from bellomberg.portfolio.vol_surface import build_vol_surface
            vol_surface = build_vol_surface(proxy_ticker, max_expiries=6)
        except Exception as e:
            vol_surface = {"error": type(e).__name__ + ": " + str(e)[:120]}
    domain = "options ({})".format(proxy_ticker or "?")

    def _non_calcolato(cosa):
        return _message("{cosa} non calcolato da questo percorso", "{cosa} not computed by this path", cosa=cosa)

    def _vix_eta_giorni(iso):
        from datetime import date as _d
        try:
            return (_d.today() - _d.fromisoformat(str(iso)[:10])).days
        except ValueError:
            return None

    lines = []; pts = []; unscored = []; info = []

    def add(label, fmt, p_, motivo=None):
        if p_ is not None:
            lines.append((label, fmt, p_)); pts.append(p_)
        else:
            lines.append((label, _message("n.d.: {motivo}", "n/a: {motivo}", motivo=motivo or _message(
                "dato non fornito dal tool", "value not provided by the tool")), None))
            unscored.append(label)

    # ---- ANCORE (fix score 09/10; 10/10 una sola taratura in core/soglie_score). ----
    # VIX/VIX3M: ancore 0 -> 2 -> 4 -> 6 = percentili 50/75/90/97 della storia CBOE dal 2009
    # (oggi 0,884/0,934/0,985/1,046), interpolate. Peso DOPPIO: e' la metrica principale.
    # get_vix_term_structure etichetta CONTANGO/FLAT/BACKWARDATION dalle STESSE costanti.
    _TS = _S.VIX3M_ANCORE
    # Prezzo della protezione (fix v2 10/10, riserva 5; 21 = finestra B2 della superficie v2):
    # sotto-verdetto INFORMATIVO, non regime (nel crash la realizzata corre piu' dell'implicita
    # e toglieva punti allo stress). 10/10 (Opus 5.5): misura INVARIANTE DI SCALA, rapporto
    # IV ATM ~30g / realizzata 21 sedute, soglie calibrate (a sconto < p20, cara >= p80 di
    # VIX / realizzata S&P 500, core/soglie_score.VRP_*). Prima differenza in punti con «cara»
    # a +6 qui e a +3 nella superficie e nel segnale.
    # Skew (v3 10/10): -RR25 a 30-45g in PUNTI VOL ASSOLUTI, ancore 3/5/7/9 -> 0..3
    # (core/soglie_score.SKEW_RR25_ANCORE_PT, convenzione SPX 1 mese, non calibrate). Uno skew
    # call (RR25 > 0) vale 0. NON normalizzato per l'IV ATM: il rapporto RR25/ATM scende nei
    # crash (calmo -4,5/14 = 0,32, crash -8/42 = 0,19) e toglieva punti allo stress; resta
    # come riga INFORMATIVA (e nella lettura della superficie).
    _RR = _S.SKEW_RR25_ANCORE_PT
    # PAVIMENTO STRESS: con VIX/VIX3M >= l'ancora piena (p97, backwardation profonda) il
    # verdetto e' almeno «IN STRESS», qualunque sia lo skew.
    PAVIMENTO_TS_STRESS = _S.VIX3M_PAVIMENTO_STRESS

    # 1) STRUTTURA A TERMINE VIX
    _ts_label = _message("Struttura VIX/VIX3M (peso 2)", "VIX/VIX3M term structure (weight 2)")
    ts_ratio = None
    if vix_ts is None:
        add(_ts_label, None, None, _non_calcolato(_message("struttura VIX", "VIX term structure")))
    elif not isinstance(vix_ts, dict) or vix_ts.get("error"):
        add(_ts_label, None, None, _message("struttura VIX non disponibile ({e})", "VIX term structure unavailable ({e})",
                                            e=str((vix_ts or {}).get("error") if isinstance(vix_ts, dict) else type(vix_ts).__name__)[:120]))
    else:
        v30, v3m = _finite_number(vix_ts.get("vix_30d")), _finite_number(vix_ts.get("vix_3m"))
        ts_ratio = (v30 / v3m) if (v30 and v3m and v3m > 0) else _finite_number(vix_ts.get("vix_vix3m_ratio"))
        _asof = vix_ts.get("vix_asof") or {}
        _date = (sorted({str(_asof.get(k)) for k in ("vix_30d", "vix_3m") if _asof.get(k)})
                 if isinstance(_asof, dict) else [])
        if ts_ratio is None:
            add(_ts_label, None, None, _message("VIX3M mancante: rapporto non calcolabile", "VIX3M missing: ratio cannot be computed"))
        elif isinstance(_asof, dict) and _asof and len(_date) > 1:
            # due chiusure di giorni diversi non fanno una curva: buco dichiarato, mai un rapporto misto
            add(_ts_label, None, None, _message("VIX e VIX3M di date diverse ({d})", "VIX and VIX3M from different dates ({d})", d=", ".join(_date)))
            ts_ratio = None
        elif (_date and _vix_eta_giorni(_date[0]) is not None
              and _vix_eta_giorni(_date[0]) > _S.ETA_MAX_GIORNALIERA_GG):
            # chiusura piu' vecchia del limite delle serie giornaliere (6 giorni, lo stesso della
            # macro sullo stesso VIX): STALE
            add(_ts_label, None, None, _message("STALE: ultima chiusura VIX {d}", "STALE: last VIX close {d}", d=_date[0]))
            ts_ratio = None
        else:
            # stessa classificazione dello strumento (core/soglie_score.struttura_vix)
            stato = {"CONTANGO": _message("contango", "contango"),
                     "FLAT": _message("contango sotto la norma", "below-normal contango"),
                     "BACKWARDATION": _message("backwardation", "backwardation")}.get(_S.struttura_vix(ts_ratio), "n.d.")
            add(_ts_label, "{:.3f} {}{}".format(ts_ratio, stato, (" (" + _date[0] + ")") if _date else
                                               _message(" (data osservazione n.d.)", " (observation date n/a)")),
                _interp(ts_ratio, _TS, _S.VIX3M_PUNTI))

    # 2-3) SUPERFICIE: RR25 30-45g (punteggio), IV 30g vs RV (prezzo della protezione, informativo)
    _ivrv_label = _message("Prezzo della protezione: IV ATM ~30g / realizzata 21 sedute",
                           "Protection price: ATM IV ~30d / 21-session realized")
    _ivrv_motivo = _message("prezzo, non regime: fuori punteggio (nel crash la realizzata supera l'implicita)",
                            "price, not regime: not scored (in a crash realized vol exceeds implied)")

    def _gg(n):
        return _message("{n}g", "{n}d", n=n)
    _rr_label = _message("Skew RR25 30-45g (put-call 25Δ)", "RR25 skew 30-45d (25Δ put-call)")
    ivrv = ivrv_ratio = rr = rr_norm = None
    prezzo_protezione = None   # codice stabile: DISCOUNT / NORMAL / EXPENSIVE
    if vol_surface is None:
        _m = _non_calcolato(_message("superficie di volatilita'", "volatility surface"))
        add(_rr_label, None, None, _m)
        info.append((_ivrv_label, _message("n.d.: {m}", "n/a: {m}", m=_m), _ivrv_motivo))
    elif not isinstance(vol_surface, dict) or vol_surface.get("error"):
        _e = str((vol_surface or {}).get("error") if isinstance(vol_surface, dict) else type(vol_surface).__name__)[:120]
        _m = _message("superficie non disponibile ({e})", "surface unavailable ({e})", e=_e)
        add(_rr_label, None, None, _m)
        info.append((_ivrv_label, _message("n.d.: {m}", "n/a: {m}", m=_m), _ivrv_motivo))
    else:
        _ts_rows = [r for r in (vol_surface.get("term_structure") or [])
                    if isinstance(r, dict) and (_finite_number(r.get("days")) or 0) >= 2]
        _cov_rows = {r.get("expiry"): r for r in ((vol_surface.get("coverage") or {}).get("rows") or []) if isinstance(r, dict)}

        def _parziale(r):
            st = _cov_rows.get(r.get("expiry")) or {}
            return st.get("status") == "partial" or st.get("chain_complete") is False

        def _vicina(lo, hi, centro):
            cand = [r for r in _ts_rows if lo <= r["days"] <= hi]
            return min(cand, key=lambda r: abs(r["days"] - centro)) if cand else None
        # integrazione 10/10: la superficie v2 espone realized_vol_21d (realized_vol_30d e' il suo alias)
        rv = _finite_number(vol_surface.get("realized_vol_21d", vol_surface.get("realized_vol_30d")))
        s30 = _vicina(20, 45, 30)
        _ivrv_nd = None
        if s30 is None:
            _ivrv_nd = _message("nessuna scadenza fra 20 e 45 giorni nella superficie", "no expiry between 20 and 45 days in the surface")
        elif _parziale(s30):
            _ivrv_nd = _message("chain parziale sulla scadenza {e}", "partial chain on expiry {e}", e=s30.get("expiry"))
        elif rv is None or _finite_number(s30.get("atm_iv")) is None:
            _ivrv_nd = _message("IV o realizzata mancante", "IV or realized vol missing")
        else:
            ivrv = (s30["atm_iv"] - rv) * 100.0
            prezzo_protezione, ivrv_ratio = _S.prezzo_protezione(s30["atm_iv"], rv)
            _pp = {"DISCOUNT": _message("a sconto sulla realizzata", "at a discount to realized"),
                   "NORMAL": _message("nella norma", "normal"),
                   "EXPENSIVE": _message("cara", "expensive")}.get(prezzo_protezione)
            if _pp is None:
                _ivrv_nd = _message("IV o realizzata non positive", "IV or realized vol not positive")
            else:
                info.append((_ivrv_label, _message(
                    "{iv:.1f} / {rv:.1f} = {r:.2f}x ({e}, {g}): {pp} [a sconto < {a:.2f}x, cara >= {c:.2f}x]",
                    "{iv:.1f} / {rv:.1f} = {r:.2f}x ({e}, {g}): {pp} [discount < {a:.2f}x, expensive >= {c:.2f}x]",
                    iv=s30["atm_iv"] * 100, rv=rv * 100, r=ivrv_ratio, e=s30.get("expiry"), g=_gg(int(s30["days"])),
                    pp=_pp, a=_S.VRP_SCONTO, c=_S.VRP_CARA), _ivrv_motivo))
        if _ivrv_nd is not None:
            info.append((_ivrv_label, _message("n.d.: {m}", "n/a: {m}", m=_ivrv_nd), _ivrv_motivo))
        s37 = _vicina(30, 45, 37)
        if s37 is None:
            add(_rr_label, None, None, _message("nessuna scadenza fra 30 e 45 giorni nella superficie", "no expiry between 30 and 45 days in the surface"))
        elif _parziale(s37):
            add(_rr_label, None, None, _message("chain parziale sulla scadenza {e}", "partial chain on expiry {e}", e=s37.get("expiry")))
        elif _finite_number(s37.get("rr25")) is None:
            add(_rr_label, None, None, _message("RR25 non misurabile (delta 25 assente)", "RR25 not measurable (no 25-delta strikes)"))
        else:
            rr = s37["rr25"] * 100.0
            add(_rr_label, "{:+.1f} pt ({}, {})".format(rr, s37.get("expiry"), _gg(int(s37["days"]))), _interp(-rr, _RR))
            # skew normalizzato: INFORMATIVO (n.d. dichiarato se manca l'IV ATM della scadenza)
            rr_norm = _S.skew_normalizzato(s37["rr25"], s37.get("atm_iv"))
            info.append((_message("Skew normalizzato RR25/IV ATM", "Normalized skew RR25/ATM IV"),
                         ("{:+.2f} ({}, {})".format(rr_norm, s37.get("expiry"), _gg(int(s37["days"])))
                          if rr_norm is not None else
                          _message("n.d.: IV ATM della scadenza mancante", "n/a: expiry ATM IV missing")),
                         _message("scende nei crash (l'IV ATM sale piu' dello skew): fuori punteggio",
                                  "falls in crashes (ATM IV rises faster than skew): not scored")))
        # P/C aggregato sulle scadenze >= 2 giorni: INFORMATIVO
        _ok = [r for r in _ts_rows if not _parziale(r)
               and _finite_number(r.get("put_oi")) is not None and _finite_number(r.get("call_oi")) is not None]
        _c = sum(r["call_oi"] for r in _ok); _p = sum(r["put_oi"] for r in _ok)
        if _ok and _c > 0:
            info.append((_message("Put/Call OI aggregato", "Aggregate put/call OI"),
                         _message("{v:.2f} su {n} scadenze >= 2g", "{v:.2f} over {n} expiries >= 2d", v=_p / _c, n=len(_ok)),
                         _message("quantita' di coperture, non il loro prezzo; nessuno storico per un percentile: fuori punteggio",
                                  "amount of hedges, not their price; no history for a percentile: not scored")))

    # P/C di UNA scadenza dal tool opzioni: INFORMATIVO, con scadenza e copertura dichiarate
    expiry_reason = None
    atm_single = None
    _pc_label = _message("Put/Call OI (una scadenza)", "Put/call OI (single expiry)")
    _pc_motivo = _message("dipende dal calendario delle scadenze: fuori punteggio", "depends on the expiry calendar: not scored")
    if isinstance(options_data, dict) and not options_data.get("error"):
        from bellomberg.core.options_expiry import valid_expiry
        try:
            declared = [options_data[key] for key in ("expiry_used", "nearest_expiry") if key in options_data]
            if not declared:
                raise ValueError("expiry_missing")
            expiries = {valid_expiry(value) for value in declared}
            if len(expiries) != 1:
                raise ValueError("expiry_conflicting")
            _exp = next(iter(expiries))
        except ValueError as exc:
            expiry_reason = str(exc)
            info.append((_pc_label, "n.d.: " + expiry_reason, _pc_motivo))
        else:
            from datetime import date as _date
            _dte = (_date.fromisoformat(_exp) - _date.today()).days
            pcr = _finite_number(options_data.get("put_call_oi_ratio"))
            _cov = options_data.get("coverage") if isinstance(options_data.get("coverage"), dict) else {}
            if options_data.get("partial") is True or _cov.get("status") not in (None, "COMPLETE"):
                info.append((_pc_label, _message("n.d.: chain parziale ({s})", "n/a: partial chain ({s})",
                                                 s=_cov.get("status") or "partial"), _pc_motivo))
            elif _dte < 2:
                info.append((_pc_label, _message("n.d.: scadenza a {d} giorni (< 2: microstruttura di chiusura)",
                                                 "n/a: expiry in {d} days (< 2: closing microstructure)", d=_dte), _pc_motivo))
            elif pcr is not None:
                info.append((_pc_label, "{:.2f} ({}, {})".format(pcr, _exp, _gg(_dte)), _pc_motivo))
            # ATM IV di UNA scadenza (R02): mai punteggiata (il regime si legge a ~30g sulla
            # superficie); mostrata solo se qualificata e valida, altrimenti n.d. col motivo
            atm_status = options_data.get("atm_status")
            _iv_motivo = _message("IV di una sola scadenza: fuori punteggio (regime letto a ~30g)",
                                  "single-expiry IV: not scored (regime read at ~30d)")
            _civ = _finite_number(options_data.get("atm_iv_call_pct"))
            _piv = _finite_number(options_data.get("atm_iv_put_pct"))
            _iv_invalid = [k for k, v in (("call", _civ), ("put", _piv))
                           if options_data.get("atm_iv_" + k + "_pct") is not None and (v is None or v <= 0)]
            if atm_status is not None and atm_status != "QUALIFIED":
                info.append(("ATM IV", "n.d.: " + str(atm_status) + ": " + ", ".join(str(x) for x in (options_data.get("atm_issues") or [])), _iv_motivo))
            else:
                if _iv_invalid:
                    # review R02 (10/10): il motivo dice COSA non va per lato (una stringa
                    # "0.25" non e' «<= 0»: e' non numerica), nella lingua del memo
                    def _perche_iv(k):
                        raw = options_data.get("atm_iv_" + k + "_pct")
                        if isinstance(raw, bool) or not isinstance(raw, Real):
                            return _message("{k} non numerica", "{k} not numeric", k=k)
                        if not math.isfinite(raw):
                            return _message("{k} non finita", "{k} not finite", k=k)
                        return _message("{k} <= 0", "{k} <= 0", k=k)
                    info.append(("ATM IV", _message("n.d.: IV non valida: {d}", "n/a: invalid IV: {d}",
                                                    d=", ".join(str(_perche_iv(k)) for k in _iv_invalid)), _iv_motivo))
                _ok_iv = [v for v in (_piv, _civ) if v is not None and v > 0]
                if _ok_iv:
                    atm_single = _ok_iv[0]
                    info.append((_message("ATM IV ({e}, {n}g)", "ATM IV ({e}, {n}d)", e=_exp, n=_dte),
                                 "{:.1f}%".format(atm_single), _iv_motivo))
    elif isinstance(options_data, dict) and options_data.get("error"):
        info.append((_pc_label, "n.d.: " + str(options_data.get("error"))[:120], _pc_motivo))

    metrics = {"atm_iv": atm_single, "vix_vix3m_ratio": ts_ratio, "iv_rv_30d_pts": ivrv, "rr25_30_45d_pts": rr,
               "iv_rv_ratio_30d": ivrv_ratio, "vix_term_structure": _S.struttura_vix(ts_ratio),
               "rr25_atm_ratio": rr_norm, "protection_price": prezzo_protezione,
               "put_call_oi": (_finite_number(options_data.get("put_call_oi_ratio"))
                               if isinstance(options_data, dict) and expiry_reason is None else None)}
    if not pts:
        reason = expiry_reason or "no_regime_metric"
        # etichetta CORTA (cella Verdetto del PDF, riserva 1): il dettaglio sta nelle righe
        verdict = "n.d. - " + (expiry_reason or _message("regime non misurato", "regime not measured"))
        return {"domain": domain, "score": None, "max_score": None, "verdict": verdict,
                "unavailable_reason": reason, "lines": lines or [("Expiry", reason, None)],
                "unscored": unscored, "info": info, "metrics": metrics}
    score = round(sum(pts), 2)
    # massimo: 6 la struttura VIX (peso doppio), 3 lo skew; il massimo di RIGA va anche al
    # blocco testo, che ne ricava la fascia (riserva 2: 2,98/6 non e' «critico»)
    line_max = {_ts_label: 6}
    max_score = sum(line_max.get(l[0], 3) for l in lines if l[2] is not None)
    floors = []
    if ts_ratio is not None and ts_ratio >= PAVIMENTO_TS_STRESS:
        floors.append("TS_BACKWARDATION")
        # «almeno IN STRESS» = il bordo della banda critica comune (core/soglie_score)
        _bordo = _S.BANDE_INDICE[2]
        _soglia = round(_bordo * max_score, 2)
        if _soglia / max_score < _bordo:
            _soglia = round(_soglia + 0.01, 2)
        if score < _soglia:
            info.append((_message("Pavimento del verdetto", "Verdict floor"),
                         _message("VIX/VIX3M {r:.3f} >= {s:.2f} (backwardation profonda): punteggio alzato di {a} a {n} (verdetto almeno IN STRESS)",
                                  "VIX/VIX3M {r:.3f} >= {s:.2f} (deep backwardation): score raised by {a} to {n} (verdict at least STRESSED)",
                                  r=ts_ratio, s=PAVIMENTO_TS_STRESS, a=round(_soglia - score, 2), n=_soglia),
                         _message("regola di struttura a termine", "term-structure rule")))
            score = _soglia
    metrics["floors"] = floors
    # etichette CORTE (cella Verdetto del PDF <= 195 pt in Arial-Bold 8,5, riserva 1): il
    # perche' (contango/backwardation, prezzo della protezione) sta nelle righe
    verdict = _verdict_bands(score, max_score, [
        _message("VOL CALMA", "CALM VOL"),
        _message("VOL NORMALE", "NORMAL VOL"),
        _message("VOL TESA", "TENSE VOL"),
        _message("VOL IN STRESS", "STRESSED VOL")])
    return {"domain": domain, "score": score, "max_score": max_score, "line_max": line_max,
            "verdict": verdict, "lines": lines, "unscored": unscored, "info": info, "metrics": metrics}


# ============================================================
# CRYPTO (fix 09/10, Opus 5.5 — audit D-C1..D-C5)
# Prima: massimo dei 5 funding PIU' ALTI dell'intero Hyperliquid (~200 perp). In 5 memo su 7
# il verdetto l'ha deciso una memecoin da 2-3 M$ di open interest, e il premio entrava in
# valore assoluto (la capitolazione usciva «surriscaldato»). Ora: funding e premio dei MAJOR,
# pesati per open interest, dal `top_10_perps_by_oi` del payload (lo stesso universo che usa
# il controllo di freschezza). Il rischio di squeeze sistemico sta dove sta la leva: il
# funding di un perp da 2 M$ di OI e' rumore di microstruttura, non il mercato.
# ============================================================
# Major = capitalizzazione e OI maggiori con mercato spot profondo. Token nativi delle
# venue e altcoin restano FUORI: il loro funding e' idiosincratico.
_CRYPTO_MAJOR = ("BTC", "ETH", "SOL")
# Copertura minima: BTC ed ETH insieme portano la gran parte dell'OI dei major; senza uno
# dei due la media pesata non rappresenta il mercato e la riga esce n.d.
_CRYPTO_OBBLIGATORI = ("BTC", "ETH")
# Tasso base Hyperliquid: 0,125 bp/h x 24 x 365 = 1095 bp annualizzati. Formula (doc
# Hyperliquid, v2 10/10): funding a 8 ore F = P + clamp(I - P, -5 bp, +5 bp), con I = 1 bp
# (interesse) e P = premio MEDIO dell'ora; si paga a ottavi ogni ora. Con P fra -4 bp e
# +6 bp il clamp riporta F esattamente a I: il funding resta al base
# annualizzato = NEUTRO, non «long che pagano». (La v1 diceva «entro circa +-5 bp»: sbagliato, la
# zona e' asimmetrica.) Fuori dalla zona F = P -/+ 5 bp. Le bande si misurano come SCARTO
# dal base, in entrambe le direzioni.
_HL_TASSO_BASE_ANN = 10.95
# v2 (decisione PM «correggi tutto», ALTO 2): il PREMIO esce dal punteggio e resta
# INFORMATIVO. Su Hyperliquid funding e premio sono la STESSA misura: il funding e' la media
# oraria del premio passata per il clamp. Punteggiarli entrambi contava due volte lo stesso
# posizionamento (premio medio +10 bp -> funding scarto +43,8 -> 3 punti + 1 punto di premio).
# Scelto l'informativo, non le bande «scarto fuori zona 2/5/10 bp»: anche ricalibrato il
# premio resterebbe una funzione del funding e la sua sola informazione in piu' e' il
# DISACCORDO fra l'istante (premio) e la media dell'ora (funding), che serve al verdetto,
# non al punteggio. Zona neutra del premio e scarto minimo perche' il disaccordo conti:
# sotto 2 bp fuori zona il premio istantaneo dei major e' rumore di microstruttura.
_HL_PREMIO_NEUTRO_BP = (-4.0, 6.0)
_CRYPTO_PREMIO_DISCORDE_BP = 2.0
# Funding e premio sono tassi istantanei (si aggiornano ogni ora): un payload piu' vecchio
# di 24 ore non descrive il posizionamento corrente.
_CRYPTO_ETA_MAX_ORE = 24.0


@scoped_language
def crypto_score(intel=None, has_crypto=True, adesso=None):
    """Posizionamento ESTREMO sui perp dei major (Hyperliquid), in entrambe le direzioni.
    PIU' ALTO = piu' affollato: long affollati (EUFORICO) o short/capitolazione (CAPITOLAZIONE),
    la direzione la dice il segno del FUNDING (v2: unica metrica punteggiata; il premio e'
    informativo e, se rilevante e di segno opposto, da' «CRYPTO SEGNALI DISCORDI»).
    `adesso` (datetime con fuso) solo per i test."""
    if intel is None:
        try:
            from bellomberg.agents import agent_tools
            intel = agent_tools.tool_get_hyperliquid_intel()
        except Exception:
            return None
    if not isinstance(intel, dict) or intel.get("error"):
        return None
    from datetime import datetime as _dt, timezone as _tz
    from bellomberg.core.presentation import message as _message
    righe = intel.get("top_10_perps_by_oi")
    major = {}
    for r in (righe if isinstance(righe, list) else []):
        if isinstance(r, dict):
            a = str(r.get("asset") or "").strip().upper()
            if a in _CRYPTO_MAJOR and a not in major:
                major[a] = r
    if not major:
        return None   # nessun major nel payload: niente da misurare (lo dichiara il cruscotto)

    def pesata(campo):
        num = den = 0.0
        usati = []
        for a in _CRYPTO_MAJOR:
            r = major.get(a)
            if r is None:
                continue
            v, w = _finite_number(r.get(campo)), _finite_number(r.get("oi_usd_m"))
            if v is None or w is None or w <= 0:
                continue
            num += v * w; den += w; usati.append(a)
        mancanti = [a for a in _CRYPTO_OBBLIGATORI if a not in usati]
        return (num / den if den > 0 and not mancanti else None), usati, mancanti

    fund, usati_f, manca_f = pesata("funding_annualized_pct")
    prem, usati_p, manca_p = pesata("premium_basis_pts")

    lines = []; pts = []; unscored = []; excluded = {}
    # Freschezza: istante di osservazione DICHIARATO dal tool (fetched_at_utc).
    adesso = adesso or _dt.now(_tz.utc)
    oss = intel.get("fetched_at_utc")
    eta_ore = None
    stale_motivo = None
    lab_oss = _message("Osservazione Hyperliquid", "Hyperliquid observation")
    try:
        t = _dt.fromisoformat(str(oss).replace("Z", "+00:00")) if oss else None
        if t is not None and t.tzinfo is None:
            t = None
    except ValueError:
        t = None
    if t is None:
        # avviso, non blocco: il dato e' live ma la sua eta' non e' verificabile
        lines.append((lab_oss, _message("n.d.: timestamp assente o invalido, freschezza non verificabile",
                                        "n/a: timestamp missing or invalid, freshness not verifiable"), None))
        excluded[lab_oss] = _message("informazione di freschezza, non una metrica",
                                     "freshness information, not a metric")
    else:
        eta_ore = (adesso - t).total_seconds() / 3600.0
        if eta_ore < 0:
            stale_motivo = _message("osservazione futura ({t})", "future observation ({t})", t=str(oss)[:19])
        elif eta_ore > _CRYPTO_ETA_MAX_ORE:
            stale_motivo = _message("STALE: osservato {h:.0f} ore fa (limite {l:.0f})",
                                    "STALE: observed {h:.0f} hours ago (limit {l:.0f})",
                                    h=eta_ore, l=_CRYPTO_ETA_MAX_ORE)
        lines.append((lab_oss, "%s (%.1f h)" % (str(oss)[:19], eta_ore), None))
        excluded[lab_oss] = _message("informazione di freschezza, non una metrica",
                                     "freshness information, not a metric")

    lab_f = _message("Funding major pesato OI", "Major funding, OI-weighted")
    lab_p = _message("Premio perp major pesato OI (informativo)", "Major perp premium, OI-weighted (informational)")
    # Funding: |scarto dal tasso base|, punti CONTINUI fra ancore calibrate (10/10, Opus 5.5)
    # sullo stesso scarto negli ultimi 2 anni orari di Hyperliquid: le etichette NORMALE /
    # SURRISCALDATO / EUFORICO partono ai percentili 50/80/95, il p99 satura a 3 punti
    # (core/soglie_score.CRYPTO_FUNDING_ANCORE/PUNTI, oggi 3,35/11,7/27/53,45).
    # Prima 5/15/30 a gradino, di giudizio: con massimo 3 ogni gradino era un'etichetta intera.
    # DIREZIONE = segno del funding (chi PAGA davvero: long che pagano gli short o viceversa),
    # dichiarata solo fuori dalla banda calma.
    scarto_f = None if fund is None else fund - _HL_TASSO_BASE_ANN
    direzione = 0
    if fund is None:
        lines.append((lab_f, _message("n.d.: major obbligatori mancanti nel payload ({m})",
                                      "n/a: required majors missing from the payload ({m})",
                                      m=", ".join(manca_f) or "-"), None))
        unscored.append(lab_f)
    else:
        fmt_f = _message("{f:+.1f}%/anno (base 10,95%: scarto {e:+.1f}) [{u}]",
                         "{f:+.1f}%/yr (base 10.95%: gap {e:+.1f}) [{u}]",
                         f=fund, e=scarto_f, u=", ".join(usati_f))
        if stale_motivo is not None:
            lines.append((lab_f, _message("n.d.: {v} non corrente ({m})", "n/a: {v} not current ({m})",
                                          v=fmt_f, m=stale_motivo), None))
            unscored.append(lab_f)
        else:
            p = _interp(abs(scarto_f), _S.CRYPTO_FUNDING_ANCORE, _S.CRYPTO_FUNDING_PUNTI)
            direzione = 0 if _S.banda(p / 3.0) == 0 else (1 if scarto_f > 0 else -1)
            lines.append((lab_f, fmt_f, p)); pts.append(p)
    # Premio con SEGNO, come scarto fuori dalla zona neutra -4/+6 bp: positivo = perp sopra
    # l'oracolo (long che spingono), negativo = sotto (short dominanti). Informativo.
    scarto_p = None
    if prem is None:
        val_p = _message("n.d.: major obbligatori mancanti nel payload ({m})",
                         "n/a: required majors missing from the payload ({m})", m=", ".join(manca_p) or "-")
    else:
        lo, hi = _HL_PREMIO_NEUTRO_BP
        scarto_p = prem - hi if prem > hi else prem - lo if prem < lo else 0.0
        val_p = _message("{p:+.1f} bps (zona neutra -4/+6: scarto {s:+.1f}) [{u}]",
                         "{p:+.1f} bps (neutral zone -4/+6: gap {s:+.1f}) [{u}]",
                         p=prem, s=scarto_p, u=", ".join(usati_p))
        if stale_motivo is not None:
            val_p = _message("n.d.: {v} non corrente ({m})", "n/a: {v} not current ({m})", v=val_p, m=stale_motivo)
    lines.append((lab_p, val_p, None))
    excluded[lab_p] = _message("informativo: il funding Hyperliquid e' la media oraria di questo premio "
                               "(stessa misura, niente doppio conteggio)",
                               "informational: Hyperliquid funding is the hourly average of this premium "
                               "(same measure, no double count)")
    # ALTO 1 (v2): funding e premio istantaneo rilevanti e di SEGNO OPPOSTO = la media
    # dell'ora e l'istante si contraddicono (flash crash con funding ancora alto, squeeze con
    # funding ancora negativo). Il posizionamento sta girando: ne' EUFORICO ne' CAPITOLAZIONE.
    discordi = (direzione != 0 and stale_motivo is None and scarto_p is not None
                and abs(scarto_p) >= _CRYPTO_PREMIO_DISCORDE_BP and (scarto_p > 0) != (direzione > 0))

    # il funding e' l'unica metrica punteggiata: senza, nessun verdetto
    if lab_f in unscored:
        score = max_score = None
        verdict = _nd_copertura()
    else:
        score = sum(pts); max_score = 3 * len(pts)
        if discordi:
            verdict = _message("CRYPTO SEGNALI DISCORDI", "CRYPTO MIXED SIGNALS")
        else:
            if direzione < 0:
                etichette = [_t("CRYPTO CALMO"), _t("CRYPTO NORMALE"),
                             _message("CRYPTO SOTTO PRESSIONE (short)", "CRYPTO UNDER SHORT PRESSURE"),
                             _message("CRYPTO CAPITOLAZIONE", "CRYPTO CAPITULATION")]
            else:
                etichette = [_t("CRYPTO CALMO"), _t("CRYPTO NORMALE"), _t("CRYPTO SURRISCALDATO"),
                             _t("CRYPTO EUFORICO")]
            verdict = _verdict_bands(score, max_score, etichette)
    return {"domain": "crypto", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "unscored": unscored, "excluded": excluded,
            "metrics": {"funding_ann_pct": fund, "premium_bps": prem,
                        "funding_scarto_base_pp": scarto_f, "premio_scarto_zona_bp": scarto_p,
                        "segnali_discordi": discordi,
                        "major_usati": usati_f, "direzione": ("short" if direzione < 0 else
                                                              "long" if direzione > 0 else "neutra"),
                        "fetched_at_utc": oss, "eta_ore": eta_ore,
                        "oi_usd_m": {a: _finite_number(r.get("oi_usd_m")) for a, r in major.items()}}}


# parole ad alto impatto negativo per il news score
# Opus 4.8 15/07: NON CALIBRATA — vedi news_score(), la riga e' dichiarata n.d. e non
# assegna punti. Il contatore era dead code (text_blob sempre vuoto, fix a :_blob) e
# accendendolo sono emersi difetti misurati che NON si chiudono con una lista di parole:
#   - termini nudi -> falsi positivi: 'sec' 13/13 FP (boilerplate 13F "filing with the
#     SEC"), 'probe' 4/5 FP (inchiesta FIFA su Infantino attribuita a una banca del book), 'miss'
#     "Barrett to miss Ireland clash" (rugby), 'warning' "A-50U Early Warning Aircraft";
#   - bigrammi rigidi -> falsi negativi: \b...\b non tollera plurale ne' inversione, cosi'
#     "Price Target Cuts" e "Analyst cuts <ticker> price target" non matchano (misurato: 12/12
#     titoli reali persi, tra cui 4 tagli di target sulla stessa DAT del book a relevance 7-8);
#   - i 5 termini IT fanno 0 hit su 16.817 righe: il feed non ha testo italiano (7 righe).
# La taratura richiede pattern tolleranti + un flusso news ONESTO, che oggi non c'e': il
# volume dipende dalla quota provider (limiter -> [] muto), non dal mercato. Prima quello.
_NEWS_RISK_KW = ["lawsuit", "downgrade", "investigation", "fraud", "bankrupt",
                 "plunge", "selloff", "profit warning", "guidance cut", "dividend cut",
                 "job cuts", "price target cut", "earnings miss", "revenue miss",
                 "profit miss", "trading halt", "product recall", "declassa"]


# --- news: soglie (Opus 5.5 09/10; v2 10/10) ----------------------------------------------
# Finestra 7 giorni: l'orizzonte del memo settimanale. Punteggio = quota del peso con flusso
# intenso, bande a quartili (25/50/75%): non cresce col numero di nomi.
# v2 10/10 (riserva MEDIO 4): la soglia v1 (5 distinti/7gg) misurava la NOTORIETA' del
# ticker, non il flusso: tool_search_news interroga marketaux 5, thenewsapi 5, gnews 15,
# yfinance 5 (agent_tools.tool_search_news) e yfinance da SOLO arriva a 5 per qualunque
# nome quotato; le sigle-parola (GNews q=ticker) saturano gnews. Uno storico per nome
# confrontabile NON esiste: news_feed e' un altro strumento (query tematiche del feed, non
# la ricerca per ticker dello scorer) e mescolarli sarebbe una misura circolare. Regola
# assoluta dichiarata: INTENSO = almeno 16 articoli distinti in 7gg (oltre il tetto della
# singola fonte piu' larga, 15: nessuna fonte da sola lo raggiunge) da almeno 2 fonti
# diverse. Con gnews muta le altre tre arrivano a 15: INTENSO e' irraggiungibile e i nomi
# restano n.d. (cecita' dichiarata). RESIDUO: per le mega cap e le sigle-parola gnews 15 +
# yfinance 5 = 20 resta raggiungibile anche in una settimana di routine; il verdetto dice
# "volume, non tono".
_NEWS_FINESTRA_GG = 7
_NEWS_SOGLIA_NOME = 16
_NEWS_MIN_FONTI_INTENSO = 2
_NEWS_QUOTA_BANDE = (25.0, 50.0, 75.0)
# Verdetto solo se si misura almeno META' del peso esaminato: sotto, descriverebbe i pochi
# nomi visti e non il book (la cecita' non deve mai leggersi "calmo").
_NEWS_COPERTURA_MIN_PCT = 50.0


def _news_fornitore(it):
    """Fornitore dell'articolo dal campo `fonte` di tool_search_news ("GNews (Reuters)")."""
    f = str(it.get("fonte") or "").strip()
    return f.split(" (")[0].strip().lower() or None


def _news_banda(q):
    return (0 if q < _NEWS_QUOTA_BANDE[0] else 1 if q < _NEWS_QUOTA_BANDE[1]
            else 2 if q < _NEWS_QUOTA_BANDE[2] else 3)


@scoped_language
def news_score(portfolio_data=None, news_items=None, max_names=6):
    """Turbolenza dal flusso notizie sui nomi del book. PIU' ALTO = piu' eventi negativi/alto impatto."""
    items = news_items
    # Opus 4.8 16/07: copertura delle fonti, raccolta mentre si raccolgono gli item.
    # tool_search_news dichiara quali fonti erano mute (P0 15/07) e qui la dichiarazione
    # veniva BUTTATA VIA: il numero e il verdetto uscivano lo stesso, costruiti su un
    # flusso dimezzato. Con la quota esaurita ogni mattina alle ~08:36 il caso NON e'
    # teorico: e' lo stato della run settimanale. Un book cieco che stampa "FLUSSO CALMO"
    # e' il fallback silenzioso peggiore, perche' e' il Capo a narrarlo al PM.
    _mute = set()
    _fonti_candidate = set()   # fonti INTERROGATE (unione sui nomi): il denominatore vero
    _coperture = []
    _per_nome = {}             # Opus 5.5 09/10: la misura e' PER NOME, non sul mucchio del book
    _positions = (portfolio_data or {}).get("positions") or []
    if items is None:
        names = []
        try:
            positions = (portfolio_data or {}).get("positions") or []
            if not positions:
                from bellomberg.agents import agent_tools
                positions = (agent_tools.tool_get_portfolio_live() or {}).get("positions") or []
            _positions = positions
            names = [p.get("ticker") for p in sorted(positions, key=lambda x: -(x.get("peso_pct") or 0))][:max_names]
            from bellomberg.agents import agent_tools
            items = []
            for tk in names:
                # max_results=30: il tetto del tool (default 10) troncava PRIMA della finestra
                # di 7 giorni in ordine di fonte, non di data. 30 >= somma dei tetti dei
                # provider (5+5+15+5): nessuna chiamata in piu', solo nessun taglio a valle.
                r = (agent_tools.tool_search_news(tk, max_results=30)
                     if hasattr(agent_tools, "tool_search_news") else None)
                if not isinstance(r, dict):
                    _per_nome[tk] = {"items": [], "copertura": "NESSUNA", "errore": "tool news n.d."}
                    continue
                if isinstance(r, dict):
                    items += (r.get("news") or [])
                    _per_nome[tk] = {"items": list(r.get("news") or []),
                                     "copertura": r.get("copertura") or "PIENA",
                                     "errore": str(r.get("error")) if r.get("error") else None}
                    if r.get("error"):
                        # nome MAI cercato: non e' "coperto", e non va contato come tale
                        _per_nome[tk]["copertura"] = "NESSUNA"
                        _coperture.append("NESSUNA")
                        continue
                    # chiave assente = nessuna fonte muta: contratto di tool_search_news
                    # (scrive 'copertura' solo dentro `if _mute:`). Vale oggi; se cambia,
                    # qui c'e' un lettore.
                    _coperture.append(r.get("copertura") or "PIENA")
                    _fm = r.get("fonti_mute") or []
                    # set.update() su una STRINGA itera i caratteri: "gnews" diventerebbe
                    # {'g','n','e','w','s'} e il memo stamperebbe "mute e, g, n, s, w".
                    _mute.update([_fm] if isinstance(_fm, str) else _fm)
                    _fonti_candidate.update(
                        f for f, s in (r.get("fonti") or {}).items() if s != "non_interrogata")
        except Exception:
            return None
    else:
        # item passati dall'esterno: si raggruppano per nome; la copertura delle fonti e'
        # IGNOTA, quindi un nome sotto soglia non si puo' dire "calmo" (resta n.d.).
        for it in items:
            if isinstance(it, dict):
                tk = it.get("ticker_associato") or it.get("ticker") or "n.d."
                _per_nome.setdefault(tk, {"items": [], "copertura": "IGNOTA", "errore": None})
                _per_nome[tk]["items"].append(it)
    _n_nomi = len(_coperture)
    _n_scoperti = sum(1 for c in _coperture if c != "PIENA")
    import re as _re
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from bellomberg.core.presentation import message as _message
    # audit/11 §5: confini di parola — 'cut'/'miss' come substring matchavano anche
    # 'execute', 'haircut', 'commission', 'dismiss', 'missile' gonfiando l'allerta
    _pats = [_re.compile(r"\b" + _re.escape(kw) + r"\b", _re.IGNORECASE) for kw in _NEWS_RISK_KW]

    def _blob(it):
        # Opus 4.8 15/07: nel repo convivono 3 forme di item news e qui ne arrivava una
        # sola: tool_search_news (:363) emette titolo/descrizione (agent_tools.py:239-245),
        # il feed/briefing title/snippet. Leggere solo title/summary lasciava il testo
        # SEMPRE vuoto -> n_risk sempre 0 -> riga 'Eventi ad alto impatto neg.' morta.
        return (str(it.get("title") or it.get("titolo") or "") + " " +
                str(it.get("summary") or it.get("snippet") or it.get("descrizione") or ""))

    def _chiavi(it):
        # stesso articolo = stesso URL o stesso titolo normalizzato (anche fra fonti diverse)
        url = str(it.get("url") or it.get("link") or "").strip().lower().rstrip("/")
        tit = " ".join(str(it.get("title") or it.get("titolo") or "").lower().split())
        return [k for k in (("u", url) if url else None, ("t", tit) if tit else None) if k]

    _adesso = _dt.now(_tz.utc)

    def _data(it):
        # ISO 8601 (marketaux/thenewsapi/gnews/yfinance pubDate) o epoch (providerPublishTime)
        raw = str(it.get("data") or it.get("published_at") or it.get("date") or "").strip()
        if not raw:
            return None
        try:
            if raw.isdigit():
                return _dt.fromtimestamp(int(raw), tz=_tz.utc)
            d = _dt.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=_tz.utc)
        except (ValueError, OverflowError, OSError):
            return None

    _pesi = {p.get("ticker"): _peso_posizione(p) for p in _positions
             if isinstance(p, dict) and p.get("ticker")}
    _visti_book = set(); n_book_7g = 0; n_risk = 0; total = 0
    nomi = {}
    for tk, rec in _per_nome.items():
        visti = set(); n7 = 0; n_senza_data = 0; n_dist = 0; fornitori = set()
        for it in rec["items"]:
            if not isinstance(it, dict):
                continue
            ch = _chiavi(it)
            if not ch or any(k in visti for k in ch):
                continue          # duplicato (o item senza URL ne' titolo)
            visti.update(ch); n_dist += 1
            d = _data(it)
            if d is None:
                n_senza_data += 1
                continue
            if d < _adesso - _td(days=_NEWS_FINESTRA_GG):
                continue
            n7 += 1
            if _news_fornitore(it):
                fornitori.add(_news_fornitore(it))
            if not any(k in _visti_book for k in ch):
                _visti_book.update(ch); n_book_7g += 1
                if any(p.search(_blob(it)) for p in _pats):
                    n_risk += 1
        total += n_dist
        # Stato del nome. INTENSO si accerta anche con fonti mute (il conteggio e' un
        # minimo) ma solo da >=2 fornitori: sopra il tetto di una fonte sola nessuna fonte
        # satura da sola (v2 10/10). CALMO solo con fonti piene e date tutte leggibili.
        if rec.get("errore"):
            stato, motivo = "n.d.", rec["errore"]
        elif n7 >= _NEWS_SOGLIA_NOME and len(fornitori) >= _NEWS_MIN_FONTI_INTENSO:
            stato, motivo = "INTENSO", None
        elif n7 >= _NEWS_SOGLIA_NOME:
            stato, motivo = "n.d.", _message("{k} fonte/i riconosciute (minimo {m})",
                                              "{k} recognised source(s) (minimum {m})",
                                              k=len(fornitori), m=_NEWS_MIN_FONTI_INTENSO)
        elif rec["copertura"] != "PIENA":
            stato, motivo = "n.d.", "copertura " + str(rec["copertura"])
        elif n7 + n_senza_data >= _NEWS_SOGLIA_NOME:
            stato, motivo = "n.d.", "%d articoli senza data" % n_senza_data
        else:
            stato, motivo = "CALMO", None
        if stato != "n.d." and _pesi.get(tk) is None:
            stato, motivo = "n.d.", "peso n.d."
        nomi[tk] = {"stato": stato, "motivo": motivo, "n_7g": n7, "n_distinti": n_dist,
                    "n_senza_data": n_senza_data, "peso_pct": _pesi.get(tk),
                    "fonti_7g": sorted(fornitori)}
    if not nomi:
        return None
    misurati = {tk: r for tk, r in nomi.items() if r["stato"] != "n.d."}
    w_mis = sum(r["peso_pct"] for r in misurati.values())
    w_int = sum(r["peso_pct"] for r in misurati.values() if r["stato"] == "INTENSO")
    w_esam = sum(r["peso_pct"] for r in nomi.values() if r["peso_pct"] is not None)
    nd = [tk for tk, r in nomi.items() if r["stato"] == "n.d."]
    lines = []; pts = []
    quota = quota_max = None
    _q_mis = 100.0 * w_mis / w_esam if w_esam else 0.0
    # v2 10/10 (riserva ALTO 2): la quota si misura sul peso ESAMINATO, non sul misurato. Prima
    # i nomi n.d. uscivano dal denominatore: con le fonti quasi cieche restavano solo i nomi
    # "intensi" e la quota convergeva al 100% (ALLERTA NOTIZIE da una fonte sola). I nomi n.d.
    # sono un intervallo: quota minima = intensi / esaminato (n.d. tutti calmi), massima =
    # (intensi + n.d.) / esaminato (n.d. tutti intensi). La fascia si emette solo se minimo e
    # massimo cadono nella STESSA banda: altrimenti la cecita' decide il verdetto -> n.d.
    if w_esam:
        quota = 100.0 * w_int / w_esam
        quota_max = 100.0 * (w_esam - w_mis + w_int) / w_esam
    if quota is not None and w_mis > 0 and _q_mis >= _NEWS_COPERTURA_MIN_PCT \
            and _news_banda(quota) == _news_banda(quota_max):
        p = _news_banda(quota)
        lines.append((_message("Volume notizie: peso con flusso intenso (>={n} distinti da >={f} fonti/{g}gg)",
                               "News volume: weight with intense flow (>={n} distinct from >={f} sources/{g}d)",
                               n=_NEWS_SOGLIA_NOME, f=_NEWS_MIN_FONTI_INTENSO, g=_NEWS_FINESTRA_GG),
                      _message("{q:.0f}% del peso esaminato ({i}/{m} nomi; n.d. {d:.0f}%)",
                               "{q:.0f}% of examined weight ({i}/{m} names; n/a {d:.0f}%)", q=quota,
                               i=sum(1 for r in misurati.values() if r["stato"] == "INTENSO"),
                               m=len(nomi), d=100.0 - _q_mis), p))
        pts.append(p)
    elif quota is not None and w_mis > 0 and _q_mis >= _NEWS_COPERTURA_MIN_PCT:
        lines.append((_message("Volume notizie per nome", "News volume per name"),
                      _message("n.d.: quota intensa fra {a:.0f}% e {b:.0f}% del peso esaminato "
                               "(nomi n.d. {d:.0f}%): la fascia dipende dai nomi non misurati",
                               "n/a: intense share between {a:.0f}% and {b:.0f}% of examined weight "
                               "(n/a names {d:.0f}%): the band depends on unmeasured names",
                               a=quota, b=quota_max, d=100.0 - _q_mis), None))
        quota = None
    else:
        # sotto meta' del peso esaminato il verdetto descriverebbe i pochi nomi visti, non il
        # book: n.d. (N7 dell'audit: 5 nomi ciechi su 6 davano "FLUSSO CALMO")
        lines.append((_message("Volume notizie per nome", "News volume per name"),
                      _message("n.d.: misurato {q:.0f}% del peso esaminato (minimo {m:.0f}%)",
                               "n/a: measured {q:.0f}% of examined weight (minimum {m:.0f}%)",
                               q=_q_mis, m=_NEWS_COPERTURA_MIN_PCT), None))
        quota = None
    for tk, r in nomi.items():
        _w = "{:.1f}%".format(r["peso_pct"]) if r["peso_pct"] is not None else "n.d."
        if r["stato"] == "n.d.":
            _v = _message("n.d.: {m} ({n} distinti in {g}gg)", "n/a: {m} ({n} distinct in {g}d)",
                          m=r["motivo"], n=r["n_7g"], g=_NEWS_FINESTRA_GG)
        else:
            _v = _message("{s}: {n} distinti in {g}gg", "{s}: {n} distinct in {g}d",
                          s=r["stato"], n=r["n_7g"], g=_NEWS_FINESTRA_GG)
        lines.append((_message("  {tk} (peso {w})", "  {tk} (weight {w})", tk=tk, w=_w), _v, None))
    lines.append((_message("Articoli distinti {g}gg (book, dedup)", "Distinct articles {g}d (book, dedup)",
                           g=_NEWS_FINESTRA_GG), str(n_book_7g), None))
    # Opus 4.8 15/07 — riga DICHIARATA n.d. (regola PM 14/07: dichiarare, non stimare).
    # Il contatore keyword non e' calibrato (difetti misurati in _NEWS_RISK_KW). Opus 5.5
    # 09/10: FUORI dal massimo (pt None, in `unscored`): prima valeva 0 punti su 3 dentro
    # il massimo, cosi' "ALLERTA NOTIZIE" era irraggiungibile e l'Event Desk diluito.
    _riga_nd = _t("Eventi ad alto impatto neg.")
    lines.append((_riga_nd, _t("n.d. (contatore non calibrato)"), None))
    if pts:
        score = sum(pts); max_score = len(pts) * 3
        verdict = _verdict_bands(score, max_score, [_t("FLUSSO CALMO"), _t("FLUSSO NORMALE"), _t("FLUSSO INTENSO"), _t("ALLERTA NOTIZIE")])
        # il conteggio misura il VOLUME, non il tono ne' la rilevanza: lo si dice nel verdetto
        verdict = _message("{v} (volume, non tono: misurato {q} del peso esaminato)",
                           "{v} (volume, not tone: measured {q} of examined weight)", v=verdict,
                           q="{:.0f}%".format(100.0 * w_mis / w_esam) if w_esam else "n.d.")
    else:
        # fonti mute / nomi non misurabili = n.d., MAI "FLUSSO CALMO". Dict con max 0 (non
        # None): eventdesk_score somma 0/0 e tiene le righe e la copertura dichiarate.
        score = 0; max_score = 0
        verdict = _message("n.d. - flusso notizie non misurabile ({n}/{m} nomi n.d.)",
                           "n/a - news flow not measurable ({n}/{m} names n/a)", n=len(nd), m=len(nomi))
    _cop = ("PARZIALE" if (_mute or any(c != "PIENA" for c in _coperture))
            else ("PIENA" if _coperture else "IGNOTA"))
    _n_fc = len(_fonti_candidate)
    if _cop == "PARZIALE":
        # Audit run 10/09 (Fable 5.1): "fonti mute su 6/6 nomi" e' stato letto da Event Desk
        # e Capo come "sei fonti su sei mute" (memo #53 e #54): le fonti mute erano DUE su
        # quattro, i 6/6 erano i NOMI. Prima le fonti, col loro rapporto; poi i nomi.
        # "scoperti" direbbe "ciechi del tutto": qui i nomi hanno fonti RIDOTTE, non zero.
        _det = _t("mute: ") + (", ".join(sorted(_mute)) if _mute else "n.d.")
        if _n_fc:
            _det += _t(" (%d/%d fonti)") % (len(_mute), _n_fc)
        _det += _t(" su %d/%d nomi") % (_n_scoperti, _n_nomi)
        lines.append((_t("Copertura fonti"), _t("PARZIALE - ") + _det, None))
        verdict = _t("%s (copertura PARZIALE: %s - il volume NON misura il flusso reale)") % (
            verdict, _det)
    return {"domain": "news", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "unscored": [_riga_nd],
            # Righe di RISCHIO vero (tono/rilevanza) da sommare in eventdesk: oggi nessuna.
            # La quota con flusso intenso misura il VOLUME, non il rischio: resta nel news
            # score ma non e' un rischio da sommare (contratto con eventdesk_score, 09/10).
            "risk_lines": [],
            "metrics": {"n_news": total, "n_news_7g": n_book_7g, "n_high_impact": n_risk,
                        "quota_intensa_pct": round(quota, 1) if quota is not None else None,
                        "quota_intensa_max_pct": round(quota_max, 1) if quota_max is not None else None,
                        "soglia_nome": _NEWS_SOGLIA_NOME, "min_fonti_intenso": _NEWS_MIN_FONTI_INTENSO,
                        "peso_misurato_pct": round(w_mis, 1), "nomi": nomi,
                        "copertura": _cop, "fonti_mute": sorted(_mute),
                        "n_fonti_mute": len(_mute), "n_fonti_candidate": _n_fc,
                        "n_nomi": _n_nomi, "n_nomi_scoperti": _n_scoperti}}


# topic di rischio geopolitico monitorati su Polymarket.
# Query rimisurate dal vivo l'11/09 col filtro qui sotto: "war conflict 2026", "iran israel
# strike" e "china taiwan tariff" non restituivano NESSUN mercato aperto pertinente fra i
# primi 8 risultati (solo mercati risolti e partite); queste trovano mercati veri e aperti
# ("Will the US officially declare war on Iran by December 31, 2026?", "Will China invade
# Taiwan by end of 2026?").
# 10/10 (Opus 5.5): il vecchio tema misto «Cina-Taiwan/dazi» e' diviso in due temi con soglie
# distinte. La query «tariffs 2026» dei dazi NON e' stata rimisurata dal vivo (la sessione del
# 10/10 non interroga Polymarket): se non trova un mercato aperto pertinente il tema esce n.d.
# DICHIARATO, mai un numero preso a caso (v. _poli_scegli_mercato).
_POLI_TOPICS = {"recessione USA": "us recession 2026", "conflitto/guerra": "war 2026",
                "Iran-Israele": "iran war 2026", "Cina-Taiwan": "china taiwan 2026",
                "dazi": "tariffs 2026"}

# Audit run 10/09 (Fable 5.1, memo #54): il tool espande la query coi sinonimi e aggiunge i
# mercati piu' scambiati del giorno; qui si prendeva il massimo "Yes" fra TUTTI i risultati.
# Misurato dal vivo l'11/09: "recessione USA" -> 0,995 da una partita di calcio (Sevilla-
# Valencia), gli altri tre temi -> 1,0 da mercati gia' RISOLTI nel 2025/inizio 2026 (parole
# di un podcast, "Israel strikes Iran by February 28", dazi di aprile 2025), mentre il
# mercato vero "US recession by end of 2026?" stava a 0,07. Il cruscotto del memo #54 e'
# uscito "EVENTI CRITICI 78/100" su quattro 100% costruiti su nulla.
# Un mercato conta per un tema solo se: la sua DOMANDA parla del tema (ogni alternativa =
# tutti i termini presenti, a confine di parola), la data di chiusura e' futura, il prezzo
# non e' gia' 0/1 (risolto). I mercati di "menzione" (Will X say ...) sono scommesse sulle
# parole di un discorso, non rischio di coda. Fra i validi vince il PIU' SCAMBIATO, non il
# piu' alto: il numero del cruscotto e' quello che il mercato prezza davvero.
_POLI_TERMINI = {
    "recessione USA": (("recession",),),
    # niente "ceasefire": la probabilita' di una TREGUA misura la pace, non la coda
    "conflitto/guerra": (("war",), ("conflict",), ("invasion",), ("invade",), ("attack",)),
    # niente coppia nuda (iran, israel): "Will Israel reopen its embassy in Iran" non e' rischio
    "Iran-Israele": (("iran", "war"), ("iran", "strike"), ("iran", "attack"),
                     ("israel", "strike"), ("israel", "attack")),
    "Cina-Taiwan": (("china", "taiwan"), ("taiwan", "invade"), ("taiwan", "invasion"),
                    ("taiwan", "blockade")),
    # dazi (v5 10/10, decisione del coordinatore): WHITELIST DI FORMA, non parole. Il tema
    # conta solo con la forma di escalation esplicita (v. _poli_dazi_ok e _POLI_DAZI_FORME); le
    # forme nominali («tariff hike», «new tariffs») da sole NON bastano perche' sono quelle che
    # si invertono («will the tariff hike be reversed»). Questi termini servono solo come
    # marcatore del tema per _poli_termini_ok, che delega a _poli_dazi_ok.
    "dazi": (("tariff",), ("trade war",)),
}
_POLI_MENZIONE = ("say", "said", "mention", "mentions")
# fix 09/10 (Opus 5.5, audit D-P3): soglie PER TEMA in punti percentuali di probabilita'
# (1/2/3 punti a partire da ciascuna soglia). Prima 10/25/45 uguali per tutti: il 30% di
# «recessione entro l'anno» pesava come il 30% di «invasione». GIUDIZIO, non taratura:
#  - recessione USA: probabilita' di base ~15% l'anno (NBER: una recessione ogni 6-7 anni
#    dal 1945) e consenso abituale 20-35%; e' coda solo ben sopra la base -> 20/35/55;
#  - conflitto/guerra (guerra nuova, invasione, attacco): evento raro e grave, gia' un 5%
#    prezzato e' materiale -> 5/15/30;
#  - Iran-Israele: attacchi ricorrenti (prezzi spesso 30-70%), impatto di mercato limitato
#    (petrolio) -> 15/35/60;
#  - Cina-Taiwan (10/10, diviso dai dazi): blocco/invasione e' un evento di GUERRA, raro e
#    gravissimo (catena dei semiconduttori) -> le soglie della guerra, 5/15/30;
#  - dazi (10/10): frequenti, prezzati spesso fra 20 e 60%, impatto per lo piu' settoriale e
#    di margine -> 20/40/60. Prima i due eventi condividevano 10/25/45 e pesavano uguale.
_POLI_SOGLIE_DEFAULT = (10, 25, 45)
_POLI_SOGLIE = {"recessione USA": (20, 35, 55), "conflitto/guerra": (5, 15, 30),
                "Iran-Israele": (15, 35, 60), "Cina-Taiwan": (5, 15, 30), "dazi": (20, 40, 60)}
# fix 09/10 (Opus 5.5, audit D-P1): mercati di RISOLUZIONE/PACE. Il loro «Yes» e' la
# probabilita' che il rischio SPARISCA («Will the war end…», «…sign a tariff deal», «…avoid a
# recession», «…lower tariffs»): letto come coda invertiva il segno. Si ESCLUDONO (contati
# fra gli scartati), non si invertono: il «No» di «la guerra finira' entro dicembre?» e' lo
# status quo (la guerra continua), non un evento di coda, e 1-Yes lo trasformerebbe in un
# 80% di rischio inventato. «end of»/«year-end» sono date, non verbi: si tolgono prima.
_POLI_RISOLUZIONE = ("end", "ends", "ended", "ending", "deal", "deals", "agreement", "accord",
                     "ceasefire", "truce", "armistice", "peace", "avoid", "avoids", "avoided",
                     "pause", "paused", "suspend", "suspended", "reopen", "reopens", "resume",
                     "resumes", "withdraw", "withdraws", "withdrawal", "talks", "negotiate",
                     "negotiations", "normalize", "normalise",
                     # v2: cessazione esplicita («agree to stop attacks», «halt strikes»)
                     "agree", "agrees", "stop", "stops", "halt", "halts", "cease", "ceases",
                     # v3 10/10 (revisione, tema dazi): annullamento giudiziario, entrate e
                     # rimborsi dei dazi: il loro Yes non e' un'escalation
                     "unconstitutional", "strike down", "strikes down", "struck down",
                     "refund", "refunds")
# v4 10/10 (revisione, tema dazi): allentamento/risoluzione dei dazi in QUALUNQUE forma, anche al
# passivo e con l'oggetto prima del verbo («tariffs on Canada be lifted»). Un mercato dazi con
# una di queste forme e' scartato anche se contiene un verbo di escalation. «revenue» non e'
# qui: «raise tariffs to boost revenue» e' un'escalation (senza verbo il tema non conta comunque).
# «strike(s)/struck down» solo contigui: «raise tariffs before the court strikes them down» conta.
# v5: seconda guardia, dopo la whitelist di forma (anche reverse, roll back, cancel, block,
# overturn, scrap, drop, back down, waive, lower, invalidate, rescind, revoke).
_POLI_DAZI_ALLENTAMENTO = (
    r"\b(lift|lifts|lifted|lifting|remove|removes|removed|removal|repeal|repeals|repealed|reduce"
    r"|reduces|reduced|reduction|illegal|unconstitutional|exempt|exempts|exempted|exemption"
    r"|exemptions|delay|delays|delayed|postpone|postpones|postponed|pause|pauses|paused|suspend"
    r"|suspends|suspended|uphold|upholds|upheld|refund|refunds|reverse|reverses|reversed"
    r"|reversal|rollback|cancel|cancels|canceled|cancelled|cancellation|block|blocks|blocked"
    r"|overturn|overturns|overturned|scrap|scraps|scrapped|drop|drops|dropped|waive|waives"
    r"|waived|waiver|lower|lowers|lowered|lowering|invalidate|invalidates|invalidated|rescind"
    r"|rescinds|rescinded|revoke|revokes|revoked|end|ends|ended|ending)\b",
    r"\b(roll|rolls|rolled|rolling)\s+back\b",
    # v6 (revisione): rifiuto/fallimento/esitazione e de-escalation. «fail» sta anche in
    # _POLI_ESCALATION (forza _poli_risoluzione a False): per i dazi vince QUESTA guardia,
    # applicata dopo e indipendentemente
    r"\b(refuse\w*|declin\w*|fail\w*|unable|reluctan\w*|hesitat\w*)\b",
    r"\bde-?escalat\w*",
    r"\b(back|backs|backed|backing)\s+down\b",
    r"\b(strike|strikes|struck)\s+down\b",
    r"\bfalls?\s+below\b",
    r"\btariffs?\b.*\bcuts?\b|\bcuts?\b.*\btariffs?\b",
)
# v5: proposizioni di CONTESTO temporale tolte prima delle guardie: «after the pause ends» dice
# QUANDO, non che il rischio finisce («Will tariffs be raised again after the pause ends» conta).
_POLI_DAZI_CONTESTO = r"\b(after|when|once|before|until)\s+(the\s+)?(\w+\s+){0,2}?(pause|suspension|truce|exemption|delay|deadline|freeze)\s+(ends|ended|expires|expired|lapses|lapsed|is\s+over)\b"
# v5: le SOLE forme che contano (verbo di escalation ATTIVO prima di «tariff», o passivo di
# escalation, o «tariffs ... rise/go up/increase/exceed», o l'avvio di una guerra commerciale).
_POLI_DAZI_VERBI = r"(raise|impose|increase|hike|announce|put|slap|levy|introduce|expand|double|triple|add)"
_POLI_DAZI_FORME = (
    # «will <soggetto, max 5 parole> <verbo> ... tariff(s)»; niente negazioni prima del verbo
    r"\bwill\s+((?!(not|never|no)\b)\S+\s+){1,5}?" + _POLI_DAZI_VERBI + r"\b[^?]*\btariffs?\b",
    # passivo di escalation: «tariffs ... be/get raised|imposed|increased|hiked|expanded|doubled|tripled»
    r"\btariffs?\b[^?]*\b(be|get|been)\s+(\w+\s+)?(raised|imposed|increased|hiked|expanded|doubled|tripled|levied|introduced)\b",
    # «will tariffs (on X) rise / go up / exceed N%», anche «China tariffs rise above 60%»;
    # «increase» qui no: «the tariff increase» e' la forma NOMINALE che si inverte
    # subito dopo «tariff(s)» o dopo «tariffs on <oggetto>». v6: niente exceed/top, misurano un
    # LIVELLO e non un'escalation («tariffs exceed 0%» al 97% valeva 3 punti)
    r"\btariffs?\s+(on\s+[^?]*?\s+)?(rise|rises|go\s+up|goes\s+up)\b",
    # «will tariffs (on X) increase» come VERBO: «the tariff increase take(s) effect» e' il nome
    r"\bwill\s+(\S+\s+){0,1}?tariffs?\b(\s+on\s+\S+(\s+\S+)?)?\s+increase\b"
    r"(?!\s+(take|takes|be|go|goes|come|comes|apply|applies|stay|stays|remain|remains|hold|holds|survive|survives))",
    # guerra commerciale avviata o in escalation
    # v6: «escalate» non preceduto da «de-»/«de» (il trattino fa scattare \bescalate dentro
    # «de-escalate»)
    r"(?<![a-z-])(start|starts|launch|launches|begin|begins|escalate|escalates)\s+(a\s+|the\s+|an?\s+)?(new\s+)?trade\s+war\b",
    r"\btrade\s+war\b[^?]*(?<![a-z-])escalat\w*",
)
# LIMITE DICHIARATO (v5): falsi negativi accettati (n.d. dichiarato), inversioni no. La riga
# dello score lo dice: «dazi: contati solo mercati di escalation esplicita».
_POLI_DAZI_NOTA = "contati solo mercati di escalation esplicita"


def _poli_dazi_ok(q):
    """True solo se la domanda (minuscola) ha una FORMA di escalation dei dazi e nessuna forma
    di allentamento/risoluzione (v5, whitelist di forma)."""
    import re as _re
    q = _re.sub(_POLI_DAZI_CONTESTO, " ", q)
    if _poli_risoluzione(q):
        return False
    if any(_re.search(x, q) for x in _POLI_DAZI_ALLENTAMENTO):
        return False
    return any(_re.search(x, q) for x in _POLI_DAZI_FORME)
# v2 10/10 (Opus 5.5, revisione MEDIO 5): «lower/reduce/lift» sono di pace solo se il loro
# oggetto e' una misura di guerra commerciale o una sanzione («lower tariffs», «lift
# sanctions»). Nudi rendevano «pace» anche «Will the Fed lower rates as the US enters
# recession?», il cui Yes richiede la recessione.
_POLI_ALLENTAMENTO = (r"\b(lower|lowers|lowered|reduce|reduces|reduced|lift|lifts|lifted|cut|cuts)\b"
                      r"(\s+\S+){0,3}?\s+(tariffs?|sanctions?|duties|embargo|restrictions?)\b")
# v2 (MEDIO 5): ESCALATION. La v1 escludeva come «pace» domande il cui Yes e' il COLLASSO
# della pace o un attacco: «Will peace talks collapse and war resume», «Ceasefire violated:
# will Russia attack…», «trade deal fail and tariffs rise», «Will Iran attack Israel before
# talks?», «Will Israel resume strikes on Iran», «withdraw from the Iran deal and strike
# Iran», «Will NATO end up in a war», «ceasefire broken?». La parola di pace dentro una
# domanda di escalation e' il CONTESTO, non l'evento: con una di queste forme la domanda NON
# e' di pace (e deve comunque parlare del tema per contare).
_POLI_AZIONE_MILITARE = r"(strikes?|attacks?|war|fighting|bombing|hostilities|offensive)"
_POLI_ESCALATION = (
    r"\bcollaps\w*", r"\bbroken\b", r"\bbreaks?\b", r"\bbreakdown\b", r"\bviolat\w*", r"\bfail\w*",
    r"\bescalat\w*", r"\bend\s+up\b",
    # «resume» + azione militare («resume strikes», «war resume»), non «resume talks»
    r"\bresum\w*\b.*\b" + _POLI_AZIONE_MILITARE + r"\b",
    r"\b" + _POLI_AZIONE_MILITARE + r"\b.*\bresum\w*",
)
# Attacco come VERBO PRINCIPALE: «will <soggetto, max 4 parole> attack/strike/invade/bomb»
# o coordinato («… and strike Iran», «… and Israel strike Iran»); non se prima del verbo c'e'
# una negazione o una cessazione («will Israel pause strikes», «agree to stop attacks»).
_POLI_VERBO_ATTACCO = (r"\b(will|and)\s+((?!(not|stop|halt|cease|end|pause|suspend|avoid)\b)\S+\s+){0,4}?"
                       r"(attack|strike|invade|bomb|blockade)\b")


def _poli_escalation(q):
    """True se la domanda (minuscola) chiede un'ESCALATION: prevale su ogni parola di pace."""
    import re as _re
    return (any(_re.search(p, q) for p in _POLI_ESCALATION)
            or _re.search(_POLI_VERBO_ATTACCO, q) is not None)


def _poli_risoluzione(question):
    """True se la domanda chiede la FINE/attenuazione del rischio (mercato di pace).
    v2: False se la domanda e' di ESCALATION (v. _POLI_ESCALATION), anche con parole di pace."""
    import re as _re
    q = str(question or "").lower()
    # v3: «strike down» (annullamento giudiziario) non e' un attacco: va deciso PRIMA della
    # regola di escalation, che leggerebbe «will ... strike» come verbo militare
    if _re.search(r"\b(strikes?|struck)\s+down\b", q):
        return True
    if _poli_escalation(q):
        return False
    q = _re.sub(r"\b(by\s+)?(the\s+)?end\s+of\b|\byear[- ]end\b|\bend[- ]of[- ]year\b", " ", q)
    if _re.search(_POLI_ALLENTAMENTO, q):
        return True
    return any(_re.search(r"\b" + w + r"\b", q) for w in _POLI_RISOLUZIONE)


def _poli_termini_ok(question, alternative):
    """True se la domanda parla del tema. Un tema SENZA termini dichiarati non filtra
    (accetta ogni domanda che non sia di menzione): il vincolo lo mette chi scrive il tema.
    I mercati di risoluzione/pace non passano MAI (v. _POLI_RISOLUZIONE)."""
    import re as _re
    q = str(question or "").lower()
    if any(_re.search(r"\b" + w + r"\b", q) for w in _POLI_MENZIONE):
        return False
    if alternative is _POLI_TERMINI.get("dazi"):
        return _poli_dazi_ok(q)   # v5: whitelist di forma (contesto temporale tolto prima)
    if _poli_risoluzione(q):
        return False
    if not alternative:
        return True
    # "s?" = plurale/terza persona: "strikes", "attacks", "invades", "conflicts"
    return any(all(_re.search(r"\b" + _re.escape(t) + r"s?\b", q) for t in alt) for alt in alternative)


def _poli_data_futura(end_date, adesso):
    from datetime import datetime as _dt, timezone as _tz
    s = str(end_date or "").strip()
    if not s:
        return False
    try:
        d = _dt.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    if d.tzinfo is None:
        return False  # timezone assente: non inventare UTC per dichiarare il mercato aperto
    return d > adesso


def _poli_scegli_mercato(risultati, alternative, adesso, esclusi=()):
    """(prob_yes, {question, end_date, volume_24h}, n_scartati) fra i mercati VALIDI del tema:
    vince il piu' scambiato (volume 24h), a parita' il Yes piu' alto. Nessun mercato valido
    -> (None, None, n_scartati): il tema esce n.d., non con un numero preso a caso.
    `esclusi` = domande gia' usate da un altro tema: un mercato conta per UN tema solo."""
    import json as _json
    scelto = None
    n_scartati = 0
    esclusi = {str(x).strip().lower() for x in (esclusi or ())}
    def activity_unavailable(row):
        status = row.get("activity_status")
        if status is not None and status != "active":
            return True
        state = row.get("provider_state")
        state = state if isinstance(state, dict) else row
        return state.get("active") is False or state.get("closed") is True or state.get("archived") is True
    for ev in risultati or []:
        if not isinstance(ev, dict):
            continue
        mkts = ev.get("markets") if ev.get("markets") else [ev]
        for m in (mkts or []):
            if not isinstance(m, dict):
                continue
            if activity_unavailable(ev) or activity_unavailable(m):
                n_scartati += 1
                continue
            outs = m.get("outcomes")
            prs = m.get("prices") or m.get("outcomePrices")
            try:
                if isinstance(outs, str):
                    outs = _json.loads(outs)
                if isinstance(prs, str):
                    prs = _json.loads(prs)
            except (ValueError, TypeError):
                n_scartati += 1
                continue
            if not outs or not prs:
                continue
            yes = None
            for o, pr in zip(outs, prs):
                if str(o).lower() in ("yes", "si", "sì"):
                    try:
                        yes = _finite_number(float(pr))
                    except (TypeError, ValueError):
                        yes = None
            if yes is None:
                n_scartati += 1
                continue
            question = m.get("question") or ev.get("title")
            end_date = m.get("end_date")  # la data dell'evento non attesta la scadenza del figlio
            if (not (0.0 < yes < 1.0) or not _poli_data_futura(end_date, adesso)
                    or not _poli_termini_ok(question, alternative)
                    or str(question or "").strip().lower() in esclusi):
                n_scartati += 1
                continue
            vol = _finite_number(m.get("volume_24h"))
            if vol is None:
                vol = _finite_number(ev.get("volume_24h"))
            chiave = (vol if vol is not None else -1.0, yes)
            if scelto is None or chiave > scelto[0]:
                scelto = (chiave, yes, {"question": str(question)[:160], "end_date": end_date,
                                        "volume_24h": vol,
                                        "activity_verification": ("explicit_active" if
                                            m.get("activity_status") == ev.get("activity_status") == "active"
                                            else "legacy_date_and_unresolved_price_only")})
    if scelto is None:
        return None, None, n_scartati
    return scelto[1], scelto[2], n_scartati


@scoped_language
def politics_score(events_by_topic=None):
    """Rischio geopolitico dai prezzi dei prediction market. PIU' ALTO = piu' probabilita' di coda.
    metrics.mercati dice QUALE domanda ha dato il numero; metrics.non_calcolabili i temi senza
    un mercato aperto pertinente (dichiarati anche in lines, a zero punti e fuori dal massimo)."""
    probs = events_by_topic
    mercati = {}
    non_calc = {}
    scartati = {}
    if probs is None:
        probs = {}
        try:
            from bellomberg.agents import agent_tools
            from datetime import datetime as _dt, timezone as _tz
            adesso = _dt.now(_tz.utc)
            usati = set()
            for label, q in _POLI_TOPICS.items():
                try:
                    r = agent_tools.tool_get_polymarket_events(q, max_results=8)
                    r = r if isinstance(r, dict) else {}
                    risultati = r.get("results") or r.get("events") or r.get("markets") or []
                    prob, info, n_sc = _poli_scegli_mercato(risultati, _POLI_TERMINI.get(label, ()),
                                                            adesso, esclusi=usati)
                except Exception as e:
                    non_calc[label] = "tool non disponibile (%s)" % type(e).__name__
                    continue
                scartati[label] = n_sc
                if prob is None:
                    if risultati or n_sc:
                        non_calc[label] = "nessun mercato aperto pertinente (%d scartati)" % n_sc
                    else:
                        non_calc[label] = str(r.get("error") or "nessun risultato dal tool")[:120]
                    continue
                probs[label] = prob
                mercati[label] = info
                usati.add(str(info.get("question") or "").strip().lower())
        except Exception:
            return None
    # fix 09/10 (Opus 5.5, audit D-P4): UNITA' UNICA = frazione strettamente fra 0 e 1, come il
    # prezzo di Polymarket. Prima `pr*100 if pr <= 1 else pr`: 1,0 diventava 100% (ACUTO) e
    # 1,5 diventava 1,5% (BASSO). Fuori da (0, 1) il valore e' ambiguo (percentuale?) o un
    # mercato gia' risolto (0/1): si RIFIUTA e si dichiara, non si indovina l'unita'.
    validi = {}
    for k, raw in (probs or {}).items():
        v = _finite_number(raw)
        if v is None:
            non_calc[k] = "valore non numerico"
        elif not (0.0 < v < 1.0):
            non_calc[k] = "unita' ambigua o mercato risolto (%s): atteso frazione fra 0 e 1" % raw
        else:
            validi[k] = v
    probs = validi
    if not probs:
        return None
    lines = []; pts = []; unscored = []
    for label, pr in probs.items():
        pct = pr * 100
        s1, s2, s3 = _POLI_SOGLIE.get(label, _POLI_SOGLIE_DEFAULT)
        p = 0 if pct < s1 else 1 if pct < s2 else 2 if pct < s3 else 3
        lines.append((label, "{:.0f}% (soglie {}/{}/{}){}".format(
            pct, s1, s2, s3, (" [" + _POLI_DAZI_NOTA + "]") if label == "dazi" else ""), p)); pts.append(p)
    # temi senza mercato: riga DICHIARATA senza punti e fuori dal massimo (regola 14/07).
    # fix 09/10: punti None, non 0 — uno 0 si stampava «0 punti (ok)» come un tema misurato calmo
    for label, motivo in non_calc.items():
        lab = label + " (n.d.)"
        lines.append((lab, motivo[:60] + ((" [" + _POLI_DAZI_NOTA + "]") if label == "dazi" else ""), None))
        unscored.append(lab)
    score = sum(pts); max_score = len(pts) * 3
    verdict = _verdict_bands(score, max_score, [_t("RISCHIO GEOPOL. BASSO"), _t("RISCHIO MODERATO"),
                                                _t("RISCHIO ELEVATO"), _t("RISCHIO ACUTO")])
    return {"domain": "politics", "score": score, "max_score": max_score, "verdict": verdict,
            "lines": lines, "unscored": unscored,
            "metrics": {"topics": {k: round(v, 3) for k, v in probs.items()},
                        "mercati": mercati, "non_calcolabili": non_calc,
                        "scartati": scartati,
                        "copertura": "%d/%d temi" % (len(probs), len(probs) + len(non_calc))}}


@scoped_language
def eventdesk_score(portfolio_data=None):
    """Score EVENT DESK (fusione News+Politics 15/07): turbolenza dal flusso
    notizie + rischio di coda dai prediction market in UNA ancora.
    Meta' non calcolabile = riga DICHIARATA (mai buchi silenziosi)."""
    try:
        n = news_score(portfolio_data)
    except Exception:
        n = None
    try:
        p = politics_score()
    except Exception:
        p = None
    if not n and not p:
        return None
    from bellomberg.core.presentation import message as _message
    # fix 09/10 (Opus 5.5, audit D-E1..D-E3): il punteggio somma SOLO righe di RISCHIO con
    # punti reali. Il volume di notizie restituite dai provider misura la copertura delle API
    # (max 10 item per nome, quote, limiter), non la turbolenza: con geopolitica n.d. il solo
    # volume portava a «EVENTI CALDI». Resta in vista come INFORMAZIONE, fuori dal massimo.
    # CONTRATTO con news_score (rifatto da un altro autore): una riga news entra nel punteggio
    # solo se news_score la dichiara in `risk_lines` (etichette) con punti numerici; le righe
    # n.d. (punti None, `unscored`, valore «n.d./n/a») sono dichiarate fuori dal massimo, ogni
    # altra riga e' informativa (esclusa per regola, motivo dichiarato). Oggi nessuna riga
    # news e' di rischio: il contatore eventi e' n.d. per dichiarazione di news_score.
    lines = []; pts = []; unscored = []; excluded = {}
    if n:
        _rischio = set(n.get("risk_lines") or ())
        _nd = set(n.get("unscored") or ())
        for (l, v, pt) in n["lines"]:
            lab = "NEWS | " + str(l)
            if l in _rischio and l not in _nd and _finite_number(pt) is not None:
                lines.append((lab, v, pt)); pts.append(pt)
            elif (l in _nd or (pt is None and l in _rischio)
                  or str(v).strip().lower().startswith(("n.d", "n/a", "unavailable"))):
                lines.append((lab, v, None)); unscored.append(lab)
            elif pt is None:
                # v2 10/10 (riconciliazione col news_score v1): riga di contesto (dettaglio per
                # nome, articoli distinti, copertura fonti). Ne' «dato mancante» ne' esclusa per
                # regola: format_score_block la stampa «informativa (fuori punteggio)».
                lines.append((lab, v, None))
            else:
                lines.append((lab, v, None))
                excluded[lab] = _message(
                    "informativa: non e' una riga di rischio (il volume misura la copertura delle API)",
                    "informational: not a risk row (volume measures API coverage)")
    else:
        lab = _t("NEWS | score non calcolabile (dichiarato)")
        lines.append((lab, "n.d.", None)); unscored.append(lab)
    if p:
        _nd = set(p.get("unscored") or ())
        for (l, v, pt) in p["lines"]:
            lab = "GEO | " + str(l)
            if l in _nd or _finite_number(pt) is None:
                lines.append((lab, v, None)); unscored.append(lab)
            else:
                lines.append((lab, v, pt)); pts.append(pt)
    else:
        lab = _t("GEO | score non calcolabile (dichiarato)")
        lines.append((lab, "n.d.", None)); unscored.append(lab)
    # Copertura minima: almeno 2 righe di rischio misurate. Con una sola (un tema geopolitico)
    # il massimo e' 3 e ogni punto salta una banda intera: e' un dato, non un verdetto.
    if len(pts) < 2:
        score = max_score = None
        verdict = _nd_copertura()
    else:
        score = sum(pts); max_score = 3 * len(pts)
        verdict = _verdict_bands(score, max_score, [_t("EVENTI CALMI"), _t("EVENTI IN FERMENTO"),
                                                    _t("EVENTI CALDI"), _t("EVENTI CRITICI")])
    # Opus 4.8 16/07: la copertura news arriva al PM SOLO da qui. news_score la dichiara nel
    # suo verdict, ma quel verdict non lo legge nessuno: qui sotto si ricalcola il proprio, e
    # collect_scoreboard -> PDF/Capo prende solo verdict/score/max_score di 'eventdesk'.
    # Senza queste righe la dichiarazione moriva nel prompt del solo specialista.
    # AGGRAVANTE che rende la riga necessaria: la cecita' ABBASSA lo score (meno fonti ->
    # meno volume -> meno punti), quindi un book scoperto si colora di VERDE nel cruscotto.
    # Forma CORTA di proposito: la colonna Verdetto del PDF e' 7.4cm (~203pt utili), il font
    # e' Helvetica-Bold 8.5 e le celle stringa NON vanno a capo -> un testo lungo sborda
    # sulla colonna accanto. Misurato col font vero, caso peggiore "EVENTI IN FERMENTO":
    # " (news: fonti mute su 6/6 nomi)" = 216pt SBORDA; " (news: 2/4 fonti mute)" = 181pt
    # (misurato 11/09), 22pt di margine. Il dettaglio (quali fonti) resta in lines e metrics.
    # Audit run 10/09 (Fable 5.1): la forma precedente " (news: fonti mute 6/6)" contava i
    # NOMI e il PM l'ha letta come "sei fonti mute su sei". Ora si contano le FONTI.
    # v2 10/10 (Opus 5.5, revisione BASSO 7): misurato col font vero, il suffisso sul verdetto
    # n.d. sbordava (213,5 pt «copertura ignota», 260,7 pt «fonti mute, dettaglio in righe»
    # su ~203,8). Ora ogni combinazione verdetto+suffisso sta sotto 195 pt in entrambe le
    # lingue (test): col suffisso il verdetto n.d. usa la forma «copertura scarsa», i
    # suffissi senza conteggio sono brevi, l'inglese dice «silent» senza «sources».
    _mn = (n or {}).get("metrics") or {}
    _suff = None
    # v2 10/10 (riserva MEDIO 5): con news n.d. il verdetto lo DICE («news n.d.») e il
    # cruscotto non colora di verde (componente_nd): «EVENTI CALMI» con il flusso notizie
    # cieco descrive la sola geopolitica. Forme misurate <=195 pt (test della colonna PDF).
    _news_nd = (not n) or n.get("score") is None or not n.get("max_score")
    _nf, _nc = _mn.get("n_fonti_mute"), _mn.get("n_fonti_candidate")
    if _news_nd:
        if _mn.get("copertura") == "PARZIALE" and _nf and _nc:
            _suff = _message(" (news n.d.: {f}/{c} mute)", " (news n/a: {f}/{c} off)", f=_nf, c=_nc)
        elif _mn.get("copertura") == "PARZIALE":
            _suff = _message(" (news n.d.: fonti mute)", " (news n/a: silent)")
        elif _mn.get("copertura") == "IGNOTA":
            _suff = _message(" (news n.d.: fonti ignote)", " (news n/a)")
        else:
            _suff = _message(" (news n.d.)", " (news n/a)")
    elif _mn.get("copertura") == "PARZIALE":
        if _nf and _nc:
            _suff = _message(" (news: {f}/{c} fonti mute)", " (news: {f}/{c} silent)", f=_nf, c=_nc)
        else:
            _suff = _message(" (news: fonti mute)", " (news: silent)")
    elif _mn.get("copertura") == "IGNOTA":
        _suff = _message(" (news: copertura ignota)", " (news: unknown)")
    if _suff:
        if score is None:
            verdict = _message("n.d.: copertura scarsa", "n/a: thin coverage")
        verdict += _suff
    return {"domain": "eventdesk", "score": score, "max_score": max_score, "verdict": verdict,
            "componente_nd": "news" if _news_nd else None,
            "lines": lines, "unscored": unscored, "excluded": excluded,
            "metrics": {"news": (n or {}).get("metrics"), "politics": (p or {}).get("metrics"),
                        "righe_rischio": len(pts), "righe_rischio_minime": 2}}


@scoped_language
def format_score_block(score: dict) -> str:
    """Blocco testo compatto da iniettare nel contesto dello specialista."""
    if not score:
        return ""
    L = []
    L.append(_t("=== SCORE DETERMINISTICO ({}) — calcolato in codice, parti da QUESTO ===").format(score["domain"].upper()))
    from bellomberg.core.presentation import message as _message
    _totale_nd = score.get("score") is None
    if _totale_nd:
        # Opus 5.5 09/10: totale n.d. -> si stampano COMUNQUE le righe dichiarate (n.d.,
        # STALE, escluse, informative): il desk deve vedere PERCHE' e' n.d.
        L.append(_message("Verdetto: {verdict}.", "Verdict: {verdict}.", verdict=score["verdict"]))
    elif not score.get("max_score"):
        # Opus 5.5 09/10: nessuna metrica misurabile = score n.d., non "0/0 punti"
        L.append(_message("Verdetto: {v} (score n.d.: nessuna metrica misurabile).",
                          "Verdict: {v} (score n/a: no measurable metric).", v=score["verdict"]))
    else:
        L.append(_t("Verdetto: {} ({}/{} punti rischio; piu' alto = piu' rischio).").format(
            score["verdict"], score["score"], score["max_score"]))
    _escluse_regola = score.get("excluded") or {}
    # contate le righe che portano punti: le righe informative (pt None, non in unscored)
    # non sono metriche e non entrano nel conteggio del massimo
    _righe = score.get("lines") or []

    def _e_buco(val, label=""):
        # valore che si dichiara n.d. (convenzione degli scorer) o etichetta "... (n.d.)"
        # come le righe di politics_score
        _s = str(val).strip().lower()
        return (_s.startswith("n.d.") or _s.startswith("n/a") or _s.startswith("stale")
                or str(label).rstrip().lower().endswith(("(n.d.)", "(n/a)")))
    # v2 10/10 (riserva BASSA): le righe-buco con 0 punti dentro il massimo di un altro scorer
    # non sono metriche MISURATE: non si contano nel «Massimo ricalcolato su N metriche»
    _n_misurate = sum(1 for _l, _v, _p in _righe if _p is not None and not _e_buco(_v, _l))
    if score.get("unscored") and not _totale_nd:  # fix 04/10 (A7): il massimo ricalcolato si DICHIARA
        L.append(_message("Massimo ricalcolato su {n} metriche misurate: escluse perché n.d. {escluse}.",
                          "Maximum recomputed on {n} measured metrics: excluded as n/a {escluse}.",
                          n=_n_misurate,
                          escluse=", ".join(str(x) for x in score["unscored"])))
    if _escluse_regola and not _totale_nd:  # PM 06/10: esclusione per regola (guardrail beta), non dato mancante
        L.append(_message("Massimo ricalcolato su {n} metriche: escluse dal punteggio per regola {escluse}.",
                          "Maximum recomputed on {n} metrics: excluded from the score by rule {escluse}.",
                          n=_n_misurate,
                          escluse="; ".join(str(k) + " (" + str(v) + ")" for k, v in _escluse_regola.items())))
    for label, val, pt in _righe:
        if pt is None and label in _escluse_regola:
            L.append(_message("  - {label:<26} {val}  -> esclusa dal punteggio (regola)",
                              "  - {label:<26} {val}  -> excluded from the score (rule)",
                              label=label, val=val))
            continue
        if pt is None and (label in (score.get("unscored") or []) or _e_buco(val)):
            # fix 04/10 (A7): riga dichiarata n.d., fuori dal punteggio
            L.append(_message("  - {label:<26} {val}  -> non punteggiata (dato mancante)",
                              "  - {label:<26} {val}  -> not scored (missing data)",
                              label=label, val=val))
            continue
        if pt is None:
            # Opus 5.5 09/10: riga di contesto (dettaglio per nome, copertura): niente punti
            # finti per-nome che non si sommano al totale
            L.append(_message("  - {label:<26} {val}  -> informativa (fuori punteggio)",
                              "  - {label:<26} {val}  -> context only (not scored)",
                              label=label, val=val))
            continue
        if pt == 0 and _e_buco(val, label):
            # un buco con 0 punti dentro il massimo di un altro scorer: MAI "(ok)"
            L.append(_message("  - {label:<26} {val}  -> 0 punti (n.d.: dato mancante, NON 'ok')",
                              "  - {label:<26} {val}  -> 0 points (n/a: missing data, NOT 'ok')",
                              label=label, val=val))
            continue
        # fix score 09/10: i punti possono essere CONTINUI (quant): fascia dal valore arrotondato.
        # Fix v2 10/10 (riserva 2): fascia = punti / massimo DELLA RIGA riportati su 0..3; la
        # struttura VIX vale fino a 6 e 2,98 era «critico» a meta' scala. Righe da 3: invariate.
        _mx_riga = (score.get("line_max") or {}).get(label, 3) or 3
        flag = ["ok", _t("attenzione"), _t("alto"), _t("critico")][max(0, min(int(round(3.0 * pt / _mx_riga)), 3))]
        L.append(_t("  - {:<26} {:>8}  -> {} punti ({})").format(label, val, pt, flag))
    for label, val, motivo in (score.get("info") or []):
        # misurate e mostrate, MAI punteggiate (es. Sharpe/VaR nel rischio book): col motivo
        L.append(_message("  - {label:<26} {val}  -> informativa, fuori punteggio ({motivo})",
                          "  - {label:<26} {val}  -> informational, not scored ({motivo})",
                          label=label, val=val, motivo=motivo))
    L.append(_t("Usa questi numeri come base fattuale: spiega COSA implicano e DOVE intervenire. "
             "Le metriche di performance (Sharpe, maxDD) sono TRAILING: fotografano il passato, "
             "non lo predicono — su titoli molto scesi dichiarane il limite (dottrina bilaterale "
             "PM 16/07) invece di trattarle come verdetto. "
             "Se chiami i tool e trovi numeri diversi, dichiara la discrepanza."))
    return "\n".join(L)


if __name__ == "__main__":
    # mock realistico su simboli INVENTATI (05/09, lotto 5b): stessi pesi di prima
    pdj = {"positions": [
        {"ticker": "THETA.L", "valore_mercato_eur": 30400}, {"ticker": "ZETA.MI", "valore_mercato_eur": 13300},
        {"ticker": "ALFA", "valore_mercato_eur": 11100}, {"ticker": "KRYPTO", "valore_mercato_eur": 5800},
        {"ticker": "OMEGA", "valore_mercato_eur": 18000}, {"ticker": "KAPPA.MI", "valore_mercato_eur": 9800}]}
    risk = {"portfolio": {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": 1.18,
                          "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4}}
    s = quant_score(pdj, risk)
    import json
    print(json.dumps({k: v for k, v in s.items() if k != "lines"}, indent=2))
    print()
    print(format_score_block(s))


# ============================================================
# CRUSCOTTO: sintesi di tutti gli score per Capo + memo (#186b)
# ============================================================
_SCORE_LABELS = {"macro": "Macro / Regime", "quant": "Rischio book", "fundamentals": "Valutazione",
                 "options": "Volatilita'", "crypto": "Crypto", "eventdesk": "Eventi & Geopolitica",
                 "news": "Notizie", "politics": "Geopolitica"}  # news/politics: chiavi legacy pre-fusione (memo vecchi)
_SCORE_ORDER = ["macro", "quant", "fundamentals", "options", "crypto", "eventdesk", "news", "politics"]
# Domini VIVI del comitato (= R1_STAGES). news/politics restano in _SCORE_ORDER solo per
# rendere i memo vecchi, ma non vanno dichiarati n.d. se mancano: sono fusi in eventdesk.
_SCORE_LIVE = ["macro", "quant", "fundamentals", "options", "crypto", "eventdesk"]


@scoped_language
def collect_scoreboard(cache):
    """Da blackboard.data['_score_cache'] -> lista ordinata di righe (label, verdict, score, max).

    Un dominio VIVO che non produce score NON sparisce: esce DICHIARATO n.d.
    (regola no-fallback-silenziosi). Prima veniva filtrato via e il cruscotto
    mostrava 4 domini su 6 senza dire che gli altri due mancavano.
    """
    rows = []
    if not isinstance(cache, dict):
        return rows
    for k in _SCORE_ORDER:
        sc = cache.get(k)
        if not (sc and isinstance(sc, dict) and sc.get("verdict")) and k in _SCORE_LIVE:
            # copre sia la chiave assente sia la chiave presente con valore None
            # (base.py cachea anche i None, regenerate_memo no)
            rows.append({"key": k, "label": _t(_SCORE_LABELS[k]),
                         "verdict": _t("n.d. - score non calcolabile (dichiarato)"),
                         "score": None, "max_score": None})
            continue
        if sc and isinstance(sc, dict) and sc.get("verdict"):
            rows.append({"key": k, "label": _t(_SCORE_LABELS[k]), "verdict": sc.get("verdict"),
                         "score": sc.get("score"), "max_score": sc.get("max_score"),
                         # v2 10/10: una componente n.d. (eventdesk senza news) -> il PDF non
                         # colora di verde un verdetto che descrive meta' del dominio
                         "componente_nd": sc.get("componente_nd")})
    return rows


@scoped_language
def format_scoreboard(cache):
    """Blocco testo per il contesto del Capo."""
    rows = collect_scoreboard(cache)
    if not rows:
        return ""
    L = [_t("=== CRUSCOTTO SCORING DETERMINISTICO (calcolato in codice dagli specialisti) ==="),
         _t("Indice 0-100 = score/massimo (piu' alto = piu' rischio). CONFRONTA i domini SOLO "
         "sull'indice: il massimo grezzo cambia col numero di metriche disponibili per "
         "specialista (9/18 e 8/21 valgono 50 e 38, non 'quasi uguale'). "
         "Bande: <25 basso, 25-50 medio, 50-75 elevato, >=75 critico.")]
    for r in rows:
        mx = r.get("max_score") or 0
        idx = "{:.0f}/100".format(100.0 * r["score"] / mx) if mx else "n.d."
        # senza guardia le righe dichiarate n.d. stamperebbero "(grezzo None/None)":
        # il Capo leggerebbe un artefatto invece di un buco
        grezzo = _t("  (grezzo {}/{})").format(r["score"], r["max_score"]) if mx else ""
        L.append("  {:<16} {:<34} {:>7}{}".format(
            r["label"], r["verdict"] or "", idx, grezzo))
    L.append(_t("DEVI sintetizzare questo cruscotto in una sezione dedicata del memo ('Cruscotto di rischio'): "
             "cosa dicono gli score nel loro insieme (regime macro, rischio del book, valutazione, volatilita', "
             "geopolitica) e come orientano concretamente le decisioni della settimana."))
    return "\n".join(L)
