# -*- coding: utf-8 -*-
"""Archivio privato dei record documentati dei metodi di valutazione.

DECISIONE PM 1A (13/09, Claude Opus 5): i record documentati di un titolo (per esempio i 61
record dell'operating FCFF) li prepara Claude dai documenti ufficiali, il PM li rivede, e il
comitato legge a ogni run SOLO i set che il PM ha approvato entro il cutoff della run.

Due tabelle APPEND-ONLY nel DB privato, con trigger come `valuation_snapshots`:
- `method_record_sets`: un set immutabile (record, motivazioni bear/base/bull, provenienza,
  chi e quando l'ha preparato, impronta sha256 del JSON canonico dei record);
- `method_record_reviews`: le decisioni del PM (approvato / respinto / ritirato), una riga
  per decisione, mai modificate.

Regola del punto nel tempo (confronto sul GIORNO ISO, granularita' dichiarata):
per ogni metodo del titolo vale il set piu' recente (giorno di preparazione, poi id) fra
quelli preparati entro il cutoff la cui ULTIMA revisione entro il cutoff e' `approvato`.
Una revisione o un set datati dopo il cutoff non esistono per chi guarda a quel cutoff.
Conseguenza da sapere: ritirare il set nuovo fa tornare in uso il precedente approvato e
mai ritirato (per spegnere tutto si ritirano entrambi).

Il punto nel tempo vale sulle date DICHIARATE (`prepared_at`, `reviewed_at` scritti da chi
prepara e da chi rivede), non sull'istante in cui la riga entra nel DB: una data dichiarata
nel passato cambia cio' che un cutoff passato vede. Lo store rifiuta solo le revisioni
anteriori alla preparazione o all'ultima revisione dello stesso set; la CLI
`tools/method_records/archivio.py` rifiuta in piu' ogni data anteriore al giorno
dell'orologio della macchina. Chi scrive fuori dalla CLI risponde delle date che dichiara.
Per la stessa regola un set approvato DOPO un altro ma dichiarato preparato PRIMA non lo
scavalca; a parita' di giorno di preparazione vince l'id maggiore (il set archiviato dopo).

Scritture: `proponi_set` e `rivedi_set` rispettano la transazione del chiamante (se ce n'e'
una aperta non confermano); senza transazione aperta confermano da sole. Nessuna delle due
crea tabelle: un DB esistente riceve l'archivio solo da
`tools/migrations/migra_method_records.py`, un DB nuovo da `MemoryDB` all'avvio.

Nessun import pesante a livello di modulo: `memory_db` si importa solo dentro
`apri_lettura`, e per ATTRIBUTO a ogni chiamata, cosi' il tripwire dei test lo copre.
"""
import hashlib
import json
import os
import re
from datetime import date, datetime
from urllib.parse import urlparse

TABELLE = ("method_record_sets", "method_record_reviews")
DECISIONI = ("approvato", "respinto", "ritirato")
SCENARI_MOTIVATI = ("bear", "base", "bull")
TRIGGER = tuple(f"{tabella}_{operazione}_immutable"
                for tabella in TABELLE for operazione in ("insert", "update", "delete"))

COMANDO_MIGRAZIONE = "python tools/migrations/migra_method_records.py (dry-run), poi --apply"


class ArchivioNonMigrato(RuntimeError):
    """Il DB non ha le tabelle (o i trigger) dell'archivio: va migrato, non creato di nascosto."""


class SetAlterato(RuntimeError):
    """Un set archiviato non corrisponde piu' alla sua impronta: nessun record va usato."""


class SetNonValido(ValueError):
    """Set o revisione fuori regola: rifiutati PRIMA di scrivere."""


STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS method_record_sets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL CHECK (ticker <> '' AND ticker = upper(ticker)),
        method_id TEXT NOT NULL,
        method_version TEXT NOT NULL,
        records_json TEXT NOT NULL,
        scenario_rationale_json TEXT NOT NULL,
        provenance TEXT NOT NULL,
        prepared_by TEXT NOT NULL,
        prepared_at TEXT NOT NULL,
        records_sha256 TEXT NOT NULL UNIQUE,
        n_records INTEGER NOT NULL,
        earliest_valid_until TEXT NOT NULL,
        latest_as_of TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_method_record_sets_ticker "
    "ON method_record_sets(ticker, method_id, prepared_at)",
    """CREATE TABLE IF NOT EXISTS method_record_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        set_id INTEGER NOT NULL REFERENCES method_record_sets(id),
        decision TEXT NOT NULL CHECK (decision IN ('approvato','respinto','ritirato')),
        reviewer TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        note TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_method_record_reviews_set ON method_record_reviews(set_id, id)",
]
# UPDATE e DELETE come valuation_snapshots. In piu' INSERT: `INSERT OR REPLACE` su un id (o
# un'impronta) gia' presente cancella e riscrive la riga SENZA far scattare il trigger di
# DELETE (recursive_triggers e' spento di default) - misurato il 13/09 su sqlite3 in memoria.
for _tabella in TABELLE:
    _chiave = "id = NEW.id OR records_sha256 = NEW.records_sha256" if _tabella == "method_record_sets" \
        else "id = NEW.id"
    STATEMENTS.append(
        f"CREATE TRIGGER IF NOT EXISTS {_tabella}_insert_immutable BEFORE INSERT ON {_tabella} "
        f"WHEN EXISTS (SELECT 1 FROM {_tabella} WHERE {_chiave}) "
        "BEGIN SELECT RAISE(ABORT, 'method record archive is immutable: row already present'); END")
    for _operazione in ("UPDATE", "DELETE"):
        STATEMENTS.append(
            f"CREATE TRIGGER IF NOT EXISTS {_tabella}_{_operazione.lower()}_immutable "
            f"BEFORE {_operazione} ON {_tabella} "
            "BEGIN SELECT RAISE(ABORT, 'method record archive is immutable'); END")


def crea_tabelle(conn):
    """Esegue gli STATEMENTS (idempotenti). Non conferma: la transazione e' del chiamante."""
    for statement in STATEMENTS:
        conn.execute(statement)


def canonico(records):
    """JSON canonico dei record: chiavi ordinate, separatori compatti, UTF-8 non escapato.
    `allow_nan=False`: un NaN/Infinity non e' JSON e non ha un'impronta stabile."""
    return json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _impronta(testo):
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- date e testi
_GIORNO = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISTANTE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?")


def _testo(valore):
    return isinstance(valore, str) and bool(valore.strip())


def _giorno_record(valore):
    """Data dei record: SOLO `YYYY-MM-DD`, come la pretende `dcf_quality._date` a valle."""
    if not isinstance(valore, str) or not _GIORNO.fullmatch(valore):
        return None
    try:
        return date.fromisoformat(valore)
    except ValueError:
        return None


def _giorno_istante(valore):
    """prepared_at / reviewed_at: data o data-ora ISO; restituisce il GIORNO scritto."""
    if not isinstance(valore, str) or not _ISTANTE.fullmatch(valore):
        return None
    try:
        datetime.fromisoformat(valore)
        return date.fromisoformat(valore[:10])
    except ValueError:
        return None


def _giorno_cutoff(cutoff):
    if isinstance(cutoff, datetime):
        return cutoff.date()
    if isinstance(cutoff, date):
        return cutoff
    giorno = _giorno_istante(cutoff)
    if giorno is None:
        raise ValueError(f"cutoff non ISO: {cutoff!r} (atteso YYYY-MM-DD, date o datetime)")
    return giorno


def _url(valore):
    try:
        parsed = urlparse(valore) if isinstance(valore, str) else None
        return bool(parsed and parsed.scheme in ("http", "https") and parsed.hostname)
    except ValueError:
        return False


def _ticker_letto(ticker):
    if not _testo(ticker) or ticker != ticker.strip():
        raise ValueError(f"ticker non valido: {ticker!r}")
    return ticker.upper()


# --------------------------------------------------------------------------- presenza archivio
def verifica_migrato(conn):
    presenti = {riga[0] for riga in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    mancanti = [nome for nome in (*TABELLE, *TRIGGER) if nome not in presenti]
    if mancanti:
        raise ArchivioNonMigrato(
            "archivio dei record documentati non migrato su questo DB: mancano "
            + ", ".join(mancanti) + ". Lanciare " + COMANDO_MIGRAZIONE)


def _conferma_se_propria(conn, propria):
    if propria and conn.in_transaction:
        conn.commit()


# --------------------------------------------------------------------------- scrittura
def _errori_record(records):
    if not isinstance(records, list) or not records:
        return ["records: lista vuota o non lista (serve almeno un record)"]
    errori = []
    for indice, record in enumerate(records):
        if not isinstance(record, dict):
            errori.append(f"record {indice}: non e' un oggetto JSON")
            continue
        nome = f"record {indice} (driver={record.get('driver')!r}, scenario={record.get('scenario')!r})"
        scade, fonte = _giorno_record(record.get("valid_until")), _giorno_record(record.get("as_of"))
        if scade is None:
            errori.append(f"{nome}: valid_until assente o non ISO YYYY-MM-DD")
        if fonte is None:
            errori.append(f"{nome}: as_of assente o non ISO YYYY-MM-DD")
        if scade and fonte and scade < fonte:
            errori.append(f"{nome}: valid_until {scade.isoformat()} precedente ad as_of {fonte.isoformat()}")
        kind = record.get("kind")
        if not _testo(kind):
            errori.append(f"{nome}: kind assente")
        elif kind.strip().lower() == "proxy":
            errori.append(f"{nome}: kind proxy non ammesso nell'archivio (serve il dato documentato)")
        if not _url(record.get("source_id")):
            errori.append(f"{nome}: source_id non e' un URL http/https: {record.get('source_id')!r}")
    return errori


def _errori_motivi(motivi):
    if not isinstance(motivi, dict) or set(motivi) != set(SCENARI_MOTIVATI) \
            or any(not _testo(v) for v in motivi.values()):
        return ["scenario_rationale: servono esattamente bear/base/bull, ciascuno con un testo non vuoto"]
    return []


def proponi_set(conn, *, ticker, method_id, method_version, records, scenario_rationale,
                provenance, prepared_by, prepared_at):
    """Archivia un set NUOVO (da rivedere). Rifiuta tutto con SetNonValido se anche un solo
    campo e' fuori regola, elencando ogni errore. Restituisce l'id del set."""
    verifica_migrato(conn)
    errori = []
    if not _testo(ticker) or ticker != ticker.strip():
        errori.append(f"ticker vuoto o con spazi ai bordi: {ticker!r}")
    for nome, valore in (("method_id", method_id), ("method_version", method_version),
                         ("provenance", provenance), ("prepared_by", prepared_by)):
        if not _testo(valore):
            errori.append(f"{nome}: testo non vuoto richiesto, ricevuto {valore!r}")
    if _giorno_istante(prepared_at) is None:
        errori.append(f"prepared_at non ISO: {prepared_at!r}")
    errori.extend(_errori_record(records))
    errori.extend(_errori_motivi(scenario_rationale))
    testo_record = None
    if not errori:
        try:
            testo_record = canonico(records)
        except (TypeError, ValueError) as exc:
            errori.append(f"records non rappresentabili in JSON canonico: {exc}")
    if errori:
        raise SetNonValido("set rifiutato, nulla scritto: " + "; ".join(errori))
    impronta = _impronta(testo_record)
    gia = conn.execute("SELECT id FROM method_record_sets WHERE records_sha256=?", (impronta,)).fetchone()
    if gia is not None:
        raise SetNonValido(f"set duplicato: stessi record del set {gia[0]} (sha256 {impronta}), nulla scritto")
    propria = not conn.in_transaction
    cursore = conn.execute(
        "INSERT INTO method_record_sets(ticker, method_id, method_version, records_json, "
        "scenario_rationale_json, provenance, prepared_by, prepared_at, records_sha256, n_records, "
        "earliest_valid_until, latest_as_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticker.upper(), method_id, method_version, testo_record,
         json.dumps(scenario_rationale, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
         provenance, prepared_by, prepared_at, impronta, len(records),
         min(_giorno_record(r["valid_until"]) for r in records).isoformat(),
         max(_giorno_record(r["as_of"]) for r in records).isoformat()))
    nuovo = cursore.lastrowid
    _conferma_se_propria(conn, propria)
    return nuovo


def rivedi_set(conn, *, set_id, decision, reviewer, reviewed_at, note=None):
    """Registra UNA decisione del PM su un set. Restituisce l'id della revisione.

    Rifiuta: set inesistente, decisione ignota, revisore vuoto, data non ISO, revisione datata
    prima della preparazione o prima dell'ultima revisione dello stesso set (retrodatarla
    cambierebbe cio' che una run passata ha visto), approvazione di un set gia' scaduto.
    Un'approvazione rilegge PRIMA il set intero con le verifiche della lettura: un set che non
    torna con la sua impronta o con le regole di scrittura solleva SetAlterato e nulla e'
    scritto (ritirarlo o respingerlo resta possibile: e' il modo di spegnerlo)."""
    verifica_migrato(conn)
    errori = []
    if decision not in DECISIONI:
        errori.append(f"decision {decision!r} non ammessa (ammesse: {', '.join(DECISIONI)})")
    if not _testo(reviewer):
        errori.append(f"reviewer: testo non vuoto richiesto, ricevuto {reviewer!r}")
    giorno = _giorno_istante(reviewed_at)
    if giorno is None:
        errori.append(f"reviewed_at non ISO: {reviewed_at!r}")
    if note is not None and not isinstance(note, str):
        errori.append("note: testo o None")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        errori.append(f"set_id intero richiesto, ricevuto {set_id!r}")
        riga = None
    else:
        riga = conn.execute("SELECT prepared_at, earliest_valid_until FROM method_record_sets WHERE id=?",
                            (set_id,)).fetchone()
        if riga is None:
            errori.append(f"set {set_id} inesistente")
    if not errori:
        preparato = _giorno_archiviato(riga[0], f"set {set_id}: prepared_at")
        if giorno < preparato:
            errori.append(f"revisione del {giorno.isoformat()} datata prima della preparazione "
                          f"del set ({preparato.isoformat()})")
        precedenti = [_giorno_archiviato(r[0], f"set {set_id}: reviewed_at") for r in conn.execute(
            "SELECT reviewed_at FROM method_record_reviews WHERE set_id=?", (set_id,))]
        if precedenti and giorno < max(precedenti):
            errori.append(f"revisione retrodatata al {giorno.isoformat()}: il set ha gia' una revisione "
                          f"del {max(precedenti).isoformat()}")
        scadenza = _giorno_archiviato(riga[1], f"set {set_id}: earliest_valid_until")
        if decision == "approvato" and scadenza < giorno:
            errori.append(f"approvazione rifiutata: il set ha record scaduti il {scadenza.isoformat()}, "
                          f"prima della revisione del {giorno.isoformat()}")
    if errori:
        raise SetNonValido("revisione rifiutata, nulla scritto: " + "; ".join(errori))
    if decision == "approvato":
        completa = conn.execute("SELECT " + ", ".join(_COLONNE_SET) + " FROM method_record_sets WHERE id=?",
                                (set_id,)).fetchone()
        _decodifica_verificata(dict(zip(_COLONNE_SET, tuple(completa))))
    propria = not conn.in_transaction
    cursore = conn.execute(
        "INSERT INTO method_record_reviews(set_id, decision, reviewer, reviewed_at, note) VALUES (?,?,?,?,?)",
        (set_id, decision, reviewer, reviewed_at, note))
    nuovo = cursore.lastrowid
    _conferma_se_propria(conn, propria)
    return nuovo


# --------------------------------------------------------------------------- lettura
_COLONNE_SET = ("id", "ticker", "method_id", "method_version", "records_json", "scenario_rationale_json",
                "provenance", "prepared_by", "prepared_at", "records_sha256", "n_records",
                "earliest_valid_until", "latest_as_of")


def _giorno_archiviato(valore, contesto):
    giorno = _giorno_istante(valore)
    if giorno is None:
        raise SetAlterato(f"{contesto} archiviato non ISO: {valore!r}")
    return giorno


def _set_del_ticker(conn, ticker):
    righe = conn.execute("SELECT " + ", ".join(_COLONNE_SET) + " FROM method_record_sets "
                         "WHERE ticker=? ORDER BY id", (ticker,)).fetchall()
    return [dict(zip(_COLONNE_SET, tuple(riga))) for riga in righe]


def _revisioni(conn, set_id, *, severa=True):
    """Le revisioni del set con il loro `_giorno`. `severa=False` (solo `storia_ticker`): una data
    archiviata non ISO lascia `_giorno` None invece di sollevare, e chi chiama la dichiara."""
    righe = conn.execute("SELECT id, decision, reviewer, reviewed_at, note FROM method_record_reviews "
                         "WHERE set_id=? ORDER BY id", (set_id,)).fetchall()
    revisioni = [dict(zip(("id", "decision", "reviewer", "reviewed_at", "note"), tuple(r))) for r in righe]
    for revisione in revisioni:
        revisione["_giorno"] = _giorno_archiviato(revisione["reviewed_at"], f"revisione {revisione['id']}: reviewed_at") \
            if severa else _giorno_istante(revisione["reviewed_at"])
    return revisioni


def _ultima_entro(revisioni, giorno):
    visibili = [r for r in revisioni if r["_giorno"] <= giorno]
    return max(visibili, key=lambda r: (r["_giorno"], r["id"])) if visibili else None


def _decodifica_verificata(riga):
    """Ricalcola impronta e colonne derivate dai record archiviati e rilancia le regole di
    scrittura su record E motivazioni (l'impronta copre solo i record); ogni differenza e' SetAlterato."""
    nome = f"set {riga['id']} ({riga['ticker']}/{riga['method_id']})"
    try:
        records = json.loads(riga["records_json"])
        motivi = json.loads(riga["scenario_rationale_json"])
        impronta = _impronta(canonico(records))
    except (TypeError, ValueError) as exc:
        raise SetAlterato(f"{nome}: record o motivazioni archiviati illeggibili: {exc}") from exc
    if impronta != riga["records_sha256"]:
        raise SetAlterato(f"{nome}: sha256 ricalcolato {impronta} diverso da quello archiviato "
                          f"{riga['records_sha256']}: nessun record va usato")
    fuori_regola = _errori_record(records) + _errori_motivi(motivi)
    if fuori_regola:
        raise SetAlterato(f"{nome}: record o motivazioni archiviati non piu' conformi alle regole di "
                          "scrittura: " + "; ".join(fuori_regola))
    derivati = {"n_records": len(records),
                "earliest_valid_until": min(_giorno_record(r["valid_until"]) for r in records).isoformat(),
                "latest_as_of": max(_giorno_record(r["as_of"]) for r in records).isoformat()}
    diversi = [k for k, v in derivati.items() if riga[k] != v]
    if diversi:
        raise SetAlterato(f"{nome}: colonne derivate non coerenti coi record: {', '.join(diversi)}")
    return records, motivi


def leggi_approvati(conn, ticker, cutoff):
    """Per ogni metodo del titolo, il set in uso al cutoff (v. docstring del modulo).
    Solleva SetAlterato se un set restituito non corrisponde alla sua impronta."""
    verifica_migrato(conn)
    giorno = _giorno_cutoff(cutoff)
    scelti = {}
    for riga in _set_del_ticker(conn, _ticker_letto(ticker)):
        preparato = _giorno_archiviato(riga["prepared_at"], f"set {riga['id']}: prepared_at")
        if preparato > giorno:
            continue
        ultima = _ultima_entro(_revisioni(conn, riga["id"]), giorno)
        if ultima is None or ultima["decision"] != "approvato":
            continue
        chiave = (preparato, riga["id"])
        corrente = scelti.get(riga["method_id"])
        if corrente is None or chiave > corrente[0]:
            scelti[riga["method_id"]] = (chiave, riga, ultima)
    risultato = []
    for metodo in sorted(scelti):
        _chiave, riga, ultima = scelti[metodo]
        records, motivi = _decodifica_verificata(riga)
        risultato.append({
            "id": riga["id"], "ticker": riga["ticker"], "method_id": riga["method_id"],
            "method_version": riga["method_version"], "records": records, "scenario_rationale": motivi,
            "provenance": riga["provenance"], "prepared_by": riga["prepared_by"],
            "prepared_at": riga["prepared_at"], "records_sha256": riga["records_sha256"],
            "earliest_valid_until": riga["earliest_valid_until"], "latest_as_of": riga["latest_as_of"],
            "review": {k: ultima[k] for k in ("id", "decision", "reviewer", "reviewed_at", "note")},
        })
    return risultato


def conta_da_rivedere(conn, ticker, cutoff):
    """Set preparati entro il cutoff senza nessuna revisione entro il cutoff."""
    verifica_migrato(conn)
    giorno = _giorno_cutoff(cutoff)
    return sum(1 for riga in _set_del_ticker(conn, _ticker_letto(ticker))
               if _giorno_archiviato(riga["prepared_at"], f"set {riga['id']}: prepared_at") <= giorno
               and _ultima_entro(_revisioni(conn, riga["id"]), giorno) is None)


def storia_ticker(conn, ticker, cutoff):
    """Tutti i set del titolo con TUTTE le revisioni, per la revisione del PM (CLI `elenca`).
    Non solleva su un set alterato: lo marca `integro: False` col motivo, set per set. Una data
    archiviata non ISO (preparazione o revisione) da' lo stato `alterato` e `visibile_al` None
    per la revisione che la porta: la regola del punto nel tempo non e' applicabile."""
    verifica_migrato(conn)
    giorno = _giorno_cutoff(cutoff)
    storia = []
    for riga in _set_del_ticker(conn, _ticker_letto(ticker)):
        revisioni = _revisioni(conn, riga["id"], severa=False)
        preparato = _giorno_istante(riga["prepared_at"])
        difetti = [f"set {riga['id']}: prepared_at archiviato non ISO: {riga['prepared_at']!r}"] \
            if preparato is None else []
        difetti += [f"revisione {r['id']}: reviewed_at archiviato non ISO: {r['reviewed_at']!r}"
                    for r in revisioni if r["_giorno"] is None]
        if difetti:
            stato = "alterato"
        elif preparato > giorno:
            stato = "preparato dopo la data"
        else:
            ultima = _ultima_entro(revisioni, giorno)
            stato = ultima["decision"] if ultima else "da rivedere"
        try:
            records, motivi = _decodifica_verificata(riga)
        except SetAlterato as exc:
            records, motivi = None, None
            difetti.append(str(exc))
        integro, motivo = not difetti, "; ".join(difetti) or None
        storia.append({
            "id": riga["id"], "method_id": riga["method_id"], "method_version": riga["method_version"],
            "provenance": riga["provenance"], "prepared_by": riga["prepared_by"],
            "prepared_at": riga["prepared_at"], "records_sha256": riga["records_sha256"],
            "n_records": riga["n_records"], "earliest_valid_until": riga["earliest_valid_until"],
            "latest_as_of": riga["latest_as_of"], "stato_al": stato, "integro": integro,
            "motivo_non_integro": motivo, "records": records, "scenario_rationale": motivi,
            "revisioni": [{**{k: r[k] for k in ("id", "decision", "reviewer", "reviewed_at", "note")},
                           "visibile_al": None if r["_giorno"] is None else r["_giorno"] <= giorno}
                          for r in revisioni],
        })
    return storia


def apri_lettura(db_path):
    """Connessione in sola lettura LOGICA (`PRAGMA query_only=ON`) a un DB che ESISTE GIA'.

    Mai creato: file assente -> FileNotFoundError prima di ogni apertura. Si apre con
    `memory_db.connect_sqlite` letto per attributo a ogni chiamata e col PATH NUDO: e' la sola
    forma che il tripwire dei test riconosce (un URI `?mode=ro` gli scivola sotto).
    Limite dichiarato: `connect_sqlite` imposta `journal_mode=WAL` alla prima apertura del
    percorso nel processo; su un DB non ancora in WAL e' una scrittura d'intestazione."""
    percorso = os.fspath(db_path)
    if not os.path.isfile(percorso):
        raise FileNotFoundError(f"archivio dei record non letto: DB assente al percorso {percorso} "
                                "(non viene creato)")
    from bellomberg.storage import memory_db
    conn = memory_db.connect_sqlite(percorso)
    try:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise RuntimeError("PRAGMA query_only non attivo: connessione chiusa senza leggere")
    except BaseException:
        conn.close()
        raise
    return conn
