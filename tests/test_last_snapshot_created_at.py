"""F43 (3) — `last_snapshot_created_at` FRA LE CHIAVI CHE IL FRONTEND CONSUMA
(31/08, Fable 5, chat backend; ordine deciso dal PM il 25/08).

Il fatto: la chiave esiste dall'11/06 solo DENTRO `reconciliation`
(twr_engine.py `build_recon_note`). F1/F2 RENDONO quell'oggetto dal 23/07
(pannello riconciliazione) — ma il punto che scrive «IN CORSO» consuma le
chiavi top-level (dates/twr_index/values_eur/regimes/regime_summary/
external_flows): la richiesta F43 (3) e' la copia fra quelle (review 31/08:
la prima stesura diceva «il frontend non legge reconciliation», falso).

La cura: chiave ADDITIVA top-level nel payload di `compute_twr_payload` =
`created_at` dell'ULTIMO snapshot, `null` dichiarato senza snapshot;
`reconciliation` resta identica. Base oraria: LOCALE con la T (la scrive
`record_nav_snapshot` con `datetime.now()`), NON UTC.

Si prova il CABLAGGIO (`compute_twr_payload` vero, non un helper): stub
dichiarati per `get_official_series` (niente rete/DB), `MemoryDB` (summary
finto) e `_build_irr_flows` (fuori perimetro). Zero rete, zero DB vero.
"""
import pytest

import bellomberg.portfolio.twr_engine as te


def _ctx(snaps):
    return {
        "dates": ["2026-08-28", "2026-08-29", "2026-08-30"],
        "values_eur": [100000.0, 100500.0, 101000.0],
        "flows_eur": [0.0, 0.0, 0.0],
        "regimes": ["official", "official", "official"],
        "notes": [],
        "snapshots": snaps,
        "official_since": "2026-08-28",
        "seamless_transition": False,
        "ledger": [],
    }


class _DBStub:
    db_path = "Z:\\percorso\\inesistente\\niente.db"   # il ramo no-snaps prova la tabella: except -> False

    def get_portfolio_summary(self):
        return {"nav_total_eur": 101000.0}


def _payload(monkeypatch, snaps):
    monkeypatch.setattr(te, "get_official_series", lambda: _ctx(snaps))
    monkeypatch.setattr(te, "MemoryDB", lambda: _DBStub())
    monkeypatch.setattr(te, "_build_irr_flows", lambda ctx, live: ([], 0.0, "stub test"))
    # review 31/08: senza questo stub «zero rete» era una garanzia FALSA —
    # compute_twr_payload importa get_risk_free DENTRO la funzione e quello fa
    # una GET viva (Bundesbank, timeout 30 s) col conforto del fallback statico
    monkeypatch.setattr('bellomberg.market_data.market_inputs.get_risk_free', lambda c="EUR": 0.03)
    te._CACHE.clear()
    return te.compute_twr_payload(force=True)


def _snap(d, nav, created):
    s = {"date": d, "nav_total_eur": nav, "invested_eur": nav, "cash_eur": 0.0,
         "source": "test"}
    if created is not None:
        s["created_at"] = created
    return s


def test_la_chiave_sta_al_livello_alto_e_viene_dall_ultimo_snapshot(monkeypatch):
    snaps = [_snap("2026-08-29", 100500.0, "2026-08-29T17:00:00"),
             _snap("2026-08-30", 101000.0, "2026-08-30T15:59:20")]
    p = _payload(monkeypatch, snaps)
    assert p["last_snapshot_created_at"] == "2026-08-30T15:59:20"


def test_senza_snapshot_la_chiave_e_presente_e_nulla(monkeypatch):
    p = _payload(monkeypatch, [])
    assert "last_snapshot_created_at" in p
    assert p["last_snapshot_created_at"] is None
    assert p["reconciliation"] is None          # com'era: nessun breach inventato


def test_snapshot_senza_created_at_e_null_non_un_crash(monkeypatch):
    p = _payload(monkeypatch, [_snap("2026-08-30", 101000.0, None)])
    assert p["last_snapshot_created_at"] is None


def test_reconciliation_resta_identica(monkeypatch):
    """Additiva: il contratto vecchio (F2 legge reconciliation dal 23/07) non si tocca."""
    snaps = [_snap("2026-08-30", 101000.0, "2026-08-30T15:59:20")]
    p = _payload(monkeypatch, snaps)
    r = p["reconciliation"]
    assert r["last_snapshot_created_at"] == "2026-08-30T15:59:20"
    for k in ("nav_live_eur", "last_snapshot_date", "last_snapshot_nav_eur",
              "delta_pct", "tolerance_pct", "breach", "note"):
        assert k in r
