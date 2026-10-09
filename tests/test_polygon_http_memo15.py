"""R16: risposte HTTP sintetiche, chiavi inventate e rete contata."""
from types import SimpleNamespace
import pytest
from bellomberg.market_data import polygon_data, iv_history
from bellomberg.portfolio import vol_surface

KEY = "SYNTHETIC_SECRET_ALPHA_BETA"
ENCODED = "SYNTH%2BENCODED%2FSECRET"

@pytest.fixture
def http_fake(monkeypatch):
    calls = []
    def arm(status, body, payload=None):
        def get(url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(status_code=status, text=body, json=lambda: payload)
        monkeypatch.setattr(polygon_data.requests, "get", get)
    monkeypatch.setattr(polygon_data, "POLYGON_KEY", KEY)
    monkeypatch.setattr(polygon_data, "REQ_OK", True)
    vol_surface._CHAIN_CACHE.clear()
    return arm, calls

def clean(value):
    text = repr(value)
    assert KEY[:10] not in text
    assert ENCODED not in text

@pytest.mark.parametrize("status", [403, 429, 503])
@pytest.mark.parametrize("body", [KEY, 'apiKey=' + ENCODED,
    '/v3/test?apiKey=' + ENCODED + '&cursor=abc',
    '{"key":"' + KEY + '"}', 'WAF echo ' + KEY,
    'x'*110 + KEY, 'x'*190 + KEY])
def test_http_body_e_log_senza_segreti(http_fake, capsys, status, body):
    arm, calls = http_fake
    arm(status, body)
    out = polygon_data._get("/v3/test")
    log = capsys.readouterr().out
    assert out["error"] == f"HTTP {status}"
    assert len(calls) == 1
    assert calls[0][1]["params"]["apiKey"] == KEY
    assert len(log.splitlines()) == 1
    assert f"[POLYGON] HTTP {status} on /v3/test:" in log
    clean(out)
    clean(log)

@pytest.mark.parametrize("consumer,count", [("expirations", 1), ("chain", 1),
    ("catalog", 1), ("detail", 1), ("surface", 1), ("history", 1)])
def test_http_consumatori_reali(http_fake, capsys, tmp_path, consumer, count):
    arm, calls = http_fake
    arm(429, 'x'*110 + KEY + ' apiKey=' + ENCODED)
    funcs = {
        "expirations": lambda: polygon_data.get_option_expirations("ZZTEST"),
        "chain": lambda: polygon_data.get_options_chain("ZZTEST", "2099-01-16"),
        "catalog": lambda: vol_surface.get_expiry_catalog("ZZTEST"),
        "detail": lambda: vol_surface.get_chain_detail("ZZTEST", "2099-01-16"),
        "surface": lambda: vol_surface.build_vol_surface("ZZTEST", include_context=False),
        "history": lambda: iv_history.save_daily_snapshot(db_path=str(tmp_path / "iv.db"),
            tickers=["ZZTEST"], snap_date="2099-01-14", force=True),
    }
    out = funcs[consumer]()
    assert len(calls) == count
    assert "HTTP 429" in repr(out)
    clean(out)
    log = capsys.readouterr().out
    assert log.count("[POLYGON] HTTP 429") == count
    clean(log)

@pytest.mark.parametrize("transport_error", [False, True])
def test_path_output_sicuro_richiesta_intatta(http_fake, capsys, monkeypatch, transport_error):
    arm, calls = http_fake
    arm(503, "unavailable")
    url = polygon_data.BASE + "/v3/" + KEY + "?apiKey=" + ENCODED
    if transport_error:
        def broken(url, **kwargs):
            calls.append((url, kwargs))
            raise ConnectionError("sensitive " + KEY)
        monkeypatch.setattr(polygon_data.requests, "get", broken)
    out = polygon_data._get(url)
    assert calls[0][0] == url
    assert len(calls) == 1
    clean(out)
    clean(capsys.readouterr().out)

@pytest.mark.parametrize("status", [403, 429, 503])
def test_body_innocuo_e_log_monoriga(http_fake, capsys, status):
    arm, calls = http_fake
    body = "rate limited\nretry later " + "x"*230
    arm(status, body)
    out = polygon_data._get("/v3/test")
    assert out == {"error": f"HTTP {status}", "_body": body[:200]}
    assert capsys.readouterr().out == f"  [POLYGON] HTTP {status} on /v3/test: " + " ".join(body[:120].split()) + "\n"
    assert len(calls) == 1

def test_200_invariato(http_fake, capsys):
    arm, calls = http_fake
    payload = {"results": [{"synthetic": KEY}]}
    arm(200, KEY, payload)
    assert polygon_data._get("/v3/test") is payload
    assert not capsys.readouterr().out
    assert len(calls) == 1

@pytest.mark.parametrize("exc", [BrokenPipeError, ValueError, UnicodeEncodeError])
def test_stdout_rotto_preserva_http(http_fake, monkeypatch, exc):
    arm, calls = http_fake
    arm(429, KEY)
    def broken(*args, **kwargs):
        if exc is UnicodeEncodeError:
            raise exc("ascii", "x", 0, 1, "synthetic")
        raise exc("synthetic")
    monkeypatch.setattr("builtins.print", broken)
    out = polygon_data._get("/v3/test")
    assert out["error"] == "HTTP 429"
    clean(out)
    assert len(calls) == 1


@pytest.mark.parametrize('status', [401, 403, 429])
def test_summary_quote_unavailable_on_http_error(http_fake, status):
    arm, calls = http_fake
    arm(status, 'synthetic provider refusal')
    out = polygon_data.get_options_summary_polygon('SYNTH', '2099-06-18')
    assert out['error'] == f'HTTP {status}'
    assert out['coverage']['status'] == 'UNAVAILABLE'
    assert out.get('spot') is None and out.get('iv_atm_call') is None
    assert len(calls) == 1


def test_summary_expiry_without_contracts_is_unavailable(http_fake):
    arm, calls = http_fake
    arm(200, '', {'results': []})
    out = polygon_data.get_options_summary_polygon('SYNTH', '2099-06-18')
    assert out['error'] == 'no data'
    assert out['coverage']['rows_observed'] == 0
    assert out.get('spot') is None
    assert len(calls) == 1
