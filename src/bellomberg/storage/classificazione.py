"""classificazione.py — il CONTRATTO della classificazione dei veicoli (05/09, Fable 5.1, lotto 1).

Direttiva PM 04/09: «il ripiego della classificazione non deve essere operating ma sconosciuto e
dichiarato; ogni classificazione porta la sua provenienza; e servono due prove — prima/dopo
identico sul mio libro, e una matrice su strumenti pubblici che non sono miei».

Il difetto che cura (misurato il 04/09, §9-septquinquagies e §9-sexquinquagies): i motori
instradano per LISTA DI TICKER del book (DAT_TICKERS, CEF_SOURCES, VEHICLE_TICKERS, MNAV_KINDS,
SECTOR_OVERRIDES), il tipo di veicolo NON esiste come dato, e un fondo chiuso estraneo che
Yahoo marca EQUITY passa tutte le cinture e finisce in DCF completo. Le liste sono il book
scritto nel sorgente: pubblicarle e' pubblicare il portafoglio; toglierle senza un dato al loro
posto e' rompere i motori.

Tre cose, in questo file e in nessun altro:

1. `Etichetta`: una classificazione IMMUTABILE con dominio, valore, fonte, evidenza, confidenza,
   dichiarazione (frase che il modello e il PM leggono) e data di verifica. `etichetta()` e'
   l'UNICA porta (lo prova `tests/test_classificazione.py` con uno scan del repo): dominio e
   fonte fuori dai vocabolari chiusi alzano ValueError, la confidenza e' DERIVATA dalla fonte
   (nessuno puo' dichiarare «misurata» un'euristica), la dichiarazione e' costruita dai
   template e non si puo' dimenticare. Due ignoranze DIVERSE: `sconosciuto()` = nessuna fonte
   dice cosa sia (buco dichiarato), `guasto()` = la fonte e' ROTTA (asimmetria copiata da
   fonti_guidance.carica_fonti: assente vs illeggibile). `ripiego()` = il valore di oggi,
   etichettato come non-misura.

2. Il NEGOZIO dei veicoli, `data/veicoli.json` (privato, ignorato da git; esempio tracciato
   `veicoli.example.json` con simboli inventati). E' l'unico posto in cui vive il TIPO di
   veicolo di un simbolo, con la sua provenienza. Stessa famiglia di
   news_aggregator.carica_termini e fonti_guidance.carica_fonti: assente/illeggibile dichiarati
   con `origine` e `motivo`; UNA voce malformata rende illeggibile il negozio INTERO (mezzo
   negozio caricato e' un ripiego muto); chiavi canoniche (MAIUSCOLE, senza spazi, una volta)
   o il negozio e' illeggibile col nome della chiave. Un simbolo ASSENTE dal negozio e'
   `sconosciuto` dichiarato, MAI `operating`.

3. Il registro `CLASSIFICATORI` (dominio -> funzione): ogni classificatore, sull'input che non
   matcha nulla, DEVE tornare `sconosciuto` (lo prova il test iterando il registro). Il lotto 1
   registra `natura` (il tipo di veicolo dal negozio); i motori (lotto 2) ne aggiungeranno altri
   e leggeranno le VISTE (`veicoli_per_tipo`, `classe_size_di`) al posto delle liste cablate.

Stdlib e helper di presentazione bilingue; nessun import di DB o provider, cosi' le prove lo importano senza far girare migrazioni
DB ne' scrivere cache. La regola del percorso dati e' quella di memory_db.DB_DIR
(BELLOMBERG_DATA_DIR relativo alla radice o assoluto), replicata qui senza importare memory_db.
"""
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional
from bellomberg.core.paths import DATA_DIR, EXAMPLES_DIR
from bellomberg.core.presentation import message as _message, render_payload, error_text

PERCORSO_VEICOLI = str(DATA_DIR / "veicoli.json")
ESEMPIO_VEICOLI = str(EXAMPLES_DIR / "veicoli.example.json")

# --------------------------------------------------------------------------
# Vocabolari CHIUSI. Aggiungere un nome qui e' una modifica di codice con la sua review:
# e' il punto — nessuno inventa una sesta forma di `_matched` in un altro file.
# --------------------------------------------------------------------------
DOMINI = ("natura", "profilo_valutazione", "classe_size", "settore_policy",
          "settore_gics", "bucket_economico", "regione_fattoriale", "regione_hhi",
          "valuta", "simbolo_dati", "identificativo", "copertura_news", "copertura_opzioni")

FONTI = ("quote_type", "misura_dato", "registro_pm", "tassonomia_esatta",
         "tassonomia_keyword", "euristica_simbolo", "cache", "ripiego", "nessuna")

CONFIDENZE = ("misurata", "dichiarata_pm", "dedotta", "nessuna")

_CONFIDENZA_DA_FONTE = {
    "quote_type": "misurata", "misura_dato": "misurata", "cache": "misurata",
    "registro_pm": "dichiarata_pm",
    "tassonomia_esatta": "dedotta", "tassonomia_keyword": "dedotta", "euristica_simbolo": "dedotta",
    "ripiego": "nessuna", "nessuna": "nessuna",
}

# il TIPO di veicolo (dominio `natura`): cosa E' lo strumento, non come si valuta
TIPI = ("operating", "bank", "cef", "dat", "etf", "etn", "commodity", "crypto", "holding",
        "sconosciuto")
# la classe per il sizing: decisione di POLICY del PM (oggi VEHICLE_TICKERS), non si deduce dal tipo
CLASSI_SIZE = ("veicolo", "single")
SOTTOSTANTI_KIND_DAT = {"dat_bitcoin": "BTC-USD", "dat_hype": "HYPE"}
# da dove viene la voce del negozio: scritta dall'utente, dedotta da quoteType Yahoo, o ignota
PROVENIENZE = ("dichiarato", "derivato:quote_type", "sconosciuto")
_FONTE_DA_PROVENIENZA = {"dichiarato": "registro_pm", "derivato:quote_type": "quote_type",
                         "sconosciuto": "nessuna"}

# i campi di una voce del negozio: nome -> (obbligatorio, validatore, descrizione)
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _str_o_none(x):
    return x is None or isinstance(x, str)


def _valuta_o_none(x):
    return x is None or (isinstance(x, str) and re.fullmatch(r"[A-Z]{3}", x) is not None)


def _data_iso_o_none(x):
    if x is None:
        return True
    if not isinstance(x, str) or not _DATA_ISO.match(x):
        return False
    try:
        date.fromisoformat(x)
        return True
    except ValueError:
        return False


CAMPI_VOCE = {
    "tipo":                (True,  lambda x: x in TIPI,                 "uno di %s" % (TIPI,)),
    "provenienza":         (True,  lambda x: x in PROVENIENZE,          "uno di %s" % (PROVENIENZE,)),
    "sottostante":         (False, _str_o_none,                         "stringa o null"),
    "kind":                (False, lambda x: x is None or (isinstance(x, str) and x in SOTTOSTANTI_KIND_DAT),
                            "kind del motore DAT dichiarato, coerente col sottostante, o null"),
    "nav_fonte":           (False, _str_o_none,                         "stringa o null (chiave del fetcher in cef_nav.FETCH_NAV)"),
    "nav_valuta":          (False, _str_o_none,                         "stringa o null (valuta del NAV)"),
    "valuta":              (False, _valuta_o_none,                     "codice di quotazione di 3 lettere maiuscole o null; GBX distinto da GBP"),
    "nome":                (False, _str_o_none,                         "stringa o null (ragione sociale, per i messaggi)"),
    "settore_tema":        (False, _str_o_none,                         "stringa o null (etichetta by_sector)"),
    "bucket_economico":    (False, _str_o_none,                         "stringa o null"),
    "settore_policy":      (False, _str_o_none,                         "stringa o null (cap settoriale del sizing)"),
    "profilo_valutazione": (False, _str_o_none,                         "stringa o null (chiave di sector_taxonomy)"),
    "classe_size":         (False, lambda x: x is None or x in CLASSI_SIZE, "uno di %s o null" % (CLASSI_SIZE,)),
    "verificato_il":       (False, _data_iso_o_none,                    "data YYYY-MM-DD o null"),
    "note":                (False, _str_o_none,                         "stringa"),
}


# --------------------------------------------------------------------------
# 1. L'etichetta e la sua unica porta
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Etichetta:
    dominio: str
    valore: Optional[str]
    fonte: str
    evidenza: str
    confidenza: str
    dichiarazione: str
    verificato_il: Optional[str] = None

    def __str__(self) -> str:
        # per i campi <dominio>_source dei payload: «fonte — evidenza», convenzione di casa
        return _message('{v0} — {v1}', '{v0} — {v1}', v0=self.fonte, v1=self.evidenza)

    def as_dict(self) -> Dict[str, Any]:
        return render_payload(asdict(self))


def _controlla(dominio: str, fonte: str, evidenza: str, verificato_il: Optional[str]) -> None:
    if dominio not in DOMINI:
        raise ValueError(_message('dominio {v0!r} fuori vocabolario: {v1}', 'Domain {v0!r} outside vocabulary: {v1}', v0=dominio, v1=DOMINI))
    if fonte not in FONTI:
        raise ValueError(_message('fonte {v0!r} fuori vocabolario: {v1}', 'Source {v0!r} outside vocabulary: {v1}', v0=fonte, v1=FONTI))
    if not isinstance(evidenza, str) or not evidenza.strip():
        raise ValueError(_message("evidenza obbligatoria: un'etichetta senza evidenza e' un'opinione", 'Evidence required: a label without evidence is an opinion'))
    if not _data_iso_o_none(verificato_il):
        raise ValueError(_message("verificato_il {v0!r} non e' una data YYYY-MM-DD", 'verificato_il {v0!r} is not a YYYY-MM-DD date', v0=verificato_il))


def _costruisci(dominio, valore, fonte, evidenza, dichiarazione, verificato_il=None) -> Etichetta:
    _controlla(dominio, fonte, evidenza, verificato_il)
    return Etichetta(dominio=dominio, valore=valore, fonte=fonte, evidenza=evidenza,
                     confidenza=_CONFIDENZA_DA_FONTE[fonte], dichiarazione=dichiarazione,
                     verificato_il=verificato_il)


def etichetta(dominio: str, valore: str, fonte: str, evidenza: str,
              verificato_il: Optional[str] = None) -> Etichetta:
    """UNICA porta per una classificazione CON valore. ValueError se dominio o fonte sono
    fuori vocabolario, se il valore manca (usa `sconosciuto`/`guasto`) o se la fonte e'
    `nessuna`/`ripiego` (usa gli helper: la frase che ne esce e' diversa apposta). La
    confidenza e' DERIVATA dalla fonte: non e' un parametro."""
    if valore is None or (isinstance(valore, str) and not valore.strip()):
        raise ValueError(_message("valore assente: usa sconosciuto() o guasto(), non un'etichetta vuota", 'Missing value: use sconosciuto() or guasto(), not an empty label'))
    if fonte in ("nessuna", "ripiego"):
        raise ValueError(_message("fonte {v0!r}: usa sconosciuto()/guasto()/ripiego(), che dichiarano il perche'", 'Source {v0!r}: use sconosciuto()/guasto()/ripiego(), which declare why', v0=fonte))
    if not isinstance(valore, str):
        raise ValueError(_message("valore {v0!r} non e' una stringa", 'Value {v0!r} is not a string', v0=valore))
    _controlla(dominio, fonte, evidenza, verificato_il)
    frase = _message('{v0} = {v1} [{v2}, {v3}]: {v4}', '{v0} = {v1} [{v2}, {v3}]: {v4}', v0=dominio, v1=valore, v2=fonte, v3=_CONFIDENZA_DA_FONTE[fonte], v4=evidenza)
    return _costruisci(dominio, valore, fonte, evidenza, frase, verificato_il)


def sconosciuto(dominio: str, evidenza: str) -> Etichetta:
    """Nessuna fonte dice cosa sia: valore None, fonte `nessuna`. E' un BUCO dichiarato,
    non un ripiego — e la frase lo dice in maiuscolo perche' sopravviva a un taglio."""
    frase = (_message("{v0} SCONOSCIUTO: {v1} (nessuna fonte lo dice: non e' un ripiego, e' un buco dichiarato)", '{v0} UNKNOWN: {v1} (no source identifies it: this is a declared gap, not a fallback)', v0=dominio, v1=evidenza))
    return _costruisci(dominio, None, "nessuna", evidenza, frase)


def ripiego(dominio: str, valore: str, evidenza: str) -> Etichetta:
    """Il valore che il codice applica OGGI in assenza di una misura (es. il limite prudente
    single-stock): resta, ma etichettato come non-misura, con confidenza `nessuna`."""
    if not isinstance(valore, str) or not valore.strip():
        raise ValueError(_message('un ripiego ha sempre un valore: quello applicato', 'A fallback always has a value: the one applied'))
    frase = _message('{v0} = {v1} per RIPIEGO, non una misura: {v2}', '{v0} = {v1} as FALLBACK, not a measurement: {v2}', v0=dominio, v1=valore, v2=evidenza)
    return _costruisci(dominio, valore, "ripiego", evidenza, frase)


def guasto(dominio: str, motivo: str) -> Etichetta:
    """La fonte e' ROTTA (negozio illeggibile, servizio giu'): il dato puo' esistere, e' la
    fonte che non risponde. Diverso da `sconosciuto`: una virgola in piu' nel negozio non
    deve far sembrare ignoto ogni veicolo del PM."""
    frase = (_message("{v0} NON DETERMINABILE, fonte ROTTA: {v1} (il dato puo' esistere: e' la fonte che non risponde)", '{v0} CANNOT BE DETERMINED, BROKEN source: {v1} (the data may exist: the source is not responding)', v0=dominio, v1=motivo))
    return _costruisci(dominio, None, "nessuna", motivo, frase)


# --------------------------------------------------------------------------
# 2. Il negozio dei veicoli
# --------------------------------------------------------------------------

class _NegozioIlleggibile(Exception):
    pass


def _senza_doppie(coppie):
    chiavi = [k for k, _ in coppie]
    doppie = sorted({k for k in chiavi if chiavi.count(k) > 1})
    if doppie:
        raise _NegozioIlleggibile(_message("chiave doppia nel JSON: {v0!r} (json.load terrebbe l'ultima in silenzio)", 'Duplicate JSON key: {v0!r} (json.load would silently retain the last one)', v0=doppie))
    return dict(coppie)


def _field_description(field, original):
    descriptions = {
        "tipo": ("uno di {allowed}", "one of {allowed}", TIPI),
        "provenienza": ("uno di {allowed}", "one of {allowed}", PROVENIENZE),
        "classe_size": ("uno di {allowed} o null", "one of {allowed} or null", CLASSI_SIZE),
    }
    if field in descriptions:
        italian, english, allowed = descriptions[field]
        return _message(italian, english, allowed=allowed)
    english = {
        "sottostante": "string or null",
        "kind": "declared DAT-engine kind, consistent with the underlying, or null",
        "nav_fonte": "string or null (fetcher key in cef_nav.FETCH_NAV)",
        "nav_valuta": "string or null (NAV currency)",
        "valuta": "3-letter uppercase quote currency or null; GBX distinct from GBP",
        "nome": "string or null (company name, for messages)",
        "settore_tema": "string or null (by_sector label)",
        "bucket_economico": "string or null",
        "settore_policy": "string or null (sizing sector cap)",
        "profilo_valutazione": "string or null (sector_taxonomy key)",
        "verificato_il": "YYYY-MM-DD date or null",
        "note": "string",
    }
    return _message(original, english[field])


def _valida_voce(chiave: str, voce: Any) -> Dict[str, Any]:
    if not isinstance(voce, dict):
        raise _NegozioIlleggibile(_message('voce {v0!r} malformata: serve un oggetto JSON, non {v1}', 'Malformed entry {v0!r}: expected a JSON object, not {v1}', v0=chiave, v1=type(voce).__name__))
    ignoti = sorted(k for k in voce if k not in CAMPI_VOCE)
    if ignoti:
        raise _NegozioIlleggibile(_message('voce {v0!r} con campo sconosciuto {v1!r}: i campi ammessi sono {v2}', 'Entry {v0!r} has unknown field {v1!r}: allowed fields are {v2}', v0=chiave, v1=ignoti[0], v2=sorted(CAMPI_VOCE)))
    pulita: Dict[str, Any] = {}
    for campo, (obbligatorio, valido, descrizione) in CAMPI_VOCE.items():
        if campo not in voce:
            if obbligatorio:
                raise _NegozioIlleggibile(_message('voce {v0!r} senza il campo obbligatorio {v1!r} ({v2})', 'Entry {v0!r} missing required field {v1!r} ({v2})', v0=chiave, v1=campo, v2=_field_description(campo, descrizione)))
            pulita[campo] = "" if campo == "note" else None
            continue
        if not valido(voce[campo]):
            raise _NegozioIlleggibile(_message('voce {v0!r}: campo {v1!r} = {v2!r} non valido ({v3})', 'Entry {v0!r}: invalid field {v1!r} = {v2!r} ({v3})', v0=chiave, v1=campo, v2=voce[campo], v3=_field_description(campo, descrizione)))
        pulita[campo] = voce[campo]
    if pulita["note"] is None:
        pulita["note"] = ""
    pulita["sottostante"] = {"BTC": "BTC-USD", "HYPE-USD": "HYPE"}.get(
        pulita["sottostante"], pulita["sottostante"])
    kind = pulita["kind"]
    if kind is not None and (pulita["tipo"] != "dat" or
                             pulita["sottostante"] != SOTTOSTANTI_KIND_DAT[kind]):
        raise _NegozioIlleggibile(_message('voce {v0!r}: kind {v1!r} incoerente con tipo {v2!r} o sottostante {v3!r} (richiede tipo dat e sottostante {v4})', 'Entry {v0!r}: kind {v1!r} inconsistent with type {v2!r} or underlying {v3!r} (requires dat type and underlying {v4})', v0=chiave, v1=kind, v2=pulita["tipo"], v3=pulita["sottostante"], v4=SOTTOSTANTI_KIND_DAT[kind]))
    return pulita


def carica_veicoli(path: Optional[str] = None) -> Dict[str, Any]:
    """Legge il negozio privato dei veicoli.
    Torna {"veicoli": {TICKER: voce}, "origine": path | 'assente' | 'illeggibile',
    "motivo": str | None}. Ogni voce esce con TUTTI i campi di CAMPI_VOCE (i facoltativi
    assenti valgono None, `note` ""): il consumatore non fa `.get` con un default. Una sola
    voce malformata rende illeggibile il negozio INTERO."""
    p = path or PERCORSO_VEICOLI
    if not os.path.exists(p):
        return {"veicoli": {}, "origine": "assente",
                "motivo": (_message('negozio non trovato: {v0} (copia {v1} in data/ e dichiaraci i TUOI veicoli: un simbolo assente vale «sconosciuto», mai «operating»)', 'Store not found: {v0} (copy {v1} into data/ and declare YOUR vehicles: an absent symbol is unknown, never operating)', v0=p, v1=os.path.basename(ESEMPIO_VEICOLI)))}
    try:
        with open(p, encoding="utf-8") as fh:
            grezzo = json.load(fh, object_pairs_hook=_senza_doppie)
    except _NegozioIlleggibile as e:
        return {"veicoli": {}, "origine": "illeggibile", "motivo": error_text(e)}
    except Exception as e:
        return {"veicoli": {}, "origine": "illeggibile",
                "motivo": _message('{v0}: {v1}', '{v0}: {v1}', v0=type(e).__name__, v1=error_text(e))}
    if not isinstance(grezzo, dict):
        return {"veicoli": {}, "origine": "illeggibile",
                "motivo": _message("il negozio non e' un oggetto JSON ma {v0}", 'Store is not a JSON object but {v0}', v0=type(grezzo).__name__)}
    veicoli: Dict[str, Dict[str, Any]] = {}
    try:
        for k, v in grezzo.items():
            if k.startswith("_"):
                continue
            kk = k.strip().upper()
            if kk != k or not kk or kk in veicoli:
                # ogni lookup fa .upper() sul ticker: una chiave minuscola o con spazi non
                # verrebbe MAI trovata e il log direbbe «assente», vero per il codice e falso
                # per chi ha appena scritto la voce (review 04/09 sul negozio delle news)
                raise _NegozioIlleggibile(_message('chiave {v0!r} non canonica o doppia: scrivila MAIUSCOLA, senza spazi e una volta sola ({v1!r})', 'Noncanonical or duplicate key {v0!r}: write it UPPERCASE, without spaces, once only ({v1!r})', v0=k, v1=kk))
            veicoli[k] = _valida_voce(k, v)
    except _NegozioIlleggibile as e:
        return {"veicoli": {}, "origine": "illeggibile", "motivo": error_text(e)}
    return {"veicoli": veicoli, "origine": p, "motivo": None}


def veicoli_correnti() -> Dict[str, Any]:
    """Il negozio riletto a ogni chiamata (come fonti_guidance.fonti_correnti): una modifica a
    mano si vede senza riavviare. Torna l'ESITO intero, non solo le voci: chi lo consuma deve
    poter dichiarare origine e motivo."""
    return carica_veicoli()


def valida_negozio(negozio: Dict[str, Any]) -> Dict[str, Any]:
    """Validate an injected registry with the file loader's rules, without I/O.

    The envelope is the existing carica_veicoli contract. Missing and corrupt
    stores remain distinct; one invalid entry invalidates the whole store.
    """
    try:
        if not isinstance(negozio, dict) or not isinstance(negozio.get("origine"), str):
            raise ValueError("registry envelope requires origine")
        origin = negozio["origine"]
        entries = negozio.get("veicoli")
        if not origin or not isinstance(entries, dict):
            raise ValueError("registry envelope requires veicoli object")
        if origin in ("assente", "illeggibile"):
            if entries or not isinstance(negozio.get("motivo"), str) or not negozio["motivo"].strip():
                raise ValueError("unavailable registry requires empty entries and reason")
            return {"origine": origin, "veicoli": {}, "motivo": negozio["motivo"]}
        clean = {}
        for key, entry in entries.items():
            if not isinstance(key, str) or not key or key.strip().upper() != key:
                raise ValueError("noncanonical registry key")
            clean[key] = _valida_voce(key, entry)
        return {"origine": origin, "veicoli": clean, "motivo": None}
    except (ValueError, TypeError, _NegozioIlleggibile):
        # Do not expose other instruments from a private registry in a refusal.
        return {"origine": "illeggibile", "veicoli": {},
                "motivo": _message("struttura o voce del registro non valida; valida il registro dei veicoli", "registry envelope or entry invalid; validate the vehicle registry")}


def _negozio(negozio: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return negozio if negozio is not None else carica_veicoli()


def voce(ticker: str, negozio: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """La voce del simbolo (lookup canonico), o None se assente. Un negozio assente o
    illeggibile da' None per tutti: chi vuole distinguere i due casi legge `origine`."""
    n = _negozio(negozio)
    return n["veicoli"].get((ticker or "").strip().upper())


def veicoli_per_tipo(tipo: str, negozio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """La VISTA che sostituisce le liste cablate (DAT_TICKERS, CEF_SOURCES...): i simboli del
    negozio con quel tipo, ordinati, piu' origine e motivo del negozio — cosi' un consumatore
    che riceve una lista vuota sa se e' «nessun veicolo» o «negozio rotto»."""
    if tipo not in TIPI:
        raise ValueError(_message('tipo {v0!r} fuori vocabolario: {v1}', 'Type {v0!r} outside vocabulary: {v1}', v0=tipo, v1=TIPI))
    n = _negozio(negozio)
    return {"tickers": sorted(t for t, v in n["veicoli"].items() if v["tipo"] == tipo),
            "origine": n["origine"], "motivo": n["motivo"]}


def classe_size_di(ticker: str, negozio: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """`veicolo` | `single` | None (voce assente o senza classe dichiarata)."""
    v = voce(ticker, negozio)
    return v["classe_size"] if v else None


# --------------------------------------------------------------------------
# 3. Il registro dei classificatori
# --------------------------------------------------------------------------

CLASSIFICATORI: Dict[str, Callable[..., Etichetta]] = {}


def registra(dominio: str):
    """Decoratore: registra un classificatore per un dominio del vocabolario. Il test del
    contratto lo chiama sull'input ignoto e pretende `sconosciuto`."""
    if dominio not in DOMINI:
        raise ValueError(_message('dominio {v0!r} fuori vocabolario: {v1}', 'Domain {v0!r} outside vocabulary: {v1}', v0=dominio, v1=DOMINI))

    def _dec(fn):
        CLASSIFICATORI[dominio] = fn
        return fn
    return _dec


@registra("natura")
def natura(ticker: str, negozio: Optional[Dict[str, Any]] = None, **_ignorati) -> Etichetta:
    """Il TIPO di veicolo di un simbolo, dal negozio. La provenienza nasce col valore:
    dichiarato -> registro_pm (dichiarata_pm); derivato:quote_type -> quote_type (misurata).
    Negozio assente/illeggibile -> `guasto` (fonte ROTTA); simbolo assente o voce che dichiara
    tipo sconosciuto -> `sconosciuto`. MAI `operating` per ripiego."""
    n = _negozio(negozio)
    t = (ticker or "").strip().upper()
    if n["origine"] in ("assente", "illeggibile"):
        return guasto("natura", _message('negozio dei veicoli {v0}: {v1}', 'Vehicle store {v0}: {v1}', v0=n["origine"], v1=n["motivo"]))
    v = n["veicoli"].get(t)
    if v is None:
        return sconosciuto("natura", _message('simbolo {v0} assente dal negozio dei veicoli ({v1})', 'Symbol {v0} absent from vehicle store ({v1})', v0=t or "<vuoto>", v1=os.path.basename(n["origine"])))
    if v["tipo"] == "sconosciuto" or v["provenienza"] == "sconosciuto":
        return sconosciuto("natura", _message('la voce di {v0} nel negozio dichiara tipo {v1!r} con provenienza {v2!r}', 'Store entry for {v0} declares type {v1!r} with provenance {v2!r}', v0=t, v1=v["tipo"], v2=v["provenienza"]))
    return etichetta("natura", v["tipo"], _FONTE_DA_PROVENIENZA[v["provenienza"]],
                     _message('voce {v0} del negozio dei veicoli ({v1}, provenienza {v2})', 'Entry {v0} in vehicle store ({v1}, provenance {v2})', v0=t, v1=os.path.basename(n["origine"]), v2=v["provenienza"]),
                     verificato_il=v["verificato_il"])


@registra("classe_size")
def classe_size(ticker: str, negozio: Optional[Dict[str, Any]] = None, **_ignorati) -> Etichetta:
    """La classe per i limiti del sizing (`veicolo` | `single`), dal negozio: e' una decisione
    di POLICY del PM, quindi la fonte e' sempre `registro_pm`. Voce assente o senza classe ->
    `sconosciuto`; negozio rotto -> `guasto`. Il valore da APPLICARE in assenza (il limite
    prudente single-stock) lo sceglie e lo etichetta il consumatore con `ripiego()`: un
    classificatore non inventa mai un valore."""
    n = _negozio(negozio)
    t = (ticker or "").strip().upper()
    if n["origine"] in ("assente", "illeggibile"):
        return guasto("classe_size", _message('negozio dei veicoli {v0}: {v1}', 'Vehicle store {v0}: {v1}', v0=n["origine"], v1=n["motivo"]))
    v = n["veicoli"].get(t)
    if v is None:
        return sconosciuto("classe_size", _message('simbolo {v0} assente dal negozio dei veicoli ({v1})', 'Symbol {v0} absent from vehicle store ({v1})', v0=t or "<vuoto>", v1=os.path.basename(n["origine"])))
    if v["classe_size"] is None:
        return sconosciuto("classe_size", _message('la voce di {v0} nel negozio non dichiara la classe di sizing (campo classe_size assente)', 'Store entry for {v0} does not declare sizing class (classe_size field missing)', v0=t))
    return etichetta("classe_size", v["classe_size"], "registro_pm",
                     _message('voce {v0} del negozio dei veicoli ({v1}): classe_size dichiarata', 'Entry {v0} in vehicle store ({v1}): classe_size declared', v0=t, v1=os.path.basename(n["origine"])), verificato_il=v["verificato_il"])


_VALUTE_DA_SUFFISSO = {
    "L": "GBX", "MI": "EUR", "AS": "EUR", "DE": "EUR", "FRA": "EUR", "F": "EUR",
    "PA": "EUR", "VI": "EUR", "HE": "EUR", "AT": "EUR", "SW": "CHF", "HK": "HKD",
    "T": "JPY", "TO": "CAD",
}


@registra("valuta")
def valuta(ticker: str, negozio: Optional[Dict[str, Any]] = None,
           valuta_posizione: Optional[str] = None, **_ignorati) -> Etichetta:
    """Valuta di QUOTAZIONE, mai nav_valuta. Posizione > negozio > suffisso dichiarato.

    Una fonte esplicita malformata non autorizza a indovinare. Il negozio assente consente
    il solo ripiego noto per suffisso; quello illeggibile potrebbe contenere un override
    diverso, quindi senza valuta della posizione il risultato resta non determinabile.
    Il ticker senza suffisso non identifica un mercato e non implica USD.
    """
    if valuta_posizione is not None:
        if not _valuta_o_none(valuta_posizione):
            return guasto("valuta", _message('valuta della posizione non valida: {v0!r}', 'Invalid position currency: {v0!r}', v0=valuta_posizione))
        return etichetta("valuta", valuta_posizione, "misura_dato", _message('valuta esplicita della posizione', 'Explicit position currency'))
    n = _negozio(negozio)
    if n["origine"] == "illeggibile":
        return guasto("valuta", _message('negozio dei veicoli illeggibile: {v0}', 'Unreadable vehicle store: {v0}', v0=n["motivo"]))
    t = (ticker or "").strip().upper()
    v = voce(t, n)
    if v is not None and v.get("valuta") is not None:
        return etichetta("valuta", v["valuta"], "registro_pm",
                         _message('valuta di quotazione dichiarata per {v0} nel negozio dei veicoli', 'Quote currency declared for {v0} in the vehicle store', v0=t),
                         verificato_il=v.get("verificato_il"))
    suffisso = t.rsplit(".", 1)[1] if "." in t else None
    if suffisso in _VALUTE_DA_SUFFISSO:
        return ripiego("valuta", _VALUTE_DA_SUFFISSO[suffisso],
                       _message('valuta esplicita assente; inferita dal suffisso .{v0}, non verificata; negozio {v1}', 'Explicit currency absent; inferred from suffix .{v0}, not verified; store {v1}', v0=suffisso, v1=n["origine"]))
    return sconosciuto("valuta", _message('valuta n.d. per {v0}: ticker ambiguo senza valuta esplicita; negozio {v1}', 'Currency unavailable for {v0}: ambiguous ticker without explicit currency; store {v1}', v0=t or "<vuoto>", v1=n["origine"]))


# quoteType Yahoo -> natura misurata. INDEX e CURRENCY non sono strumenti detenibili: natura
# ignota anche se il profilo di valutazione li tratta come «non valutabili a DCF».
_NATURA_DA_QUOTE_TYPE = {"ETF": "etf", "MUTUALFUND": "etf", "MONEYMARKET": "etf",
                         "CRYPTOCURRENCY": "crypto", "FUTURE": "commodity", "EQUITY": "operating"}
QUOTE_TYPES_PANIERE = ("ETF", "MUTUALFUND", "MONEYMARKET", "INDEX", "CURRENCY",
                       "CRYPTOCURRENCY", "FUTURE")


def natura_da_dato(quote_type: str = "", industry: str = "", sector: str = "") -> Etichetta:
    """La natura dai DATI di Yahoo, senza negozio: quoteType misurato; se manca, industry o
    sector presenti fanno dedurre «operating» (dedotta, non misurata); niente di niente e'
    SCONOSCIUTO — mai operating per ripiego."""
    qt = (quote_type or "").strip().upper()
    if qt in _NATURA_DA_QUOTE_TYPE:
        return etichetta("natura", _NATURA_DA_QUOTE_TYPE[qt], "quote_type", "quoteType=%s" % qt)
    if qt in QUOTE_TYPES_PANIERE:
        return sconosciuto("natura", _message("quoteType={v0}: non e' uno strumento detenibile in portafoglio", 'quoteType={v0}: instrument cannot be held in the portfolio', v0=qt))
    if (industry or "").strip() or (sector or "").strip():
        return etichetta("natura", "operating", "euristica_simbolo",
                         _message("quoteType assente, ma industry {v0!r} / sector {v1!r} presenti: dedotta societa' operativa, non misurata", 'quoteType missing, but industry {v0!r} / sector {v1!r} present: operating company inferred, not measured', v0=industry or "", v1=sector or ""))
    return sconosciuto("natura", _message('quoteType assente, industry e sector vuoti: nessuna fonte dice che strumento sia', 'quoteType missing, industry and sector empty: no source identifies the instrument'))


def natura_risolta(ticker: str, quote_type: str = "", industry: str = "", sector: str = "",
                   negozio: Optional[Dict[str, Any]] = None) -> Etichetta:
    """La natura che i motori usano: il livello DICHIARATO (negozio) vince sul livello
    MISURATO (quoteType/industry); una voce sconosciuta o assente cede al dato; un negozio
    ROTTO resta rotto (la voce del PM potrebbe esistere e dire «veicolo»: il dato da solo e'
    esattamente cio' che ha mandato un fondo chiuso al DCF)."""
    n = _negozio(negozio)
    ns = natura(ticker, n)
    if ns.valore is not None:
        return ns
    if n["origine"] == "illeggibile":
        return ns   # guasto: dichiarato, non sovrascritto dal dato
    nd = natura_da_dato(quote_type, industry, sector)
    if n["origine"] != "assente":
        return nd
    # negozio ASSENTE (clone senza il file): decide il solo dato, ma l'etichetta lo DICE —
    # un veicolo non dichiarato passa per societa' e il payload deve poterlo dire (review
    # 05/09: prima l'assenza non lasciava traccia)
    ev = _message("{evidence} | negozio dei veicoli ASSENTE: decide il solo dato (un veicolo non dichiarato puo' passare per societa' operativa)", "{evidence} | Vehicle store MISSING: classification uses data alone (an undeclared vehicle may be classified as an operating company)", evidence=nd.evidenza)
    if nd.valore is None:
        return sconosciuto("natura", ev)
    return etichetta("natura", nd.valore, nd.fonte, ev)
