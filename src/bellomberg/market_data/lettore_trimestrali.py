# -*- coding: utf-8 -*-
"""VOCE 8, TERZO PEZZO — il LETTORE dei comunicati (18/08, Fable 5,
ok PM 13/08 "ok procedi tu").

La parte DETERMINISTICA dell'anello trimestrale-automatica: scaricare il
documento ufficiale dalla fonte per-ticker (fonti_guidance), estrarne il testo
con MISURE, e pescare dalla pagina IR i link-candidati al comunicato dei
risultati. La LETTURA integrale e la proposta guidance+addendum-tesi restano
della sessione al GATE del PM (default: propone — decisione PM aperta):
questo modulo NON chiama nessun LLM e NON scrive mai nel DB.

Anti-fallback-silenzioso (regola PM 14/07):
- download fallito -> stato 'errore' con causa, nessun file monco su disco;
- estrazione vuota -> 'illeggibile' dichiarato, mai stringa vuota con 'ok';
- ogni esito porta i numeri (bytes, pagine, caratteri): una garanzia e' una
  misura, non una frase.

Uso operativo:  python lettore_trimestrali.py TICKER
Scarica la pagina IR del ticker, stampa i candidati-comunicato e salva tutto
in data/trimestrali/<ticker>/ (runtime, non committato).
"""
import os
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from bellomberg.core.paths import DATA_DIR

TRIMESTRALI_DIR = str(DATA_DIR / "trimestrali")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Bellomberg/1.0 "
      "(lettore trimestrali; contatto PM)")

# Parole che segnalano un comunicato risultati (href + testo del link).
# Le brevi (h1, q2, 9m...) SOLO a confine di parola: 'storia.html' non deve
# scattare per un 'h1' annegato.
_PAROLE_LUNGHE = ("result", "risultati", "earnings", "quarter", "trimestral",
                  "semestral", "half-year", "halfyear", "interim", "relazione",
                  "resoconto", "comunicato", "press-release", "financial-report",
                  "guidance")
_RE_BREVI = re.compile(r"(?<![a-z0-9])(h1|h2|q[1-4]|1h|9m)(?![a-z0-9])")


def estrai_testo(path: str) -> dict:
    """Testo da PDF/HTML/testo piano, con misure. Mai un 'ok' vuoto."""
    if not os.path.isfile(path):
        return {"stato": "errore", "testo": "", "caratteri": 0, "pagine": None,
                "formato": None, "motivo": f"file non trovato: {path}"}
    with open(path, "rb") as fh:
        grezzo = fh.read()

    est = os.path.splitext(path)[1].lower()
    if est == ".pdf" or grezzo[:5] == b"%PDF-":
        return _estrai_pdf(path)
    testa = grezzo[:2048].lower()
    if est in (".html", ".htm") or b"<html" in testa or b"<!doctype" in testa:
        return _estrai_html(grezzo)
    try:
        testo = grezzo.decode("utf-8").strip()
    except UnicodeDecodeError:
        testo = ""
    if testo and "\x00" not in testo:
        return {"stato": "ok", "testo": testo, "caratteri": len(testo),
                "pagine": None, "formato": "testo"}
    return {"stato": "illeggibile", "testo": "", "caratteri": 0, "pagine": None,
            "formato": None,
            "motivo": "binario non riconosciuto (ne' PDF ne' HTML ne' testo)"}


def _estrai_pdf(path: str) -> dict:
    try:
        from pypdf import PdfReader
        lettore = PdfReader(path)
        pagine = [pg.extract_text() or "" for pg in lettore.pages]
    except Exception as e:  # PDF rotto/cifrato/scansionato senza xref sano
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "pdf",
                "motivo": f"pypdf: {type(e).__name__}: {e}"}
    testo = "\n".join(pagine).strip()
    if not testo:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": len(pagine), "formato": "pdf",
                "motivo": ("estrazione VUOTA su %d pagine: probabile PDF "
                           "scansionato (serve OCR)" % len(pagine))}
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": len(pagine), "formato": "pdf"}


class _TestoHTML(HTMLParser):
    _MUTI = ("script", "style", "noscript")

    def __init__(self):
        super().__init__()
        self.pezzi = []
        self._muto = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._MUTI:
            self._muto += 1

    def handle_endtag(self, tag):
        if tag in self._MUTI and self._muto:
            self._muto -= 1

    def handle_data(self, data):
        if not self._muto and data.strip():
            self.pezzi.append(data.strip())


def _estrai_html(grezzo: bytes) -> dict:
    parser = _TestoHTML()
    parser.feed(grezzo.decode("utf-8", errors="replace"))
    testo = "\n".join(parser.pezzi).strip()
    if not testo:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "html",
                "motivo": "HTML senza testo estraibile"}
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": None, "formato": "html"}


def scarica_documento(url: str, dest_dir: str, timeout: int = 30) -> dict:
    """Scarica e salva SOLO a download riuscito: niente file monchi."""
    try:
        risposta = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        risposta.raise_for_status()
    except Exception as e:
        return {"stato": "errore", "url": url,
                "motivo": f"{type(e).__name__}: {e}"}
    os.makedirs(dest_dir, exist_ok=True)
    nome = re.sub(r"[^A-Za-z0-9._-]", "_",
                  url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]) or "documento"
    percorso = os.path.join(dest_dir, nome)
    with open(percorso, "wb") as fh:
        fh.write(risposta.content)
    return {"stato": "ok", "url": url, "path": percorso,
            "bytes": len(risposta.content),
            "content_type": risposta.headers.get("Content-Type")}


class _Ancore(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ancore = []       # [href, testo]
        self._aperta = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            self._aperta = [href, ""]

    def handle_data(self, data):
        if self._aperta is not None:
            self._aperta[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._aperta is not None:
            self.ancore.append(self._aperta)
            self._aperta = None


def candidati_comunicato(html_testo: str, base_url: str) -> list:
    """Link-candidati al comunicato risultati da una pagina IR: parole chiave
    su href+testo, URL assolutizzati, .pdf in testa. Pagina senza candidati =
    lista vuota (esito legittimo, non errore)."""
    parser = _Ancore()
    parser.feed(html_testo)
    candidati = []
    for href, testo in parser.ancore:
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        blob = (href + " " + testo).lower()
        if not (any(p in blob for p in _PAROLE_LUNGHE) or _RE_BREVI.search(blob)):
            continue
        url = urljoin(base_url.rstrip("/") + "/", href)
        candidati.append({"url": url, "testo": " ".join(testo.split()),
                          "pdf": url.lower().split("?")[0].endswith(".pdf")})
    candidati.sort(key=lambda c: not c["pdf"])
    return candidati


if __name__ == "__main__":
    import json
    import sys
    from datetime import date

    from bellomberg.market_data.fonti_guidance import fonte_per

    if len(sys.argv) < 2:
        print("uso: python lettore_trimestrali.py TICKER")
        sys.exit(2)
    ticker = sys.argv[1].upper()
    fonte = fonte_per(ticker)
    if fonte["stato"] != "ok":
        print(json.dumps(fonte, indent=2, ensure_ascii=False))
        sys.exit(1)
    base = os.path.join(TRIMESTRALI_DIR, ticker, date.today().isoformat())
    pagina = scarica_documento(fonte["ir_url"], base)
    esito = {"ticker": ticker, "fonte": fonte, "pagina_ir": pagina}
    if pagina["stato"] == "ok":
        estratto = estrai_testo(pagina["path"])
        esito["estrazione"] = {k: v for k, v in estratto.items() if k != "testo"}
        if estratto["formato"] == "html" and estratto["stato"] == "ok":
            with open(pagina["path"], "rb") as fh:
                esito["candidati"] = candidati_comunicato(
                    fh.read().decode("utf-8", errors="replace"),
                    base_url=fonte["ir_url"])
    with open(os.path.join(base, "dossier.json"), "w", encoding="utf-8") as fh:
        json.dump(esito, fh, indent=2, ensure_ascii=False)
    print(json.dumps(esito, indent=2, ensure_ascii=False))
