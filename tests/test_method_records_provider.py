"""Provider method_inputs dall'archivio dei record approvati dal PM (decisione PM 1A, 13/09).

Simboli e numeri INVENTATI (SYNTH-*, caso piatto di test_sector_operating_drivers: 61 record,
FV base 14,13). Nessun dato del portafoglio.

DB: sempre un file in tmp_path. `memory_db.SQLITE_PATH` e' rediretto li'; ogni apertura via
`memory_db.connect_sqlite` e' registrata (sopra il tripwire di conftest, che resta attivo) e
ogni test del provider verifica che il percorso aperto sia quello tmp, NUDO (niente URI).

ARCHIVIO: si usa sempre il modulo reale e il suo schema. Un modulo assente o vuoto
fa fallire la preparazione della prova: nessun sostituto puo' certificare l'integrazione.
"""
from copy import deepcopy
import hashlib
import importlib
import json
import os
import sqlite3
import sys

import pytest

from test_sector_operating_drivers import DAY, operating_records
from test_documented_dcf_contract import contract_tools  # noqa: F401  (fixture)
from test_valuation_snapshot_persistence import db  # noqa: F401  (fixture di contract_tools)


STORE = "bellomberg.storage.method_records_store"
TICKER = "SYNTH-EXT"
REVIEWER = "PM-SYNTH"
RATIONALE = {"bear": "Synthetic archived adverse case", "base": "Synthetic archived central case",
             "bull": "Synthetic archived favourable case"}
EXPLICIT_RATIONALE = {s: "Synthetic desk proposal " + s for s in ("bear", "base", "bull")}
PREPARED = "2026-09-09"
REVIEWED = DAY  # 2026-09-10
EXPIRY = "2027-01-01"  # valid_until di tutti i 61 record sintetici

# Tabella degli esiti CONGELATA (oracolo fuori dal codice sotto test): stato e frammento
# del messaggio che il PM/desk legge nell'envelope method_inputs.
ESITI = {
    "db_assente": ("source_error", "DB del progetto assente"),
    "modulo_assente": ("source_error", "non importabile"),
    "non_migrato": ("source_error", "archivio non migrato: tools/migrations/migra_method_records.py"),
    "permesso_negato": ("source_error", "PermissionError"),
    "lock": ("source_error", "OperationalError: database is locked"),
    "alterato": ("source_error", "set approvato alterato"),
    "nessun_approvato": ("data_missing", "nessun set approvato per SYNTH-EXT"),
    "approvato": ("ok", None),
}


def _sha(records):
    """Contratto condiviso: JSON canonico sort_keys, separatori compatti, ensure_ascii False."""
    canon = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Archivio:
    """Scrive set e revisioni nel DB tmp con il modulo reale."""

    def __init__(self, store, path):
        self.store, self.path = store, path

    def _write(self, action):
        from bellomberg.storage import memory_db
        conn = memory_db.connect_sqlite(self.path)
        try:
            value = action(conn)
            conn.commit()
            return value
        finally:
            conn.close()

    def crea(self, migrated=True):
        if migrated:
            self._write(self.store.crea_tabelle)
        else:
            self._write(lambda conn: conn.execute("CREATE TABLE synthetic_other (x TEXT)"))

    def proponi(self, records=None, *, ticker=TICKER, method_id="operating_fcff", method_version="2",
                prepared_at=PREPARED, rationale=None):
        records = operating_records() if records is None else records
        return self._write(lambda conn: self.store.proponi_set(conn, ticker=ticker, method_id=method_id,
            method_version=method_version, records=records,
            scenario_rationale=deepcopy(RATIONALE if rationale is None else rationale),
            provenance="https://example.org/synthetic/official-documents",
            prepared_by="claude-synthetic", prepared_at=prepared_at))

    def rivedi(self, set_id, decision="approvato", reviewed_at=REVIEWED):
        return self._write(lambda conn: self.store.rivedi_set(conn, set_id=set_id, decision=decision,
                                                              reviewer=REVIEWER, reviewed_at=reviewed_at))

    def approvato(self, **kwargs):
        set_id = self.proponi(**kwargs)
        self.rivedi(set_id)
        return set_id


@pytest.fixture
def archivio(tmp_path, monkeypatch):
    from bellomberg.storage import memory_db
    path = str(tmp_path / "archivio_synth.db")
    monkeypatch.setattr(memory_db, "SQLITE_PATH", path)
    store = importlib.import_module(STORE)
    assert store.STATEMENTS, "Lo schema reale dell'archivio deve essere presente"
    opened, guarded = [], memory_db.connect_sqlite

    def spy_connect(path=None, **kwargs):
        opened.append((path, kwargs))
        return guarded(path, **kwargs)

    monkeypatch.setattr(memory_db, "connect_sqlite", spy_connect)
    read_paths, real_open = [], store.apri_lettura

    def spy_open(db_path):
        read_paths.append(db_path)
        return real_open(db_path)

    monkeypatch.setattr(store, "apri_lettura", spy_open)
    arch = Archivio(store, path)
    arch.opened, arch.read_paths = opened, read_paths
    yield arch
    tmp = os.path.normcase(os.path.realpath(str(tmp_path)))
    for opened_path, kwargs in opened:
        assert isinstance(opened_path, str) and not opened_path.startswith("file:"), opened_path
        assert not kwargs.get("uri"), kwargs
        assert os.path.normcase(os.path.realpath(opened_path)).startswith(tmp), opened_path


def _provider():
    from bellomberg.valuation import sector_analysis
    return sector_analysis.default_sector_providers()["method_inputs"]


def _profile(*args, **kwargs):
    return {"status": "ok", "source_id": "synthetic", "as_of": DAY,
            "data": {"info": {}, "evidence": [{"field": f, "value": v, "source_id": "synthetic", "as_of": DAY}
                                              for f, v in [("instrument", "equity"),
                                                           ("business_model", "manufacturing")]]}}


def _prepare(as_of=DAY, user_context=None):
    from bellomberg.valuation import sector_analysis
    return sector_analysis.prepare_sector_analysis(TICKER, as_of=as_of, user_context=user_context,
        providers={"profile": _profile, "method_inputs": _provider()})


# ---------------------------------------------------------------------- provider: esiti
def _scenario(name, archivio, monkeypatch):
    if name == "db_assente":
        return
    if name == "modulo_assente":
        archivio.crea()
        archivio.approvato()
        import bellomberg.storage as storage_package
        monkeypatch.delattr(storage_package, "method_records_store", raising=False)
        monkeypatch.setitem(sys.modules, STORE, None)
        return
    if name == "non_migrato":
        archivio.crea(migrated=False)
        return
    archivio.crea()
    if name == "nessun_approvato":
        archivio.proponi()
        changed = operating_records()
        changed[0]["rationale"] = "Synthetic second pending proposal"
        archivio.proponi(changed)
        return
    archivio.approvato()

    def raising(exc):
        def boom(*args, **kwargs):
            raise exc
        return boom

    if name == "permesso_negato":
        monkeypatch.setattr(archivio.store, "apri_lettura", raising(PermissionError("synthetic denied")))
    if name == "lock":
        monkeypatch.setattr(archivio.store, "leggi_approvati",
                            raising(sqlite3.OperationalError("database is locked")))
    if name == "alterato":
        monkeypatch.setattr(archivio.store, "leggi_approvati",
                            raising(archivio.store.SetAlterato("sha synthetic")))


@pytest.mark.parametrize("name", sorted(ESITI))
def test_tabella_esiti_del_provider(archivio, monkeypatch, name):
    _scenario(name, archivio, monkeypatch)
    existed = os.path.exists(archivio.path)
    envelope = _provider()(TICKER, as_of=DAY)
    status, fragment = ESITI[name]
    assert envelope["status"] == status, envelope
    assert envelope["source_id"] == "method_records_archive"
    assert envelope["records"] == []
    assert os.path.exists(archivio.path) == existed  # mai creato
    if fragment is not None:
        assert fragment in envelope["message"], envelope["message"]
    if name == "nessun_approvato":
        # nessun conteggio della coda nell'envelope: cambierebbe lo snapshot_id
        assert envelope["message"] == "nessun set approvato per SYNTH-EXT"
        assert envelope["data"] is None
    if name == "db_assente":
        assert archivio.read_paths == [] and archivio.opened == []
    if name in ("approvato", "nessun_approvato", "lock", "alterato"):
        assert archivio.read_paths == [archivio.path]  # path NUDO, lo stesso di SQLITE_PATH


def test_set_approvato_envelope_completo(archivio):
    archivio.crea()
    set_id = archivio.approvato()
    records = operating_records()
    envelope = _provider()("synth-ext", as_of=DAY)
    assert envelope["status"] == "ok" and envelope["as_of"] == REVIEWED
    assert set(envelope["data"]) == {"sets"} and set(envelope["data"]["sets"]) == {"operating_fcff"}
    entry = envelope["data"]["sets"]["operating_fcff"]
    assert entry["set_id"] == set_id and entry["method_version"] == "2"
    assert entry["records_sha256"] == _sha(records)
    assert entry["records"] == records and len(entry["records"]) == 61
    assert entry["scenario_rationale"] == RATIONALE
    assert entry["earliest_valid_until"] == EXPIRY and entry["latest_as_of"] == DAY
    assert entry["prepared_at"] == PREPARED and entry["prepared_by"] == "claude-synthetic"
    assert entry["review"]["decision"] == "approvato" and entry["review"]["reviewer"] == REVIEWER
    assert entry["review"]["reviewed_at"] == REVIEWED
    assert archivio.opened and all(path == archivio.path for path, _ in archivio.opened)


def test_cutoff_successivo_all_approvazione_porta_la_data_dell_approvazione(archivio):
    """Data della fonte e dell'origine = giorno dell'approvazione, mai il cutoff della run."""
    from bellomberg.valuation import sector_analysis
    archivio.crea()
    set_id = archivio.approvato()  # approvato il 2026-09-10
    cutoff = "2026-09-20"
    envelope = _provider()(TICKER, as_of=cutoff)
    assert envelope["status"] == "ok" and envelope["as_of"] == REVIEWED, envelope.get("as_of")
    bundle = _prepare(as_of=cutoff)
    assert bundle["case"]["sources"]["method_inputs"]["as_of"] == REVIEWED
    origin = sector_analysis.method_inputs_origin(bundle)
    assert "approvato il %s" % REVIEWED in origin and cutoff not in origin, origin
    revised = sector_analysis.revise_sector_analysis(bundle, method_records=operating_records(),
                                                     analysis_context={"scenario_rationale": EXPLICIT_RATIONALE})
    tasks = _superseded(revised)
    assert len(tasks) == 1, revised["acquisition_tasks"]
    assert "set %s approvato il %s da %s" % (set_id, REVIEWED, REVIEWER) in tasks[0]["reason"], tasks[0]["reason"]
    assert cutoff not in tasks[0]["reason"]


@pytest.mark.parametrize("case,expected", [
    ("approvato_dopo_il_cutoff", "data_missing"),
    ("preparato_e_approvato_dopo_il_cutoff", "data_missing"),
    ("ritirato", "data_missing"),
    ("respinto", "data_missing"),
    ("ritirato_dopo_il_cutoff", "ok"),
    ("altro_titolo", "data_missing"),
])
def test_punto_nel_tempo_e_decisione(archivio, case, expected):
    archivio.crea()
    if case == "approvato_dopo_il_cutoff":
        archivio.rivedi(archivio.proponi(), reviewed_at="2026-09-11")
    if case == "preparato_e_approvato_dopo_il_cutoff":
        archivio.rivedi(archivio.proponi(prepared_at="2026-09-11"), reviewed_at="2026-09-11")
    if case in ("ritirato", "respinto"):
        set_id = archivio.approvato()
        archivio.rivedi(set_id, decision={"ritirato": "ritirato", "respinto": "respinto"}[case], reviewed_at=DAY)
    if case == "ritirato_dopo_il_cutoff":
        archivio.rivedi(archivio.approvato(), decision="ritirato", reviewed_at="2026-09-11")
    if case == "altro_titolo":
        archivio.approvato(ticker="SYNTH-OTHER")
    assert _provider()(TICKER, as_of=DAY)["status"] == expected


def test_set_alterato_nel_db_vero_diventa_source_error(archivio):
    from bellomberg.storage import memory_db
    archivio.crea()
    archivio.approvato()
    conn = memory_db.connect_sqlite(archivio.path)
    try:
        # si toglie il trigger SOLO nel DB tmp per simulare una manomissione, poi si rimette
        conn.execute("DROP TRIGGER method_record_sets_update_immutable")
        tampered = operating_records()
        tampered[0]["rationale"] = "Synthetic tampering after approval"
        conn.execute("UPDATE method_record_sets SET records_json=?",
                     (json.dumps(tampered, sort_keys=True, separators=(",", ":"), ensure_ascii=False),))
        archivio.store.crea_tabelle(conn)
        conn.commit()
    finally:
        conn.close()
    envelope = _provider()(TICKER, as_of=DAY)
    assert envelope["status"] == "source_error" and "set approvato alterato" in envelope["message"]
    assert envelope["records"] == [] and envelope["data"] is None


def test_permission_error_nel_provider_non_diventa_credenziali(archivio, monkeypatch):
    archivio.crea()
    archivio.approvato()

    def denied(*args, **kwargs):
        raise PermissionError("synthetic lock")

    monkeypatch.setattr(archivio.store, "apri_lettura", denied)
    source = _prepare()["case"]["sources"]["method_inputs"]
    assert source["status"] == "source_error", source
    assert source["source_id"] == "method_records_archive"


def test_base_exception_dal_lettore_risale_dal_provider(archivio, monkeypatch):
    """ProduzioneToccata (conftest) e' una BaseException: se il provider la inghiottisse
    diventerebbe un source_error e il tripwire sul DB vero tacerebbe."""
    class Interrotto(BaseException):
        pass

    archivio.crea()
    archivio.approvato()

    def interrompe(*args, **kwargs):
        raise Interrotto("synthetic interruption")

    monkeypatch.setattr(archivio.store, "apri_lettura", interrompe)
    with pytest.raises(Interrotto):
        _provider()(TICKER, as_of=DAY)
    with pytest.raises(Interrotto):
        _prepare()


def test_default_providers_collegano_l_archivio_senza_io_alla_costruzione(archivio):
    from bellomberg.valuation import sector_analysis
    providers = sector_analysis.default_sector_providers()
    assert "method_inputs" in providers and set(providers) == set(sector_analysis.PROVIDERS)
    assert archivio.read_paths == [] and archivio.opened == []


# --------------------------------------------------------------- cablaggio fino al FV
def test_cablaggio_archivio_approvato_fino_al_fv(archivio, tmp_path):
    from bellomberg.valuation import dcf_engine, sector_analysis
    archivio.crea()
    set_id = archivio.approvato()
    bundle = _prepare()
    assert bundle["decision"]["method_id"] == "operating_fcff"
    source = bundle["case"]["sources"]["method_inputs"]
    assert source["status"] == "ok" and source["source_id"] == "method_records_archive"
    assert len(bundle["case"]["records"]) == 61
    assert bundle["analysis_context"] == {"scenario_rationale": RATIONALE}
    result = dcf_engine.generate_valuation(TICKER, prepared_bundle=bundle, output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["fair_value_base"] == pytest.approx(14.13)
    assert len(result["input_consumption"]["consumed_records"]) == 61
    origin = ("archivio approvato (set %s, approvato il %s da %s, scadenza minima %s)"
              % (set_id, REVIEWED, REVIEWER, EXPIRY))
    assert sector_analysis.method_inputs_origin(bundle) == origin
    summary = sector_analysis.sector_analysis_summary(bundle)
    assert "Record del metodo: " + origin in summary
    assert "motivazioni scenario: archivio approvato (set %s)" % set_id in summary
    assert "valuation_date 2025-12-31: 253 giorni prima del cutoff 2026-09-10" in summary
    row = json.loads(sector_analysis.valuation_results_block({TICKER: result}).splitlines()[-1])
    assert row["method_inputs_origin"] == origin
    assert row["scenario_rationale_origin"] == "archivio approvato (set %s)" % set_id
    assert row["valuation_date_age_days"] == 253


def test_desk_chiama_get_valuation_col_solo_ticker(archivio, contract_tools):  # noqa: F811
    chat_tools, _, _ = contract_tools
    archivio.crea()
    archivio.approvato()
    providers = {"profile": _profile, "method_inputs": _provider()}
    result = chat_tools.dispatch("get_valuation", {"ticker": TICKER}, sector_providers=providers, as_of=DAY)["data"]
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert result["fair_value_base"] == pytest.approx(14.13)
    assert result["acquisition_snapshot"]["case"]["sources"]["method_inputs"]["source_id"] == "method_records_archive"
    assert "thesis_id" in result["_thesis_saved"], result["_thesis_saved"]


def test_coda_di_revisione_non_cambia_lo_snapshot(archivio):
    archivio.crea()
    archivio.approvato()
    first = _prepare()
    changed = operating_records()
    changed[0]["rationale"] = "Synthetic pending proposal"
    archivio.proponi(changed)
    assert _prepare()["snapshot_id"] == first["snapshot_id"]


# ------------------------------------------------------------ scadenza e metodo diverso
def test_set_scaduto_e_stale_tutto_o_niente(archivio):
    from bellomberg.valuation import sector_analysis
    archivio.crea()
    set_id = archivio.approvato()
    bundle = _prepare(as_of="2027-02-01")
    source = bundle["case"]["sources"]["method_inputs"]
    assert source["status"] == "stale" and source["records"] == []
    assert source["message"].startswith("STALE: record scaduti dal 2027-01-01, set approvato il 2026-09-10")
    assert bundle["case"]["records"] == []
    assert any(t["field"] == "method_inputs" and t["status"] == "stale" for t in bundle["acquisition_tasks"])
    assert sector_analysis.method_inputs_origin(bundle).startswith("archivio STALE")
    assert "scenario_rationale" not in bundle["analysis_context"]
    assert bundle["case"]["scenario_rationale_origin"] == "assente"
    assert "set %s" % set_id in source["message"]


def test_scadenza_uguale_al_cutoff_e_ancora_valida(archivio):
    archivio.crea()
    archivio.approvato()
    bundle = _prepare(as_of=EXPIRY)
    assert bundle["case"]["sources"]["method_inputs"]["status"] == "ok"
    assert len(bundle["case"]["records"]) == 61


def test_set_di_un_altro_metodo_non_entra(archivio):
    from bellomberg.valuation import sector_analysis
    archivio.crea()
    archivio.approvato(method_id="bank_residual_income")
    bundle = _prepare()
    source = bundle["case"]["sources"]["method_inputs"]
    assert source["status"] == "data_missing" and source["records"] == []
    assert source["message"] == ("archivio: nessun set per il metodo operating_fcff "
                                 "(set approvati per: bank_residual_income)")
    # solo i nomi spiegano il buco: i record dell'altro metodo non entrano nello snapshot
    assert source["data"] == {"method_id": "operating_fcff", "other_approved_methods": ["bank_residual_income"]}
    assert bundle["case"]["records"] == []
    assert bundle["decision"]["requirements_status"] == "incomplete"
    revised = sector_analysis.revise_sector_analysis(bundle, method_records=operating_records(),
                                                     analysis_context={"scenario_rationale": EXPLICIT_RATIONALE})
    assert revised["case"]["sources"]["method_inputs"]["data"]["previous_acquisition"] == source  # replay idempotente


def test_set_approvato_di_un_altro_metodo_non_cambia_lo_snapshot(archivio):
    """Nello snapshot entra SOLO il set del metodo deciso, datato dalla SUA approvazione:
    approvare il set di un altro metodo non cambia ne' lo snapshot_id ne' la data della fonte."""
    from bellomberg.valuation import sector_analysis
    archivio.crea()
    set_id = archivio.proponi(prepared_at="2026-09-01")
    archivio.rivedi(set_id, reviewed_at="2026-09-05")
    first = _prepare()
    other = operating_records()
    other[0]["rationale"] = "Synthetic set of another method"
    other_id = archivio.proponi(other, method_id="bank_residual_income", method_version="1", prepared_at="2026-09-06")
    archivio.rivedi(other_id, reviewed_at="2026-09-09")
    assert _provider()(TICKER, as_of=DAY)["as_of"] == "2026-09-09"  # l'archivio vede l'approvazione piu' recente
    second = _prepare()
    assert second["snapshot_id"] == first["snapshot_id"]
    for bundle in (first, second):
        source = bundle["case"]["sources"]["method_inputs"]
        assert source["status"] == "ok" and source["as_of"] == "2026-09-05", source.get("as_of")
        assert set(source["data"]) == {"sets", "method_id"} and set(source["data"]["sets"]) == {"operating_fcff"}
        assert len(bundle["case"]["records"]) == 61
        assert "set %s, approvato il 2026-09-05 da %s" % (set_id, REVIEWER) in sector_analysis.method_inputs_origin(bundle)
    revised = sector_analysis.revise_sector_analysis(second, method_records=operating_records(),
                                                     analysis_context={"scenario_rationale": EXPLICIT_RATIONALE})
    # il replay senza I/O riseleziona lo stesso envelope
    assert revised["case"]["sources"]["method_inputs"]["data"]["previous_acquisition"] == \
        second["case"]["sources"]["method_inputs"]
    tasks = _superseded(revised)
    assert len(tasks) == 1 and "set %s approvato il 2026-09-05 da %s" % (set_id, REVIEWER) in tasks[0]["reason"]


def test_versione_del_metodo_diversa_dal_catalogo_e_stale(archivio):
    archivio.crea()
    archivio.approvato(method_version="1")
    source = _prepare()["case"]["sources"]["method_inputs"]
    assert source["status"] == "stale" and source["records"] == []
    assert "versione 1" in source["message"] and "catalogo 2" in source["message"]


# ------------------------------------------------------------------------- sostituzione
def _superseded(bundle):
    return [t for t in bundle["acquisition_tasks"] if t.get("status") == "superseded"]


def test_set_esplicito_sostituisce_l_archivio_anche_alla_seconda_revisione(archivio, tmp_path):
    from bellomberg.valuation import dcf_engine, sector_analysis
    archivio.crea()
    set_id = archivio.approvato()
    bundle = _prepare()
    context = {"scenario_rationale": EXPLICIT_RATIONALE}
    first = sector_analysis.revise_sector_analysis(bundle, method_records=operating_records(), analysis_context=context)
    second = sector_analysis.revise_sector_analysis(first, method_records=operating_records(), analysis_context=context)
    for revised in (first, second):
        assert len(revised["case"]["records"]) == 61
        source = revised["case"]["sources"]["method_inputs"]
        assert source["source_id"] == "explicit_method_records"
        assert source["data"]["provider_records"] == []
        tasks = _superseded(revised)
        assert len(tasks) == 1, revised["acquisition_tasks"]
        assert tasks[0]["blocking"] is False and tasks[0]["field"] == "method_inputs"
        assert "set %s approvato il %s da %s" % (set_id, REVIEWED, REVIEWER) in tasks[0]["reason"]
        assert sector_analysis.method_inputs_origin(revised) == "esplicito NON approvato"
        assert revised["case"]["scenario_rationale_origin"] == "analysis_context"
    assert first["case"]["sources"]["method_inputs"]["data"]["previous_acquisition"]["source_id"] == "method_records_archive"
    result = dcf_engine.generate_valuation(TICKER, prepared_bundle=second, output_dir=str(tmp_path))
    assert result["valuation_usability"]["usable"], result["valuation_usability"]
    assert len(result["input_consumption"]["consumed_records"]) == 61


def test_set_esplicito_nella_stessa_acquisizione_sostituisce(archivio):
    archivio.crea()
    archivio.approvato()
    bundle = _prepare(user_context={"method_records": operating_records(),
                                    "analysis_context": {"scenario_rationale": EXPLICIT_RATIONALE}})
    assert len(bundle["case"]["records"]) == 61
    assert len(_superseded(bundle)) == 1


@pytest.mark.parametrize("state,fragment", [
    ("stale", "archivio STALE"),
    ("assente", "archivio senza set approvato valido (data_missing): nessun set approvato per SYNTH-EXT"),
    ("db_assente", "archivio non letto (source_error): archivio record non letto: DB del progetto assente"),
])
def test_sostituzione_dichiara_archivio_stale_o_assente(archivio, state, fragment):
    if state != "db_assente":
        archivio.crea()
    cutoff = DAY
    if state == "stale":
        archivio.approvato()
        cutoff = "2027-02-01"
    records = operating_records()
    for row in records:
        row["valid_until"] = "2028-01-01"
    bundle = _prepare(as_of=cutoff, user_context={"method_records": records})
    tasks = _superseded(bundle)
    assert len(tasks) == 1 and fragment in tasks[0]["reason"], bundle["acquisition_tasks"]
    if state == "assente":
        # l'archivio e' stato letto: manca il set, non la lettura
        assert "non letto" not in tasks[0]["reason"], tasks[0]["reason"]
    assert len(bundle["case"]["records"]) == 61


def test_sostituzione_dichiara_l_envelope_d_errore_di_acquire(archivio):
    """Il lettore che solleva (RuntimeError dello store, non catturato dal provider) produce
    l'envelope d'errore di _acquire: anche quello resta dichiarato nel task di sostituzione."""
    from bellomberg.valuation import sector_analysis

    def rotto(ticker, *, as_of):
        raise RuntimeError("synthetic reader failure")

    bundle = sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY,
        providers={"profile": _profile, "method_inputs": rotto},
        user_context={"method_records": operating_records(),
                      "analysis_context": {"scenario_rationale": EXPLICIT_RATIONALE}})
    previous = bundle["case"]["sources"]["method_inputs"]["data"]["previous_acquisition"]
    assert previous["source_id"] == "method_inputs" and previous["status"] == "source_error", previous
    tasks = _superseded(bundle)
    assert len(tasks) == 1, bundle["acquisition_tasks"]
    assert "(source_error)" in tasks[0]["reason"] and "RuntimeError" in tasks[0]["reason"], tasks[0]["reason"]
    assert "archivio non letto (source_error): RuntimeError: synthetic reader failure" in tasks[0]["reason"]
    assert len(bundle["case"]["records"]) == 61


# ------------------------------------------------------------------------- motivazioni
def test_motivazioni_esplicite_vincono_e_l_origine_lo_dice(archivio):
    archivio.crea()
    archivio.approvato()
    bundle = _prepare(user_context={"analysis_context": {"scenario_rationale": EXPLICIT_RATIONALE}})
    assert bundle["analysis_context"]["scenario_rationale"] == EXPLICIT_RATIONALE
    assert bundle["case"]["scenario_rationale_origin"] == "analysis_context"


def test_revisione_del_solo_contesto_riadotta_le_motivazioni_dell_archivio(archivio):
    from bellomberg.valuation import sector_analysis
    archivio.crea()
    set_id = archivio.approvato()
    revised = sector_analysis.revise_sector_analysis(_prepare(), analysis_context={"revisions": []})
    assert revised["analysis_context"]["scenario_rationale"] == RATIONALE
    assert revised["case"]["scenario_rationale_origin"] == "archivio approvato (set %s)" % set_id
    sector_analysis.validate_bundle(revised, TICKER)


def test_motivazioni_dell_archivio_non_passano_a_un_set_esplicito(archivio, tmp_path):
    from bellomberg.valuation import dcf_engine, sector_analysis
    archivio.crea()
    archivio.approvato()
    revised = sector_analysis.revise_sector_analysis(_prepare(), method_records=operating_records())
    assert "scenario_rationale" not in revised["analysis_context"]
    assert revised["case"]["scenario_rationale_origin"] == "assente"
    result = dcf_engine.generate_valuation(TICKER, prepared_bundle=revised, output_dir=str(tmp_path))
    assert not result["valuation_usability"]["usable"]
    assert result.get("fair_value_base") is None


def test_testi_del_tool_e_del_desk_dicono_che_l_archivio_arriva_da_solo():
    from bellomberg.agents import chat_tools
    from bellomberg.agents.specialists.fundamentals import FundamentalsSpecialist
    tool = next(t for t in chat_tools.TOOL_DEFINITIONS if t["name"] == "get_valuation")
    schema = tool["input_schema"]["properties"]["method_records"]["description"]
    assert "NON passare method_records" in schema and "PROPOSTA NON approvata" in schema
    assert "col SOLO ticker" in tool["description"] and "PROPOSTA NON approvata" in tool["description"]
    prompt = FundamentalsSpecialist.system_prompt
    assert "method_inputs_origin" in prompt and "PROPOSTA NON approvata" in prompt
    assert "NON ripassare method_records" in prompt


def test_origine_nessuno_e_blocco_senza_snapshot():
    from bellomberg.valuation import sector_analysis
    bundle = sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY, providers={"profile": _profile})
    assert sector_analysis.method_inputs_origin(bundle).startswith("nessuno")
    row = json.loads(sector_analysis.valuation_results_block({TICKER: {"fair_value_base": 1}}).splitlines()[-1])
    assert row["method_inputs_origin"].startswith("n.d.")
    assert row["valuation_date_age_days"] is None


@pytest.mark.parametrize("failure", [None, "data non ISO"])
def test_lettura_chiude_la_connessione_e_dichiara_la_causa(archivio, monkeypatch, failure):
    archivio.crea()
    archivio.approvato()
    connections = []
    original = archivio.store.apri_lettura

    def track(path):
        connection = original(path)
        connections.append(connection)
        return connection

    monkeypatch.setattr(archivio.store, "apri_lettura", track)
    if failure:
        def fail(*args):
            raise archivio.store.SetAlterato(failure)
        monkeypatch.setattr(archivio.store, "leggi_approvati", fail)
    result = _provider()(TICKER, as_of=DAY)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    if failure:
        assert result["status"] == "source_error" and failure in result["message"]
        assert "sha256 discordante" not in result["message"]
    else:
        assert result["status"] == "ok"
