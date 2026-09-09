"""I client Polygon/Quiver dichiarano nel LOG ogni risposta non-200 (27/08, run V9).

Il fatto misurato: `polygon_data._get` e `quiver_data._get` sul non-200
tornavano `{"error": "HTTP 429"}` al chiamante SENZA stampare nulla, quindi il
«ZERO righe 429 nel log della run V9» (MASTER §9-octoquadragies) non era una
misura: il log non poteva mostrare un 429 nemmeno se c'era stato. Da oggi la
riga esce nella forma `  [POLYGON] HTTP <code> on <path>: <corpo>` (analoga a
`[FINNHUB] HTTP <code> on <path>: ...` di `finnhub_news._api_get` — che pero'
sul 429 stampa `429 rate limited (...)` senza HTTP ne' path) — e alla prossima
run l'assenza di righe `HTTP 429 on` sara' una misura. Anche l'eccezione di
trasporto (timeout, rete giu') lascia una riga: `[POLYGON] <Tipo> on <path>`.

Ogni test passa per il trasporto vero (`requests.get` finto) e legge lo stdout:
quello che finisce in `consigliere_run.log`. Il contratto del ritorno
(`{"error": "HTTP <code>", "_body": ...}`) NON cambia: la riga e' additiva —
anche quando la print esplode (stdout chiuso, encoding), review 27/08.
"""
import builtins

import requests

from bellomberg.market_data import finnhub_news
from bellomberg.market_data import polygon_data
from bellomberg.market_data import quiver_data


class _R:
    def __init__(self, status, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _arma(monkeypatch, modulo, chiave_attr, risposta):
    monkeypatch.setattr(modulo, "REQ_OK", True)
    monkeypatch.setattr(modulo, chiave_attr, "chiave-finta-segreta")
    if isinstance(risposta, Exception):
        def _get(url, **k):
            raise risposta
        monkeypatch.setattr(modulo.requests, "get", _get)
    else:
        monkeypatch.setattr(modulo.requests, "get", lambda url, **k: risposta)


def _righe(out, tag):
    return [r for r in out.splitlines() if tag in r]


# ------------------------------------------------------------ Polygon

def test_polygon_429_lascia_una_riga_nel_log_con_la_forma_esatta(monkeypatch, capsys):
    """La forma esatta e' il contratto per chi fa grep sul log della run:
    `[POLYGON] HTTP 429 on <path>:` — non «un 429 da qualche parte»."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY",
          _R(429, "You've exceeded the maximum requests per minute"))

    esito = polygon_data._get("/v3/reference/options/contracts", {"underlying_ticker": "ALFA"})

    righe = _righe(capsys.readouterr().out, "[POLYGON]")
    assert len(righe) == 1, righe
    assert "[POLYGON] HTTP 429 on /v3/reference/options/contracts: " in righe[0], righe[0]
    assert "exceeded the maximum requests" in righe[0], righe[0]
    # il contratto verso il chiamante non cambia (la riga e' additiva)
    assert esito == {"error": "HTTP 429",
                     "_body": "You've exceeded the maximum requests per minute"}


def test_polygon_la_riga_non_porta_la_chiave_api(monkeypatch, capsys):
    """Il log della run e' un file di testo che gira: la chiave non ci entra —
    neanche quando il path e' un `next_url` assoluto con la query attaccata,
    neanche se il corpo della risposta la riecheggia."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY",
          _R(429, "rate limited for key chiave-finta-segreta"))

    polygon_data._get("https://api.polygon.io/v3/reference/options/contracts"
                      "?cursor=abc&apiKey=chiave-finta-segreta")

    out = capsys.readouterr().out
    assert "[POLYGON] HTTP 429 on /v3/reference/options/contracts: " in out, out
    assert "chiave-finta-segreta" not in out, out


def test_polygon_200_non_sporca_il_log(monkeypatch, capsys):
    _arma(monkeypatch, polygon_data, "POLYGON_KEY", _R(200, "{}", {"results": []}))

    polygon_data._get("/v3/reference/options/contracts")

    assert "[POLYGON]" not in capsys.readouterr().out


def test_polygon_ogni_non_200_lascia_la_riga_non_solo_il_429(monkeypatch, capsys):
    """Un corpo HTML multi-riga (WAF, proxy, 503) sta su UNA riga di log."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY",
          _R(503, "<html>\n<body>\nService Unavailable\n</body>\n</html>"))

    polygon_data._get("/v2/aggs/ticker/ALFA/range/1/day/2026-01-01/2026-08-27")

    righe = _righe(capsys.readouterr().out, "[POLYGON]")
    assert len(righe) == 1, righe
    assert "[POLYGON] HTTP 503 on /v2/aggs/ticker/ALFA/range/1/day/2026-01-01/2026-08-27: " in righe[0]
    assert "Service Unavailable" in righe[0], righe[0]


def test_polygon_il_cablaggio_pubblico_passa_per_la_riga(monkeypatch, capsys):
    """Non l'helper da solo: la funzione che il tool degli agenti chiama."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY", _R(429, "rate limited"))

    esito = polygon_data.get_option_expirations("ALFA")

    out = capsys.readouterr().out
    assert "[POLYGON] HTTP 429 on " in out, out
    assert esito.get("error") == "HTTP 429", esito


def test_polygon_stdout_che_esplode_non_rompe_il_contratto_http(monkeypatch, capsys):
    """Review 27/08: `except OSError` non copriva `ValueError` («I/O operation
    on closed file», encoding): l'eccezione della print scappava, `_get` la
    catturava e il chiamante riceveva `{"error": "I/O operation..."}` invece
    del codice HTTP. La riga di log e' additiva: se non si puo' scrivere, si
    perde LEI, non il contratto."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY", _R(429, "rate limited"))

    def _print_rotta(*a, **k):
        raise ValueError("I/O operation on closed file")
    monkeypatch.setattr(builtins, "print", _print_rotta)

    esito = polygon_data._get("/v3/reference/options/contracts")

    assert esito == {"error": "HTTP 429", "_body": "rate limited"}, esito


def test_polygon_eccezione_di_trasporto_lascia_una_riga_senza_str_e(monkeypatch, capsys):
    """Timeout/rete giu': una riga col TIPO e il path — mai `str(e)`, che per
    requests contiene l'URL intero con `apiKey=` (misurato dalla review su un
    ConnectTimeout vero). E la chiave sparisce anche dal dict di ritorno."""
    _arma(monkeypatch, polygon_data, "POLYGON_KEY", requests.exceptions.ConnectTimeout(
        "HTTPSConnectionPool(host='api.polygon.io'): Max retries exceeded with url: "
        "/v3/reference/options/contracts?underlying_ticker=ALFA&apiKey=chiave-finta-segreta"))

    esito = polygon_data._get("/v3/reference/options/contracts", {"underlying_ticker": "ALFA"})

    out = capsys.readouterr().out
    righe = _righe(out, "[POLYGON]")
    assert len(righe) == 1, out
    assert "[POLYGON] ConnectTimeout on /v3/reference/options/contracts" in righe[0], righe[0]
    assert "chiave-finta-segreta" not in out, out
    assert "chiave-finta-segreta" not in esito["error"], esito
    assert "ConnectTimeout" in esito["error"] or "Max retries" in esito["error"], esito


# ------------------------------------------------------------ Quiver

def test_quiver_429_lascia_una_riga_nel_log_con_la_forma_esatta(monkeypatch, capsys):
    _arma(monkeypatch, quiver_data, "QUIVER_KEY", _R(429, "Request was throttled."))

    esito = quiver_data._get("/historical/congresstrading/ALFA")

    righe = _righe(capsys.readouterr().out, "[QUIVER]")
    assert len(righe) == 1, righe
    assert "[QUIVER] HTTP 429 on /historical/congresstrading/ALFA: Request was throttled." in righe[0], righe[0]
    assert esito == {"error": "HTTP 429", "_body": "Request was throttled."}


def test_quiver_200_non_sporca_il_log(monkeypatch, capsys):
    _arma(monkeypatch, quiver_data, "QUIVER_KEY", _R(200, "[]", []))

    quiver_data._get("/historical/congresstrading/ALFA")

    assert "[QUIVER]" not in capsys.readouterr().out


def test_quiver_il_cablaggio_pubblico_passa_per_la_riga(monkeypatch, capsys):
    _arma(monkeypatch, quiver_data, "QUIVER_KEY", _R(429, "Request was throttled."))

    esito = quiver_data.get_congress_trades("ALFA")

    out = capsys.readouterr().out
    assert "[QUIVER] HTTP 429 on /historical/congresstrading/ALFA: " in out, out
    assert esito.get("error") == "HTTP 429", esito


def test_quiver_il_corpo_della_risposta_sta_su_una_riga_sola(monkeypatch, capsys):
    """Un corpo HTML multi-riga non deve spezzare la riga di log: chi conta i
    429 con un grep di riga li deve trovare tutti su una riga."""
    _arma(monkeypatch, quiver_data, "QUIVER_KEY",
          _R(503, "<html>\n<body>\nService Unavailable\n</body>\n</html>"))

    quiver_data._get("/historical/lobbying/ALFA")

    righe = _righe(capsys.readouterr().out, "[QUIVER]")
    assert len(righe) == 1, righe
    assert "[QUIVER] HTTP 503 on /historical/lobbying/ALFA: " in righe[0], righe[0]
    assert "Service Unavailable" in righe[0], righe[0]


def test_quiver_stdout_che_esplode_non_rompe_il_contratto_http(monkeypatch):
    _arma(monkeypatch, quiver_data, "QUIVER_KEY", _R(429, "Request was throttled."))

    def _print_rotta(*a, **k):
        raise OSError(22, "Invalid argument")
    monkeypatch.setattr(builtins, "print", _print_rotta)

    esito = quiver_data._get("/historical/congresstrading/ALFA")

    assert esito == {"error": "HTTP 429", "_body": "Request was throttled."}, esito


def test_quiver_eccezione_di_trasporto_lascia_una_riga(monkeypatch, capsys):
    _arma(monkeypatch, quiver_data, "QUIVER_KEY",
          requests.exceptions.ConnectionError("connessione rifiutata"))

    esito = quiver_data._get("/historical/govcontractsall/ALFA")

    righe = _righe(capsys.readouterr().out, "[QUIVER]")
    assert len(righe) == 1, righe
    assert "[QUIVER] ConnectionError on /historical/govcontractsall/ALFA" in righe[0], righe[0]
    assert "connessione rifiutata" in esito["error"], esito


# ------------------------------------------------------------ Finnhub (stesso buco, review)

def test_finnhub_stdout_che_esplode_non_rilancia_dal_ramo_di_errore(monkeypatch):
    """`finnhub_news._log` aveva lo stesso `except OSError`: con stdout chiuso
    la print sollevava ValueError DENTRO l'except di `_api_get` (riga 97), che
    richiama `_muto` → `_log` → seconda eccezione dal gestore — la classe di
    guasto dell'autopsia (40). Ora il motivo arriva lo stesso in `motivo`."""
    monkeypatch.setattr(finnhub_news, "REQ_OK", True)
    monkeypatch.setattr(finnhub_news, "_get_key", lambda: "chiave-finta")
    monkeypatch.setattr(finnhub_news.requests, "get", lambda url, **k: _R(503, "giu'"))

    def _print_rotta(*a, **k):
        raise ValueError("I/O operation on closed file")
    monkeypatch.setattr(builtins, "print", _print_rotta)

    motivo = []
    esito = finnhub_news._api_get("/company-news", {"symbol": "ALFA"}, motivo=motivo)

    assert esito is None
    assert any("503" in m for m in motivo), motivo
