"""Metriche DAT: la fonte ufficiale con `__NEXT_DATA__` (Akamai) risponde 403 al client di
prima, 200 a un client che manda il SET DI HEADER di un browser (27/08, run V9 «TROVATE DALLA
RUN»).

Cosa e' MISURATO (27/08, n=1 per cella): (a) `requests` col solo User-Agent (nessuno /
Chrome/126, come faceva il modulo) → 403 «Access Denied» errors.edgesuite.net; (b) `curl_cffi`
impersonate chrome/chrome124/safari/firefox con gli header di default → 200, `__NEXT_DATA__`
e `btcTrackerData` presenti; (c) review: `curl_cffi` con impronta TLS Chrome ma
`default_headers=False` → 403, e `requests` liscio coi 13 header letti dal verbose di (b) → 200
identico. Quindi il cancello oggi e' il set di header da browser (sec-ch-ua, sec-fetch-*,
accept, accept-language, priority…), NON l'impronta TLS.

Cura: `_fetch_next_data` usa `curl_cffi` con impersonazione — manda quel set e lo mantiene con
la libreria (gia' dipendenza di yfinance in questo ambiente); l'alternativa (dict di header a
mano su `requests`) e' a zero dipendenze ma da mantenere a mano: scelta reversibile in una
riga. Se il modulo manca, l'errore lo DICE (regola 14/07: mai un ripiego zitto su `requests`,
che tornerebbe 403 e farebbe cercare la causa nel sito).

05/09 (Fable 5.1, classificazione lotto 2b): il dispatcher `get_dat_metrics` NON instrada piu'
per alias cablati del book ma dal NEGOZIO dei veicoli: voce `tipo=dat` con `nav_fonte` = chiave
del registro `FETCH_DAT` (per FONTE, non per simbolo). Qui il negozio e' finto, i simboli sono
inventati e la chiave si legge dal registro per identita' del fetcher, mai scritta.

Zero rete: il trasporto e' finto; si misura COSA parte e cosa torna al chiamante.
"""
import importlib
import json
import re
import sys

import pytest
from curl_cffi.requests.exceptions import HTTPError as _HTTPErrorVero
from curl_cffi.requests.impersonate import BrowserTypeLiteral

import bellomberg.storage.classificazione as cl
from bellomberg.valuation import dat_metrics


def _nomina(testo, simbolo):
    """Parola intera: TESORO non e' nominato da TESORO2 (stesso confine del cancello)."""
    return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(simbolo), testo) is not None

# cifre TONDE e inventate (review 05/09: le cifre vere dell'emittente sono la sua identita')
HTML = ('<html><head><script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"btcTrackerData": [{"btc_holdings": 600000, '
        '"basic_shares_outstanding": 300000000}]}}}</script></head></html>')

# la chiave del fetcher che legge __NEXT_DATA__: un dominio, letto dal registro per identita'
CHIAVE = next(k for k, f in dat_metrics.FETCH_DAT.items() if f is dat_metrics._fetch_dat_next_data)
CHIAVE_ALTRA = next(k for k, f in dat_metrics.FETCH_DAT.items() if f is not dat_metrics._fetch_dat_next_data)

VOCI = {
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD", "nav_fonte": CHIAVE},
    "TESORO2": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD"},          # senza fonte
    "TESORO3": {"tipo": "dat", "provenienza": "dichiarato", "nav_fonte": "sito-finto.example"},   # fonte ignota
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": CHIAVE},                # non e' una DAT
    "ACME": {"tipo": "operating", "provenienza": "dichiarato"},
}


@pytest.fixture(scope="module")
def NEG(tmp_path_factory):
    p = tmp_path_factory.mktemp("negozio") / "v.json"
    p.write_text(json.dumps(VOCI), encoding="utf-8")
    return cl.carica_veicoli(str(p))


class _R:
    def __init__(self, status=200, text=HTML, reason="OK"):
        self.status_code = status
        self.text = text
        self.reason = reason

    def raise_for_status(self):
        if self.status_code >= 400:
            # la forma VERA della libreria (review 27/08), non una di comodo
            raise _HTTPErrorVero("HTTP Error %d: %s" % (self.status_code, self.reason), 0, self)


def _arma(monkeypatch, risposta):
    chiamate = []

    def _get(url, **kw):
        chiamate.append((url, kw))
        return risposta if not callable(risposta) else risposta()
    monkeypatch.setattr(dat_metrics, "_CURL_OK", True)
    monkeypatch.setattr(dat_metrics, "_curl_get", _get)
    monkeypatch.setattr(dat_metrics.requests, "get",
                        lambda *a, **k: pytest.fail("ripiego zitto su requests"))
    monkeypatch.setattr(dat_metrics, "_CACHE", {})
    # i derivati chiamano yfinance: stub (review 05/09: sotto una mutazione del dispatcher un
    # test chiamava la rete e la mutazione cadeva per timeout, non per asserzione)
    monkeypatch.setattr(dat_metrics, "_derivati_mnav_btc",
                        lambda latest, ticker: {"derived_mnav": {"stub": True, "per": ticker}})
    return chiamate


def test_il_registro_e_per_fonte_non_per_simbolo():
    assert len(dat_metrics.FETCH_DAT) >= 2
    for chiave, fn in dat_metrics.FETCH_DAT.items():
        assert re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", chiave), chiave   # dominio, non ticker
        assert callable(fn)


def test_il_fetch_parte_con_un_impersonate_valido_e_il_timeout_del_modulo(monkeypatch):
    chiamate = _arma(monkeypatch, _R())

    nd = dat_metrics._fetch_next_data("https://esempio.invalid/")

    assert nd["props"]["pageProps"]["btcTrackerData"][0]["btc_holdings"] == 600000
    assert len(chiamate) == 1 and chiamate[0][0] == "https://esempio.invalid/"
    kw = chiamate[0][1]
    # review: «impersonate» deve essere un valore che la libreria accetta (un
    # alias sbagliato dal vivo e' ImpersonateError), e il timeout quello del modulo
    assert kw.get("impersonate") in BrowserTypeLiteral.__args__, kw
    assert kw.get("timeout") == dat_metrics.TIMEOUT, kw


def test_senza_curl_cffi_l_errore_lo_dice(monkeypatch):
    """Il ripiego su `requests` e' VIETATO: tornerebbe 403 e il desk cercherebbe
    la causa nel sito invece che nella dipendenza mancante."""
    monkeypatch.setattr(dat_metrics, "_CURL_OK", False)
    monkeypatch.setattr(dat_metrics, "_curl_get", None)
    monkeypatch.setattr(dat_metrics.requests, "get",
                        lambda *a, **k: pytest.fail("ripiego zitto su requests"))

    with pytest.raises(RuntimeError) as ei:
        dat_metrics._fetch_next_data("https://esempio.invalid/")
    assert "curl_cffi" in str(ei.value)


def test_import_fallito_spegne_il_flag_davvero(monkeypatch):
    """Review 27/08: il test sopra prova il FLAG, non l'import fallito — un
    ramo `except` che ripiegasse su requests e tenesse `_CURL_OK=True`
    passava. Qui il modulo viene reso NON importabile e si ricarica."""
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", None)
    try:
        mod = importlib.reload(dat_metrics)
        assert mod._CURL_OK is False
        assert mod._curl_get is None
        with pytest.raises(RuntimeError) as ei:
            mod._fetch_next_data("https://esempio.invalid/")
        assert "curl_cffi" in str(ei.value)
    finally:
        monkeypatch.undo()
        importlib.reload(dat_metrics)
        assert dat_metrics._CURL_OK is True


def test_il_403_arriva_al_chiamante_con_codice_e_fonte(monkeypatch, NEG):
    """`get_dat_metrics` -> `{"error": ...}` con il codice E la fonte (review:
    con la forma della libreria «HTTP Error 403: Forbidden» l'URL si perdeva),
    mai eccezione verso il tool (convenzione del modulo)."""
    _arma(monkeypatch, _R(403, "Access Denied", reason="Forbidden"))

    out = dat_metrics.get_dat_metrics("TESORO", negozio=NEG)

    assert "error" in out and "403" in out["error"], out
    assert CHIAVE in out["error"], out
    assert not dat_metrics._CACHE               # un 403 non si cachea


def test_il_cablaggio_pubblico_usa_il_fetch_con_impronta_e_dichiara_la_fonte(monkeypatch, NEG):
    """`get_dat_metrics` deve passare per il client nuovo: se un giorno qualcuno rimette
    `requests.get` nel fetcher, questo cade. E il payload dice da quale FONTE viene."""
    _arma(monkeypatch, _R())

    out = dat_metrics.get_dat_metrics("TESORO", negozio=NEG)

    assert out.get("ticker") == "TESORO", out
    assert out["latest"]["btc_holdings"] == 600000, out
    assert out["nav_fonte"] == CHIAVE, out
    assert out["derived_mnav"]["per"] == "TESORO"   # il prezzo live e' del ticker chiesto
    assert ("TESORO", CHIAVE) in dat_metrics._CACHE           # esito buono: in cache per (ticker, fonte)


def test_la_cache_non_serve_un_payload_di_una_fonte_cambiata(monkeypatch, NEG, tmp_path):
    """Review 05/09: cache per solo ticker = dopo un cambio di nav_fonte nel negozio il tool
    serviva per 30 min il payload della fonte vecchia."""
    _arma(monkeypatch, _R())
    monkeypatch.setitem(dat_metrics.FETCH_DAT, CHIAVE_ALTRA, lambda t: {"fonte": "altra", "dat_di": "X"})
    out = dat_metrics.get_dat_metrics("TESORO", negozio=NEG)
    assert out["nav_fonte"] == CHIAVE
    voci = {"TESORO": dict(VOCI["TESORO"], nav_fonte=CHIAVE_ALTRA)}
    p = tmp_path / "v2.json"
    p.write_text(json.dumps(voci), encoding="utf-8")
    out2 = dat_metrics.get_dat_metrics("TESORO", negozio=cl.carica_veicoli(str(p)))
    assert out2["nav_fonte"] == CHIAVE_ALTRA and out2["fonte"] == "altra"


def test_l_errore_del_fetcher_porta_ticker_e_fonte(monkeypatch, NEG):
    """Review 05/09: il payload d'errore usciva senza ticker ne' nav_fonte: il consumatore non
    sapeva QUALE fonte aveva fallito."""
    _arma(monkeypatch, _R())
    monkeypatch.setitem(dat_metrics.FETCH_DAT, CHIAVE, lambda t: {"error": "inputs incompleti"})
    out = dat_metrics.get_dat_metrics("TESORO", negozio=NEG)
    assert out["error"] == "inputs incompleti" and out["ticker"] == "TESORO" and out["nav_fonte"] == CHIAVE
    assert not dat_metrics._CACHE                    # un errore non si cachea


def test_dispatcher_senza_alias_del_book_conta_e_non_elenca(monkeypatch, NEG):
    """Ieri il dispatcher instradava per alias cablati e l'errore elencava le DAT
    supportate: oggi decide il negozio e il rifiuto porta un conteggio + l'istruzione."""
    _arma(monkeypatch, _R())
    for t in ("ACME", "FONDO.L", "ZZZQ.XX"):
        out = dat_metrics.get_dat_metrics(t, negozio=NEG)
        assert "error" in out and t in out["error"], out
        assert "1 DAT" in out["error"] and "nav_fonte" in out["error"], out
        for altro in set(VOCI) - {t}:
            assert not _nomina(out["error"], altro), (t, altro)


def test_dat_senza_fonte_riconosciuta_riceve_il_suo_errore(monkeypatch, NEG):
    _arma(monkeypatch, _R())
    out = dat_metrics.get_dat_metrics("TESORO2", negozio=NEG)
    assert "error" in out and "TESORO2" in out["error"] and "nav_fonte" in out["error"]
    assert CHIAVE in out["error"] and CHIAVE_ALTRA in out["error"]      # le chiavi ammesse: domini
    out3 = dat_metrics.get_dat_metrics("TESORO3", negozio=NEG)
    assert "error" in out3 and "sito-finto.example" in out3["error"] and "FETCH_DAT" in out3["error"]
    for altro in set(VOCI) - {"TESORO2", "TESORO3"}:
        assert not _nomina(out["error"], altro) and not _nomina(out3["error"], altro), altro


def test_negozio_assente_o_illeggibile_dichiarato(monkeypatch, tmp_path):
    _arma(monkeypatch, _R())
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    out = dat_metrics.get_dat_metrics("TESORO", negozio=assente)
    assert "error" in out and "ASSENTE" in out["error"]
    (tmp_path / "rotto.json").write_text("{", encoding="utf-8")
    out2 = dat_metrics.get_dat_metrics("TESORO", negozio=cl.carica_veicoli(str(tmp_path / "rotto.json")))
    assert "error" in out2 and "ILLEGGIBILE" in out2["error"]


def test_ticker_vuoto_nessun_default(monkeypatch, NEG):
    _arma(monkeypatch, _R())
    out = dat_metrics.get_dat_metrics("", negozio=NEG)
    assert "error" in out and "nessun default" in out["error"]
