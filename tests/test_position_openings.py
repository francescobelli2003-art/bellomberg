"""Documented opening balances, never invented purchases/cash or acquisition dates."""
from datetime import date, datetime, timedelta

import pytest

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self: None)
    return MemoryDB(db_path=str(tmp_path / "opening.db"), chroma_path=str(tmp_path / "chroma"))


@pytest.fixture
def api(db, monkeypatch):
    from bellomberg.api import bellomberg_api as api
    from bellomberg.portfolio import portfolio_analytics as pa, twr_engine as twr
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "_trade_fx", lambda day, currency: {
        "tasso": 1.0 if currency == "EUR" else 0.8,
        "fonte": "identity" if currency == "EUR" else "storico", "data": day, "nota": None})
    monkeypatch.setattr(pa, "MemoryDB", lambda: db)
    monkeypatch.setattr(twr, "MemoryDB", lambda: db)
    monkeypatch.setattr(twr, "SQLITE_PATH", db.db_path)
    monkeypatch.setattr(pa, "_build_fx_history", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no FX network")))
    twr.invalidate_cache()
    return api


def body(api, **changes):
    data = dict(ticker="SYNTH.MI", quantita=10, prezzo_medio=20, valuta="EUR",
                as_of="2024-01-31", provenienza="Estratto broker sintetico", nota="Saldo noto, acquisti ignoti")
    data.update(changes)
    return api.OpeningPositionIn(**data)


def create(api, **changes):
    request = body(api, **changes)
    preview = api.preview_opening_position(request)
    return api.create_opening_position(request.model_copy(update={"preview_id": preview["preview_id"]}))


def state(db):
    with db._conn() as conn:
        return {table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)]
                for table in ("positions", "position_openings", "trade_history", "cash_state",
                              "cash_movements", "nav_snapshots")}


def position(db, ticker="SYNTH.MI"):
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM positions WHERE ticker=?", (ticker,)).fetchone()
    return dict(row) if row else None


def trade(api, **changes):
    request = dict(ticker="SYNTH.MI", action="SELL", quantita=4, prezzo=30, valuta="EUR",
                   data="2024-02-10", senza_decisione=True)
    request.update(changes)
    return api.log_trade(api.TradeIn(**request))


def test_additive_migration_is_empty_and_idempotent(db):
    assert db.get_opening_positions() == []
    MemoryDB(db_path=db.db_path, chroma_path=db.chroma_path)
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=11").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM position_openings").fetchone()[0] == 0


def test_preview_is_read_only_and_creation_needs_its_token(api, db):
    before = state(db)
    preview = api.preview_opening_position(body(api))
    assert state(db) == before
    assert preview["cash_delta_eur"] == 0
    assert preview["cash_disponibile_eur"] is None
    assert preview["opening"]["as_of"] == "2024-01-31"
    assert preview["opening"]["precisione_data"] == "day"
    with pytest.raises(api.HTTPException) as error:
        api.create_opening_position(body(api))
    assert error.value.status_code == 409
    assert state(db) == before


def test_creation_changes_only_position_and_documented_balance(api, db):
    before = state(db)
    result = create(api)
    after = state(db)
    for table in ("trade_history", "cash_state", "cash_movements", "nav_snapshots"):
        assert after[table] == before[table]
    assert position(db)["quantita"] == 10
    assert position(db)["prezzo_medio"] == 20
    assert position(db)["data_apertura"] is None
    saved = api.get_opening_position("SYNTH.MI")["opening"]
    assert saved["id"] == result["opening"]["id"]
    assert saved["as_of"] == "2024-01-31"
    assert saved["provenienza"] == "Estratto broker sintetico"
    assert saved["created_at"].endswith("+00:00")
    assert api.list_opening_positions()["openings"] == [saved]


@pytest.mark.parametrize("change", [{"quantita": 0}, {"quantita": -1}, {"prezzo_medio": -1},
    {"quantita": float("inf")}, {"prezzo_medio": float("nan")}, {"provenienza": " "},
    {"valuta": ""}, {"ticker": " "}, {"as_of": ""}, {"as_of": "2024-02-30"},
    {"as_of": "1999-01-01"}, {"as_of": "2999-01-01"}])
def test_invalid_opening_never_writes(api, db, change):
    before = state(db)
    with pytest.raises((api.HTTPException, ValueError)):
        api.preview_opening_position(body(api, **change))
    assert state(db) == before


def test_zero_documented_cost_and_foreign_balance_require_no_fx(api, db):
    create(api, ticker="SYNTH.US", prezzo_medio=0, valuta="USD")
    assert position(db, "SYNTH.US")["prezzo_medio"] == 0
    assert db.get_opening_positions()[0]["valuta"] == "USD"
    assert state(db)["trade_history"] == []


@pytest.mark.parametrize("existing", ["position", "trade", "opening"])
def test_existing_history_or_position_cannot_be_retrofitted(api, db, existing):
    if existing == "position":
        db.add_or_update_position("SYNTH.MI", quantita=10, prezzo_medio=20, valuta="EUR")
    elif existing == "trade":
        db.log_trade("SYNTH.MI", "BUY", 10, 20, valuta="EUR", data="2024-01-10")
    else:
        create(api)
    before = state(db)
    with pytest.raises(api.HTTPException) as error:
        api.preview_opening_position(body(api))
    assert error.value.status_code == 409
    assert state(db) == before


@pytest.mark.parametrize("drift", ["body", "position", "cash", "expired", "used"])
def test_preview_drift_and_replay_tokens_refuse_atomically(api, db, monkeypatch, drift):
    request = body(api)
    preview = api.preview_opening_position(request)
    confirmed = request.model_copy(update={"preview_id": preview["preview_id"]})
    if drift == "body": confirmed = confirmed.model_copy(update={"quantita": 11})
    if drift == "position": db.add_or_update_position("SYNTH.MI", quantita=2, prezzo_medio=5)
    if drift == "cash": db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    if drift == "expired": monkeypatch.setattr(api.time, "monotonic", lambda: 10**15)
    if drift == "used": api.create_opening_position(confirmed)
    before = state(db)
    with pytest.raises(api.HTTPException) as error:
        api.create_opening_position(confirmed)
    assert error.value.status_code == 409
    assert state(db) == before


def test_sell_and_backdated_add_replay_from_known_balance(api, db):
    create(api)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    sell = trade(api)  # 4 * (30 - 20) = 40; qty 6, cash 1120.
    assert position(db)["quantita"] == 6
    assert sell["cash_disponibile_eur"] == 1120
    added = trade(api, action="ADD", quantita=10, prezzo=40, data="2024-02-05")
    assert position(db)["quantita"] == 16
    assert position(db)["prezzo_medio"] == 30
    assert position(db)["data_apertura"] is None
    assert added["cash_disponibile_eur"] == 720
    with db._conn() as conn:
        sell_row = conn.execute("SELECT realized_local,realized_eur FROM trade_history WHERE id=?", (sell["trade_id"],)).fetchone()
    assert tuple(sell_row) == (0, 0)
    assert added["ricalcolo"]["trade_successivi"] == [sell["trade_id"]]
    assert added["ricalcolo"]["baseline"]["as_of"] == "2024-01-31"


@pytest.mark.parametrize("when", ["2024-01-30", "2024-01-31T23:59:00", "2024-01-31"])
def test_trade_before_or_ambiguous_with_day_balance_refuses(api, db, when):
    create(api)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    before = state(db)
    with pytest.raises(api.HTTPException) as error:
        trade(api, action="BUY", data=when)
    assert error.value.status_code == 409
    assert state(db) == before


def test_known_intraday_balance_allows_later_measured_trade(api, db):
    create(api, as_of="2024-01-31T10:00:00")
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    result = trade(api, data="2024-01-31T10:01:00")
    assert result["cash_disponibile_eur"] == 1120
    assert position(db)["quantita"] == 6


def test_direct_import_path_also_preserves_baseline_and_rejects_earlier_rows(api, db):
    create(api)
    db.log_trade("SYNTH.MI", "SELL", 4, 30, valuta="EUR", data="2024-02-10")
    db.log_trade("SYNTH.MI", "ADD", 10, 40, valuta="EUR", data="2024-02-05")
    assert position(db)["quantita"] == 16
    assert position(db)["prezzo_medio"] == 30
    before = state(db)
    with pytest.raises(memory_db.RicalcoloImpossibile):
        db.log_trade("SYNTH.MI", "BUY", 1, 10, valuta="EUR", data="2024-01-30")
    assert state(db) == before


def test_opening_is_transactional_if_position_insert_fails(api, db):
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_position BEFORE INSERT ON positions BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    before = state(db)
    with pytest.raises(api.HTTPException): create(api)
    assert state(db) == before


def test_documented_native_eur_cost_is_known_foreign_acquisition_fx_is_unknown(api, db):
    from bellomberg.portfolio.portfolio_analytics import pl_fx_per_posizione
    create(api)
    create(api, ticker="SYNTH.US", valuta="USD")
    result = pl_fx_per_posizione([], "2024-02-20", openings=db.get_opening_positions())
    assert result["per_ticker"]["SYNTH.MI"]["costo_eur_storico"] == 200
    assert result["per_ticker"]["SYNTH.US"]["costo_eur_storico"] is None
    assert result["per_ticker"]["SYNTH.US"]["costo_nativo"] == 200
    assert "acquisto" in " ".join(result["per_ticker"]["SYNTH.US"]["note"])


def snapshots(db):
    with db._conn() as conn:
        for day, nav in [("2024-02-04", 1000), ("2024-02-06", 1100), ("2024-02-08", 1200), ("2024-02-09", 1260)]:
            conn.execute("INSERT INTO nav_snapshots(date,nav_total_eur,invested_eur,cash_eur,source,created_at) VALUES (?,?,?,?,?,?)",
                         (day, nav, nav-100, 100, "price_updater", day + "T20:00:00+00:00"))


def test_performance_starts_after_latest_baseline_registration_not_as_of(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr, portfolio_analytics as pa
    create(api, ticker="FIRST.MI", as_of="2024-01-01")
    create(api, ticker="SECOND.MI", as_of="2024-01-03")
    with db._conn() as conn:
        conn.execute("UPDATE position_openings SET created_at='2024-02-05T10:00:00+00:00' WHERE ticker='FIRST.MI'")
        conn.execute("UPDATE position_openings SET created_at='2024-02-07T10:00:00+00:00' WHERE ticker='SECOND.MI'")
    snapshots(db)
    monkeypatch.setattr(twr, "_load_snapshots", lambda: _load_snaps(db))
    monkeypatch.setattr(twr, "get_cash_movements", lambda: [])
    monkeypatch.setattr(pa, "compute_nav_history", lambda: (_ for _ in ()).throw(AssertionError("no fabricated reconstruction")))
    result = twr.get_official_series()
    assert result["dates"] == ["2024-02-08", "2024-02-09"]
    assert result["values_eur"] == [1200, 1260]
    assert result["official_since"] == "2024-02-08"
    coverage = twr._performance_coverage(db, result)
    assert coverage["baseline_added_at"] == "2024-02-07T10:00:00+00:00"
    assert coverage["snapshot_prima_del_baseline"] == 2
    flows, terminal, basis = twr._build_irr_flows(result, {"nav_total_eur": 999999})
    assert flows == [("2024-02-08", -1200)]
    assert terminal == 1260
    assert "2024-02-08" in basis and "2024-02-09" in basis


def _load_snaps(db):
    with db._conn() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM nav_snapshots ORDER BY date")]


def test_reconstructed_nav_refuses_partial_trade_only_history_with_baseline(api, db):
    from bellomberg.portfolio import portfolio_analytics as pa
    create(api)
    result = pa.compute_nav_history(force=True)
    assert result["error_code"] == "OPENING_HISTORY_INCOMPLETE"
    assert result["position_openings"][0]["as_of"] == "2024-01-31"
    assert "acquisti" in result["error"]


def test_snapshot_racing_opening_does_not_certify_old_nav(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr
    from datetime import timezone
    clock = {"after": False}
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 2, 7, 11 if clock["after"] else 9, tzinfo=timezone.utc)
    monkeypatch.setattr(twr, "datetime", FrozenClock)
    def old_summary():
        create(api)  # balance commits after the snapshot valuation started
        with db._conn() as conn:
            conn.execute("UPDATE position_openings SET created_at='2024-02-07T10:00:00+00:00'")
        clock["after"] = True
        return {"nav_total_eur": 1000, "totale_valore_mercato_eur": 0,
                "cash_disponibile_eur": 1000, "cash_source": "sqlite:cash_state"}
    monkeypatch.setattr(db, "get_portfolio_summary", old_summary)
    assert twr.record_nav_snapshot(db)["ok"] is True
    monkeypatch.setattr(twr, "_load_snapshots", lambda: _load_snaps(db))
    monkeypatch.setattr(twr, "get_cash_movements", lambda: [])
    result = twr.get_official_series()
    assert result["snapshots"] == []
    assert result["snapshot_prima_del_baseline"] == 1


def test_foreign_baseline_does_not_hide_known_eur_when_later_fx_unavailable(api, db):
    from bellomberg.portfolio import portfolio_analytics as pa
    create(api)
    create(api, ticker="SYNTH.US", valuta="USD")
    rows = [{"ticker": "SYNTH.US", "action": "ADD", "quantita": 1, "prezzo": 20,
             "valuta": "USD", "data": "2024-02-01"}]
    result = pa.pl_fx_per_posizione(rows, "2024-02-20", openings=db.get_opening_positions())
    assert result["per_ticker"]["SYNTH.MI"]["costo_eur_storico"] == 200
    assert result["per_ticker"]["SYNTH.US"]["costo_eur_storico"] is None
    assert result["per_ticker"]["SYNTH.US"]["costo_nativo"] == 220


@pytest.mark.parametrize("field,value", [("quantita", 12), ("prezzo_medio", 33), ("valuta", "USD")])
def test_modified_balance_position_refuses_replay(api, db, field, value):
    create(api)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    db.add_or_update_position("SYNTH.MI", **{field: value})
    before = state(db)
    with pytest.raises(api.HTTPException): trade(api)
    assert state(db) == before


def test_closed_baseline_reopened_by_real_buy_knows_only_new_acquisition_date(api, db):
    create(api)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    trade(api, quantita=10)
    trade(api, action="BUY", quantita=2, prezzo=40, data="2024-02-12T10:00:00")
    assert position(db)["data_apertura"] == "2024-02-12T10:00:00"
    assert position(db)["quantita"] == 2
    assert position(db)["prezzo_medio"] == 40
    assert db.get_opening_positions()[0]["as_of"] == "2024-01-31"


def test_snapshot_missing_price_with_opening_is_not_official(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr
    create(api)
    monkeypatch.setattr(db, "get_portfolio_summary", lambda: {
        "nav_total_eur": 200, "totale_valore_mercato_eur": 200,
        "cash_disponibile_eur": 0, "cash_source": "sqlite:cash_state",
        "stale_positions": ["SYNTH.MI"]})
    before = state(db)
    out = twr.record_nav_snapshot(db)
    assert out["ok"] is False and "prezzi" in out["reason"]
    assert state(db) == before


def test_snapshot_irr_uses_only_external_flows_between_covered_endpoints(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr
    create(api)
    with db._conn() as conn:
        conn.execute("UPDATE position_openings SET created_at='2024-02-07T10:00:00+00:00'")
    snapshots(db)
    monkeypatch.setattr(twr, "_load_snapshots", lambda: _load_snaps(db))
    monkeypatch.setattr(twr, "get_cash_movements", lambda: [
        {"date": "2024-02-01", "type": "DEPOSIT", "amount_eur": 1000},
        {"date": "2024-02-08", "type": "DEPOSIT", "amount_eur": 50},
        {"date": "2024-02-09", "type": "WITHDRAWAL", "amount_eur": 10},
        {"date": "2024-02-10", "type": "DEPOSIT", "amount_eur": 999}])
    ctx = twr.get_official_series()
    assert ctx["flows_eur"] == [0, -10]
    flows, terminal, _ = twr._build_irr_flows(ctx, {})
    assert flows == [("2024-02-08", -1200), ("2024-02-09", 10)]
    assert terminal == 1260
    assert twr.compute_twr(ctx["values_eur"], ctx["flows_eur"]) == pytest.approx([70/1200])


def test_zero_eur_documented_cost_is_visible_in_summary_with_measured_price(api, db, monkeypatch):
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_sources", lambda: {})
    create(api, prezzo_medio=0)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    db.update_price("SYNTH.MI", 30, valuta="EUR")
    summary = db.get_portfolio_summary()
    row = summary["positions"][0]
    assert row["pl_eur"] == 300
    assert row["pl_eur_fx"] == 300
    assert row["costo_eur_storico"] == 0
    assert row["pl_pct_fx"] is None
    assert row["data_apertura"] is None
    assert row["position_opening"]["as_of"] == "2024-01-31"
    assert "Saldi iniziali" in summary["totale_aperto_piu_realizzato_note"]


def test_cost_overflow_cannot_be_registered(api, db):
    before = state(db)
    with pytest.raises(api.HTTPException):
        api.preview_opening_position(body(api, quantita=1e308, prezzo_medio=1e308))
    assert state(db) == before


def test_trade_replay_failure_rolls_back_trade_cash_and_previous_sales(api, db):
    create(api)
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    trade(api)
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_realized BEFORE UPDATE OF realized_local ON trade_history BEGIN SELECT RAISE(ABORT,'synthetic replay failure'); END")
    before = state(db)
    with pytest.raises(api.HTTPException):
        trade(api, action="ADD", quantita=10, prezzo=40, data="2024-02-05")
    assert state(db) == before


def test_cache_failure_after_commit_is_reported_as_success(api, db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr
    monkeypatch.setattr(twr, "invalidate_cache", lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
    result = create(api)
    assert result["ok"] is True
    assert result["opening"]["id"] == db.get_opening_positions()[0]["id"]
    assert "Cache performance non aggiornata" in result["performance_note"]


def test_foreign_opening_realized_eur_is_unknown_until_old_pool_closes(api, db):
    create(api, ticker="SYNTH.US", valuta="USD")
    db.apply_cash_movement("DEPOSIT", 1000, data="2024-01-01")
    sell = trade(api, ticker="SYNTH.US", valuta="USD", quantita=10)
    with db._conn() as conn:
        row = conn.execute("SELECT realized_local,realized_eur FROM trade_history WHERE id=?", (sell["trade_id"],)).fetchone()
    assert tuple(row) == (100, None)
    assert any("acquisto" in note for note in sell["ricalcolo"]["note"])
    trade(api, ticker="SYNTH.US", valuta="USD", action="BUY", quantita=2, prezzo=40, data="2024-02-12")
    new_sell = trade(api, ticker="SYNTH.US", valuta="USD", quantita=2, prezzo=50, data="2024-02-13")
    with db._conn() as conn:
        row = conn.execute("SELECT realized_local,realized_eur FROM trade_history WHERE id=?", (new_sell["trade_id"],)).fetchone()
    assert tuple(row) == (20, 16)


def test_foreign_opening_closed_pool_does_not_label_new_known_cost_unknown(api, db):
    from bellomberg.portfolio import portfolio_analytics as pa
    create(api, ticker="SYNTH.US", valuta="USD")
    rows = [{"ticker": "SYNTH.US", "action": "SELL", "quantita": 10, "prezzo": 30,
             "valuta": "USD", "data": "2024-02-10"},
            {"ticker": "SYNTH.US", "action": "BUY", "quantita": 2, "prezzo": 40,
             "valuta": "USD", "data": "2024-02-12"}]
    result = pa.costo_storico_per_ticker(rows, lambda *_: (0.8, "2024-02-12", None), db.get_opening_positions())
    assert result["SYNTH.US"]["costo_eur_storico"] == 64
    assert not any("costo storico EUR n.d." in note for note in result["SYNTH.US"]["note"])
