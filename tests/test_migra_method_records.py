# -*- coding: utf-8 -*-
"""Migrazione e CLI dell'archivio dei record documentati (decisione PM 1A, 13/09, Claude Opus 5).

- `tools/migrations/migra_method_records.py`: aggiunge l'archivio ai DB ESISTENTI (i nuovi
  lo ricevono da `MemoryDB`). Dry-run di default con le scritture sulla sorgente CONTATE,
  --apply con backup, rilettura e rifiuto a porta 8765 occupata.
- `tools/method_records/archivio.py`: proponi / elenca / rivedi, dry-run di default.

Tutto su DB in tmp costruiti qui, in WAL come quello del progetto. Simboli e numeri
inventati. Nessuna lettura del DB vero: il default `--db` e' provato ripuntando
`memory_db.SQLITE_PATH` a un tmp.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pytest

from bellomberg.storage import method_records_store as store
from tools.method_records import archivio
from tools.migrations import migra_method_records as migrazione

OGGETTI_ARCHIVIO = {
    "table:method_record_sets", "table:method_record_reviews",
    "index:idx_method_record_sets_ticker", "index:idx_method_record_reviews_set",
    "index:sqlite_autoindex_method_record_sets_1",
    "trigger:method_record_sets_insert_immutable", "trigger:method_record_sets_update_immutable",
    "trigger:method_record_sets_delete_immutable", "trigger:method_record_reviews_insert_immutable",
    "trigger:method_record_reviews_update_immutable", "trigger:method_record_reviews_delete_immutable",
}
MOTIVI = {"bear": "calo inventato", "base": "tenuta inventata", "bull": "crescita inventata"}
OGGI_DEI_TEST = date(2026, 9, 13)


@pytest.fixture(autouse=True)
def orologio_fermo(monkeypatch):
    """La CLI rifiuta --preparato-il / --revisto-il anteriori al giorno dell'orologio della
    macchina: le prove con date scritte fermano quell'orologio al 13/09/2026. Una sola prova
    (test_la_regola_delle_date_usa_l_orologio_vero_della_macchina) lo lascia vero."""
    monkeypatch.setattr(archivio, "_oggi", lambda: OGGI_DEI_TEST, raising=False)


def _crea_wal(path, script):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.executescript(script)
        conn.commit()


def _oggetti_archivio(path):
    with closing(sqlite3.connect(path)) as conn:
        return {(tipo + ":" + nome): sql for tipo, nome, tabella, sql in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master")
            if tabella in ("method_record_sets", "method_record_reviews")}


def _righe(path, tabella):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT * FROM " + tabella + " ORDER BY 1").fetchall()


def _bak(cartella):
    return sorted(p.name for p in cartella.iterdir() if p.name.endswith(".bak"))


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.db"
    _crea_wal(path, "CREATE TABLE valuation_theses(id INTEGER PRIMARY KEY, ticker TEXT, fair_value REAL);"
                    "CREATE TABLE decisions(id INTEGER PRIMARY KEY, ticker TEXT, action TEXT);"
                    "INSERT INTO valuation_theses VALUES(1,'SYNTH-ARC',41.5);"
                    "INSERT INTO decisions VALUES(1,'SYNTH-ARC','RESEARCH');")
    return path


# ===========================================================================
# MIGRAZIONE
# ===========================================================================
def test_dry_run_non_scrive_e_lo_misura(legacy, tmp_path):
    prima = legacy.read_bytes()
    esito = migrazione.migra(legacy)
    assert esito["modo"] == "dry-run" and esito["backup"] is None
    assert esito["tabelle_mancanti"] == ["method_record_reviews", "method_record_sets"]
    assert esito["scritture_sorgente"]["osservate"] > 0, "il contatore deve aver visto le letture"
    assert esito["scritture_sorgente"]["scritture"] == 0
    assert esito["scritture_sorgente"]["total_changes"] == 0
    assert esito["sorgente_invariata"] is True
    assert esito["prova"]["scritture"]["scritture"] > 0, "la prova su copia installa davvero: il contatore conta"
    assert esito["prova"]["tabelle_preesistenti_invariate"] is True
    assert legacy.read_bytes() == prima
    assert _oggetti_archivio(legacy) == {} and _bak(tmp_path) == []


def test_la_sorgente_del_dry_run_rifiuta_ogni_scrittura(legacy, monkeypatch):
    # prima di tutto, e in fretta: senza query_only la scrittura sotto riuscirebbe e poi
    # conn.backup() ritenterebbe all'infinito su SQLITE_LOCKED (misurato: la batteria si blocca)
    with closing(migrazione.apri(legacy, sola_lettura=True)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    vera = migrazione.fingerprint
    tentativi = []

    def scrive_prima(conn):
        if not tentativi:
            tentativi.append(True)
            conn.execute("UPDATE decisions SET action='SELL'")
        return vera(conn)
    monkeypatch.setattr(migrazione, "fingerprint", scrive_prima)
    prima = legacy.read_bytes()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        migrazione.migra(legacy)
    assert tentativi and legacy.read_bytes() == prima


def test_apply_installa_lo_stesso_schema_dei_db_nuovi_con_backup_e_rilettura(legacy, tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(migrazione, "backend_alive", lambda: False)
    esito = migrazione.migra(legacy, apply=True)
    assert esito["modo"] == "apply"
    assert esito["rilettura"]["schema_completo"] is True
    assert esito["rilettura"]["tabelle_preesistenti_invariate"] is True
    assert esito["applicazione"]["tabelle_preesistenti_invariate"] is True
    # schema: nomi congelati qui + stesso SQL di un DB nato da MemoryDB (due strade, un contratto)
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    nuovo = tmp_path / "nuovo.db"
    memory_db.MemoryDB(db_path=str(nuovo), chroma_path=str(tmp_path / "chroma"))
    assert set(_oggetti_archivio(legacy)) == OGGETTI_ARCHIVIO
    assert _oggetti_archivio(legacy) == _oggetti_archivio(nuovo)
    # dati di prima intatti, backup con i dati di prima e senza archivio
    assert _righe(legacy, "valuation_theses") == [(1, "SYNTH-ARC", 41.5)]
    assert _righe(legacy, "decisions") == [(1, "SYNTH-ARC", "RESEARCH")]
    assert _bak(tmp_path) and esito["backup"].endswith(_bak(tmp_path)[0])
    assert _righe(esito["backup"], "decisions") == [(1, "SYNTH-ARC", "RESEARCH")]
    assert _oggetti_archivio(esito["backup"]) == {}
    # l'archivio migrato si usa
    with closing(sqlite3.connect(legacy)) as conn:
        set_id = store.proponi_set(conn, ticker="SYNTH-ARC", method_id="operating_fcff", method_version="2",
                                   records=[{"valid_until": "2027-01-01", "as_of": "2026-03-31",
                                             "kind": "historical", "source_id": "https://example.com/a"}],
                                   scenario_rationale=MOTIVI, provenance="inventata", prepared_by="Claude",
                                   prepared_at="2026-09-01")
        store.rivedi_set(conn, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
        assert [d["id"] for d in store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-10")] == [set_id]
    # idempotente
    ancora = migrazione.migra(legacy, apply=True)
    assert ancora["tabelle_mancanti"] == [] and ancora["rilettura"]["tabelle_preesistenti_invariate"] is True


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_una_installazione_che_tocca_dati_preesistenti_torna_indietro(legacy, tmp_path, monkeypatch, apply):
    monkeypatch.setattr(migrazione, "backend_alive", lambda: False)
    installa = migrazione._installa

    def installa_e_sporca(conn):
        installa(conn)
        conn.execute("UPDATE decisions SET action='SELL'")
    monkeypatch.setattr(migrazione, "_installa", installa_e_sporca)
    with pytest.raises(ValueError, match="preesistenti variati"):
        migrazione.migra(legacy, apply=apply)
    assert _righe(legacy, "decisions") == [(1, "SYNTH-ARC", "RESEARCH")]
    assert _oggetti_archivio(legacy) == {}


def test_apply_rifiutato_a_porta_occupata_senza_backup_ne_scritture(legacy, tmp_path, monkeypatch):
    monkeypatch.setattr(migrazione, "backend_alive", lambda: True)
    prima = legacy.read_bytes()
    with pytest.raises(RuntimeError, match="8765"):
        migrazione.migra(legacy, apply=True)
    assert legacy.read_bytes() == prima and _bak(tmp_path) == []


def test_db_assente_non_viene_creato(tmp_path):
    percorso = tmp_path / "data" / "consigliere_assente.db"
    with pytest.raises(FileNotFoundError):
        migrazione.migra(percorso, apply=False)
    assert not percorso.exists() and not percorso.parent.exists()


def test_db_non_wal_rifiutato_prima_di_aprirlo(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    percorso = tmp_path / "rollback.db"
    with closing(sqlite3.connect(percorso)) as conn:
        conn.executescript("CREATE TABLE decisions(id INTEGER PRIMARY KEY);"
                           "CREATE TABLE valuation_theses(id INTEGER PRIMARY KEY);")
        conn.commit()
    aperture = []
    monkeypatch.setattr(memory_db, "connect_sqlite", lambda *a, **k: aperture.append(a))
    prima = percorso.read_bytes()
    with pytest.raises(ValueError, match="WAL"):
        migrazione.migra(percorso)
    assert percorso.read_bytes() == prima and aperture == []


def test_db_estraneo_rifiutato(tmp_path):
    percorso = tmp_path / "estraneo.db"
    _crea_wal(percorso, "CREATE TABLE altro(id INTEGER);")
    prima = percorso.read_bytes()
    with pytest.raises(ValueError, match="decisions"):
        migrazione.migra(percorso)
    assert percorso.read_bytes() == prima


def test_schema_incompatibile_rifiutato_prima_di_ogni_modifica(legacy, monkeypatch):
    monkeypatch.setattr(migrazione, "backend_alive", lambda: False)
    with closing(sqlite3.connect(legacy)) as conn:
        conn.execute("CREATE TABLE method_record_sets(id INTEGER PRIMARY KEY, ticker TEXT)")
        conn.commit()
    prima = legacy.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        migrazione.migra(legacy, apply=True)
    assert legacy.read_bytes() == prima


def test_scrittura_concorrente_fra_preflight_e_apply_blocca(legacy, monkeypatch):
    chiamate = []

    def scrittore_concorrente():
        if not chiamate:
            with closing(sqlite3.connect(legacy)) as conn:
                conn.execute("UPDATE decisions SET action='HOLD'")
                conn.commit()
        chiamate.append(True)
        return False
    monkeypatch.setattr(migrazione, "backend_alive", scrittore_concorrente)
    with pytest.raises(ValueError, match="variato"):
        migrazione.migra(legacy, apply=True)
    assert _righe(legacy, "decisions") == [(1, "SYNTH-ARC", "HOLD")]
    assert _oggetti_archivio(legacy) == {}


def test_la_migrazione_apre_il_db_solo_dall_helper_col_path_nudo(legacy, monkeypatch):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(migrazione, "backend_alive", lambda: False)
    vera = memory_db.connect_sqlite
    percorsi = []

    def spia(*a, **k):
        percorsi.append((a, k))
        return vera(*a, **k)
    monkeypatch.setattr(memory_db, "connect_sqlite", spia)
    migrazione.migra(legacy, apply=True)
    sul_db = [p for p in percorsi if p[0] and str(p[0][0]) == str(legacy)]
    assert len(sul_db) >= 3, "preflight, applicazione e rilettura"
    assert all(k == {} and isinstance(a[0], str) for a, k in percorsi), percorsi


def test_main_della_migrazione_usa_il_db_del_progetto_risolto_alla_chiamata(legacy, monkeypatch, capsys):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(legacy))
    assert migrazione.main([]) == 0
    esito = json.loads(capsys.readouterr().out)
    assert esito["db"] == str(legacy) and esito["modo"] == "dry-run"


# ===========================================================================
# CLI archivio.py
# ===========================================================================
def _dossier(tmp_path, **cambi):
    records = [
        {"field": "enterprise_perimeter", "driver": "perimeter", "scenario": "model", "value": {"entity": "SYNTH-ARC"},
         "entity": "SYNTH-ARC", "period": "FY2026", "unit": "contract", "accounting_basis": "IFRS",
         "source_id": "https://example.com/synth-arc/relazione.pdf", "as_of": "2026-03-31",
         "valid_until": "2027-03-31", "kind": "historical", "rationale": "inventata"},
    ]
    for n, scenario in enumerate(("bear", "base", "bull")):
        records.append({"field": "revenue_growth", "driver": "revenue_growth", "scenario": scenario,
                        "value": 2.5 + n, "entity": "SYNTH-ARC", "period": "FY2027", "unit": "percent",
                        "accounting_basis": "IFRS", "source_id": "https://example.com/synth-arc/guidance.pdf",
                        "as_of": "2026-05-15", "valid_until": "2027-01-31", "kind": "company_guidance",
                        "rationale": "inventata"})
    dossier = {"ticker": "synth-arc", "method_id": "operating_fcff", "method_version": "2",
               "records": records, "scenario_rationale": MOTIVI,
               "provenance": "preparato da Claude dai documenti ufficiali (inventati)"}
    dossier.update(cambi)
    percorso = tmp_path / "dossier.json"
    percorso.write_text(json.dumps(dossier, ensure_ascii=False), encoding="utf-8")
    return percorso, dossier


@pytest.fixture
def db_archivio(tmp_path):
    path = tmp_path / "progetto.db"
    _crea_wal(path, "CREATE TABLE decisions(id INTEGER PRIMARY KEY, ticker TEXT, action TEXT);"
                    "INSERT INTO decisions VALUES(1,'SYNTH-ARC','RESEARCH');")
    with closing(sqlite3.connect(path)) as conn:
        store.crea_tabelle(conn)
        conn.commit()
    return path


def _lancia(capsys, argv):
    codice = archivio.main(argv)
    return codice, json.loads(capsys.readouterr().out)


def _proponi_apply(capsys, db, dossier, monkeypatch):
    monkeypatch.setattr(archivio, "backend_alive", lambda: False)
    return _lancia(capsys, ["proponi", "--db", str(db), "--file", str(dossier), "--preparato-da",
                            "Claude Opus 5", "--preparato-il", "2026-09-13", "--apply"])


def test_proponi_dry_run_prova_su_copia_e_non_tocca_il_db(db_archivio, tmp_path, capsys):
    dossier, contenuto = _dossier(tmp_path)
    prima = db_archivio.read_bytes()
    codice, esito = _lancia(capsys, ["proponi", "--db", str(db_archivio), "--file", str(dossier),
                                     "--preparato-da", "Claude Opus 5", "--preparato-il", "2026-09-13"])
    atteso = hashlib.sha256(json.dumps(contenuto["records"], sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    assert codice == 0 and esito["esito"] == "ok" and esito["modo"] == "dry-run"
    assert esito["prova"]["id"] == 1 and esito["prova"]["records_sha256"] == atteso
    assert esito["prova"]["n_records"] == 4 and esito["prova"]["earliest_valid_until"] == "2027-01-31"
    assert esito["scritture_sorgente"]["osservate"] > 0, "il contatore deve aver visto le letture (sonda P30)"
    assert esito["scritture_sorgente"]["scritture"] == 0 and esito["scritture_sorgente"]["total_changes"] == 0
    assert esito["backup"] is None and _bak(tmp_path) == []
    assert db_archivio.read_bytes() == prima
    assert _righe(db_archivio, "method_record_sets") == []


def test_proponi_apply_scrive_con_backup_e_rilettura(db_archivio, tmp_path, capsys, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    codice, esito = _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    assert codice == 0 and esito["modo"] == "apply"
    assert esito["rilettura"]["id"] == 1 and esito["rilettura"]["confermato"] is True
    [riga] = _righe(db_archivio, "method_record_sets")
    assert riga[1] == "SYNTH-ARC" and riga[7] == "Claude Opus 5" and riga[8] == "2026-09-13"
    assert _righe(db_archivio, "decisions") == [(1, "SYNTH-ARC", "RESEARCH")]
    assert esito["backup"] and _righe(esito["backup"], "method_record_sets") == []
    # stesso dossier una seconda volta: rifiutato, nulla scritto
    codice, esito = _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    assert codice == 2 and esito["esito"] == "rifiutato" and "duplicato" in esito["messaggio"]
    assert len(_righe(db_archivio, "method_record_sets")) == 1


@pytest.mark.parametrize("sconfina", ["due-set", "tabella-fuori-archivio", "indice-nuovo"])
def test_la_scrittura_che_sconfina_da_una_riga_torna_indietro(db_archivio, sconfina):
    """Il controllo di _applica_operazione: UNA riga nuova nella tabella dichiarata e basta."""
    def operazione(conn):
        base = dict(method_id="operating_fcff", method_version="2", scenario_rationale=MOTIVI,
                    provenance="inventata", prepared_by="Claude", prepared_at="2026-09-13")
        record = {"valid_until": "2027-01-01", "as_of": "2026-03-31", "kind": "historical",
                  "source_id": "https://example.com/a"}
        nuovo = store.proponi_set(conn, ticker="SYNTH-ARC", records=[record], **base)
        if sconfina == "due-set":
            store.proponi_set(conn, ticker="SYNTH-ARC", records=[dict(record, kind="company_guidance")], **base)
        elif sconfina == "tabella-fuori-archivio":
            conn.execute("UPDATE decisions SET action='SELL'")
        else:
            conn.execute("CREATE INDEX idx_synth_decisions_ticker ON decisions(ticker)")
        return {"tabella": "method_record_sets", "id": nuovo}
    prima = db_archivio.read_bytes()
    with pytest.raises(ValueError, match="rollback"):
        archivio.esegui_scrittura(db_archivio, operazione, lambda conn, applicato: {}, apply=False)
    with pytest.raises(ValueError, match="rollback"):
        archivio._applica_operazione(db_archivio, operazione, *_impronte(db_archivio))
    assert db_archivio.read_bytes() == prima
    assert _righe(db_archivio, "method_record_sets") == []
    assert _righe(db_archivio, "decisions") == [(1, "SYNTH-ARC", "RESEARCH")]


def _impronte(path):
    from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
    with closing(sqlite3.connect(path)) as conn:
        return fingerprint(conn), schema_fingerprint(conn)


def test_proponi_apply_a_porta_occupata_non_fa_backup_ne_scrive(db_archivio, tmp_path, capsys, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    monkeypatch.setattr(archivio, "backend_alive", lambda: True)
    prima = db_archivio.read_bytes()
    codice, esito = _lancia(capsys, ["proponi", "--db", str(db_archivio), "--file", str(dossier),
                                     "--preparato-da", "Claude", "--preparato-il", "2026-09-13", "--apply"])
    assert codice == 2 and "8765" in esito["messaggio"]
    assert db_archivio.read_bytes() == prima and _bak(tmp_path) == []


def _argv_rivedi_apply(db):
    return ["rivedi", "--db", str(db), "--set-id", "1", "--decisione", "approvato", "--revisore", "PM",
            "--revisto-il", "2026-09-14", "--apply"]


@pytest.mark.parametrize("comando", ["proponi", "rivedi"])
def test_un_guasto_dopo_il_commit_esce_1_e_dichiara_applicazione_e_backup(db_archivio, tmp_path, capsys,
                                                                          monkeypatch, comando):
    """Un errore DOPO il COMMIT (qui: l'apertura del DB per la rilettura) non e' un 'rifiutato,
    nulla scritto': la riga c'e' e il backup pure (caso E1 dello scettico). Codice 1 e un
    messaggio che lo dice, perche' rilanciare rivedi appenderebbe una seconda revisione per sempre."""
    dossier, _ = _dossier(tmp_path)
    tabella = "method_record_sets"
    if comando == "rivedi":
        _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
        tabella = "method_record_reviews"
    righe_prima, bak_prima = len(_righe(db_archivio, tabella)), _bak(tmp_path)
    viste = []

    def apri_rotta(percorso):
        viste.append(len(_righe(db_archivio, tabella)))
        raise sqlite3.OperationalError("disk I/O error inventato dal test")
    monkeypatch.setattr(store, "apri_lettura", apri_rotta)
    if comando == "proponi":
        codice, esito = _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    else:
        codice, esito = _lancia(capsys, _argv_rivedi_apply(db_archivio))
    assert viste == [righe_prima + 1], "il guasto deve cadere DOPO il commit, a riga gia' confermata"
    assert codice == 1 and esito["esito"] == "rilettura non confermata"
    assert len(_righe(db_archivio, tabella)) == righe_prima + 1
    assert esito["applicazione"]["tabella"] == tabella and esito["applicazione"]["id"] == 1
    nuovi = [b for b in _bak(tmp_path) if b not in bak_prima]
    assert len(nuovi) == 1 and esito["backup"].endswith(nuovi[0])
    assert esito["rilettura"]["confermato"] is False and "OperationalError" in esito["rilettura"]["errore"]
    assert "APPLICATA" in esito["messaggio"] and nuovi[0] in esito["messaggio"]


@pytest.mark.parametrize("comando", ["proponi", "rivedi"])
def test_una_rilettura_che_non_conferma_la_riga_esce_1(db_archivio, tmp_path, capsys, monkeypatch, comando):
    """Il ramo 'rilettura non confermata' senza eccezioni (sonde P08, P09, P10): proponi rilegge un
    set che la storia marca non integro; rivedi rilegge un file in cui la revisione non c'e'."""
    dossier, _ = _dossier(tmp_path)
    if comando == "proponi":
        vera_storia = store.storia_ticker

        def storia_non_integra(conn, ticker, cutoff):
            return [dict(voce, integro=False, motivo_non_integro="alterato dal test")
                    for voce in vera_storia(conn, ticker, cutoff)]
        monkeypatch.setattr(store, "storia_ticker", storia_non_integra)
        codice, esito = _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
        tabella = "method_record_sets"
    else:
        _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
        vera_apertura = store.apri_lettura

        def apri_il_backup(percorso):
            return vera_apertura(tmp_path / sorted(_bak(tmp_path))[-1])
        monkeypatch.setattr(store, "apri_lettura", apri_il_backup)
        codice, esito = _lancia(capsys, _argv_rivedi_apply(db_archivio))
        tabella = "method_record_reviews"
    assert codice == 1 and esito["esito"] == "rilettura non confermata"
    assert esito["rilettura"]["confermato"] is False and "errore" not in esito["rilettura"]
    assert len(_righe(db_archivio, tabella)) == 1
    assert "APPLICATA" in esito["messaggio"] and esito["backup"]


@pytest.mark.parametrize("cambi,frammento", [
    ({"prepared_by": "Claude"}, "prepared_by"),                 # arriva dagli argomenti, non dal file
    ({"records": []}, "vuot"),
    ({"scenario_rationale": {"bear": "a", "base": "b"}}, "scenario_rationale"),
], ids=["campo-in-piu", "record-vuoti", "motivi-incompleti"])
def test_proponi_rifiuta_dossier_fuori_regola(db_archivio, tmp_path, capsys, cambi, frammento):
    dossier, _ = _dossier(tmp_path, **cambi)
    codice, esito = _lancia(capsys, ["proponi", "--db", str(db_archivio), "--file", str(dossier),
                                     "--preparato-da", "Claude", "--preparato-il", "2026-09-13"])
    assert codice == 2 and esito["esito"] == "rifiutato" and frammento in esito["messaggio"]


def test_proponi_rifiuta_chiavi_doppie_nel_dossier(db_archivio, tmp_path, capsys):
    percorso = tmp_path / "doppio.json"
    percorso.write_text('{"ticker": "SYNTH-ARC", "ticker": "SYNTH-ZED"}', encoding="utf-8")
    codice, esito = _lancia(capsys, ["proponi", "--db", str(db_archivio), "--file", str(percorso),
                                     "--preparato-da", "Claude", "--preparato-il", "2026-09-13"])
    assert codice == 2 and "doppia" in esito["messaggio"]


def test_proponi_accetta_il_dossier_scritto_con_bom(db_archivio, tmp_path, capsys):
    """Un dossier salvato da un editor Windows porta il BOM UTF-8: tollerato (sonda P22)."""
    dossier, contenuto = _dossier(tmp_path)
    dossier.write_text(json.dumps(contenuto, ensure_ascii=False), encoding="utf-8-sig")
    assert dossier.read_bytes().startswith(b"\xef\xbb\xbf")
    codice, esito = _lancia(capsys, ["proponi", "--db", str(db_archivio), "--file", str(dossier),
                                     "--preparato-da", "Claude", "--preparato-il", "2026-09-13"])
    assert codice == 0 and esito["prova"]["n_records"] == 4


def test_rivedi_dry_run_non_scrive_e_apply_rende_il_set_leggibile(db_archivio, tmp_path, capsys, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    argv = ["rivedi", "--db", str(db_archivio), "--set-id", "1", "--decisione", "approvato",
            "--revisore", "PM (inventato)", "--nota", "fonti viste", "--revisto-il", "2026-09-14"]
    codice, esito = _lancia(capsys, argv)
    assert codice == 0 and esito["modo"] == "dry-run" and esito["prova"]["id"] == 1
    assert _righe(db_archivio, "method_record_reviews") == []
    codice, esito = _lancia(capsys, argv + ["--apply"])
    assert codice == 0 and esito["rilettura"]["confermato"] is True
    with closing(store.apri_lettura(db_archivio)) as conn:
        [letto] = store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-14")
    assert letto["review"]["reviewer"] == "PM (inventato)" and letto["review"]["note"] == "fonti viste"


def test_rivedi_senza_data_usa_l_orologio_e_lo_dichiara(db_archivio, tmp_path, capsys, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    codice, esito = _lancia(capsys, ["rivedi", "--db", str(db_archivio), "--set-id", "1",
                                     "--decisione", "respinto", "--revisore", "PM"])
    assert codice == 0 and esito["origine_data"] == "orologio della macchina"
    assert esito["prova"]["reviewed_at"][:4].isdigit()


@pytest.mark.parametrize("comando", ["proponi", "rivedi"])
def test_date_dichiarate_anteriori_a_oggi_rifiutate_senza_scrivere(db_archivio, tmp_path, capsys, monkeypatch,
                                                                  comando):
    """Il punto nel tempo vale sulle date DICHIARATE: una data nel passato scritta oggi cambierebbe
    cio' che un cutoff passato ha visto (caso E2 dello scettico: set preparato il 01/09 senza
    revisioni, `rivedi --revisto-il 2026-09-02` il 13/09 lo metteva in uso al 05/09)."""
    monkeypatch.setattr(archivio, "backend_alive", lambda: False)
    dossier, _ = _dossier(tmp_path)
    if comando == "rivedi":
        with closing(sqlite3.connect(db_archivio)) as conn:
            store.proponi_set(conn, ticker="SYNTH-ARC", method_id="operating_fcff", method_version="2",
                              records=json.loads(dossier.read_text(encoding="utf-8"))["records"],
                              scenario_rationale=MOTIVI, provenance="inventata", prepared_by="Claude",
                              prepared_at="2026-09-01")
        argv, opzione = ["rivedi", "--db", str(db_archivio), "--set-id", "1", "--decisione", "approvato",
                         "--revisore", "PM", "--revisto-il", "2026-09-02", "--apply"], "--revisto-il"
    else:
        argv, opzione = ["proponi", "--db", str(db_archivio), "--file", str(dossier), "--preparato-da", "Claude",
                         "--preparato-il", "2026-09-12T23:59:00", "--apply"], "--preparato-il"
    prima, bak_prima = db_archivio.read_bytes(), _bak(tmp_path)
    righe_prima = (_righe(db_archivio, "method_record_sets"), _righe(db_archivio, "method_record_reviews"))
    codice, esito = _lancia(capsys, argv)
    assert codice == 2 and esito["esito"] == "rifiutato" and esito["errore"] == "DataRetrodatata"
    assert opzione in esito["messaggio"] and "2026-09-13" in esito["messaggio"]
    assert db_archivio.read_bytes() == prima and _bak(tmp_path) == bak_prima
    assert (_righe(db_archivio, "method_record_sets"), _righe(db_archivio, "method_record_reviews")) == righe_prima


def test_la_regola_delle_date_usa_l_orologio_vero_della_macchina(db_archivio, tmp_path, capsys, monkeypatch):
    """Le altre prove fermano l'orologio della CLI: questa lo lascia vero, cosi' `_oggi` gira
    davvero (uno stub in ogni prova nasconderebbe una funzione rotta)."""
    monkeypatch.undo()   # toglie l'orologio fermo della fixture automatica
    dossier, _ = _dossier(tmp_path)
    oggi = date.today()
    base = ["proponi", "--db", str(db_archivio), "--file", str(dossier), "--preparato-da", "Claude"]
    codice, esito = _lancia(capsys, base + ["--preparato-il", (oggi - timedelta(days=1)).isoformat()])
    assert codice == 2 and esito["errore"] == "DataRetrodatata" and oggi.isoformat() in esito["messaggio"]
    codice, esito = _lancia(capsys, base + ["--preparato-il", oggi.isoformat()])
    assert codice == 0 and esito["prova"]["prepared_at"] == oggi.isoformat()


def test_elenca_mostra_stato_revisioni_record_e_scadenza(db_archivio, tmp_path, capsys, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    _lancia(capsys, ["rivedi", "--db", str(db_archivio), "--set-id", "1", "--decisione", "approvato",
                     "--revisore", "PM", "--revisto-il", "2026-09-14", "--apply"])
    prima = db_archivio.read_bytes()
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "synth-arc", "--al", "2026-09-14"])
    assert codice == 0 and esito["modo"] == "lettura" and esito["ticker"] == "SYNTH-ARC"
    assert esito["da_rivedere_al"] == 0
    assert [(u["method_id"], u["set_id"], u["scadenza_minima"]) for u in esito["in_uso_al"]] == [
        ("operating_fcff", 1, "2027-01-31")]
    [voce] = esito["set"]
    assert voce["stato_al"] == "approvato" and voce["integro"] is True
    assert voce["scadenza_minima"] == "2027-01-31" and voce["n_records"] == 4
    assert [(r["decision"], r["reviewer"], r["reviewed_at"]) for r in voce["revisioni"]] == [
        ("approvato", "PM", "2026-09-14")]
    assert [(g["driver"], g["scenario"], g["n"], g["valid_until_min"]) for g in voce["record_per_driver_scenario"]] == [
        ("perimeter", "model", 1, "2027-03-31"), ("revenue_growth", "base", 1, "2027-01-31"),
        ("revenue_growth", "bear", 1, "2027-01-31"), ("revenue_growth", "bull", 1, "2027-01-31")]
    assert len(voce["record"]) == 4 and voce["scenario_rationale"] == MOTIVI
    # il giorno prima della revisione lo stesso set e' ancora da rivedere
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--al", "2026-09-13"])
    assert esito["in_uso_al"] == [] and esito["da_rivedere_al"] == 1 and esito["set"][0]["stato_al"] == "da rivedere"
    # --apply non cambia nulla ed e' dichiarato
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--apply"])
    assert codice == 0 and "non scrive" in esito["nota_apply"]
    assert db_archivio.read_bytes() == prima


def _approva_il_dossier(capsys, db, tmp_path, monkeypatch):
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db, dossier, monkeypatch)
    codice, _esito = _lancia(capsys, ["rivedi", "--db", str(db), "--set-id", "1", "--decisione", "approvato",
                                      "--revisore", "PM", "--revisto-il", "2026-09-14", "--apply"])
    assert codice == 0


def test_elenca_dichiara_scaduto_il_set_in_uso_oltre_la_scadenza_minima(db_archivio, tmp_path, capsys, monkeypatch):
    """Il set in uso con record scaduti resta in uso (lo scarta il valutatore a valle) ma la
    vista del PM lo deve dire (sonda P11). Scadenza minima del dossier: 2027-01-31."""
    _approva_il_dossier(capsys, db_archivio, tmp_path, monkeypatch)
    viste = {}
    for al in ("2027-01-31", "2027-02-01"):
        codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--al", al])
        assert codice == 0
        viste[al] = [(u["set_id"], u["scaduto_al"]) for u in esito["in_uso_al"]]
    assert viste == {"2027-01-31": [(1, False)], "2027-02-01": [(1, True)]}


def test_elenca_su_un_set_alterato_non_finge_nessun_set_in_uso(db_archivio, tmp_path, capsys, monkeypatch):
    """Un set approvato e poi alterato: 'nessun set in uso' sarebbe un ripiego muto (sonda P12)."""
    _approva_il_dossier(capsys, db_archivio, tmp_path, monkeypatch)
    with closing(sqlite3.connect(db_archivio)) as conn:
        conn.execute("DROP TRIGGER method_record_sets_update_immutable")
        conn.execute("UPDATE method_record_sets SET records_sha256=? WHERE id=1", ("0" * 64,))
        store.crea_tabelle(conn)
        conn.commit()
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--al", "2026-09-14"])
    assert codice == 0 and esito["in_uso_al"] is None and "sha256" in esito["errore_in_uso"]
    [voce] = esito["set"]
    assert voce["integro"] is False and "sha256" in voce["motivo_non_integro"]


def test_elenca_prima_della_preparazione_dichiara_preparato_dopo_la_data(db_archivio, tmp_path, capsys, monkeypatch):
    """Sonda P14: al giorno prima della preparazione dichiarata il set non esiste per la vista."""
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db_archivio, dossier, monkeypatch)      # preparato il 2026-09-13
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--al", "2026-09-12"])
    assert codice == 0 and esito["in_uso_al"] == [] and esito["da_rivedere_al"] == 0
    assert [(v["stato_al"], v["integro"]) for v in esito["set"]] == [("preparato dopo la data", True)]


def test_elenca_con_una_data_archiviata_non_iso_resta_in_piedi_e_lo_dichiara(db_archivio, tmp_path, capsys,
                                                                             monkeypatch):
    """Una revisione scritta a mano con la data in forma non ISO: la vista del PM non cade (prima
    usciva 2), marca il set 'alterato' e dichiara n.d. il set in uso e il conto da rivedere."""
    dossier, _ = _dossier(tmp_path)
    _proponi_apply(capsys, db_archivio, dossier, monkeypatch)
    with closing(sqlite3.connect(db_archivio)) as conn:
        conn.execute("INSERT INTO method_record_reviews(set_id,decision,reviewer,reviewed_at) "
                     "VALUES(1,'approvato','scritta a mano','14/09/2026')")
        conn.commit()
    codice, esito = _lancia(capsys, ["elenca", "--db", str(db_archivio), "--ticker", "SYNTH-ARC", "--al", "2026-09-14"])
    assert codice == 0
    [voce] = esito["set"]
    assert voce["stato_al"] == "alterato" and voce["integro"] is False and "non ISO" in voce["motivo_non_integro"]
    assert [r["visibile_al"] for r in voce["revisioni"]] == [None]
    assert esito["in_uso_al"] is None and "non ISO" in esito["errore_in_uso"]
    assert esito["da_rivedere_al"] is None and "non ISO" in esito["errore_da_rivedere"]


def test_cli_su_archivio_non_migrato_indica_la_migrazione(legacy, tmp_path, capsys):
    dossier, _ = _dossier(tmp_path)
    prima = legacy.read_bytes()
    for argv in (["elenca", "--db", str(legacy), "--ticker", "SYNTH-ARC"],
                 ["proponi", "--db", str(legacy), "--file", str(dossier), "--preparato-da", "Claude",
                  "--preparato-il", "2026-09-13"]):
        codice, esito = _lancia(capsys, argv)
        assert codice == 2 and esito["errore"] == "ArchivioNonMigrato"
        assert "migra_method_records" in esito["messaggio"]
    assert legacy.read_bytes() == prima


def test_cli_db_assente_non_creato(tmp_path, capsys):
    percorso = tmp_path / "data" / "assente.db"
    codice, esito = _lancia(capsys, ["elenca", "--db", str(percorso), "--ticker", "SYNTH-ARC"])
    assert codice == 2 and esito["errore"] == "FileNotFoundError"
    assert not percorso.exists() and not percorso.parent.exists()


def test_cli_senza_db_usa_quello_del_progetto_risolto_alla_chiamata(db_archivio, monkeypatch, capsys):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(db_archivio))
    codice, esito = _lancia(capsys, ["elenca", "--ticker", "SYNTH-ARC"])
    assert codice == 0 and esito["db"] == str(db_archivio)
