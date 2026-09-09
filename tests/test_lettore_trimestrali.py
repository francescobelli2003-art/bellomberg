"""Voce 8, TERZO PEZZO (ok PM 13/08, Fable 5): il LETTORE dei comunicati.

La parte DETERMINISTICA dell'anello: scaricare il documento ufficiale,
estrarne il testo con MISURE (pagine/caratteri, mai un successo di facciata),
e pescare dalla pagina IR i link-candidati al comunicato dei risultati.
La LETTURA e la proposta al gate restano della sessione (gate PM: propone) —
questo modulo non chiama nessun LLM e non scrive mai nel DB.

Contratto anti-fallback-silenzioso (regola PM 14/07):
- download fallito -> stato 'errore' con causa, MAI un file vuoto zitto;
- estrazione vuota -> stato 'illeggibile' dichiarato (PDF scansionato,
  binario ignoto), MAI stringa vuota con stato ok;
- l'estrazione riporta i numeri (caratteri, pagine): una garanzia e' una
  misura, non una frase.
"""
import pytest

from bellomberg.market_data.lettore_trimestrali import candidati_comunicato, estrai_testo, scarica_documento

def _pdf_minimo() -> bytes:
    """PDF minimo VALIDO con una riga di testo, xref calcolata sul serio:
    prova l'estrazione vera su un PDF corretto, non la tolleranza di pypdf
    verso un fixture rotto."""
    stream = b"BT /F1 12 Tf 72 720 Td (Risultati semestrali 2026 ok) Tj ET"
    corpi = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: (b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>"),
        4: (b"<</Length " + str(len(stream)).encode() + b">>stream\n"
            + stream + b"\nendstream"),
        5: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    }
    out = bytearray(b"%PDF-1.4\n")
    offset = {}
    for n, corpo in corpi.items():
        offset[n] = len(out)
        out += b"%d 0 obj" % n + corpo + b" endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(corpi) + 1)
    out += b"0000000000 65535 f \n"
    for n in corpi:
        out += b"%010d 00000 n \n" % offset[n]
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF"
            % (len(corpi) + 1, xref))
    return bytes(out)

HTML_PAGINA_IR = """
<html><body>
<a href="/media/press-release-h1-2026-results.pdf">Half-Year 2026 Results</a>
<a href="https://cdn.example.com/doc/q2-2026-earnings.pdf">Q2 2026 earnings release</a>
<a href="/chi-siamo/storia.html">La nostra storia</a>
<a href="/governance/cookie-policy">Cookie policy</a>
<a href="/it/comunicato-risultati-semestrali-2026.html">Comunicato risultati semestrali</a>
<a href="mailto:ir@example.com">Contatti IR</a>
</body></html>
"""


# ---------------- estrai_testo: misure, mai facciata ----------------

def test_estrazione_pdf_vero_con_misure(tmp_path):
    p = tmp_path / "comunicato.pdf"
    p.write_bytes(_pdf_minimo())
    r = estrai_testo(str(p))
    assert r["stato"] == "ok"
    assert r["formato"] == "pdf"
    assert "Risultati semestrali 2026" in r["testo"]
    assert r["pagine"] == 1
    assert r["caratteri"] == len(r["testo"]) > 0


def test_estrazione_html_spoglia_i_tag(tmp_path):
    p = tmp_path / "pagina.html"
    p.write_text("<html><body><h1>Titolo</h1><p>Ricavi a 15.500 M</p>"
                 "<script>var x=1;</script></body></html>", encoding="utf-8")
    r = estrai_testo(str(p))
    assert r["stato"] == "ok"
    assert r["formato"] == "html"
    assert "Ricavi a 15.500 M" in r["testo"]
    assert "<p>" not in r["testo"]
    assert "var x=1" not in r["testo"]      # gli script non sono testo


def test_estrazione_vuota_dichiarata_illeggibile(tmp_path):
    p = tmp_path / "garbage.pdf"
    p.write_bytes(b"\x00\x01\x02NONSONOUNPDF")
    r = estrai_testo(str(p))
    assert r["stato"] == "illeggibile"
    assert r["caratteri"] == 0
    assert r.get("motivo")                   # la causa viaggia col verdetto


def test_file_inesistente_dichiarato(tmp_path):
    r = estrai_testo(str(tmp_path / "non_esiste.pdf"))
    assert r["stato"] == "errore"
    assert "non_esiste.pdf" in r["motivo"]


# ---------------- scarica_documento: errori dichiarati ----------------

class _RispostaFinta:
    def __init__(self, content=b"x", status=200, ctype="application/pdf"):
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("requests").HTTPError(f"HTTP {self.status_code}")


def test_scaricamento_salva_e_misura(tmp_path, monkeypatch):
    import requests as _rq
    monkeypatch.setattr(_rq, "get",
                        lambda url, **kw: _RispostaFinta(content=b"%PDF-fake"))
    r = scarica_documento("https://ir.example.com/h1.pdf", str(tmp_path))
    assert r["stato"] == "ok"
    assert r["bytes"] == 9
    with open(r["path"], "rb") as fh:
        assert fh.read() == b"%PDF-fake"


def test_scaricamento_http_4xx_dichiarato_niente_file(tmp_path, monkeypatch):
    import requests as _rq
    monkeypatch.setattr(_rq, "get",
                        lambda url, **kw: _RispostaFinta(status=403))
    r = scarica_documento("https://ir.example.com/blocked.pdf", str(tmp_path))
    assert r["stato"] == "errore"
    assert "403" in r["motivo"]
    import os
    assert os.listdir(tmp_path) == []        # nessun file monco lasciato


def test_scaricamento_rete_giu_dichiarato(tmp_path, monkeypatch):
    import requests as _rq

    def _boom(url, **kw):
        raise _rq.ConnectionError("rete giu'")
    monkeypatch.setattr(_rq, "get", _boom)
    r = scarica_documento("https://ir.example.com/x.pdf", str(tmp_path))
    assert r["stato"] == "errore"
    assert "rete giu'" in r["motivo"]


# ---------------- candidati_comunicato: caccia ai link ----------------

def test_candidati_trovati_con_url_assoluti():
    c = candidati_comunicato(HTML_PAGINA_IR, base_url="https://ir.example.com")
    urls = [x["url"] for x in c]
    assert "https://ir.example.com/media/press-release-h1-2026-results.pdf" in urls
    assert "https://cdn.example.com/doc/q2-2026-earnings.pdf" in urls
    assert ("https://ir.example.com/it/comunicato-risultati-semestrali-2026.html"
            in urls)


def test_candidati_escludono_il_rumore():
    c = candidati_comunicato(HTML_PAGINA_IR, base_url="https://ir.example.com")
    urls = " ".join(x["url"] for x in c)
    assert "storia" not in urls
    assert "cookie" not in urls
    assert "mailto" not in urls


def test_candidati_pdf_prima_degli_html():
    c = candidati_comunicato(HTML_PAGINA_IR, base_url="https://ir.example.com")
    formati = [x["url"].endswith(".pdf") for x in c]
    assert formati == sorted(formati, reverse=True)  # i .pdf stanno in testa


def test_pagina_senza_candidati_lista_vuota_non_errore():
    c = candidati_comunicato("<html><a href='/about'>About</a></html>",
                             base_url="https://x.example")
    assert c == []
