"""I-20: il download curato non contatta host estranei, neppure via redirect."""
import hashlib
from pathlib import Path
from unittest.mock import create_autospec

import pytest
import requests

from bellomberg.market_data.lettore_trimestrali import UA, scarica_documento


def _risposta(url, status=200, *, location=None, content=b"%PDF-documento"):
    risposta = requests.Response()
    risposta.status_code = status
    risposta.url = url
    risposta.request = requests.Request("GET", url).prepare()
    risposta.headers["Content-Type"] = "application/pdf"
    if location is not None:
        risposta.headers["Location"] = location
    risposta._content = content
    return risposta


def _rete_finta(monkeypatch, risposte):
    """Solo il confine HTTP e' finto; risposta, file e hash restano reali."""
    chiamate = []

    def get(url, *, timeout, headers, allow_redirects=True):
        chiamate.append((url, timeout, headers.copy(), allow_redirects))
        risposta = risposte[url]
        if isinstance(risposta, Exception):
            raise risposta
        return risposta

    spy = create_autospec(requests.get, side_effect=get)
    monkeypatch.setattr(requests, "get", spy)
    return chiamate, spy


@pytest.mark.parametrize("url", [
    "https://estraneo.example/report.pdf",
    "https://ir.example.estraneo.example/report.pdf",
    "ftp://ir.example/report.pdf",
    "https://utente:password@ir.example/report.pdf",
    "https://ir.example@estraneo.example/report.pdf",
    "https:///report.pdf",
])
def test_url_iniziale_non_consentito_non_contatta_la_rete(tmp_path, monkeypatch, url):
    chiamate, _ = _rete_finta(monkeypatch, {})
    archivio = tmp_path / "documenti"
    risultato = scarica_documento(url, str(archivio), host_consentiti={"ir.example"})
    assert risultato["stato"] == "errore"
    assert risultato["motivo"]
    assert chiamate == []
    assert not archivio.exists()


@pytest.mark.parametrize("host_consentiti", [[], set(), "ir.example"])
def test_allowlist_vuota_o_tipo_errato_non_diventa_accesso_libero(
        tmp_path, monkeypatch, host_consentiti):
    chiamate, _ = _rete_finta(monkeypatch, {})
    risultato = scarica_documento("https://ir.example/a.pdf", str(tmp_path),
                                 host_consentiti=host_consentiti)
    assert risultato["stato"] == "errore"
    assert chiamate == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("location", [
    "https://estraneo.example/report.pdf",
    "//estraneo.example/report.pdf",
    "https://utente:password@ir.example/report.pdf",
    "ftp://ir.example/report.pdf",
    None,
])
def test_redirect_non_consentito_rifiutato_prima_del_prossimo_get(
        tmp_path, monkeypatch, location):
    url = "https://ir.example/start"
    chiamate, _ = _rete_finta(monkeypatch, {
        url: _risposta(url, 302, location=location),
    })
    conservato = tmp_path / "preesistente.txt"
    conservato.write_bytes(b"preservare")
    risultato = scarica_documento(url, str(tmp_path), host_consentiti=["ir.example"])
    assert risultato["stato"] == "errore"
    assert risultato["motivo"]
    assert [c[0] for c in chiamate] == [url]
    assert chiamate[0][3] is False
    assert list(tmp_path.iterdir()) == [conservato]
    assert conservato.read_bytes() == b"preservare"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_relativo_e_cdn_consentiti_salvano_snapshot_immutabile(
        tmp_path, monkeypatch, status):
    iniziale = "https://ir.example/reports/start"
    intermedio = "https://ir.example/annual.pdf"
    finale = "https://cdn.example/files/report.pdf"
    contenuto = b"%PDF-snapshot-originale"
    chiamate, spy = _rete_finta(monkeypatch, {
        iniziale: _risposta(iniziale, status, location="../annual.pdf"),
        intermedio: _risposta(intermedio, 307, location=finale),
        finale: _risposta(finale, content=contenuto),
    })
    risultato = scarica_documento(iniziale, str(tmp_path), timeout=17,
                                 host_consentiti=["ir.example", "cdn.example"])
    assert risultato["stato"] == "ok"
    assert risultato["url"] == iniziale
    assert risultato["url_finale"] == finale
    assert risultato["bytes"] == len(contenuto)
    assert risultato["sha256"] == hashlib.sha256(contenuto).hexdigest()
    percorso = Path(risultato["path"])
    assert percorso.read_bytes() == contenuto
    assert list(tmp_path.iterdir()) == [percorso]
    assert [c[0] for c in chiamate] == [iniziale, intermedio, finale]
    assert all(c[1:] == (17, {"User-Agent": UA}, False) for c in chiamate)
    assert all(set(c.kwargs) == {"timeout", "headers", "allow_redirects"}
               for c in spy.call_args_list)  # nessun bypass TLS/auth
    ripetuto = scarica_documento(iniziale, str(tmp_path),
                                host_consentiti={"ir.example", "cdn.example"})
    assert ripetuto["path"] == risultato["path"]
    assert list(tmp_path.iterdir()) == [percorso]
    assert percorso.read_bytes() == contenuto


def test_redirect_circolare_si_ferma_senza_richiamare_lo_stesso_url(tmp_path, monkeypatch):
    url = "https://ir.example/start"
    chiamate, _ = _rete_finta(monkeypatch, {url: _risposta(url, 302, location="/start")})
    risultato = scarica_documento(url, str(tmp_path), host_consentiti={"ir.example"})
    assert risultato["stato"] == "errore"
    assert "redirect" in risultato["motivo"].lower()
    assert [c[0] for c in chiamate] == [url]
    assert list(tmp_path.iterdir()) == []


def test_catena_redirect_oltre_budget_non_contatta_il_prossimo_hop(tmp_path, monkeypatch):
    urls = [f"https://ir.example/{i}" for i in range(8)]
    chiamate, _ = _rete_finta(monkeypatch, {
        url: _risposta(url, 302, location=f"/{i + 1}") for i, url in enumerate(urls[:-1])
    })
    risultato = scarica_documento(urls[0], str(tmp_path), host_consentiti={"ir.example"})
    assert risultato["stato"] == "errore"
    assert "redirect" in risultato["motivo"].lower()
    assert [c[0] for c in chiamate] == urls[:7]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("errore", [
    requests.ConnectionError("rete non disponibile"),
    requests.exceptions.SSLError("certificato non valido"),
    _risposta("https://ir.example/a.pdf", 403),
    _risposta("https://ir.example/a.pdf", content=b""),
])
def test_download_fallito_non_crea_archivio_o_file(tmp_path, monkeypatch, errore):
    url = "https://ir.example/a.pdf"
    chiamate, _ = _rete_finta(monkeypatch, {url: errore})
    archivio = tmp_path / "documenti"
    risultato = scarica_documento(url, str(archivio), host_consentiti={"ir.example"})
    assert risultato["stato"] == "errore"
    assert risultato["motivo"]
    assert len(chiamate) == 1
    assert not archivio.exists()


def test_header_sec_ricalcolati_a_ogni_hop_non_trasferiti_al_cdn(tmp_path, monkeypatch):
    iniziale = "https://ir.example/start"
    sec = "https://www.sec.gov/Archives/report.htm"
    cdn = "https://cdn.example/report.pdf"
    chiamate, _ = _rete_finta(monkeypatch, {
        iniziale: _risposta(iniziale, 302, location=sec),
        sec: _risposta(sec, 302, location=cdn),
        cdn: _risposta(cdn),
    })
    risultato = scarica_documento(iniziale, str(tmp_path),
                                 host_consentiti={"ir.example", "www.sec.gov", "cdn.example"})
    assert risultato["stato"] == "ok"
    assert chiamate[0][2] == {"User-Agent": UA}
    assert chiamate[1][2]["User-Agent"].startswith("Bellomberg Personal Terminal ")
    assert chiamate[1][2]["Accept"] == "*/*"
    assert chiamate[2][2] == {"User-Agent": UA}
    assert all(c[3] is False for c in chiamate)


def test_senza_allowlist_preserva_redirect_automatici_e_url_finale_legacy(tmp_path, monkeypatch):
    iniziale = "https://ir.example/start"
    finale = "https://altro-host.example/report.pdf"
    chiamate, spy = _rete_finta(monkeypatch, {iniziale: _risposta(finale)})
    risultato = scarica_documento(iniziale, str(tmp_path), host_consentiti=None)
    assert risultato["stato"] == "ok"
    assert risultato["url_finale"] == finale
    assert Path(risultato["path"]).read_bytes() == b"%PDF-documento"
    assert [c[0] for c in chiamate] == [iniziale]
    assert "allow_redirects" not in spy.call_args.kwargs
