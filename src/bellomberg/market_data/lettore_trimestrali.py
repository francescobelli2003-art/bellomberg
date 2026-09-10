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
import hashlib
import tempfile
from io import BytesIO
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

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
                  "guidance", "annual", "jahresbericht", "geschaeftsbericht",
                  "geschäftsbericht", "halbjahres", "finanzbericht")
_RE_BREVI = re.compile(r"(?<![a-z0-9])(h1|h2|q[1-4]|1h|9m)(?![a-z0-9])")


def estrai_testo(path: str, *, contenuto: bytes | None = None) -> dict:
    """Testo con misure; contenuto permette di citare lo stesso snapshot hashato."""
    if contenuto is None and not os.path.isfile(path):
        return {"stato": "errore", "testo": "", "caratteri": 0, "pagine": None,
                "formato": None, "motivo": f"file non trovato: {path}"}
    if contenuto is None:
        with open(path, "rb") as fh:
            grezzo = fh.read()
    else:
        grezzo = contenuto

    est = os.path.splitext(path)[1].lower()
    if est == ".pdf" or grezzo[:5] == b"%PDF-":
        return _estrai_pdf(BytesIO(grezzo))
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
    riferimenti = []
    posizione = 0
    for numero, pagina in enumerate(pagine, 1):
        riferimenti.append({"pagina": numero, "inizio": posizione,
                            "fine": posizione + len(pagina), "testo": pagina})
        posizione += len(pagina) + 1
    testo = "\n".join(pagine)
    if not testo.strip():
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": len(pagine), "formato": "pdf",
                "motivo": ("estrazione VUOTA su %d pagine: probabile PDF "
                           "scansionato (serve OCR)" % len(pagine))}
    vuote = [r["pagina"] for r in riferimenti if not r["testo"].strip()]
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": len(pagine), "formato": "pdf", "riferimenti": riferimenti,
            "pagine_senza_testo": vuote,
            "avvisi": ([f"Pagine senza testo: {vuote}; contenuto non verificabile senza OCR"]
                       if vuote else [])}


class _TestoHTML(HTMLParser):
    _MUTI = ("script", "style", "noscript", "template", "ix:hidden")
    _BLOCCHI = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li",
                "section", "article", "br", "hr")

    def __init__(self):
        super().__init__()
        self.pezzi = []
        self._muto = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._MUTI:
            self._muto += 1
        if not self._muto and tag in self._BLOCCHI:
            self.pezzi.append("\n")
        if not self._muto and tag in ("td", "th"):
            self.pezzi.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self._MUTI and self._muto:
            self._muto -= 1
        if not self._muto and tag in self._BLOCCHI:
            self.pezzi.append("\n")

    def handle_data(self, data):
        if not self._muto:
            self.pezzi.append(data)


def _estrai_html(grezzo: bytes) -> dict:
    parser = _TestoHTML()
    dichiarata = re.search(br"(?:charset\s*=\s*[\"']?|encoding\s*=\s*[\"'])([A-Za-z0-9._-]+)",
                          grezzo[:4096], re.I)
    codifica = dichiarata[1].decode("ascii") if dichiarata else "utf-8-sig"
    if grezzo.startswith((b"\xff\xfe", b"\xfe\xff")):
        codifica = "utf-16"
    try:
        parser.feed(grezzo.decode(codifica))
    except (UnicodeError, LookupError) as exc:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "html",
                "motivo": f"codifica HTML {codifica} non leggibile: {exc}"}
    testo = "".join(parser.pezzi).strip()
    if not testo:
        return {"stato": "illeggibile", "testo": "", "caratteri": 0,
                "pagine": None, "formato": "html",
                "motivo": "HTML senza testo estraibile"}
    return {"stato": "ok", "testo": testo, "caratteri": len(testo),
            "pagine": None, "formato": "html", "codifica": codifica}


def scarica_documento(url: str, dest_dir: str, timeout: int = 30, *,
                      host_consentiti=None) -> dict:
    """Snapshot immutabili; host curati opzionali, controllati prima di ogni GET.

    Senza host_consentiti resta il download legacy con redirect automatici.
    Con allowlist: massimo 6 redirect HTTP(S), nessuna credenziale negli URL.
    """
    temporaneo = None
    try:
        if host_consentiti is None:
            if urlsplit(url).scheme not in ("http", "https"):
                raise ValueError("URL HTTP(S) richiesto")
            headers = {"User-Agent": UA}
            if urlsplit(url).hostname in ("www.sec.gov", "sec.gov", "data.sec.gov"):
                from bellomberg.market_data.sec_edgar import _headers
                headers = {**_headers(), "Accept": "*/*"}
            risposta = requests.get(url, timeout=timeout, headers=headers)
        else:
            if not isinstance(host_consentiti, (list, tuple, set, frozenset)):
                raise ValueError("host_consentiti richiede una lista o un insieme di hostname")
            if any(not isinstance(host, str) or not host for host in host_consentiti):
                raise ValueError("hostname consentiti non validi")
            consentiti = {host.lower() for host in host_consentiti}
            corrente = url
            visitati = set()
            limite_redirect = 6
            for salto in range(limite_redirect + 1):
                parti = urlsplit(corrente)
                if parti.scheme not in ("http", "https"):
                    raise ValueError("URL HTTP(S) richiesto anche nei redirect")
                if parti.username is not None or parti.password is not None:
                    raise ValueError("credenziali negli URL non consentite")
                if not parti.hostname or parti.hostname not in consentiti:
                    raise ValueError(f"host non consentito: {parti.hostname}")
                if corrente in visitati:
                    raise ValueError("redirect circolare: URL gia' visitato")
                visitati.add(corrente)
                headers = {"User-Agent": UA}
                if parti.hostname in ("www.sec.gov", "sec.gov", "data.sec.gov"):
                    from bellomberg.market_data.sec_edgar import _headers
                    headers = {**_headers(), "Accept": "*/*"}
                risposta = requests.get(corrente, timeout=timeout, headers=headers,
                                        allow_redirects=False)
                if risposta.status_code not in (301, 302, 303, 307, 308):
                    break
                posizione = risposta.headers.get("Location")
                if not posizione or not posizione.strip():
                    raise ValueError("redirect senza Location: documento non scaricato")
                if salto == limite_redirect:
                    raise ValueError(f"limite di {limite_redirect} redirect superato")
                corrente = urljoin(corrente, posizione)
        risposta.raise_for_status()
        contenuto = risposta.content
        if not contenuto:
            raise ValueError("documento vuoto")
        digest = hashlib.sha256(contenuto).hexdigest()
        nome = re.sub(r"[^A-Za-z0-9._-]", "_",
                      urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "documento"
        radice, est = os.path.splitext(nome[:100])
        os.makedirs(dest_dir, exist_ok=True)
        percorso = os.path.join(dest_dir, f"{radice}-{digest}{est}")
        with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False) as fh:
            temporaneo = fh.name
            fh.write(contenuto)
        try:
            os.link(temporaneo, percorso)  # pubblicazione atomica, MAI sovrascrivere
        except FileExistsError:
            with open(percorso, "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    raise ValueError("archivio alterato: hash del file esistente incoerente")
        return {"stato": "ok", "url": url, "url_finale": getattr(risposta, "url", None),
                "path": percorso, "sha256": digest, "bytes": len(contenuto),
                "content_type": risposta.headers.get("Content-Type")}
    except Exception as e:
        return {"stato": "errore", "url": url,
                "motivo": f"{type(e).__name__}: {e}"}
    finally:
        if temporaneo and os.path.isfile(temporaneo):
            os.unlink(temporaneo)


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
        if not (any(p in blob for p in _PAROLE_LUNGHE) or _RE_BREVI.search(blob)
                or re.search(r"/reports/\d+/document(?:[?#]|$)", href)):
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
