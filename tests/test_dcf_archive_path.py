"""Archive routing uses the private canonical directory; no valuation/provider calls."""
from pathlib import Path


def test_superseded_model_and_payload_use_canonical_private_archive(tmp_path, monkeypatch):
    from bellomberg.valuation import dcf_engine

    monkeypatch.setattr(dcf_engine, 'PROJECT_ROOT', tmp_path)
    report = tmp_path / 'report'
    report.mkdir()
    workbook = report / 'VAL_DEMO_FLAGGED.xlsx'
    payload = report / 'VAL_DEMO_FLAGGED.payload.json'
    workbook.write_bytes(b'synthetic workbook')
    payload.write_bytes(b'{"fixture":true}')
    old_root = tmp_path / 'attic/val_superseded'
    old_root.mkdir(parents=True)
    historical = old_root / workbook.name
    historical.write_bytes(b'preserve older root history')

    result = dcf_engine._prepare_canonical_path(str(report), 'DEMO')

    canonical = tmp_path / 'archive/private/attic/val_superseded'
    assert Path(result) == report / 'VAL_DEMO.xlsx'
    assert (canonical / workbook.name).read_bytes() == b'synthetic workbook'
    assert (canonical / payload.name).read_bytes() == b'{"fixture":true}'
    assert historical.read_bytes() == b'preserve older root history'
    assert not workbook.exists() and not payload.exists()
