"""Tiingo DICHIARATO (04/10, B1 — Opus 5.5).

Il fatto misurato (ricognizione R1 04/10): `fetch_tiingo_news` tornava `[]` in
silenzio su non-200 e su qualunque eccezione, e nel `news_feed.log` la parola
«tiingo» compariva 0 volte. Con l'abbonamento in scadenza (~11/10) uno
scaduto sarebbe stato INVISIBILE. Da oggi ogni esito non-live lascia una riga
`  [TIINGO] ...` nel log, `last_status()` dice l'esito dell'ultima chiamata
(letto da news_aggregator per dichiarare la fonte muta) e `motivo` porta il
perche' al chiamante. Il ritorno (lista) NON cambia.

La chiave Tiingo viaggia in QUERYSTRING (`token=`): mai nel log, mai nel motivo.
Nessuna rete: `requests.get` e' sempre finto. Simboli inventati (ZZTEST).
"""
import builtins

import pytest
import requests

from bellomberg.market_data import tiingo_news

CHIAVE = "chiave-finta-tiingo-segreta"


class _R:
    def __init__(self, status, text="", payload=None, json_exc=None):
        self.status_code = status
        self.text = text
        self._payload = payload
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._payload


@pytest.fixture(autouse=True)
def _stato_pulito(monkeypatch):
    # 10/10: Tiingo News e' SPENTA per decisione PM (abbonamento non rinnovato). Questi test
    # provano il codice di rete DORMIENTE, quello che torna vivo con TIINGO_NEWS_ENABLED=1:
    # lo si accende qui. La fonte spenta ha i suoi test in test_tiingo_spenta_gnews_top.py.
    monkeypatch.setattr(tiingo_news, "FONTE_SPENTA", False)
    tiingo_news.reset_status()
    yield
    tiingo_news.reset_status()


def _arma(monkeypatch, risposta, chiave=CHIAVE):
    monkeypatch.setattr(tiingo_news, "REQ_OK", True)
    monkeypatch.setattr(tiingo_news, "TIINGO_KEY", chiave)
    chiamate = []
    if isinstance(risposta, Exception):
        def _get(url, **k):
            chiamate.append((url, k))
            raise risposta
    else:
        def _get(url, **k):
            chiamate.append((url, k))
            return risposta
    monkeypatch.setattr(tiingo_news.requests, "get", _get)
    return chiamate


def _righe(out):
    return [r for r in out.splitlines() if "[TIINGO]" in r]


ARTICOLO = {"title": "Titolo di prova", "source": "esempio.test", "url": "https://esempio.test/a",
            "publishedDate": "2026-10-04T10:00:00Z", "description": "testo", "tickers": ["zztest"]}


# ------------------------------------------------------------ riga di log

def test_401_lascia_una_riga_con_la_forma_esatta(monkeypatch, capsys):
    _arma(monkeypatch, _R(401, '{"detail":"Invalid token."}'))

    esito = tiingo_news.fetch_tiingo_news(["ZZTEST"])

    righe = _righe(capsys.readouterr().out)
    assert len(righe) == 1, righe
    assert '[TIINGO] HTTP 401 on /tiingo/news: {"detail":"Invalid token."}' in righe[0], righe[0]
    assert esito == []  # ritorno invariato


def test_200_non_sporca_il_log(monkeypatch, capsys):
    _arma(monkeypatch, _R(200, "[]", [ARTICOLO]))

    esito = tiingo_news.fetch_tiingo_news(["ZZTEST"])

    assert "[TIINGO]" not in capsys.readouterr().out
    assert len(esito) == 1 and esito[0]["provider"] == "tiingo"
    assert esito[0]["_tickers"] == ["ZZTEST"]


def test_corpo_multilinea_sta_su_una_riga(monkeypatch, capsys):
    _arma(monkeypatch, _R(503, "<html>\n<body>\nService Unavailable\n</body>\n</html>"))

    tiingo_news.fetch_tiingo_news()

    righe = _righe(capsys.readouterr().out)
    assert len(righe) == 1, righe
    assert "[TIINGO] HTTP 503 on /tiingo/news: " in righe[0]
    assert "Service Unavailable" in righe[0]


def test_print_che_esplode_non_rompe_il_contratto(monkeypatch):
    _arma(monkeypatch, _R(401, "Invalid token."))

    def _print_rotta(*a, **k):
        raise ValueError("I/O operation on closed file")
    monkeypatch.setattr(builtins, "print", _print_rotta)

    motivo = []
    esito = tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo)

    assert esito == []
    assert tiingo_news.last_status()["stato"] == "HTTP_401"
    assert motivo and motivo[0].startswith("HTTP 401 on /tiingo/news"), motivo


def test_eccezione_di_trasporto_riga_col_tipo_senza_chiave(monkeypatch, capsys):
    """`str(e)` di requests contiene l'URL con `token=<chiave>`: la riga porta
    solo il TIPO, e la chiave non esce ne' dal log ne' dal motivo."""
    _arma(monkeypatch, requests.exceptions.ConnectTimeout(
        "HTTPSConnectionPool(host='api.tiingo.com'): Max retries exceeded with url: "
        f"/tiingo/news?token={CHIAVE}&limit=10"))

    motivo = []
    esito = tiingo_news.fetch_tiingo_news(motivo=motivo)

    out = capsys.readouterr().out
    righe = _righe(out)
    assert len(righe) == 1, out
    assert "[TIINGO] ConnectTimeout on /tiingo/news" in righe[0], righe[0]
    assert CHIAVE not in out
    assert esito == []
    assert motivo == ["ConnectTimeout on /tiingo/news"], motivo
    st = tiingo_news.last_status()
    assert st["stato"] == "ERRORE_ConnectTimeout" and st["http"] is None


def test_corpo_che_riecheggia_la_chiave_e_mascherato(monkeypatch, capsys):
    _arma(monkeypatch, _R(403, f"token {CHIAVE} expired; retry with token={CHIAVE}"))

    motivo = []
    tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo)

    out = capsys.readouterr().out
    assert "[TIINGO] HTTP 403 on /tiingo/news: " in out, out
    assert CHIAVE not in out, out
    assert "***" in out, out
    assert all(CHIAVE not in m for m in motivo), motivo
    assert motivo and "***" in motivo[0], motivo


def test_maschera_token_in_querystring_anche_con_chiave_diversa():
    assert tiingo_news._maschera("url?token=altrachiave&limit=3") == "url?token=***&limit=3"


def test_la_chiave_va_davvero_in_querystring_e_non_nel_log(monkeypatch, capsys):
    """Il trasporto non cambia (token in querystring, header non verificato dal
    vivo): lo stub riceve la chiave, il log no."""
    chiamate = _arma(monkeypatch, _R(401, "nope"))

    tiingo_news.fetch_tiingo_news(["ZZTEST"])

    assert chiamate and chiamate[0][1]["params"]["token"] == CHIAVE
    assert chiamate[0][0].endswith("/tiingo/news")
    assert CHIAVE not in capsys.readouterr().out


# ------------------------------------------------------------ last_status

def test_mai_interrogata_e_none():
    assert tiingo_news.last_status() is None


def test_live_poi_401_poi_live_guarisce(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", [ARTICOLO, ARTICOLO]))
    tiingo_news.fetch_tiingo_news(["ZZTEST"])
    st = tiingo_news.last_status()
    assert st["stato"] == "live" and st["http"] == 200 and st["n_item"] == 2
    assert st["path"] == "/tiingo/news"
    assert isinstance(st["mono"], float) and isinstance(st["quando"], str)

    _arma(monkeypatch, _R(401, "Invalid token."))
    tiingo_news.fetch_tiingo_news(["ZZTEST"])
    st = tiingo_news.last_status()
    assert st["stato"] == "HTTP_401" and st["http"] == 401 and st["n_item"] == 0

    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news(["ZZTEST"])
    st = tiingo_news.last_status()
    assert st["stato"] == "live" and st["n_item"] == 0  # zero MISURATO, non muto


def test_mono_avanza_a_ogni_chiamata(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news()
    primo = tiingo_news.last_status()["mono"]
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["mono"] >= primo


def test_last_status_restituisce_una_copia(monkeypatch):
    _arma(monkeypatch, _R(401, "x"))
    tiingo_news.fetch_tiingo_news()
    st = tiingo_news.last_status()
    st["stato"] = "live"
    assert tiingo_news.last_status()["stato"] == "HTTP_401"


def test_reset_riporta_a_mai_interrogata(monkeypatch):
    _arma(monkeypatch, _R(401, "x"))
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status() is not None
    tiingo_news.reset_status()
    assert tiingo_news.last_status() is None


def test_corpo_200_non_lista_e_risposta_inattesa(monkeypatch, capsys):
    _arma(monkeypatch, _R(200, '{"detail":"x"}', {"detail": "piano scaduto"}))

    motivo = []
    esito = tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo)

    assert esito == []
    st = tiingo_news.last_status()
    assert st["stato"] == "RISPOSTA_INATTESA" and st["http"] == 200
    assert motivo == ["/tiingo/news: risposta inattesa (dict): {'detail': 'piano scaduto'}"], motivo
    # RV-N P3: il corpo (il PERCHE' di Tiingo) esce nella riga
    assert ("[TIINGO] risposta inattesa (dict) on /tiingo/news: {'detail': 'piano scaduto'}"
            in capsys.readouterr().out)


def test_corpo_200_dict_con_la_chiave_e_mascherato_e_su_una_riga(monkeypatch, capsys):
    _arma(monkeypatch, _R(200, "{}", {"detail": f"token {CHIAVE}\nscaduto"}))

    motivo = []
    tiingo_news.fetch_tiingo_news(motivo=motivo)

    out = capsys.readouterr().out
    righe = _righe(out)
    assert len(righe) == 1, out
    assert CHIAVE not in out and "***" in righe[0], out
    assert all(CHIAVE not in m for m in motivo), motivo


# ------------------------------------------------------------ VUOTO_SOSPETTO (RV-N P2)

def test_tre_feed_generali_vuoti_sono_vuoto_sospetto(monkeypatch, capsys):
    """Il feed generale in condizioni normali non e' mai vuoto: tre 200 [] di
    fila = abbonamento/piano da verificare, NON live."""
    _arma(monkeypatch, _R(200, "[]", []))

    tiingo_news.fetch_tiingo_news()
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "live"
    assert "[TIINGO]" not in capsys.readouterr().out

    motivo = []
    esito = tiingo_news.fetch_tiingo_news(motivo=motivo)

    assert esito == []
    st = tiingo_news.last_status()
    assert st["stato"] == "VUOTO_SOSPETTO" and st["http"] == 200 and st["n_item"] == 0
    righe = _righe(capsys.readouterr().out)
    assert len(righe) == 1, righe
    assert "[TIINGO] feed generale a 0 articoli per 3 chiamate consecutive" in righe[0], righe[0]
    assert "abbonamento/piano da verificare" in righe[0]
    assert motivo and "per 3 chiamate consecutive" in motivo[0], motivo


def test_due_feed_generali_vuoti_restano_live(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news()
    motivo = []
    tiingo_news.fetch_tiingo_news(motivo=motivo)
    assert tiingo_news.last_status()["stato"] == "live"
    assert motivo == []


def test_generale_con_articoli_azzera_il_contatore(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news()
    tiingo_news.fetch_tiingo_news()
    _arma(monkeypatch, _R(200, "[]", [ARTICOLO]))
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "live"
    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news()
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "live"  # 2 dopo l'azzeramento, non 4
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "VUOTO_SOSPETTO"


def test_per_ticker_vuoto_resta_live_e_non_conta(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", []))
    for _ in range(5):
        tiingo_news.fetch_tiingo_news(["ZZTEST"])
    assert tiingo_news.last_status()["stato"] == "live"
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "live"  # i per-ticker non hanno contato


def test_reset_azzera_anche_il_contatore(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", []))
    tiingo_news.fetch_tiingo_news()
    tiingo_news.fetch_tiingo_news()
    tiingo_news.reset_status()
    tiingo_news.fetch_tiingo_news()
    assert tiingo_news.last_status()["stato"] == "live"


def test_json_rotto_e_dichiarato(monkeypatch, capsys):
    _arma(monkeypatch, _R(200, "<html>", json_exc=ValueError("Expecting value")))

    esito = tiingo_news.fetch_tiingo_news()

    assert esito == []
    assert tiingo_news.last_status()["stato"] == "ERRORE_ValueError"
    assert "[TIINGO] ValueError on /tiingo/news" in capsys.readouterr().out


def test_senza_chiave_dichiara_e_non_chiama(monkeypatch):
    chiamate = _arma(monkeypatch, _R(200, "[]", []), chiave="")

    motivo = []
    esito = tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo)

    assert esito == [] and chiamate == []
    assert tiingo_news.last_status()["stato"] == "SENZA_CHIAVE"
    assert motivo and "TIINGO_API_KEY" in motivo[0], motivo


def test_senza_requests_dichiara(monkeypatch):
    chiamate = _arma(monkeypatch, _R(200, "[]", []))
    monkeypatch.setattr(tiingo_news, "REQ_OK", False)

    motivo = []
    assert tiingo_news.fetch_tiingo_news(motivo=motivo) == []
    assert chiamate == []
    assert tiingo_news.last_status()["stato"] == "ERRORE_ImportError"
    assert motivo, motivo


def test_motivo_vuoto_su_successo(monkeypatch):
    _arma(monkeypatch, _R(200, "[]", [ARTICOLO]))
    motivo = []
    tiingo_news.fetch_tiingo_news(["ZZTEST"], motivo=motivo)
    assert motivo == []


def test_motivo_none_non_esplode(monkeypatch):
    _arma(monkeypatch, _R(429, "rate limited"))
    assert tiingo_news.fetch_tiingo_news() == []
    assert tiingo_news.last_status()["stato"] == "HTTP_429"
