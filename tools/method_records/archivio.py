# -*- coding: utf-8 -*-
"""CLI dell'archivio privato dei record documentati (decisione PM 1A, 13/09, Claude Opus 5).

I set li PREPARA Claude dai documenti ufficiali (dossier JSON), il PM li RIVEDE. Ogni comando
che scrive gira prima su una COPIA del DB in tmp (dry-run, default) e scrive davvero solo con
--apply: porta 8765 libera, backup del DB accanto al file, scrittura in una transazione che
controlla di aggiungere UNA riga e basta, rilettura.

    python tools/method_records/archivio.py proponi --file dossier.json --preparato-da "Claude Opus 5" [--preparato-il 2026-09-13] [--apply]
    python tools/method_records/archivio.py elenca --ticker SYNTH-ARC [--al 2026-09-13]
    python tools/method_records/archivio.py rivedi --set-id 1 --decisione approvato --revisore PM [--nota "..."] [--revisto-il ...] [--apply]

Dossier: {ticker, method_id, method_version, records, scenario_rationale, provenance} e basta:
chi e quando lo ha preparato arrivano dagli argomenti. Senza --preparato-il / --revisto-il la data
e' l'orologio della macchina, e l'uscita lo dichiara (`origine_data`). Il punto nel tempo vale
sulle date DICHIARATE: --preparato-il / --revisto-il anteriori al giorno dell'orologio della
macchina sono rifiutati prima di aprire il DB (cambierebbero cio' che un cutoff passato ha visto).
Uscita: JSON su stdout; codice 0 riuscito, 2 rifiutato (nulla scritto nel DB), 1 scrittura
APPLICATA (COMMIT fatto, backup preso) ma rilettura non confermata o fallita: non rilanciare il
comando, controllare con `elenca`.
"""
import argparse
from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bellomberg.storage import method_records_store as store
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_method_records as _migrazione

CAMPI_DOSSIER = ("ticker", "method_id", "method_version", "records", "scenario_rationale", "provenance")
OROLOGIO = "orologio della macchina"


class DossierNonValido(ValueError):
    pass


class DataRetrodatata(ValueError):
    """--preparato-il / --revisto-il anteriori al giorno dell'orologio della macchina."""


def backend_alive():
    return _migrazione.backend_alive()


# --------------------------------------------------------------------------- dossier
def _senza_doppie(coppie):
    visti = {}
    for chiave, valore in coppie:
        if chiave in visti:
            raise DossierNonValido(f"chiave doppia nel dossier: {chiave!r} (una delle due andrebbe persa in silenzio)")
        visti[chiave] = valore
    return visti


def leggi_dossier(percorso):
    try:
        with open(percorso, encoding="utf-8-sig") as file:
            dossier = json.load(file, object_pairs_hook=_senza_doppie)
    except json.JSONDecodeError as exc:
        raise DossierNonValido(f"dossier non JSON: {exc}") from exc
    if not isinstance(dossier, dict):
        raise DossierNonValido("il dossier deve essere un oggetto JSON")
    in_piu = sorted(set(dossier) - set(CAMPI_DOSSIER))
    mancanti = sorted(set(CAMPI_DOSSIER) - set(dossier))
    if in_piu or mancanti:
        parti = []
        if in_piu:
            parti.append(f"campi non previsti {in_piu} (prepared_by e prepared_at arrivano dagli argomenti)")
        if mancanti:
            parti.append(f"campi mancanti {mancanti}")
        raise DossierNonValido("dossier rifiutato: " + "; ".join(parti))
    return dossier


def _adesso():
    return datetime.now().isoformat(timespec="seconds")


def _oggi():
    return date.today()


def _rifiuta_se_retrodatata(valore, opzione):
    """Una data dichiarata anteriore a oggi riscriverebbe cio' che una run passata ha visto:
    rifiutata prima di aprire il DB. Una data non ISO passa oltre e la rifiuta lo store."""
    giorno, oggi = store._giorno_istante(valore), _oggi()
    if giorno is not None and giorno < oggi:
        raise DataRetrodatata(
            f"{opzione} {valore} anteriore a oggi ({oggi.isoformat()}, {OROLOGIO}): il punto nel tempo vale "
            "sulle date dichiarate e una data passata cambierebbe cio' che un cutoff passato ha visto; nulla scritto")


# --------------------------------------------------------------------------- scrittura in due tempi
def _copia_verificata(conn, destinazione, prima, schema):
    with closing(sqlite3.connect(destinazione)) as copia:
        conn.backup(copia)
        if fingerprint(copia) != prima or schema_fingerprint(copia) != schema:
            raise ValueError(f"copia {destinazione} diversa dallo snapshot di preflight")


def _sequenze_altrui(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        return []
    return conn.execute("SELECT name, seq FROM sqlite_sequence WHERE name NOT IN (?, ?) ORDER BY name",
                        store.TABELLE).fetchall()


def _applica_operazione(percorso, operazione, prima, schema):
    contatore = _migrazione.Statements()
    with closing(_migrazione.apri(percorso, sola_lettura=False)) as conn:
        conn.set_trace_callback(contatore.trace)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if fingerprint(conn) != prima or schema_fingerprint(conn) != schema:
                raise ValueError("DB variato dopo il preflight: nulla scritto")
            sequenze = _sequenze_altrui(conn)
            risultato = operazione(conn)
            if not conn.in_transaction:
                raise RuntimeError("l'operazione ha chiuso la transazione: controllo impossibile")
            dopo = fingerprint(conn)
            toccabili = set(store.TABELLE) | {"sqlite_sequence"}
            if {k: v for k, v in dopo.items() if k not in toccabili} != \
                    {k: v for k, v in prima.items() if k not in toccabili} \
                    or schema_fingerprint(conn) != schema or _sequenze_altrui(conn) != sequenze:
                raise ValueError("tabelle fuori dall'archivio o schema variati: rollback")
            for tabella in store.TABELLE:
                attese = prima[tabella]["rows"] + (1 if tabella == risultato["tabella"] else 0)
                if dopo[tabella]["rows"] != attese:
                    raise ValueError(f"{tabella}: attese {attese} righe, trovate {dopo[tabella]['rows']}: rollback")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return {**risultato, "scritture": _migrazione.conteggio(contatore)}


def esegui_scrittura(db_path, operazione, rileggi, *, apply):
    """Preflight in sola lettura -> prova su copia in tmp -> (solo --apply) porta, backup, scrittura, rilettura."""
    percorso = _migrazione.richiedi_wal(db_path)
    contatore = _migrazione.Statements()
    with tempfile.TemporaryDirectory(prefix="bellomberg_archivio_record_", ignore_cleanup_errors=True) as lavoro:
        prova = Path(lavoro) / "prova.db"
        with closing(_migrazione.apri(percorso, sola_lettura=True)) as conn:
            conn.set_trace_callback(contatore.trace)
            store.verifica_migrato(conn)
            conn.execute("BEGIN")
            prima = fingerprint(conn)
            schema = schema_fingerprint(conn)
            _copia_verificata(conn, prova, prima, schema)
            conn.execute("ROLLBACK")
            cambi = conn.total_changes
        esito = {"esito": "ok", "modo": "apply" if apply else "dry-run", "db": str(percorso), "backup": None,
                 "scritture_sorgente": {"osservate": contatore.observed, "scritture": contatore.writes,
                                        "total_changes": cambi},
                 "prova": _applica_operazione(prova, operazione, prima, schema)}
    if not apply:
        return esito
    if backend_alive():
        raise RuntimeError("porta 8765 occupata: chiudere il backend prima di scrivere nell'archivio (nulla scritto)")
    backup = percorso.with_name(percorso.name + ".pre-archivio-record-"
                                + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
    with closing(_migrazione.apri(percorso, sola_lettura=True)) as conn:
        conn.execute("BEGIN")
        if fingerprint(conn) != prima or schema_fingerprint(conn) != schema:
            raise ValueError("DB variato fra la prova e il backup: nulla scritto")
        backup.touch(exist_ok=False)
        _copia_verificata(conn, backup, prima, schema)
        conn.execute("ROLLBACK")
    esito["backup"] = str(backup)
    if backend_alive():
        raise RuntimeError("porta 8765 occupata dopo il backup: nulla scritto")
    esito["applicazione"] = _applica_operazione(percorso, operazione, prima, schema)
    # Da qui la riga e' nel DB (COMMIT fatto) e il backup esiste: nessun errore puo' piu' uscire
    # come "rifiutato, nulla scritto". Un guasto della rilettura si dichiara come tale (codice 1).
    try:
        with closing(store.apri_lettura(percorso)) as conn:
            rilettura = rileggi(conn, esito["applicazione"])
        rilettura["stesso_esito_della_prova"] = all(
            esito["applicazione"].get(k) == v for k, v in esito["prova"].items() if k != "scritture")
    except Exception as exc:  # noqa: BLE001 - dopo il COMMIT ogni errore e' una rilettura non confermata
        rilettura = {"id": esito["applicazione"].get("id"), "confermato": False,
                     "errore": f"{type(exc).__name__}: {exc}"}
    esito["rilettura"] = rilettura
    return esito


# --------------------------------------------------------------------------- comandi
def proponi(db_path, file, preparato_da, preparato_il=None, *, apply=False):
    if preparato_il:
        _rifiuta_se_retrodatata(preparato_il, "--preparato-il")
    dossier = leggi_dossier(file)
    origine = "argomento" if preparato_il else OROLOGIO
    preparato_il = preparato_il or _adesso()

    def operazione(conn):
        nuovo = store.proponi_set(conn, ticker=dossier["ticker"], method_id=dossier["method_id"],
                                  method_version=dossier["method_version"], records=dossier["records"],
                                  scenario_rationale=dossier["scenario_rationale"],
                                  provenance=dossier["provenance"], prepared_by=preparato_da,
                                  prepared_at=preparato_il)
        riga = conn.execute("SELECT ticker, method_id, records_sha256, n_records, earliest_valid_until, "
                            "latest_as_of, prepared_at FROM method_record_sets WHERE id=?", (nuovo,)).fetchone()
        return {"tabella": "method_record_sets", "id": nuovo,
                **dict(zip(("ticker", "method_id", "records_sha256", "n_records", "earliest_valid_until",
                            "latest_as_of", "prepared_at"), tuple(riga)))}

    def rileggi(conn, applicato):
        riga = conn.execute("SELECT records_sha256 FROM method_record_sets WHERE id=?",
                            (applicato["id"],)).fetchone()
        [voce] = [s for s in store.storia_ticker(conn, applicato["ticker"], applicato["prepared_at"][:10])
                  if s["id"] == applicato["id"]] or [None]
        return {"id": applicato["id"],
                "confermato": riga is not None and riga[0] == applicato["records_sha256"]
                and voce is not None and voce["integro"]}

    esito = esegui_scrittura(db_path, operazione, rileggi, apply=apply)
    esito["origine_data"] = origine
    return esito


def rivedi(db_path, set_id, decisione, revisore, nota=None, revisto_il=None, *, apply=False):
    if revisto_il:
        _rifiuta_se_retrodatata(revisto_il, "--revisto-il")
    origine = "argomento" if revisto_il else OROLOGIO
    revisto_il = revisto_il or _adesso()

    def operazione(conn):
        nuovo = store.rivedi_set(conn, set_id=set_id, decision=decisione, reviewer=revisore,
                                 reviewed_at=revisto_il, note=nota)
        return {"tabella": "method_record_reviews", "id": nuovo, "set_id": set_id, "decision": decisione,
                "reviewer": revisore, "reviewed_at": revisto_il, "note": nota}

    def rileggi(conn, applicato):
        riga = conn.execute("SELECT set_id, decision, reviewer, reviewed_at, note FROM method_record_reviews "
                            "WHERE id=?", (applicato["id"],)).fetchone()
        atteso = tuple(applicato[k] for k in ("set_id", "decision", "reviewer", "reviewed_at", "note"))
        return {"id": applicato["id"], "confermato": riga is not None and tuple(riga) == atteso}

    esito = esegui_scrittura(db_path, operazione, rileggi, apply=apply)
    esito["origine_data"] = origine
    return esito


def _gruppi(records):
    gruppi = {}
    for record in records:
        chiave = (str(record.get("driver")), str(record.get("scenario")))
        voce = gruppi.setdefault(chiave, {"driver": chiave[0], "scenario": chiave[1], "n": 0, "valid_until_min": None})
        voce["n"] += 1
        scade = record.get("valid_until")
        if voce["valid_until_min"] is None or scade < voce["valid_until_min"]:
            voce["valid_until_min"] = scade
    return [gruppi[k] for k in sorted(gruppi)]


def elenca(db_path, ticker, al=None):
    percorso = _migrazione.richiedi_wal(db_path)
    origine = "argomento" if al else OROLOGIO
    al = al or date.today().isoformat()
    with closing(store.apri_lettura(percorso)) as conn:
        storia = store.storia_ticker(conn, ticker, al)
        try:
            in_uso, errore_in_uso = store.leggi_approvati(conn, ticker, al), None
        except store.SetAlterato as exc:
            in_uso, errore_in_uso = None, str(exc)
        try:
            da_rivedere, errore_da_rivedere = store.conta_da_rivedere(conn, ticker, al), None
        except store.SetAlterato as exc:
            da_rivedere, errore_da_rivedere = None, str(exc)
    giorno = al[:10]
    set_letti = []
    for voce in storia:
        records = voce["records"] or []
        set_letti.append({
            **{k: v for k, v in voce.items() if k != "records"},
            "scadenza_minima": voce["earliest_valid_until"],
            "record_per_driver_scenario": _gruppi(records),
            "record": sorted(records, key=lambda r: (str(r.get("driver")), str(r.get("scenario")))),
        })
    return {
        "esito": "ok", "modo": "lettura", "db": str(percorso), "ticker": ticker.upper(), "al": al,
        "origine_data": origine,
        "in_uso_al": None if in_uso is None else [
            {"method_id": s["method_id"], "set_id": s["id"], "method_version": s["method_version"],
             "approvato_il": s["review"]["reviewed_at"], "revisore": s["review"]["reviewer"],
             "scadenza_minima": s["earliest_valid_until"], "scaduto_al": s["earliest_valid_until"] < giorno}
            for s in in_uso],
        "errore_in_uso": errore_in_uso, "da_rivedere_al": da_rivedere, "errore_da_rivedere": errore_da_rivedere,
        "set": set_letti,
    }


# --------------------------------------------------------------------------- main
def _parser():
    comune = argparse.ArgumentParser(add_help=False)
    comune.add_argument("--db", default=None, help="default: il DB del progetto (memory_db.SQLITE_PATH)")
    modo = comune.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="default: prova su copia in tmp")
    modo.add_argument("--apply", action="store_true", help="scrive davvero (porta 8765 libera, backup)")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    comandi = parser.add_subparsers(dest="comando", required=True)
    p = comandi.add_parser("proponi", parents=[comune])
    p.add_argument("--file", required=True)
    p.add_argument("--preparato-da", required=True)
    p.add_argument("--preparato-il", default=None)
    e = comandi.add_parser("elenca", parents=[comune])
    e.add_argument("--ticker", required=True)
    e.add_argument("--al", default=None, help="data della vista (default: oggi, orologio della macchina)")
    r = comandi.add_parser("rivedi", parents=[comune])
    r.add_argument("--set-id", type=int, required=True)
    r.add_argument("--decisione", choices=store.DECISIONI, required=True)
    r.add_argument("--revisore", required=True)
    r.add_argument("--nota", default=None)
    r.add_argument("--revisto-il", default=None)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.db is None:
        from bellomberg.storage import memory_db
        args.db = memory_db.SQLITE_PATH
    try:
        if args.comando == "proponi":
            esito = proponi(args.db, args.file, args.preparato_da, args.preparato_il, apply=args.apply)
        elif args.comando == "rivedi":
            esito = rivedi(args.db, args.set_id, args.decisione, args.revisore, args.nota, args.revisto_il,
                           apply=args.apply)
        else:
            esito = elenca(args.db, args.ticker, args.al)
            if args.apply:
                esito["nota_apply"] = "elenca non scrive: --apply ignorato, nessuna scrittura"
    except (store.SetNonValido, store.ArchivioNonMigrato, store.SetAlterato, FileNotFoundError,
            ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"esito": "rifiutato", "comando": args.comando, "errore": type(exc).__name__,
                          "messaggio": str(exc)}, indent=2, ensure_ascii=False))
        return 2
    codice = 0
    if esito.get("rilettura", {}).get("confermato") is False:
        applicata = esito.get("applicazione") or {}
        esito["esito"] = "rilettura non confermata"
        esito["messaggio"] = (
            f"scrittura APPLICATA nel DB ({applicata.get('tabella')} id {applicata.get('id')}, COMMIT fatto) e "
            f"backup preso ({esito.get('backup')}), ma la rilettura non la conferma: NON rilanciare il comando "
            "(rivedi appenderebbe una seconda revisione per sempre), controllare con `elenca`")
        codice = 1
    print(json.dumps(esito, indent=2, ensure_ascii=False))
    return codice


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
