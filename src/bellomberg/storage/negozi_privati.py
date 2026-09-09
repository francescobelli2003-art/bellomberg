# -*- coding: utf-8 -*-
"""negozi_privati.py — i NEGOZI PRIVATI dei dati del book che vivevano nei moduli fuori
dalla classificazione (lotto 5 del criterio (1), 05/09, Fable 5.1).

Ogni negozio e' un file JSON in `data/` (ignorato da git, fuori dal perimetro pubblico) con la
FORMA in un `.example.json` tracciato coi simboli inventati. Le regole sono quelle di
`news_aggregator.carica_termini`, `fonti_guidance.carica_fonti` e `classificazione.carica_veicoli`
(che restano dove sono): negozio assente o illeggibile = voci VUOTE con `origine` e `motivo`
scritti, mai un ripiego muto (regola PM 14/07); UNA voce malformata rende illeggibile il negozio
INTERO (mezzo negozio caricato e' un ripiego muto); le chiavi con `_` davanti sono note.

Famiglie (negozio in data/ -> esempio tracciato -> chi lo legge):
- alias_fonti.json            alias listino->fonte: `finnhub`, `sec`, `yfinance` (stesso
                              strumento su Yahoo) e `correlazione` (proxy della sola serie)
                              -> finnhub_news, sec_edgar, price_updater, consigliere_multi
- iv_tickers.json             la lista DICHIARATA dei simboli con IV storica -> iv_history, vol_cone
- fattori_portafoglio.json    `crypto_correlati` (BTC come settimo fattore) e `regioni`
                              (override dell'esposizione economica) -> portfolio_factors
- lei_emittenti.json          SIMBOLO -> LEI verificato -> esef.resolve_lei
- istituzioni.json            `cik` (slug -> CIK 13F) e `cef` (SIMBOLO del fondo chiuso ->
                              gestore) -> sec_edgar, agent_tools, cef_lookthrough
- retro_title_correzioni.json le correzioni a mano dei titoli delle chat -> retro_title_chats
- prezzi_speciali.json       `senza_yfinance` (i simboli senza serie prezzi utile: i motori li
                              escludono e lo dichiarano) e `coingecko` (SIMBOLO -> id CoinGecko,
                              ultima fonte di price_updater). Le due sezioni sono INDIPENDENTI
                              -> portfolio_analytics/factors/garch/montecarlo/risk, price_updater

SOLO stdlib: nessun import di progetto. La regola del percorso dati e' quella di memory_db.DB_DIR
(BELLOMBERG_DATA_DIR relativo alla radice o assoluto), replicata come in classificazione.py.
I consumatori rileggono il negozio A OGNI CHIAMATA (come fonti_correnti/termini_correnti): una
modifica a mano si vede senza riavviare, e le prove possono puntare `PERCORSO_*` altrove.
"""
import json
import os
import re
from typing import Any, Callable, Dict, Optional, Tuple
from bellomberg.core.paths import DATA_DIR

PERCORSO_ALIAS = str(DATA_DIR / "alias_fonti.json")
ESEMPIO_ALIAS = "alias_fonti.example.json"
PERCORSO_IV = str(DATA_DIR / "iv_tickers.json")
ESEMPIO_IV = "iv_tickers.example.json"
PERCORSO_FATTORI = str(DATA_DIR / "fattori_portafoglio.json")
ESEMPIO_FATTORI = "fattori_portafoglio.example.json"
PERCORSO_LEI = str(DATA_DIR / "lei_emittenti.json")
ESEMPIO_LEI = "lei_emittenti.example.json"
PERCORSO_ISTITUZIONI = str(DATA_DIR / "istituzioni.json")
ESEMPIO_ISTITUZIONI = "istituzioni.example.json"
PERCORSO_CORREZIONI_TITOLI = str(DATA_DIR / "retro_title_correzioni.json")
ESEMPIO_CORREZIONI_TITOLI = "retro_title_correzioni.example.json"
PERCORSO_PREZZI = str(DATA_DIR / "prezzi_speciali.json")
ESEMPIO_PREZZI = "prezzi_speciali.example.json"
PERCORSO_TEMI_TITOLI = str(DATA_DIR / "news_topics_tickers.json")
ESEMPIO_TEMI_TITOLI = "news_topics_tickers.example.json"


# ============================================================
# IL CARICATORE COMUNE
# ============================================================
def carica(path: str, esempio: str, valida: Callable[[Dict[str, Any]], Any],
           nome: str = "voci", vuoto: Any = None) -> Dict[str, Any]:
    """Legge un negozio privato. Torna {nome: voci, "origine": path | 'assente' |
    'illeggibile', "motivo": str | None}. `valida(grezzo)` riceve l'oggetto SENZA le chiavi
    `_` e torna le voci nella forma del consumatore; solleva ValueError con il motivo su UNA
    voce malformata, e allora il negozio INTERO e' illeggibile: mezzo negozio caricato e' un
    ripiego muto. `vuoto` e' il valore delle voci quando il negozio non si legge ({} se non
    detto): ha la forma delle voci, cosi' chi legge non deve distinguere un tipo diverso."""
    vuoto = {} if vuoto is None else vuoto
    if not os.path.exists(path):
        return {nome: vuoto, "origine": "assente",
                "motivo": "negozio non trovato: %s (copia %s in data/ e mettici i TUOI dati)"
                          % (path, esempio)}
    try:
        with open(path, encoding="utf-8") as fh:
            # Stessa regola del negozio veicoli, anche sugli oggetti annidati.
            from bellomberg.storage.classificazione import _senza_doppie
            grezzo = json.load(fh, object_pairs_hook=_senza_doppie)
    except Exception as e:
        return {nome: vuoto, "origine": "illeggibile",
                "motivo": "%s: %s" % (type(e).__name__, e)}
    if not isinstance(grezzo, dict):
        return {nome: vuoto, "origine": "illeggibile",
                "motivo": "il negozio non e' un oggetto JSON ma %s" % type(grezzo).__name__}
    try:
        voci = valida({k: v for k, v in grezzo.items() if not str(k).startswith("_")})
    except ValueError as e:
        return {nome: vuoto, "origine": "illeggibile", "motivo": str(e)}
    return {nome: voci, "origine": path, "motivo": None}


def stringa_piena(chiave: Any, valore: Any) -> str:
    """Il valore di una voce dev'essere una stringa non vuota: torna la stringa ripulita."""
    if not isinstance(valore, str) or not valore.strip():
        raise ValueError("voce %r malformata: serve una stringa non vuota" % (chiave,))
    return valore.strip()


def mappa_canonica(grezzo: Any, valida_valore: Callable[[Any, Any], Any],
                   cosa: str = "chiave") -> Dict[str, Any]:
    """{CHIAVE: valore validato}. Ogni lookup fa .upper().strip(): una chiave scritta in
    minuscolo o con spazi non verrebbe MAI trovata e il log direbbe «voce assente», vero per
    il codice e falso per chi l'ha appena scritta — quindi e' malformata, come un doppione."""
    if not isinstance(grezzo, dict):
        raise ValueError("%s: serve un oggetto JSON, non %s" % (cosa, type(grezzo).__name__))
    out: Dict[str, Any] = {}
    for k, v in grezzo.items():
        kk = str(k).strip().upper()
        if kk != k or kk in out or not kk or re.search(r"\s", kk):
            raise ValueError("%s %r non canonica o doppia: scrivila MAIUSCOLA, senza spazi e "
                             "una volta sola (%r)" % (cosa, k, kk))
        out[kk] = valida_valore(k, v)
    return out


def lista_canonica(grezzo: Any, cosa: str = "simbolo") -> Tuple[str, ...]:
    """Tupla di simboli MAIUSCOLI, non vuoti, senza doppioni, nell'ordine del file."""
    if not isinstance(grezzo, list):
        raise ValueError("%s: serve una lista JSON, non %s" % (cosa, type(grezzo).__name__))
    out = []
    for x in grezzo:
        if not isinstance(x, str) or not x.strip() or x.strip().upper() != x:
            raise ValueError("%s %r malformato: scrivilo MAIUSCOLO, senza spazi" % (cosa, x))
        if x in out:
            raise ValueError("%s %r doppio" % (cosa, x))
        out.append(x)
    return tuple(out)


def _sezioni_ammesse(grezzo: Dict[str, Any], ammesse: Tuple[str, ...]) -> None:
    fuori = sorted(set(grezzo) - set(ammesse))
    if fuori:
        raise ValueError("sezione %r sconosciuta: ammesse %s" % (fuori[0], ", ".join(ammesse)))


# ============================================================
# LE FAMIGLIE
# ============================================================
def _valida_alias(grezzo: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    _sezioni_ammesse(grezzo, ("finnhub", "sec", "yfinance", "correlazione"))
    finnhub = mappa_canonica(grezzo.get("finnhub", {}), stringa_piena, "alias finnhub")
    sec = mappa_canonica(grezzo.get("sec", {}), stringa_piena, "alias sec")

    def _simbolo(chiave, valore):
        simbolo = stringa_piena(chiave, valore)
        if simbolo != simbolo.upper() or re.search(r"\s", simbolo):
            raise ValueError("alias %r malformato: scrivilo MAIUSCOLO e senza spazi" % simbolo)
        return simbolo

    yfinance = mappa_canonica(grezzo.get("yfinance", {}), _simbolo, "alias yfinance")
    correlazione = mappa_canonica(
        grezzo.get("correlazione", {}), _simbolo, "proxy correlazione")
    riservati = sorted(set(yfinance) & {"BTC", "ETH", "SOL"})
    if riservati:
        raise ValueError("alias yfinance %r ridefinisce una conversione canonica pubblica" %
                         riservati[0])
    for k in sec:
        if "." in k:
            raise ValueError("alias sec %r: la chiave e' la BASE di listino (prima del punto), "
                             "lookup_cik confronta la base e non la troverebbe mai" % k)
    return {"finnhub": finnhub, "sec": sec,
            "yfinance": yfinance, "correlazione": correlazione}


def carica_alias(path: Optional[str] = None) -> Dict[str, Any]:
    """Alias per quattro fonti; `yfinance` e `correlazione` sono sezioni opzionali."""
    return carica(path or PERCORSO_ALIAS, ESEMPIO_ALIAS, _valida_alias, "alias",
                  {"finnhub": {}, "sec": {}, "yfinance": {}, "correlazione": {}})


def _valida_iv(grezzo: Dict[str, Any]) -> Tuple[str, ...]:
    _sezioni_ammesse(grezzo, ("tickers",))
    if "tickers" not in grezzo:
        raise ValueError("manca la chiave 'tickers' (la lista dichiarata dei simboli)")
    return lista_canonica(grezzo["tickers"])


def carica_iv(path: Optional[str] = None) -> Dict[str, Any]:
    """{"tickers": (SIMBOLO, ...), origine, motivo}."""
    return carica(path or PERCORSO_IV, ESEMPIO_IV, _valida_iv, "tickers", ())


def _valida_fattori(regioni_valide):
    def _v(grezzo: Dict[str, Any]) -> Dict[str, Any]:
        _sezioni_ammesse(grezzo, ("crypto_correlati", "regioni"))
        crypto = frozenset(lista_canonica(grezzo.get("crypto_correlati", [])))

        def _regione(k, v):
            v = stringa_piena(k, v)
            if regioni_valide is not None and v not in regioni_valide:
                raise ValueError("regione %r di %r sconosciuta: ammesse %s"
                                 % (v, k, ", ".join(sorted(regioni_valide))))
            return v
        regioni = mappa_canonica(grezzo.get("regioni", {}), _regione, "override di regione")
        return {"crypto_correlati": crypto, "regioni": regioni}
    return _v


def carica_fattori(path: Optional[str] = None, regioni_valide=None) -> Dict[str, Any]:
    """{"fattori": {"crypto_correlati": frozenset, "regioni": {SIMBOLO: regione}}, origine,
    motivo}. `regioni_valide`: l'insieme delle regioni che il consumatore sa regredire; un
    valore fuori e' una voce malformata (il negozio intero e' illeggibile)."""
    return carica(path or PERCORSO_FATTORI, ESEMPIO_FATTORI, _valida_fattori(regioni_valide),
                  "fattori", {"crypto_correlati": frozenset(), "regioni": {}})


_LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def _valida_lei(grezzo: Dict[str, Any]) -> Dict[str, str]:
    def _lei(k, v):
        v = stringa_piena(k, v)
        if not _LEI.match(v):
            raise ValueError("voce %r: %r non ha la forma di un LEI (20 caratteri alfanumerici, "
                             "le ultime due cifre di controllo)" % (k, v))
        return v
    return mappa_canonica(grezzo, _lei, "simbolo")


def carica_lei(path: Optional[str] = None) -> Dict[str, Any]:
    """{"lei": {SIMBOLO: LEI}, origine, motivo}."""
    return carica(path or PERCORSO_LEI, ESEMPIO_LEI, _valida_lei, "lei")


_SLUG = re.compile(r"^[a-z0-9_]+$")
_CIK = re.compile(r"^[0-9]{10}$")


def _valida_istituzioni(grezzo: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    _sezioni_ammesse(grezzo, ("cik", "cef"))
    cik_grezzo = grezzo.get("cik", {})
    if not isinstance(cik_grezzo, dict):
        raise ValueError("cik: serve un oggetto JSON slug -> CIK")
    cik: Dict[str, str] = {}
    for slug, v in cik_grezzo.items():
        if not _SLUG.match(str(slug)):
            raise ValueError("slug %r malformato: minuscolo, lettere/cifre/_" % (slug,))
        v = stringa_piena(slug, v)
        if not _CIK.match(v):
            raise ValueError("CIK di %r malformato: servono 10 cifre, non %r" % (slug, v))
        cik[slug] = v

    def _cef(k, v):
        if not isinstance(v, dict):
            raise ValueError("cef %r: serve un oggetto {investor, manager, not_in_13f}" % (k,))
        fuori = sorted(set(v) - {"investor", "manager", "not_in_13f"})
        if fuori:
            raise ValueError("cef %r: campo %r sconosciuto" % (k, fuori[0]))
        inv = stringa_piena("%s.investor" % k, v.get("investor"))
        if inv not in cik:
            raise ValueError("cef %r: investor %r non e' uno slug della sezione cik" % (k, inv))
        out = {"investor": inv, "manager": stringa_piena("%s.manager" % k, v.get("manager"))}
        if v.get("not_in_13f") is not None:
            out["not_in_13f"] = stringa_piena("%s.not_in_13f" % k, v.get("not_in_13f"))
        return out
    cef = mappa_canonica(grezzo.get("cef", {}), _cef, "fondo chiuso")
    return {"cik": cik, "cef": cef}


def carica_istituzioni(path: Optional[str] = None) -> Dict[str, Any]:
    """{"istituzioni": {"cik": {slug: CIK}, "cef": {SIMBOLO: {investor, manager, not_in_13f}}},
    origine, motivo}."""
    return carica(path or PERCORSO_ISTITUZIONI, ESEMPIO_ISTITUZIONI, _valida_istituzioni,
                  "istituzioni", {"cik": {}, "cef": {}})


def _id_sessione(k: Any) -> int:
    s = str(k).strip()
    if not s.isdigit():
        raise ValueError("id di sessione %r malformato: serve un intero" % (k,))
    return int(s)


def _lista_parole(k, v):
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ValueError("voce %r: serve una lista di parole (stringhe non vuote)" % (k,))
    return list(v)


def _valida_correzioni(grezzo: Dict[str, Any]) -> Dict[str, Dict[int, Any]]:
    _sezioni_ammesse(grezzo, ("correzioni", "intatti"))
    corr_grezze = grezzo.get("correzioni", {})
    int_grezzi = grezzo.get("intatti", {})
    if not isinstance(corr_grezze, dict) or not isinstance(int_grezzi, dict):
        raise ValueError("correzioni e intatti sono oggetti JSON con l'id di sessione per chiave")
    correzioni: Dict[int, Dict[str, Any]] = {}
    for k, v in corr_grezze.items():
        sid = _id_sessione(k)
        if not isinstance(v, dict):
            raise ValueError("correzione %r: serve un oggetto {titolo, cosa_c_era, perche}" % (k,))
        fuori = sorted(set(v) - {"titolo", "cosa_c_era", "perche", "deve_contenere"})
        if fuori:
            raise ValueError("correzione %r: campo %r sconosciuto" % (k, fuori[0]))
        voce = {c: stringa_piena("%s.%s" % (k, c), v.get(c))
                for c in ("titolo", "cosa_c_era", "perche")}
        voce["deve_contenere"] = _lista_parole(k, v.get("deve_contenere", []))
        if sid in correzioni:
            raise ValueError("correzione %r doppia" % (k,))
        correzioni[sid] = voce
    intatti: Dict[int, list] = {}
    for k, v in int_grezzi.items():
        sid = _id_sessione(k)
        if sid in correzioni:
            raise ValueError("id %r sta sia in correzioni sia in intatti" % (k,))
        intatti[sid] = _lista_parole(k, v)
    return {"correzioni": correzioni, "intatti": intatti}


def carica_correzioni_titoli(path: Optional[str] = None) -> Dict[str, Any]:
    """{"correzioni": {id: {titolo, cosa_c_era, perche, deve_contenere}}, "intatti": {id: [parole]},
    origine, motivo}."""
    r = carica(path or PERCORSO_CORREZIONI_TITOLI, ESEMPIO_CORREZIONI_TITOLI, _valida_correzioni,
               "voci", {"correzioni": {}, "intatti": {}})
    voci = r.pop("voci")
    return {"correzioni": voci["correzioni"], "intatti": voci["intatti"],
            "origine": r["origine"], "motivo": r["motivo"]}


# id CoinGecko: minuscolo, cifre e trattini singoli (la forma dell'URL dell'API). La chiave
# resta MAIUSCOLA come in tutte le famiglie (mappa_canonica): l'id sta nel VALORE, perche'
# una chiave minuscola non verrebbe MAI trovata dal lookup .upper() di price_updater.
_ID_COINGECKO = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _valida_prezzi(grezzo: Dict[str, Any]) -> Dict[str, Any]:
    _sezioni_ammesse(grezzo, ("senza_yfinance", "coingecko"))
    # la sezione e' OBBLIGATORIA anche se vuota (review 05/09): ometterla si leggerebbe come
    # «nessun simbolo da saltare», che e' la stessa cosa che dice un negozio ASSENTE — ma qui
    # il negozio c'e' e il KO dei motori non scatterebbe. Una lista vuota va scritta `[]`.
    if "senza_yfinance" not in grezzo:
        raise ValueError("manca la sezione obbligatoria 'senza_yfinance': scrivila, anche "
                         "vuota ([]), cosi' «nessun simbolo da saltare» e' una DICHIARAZIONE "
                         "e non una dimenticanza")
    senza = frozenset(lista_canonica(grezzo["senza_yfinance"], "simbolo"))
    # `lista_canonica` (usata anche dalla lista IV) rifiuta gli spazi in TESTA e in CODA ma
    # non quelli INTERNI: qui un «ALFA BETA» non combacerebbe con nessun ticker del DB e lo
    # skip sarebbe muto. Il limite dell'helper condiviso resta suo; qui si stringe.
    for sim in sorted(senza):
        if re.search(r"\s", sim):
            raise ValueError("simbolo %r malformato: niente spazi dentro il simbolo "
                             "(non combacerebbe con nessun ticker)" % sim)

    def _id(k, v):
        v = stringa_piena(k, v)
        if not _ID_COINGECKO.match(v):
            raise ValueError("id CoinGecko %r di %r malformato: minuscolo, cifre e trattini "
                             "singoli (come nell'URL dell'API)" % (v, k))
        return v
    coingecko = mappa_canonica(grezzo.get("coingecko", {}), _id, "simbolo")
    return {"senza_yfinance": senza, "coingecko": coingecko}


def carica_prezzi_speciali(path: Optional[str] = None) -> Dict[str, Any]:
    """{"prezzi": {"senza_yfinance": frozenset, "coingecko": {SIMBOLO: id}}, origine, motivo}.
    Le due sezioni sono INDIPENDENTI: un id CoinGecko non implica lo skip nei motori (i proxy
    crypto generici di price_updater hanno una serie yfinance), e un simbolo saltato puo' non
    avere nessuna fonte. Negozio assente = insieme e mappa VUOTI, col motivo: chi legge
    DICHIARA (i motori dove il numero in euro cambierebbe base si fermano)."""
    return carica(path or PERCORSO_PREZZI, ESEMPIO_PREZZI, _valida_prezzi, "prezzi",
                  {"senza_yfinance": frozenset(), "coingecko": {}})


# La chiave di QUESTA famiglia non e' un simbolo: e' l'`id` di un tema di news_topics.TOPICS,
# che nel registro e' uno slug MINUSCOLO. Percio' `mappa_canonica` non si puo' usare — forza
# le chiavi MAIUSCOLE, e qui il lookup confronta `t["id"]`: una chiave maiuscola non verrebbe
# MAI trovata e il codice direbbe «nessun titolo per questo tema», vero per la macchina e
# falso per chi ha appena scritto la voce. Il vincolo e' lo stesso delle altre famiglie
# (la chiave dev'essere nella forma in cui la si cerca), la forma e' l'opposta.
_ID_TEMA = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def _valida_temi_titoli(grezzo: Dict[str, Any]) -> Dict[str, list]:
    if not isinstance(grezzo, dict):
        raise ValueError("il negozio dei temi: serve un oggetto JSON, non %s"
                         % type(grezzo).__name__)
    out: Dict[str, list] = {}
    for k, v in grezzo.items():
        kk = str(k)
        if not _ID_TEMA.match(kk):
            raise ValueError("id di tema %r malformato: e' lo slug del registro, minuscolo, "
                              "cifre e trattini bassi (come `tema_esempio`)" % (k,))
        # NIENTE controllo del doppione qui, ed e' una scelta misurata, non una svista: la
        # chiave non viene trasformata (nelle altre famiglie `mappa_canonica` la porta in
        # MAIUSCOLO, e li' due chiavi diverse possono collidere), quindi un `kk in out`
        # sarebbe irraggiungibile — `json.load` tiene l'ULTIMA di due chiavi uguali e la
        # prima e' gia' sparita quando arriviamo qui. Un controllo che non puo' scattare si
        # legge come una garanzia e non lo e'. LIMITE DICHIARATO, e vale per tutte e otto le
        # famiglie: una chiave scritta due volte nel negozio applica la seconda in silenzio.
        # Curarlo vuol dire un `object_pairs_hook` nel caricatore COMUNE, cioe' toccare le
        # altre sette: e' un lotto suo.
        simboli = lista_canonica(v, "tema %s: titolo" % kk)
        if not simboli:
            # una lista vuota si legge «nessun titolo», che e' ANCHE il ripiego del negozio
            # assente: due stati diversi con la stessa forma. Per togliere un legame si
            # toglie la voce, cosi' `temi_senza_riscontro` non ha nulla da dichiarare.
            raise ValueError("tema %s: lista vuota — per togliere il legame togli la voce, "
                             "non lasciare la lista vuota" % kk)
        out[kk] = list(simboli)
    return out


def carica_temi_titoli(path: Optional[str] = None) -> Dict[str, Any]:
    """{"temi": {id_tema: [SIMBOLO, ...]}, origine, motivo} — quali TITOLI muove ogni tema
    di news_topics. Stava cablata nel sorgente come `tickers_affected` (12 temi, 29
    occorrenze): non era prosa, `news_aggregator` la copia su OGNI notizia del tema e il
    payload arriva al modello dei desk. Negozio assente = mappa VUOTA, e chi legge DICHIARA
    (`providers_blocked` la porta fra le fonti mute): «nessuna notizia macro» e «non so a
    quali titoli legarle» non possono essere la stessa frase."""
    return carica(path or PERCORSO_TEMI_TITOLI, ESEMPIO_TEMI_TITOLI, _valida_temi_titoli,
                  "temi", {})
