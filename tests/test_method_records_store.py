# -*- coding: utf-8 -*-
"""Archivio privato dei record documentati (decisione PM 1A, 13/09, Claude Opus 5).

Il comitato legge a ogni run SOLO i set che il PM ha approvato entro il cutoff. Questa
batteria fissa il contratto di `bellomberg.storage.method_records_store` con un ORACOLO
CONGELATO QUI (tabella `ESITI`): chi cambia la regola del punto nel tempo deve cambiare
anche la tabella, a mano, e dire perche'.

Tutto in tmp: simboli e numeri INVENTATI (SYNTH-*), nessun dato del portafoglio, nessun
DB vero. L'impronta attesa dei record e' ricalcolata qui con hashlib + json, non con la
funzione sotto test.
"""
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime

import pytest

from bellomberg.storage import method_records_store as store

MOTIVI = {"bear": "calo inventato della domanda",
          "base": "tenuta inventata del portafoglio ordini",
          "bull": "crescita inventata dei margini"}

TRIGGER_ATTESI = {
    "method_record_sets_insert_immutable", "method_record_sets_update_immutable",
    "method_record_sets_delete_immutable", "method_record_reviews_insert_immutable",
    "method_record_reviews_update_immutable", "method_record_reviews_delete_immutable",
}
TABELLE_ATTESE = {"method_record_sets", "method_record_reviews"}
CHIAVI_SET_LETTO = {"id", "ticker", "method_id", "method_version", "records", "scenario_rationale",
                    "provenance", "prepared_by", "prepared_at", "records_sha256",
                    "earliest_valid_until", "latest_as_of", "review"}
CHIAVI_REVISIONE = {"id", "decision", "reviewer", "reviewed_at", "note"}


def _record(driver="revenue_growth", scenario="base", *, valore=4.25, as_of="2026-03-31",
            valid_until="2027-06-30", kind="company_guidance",
            source_id="https://example.com/synth-arc/relazione-annuale.pdf"):
    return {"field": "revenue_growth", "driver": driver, "scenario": scenario, "value": valore,
            "entity": "SYNTH-ARC S.p.A.", "period": "FY2027", "unit": "percent",
            "accounting_basis": "IFRS", "source_id": source_id, "as_of": as_of,
            "valid_until": valid_until, "kind": kind, "rationale": "numero inventato per il test"}


def _sha_atteso(records):
    testo = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


def _proponi(conn, *, ticker="SYNTH-ARC", method_id="operating_fcff", records=None,
             prepared_at="2026-09-01", motivi=None, **extra):
    argomenti = dict(ticker=ticker, method_id=method_id, method_version="2",
                     records=records if records is not None else [_record()],
                     scenario_rationale=motivi if motivi is not None else dict(MOTIVI),
                     provenance="preparato da Claude dai documenti ufficiali (inventati)",
                     prepared_by="Claude Opus 5", prepared_at=prepared_at)
    argomenti.update(extra)
    return store.proponi_set(conn, **argomenti)


def _conta(conn, tabella):
    return conn.execute("SELECT count(*) FROM " + tabella).fetchone()[0]


@pytest.fixture
def archivio(tmp_path):
    from bellomberg.storage import memory_db
    conn = memory_db.connect_sqlite(str(tmp_path / "archivio.db"))
    store.crea_tabelle(conn)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. La tabella degli esiti, congelata
# ---------------------------------------------------------------------------
# (etichetta, metodo, preparato il, [(decisione, revisto il), ...])
LINEA = [
    ("B1", "bank_ddm", "2026-05-01", [("approvato", "2026-05-02")]),
    ("S1", "operating_fcff", "2026-06-01", [("approvato", "2026-06-03"), ("ritirato", "2026-08-01")]),
    ("R1", "real_estate_nav", "2026-06-01", [("approvato", "2026-06-02")]),
    ("R2", "real_estate_nav", "2026-06-10", [("approvato", "2026-06-11")]),
    ("S2", "operating_fcff", "2026-07-01", [("respinto", "2026-07-02")]),
    ("S3", "operating_fcff", "2026-07-15", [("approvato", "2026-09-12")]),
    ("S4", "operating_fcff", "2026-09-11", []),
]

# (cutoff, set in uso per metodo, set da rivedere) per SYNTH-ARC
ESITI = [
    ("2026-04-30", {}, 0),
    ("2026-05-01", {}, 1),                                   # B1 preparato, non ancora rivisto
    ("2026-05-02", {"bank_ddm": "B1"}, 0),                   # approvato valido
    ("2026-06-01", {"bank_ddm": "B1"}, 2),                   # S1 e R1 in coda
    ("2026-06-02", {"bank_ddm": "B1", "real_estate_nav": "R1"}, 1),
    ("2026-06-03", {"bank_ddm": "B1", "real_estate_nav": "R1", "operating_fcff": "S1"}, 0),
    ("2026-06-10", {"bank_ddm": "B1", "real_estate_nav": "R1", "operating_fcff": "S1"}, 1),
    ("2026-06-11", {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S1"}, 0),  # il piu' recente vince
    ("2026-07-01", {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S1"}, 1),
    ("2026-07-02", {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S1"}, 0),  # S2 respinto
    ("2026-07-15", {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S1"}, 1),
    ("2026-08-01", {"bank_ddm": "B1", "real_estate_nav": "R2"}, 1),                          # S1 ritirato
    ("2026-09-10", {"bank_ddm": "B1", "real_estate_nav": "R2"}, 1),  # S3 approvato DOPO: invisibile
    ("2026-09-11", {"bank_ddm": "B1", "real_estate_nav": "R2"}, 2),  # S4 preparato il giorno stesso
    ("2026-09-12", {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S3"}, 1),
]


def _costruisci_linea(conn):
    etichette = {}
    for n, (etichetta, metodo, preparato, _revisioni) in enumerate(LINEA):
        nuovo = _proponi(conn, method_id=metodo, prepared_at=preparato,
                         records=[_record(valore=10.0 + n)])
        etichette[nuovo] = etichetta
    per_etichetta = {v: k for k, v in etichette.items()}
    for etichetta, _metodo, _preparato, revisioni in LINEA:
        for decisione, revisto in revisioni:
            store.rivedi_set(conn, set_id=per_etichetta[etichetta], decision=decisione,
                             reviewer="PM (inventato)", reviewed_at=revisto)
    # un altro titolo, mai visibile per SYNTH-ARC
    altro = _proponi(conn, ticker="SYNTH-ZED", prepared_at="2026-06-01", records=[_record(valore=99.5)])
    store.rivedi_set(conn, set_id=altro, decision="approvato", reviewer="PM (inventato)",
                     reviewed_at="2026-06-02")
    etichette[altro] = "Z1"
    return etichette


@pytest.mark.parametrize("cutoff,in_uso,da_rivedere", ESITI, ids=[e[0] for e in ESITI])
def test_tabella_degli_esiti_congelata(archivio, cutoff, in_uso, da_rivedere):
    etichette = _costruisci_linea(archivio)
    letti = store.leggi_approvati(archivio, "SYNTH-ARC", cutoff)
    assert {d["method_id"]: etichette[d["id"]] for d in letti} == in_uso
    assert len(letti) == len(in_uso), "un metodo compare una sola volta"
    for d in letti:
        assert d["ticker"] == "SYNTH-ARC"
        assert d["review"]["decision"] == "approvato"
        assert d["review"]["reviewed_at"][:10] <= cutoff
        assert d["prepared_at"][:10] <= cutoff
    assert store.conta_da_rivedere(archivio, "SYNTH-ARC", cutoff) == da_rivedere


def test_il_cutoff_vale_come_data_in_ogni_forma_e_il_simbolo_in_ogni_maiuscola(archivio):
    etichette = _costruisci_linea(archivio)
    atteso = {"bank_ddm": "B1", "real_estate_nav": "R2", "operating_fcff": "S3"}
    for cutoff in ("2026-09-12", date(2026, 9, 12), datetime(2026, 9, 12, 7, 30), "2026-09-12T23:59:00"):
        letti = store.leggi_approvati(archivio, "synth-arc", cutoff)
        assert {d["method_id"]: etichette[d["id"]] for d in letti} == atteso, cutoff
    assert [etichette[d["id"]] for d in store.leggi_approvati(archivio, "SYNTH-ZED", "2026-06-02")] == ["Z1"]


def test_una_revisione_dopo_il_cutoff_non_riscrive_il_passato(archivio):
    """Stesso set, due cutoff: la revisione del 12/09 non esiste per chi guarda al 10/09."""
    set_id = _proponi(archivio, prepared_at="2026-09-01")
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-12T09:15:00")
    assert store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10") == []
    assert store.conta_da_rivedere(archivio, "SYNTH-ARC", "2026-09-10") == 1
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-12")] == [set_id]


def test_set_preparato_dopo_il_cutoff_resta_invisibile_anche_con_una_revisione_anteriore(archivio):
    """rivedi_set non scrive revisioni datate prima della preparazione; una riga scritta A MANO
    (senza passare dal codice) non deve bastare a far vedere al 10/09 un set preparato l'11/09."""
    set_id = _proponi(archivio, prepared_at="2026-09-11")
    archivio.execute("INSERT INTO method_record_reviews(set_id,decision,reviewer,reviewed_at) VALUES(?,?,?,?)",
                     (set_id, "approvato", "scritta a mano", "2026-09-05"))
    archivio.commit()
    assert store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10") == []
    assert store.conta_da_rivedere(archivio, "SYNTH-ARC", "2026-09-10") == 0
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-11")] == [set_id]


# ---------------------------------------------------------------------------
# 2. Cosa porta un set letto
# ---------------------------------------------------------------------------
def test_il_set_approvato_porta_record_motivi_provenienza_e_revisione(archivio):
    records = [_record("revenue_growth", "base", as_of="2026-02-01", valid_until="2027-03-31"),
               _record("revenue_growth", "bull", valore=6.5, as_of="2026-04-30", valid_until="2027-01-15")]
    set_id = _proponi(archivio, ticker="synth-arc", records=records, prepared_at="2026-09-01T10:00:00")
    review_id = store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM (inventato)",
                                 reviewed_at="2026-09-02", note="fonti controllate")
    [letto] = store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10")
    assert set(letto) == CHIAVI_SET_LETTO
    assert letto["id"] == set_id and letto["ticker"] == "SYNTH-ARC"
    assert letto["method_id"] == "operating_fcff" and letto["method_version"] == "2"
    assert letto["records"] == records
    assert letto["scenario_rationale"] == MOTIVI
    assert letto["provenance"] == "preparato da Claude dai documenti ufficiali (inventati)"
    assert letto["prepared_by"] == "Claude Opus 5" and letto["prepared_at"] == "2026-09-01T10:00:00"
    assert letto["records_sha256"] == _sha_atteso(records)
    assert letto["earliest_valid_until"] == "2027-01-15"
    assert letto["latest_as_of"] == "2026-04-30"
    assert set(letto["review"]) == CHIAVI_REVISIONE
    assert letto["review"] == {"id": review_id, "decision": "approvato", "reviewer": "PM (inventato)",
                               "reviewed_at": "2026-09-02", "note": "fonti controllate"}


def test_canonico_e_la_forma_dichiarata_dell_impronta(archivio):
    records = [{"z": 1, "a": "citta' con accento: è", "m": [1.5, {"b": 2, "a": 1}]}]
    assert store.canonico(records) == json.dumps(records, sort_keys=True, separators=(",", ":"),
                                                 ensure_ascii=False)
    assert "̀" in store.canonico(records)


# ---------------------------------------------------------------------------
# 3. Integrita': impronta ricalcolata, trigger, duplicati
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("colonna,valore", [
    ("records_json", None),              # un numero cambiato dentro i record
    ("records_sha256", "0" * 64),        # l'impronta riscritta
    ("earliest_valid_until", "2030-01-01"),  # la scadenza minima allungata a mano
], ids=["record", "impronta", "scadenza"])
def test_set_alterato_fuori_dal_codice_solleva(archivio, colonna, valore):
    set_id = _proponi(archivio, records=[_record(valore=4.25)])
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    archivio.commit()
    archivio.execute("DROP TRIGGER method_record_sets_update_immutable")
    if valore is None:
        grezzo = archivio.execute("SELECT records_json FROM method_record_sets WHERE id=?", (set_id,)).fetchone()[0]
        valore = grezzo.replace("4.25", "9.25")
        assert valore != grezzo
    archivio.execute("UPDATE method_record_sets SET " + colonna + "=? WHERE id=?", (valore, set_id))
    store.crea_tabelle(archivio)   # chi altera e rimette il trigger: l'archivio sembra intatto
    assert TRIGGER_ATTESI <= {r[0] for r in archivio.execute("SELECT name FROM sqlite_master")}
    with pytest.raises(store.SetAlterato):
        store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10")


def _altera(conn, set_id, **colonne):
    """Manomissione fuori dal codice: toglie il trigger, riscrive le colonne, lo rimette."""
    conn.commit()
    conn.execute("DROP TRIGGER method_record_sets_update_immutable")
    for colonna, valore in colonne.items():
        conn.execute("UPDATE method_record_sets SET " + colonna + "=? WHERE id=?", (valore, set_id))
    store.crea_tabelle(conn)
    conn.commit()


def _canonico_atteso(records):
    return json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.mark.parametrize("caso", ["motivi-vuoti", "record-proxy-con-impronta-coerente"])
def test_set_riscritto_fuori_regola_con_impronta_coerente_solleva(archivio, caso):
    """L'impronta copre solo i record: motivazioni riscritte, o record riscritti INSIEME
    all'impronta ricalcolata, li ferma solo la rilettura delle regole di scrittura
    (caso E6 e sonda P03 dello scettico, 13/09)."""
    set_id = _proponi(archivio)
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    if caso == "motivi-vuoti":
        colonne, frammento = {"scenario_rationale_json": "{}"}, "scenario_rationale"
    else:
        proxy = [_record(kind="proxy")]
        colonne = {"records_json": _canonico_atteso(proxy), "records_sha256": _sha_atteso(proxy)}
        frammento = "proxy"
    _altera(archivio, set_id, **colonne)
    with pytest.raises(store.SetAlterato, match=frammento):
        store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10")
    [voce] = store.storia_ticker(archivio, "SYNTH-ARC", "2026-09-10")
    assert voce["integro"] is False and frammento in voce["motivo_non_integro"]


@pytest.mark.parametrize("colonne", [{"records_sha256": "0" * 64}, {"scenario_rationale_json": "{}"}],
                         ids=["impronta", "motivi-vuoti"])
def test_approvare_un_set_alterato_e_rifiutato_e_nulla_scritto(archivio, colonne):
    """Il PM non approva un contenuto che non torna con la sua impronta (caso E5 dello scettico).
    Ritirarlo o respingerlo resta possibile: e' il modo di spegnerlo."""
    set_id = _proponi(archivio)
    _altera(archivio, set_id, **colonne)
    with pytest.raises(store.SetAlterato):
        store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    assert _conta(archivio, "method_record_reviews") == 0
    store.rivedi_set(archivio, set_id=set_id, decision="ritirato", reviewer="PM", reviewed_at="2026-09-02")
    assert _conta(archivio, "method_record_reviews") == 1


@pytest.mark.parametrize("colonna", ["reviewed_at", "prepared_at"])
def test_storia_ticker_non_solleva_su_una_data_archiviata_non_iso_e_la_dichiara(archivio, colonna):
    """La vista per la revisione del PM resta in piedi: il set con la data non ISO e' marcato
    'alterato' col motivo, invece di far cadere tutto (revisione) o di ricevere un'etichetta
    falsa come 'preparato dopo la data' (preparazione). La lettura per il comitato solleva."""
    buono = _proponi(archivio, prepared_at="2026-09-01", records=[_record(valore=1.0)])
    if colonna == "reviewed_at":
        archivio.execute("INSERT INTO method_record_reviews(set_id,decision,reviewer,reviewed_at) VALUES(?,?,?,?)",
                         (buono, "approvato", "scritta a mano", "02/09/2026"))
        alterato = buono
    else:
        records = [_record(valore=2.0)]
        alterato = archivio.execute(
            "INSERT INTO method_record_sets(ticker,method_id,method_version,records_json,scenario_rationale_json,"
            "provenance,prepared_by,prepared_at,records_sha256,n_records,earliest_valid_until,latest_as_of) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("SYNTH-ARC", "operating_fcff", "2", _canonico_atteso(records), json.dumps(MOTIVI), "scritta a mano",
             "Claude", "ieri", _sha_atteso(records), 1, "2027-06-30", "2026-03-31")).lastrowid
    archivio.commit()
    storia = {voce["id"]: voce for voce in store.storia_ticker(archivio, "SYNTH-ARC", "2026-09-10")}
    voce = storia[alterato]
    assert voce["stato_al"] == "alterato" and voce["integro"] is False
    assert colonna in voce["motivo_non_integro"] and "non ISO" in voce["motivo_non_integro"]
    if colonna == "reviewed_at":
        assert [r["visibile_al"] for r in voce["revisioni"]] == [None]
    else:
        assert storia[buono]["integro"] is True and storia[buono]["stato_al"] == "da rivedere"
    with pytest.raises(store.SetAlterato, match="non ISO"):
        store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10")


@pytest.mark.parametrize("sql", [
    "UPDATE method_record_sets SET provenance='riscritta'",
    "DELETE FROM method_record_sets",
    "INSERT OR REPLACE INTO method_record_sets(id,ticker,method_id,method_version,records_json,"
    "scenario_rationale_json,provenance,prepared_by,prepared_at,records_sha256,n_records,"
    "earliest_valid_until,latest_as_of) SELECT id,ticker,method_id,method_version,'[]',"
    "scenario_rationale_json,'riscritta',prepared_by,prepared_at,'f',0,earliest_valid_until,"
    "latest_as_of FROM method_record_sets",
    "UPDATE method_record_reviews SET decision='respinto'",
    "DELETE FROM method_record_reviews",
    "INSERT OR REPLACE INTO method_record_reviews(id,set_id,decision,reviewer,reviewed_at,note) "
    "SELECT id,set_id,'ritirato',reviewer,reviewed_at,note FROM method_record_reviews",
], ids=["update-set", "delete-set", "replace-set", "update-revisione", "delete-revisione", "replace-revisione"])
def test_trigger_rendono_l_archivio_immutabile(archivio, sql):
    set_id = _proponi(archivio)
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    archivio.commit()
    prima = (archivio.execute("SELECT * FROM method_record_sets").fetchall(),
             archivio.execute("SELECT * FROM method_record_reviews").fetchall())
    with pytest.raises(sqlite3.IntegrityError, match="immutab"):
        archivio.execute(sql)
    archivio.rollback()
    dopo = (archivio.execute("SELECT * FROM method_record_sets").fetchall(),
            archivio.execute("SELECT * FROM method_record_reviews").fetchall())
    assert dopo == prima


def test_replace_con_id_nuovo_e_stessa_impronta_fermato_anche_a_foreign_key_spente(archivio, tmp_path):
    """`INSERT OR REPLACE` con un id NUOVO e la stessa impronta: il conflitto sull'UNIQUE di
    records_sha256 cancellerebbe il set approvato senza passare dal trigger di DELETE, e su una
    connessione sqlite3 grezza (foreign_keys spente, come un tool esterno) la revisione non lo
    trattiene. Lo ferma solo la clausola sull'impronta del trigger di INSERT (sonda P04)."""
    set_id = _proponi(archivio)
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    with closing(sqlite3.connect(tmp_path / "archivio.db")) as grezza:
        assert grezza.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        prima = (grezza.execute("SELECT * FROM method_record_sets").fetchall(),
                 grezza.execute("SELECT * FROM method_record_reviews").fetchall())
        with pytest.raises(sqlite3.IntegrityError, match="immutab"):
            grezza.execute(
                "INSERT OR REPLACE INTO method_record_sets(id,ticker,method_id,method_version,records_json,"
                "scenario_rationale_json,provenance,prepared_by,prepared_at,records_sha256,n_records,"
                "earliest_valid_until,latest_as_of) SELECT id + 100,ticker,method_id,method_version,records_json,"
                "scenario_rationale_json,'riscritta',prepared_by,prepared_at,records_sha256,n_records,"
                "earliest_valid_until,latest_as_of FROM method_record_sets")
        grezza.rollback()
        dopo = (grezza.execute("SELECT * FROM method_record_sets").fetchall(),
                grezza.execute("SELECT * FROM method_record_reviews").fetchall())
    assert dopo == prima and [riga[0] for riga in dopo[0]] == [set_id]


def test_crea_tabelle_installa_tabelle_e_trigger_ed_e_idempotente(archivio):
    store.crea_tabelle(archivio)
    nomi = {r[0] for r in archivio.execute("SELECT name FROM sqlite_master")}
    assert TABELLE_ATTESE <= nomi and TRIGGER_ATTESI <= nomi


def test_duplicato_di_impronta_rifiutato_senza_scrivere(archivio):
    records = [_record(valore=7.75)]
    _proponi(archivio, records=records, prepared_at="2026-09-01")
    with pytest.raises(store.SetNonValido, match="duplicato"):
        _proponi(archivio, records=[dict(r) for r in records], prepared_at="2026-09-05",
                 provenance="un'altra provenienza")
    assert _conta(archivio, "method_record_sets") == 1


# ---------------------------------------------------------------------------
# 4. Validazione alla scrittura: niente entra se un solo campo e' fuori regola
# ---------------------------------------------------------------------------
def _senza(chiave):
    r = _record()
    del r[chiave]
    return [r]


VIETATI = [
    ("lista-vuota", dict(records=[]), "vuot"),
    ("record-non-oggetto", dict(records=["testo"]), "record 0"),
    ("senza-valid_until", dict(records=_senza("valid_until")), "valid_until"),
    ("valid_until-non-iso", dict(records=[_record(valid_until="30/06/2027")]), "valid_until"),
    ("valid_until-con-ora", dict(records=[_record(valid_until="2027-06-30T00:00:00")]), "valid_until"),
    ("senza-as_of", dict(records=_senza("as_of")), "as_of"),
    ("scade-prima-della-fonte", dict(records=[_record(as_of="2026-05-01", valid_until="2026-04-30")]), "valid_until"),
    ("proxy", dict(records=[_record(kind="proxy")]), "proxy"),
    ("PROXY", dict(records=[_record(kind="PROXY")]), "proxy"),
    ("senza-kind", dict(records=_senza("kind")), "kind"),
    ("fonte-non-url", dict(records=[_record(source_id="relazione annuale inventata")]), "source_id"),
    ("fonte-ftp", dict(records=[_record(source_id="ftp://example.com/relazione.pdf")]), "source_id"),
    ("fonte-senza-host", dict(records=[_record(source_id="https://")]), "source_id"),
    ("valore-nan", dict(records=[_record(valore=math.nan)]), "JSON"),
    ("motivi-senza-bull", dict(motivi={"bear": "a", "base": "b"}), "scenario_rationale"),
    ("motivo-vuoto", dict(motivi={"bear": "a", "base": "  ", "bull": "c"}), "scenario_rationale"),
    ("motivo-in-piu", dict(motivi={**MOTIVI, "model": "extra"}), "scenario_rationale"),
    ("simbolo-vuoto", dict(ticker=""), "ticker"),
    ("simbolo-con-spazi", dict(ticker=" SYNTH-ARC"), "ticker"),
    ("preparato-non-iso", dict(prepared_at="ieri"), "prepared_at"),
    ("versione-non-testo", dict(method_version=2), "method_version"),
    ("provenienza-vuota", dict(provenance=""), "provenance"),
    ("preparatore-vuoto", dict(prepared_by=" "), "prepared_by"),
]


@pytest.mark.parametrize("modifica,frammento", [(v[1], v[2]) for v in VIETATI], ids=[v[0] for v in VIETATI])
def test_set_fuori_regola_rifiutato_e_nulla_scritto(archivio, modifica, frammento):
    with pytest.raises(store.SetNonValido, match=frammento):
        _proponi(archivio, **modifica)
    assert _conta(archivio, "method_record_sets") == 0


def test_il_rifiuto_elenca_tutti_i_record_fuori_regola(archivio):
    records = [_record(), _record(kind="proxy"), _record(source_id="nessuna fonte")]
    with pytest.raises(store.SetNonValido) as errore:
        _proponi(archivio, records=records)
    assert "record 1" in str(errore.value) and "record 2" in str(errore.value)
    assert "record 0" not in str(errore.value)


VIETATE_REVISIONI = [
    ("set-inesistente", dict(set_id=999), "inesistente"),
    ("decisione-ignota", dict(decision="sospeso"), "decision"),
    ("revisore-vuoto", dict(reviewer=""), "reviewer"),
    ("data-non-iso", dict(reviewed_at="13/09/2026"), "reviewed_at"),
    ("prima-della-preparazione", dict(reviewed_at="2026-08-31"), "prima"),
]


@pytest.mark.parametrize("modifica,frammento", [(v[1], v[2]) for v in VIETATE_REVISIONI],
                         ids=[v[0] for v in VIETATE_REVISIONI])
def test_revisione_fuori_regola_rifiutata(archivio, modifica, frammento):
    set_id = _proponi(archivio, prepared_at="2026-09-01")
    argomenti = dict(set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    argomenti.update(modifica)
    with pytest.raises(store.SetNonValido, match=frammento):
        store.rivedi_set(archivio, **argomenti)
    assert _conta(archivio, "method_record_reviews") == 0


def test_revisione_retrodatata_rifiutata_perche_cambierebbe_la_storia(archivio):
    set_id = _proponi(archivio, prepared_at="2026-09-01")
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-10")
    with pytest.raises(store.SetNonValido, match="retrodatat"):
        store.rivedi_set(archivio, set_id=set_id, decision="ritirato", reviewer="PM", reviewed_at="2026-09-05")
    assert _conta(archivio, "method_record_reviews") == 1
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-07")] == []


def test_approvare_un_set_gia_scaduto_e_rifiutato(archivio):
    set_id = _proponi(archivio, records=[_record(as_of="2026-01-31", valid_until="2026-07-01")],
                      prepared_at="2026-06-01")
    with pytest.raises(store.SetNonValido, match="scadut"):
        store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-08-01")
    # ritirarlo o respingerlo resta possibile
    store.rivedi_set(archivio, set_id=set_id, decision="respinto", reviewer="PM", reviewed_at="2026-08-01")
    assert _conta(archivio, "method_record_reviews") == 1


def test_approvare_il_giorno_della_scadenza_minima_e_ammesso_il_giorno_dopo_no(archivio):
    """Il confine (sonda P05): il giorno di earliest_valid_until il set e' ancora valido, come
    `valid_until >= cutoff` a valle; il giorno dopo l'approvazione e' rifiutata."""
    def scade_il_primo_luglio(valore):
        return [_record(valore=valore, as_of="2026-01-31", valid_until="2026-07-01")]
    ultimo_giorno = _proponi(archivio, records=scade_il_primo_luglio(1.0), prepared_at="2026-06-01")
    store.rivedi_set(archivio, set_id=ultimo_giorno, decision="approvato", reviewer="PM",
                     reviewed_at="2026-07-01T17:00:00")
    giorno_dopo = _proponi(archivio, records=scade_il_primo_luglio(2.0), prepared_at="2026-06-01")
    with pytest.raises(store.SetNonValido, match="scadut"):
        store.rivedi_set(archivio, set_id=giorno_dopo, decision="approvato", reviewer="PM", reviewed_at="2026-07-02")
    assert _conta(archivio, "method_record_reviews") == 1


def test_a_parita_di_approvazione_vince_il_giorno_di_preparazione_dichiarato_poi_l_id(archivio):
    """Il punto nel tempo vale sulle date DICHIARATE (caso E3 dello scettico): B, archiviato e
    approvato DOPO A lo stesso giorno ma dichiarato preparato prima, non scavalca A. A parita'
    di GIORNO di preparazione vince l'id maggiore, cioe' il set archiviato dopo (sonda P01)."""
    a = _proponi(archivio, prepared_at="2026-09-10", records=[_record(valore=1.0)])
    b = _proponi(archivio, prepared_at="2026-09-05", records=[_record(valore=5.0)])
    store.rivedi_set(archivio, set_id=a, decision="approvato", reviewer="PM", reviewed_at="2026-09-11T09:00:00")
    store.rivedi_set(archivio, set_id=b, decision="approvato", reviewer="PM", reviewed_at="2026-09-11T18:00:00")
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-11")] == [a]
    c = _proponi(archivio, prepared_at="2026-09-10", records=[_record(valore=9.0)])
    store.rivedi_set(archivio, set_id=c, decision="approvato", reviewer="PM", reviewed_at="2026-09-12")
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-12")] == [c]
    assert [d["id"] for d in store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-11")] == [a]


# ---------------------------------------------------------------------------
# 5. Transazioni: rispetta quella del chiamante, altrimenti conferma
# ---------------------------------------------------------------------------
def test_senza_transazione_del_chiamante_la_scrittura_e_confermata(archivio, tmp_path):
    set_id = _proponi(archivio)
    with closing(sqlite3.connect(tmp_path / "archivio.db")) as altra:
        assert altra.execute("SELECT id FROM method_record_sets").fetchall() == [(set_id,)]


def test_dentro_la_transazione_del_chiamante_non_conferma_da_sola(archivio):
    archivio.execute("BEGIN IMMEDIATE")
    set_id = _proponi(archivio)
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    assert archivio.in_transaction
    archivio.rollback()
    assert _conta(archivio, "method_record_sets") == 0 and _conta(archivio, "method_record_reviews") == 0


# ---------------------------------------------------------------------------
# 6. Archivio assente o non migrato
# ---------------------------------------------------------------------------
def test_db_assente_non_viene_creato_e_non_si_apre_nulla(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    chiamate = []
    monkeypatch.setattr(memory_db, "connect_sqlite", lambda *a, **k: chiamate.append((a, k)))
    percorso = tmp_path / "data" / "assente.db"
    with pytest.raises(FileNotFoundError, match="assente"):
        store.apri_lettura(percorso)
    assert not percorso.exists() and not percorso.parent.exists()
    assert chiamate == []


def test_apri_lettura_passa_dall_helper_coperto_dal_tripwire_col_path_nudo(archivio, tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    set_id = _proponi(archivio)
    store.rivedi_set(archivio, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    percorso = tmp_path / "archivio.db"
    chiamate = []
    vera = memory_db.connect_sqlite     # e' gia' la guardia di conftest

    def spia(*a, **k):
        chiamate.append((a, k))
        return vera(*a, **k)
    monkeypatch.setattr(memory_db, "connect_sqlite", spia)
    with closing(store.apri_lettura(percorso)) as conn:
        assert chiamate == [((str(percorso),), {})], "path NUDO, niente URI ?mode=ro"
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert [d["id"] for d in store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-10")] == [set_id]
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO method_record_reviews(set_id,decision,reviewer,reviewed_at) "
                         "VALUES(?,?,?,?)", (set_id, "ritirato", "PM", "2026-09-11"))


@pytest.mark.parametrize("chiamata", ["leggi", "conta", "proponi", "rivedi"])
def test_tabelle_assenti_dichiarano_archivio_non_migrato(tmp_path, chiamata):
    with closing(sqlite3.connect(tmp_path / "vecchio.db")) as conn:
        conn.execute("CREATE TABLE decisions(id INTEGER PRIMARY KEY)")
        azioni = {
            "leggi": lambda: store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-10"),
            "conta": lambda: store.conta_da_rivedere(conn, "SYNTH-ARC", "2026-09-10"),
            "proponi": lambda: _proponi(conn),
            "rivedi": lambda: store.rivedi_set(conn, set_id=1, decision="approvato", reviewer="PM",
                                               reviewed_at="2026-09-02"),
        }
        with pytest.raises(store.ArchivioNonMigrato, match="migra_method_records"):
            azioni[chiamata]()
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master")} == {"decisions"}


def test_tabelle_senza_trigger_non_sono_un_archivio(archivio):
    archivio.execute("DROP TRIGGER method_record_reviews_delete_immutable")
    with pytest.raises(store.ArchivioNonMigrato, match="method_record_reviews_delete_immutable"):
        store.leggi_approvati(archivio, "SYNTH-ARC", "2026-09-10")


# ---------------------------------------------------------------------------
# 7. Aggancio in memory_db: i DB NUOVI nascono con l'archivio, gli esistenti no
# ---------------------------------------------------------------------------
def test_un_db_nuovo_di_memory_db_nasce_con_l_archivio(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    percorso = tmp_path / "nuovo.db"
    db = memory_db.MemoryDB(db_path=str(percorso), chroma_path=str(tmp_path / "chroma"))
    with closing(sqlite3.connect(percorso)) as conn:
        nomi = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert TABELLE_ATTESE <= nomi and TRIGGER_ATTESI <= nomi
    with db._conn() as conn:
        set_id = _proponi(conn)
        store.rivedi_set(conn, set_id=set_id, decision="approvato", reviewer="PM", reviewed_at="2026-09-02")
    with closing(store.apri_lettura(percorso)) as conn:
        assert [d["id"] for d in store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-10")] == [set_id]


def test_un_db_esistente_non_si_migra_da_solo_all_avvio(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    percorso = tmp_path / "esistente.db"
    with closing(sqlite3.connect(percorso)) as conn:
        conn.execute("CREATE TABLE legacy_fixture(id INTEGER)")
        conn.commit()
    memory_db.MemoryDB(db_path=str(percorso), chroma_path=str(tmp_path / "chroma"))
    with closing(store.apri_lettura(percorso)) as conn:
        with pytest.raises(store.ArchivioNonMigrato):
            store.leggi_approvati(conn, "SYNTH-ARC", "2026-09-10")
