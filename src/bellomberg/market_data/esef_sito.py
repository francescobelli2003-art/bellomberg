"""Fonti dal sito dell'emittente (fase F): pacchetti ESEF ufficiali e PDF IR trovati da soli.

Rete gentile e limitata: solo HTTPS, solo il dominio registrabile del sito della società
(sottodomini ammessi), robots.txt rispettato, al massimo MAX_PAGINE pagine HTML per titolo e
profondita' MAX_PROFONDITA, pausa tra le richieste, pagine oltre MAX_PAGINA_BYTES scartate,
nessun JavaScript. L'esplorazione parte solo dal controllo giornaliero, dall'attivazione o da
una richiesta esplicita, mai dalla run del Consigliere: pipeline e impronta leggono la cache
(CACHE_GIORNI). Il pacchetto si scarica (max 80 MB) e si converte in locale (ixbrl_oim); LEI e
periodo si verificano sui fatti come per il repository (filing_esef.documento_esef).
"""
import calendar
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

MAX_PAGINE = 12
MAX_PROFONDITA = 2
MAX_PAGINA_BYTES = 3 * 1024 * 1024
MAX_REDIRECT = 4
PAUSA_S = 1.0
CACHE_GIORNI = 7
SITO_GIORNI = 30  # sito della societa' da yfinance
TIMEOUT_S = 20
TEMPO_PAGINA_S = 60      # tetto di orologio per pagina (il timeout di requests vale per lettura)
TEMPO_SITO_S = 180       # tetto di orologio per l'esplorazione di un titolo
TEMPO_GIRO_S = 900       # tetto del passo giornaliero (tutti i titoli)

_LEI = r"[A-Z0-9]{18}[0-9]{2}"
# Nome ufficiale dei pacchetti ESEF: LEI-AAAA-MM-GG-n[-lingua].xbri|zip (percorsi diversi ogni anno)
_PACCHETTO = re.compile(rf"^({_LEI})-(\d{{4}}-\d{{2}}-\d{{2}})-(\d+)(?:-([a-z]{{2}}))?\b[^/]*\.(xbri|zip)$", re.I)
# Pagine da visitare (prova reale 04/10/2026: parole larghe facevano visitare prodotti e archivi
# software): parole forti da investitori/bilanci, deboli, e negative che escludono la pagina.
_FORTI = re.compile(
    r"investor|investitor|investisseur|inversor|anleger|investoren|aktion[aä]r|azionist|shareholder"
    r"|bilanci|financial[\s_-]*(?:report|result|statement|information)|annual[\s_-]*report|gesch[aä]ftsbericht"
    r"|finanzbericht|rapport[\s_-]*annuel|results|risultati|r[ée]sultats|esef|publications?|pubblicazioni"
    r"|relazioni[\s_-]*finanziarie|reports?[\s_&-]*(?:and[\s_-]*)?presentations?|annual[\s_-]*reports|finanzberichte|filings|md\W?&?\W?a\b|regulated[\s_-]*information|informazione[\s_-]*regolamentata", re.I)
_DEBOLI = re.compile(r"report|relazion|bericht|documenti|documents|downloads|quarter|trimestr|semestr|interim|annual"
                     r"|financ|finanzi", re.I)
_NEGATIVE = re.compile(
    r"prodott|product|finanziament|mutu[oi]|prestit|conti-|carte|sostenib|sustainab|\besg\b|governance|career|carriere"
    r"|lavora|jobs?\b|press|news|notizie|avvisi|privacy|cookie|legal|contatt|contact|tools?\b|software|driver"
    r"|design-resources|support|shop|store|blog|equator|fornitor|supplier|club|landing|famiglie|business/prodotti"
    r"|login|accedi|eventi|events?\b|media\b|video|podcast|general-meeting|hauptversammlung|assemblea|\bagm\b"
    r"|proposals|guida|dialogo|politiche|policy|contacts|contatti|rating|dividend|calendar|calendario|glance"
    r"|fixed-income|anleihen|\bshare\b|-share|aktie\b|sedar|press-release", re.I)
# Sezioni intere da non visitare anche se il titolo parla di risultati (prova reale: comunicati stampa)
_NEGATIVE_PERCORSO = re.compile(r"/(?:media|press|stampa|news|notizie|comunicati|newsroom|blog|careers?|lavora|jobs)(?:/|$|-)", re.I)
_SUFFISSI_2 = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.jp", "co.nz", "com.br", "com.cn", "com.hk",
               "com.sg", "com.mx", "co.za", "com.tr", "co.kr", "co.in", "com.tw", "com.ar", "co.il"}
# Revisione R-SITI2 D7 (Public Suffix List non installata: regola DICHIARATA). Un'etichetta generica di secondo
# livello davanti a un ccTLD di due lettere e' un suffisso pubblico («zz.com.pl», «zz.or.jp», «zz.co.th»).
_SECONDO_LIVELLO = {"com", "co", "net", "org", "or", "ne", "ac", "gov", "gob", "go", "edu", "ltd", "plc", "nom",
                    "gv", "gen", "biz", "info", "mil", "sch", "nic", "ind", "firm", "web"}
# Hosting condivisi: ogni inquilino e' un dominio a se' (mai «stesso dominio» di un altro inquilino)
HOSTING_CONDIVISI = ("wixsite.com", "github.io", "netlify.app", "herokuapp.com", "azurewebsites.net", "web.app",
                     "firebaseapp.com", "blogspot.com", "wordpress.com", "squarespace.com", "weebly.com", "pages.dev",
                     "vercel.app", "myshopify.com", "webflow.io", "gitlab.io", "glitch.me", "onrender.com")


CARTELLA = None  # cache sotto DATA_DIR/filing_sito; i test la spostano in tmp


def _cache_dir(cache_dir=None):
    if cache_dir is not None:
        return Path(cache_dir)
    if CARTELLA is not None:
        return Path(CARTELLA)
    from bellomberg.core.paths import DATA_DIR
    return Path(DATA_DIR) / "filing_sito"


def _scrivi_json(path, dati):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(dati, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _leggi_json(path):
    try:
        dati = json.loads(Path(path).read_text(encoding="utf-8"))
        return dati if isinstance(dati, dict) else None
    except (OSError, ValueError):
        return None


def _nome_file(ticker):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(ticker).upper())[:40] + ".json"


def dominio_registrabile(host):
    """«group.esempio.example» -> «esempio.example». Regola dichiarata (niente Public Suffix List
    installata): suffissi a due livelli noti («co.uk»), etichetta generica + ccTLD («com.pl», «or.jp»),
    hosting condivisi come suffissi pubblici («zzsub.wixsite.com» e' il dominio dell'inquilino)."""
    h = str(host or "").lower().rstrip(".")
    parti = h.split(".")
    if len(parti) < 2:
        return None
    for condiviso in HOSTING_CONDIVISI:
        if h.endswith("." + condiviso):
            return ".".join(parti[-(len(condiviso.split(".")) + 1):])
    composto = (".".join(parti[-2:]) in _SUFFISSI_2
                or (len(parti[-1]) == 2 and parti[-1].isalpha() and parti[-2] in _SECONDO_LIVELLO))
    n = 3 if composto and len(parti) >= 3 else 2
    return ".".join(parti[-n:])


def stesso_dominio(url, dominio):
    """URL https dello stesso dominio registrabile (sottodomini compresi). `dominio` puo' essere una
    tupla (sito dell'emittente, sito IR scoperto): vale se l'URL sta in uno dei due."""
    if isinstance(dominio, (tuple, list, set, frozenset)):
        return any(stesso_dominio(url, d) for d in dominio if d)
    parti = urlsplit(url)
    host = (parti.hostname or "").lower()
    return (parti.scheme == "https" and parti.username is None and parti.password is None
            and bool(host) and (host == dominio or host.endswith("." + dominio)))


# solo codici di lingua veri: «/ir» non e' una lingua (revisione finale: la sezione IR spariva)
_LINGUE = "en|it|de|fr|es|nl|pt|sv|da|fi|no|nb|pl|cs|el|hu|ro|bg|hr|sk|sl|et|lv|lt|ga|mt|ja|zh|ko|ru|tr"


def _normalizza(url):
    """Chiave di una pagina: senza barra finale e senza il prefisso di lingua («/de/…» e «/…»
    sono la stessa pagina per l'esplorazione: prova reale, versioni tedesche doppie)."""
    p = urlsplit(url)
    percorso = re.sub(rf"^/(?:{_LINGUE})(?:-[a-z]{{2}})?(?=/|$)", "", (p.path or "/").rstrip("/"), flags=re.I) or "/"
    return urlunsplit((p.scheme, p.netloc.lower(), percorso, p.query, ""))


def _info_yfinance(ticker):
    import yfinance
    try:  # «XXX.FRA» -> simbolo Yahoo dagli alias dell'app (prova reale: .FRA sconosciuto a Yahoo)
        from bellomberg.cli.price_updater import data_ticker
        simbolo = data_ticker(ticker)
    except Exception:
        simbolo = ticker
    return yfinance.Ticker(simbolo).info or {}


def sito_societa(ticker, *, info_fn=None, cache_dir=None, oggi=None):
    """Sito web della societa' (yfinance `website`), in cache SITO_GIORNI. None se ignoto.
    Solo https (un http si prova come https)."""
    oggi = oggi or date.today()
    path = _cache_dir(cache_dir) / "siti" / _nome_file(ticker)
    voce = _leggi_json(path)
    if voce and voce.get("at"):
        try:
            if (oggi - date.fromisoformat(voce["at"][:10])).days < SITO_GIORNI:
                return voce.get("sito")
        except ValueError:
            pass
    try:
        info = (info_fn or _info_yfinance)(ticker) or {}
        grezzo = str(info.get("website") or "").strip()
        grezzo_ir = str(info.get("irWebsite") or "").strip()
    except Exception:
        return (voce or {}).get("sito")  # yfinance assente: vale l'ultimo noto
    sito, sito_ir = _https(grezzo), _https(grezzo_ir)
    _scrivi_json(path, {"at": oggi.isoformat(), "sito": sito, "sito_ir": sito_ir})
    return sito


def _https(grezzo):
    """URL normalizzato in https (un http si prova come https), None se non valido."""
    grezzo = str(grezzo or "").strip()
    if not grezzo:
        return None
    if "://" not in grezzo:
        grezzo = "https://" + grezzo
    p = urlsplit(grezzo)
    if not p.hostname or p.scheme not in ("http", "https") or p.username or p.password:
        return None
    return urlunsplit(("https", p.hostname.lower(), p.path or "/", "", ""))


def sito_ir_societa(ticker, *, info_fn=None, cache_dir=None, oggi=None):
    """Sito IR dichiarato da yfinance (`irWebsite`), in https; None se ignoto. Stessa cache del sito."""
    oggi = oggi or date.today()
    path = _cache_dir(cache_dir) / "siti" / _nome_file(ticker)
    voce = _leggi_json(path) or {}
    if "sito_ir" in voce:
        return voce.get("sito_ir")
    # cache scritta prima del 06/10 (o mai scritta): yfinance riletto. Revisione R-FONTI: il sito gia' noto
    # NON si cancella, e un errore di yfinance sale al chiamante (scopri lo dichiara: «irWebsite non letto»)
    info = (info_fn or _info_yfinance)(ticker) or {}
    sito_ir = _https(info.get("irWebsite"))
    if voce.get("sito"):
        _scrivi_json(path, {**voce, "sito_ir": sito_ir})
    else:
        _scrivi_json(path, {"at": oggi.isoformat(), "sito": _https(info.get("website")), "sito_ir": sito_ir})
    return sito_ir


class _Link(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.link, self._aperto = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._aperto = [href, ""]
                self.link.append(self._aperto)

    def handle_data(self, data):
        if self._aperto is not None and len(self._aperto[1]) < 300:
            self._aperto[1] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._aperto = None


_FILE_NEL_TESTO = re.compile(
    r"""["'(=]((?:https?:)?(?:\\?/)[^"'\s()<>]{1,300}?\.(?:pdf|xbri|zip))(?:\?[^"'\s<>]{0,200})?["')]""", re.I)
# Prova reale 05/10: dati della pagina in JSON con «/» al posto di «/» e percorsi con spazi
# («/Gruppo/Investor Relations/…pdf») tra virgolette: l'espressione sopra non li vedeva.
_FILE_TRA_VIRGOLETTE = re.compile(
    r"""(["'])((?:https?:)?/[^"'<>\r\n]{1,300}?\.(?:pdf|xbri|zip))(?:\?[^"'\s<>]{0,200})?\1""", re.I)


def estrai_link(html, base):
    """[(url assoluto senza frammento, testo)] dei link di una pagina.

    Prova reale: i PDF possono stare nei dati della pagina e non in <a href>; si
    raccolgono anche gli indirizzi di file nel testo statico (nessun JavaScript eseguito)."""
    p = _Link()
    try:
        p.feed(html)
    except Exception:
        pass
    noti = {h.strip() for h, _ in p.link}
    for m in _FILE_NEL_TESTO.finditer(html):
        grezzo = m.group(1).replace("\\/", "/")
        if grezzo not in noti:
            noti.add(grezzo)
            p.link.append([grezzo, ""])
    testo = re.sub(r"\\u002[fF]", "/", html).replace("\\/", "/")
    for m in _FILE_TRA_VIRGOLETTE.finditer(testo):
        grezzo = m.group(2).strip()
        if grezzo not in noti:
            noti.add(grezzo)
            p.link.append([grezzo, ""])
    out = []
    for href, testo in p.link:
        href = href.strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            url = urljoin(base, href)
        except ValueError:
            continue
        out.append((url.split("#", 1)[0], " ".join(testo.split())))
    return out


STATI_BLOCCO = (401, 403, 429)
MAX_5XX_DI_FILA = 3
MAX_JSON_BYTES = 8 * 1024 * 1024  # risposta JSON di una piattaforma IR (anni di documenti)  # pagine in errore del server di fila: il sito non sta bene, si riprova al giro dopo
TOKEN_BOT = "Bellomberg"  # nome del nostro agente per le regole «User-agent: Bellomberg» di robots.txt
_URL_NEL_TESTO = re.compile(r"""https?://[^\s'"<>]+|\?[^\s'"<>]*=[^\s'"<>]*""")


def motivo_eccezione(exc, n=160):
    """Tipo e testo dell'eccezione SENZA indirizzi ne' querystring (regola 6; revisione R-8 C7)."""
    return f"{type(exc).__name__}: {_URL_NEL_TESTO.sub('<url>', str(exc))[:n]}"


def frase_blocco(stato):
    """401/403: il sito rifiuta il bot; 429: limite di ritmo (revisione R-8: non e' un rifiuto)."""
    return f"sito limita il ritmo (HTTP {stato})" if stato == 429 else f"sito blocca i bot (HTTP {stato})"


class FuoriDominio(ValueError):
    """Redirect verso un altro dominio: `url` e' la destinazione (la scoperta del sito IR la segue con
    un navigatore del nuovo dominio, robots.txt compreso)."""

    def __init__(self, dominio, url):
        self.url = url
        super().__init__(f"fuori dal dominio {dominio}: {urlsplit(url).hostname}")


class TettoRichieste(RuntimeError):
    """Tetto di richieste del titolo raggiunto (revisione R-SITI2 D6): ci si ferma, dichiarato."""


class SitoBloccato(ValueError):
    """Il sito rifiuta il bot (HTTP 401/403) o limita il ritmo (429): esito dichiarato, ci si ferma."""

    def __init__(self, stato, dove=""):
        self.stato = stato
        super().__init__(frase_blocco(stato) + (f" su {dove}" if dove else ""))


class Navigatore:
    """GET di pagine HTML entro un dominio: redirect controllati, IP pubblici, tetto, pausa."""

    def __init__(self, dominio, *, get=None, dormi=None, pausa=PAUSA_S, tempo_max=TEMPO_SITO_S, orologio=None):
        self.dominio, self.pausa = dominio, pausa
        self._orologio = orologio or time.monotonic
        self._scadenza = self._orologio() + tempo_max
        self._get = get
        self._dormi = dormi or time.sleep
        self.richieste = 0
        self._robots = None
        self.budget = None  # BudgetTitolo condiviso (revisione R-SITI2 D6: il tetto vale dentro _http)
        self.bloccato = {}  # host -> stato HTTP con cui il sito rifiuta il bot (dichiarato nell'esito)
        self.robots_ignoto = {}  # host -> perche' robots.txt non si e' letto (RFC 9309: tutto vietato)

    def _http(self, url, corpo=None):
        import requests
        from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
        corrente = url
        for _ in range(MAX_REDIRECT + 1):
            if self._orologio() > self._scadenza:
                raise TimeoutError("tempo massimo dell'esplorazione superato")
            if not stesso_dominio(corrente, self.dominio):
                raise FuoriDominio(self.dominio, corrente)
            p = urlsplit(corrente)
            if self.budget is not None and self.budget.richieste() >= MAX_RICHIESTE_TITOLO:
                raise TettoRichieste(f"tetto di {MAX_RICHIESTE_TITOLO} richieste per titolo raggiunto")
            if corrente != url and p.path != "/robots.txt" and not self.consentito(corrente):
                # revisione R-SITI2 D9: robots.txt riletto a ogni salto di redirect
                raise PermissionError(f"robots.txt vieta {p.path[:80]} (raggiunto per redirect)")
            if self._get is None:
                _richiedi_indirizzi_pubblici(p.hostname, p.port or 443)
            if self.richieste:
                self._dormi(self.pausa)
            self.richieste += 1
            if corpo is None:
                r = (self._get or requests.get)(corrente, timeout=TIMEOUT_S, headers={"User-Agent": UA},
                                                allow_redirects=False, stream=True)
            elif self._get is not None:  # prove: la stessa funzione finta, col metodo dichiarato
                r = self._get(corrente, timeout=TIMEOUT_S, headers={"User-Agent": UA}, allow_redirects=False,
                              stream=True, method="POST", json=corpo)
            else:  # chiamata pubblica JSON di una piattaforma IR (come la fa la pagina)
                r = requests.post(corrente, timeout=TIMEOUT_S, headers={"User-Agent": UA}, allow_redirects=False,
                                  stream=True, json=corpo)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                if getattr(r, "raw", None) is not None:
                    r.close()
                corrente = urljoin(corrente, r.headers["Location"])
                continue
            return corrente, r
        raise ValueError("troppi redirect")

    def _corpo(self, r):
        return self._byte(r, MAX_PAGINA_BYTES, "pagina troppo grande").decode(r.encoding or "utf-8", errors="replace")

    def _byte(self, r, massimo, troppo):
        dichiarato = r.headers.get("Content-Length")
        if dichiarato and str(dichiarato).isdigit() and int(dichiarato) > massimo:
            raise ValueError(troppo)
        if getattr(r, "raw", None) is not None:
            pezzi, n = [], 0
            limite = min(self._orologio() + TEMPO_PAGINA_S, self._scadenza)
            for pezzo in r.iter_content(64 * 1024):
                n += len(pezzo)
                if n > massimo:
                    r.close()
                    raise ValueError(troppo)
                if self._orologio() > limite:  # server lento a gocce (revisione finale)
                    r.close()
                    raise TimeoutError("pagina troppo lenta")
                pezzi.append(pezzo)
            r.close()
            grezzo = b"".join(pezzi)
        else:
            grezzo = r.content or b""
            if len(grezzo) > massimo:
                raise ValueError(troppo)
        return grezzo

    def consentito(self, url):
        """robots.txt dell'host, letto una volta per host (RFC 9309, revisione R-8 C4): 200 = regole;
        404 e altri 4xx = consentito; 401/403 = sito che rifiuta i bot (tutto vietato); 5xx o
        irraggiungibile = tutto vietato col motivo in `robots_ignoto`. Le regole valgono sia per il
        nome del nostro agente («User-agent: Bellomberg») sia per lo UA completo."""
        host = urlsplit(url).hostname
        if self._robots is None:
            self._robots = {}
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                _, r = self._http(f"https://{host}/robots.txt")
                if r.status_code in (401, 403):
                    # convenzione di urllib.robotparser: robots.txt negato = tutto vietato (e dichiarato)
                    self.bloccato[host] = r.status_code
                    rp.parse([])
                    rp.disallow_all = True
                elif r.status_code >= 500:
                    self.robots_ignoto[host] = f"HTTP {r.status_code}"
                    rp.parse([])
                    rp.disallow_all = True
                else:
                    rp.parse(self._corpo(r).splitlines() if r.status_code == 200 else [])
            except Exception as exc:
                self.robots_ignoto[host] = motivo_eccezione(exc, 100)
                rp.parse([])
                rp.disallow_all = True
            self._robots[host] = rp
        from bellomberg.market_data.lettore_trimestrali import UA
        return self._robots[host].can_fetch(TOKEN_BOT, url) and self._robots[host].can_fetch(UA, url)

    def pagina(self, url):
        """(url finale, html) di una pagina HTML; eccezione se non e' HTML o va fuori dominio."""
        finale, r = self._http(url)
        if r.status_code in STATI_BLOCCO:
            self.bloccato[urlsplit(finale).hostname] = r.status_code
            raise SitoBloccato(r.status_code, urlsplit(finale).path[:80] or "/")
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        tipo = (r.headers.get("Content-Type") or "").lower()
        if tipo and "html" not in tipo:
            raise ValueError(f"non HTML ({tipo[:40]})")
        return finale, self._corpo(r)

    def dati_json(self, url, corpo=None, massimo=None):
        """JSON di una chiamata pubblica di piattaforma IR (GET, o POST con `corpo`): robots.txt dell'host,
        ritmo, IP pubblici; vietato, bloccato, HTTP diverso da 200 o JSON illeggibile = eccezione dichiarata."""
        if not self.consentito(url):
            host = urlsplit(url).hostname
            if self.bloccato.get(host):
                raise SitoBloccato(self.bloccato[host], "robots.txt")
            if self.robots_ignoto.get(host):
                raise PermissionError(f"robots.txt non leggibile ({self.robots_ignoto[host]})")
            raise PermissionError(f"robots.txt vieta {urlsplit(url).path[:80]}")
        finale, r = self._http(url, corpo)
        if r.status_code in STATI_BLOCCO:
            self.bloccato[urlsplit(finale).hostname] = r.status_code
            raise SitoBloccato(r.status_code, urlsplit(finale).path[:80])
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        try:
            return json.loads(self._byte(r, massimo or MAX_JSON_BYTES, "risposta troppo grande").decode("utf-8-sig"))
        except ValueError as exc:
            if str(exc) == "risposta troppo grande":
                raise
            raise ValueError("risposta non JSON") from exc

    def prima_pagina_pdf(self, url, max_bytes=None):
        """Testo della prima pagina di un PDF dello stesso dominio (robots.txt, pausa e redirect come
        le pagine; tetto MAX_PDF_PRIMA_PAGINA). Eccezione dichiarata se vietato, bloccato o illeggibile."""
        if not self.consentito(url):
            raise PermissionError(f"robots.txt vieta {urlsplit(url).path[:80]}")
        finale, r = self._http(url)
        if r.status_code in STATI_BLOCCO:
            self.bloccato[urlsplit(finale).hostname] = r.status_code
            raise SitoBloccato(r.status_code, urlsplit(finale).path[:80])
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        grezzo = self._byte(r, max_bytes or MAX_PDF_PRIMA_PAGINA, "PDF troppo grande")
        if not grezzo.startswith(b"%PDF-"):
            raise ValueError("non e' un PDF")
        import io
        from pypdf import PdfReader
        lettore = PdfReader(io.BytesIO(grezzo))
        if not lettore.pages:
            raise ValueError("PDF senza pagine")
        return (lettore.pages[0].extract_text() or "")[:4000]


def _anni(testo):
    return [int(a) for a in re.findall(r"(?<!\d)(20[0-4]\d)(?!\d)", testo)]


def _punteggio(url, testo, oggi=None):
    """Priorita' di una pagina da visitare, sull'ultimo tratto del percorso e sul testo del link
    (prova reale: «investor-relations» in ogni sotto-pagina le rendeva tutte uguali); 0 = no."""
    # tratti limitati: le espressioni restano lineari anche su link ostili (revisione finale)
    chiave = unquote(urlsplit(url).path).rstrip("/").rsplit("/", 1)[-1][:200] + " " + testo[:200]
    forti, deboli = len(_FORTI.findall(chiave)), len(_DEBOLI.findall(chiave))
    if _NEGATIVE.search(chiave) and not forti:
        return 0
    punti = 3 * forti + deboli
    anni = _anni(chiave)
    if punti and anni:  # archivi per anno: prima gli ultimi tre esercizi, i vecchi mai
        punti = punti + 2 if max(anni) >= (oggi or date.today()).year - 3 else 0
    return punti


def _sottopagina(href, padre):
    """Una pagina dentro la stessa sezione («…/bilanci-e-relazioni/2025») non e' un livello in piu'."""
    a, b = urlsplit(href), urlsplit(padre)
    return a.hostname == b.hostname and a.path.rstrip("/").startswith(b.path.rstrip("/") + "/") and len(b.path) > 1


def esplora(sito, *, navigatore=None, max_pagine=MAX_PAGINE, max_profondita=MAX_PROFONDITA, oggi=None):
    """Visita le pagine IR del sito, la piu' promettente prima: {"pagine", "link", "motivi"}.

    Si seguono solo i link dello stesso dominio con parole da investitori/bilanci (negative
    escluse), fino a `max_profondita` livelli dalla home; le sotto-pagine della stessa sezione
    (anni di un archivio) restano al livello della pagina. `link` raccoglie tutti i link di
    tutte le pagine visitate (anche verso file: pacchetti, PDF), una volta sola."""
    dominio = dominio_registrabile(urlsplit(sito).hostname)
    if not dominio:
        return {"pagine": [], "link": [], "motivi": ["sito della societa' non valido"]}
    nav = navigatore or Navigatore(dominio)
    coda, visti, pagine, link, motivi, noti = [(0, 99, sito)], set(), [], [], [], set()
    errori_server, redirect_esterno, piattaforme = 0, None, []
    while coda and len(pagine) < max_pagine:
        coda.sort(key=lambda x: (-x[1], x[0]))
        prof, punti_pagina, url = coda.pop(0)
        chiave = _normalizza(url)
        if chiave in visti:
            continue
        visti.add(chiave)
        try:
            if not nav.consentito(url):
                stato = getattr(nav, "bloccato", {}).get(urlsplit(url).hostname)
                if stato:  # robots.txt stesso negato: il sito rifiuta i bot, ci si ferma
                    motivi.append(f"{frase_blocco(stato)} su robots.txt")
                    break
                ignoto = getattr(nav, "robots_ignoto", {}).get(urlsplit(url).hostname)
                if ignoto:  # RFC 9309: robots.txt non leggibile = nessuna pagina, si riprova al giro dopo
                    motivi.append(f"robots.txt non leggibile ({ignoto}): esplorazione rinviata")
                    break
                motivi.append(f"robots.txt esclude {urlsplit(url).path[:80]}")
                continue
            finale, html = nav.pagina(url)
        except SitoBloccato as exc:
            motivi.append(str(exc))
            break  # mai insistere su un sito che rifiuta il bot
        except TettoRichieste as exc:
            motivi.append(str(exc))
            break
        except Exception as exc:
            if isinstance(exc, FuoriDominio) and url == sito and not pagine:
                redirect_esterno = exc.url  # la pagina di partenza rimanda a un altro dominio
            motivi.append(f"{urlsplit(url).path[:80] or '/'}: {motivo_eccezione(exc, 120)}")
            errori_server = errori_server + 1 if str(exc).startswith("HTTP 5") else 0
            if errori_server >= MAX_5XX_DI_FILA:
                motivi.append(f"sito non disponibile (HTTP 5xx su {errori_server} pagine di fila): esplorazione "
                              "rinviata")
                break
            continue
        errori_server = 0
        if _normalizza(finale) != chiave and _normalizza(finale) in visti:
            continue  # redirect verso una pagina gia' letta (prova reale)
        visti.add(_normalizza(finale))
        pagine.append(finale)
        from bellomberg.market_data.piattaforme_ir import rileva
        for p in rileva(html, finale):  # elenco documenti caricato da JavaScript: la chiamata pubblica
            if all((p["tipo"], p["url"]) != (x["tipo"], x["url"]) for x in piattaforme):
                piattaforme.append(p)
        for href, testo in estrai_link(html, finale):
            if href not in noti:
                noti.add(href)
                link.append({"url": href, "testo": testo[:200], "pagina": finale})
            if (not stesso_dominio(href, dominio) or _normalizza(href) in visti
                    or re.search(r"\.(pdf|zip|xbri|xhtml|xlsx?|docx?|pptx?|jpe?g|png|gif|svg|mp4|mp3)$",
                                 urlsplit(href).path, re.I)):
                continue
            if _NEGATIVE_PERCORSO.search(unquote(urlsplit(href).path)):
                continue
            sotto = _sottopagina(href, finale)
            livello = prof if sotto else prof + 1
            punti = _punteggio(href, testo, oggi)
            coda_url = unquote(urlsplit(href).path).rstrip("/").rsplit("/", 1)[-1][:200] + " " + testo[:200]
            if sotto and prof and not _NEGATIVE.search(coda_url):
                # schede e anni di una pagina IR («…/bilanci-e-relazioni/2025»): valgono quanto la
                # pagina, i tre esercizi recenti un po' di piu', i vecchi mai
                anni = _anni(coda_url)
                if anni and max(anni) < (oggi or date.today()).year - 3:
                    punti = 0
                else:
                    punti = max(punti, min(punti_pagina, 6) + (2 if anni else 0))
            if livello <= max_profondita and punti:
                # i link di una pagina IR valgono un po' di piu' di quelli della home
                coda.append((livello, punti + (2 if punti_pagina >= 3 and prof else 0), href))
    if coda and len(pagine) >= max_pagine:
        motivi.append(f"limite di {max_pagine} pagine raggiunto")
    return {"pagine": pagine, "link": link, "motivi": motivi, "richieste": nav.richieste,
            "bloccato": next(iter(getattr(nav, "bloccato", {}).values()), None),
            "robots_ignoto": next(iter(getattr(nav, "robots_ignoto", {}).values()), None),
            "redirect_esterno": redirect_esterno, "piattaforme": piattaforme}


def pacchetti(link):
    """Pacchetti ESEF tra i link: [{"url", "lei", "period_end", "versione", "lingua"}] (nome
    ufficiale LEI-data-n[-lingua]; la verifica vera e' sui fatti dopo la conversione)."""
    out, visti = [], set()
    for voce in link:
        url = voce["url"]
        nome = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        m = _PACCHETTO.match(nome)
        if not m or url in visti or urlsplit(url).scheme != "https":
            continue
        visti.add(url)
        try:
            date.fromisoformat(m.group(2))
        except ValueError:
            continue
        out.append({"url": url, "lei": m.group(1).upper(), "period_end": m.group(2), "versione": int(m.group(3)),
                    "lingua": (m.group(4) or "").lower() or None, "formato": m.group(5).lower()})
    return out


# ------------------------------------------------------------------ sito IR scoperto (seguito main 06/10)
# yfinance da' spesso il sito commerciale: il sito IR vero sta su un sottodominio, su un dominio dedicato
# o sul sito di gruppo. Lo si cerca dalla home (link «Investor Relations»), da `irWebsite` di yfinance e
# dai candidati standard; il dominio trovato diventa il sito IR (dichiarato «scoperto da <home>").
# robots.txt e 401/403/429 rispettati su ogni dominio; l'identita' si verifica sempre sul documento.
_IR_TESTO = re.compile(r"investor[\s_-]*relations?|\binvestors?\b|investidor|investisseur|inversor|anleger"
                       r"|investoren|azionist|shareholder|投資家|株主", re.I)
_IR_SIGLA = re.compile(r"(?<![A-Za-z])IR(?![A-Za-z])")  # «IR» maiuscolo (non «Ir para…»)
_GRUPPO_TESTO = re.compile(r"\bglobal\b|\bgroup\b|gruppo|groupe|grupo|corporate|holdings?\b", re.I)
MAX_CANDIDATI_IR = 8
MAX_PIATTAFORME = 3  # chiamate di piattaforma per esplorazione
MAX_SALTI_IR = 2  # redirect verso altri domini seguiti per candidato


def _candidati_ir(sito, visita, sito_ir_dichiarato, con_documenti):
    """[(url https, origine)] dei candidati a sito IR, in ordine: irWebsite di yfinance, link IR della
    home verso un altro host, poi (solo se la home non ha dato documenti) i candidati standard e i siti
    di gruppo linkati dalla home. Esclusi host gia' esplorati quando la home ha gia' dato documenti."""
    home = (urlsplit(sito).hostname or "").lower()
    dom = dominio_registrabile(home)
    visti = {_normalizza(p) for p in visita.get("pagine") or []}
    host_visti = {(urlsplit(p).hostname or "").lower() for p in visita.get("pagine") or []}
    prima = (visita.get("pagine") or [None])[0]
    out = []

    def aggiungi(url, origine):
        u = _https(url)
        h = (urlsplit(u).hostname or "").lower() if u else ""
        if (not u or _normalizza(u) in visti or u in (x for x, _ in out)
                or (con_documenti and (h == home or h in host_visti))):
            return
        out.append((u, origine))
    if sito_ir_dichiarato:
        aggiungi(sito_ir_dichiarato, "irWebsite di yfinance")
    gruppi = []
    for v in visita.get("link") or []:
        if v.get("pagina") != prima:
            continue
        u, testo = v["url"], v.get("testo") or ""
        h = (urlsplit(u).hostname or "").lower()
        if not h or h == home or urlsplit(u).path.lower().endswith((".pdf", ".zip", ".xbri")):
            continue
        coda = unquote(urlsplit(u).path).rstrip("/").rsplit("/", 1)[-1]
        if _IR_TESTO.search(testo) or _IR_SIGLA.search(testo) or _IR_TESTO.search(coda) or h.split(".")[0] in (
                "ir", "investor", "investors"):
            aggiungi(u, f"link «{' '.join(testo.split())[:40]}» dalla home")
        elif _GRUPPO_TESTO.search(testo) and dominio_registrabile(h) != dom and h not in gruppi:
            gruppi.append(h)
    if not con_documenti and dom:
        # prima i siti di gruppo linkati dalla home (prova dal vivo 06/10: la holding quotata sta li'),
        # poi i candidati standard del dominio
        for h in gruppi[:2]:
            for percorso in ("/investors", "/en/investors"):
                aggiungi(f"https://{h}{percorso}", f"sito di gruppo {h} linkato dalla home")
        for c in (f"https://investors.{dom}/", f"https://investor.{dom}/", f"https://ir.{dom}/",
                  f"https://{home}/investors", f"https://{home}/en/investors"):
            aggiungi(c, "candidato standard")
    return out[:MAX_CANDIDATI_IR]


MAX_RICHIESTE_TITOLO = 80  # richieste HTTP per titolo, tutta la scoperta compresa (home, IR, piattaforme, PDF)


class BudgetTitolo:
    """UN orologio e UN tetto di richieste per titolo (revisione R-FONTI S4): ogni Navigatore creato da
    `scopri` (home, candidati e salti del sito IR, piattaforme, prime pagine) lo condivide."""

    def __init__(self, nav):
        self.navi = [nav] if nav is not None else []
        self.scadenza = getattr(nav, "_scadenza", None)
        if nav is not None and hasattr(nav, "budget"):
            nav.budget = self

    def entro(self, nav):
        if nav is not None:
            if self.scadenza is not None and hasattr(nav, "_scadenza"):
                nav._scadenza = min(nav._scadenza, self.scadenza)
            if hasattr(nav, "budget"):
                nav.budget = self
            self.navi.append(nav)
        return nav

    def richieste(self):
        return sum(getattr(n, "richieste", 0) or 0 for n in self.navi)

    def esaurito(self):
        """Motivo se il tempo o le richieste del titolo sono finiti, altrimenti None."""
        if self.scadenza is not None and any(hasattr(n, "_orologio") and n._orologio() > self.scadenza
                                             for n in self.navi):
            return "tempo massimo dell'esplorazione del titolo superato"
        if self.richieste() >= MAX_RICHIESTE_TITOLO:
            return f"tetto di {MAX_RICHIESTE_TITOLO} richieste per titolo raggiunto"
        return None


def scopri_sito_ir(sito, visita, *, sito_ir_dichiarato=None, con_documenti=False, navigatore_fn=None, oggi=None,
                   budget=None):
    """{"visita", "nav", "sito_ir", "origine", "motivi"}: prima esplorazione riuscita fra i candidati a
    sito IR (robots.txt e blocchi di ogni dominio rispettati; redirect verso altri domini seguiti fino a
    MAX_SALTI_IR con un navigatore del nuovo dominio). `visita` None se nessun candidato regge."""
    fabbrica = navigatore_fn or (lambda d: Navigatore(d))
    bloccati = {dominio_registrabile(h) for h in (visita.get("bloccati") or [])}
    motivi, provati = [], 0
    for url, origine in _candidati_ir(sito, visita, sito_ir_dichiarato, con_documenti):
        for salto in range(MAX_SALTI_IR + 1):
            host = (urlsplit(url).hostname or "").lower()
            dom = dominio_registrabile(host)
            if not dom or dom in bloccati:
                motivi.append(f"sito IR {host}: dominio che rifiuta il bot o vieta l'esplorazione, non provato")
                break
            fermo = budget.esaurito() if budget is not None else None
            if fermo:
                motivi.append(f"{fermo}: scoperta del sito IR interrotta ({provati} indirizzi provati)")
                return {"visita": None, "nav": None, "sito_ir": None, "origine": None, "motivi": motivi}
            provati += 1
            nav = fabbrica(dom)
            if budget is not None:
                budget.entro(nav)
            vis = esplora(url, navigatore=nav, oggi=oggi)
            if vis["pagine"]:
                via = " (dopo redirect)" if salto else ""
                return {"visita": vis, "nav": nav, "sito_ir": vis["pagine"][0], "motivi": motivi,
                        "origine": f"{origine}{via}, scoperto da {sito}"}
            # dichiarati: blocchi, robots.txt che vieta o in errore del server; un host che non esiste
            # (DNS) o non risponde e' solo un candidato non raggiunto (contato sotto)
            if vis.get("bloccato") or str(vis.get("robots_ignoto") or "").startswith("HTTP") or any(
                    str(m).startswith("robots.txt esclude") for m in vis["motivi"]):
                motivi.append(f"sito IR {host} ({origine}): {vis['motivi'][0] if vis['motivi'] else 'nessuna pagina'}")
                if vis.get("bloccato"):
                    bloccati.add(dom)
                break
            if vis.get("redirect_esterno") and salto < MAX_SALTI_IR:
                url = _https(vis["redirect_esterno"]) or url
                continue
            break
    if provati:
        motivi.append(f"sito IR non trovato oltre il sito della societa' ({provati} indirizzi provati)")
    return {"visita": None, "nav": None, "sito_ir": None, "origine": None, "motivi": motivi}


class Domini(tuple):
    """(sito della societa', sito IR scoperto); `gruppo` = il sito IR viene da un link «Group» della home
    (revisione R-FONTI S2: etichetta «soggetto da verificare», il documento deve provarlo)."""
    gruppo = False


def domini_voce(voce):
    """Domini dei documenti dell'emittente: il sito (yfinance) e, se diverso, il sito IR scoperto."""
    a = dominio_sito((voce or {}).get("sito"))
    b = dominio_sito((voce or {}).get("sito_ir"))
    if b and a and b != a:
        d = Domini((a, b))
        d.gruppo = "sito di gruppo" in str((voce or {}).get("sito_ir_origine") or "")
        return d
    return a or b


def scopri(ticker, *, lei=None, oggi=None, forza=False, cache_dir=None, sito_fn=None, navigatore_fn=None,
           prima_pagina_fn=None, sito_ir_fn=None):
    """Esplora il sito di `ticker` (cache CACHE_GIORNI) e salva pacchetti e link utili.

    Esito: {"ticker", "at", "sito", "pagine", "pacchetti", "pdf", "prime_pagine", "accesso",
    "motivi"}; `pdf` = link a PDF con testo (per la ricerca dei PDF IR), `prime_pagine` = testo
    della prima pagina dei pochi PDF che senza non si decidono, `accesso` = {"stato", "motivo"}
    (bloccato / robots_vieta dichiarati). Chiamare solo fuori dalla run del Consigliere."""
    oggi = oggi or date.today()
    path = _cache_dir(cache_dir) / _nome_file(ticker)
    voce = _leggi_json(path)
    if voce:
        try:
            # scadenza sul giorno scritto con lo stesso orologio di `oggi` («giorno»); `at` e'
            # l'ora vera (limite di un'ora sotto). Voci vecchie senza «giorno»: data di `at`
            giorni = (oggi - date.fromisoformat(str(voce.get("giorno") or voce.get("at"))[:10])).days
            # esplorazione fallita (nessuna pagina letta): si riprova al giro successivo, non fra 7 giorni
            if not forza and giorni < (1 if voce.get("fallita") else CACHE_GIORNI):
                return voce
            # anche su richiesta, al massimo un'esplorazione l'ora per titolo
            if forza and datetime.now() - datetime.fromisoformat(str(voce.get("at"))) < timedelta(hours=1):
                return voce
        except ValueError:
            pass
    sito = (sito_fn or (lambda t: sito_societa(t, cache_dir=cache_dir, oggi=oggi)))(ticker)
    if sito_ir_fn is None:  # con un sito iniettato (prove) nessun irWebsite, salvo richiesta esplicita
        sito_ir_fn = (lambda t: None) if sito_fn else (lambda t: sito_ir_societa(t, cache_dir=cache_dir, oggi=oggi))
    esito = {"ticker": ticker, "at": datetime.now().isoformat(timespec="seconds"), "giorno": oggi.isoformat(),
             "sito": sito,
             "pagine": [], "pacchetti": [], "pdf": [], "motivi": []}
    # GENERALITA' UE (Opus 5.5): prima data in cui ogni pacchetto e' stato visto sul sito (dalle esplorazioni
    # precedenti, anche fallite; voci senza la data: il giorno di quell'esplorazione)
    gia_visti = {u: g for u, g in ((voce or {}).get("pacchetti_visti") or {}).items() if g}
    for p in (voce or {}).get("pacchetti") or []:
        if isinstance(p, dict) and p.get("url"):
            prima = p.get("visto_il") or (voce.get("giorno") or str(voce.get("at") or "")[:10] or None)
            if prima:
                gia_visti[p["url"]] = min(x for x in (gia_visti.get(p["url"]), prima) if x)
    esito["pacchetti_visti"] = dict(gia_visti)
    if not sito:
        esito["motivi"].append("sito della societa' non noto (yfinance)")
        esito["accesso"] = {"stato": "sito_ignoto", "motivo": "sito della societa' non noto (yfinance)"}
    else:
        dominio = dominio_registrabile(urlsplit(sito).hostname)
        nav = (navigatore_fn or (lambda d: Navigatore(d)))(dominio) if dominio else None
        # un solo orologio e un solo tetto di richieste per il titolo (R-FONTI S4; dentro _http: R-SITI2 D6)
        budget = BudgetTitolo(nav)
        visita = esplora(sito, navigatore=nav)
        esito.update(pagine=list(visita["pagine"]), motivi=list(visita["motivi"]))
        # piattaforme IR (Q4, MZiQ): la chiamata pubblica JSON della pagina, col robots.txt dell'host
        navi_piattaforme, esito["piattaforme"], da_piattaforme = {}, [], []

        def leggi_piattaforme(lista):
            from bellomberg.market_data import piattaforme_ir
            for p in lista:
                if len(esito["piattaforme"]) >= MAX_PIATTAFORME:  # tetto per TITOLO (R-FONTI S5)
                    break
                fermo = budget.esaurito()
                if fermo:
                    esito["motivi"].append(f"{fermo}: piattaforme IR non lette")
                    break
                if any(x.get("_url") == p["url"] for x in esito["piattaforme"]):
                    continue
                d = dominio_registrabile(urlsplit(p["url"]).hostname)
                riga = {"tipo": p["tipo"], "pagina": p["pagina"], "documenti": 0, "motivo": None, "_url": p["url"]}
                try:
                    if d not in navi_piattaforme:
                        navi_piattaforme[d] = budget.entro((navigatore_fn or (lambda x: Navigatore(x)))(d))
                    nav_p = navi_piattaforme[d]
                    voci = piattaforme_ir.documenti(p, nav_p.dati_json(p["url"], p.get("corpo")), oggi=oggi)
                    riga["documenti"] = len(voci)
                    da_piattaforme.extend(voci)
                except (SitoBloccato, PermissionError) as exc:
                    riga["motivo"] = str(exc)  # robots.txt / blocco: fermo, dichiarato
                except Exception as exc:
                    riga["motivo"] = motivo_eccezione(exc)
                esito["piattaforme"].append(riga)
        leggi_piattaforme(visita.get("piattaforme") or [])
        # sito IR vero (seguito main 06/10): su un altro host o dominio, scoperto e dichiarato
        pdf_home = [v for v in visita["link"] if urlsplit(v["url"]).path.lower().endswith(".pdf")] + da_piattaforme
        lei_noto = lei or lei_dal_negozio(ticker)
        chiusura_home = chiusura_esercizio_emittente(lei_noto, pacchetti(visita["link"]))["chiusura"]
        con_documenti = bool(scegli_pdf(pdf_home, oggi=oggi, dominio=dominio, chiusura_esercizio=chiusura_home))
        try:
            dichiarato = sito_ir_fn(ticker)
        except Exception as exc:
            dichiarato = None
            esito["motivi"].append(f"irWebsite non letto ({motivo_eccezione(exc, 80)})")
        bloccati = list((getattr(nav, "bloccato", None) or {}).keys())
        if _accesso(visita)["stato"] in ("bloccato", "robots_vieta", "robots_illeggibile"):
            bloccati.append(urlsplit(sito).hostname)  # la home vieta o rifiuta: tutto il dominio resta fermo
        ir = scopri_sito_ir(sito, {**visita, "bloccati": bloccati},
                            sito_ir_dichiarato=dichiarato, con_documenti=con_documenti,
                            navigatore_fn=navigatore_fn, oggi=oggi, budget=budget)
        esito["motivi"] += ir["motivi"]
        domini = dominio
        if ir["visita"]:
            vir = ir["visita"]
            esito.update(sito_ir=ir["sito_ir"], sito_ir_origine=ir["origine"])
            esito["pagine"] += vir["pagine"]
            esito["motivi"] += vir["motivi"]
            noti = {v["url"] for v in visita["link"]}
            visita = {**visita, "link": visita["link"] + [v for v in vir["link"] if v["url"] not in noti],
                      "pagine": visita["pagine"] or vir["pagine"]}
            domini = domini_voce(esito)
            leggi_piattaforme(vir.get("piattaforme") or [])
        dominio_ir = domini[1] if isinstance(domini, tuple) else None
        for riga in esito["piattaforme"]:
            riga.pop("_url", None)
        # solo pacchetti dal dominio esplorato (il LEI si riverifica comunque sui fatti)
        esito["pacchetti"] = [p for p in pacchetti(visita["link"]) if stesso_dominio(p["url"], domini)]
        # GENERALITA' UE (Opus 5.5): giorno in cui il pacchetto e' stato visto sul sito la PRIMA volta (limite
        # superiore della pubblicazione: serve al cutoff dello storico, niente look-ahead). Voci precedenti senza
        # la data: il giorno di quell'esplorazione
        for p in esito["pacchetti"]:
            p["visto_il"] = min(x for x in (gia_visti.get(p["url"]), oggi.isoformat()) if x)
            gia_visti[p["url"]] = p["visto_il"]
        # v2: la prima data resta anche se un'esplorazione successiva fallisce o non rivede il pacchetto
        esito["pacchetti_visti"] = {u: g for u, g in gia_visti.items() if g}
        # chiusura dell'esercizio dell'emittente (pacchetti e repository ESEF in cache): decide le relazioni senza
        # aggettivo con la data nel nome (esercizio non solare), dichiarata nella voce
        esito["chiusura_esercizio"] = chiusura_esercizio_emittente(lei_noto, esito["pacchetti"])
        chiusura = esito["chiusura_esercizio"]["chiusura"]
        esito["fallita"] = not esito["pagine"]
        noti_pdf = {v["url"] for v in da_piattaforme}
        esito["pdf"] = (da_piattaforme + [v for v in visita["link"] if urlsplit(v["url"]).scheme == "https"
                                          and urlsplit(v["url"]).path.lower().endswith(".pdf")
                                          and v["url"] not in noti_pdf])[:500]
        # PDF col nome generico o senza periodo: prima pagina letta (pochi, stesso dominio, robots.txt)
        prime = {}
        if nav is not None and not visita.get("bloccato"):
            # PDF su un altro host linkato dalla pagina IR (decisione PM 06/10 sera): navigatore di
            # QUELL'host, col suo robots.txt, il suo ritmo e i suoi blocchi
            navi = {dominio: nav}
            if dominio_ir:
                navi[dominio_ir] = ir["nav"]

            def leggi(url):
                if prima_pagina_fn is not None:
                    return prima_pagina_fn(url)
                d = dominio_registrabile(urlsplit(url).hostname) or dominio
                if d not in navi:
                    navi[d] = budget.entro((navigatore_fn or (lambda x: Navigatore(x)))(d))
                return navi[d].prima_pagina_pdf(url)
            pronto = prima_pagina_fn is not None or hasattr(nav, "prima_pagina_pdf")
            for url in (da_leggere_prima_pagina(esito["pdf"], domini, chiusura_esercizio=chiusura) if pronto else []):
                fermo = budget.esaurito()
                if fermo:
                    prime[url] = {"errore": f"{fermo}: prima pagina non letta"}
                    continue
                try:
                    prime[url] = {"testo": str(leggi(url))[:4000]}
                except Exception as exc:
                    prime[url] = {"errore": motivo_eccezione(exc)}
                    if isinstance(exc, (SitoBloccato, PermissionError)):
                        prime[url]["vietato"] = True  # robots.txt / blocco: fermo, mai aggirato
                    if isinstance(exc, SitoBloccato) and stesso_dominio(url, dominio):
                        break
        esito["prime_pagine"] = prime
        esito["accesso"] = _accesso(visita)
        if lei and not any(p["lei"] == str(lei).upper() for p in esito["pacchetti"]):
            esito["motivi"].append(f"nessun pacchetto ESEF col LEI {str(lei).upper()} nelle pagine visitate")
        # PDF delle relazioni (decisione PM 05/10): esito in testa ai motivi, origine dichiarata
        scelta = scegli_pdf(esito["pdf"], oggi=oggi, prime_pagine=prime, dominio=domini, chiusura_esercizio=chiusura)
        if scelta:
            per_tipo = ", ".join(f"{n} {t}" for t, n in sorted(scelta["ammessi_per_tipo"].items()))
            frase = (f"{nome_documento(scelta['ultimo'])} al {scelta['ultimo']['periodo']} in PDF dal "
                     f"{scelta['etichetta']} (periodo {scelta['periodo_stato'].replace('_', ' ')}; "
                     f"{scelta['candidati']} PDF ammessi: {per_tipo}; {scelta['scartati_totale']} scartati col motivo)")
        elif esito["pdf"]:
            frase = riepilogo_scarti(esito["pdf"], oggi=oggi, prime_pagine=prime, dominio=domini,
                                     chiusura_esercizio=chiusura)
        else:
            frase = None
        if frase:
            esito["motivi"].insert(1 if esito["accesso"]["stato"] != "ok" else 0, frase)
    _scrivi_json(path, esito)
    return esito


def _accesso(visita):
    """Esito dell'accesso al sito, dichiarato: ok | bloccato (HTTP 401/403/429) | robots_illeggibile |
    robots_vieta | non_raggiunto."""
    if visita.get("bloccato"):
        return {"stato": "bloccato", "motivo": frase_blocco(visita["bloccato"])}
    if visita.get("robots_ignoto") and not visita.get("pagine"):
        return {"stato": "robots_illeggibile",
                "motivo": f"robots.txt non leggibile ({visita['robots_ignoto']}): esplorazione rinviata"}
    motivi = visita.get("motivi") or []
    if not visita.get("pagine") and any(m.startswith("robots.txt esclude") for m in motivi):
        return {"stato": "robots_vieta", "motivo": "robots.txt vieta l'esplorazione delle pagine del sito"}
    if not visita.get("pagine"):
        return {"stato": "non_raggiunto", "motivo": "nessuna pagina letta" + (f": {motivi[0]}" if motivi else "")}
    return {"stato": "ok", "motivo": None}


def entro_il_cutoff(quando, fino_al, oggi=None):
    """True se un documento noto dal giorno `quando` (ISO) era noto alla run con cutoff `fino_al` (ISO).

    Stessa regola dello storico SEC (sec_xbrl.depositato_entro): con un cutoff storico lo stesso giorno puo'
    essere successivo all'ora della decisione ed e' escluso; con fino_al = oggi vale <=. Data ignota: no."""
    if not fino_al:
        return True
    if not quando:
        return False
    quando, fino_al = str(quando)[:10], str(fino_al)[:10]
    if len(quando) != 10:  # v2: data troncata o malformata («2025») = ignota, come sec_xbrl.depositato_entro
        return False
    oggi = str(oggi or date.today().isoformat())[:10]
    return quando < fino_al or (quando == fino_al and fino_al >= oggi)


def _visto_il(p, voce):
    """(giorno, base) in cui il pacchetto risulta sul sito: `visto_il` registrato dall'esplorazione (prima volta
    in cui e' stato visto) o, per le voci di cache precedenti, il giorno dell'esplorazione che lo ha trovato.
    E' un limite superiore della pubblicazione (il pacchetto era online quel giorno); None se ignoto."""
    registrata = p.get("visto_il") or (voce.get("pacchetti_visti") or {}).get(p.get("url"))
    if registrata:
        return str(registrata)[:10], "prima esplorazione del sito che lo ha trovato"
    giorno = voce.get("giorno") or str(voce.get("at") or "")[:10] or None
    if giorno:
        return str(giorno)[:10], "esplorazione del sito che lo ha trovato (voce di cache senza la prima data)"
    return None, None


def righe_da_cache(lei, *, cache_dir=None, preferita="en", fino_al=None, esclusi=None):
    """Righe di catalogo dai pacchetti trovati sul sito (sola cache, nessuna rete): una per
    esercizio, lingua preferita se c'e', poi la versione piu' alta. Forma delle righe del
    repository con `origine: "sito"` e `json_url` = URL del pacchetto.

    GENERALITA' UE (Opus 5.5), niente look-ahead: con `fino_al` (cutoff ISO della run) restano solo i pacchetti
    visti sul sito entro il cutoff (la data registrata nella cache e' un limite superiore della pubblicazione);
    gli altri - data dopo il cutoff o ignota - si aggiungono a `esclusi` (lista) col motivo, mai zitti. Senza
    `fino_al` il risultato e' quello di sempre."""
    lei = str(lei or "").upper()
    cartella = _cache_dir(cache_dir)
    trovati = []
    try:
        file = sorted(cartella.glob("*.json"))
    except OSError:
        file = []
    for path in file:
        voce = _leggi_json(path) or {}
        for p in voce.get("pacchetti") or []:
            if isinstance(p, dict) and p.get("lei") == lei and p.get("period_end") and p.get("url"):
                trovati.append((p, voce))
    if fino_al:
        visti = {}
        for p, voce in trovati:  # lo stesso pacchetto in piu' voci: vale la data piu' antica
            quando, base = _visto_il(p, voce)
            prima = visti.get(p["url"])
            if prima is None or (quando and (prima[0] is None or quando < prima[0])):
                visti[p["url"]] = (quando, base)
        tenuti = []
        for p, voce in trovati:
            quando, base = visti[p["url"]]
            if entro_il_cutoff(quando, fino_al):
                tenuti.append((p, voce))
            elif esclusi is not None and not any(p["url"] in e for e in esclusi):
                esclusi.append(
                    f"pacchetto dal sito FY{str(p['period_end'])[:4]} ({p['url']}) escluso dal cutoff {str(fino_al)[:10]}: "
                    + (f"visto sul sito il {quando} ({base}), pubblicazione entro il cutoff non dimostrata" if quando
                       else "data di pubblicazione ignota (nessuna data nella cache dell'esplorazione)"))
        trovati = tenuti
    per_periodo = {}
    for p, voce in trovati:
        chiave = (p.get("lingua") == preferita, p.get("lingua") is None, int(p.get("versione") or 0))
        attuale = per_periodo.get(p["period_end"])
        if attuale is None or chiave > attuale[0]:
            per_periodo[p["period_end"]] = (chiave, p, voce)
    righe = []
    for _, (_, p, voce) in sorted(per_periodo.items(), reverse=True):
        riga = {"id": p["url"], "period_end": p["period_end"], "json_url": p["url"], "report_url": p["url"],
                "language": None, "date_added": None, "origine": "sito", "lingua_nome": p.get("lingua")}
        if fino_al:
            riga["visto_il"], riga["visto_base"] = visti[p["url"]]
        righe.append(riga)
    return righe


def mese_esercizio(giorno):
    """Mese di chiusura (1-12) di una data ISO; con le chiusure a 52/53 settimane i primi 7 giorni del mese contano
    per il mese prima («2025-10-03» -> settembre). None se la data non si legge."""
    try:
        d = date.fromisoformat(str(giorno or "")[:10])
    except ValueError:
        return None
    return d.month if d.day > 7 else (d.month - 2) % 12 + 1


GIORNI_SEMESTRE = (150, 215)  # due depositi a ~6 mesi: uno dei due e' una relazione infrannuale


def _a_sei_mesi(a, b):
    giorni = abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
    return GIORNI_SEMESTRE[0] <= giorni <= GIORNI_SEMESTRE[1]


def chiusura_esercizio_emittente(lei=None, pacchetti=None, *, repository_fn=None):
    """{"chiusura": "MM-DD" | None, "base", "periodi"}: chiusura dell'esercizio dell'emittente dai depositi ESEF.

    GENERALITA' UE (Opus 5.5, v3 dopo due review):
    1. depositi ANNUALI verificati in cache (repository filings.xbrl.org = relazioni finanziarie annuali, e
       pacchetti del sito con la chiusura verificata sui fatti di durata annuale): vale il PIU' RECENTE; un mese
       diverso nei precedenti e' un cambio d'esercizio, dichiarato. I pacchetti del sito non verificati non
       decidono e, se cadono in altri mesi, sono dichiarati;
    2. solo pacchetti del sito NON verificati (possono essere semestrali): decidono solo se nessuno sta a ~6 mesi
       da un altro e se cadono tutti nello stesso mese; altrimenti chiusura None, col motivo (mai «il piu'
       recente» per difetto: un semestrale dopo l'annuale invertirebbe annuale e semestrale).
    Senza LEI dell'emittente nessuna chiusura; pacchetti del sito tutti di un altro LEI: dichiarato. Chiusure a
    52/53 settimane: mese_esercizio."""
    lei = str(lei or "").upper() or None
    pk = [p for p in pacchetti or [] if isinstance(p, dict) and p.get("period_end")]
    if not lei:
        return {"chiusura": None, "periodi": [],
                "base": "LEI dell'emittente non noto: la chiusura dei pacchetti ESEF sul sito non e' attribuibile "
                        "all'emittente, esercizio ignoto"}
    avvisi = []
    altri = sorted({str(p.get("lei") or "").upper() for p in pk} - {lei})
    propri = [p for p in pk if str(p.get("lei") or "").upper() == lei]
    if altri and not propri:
        avvisi.append(f"pacchetti ESEF sul sito di un altro LEI ({', '.join(altri)}) e nessuno del LEI {lei}: "
                      "LEI dell'emittente da verificare")
    try:
        annuali = sorted({str(x)[:10] for x in (repository_fn or _periodi_repository)(lei) if mese_esercizio(x)})
    except Exception as exc:  # cache illeggibile: si dichiara, i pacchetti del sito restano
        annuali = []
        avvisi.append(f"depositi annuali in cache illeggibili ({type(exc).__name__})")
    sito = sorted({str(p["period_end"])[:10] for p in propri if mese_esercizio(p["period_end"])})
    tutti = sorted(set(annuali + sito))
    coda = ("; " + "; ".join(avvisi)) if avvisi else ""

    def chiusura_di(mese):
        return f"{mese:02d}-{calendar.monthrange(2001, mese)[1]:02d}"
    if annuali:
        ultimo = annuali[-1]
        m = mese_esercizio(ultimo)
        base = f"chiusura dal deposito annuale ESEF verificato piu' recente ({ultimo})"
        prima = sorted({mese_esercizio(d) for d in annuali[:-1]} - {m})
        if prima:
            base += (f"; cambio d'esercizio: depositi annuali precedenti con chiusura a fine mese "
                     f"{', '.join(f'{x:02d}' for x in prima)}")
        non_verificati = [d for d in sito if d not in annuali and mese_esercizio(d) != m]
        if non_verificati:
            base += (f"; pacchetti del sito non verificati in altri mesi ({', '.join(non_verificati)}): non "
                     "considerati, probabili relazioni infrannuali")
        return {"chiusura": chiusura_di(m), "base": base + coda, "periodi": tutti}
    if not sito:
        return {"chiusura": None, "periodi": [],
                "base": "nessun deposito ESEF con la chiusura dell'esercizio (sito e repository in cache)" + coda}
    vicini = sorted({d for a in sito for b in sito if a < b and _a_sei_mesi(a, b) for d in (a, b)})
    if vicini:
        return {"chiusura": None, "periodi": tutti,
                "base": ("pacchetti ESEF del sito non verificati a ~6 mesi l'uno dall'altro (" + ", ".join(vicini)
                         + "): uno e' una relazione infrannuale, esercizio non determinabile senza un deposito "
                         "annuale verificato") + coda}
    mesi = {mese_esercizio(d) for d in sito}
    if len(mesi) > 1:
        return {"chiusura": None, "periodi": tutti,
                "base": ("pacchetti ESEF del sito non verificati con chiusure in mesi diversi (" + ", ".join(sito)
                         + "): esercizio non determinabile, mai il piu' recente per difetto") + coda}
    return {"chiusura": chiusura_di(mesi.pop()), "periodi": tutti,
            "base": f"chiusura dai pacchetti ESEF del sito ({', '.join(sito)}), non verificati, tutti nello stesso mese"
                    + coda}


def _periodi_repository(lei):
    """Chiusure ANNUALI verificate in cache per `lei` (nessuna rete): depositi del repository filings.xbrl.org
    (relazioni finanziarie annuali) e pacchetti del sito con la chiusura verificata sui fatti di durata annuale."""
    from bellomberg.market_data import esef
    cache = esef._load_cache(lei) or {}
    filings = cache.get("filings") or {}
    out = [f.get("period_end") for f in filings.values() if isinstance(f, dict) and f.get("period_end")]
    out += [v.get("chiusura_verificata") for v in (cache.get("sito") or {}).values()
            if isinstance(v, dict) and v.get("chiusura_verificata")]
    return out


def lei_dal_negozio(ticker):
    """LEI del titolo dal negozio privato dei LEI (file locale, nessuna rete), None se assente o illeggibile."""
    try:
        from bellomberg.storage.negozi_privati import carica_lei
        return (carica_lei().get("lei") or {}).get(str(ticker or "").upper().strip()) or None
    except Exception:
        return None


def chiusura_voce(voce):
    """«MM-DD» della chiusura d'esercizio di una voce di esplorazione (scopri), None se ignota. Le voci scritte
    prima della registrazione si ricalcolano dai loro pacchetti e dal repository in cache (LEI dal negozio)."""
    if not isinstance(voce, dict):
        return None
    registrata = voce.get("chiusura_esercizio")
    if isinstance(registrata, dict) and "chiusura" in registrata:
        return registrata.get("chiusura")
    lei = voce.get("lei") or lei_dal_negozio(voce.get("ticker"))
    return chiusura_esercizio_emittente(lei, voce.get("pacchetti"))["chiusura"]

INDICE_SITO = "esef_sito_indice.json"
# Revisione 04/10 (R8): pacchetti invalidi (zip/XHTML rotti) ricordati per URL, sha256 e data:
# prima si riscaricavano (fino a 80 MB) a ogni giro come se l'errore fosse transitorio.
INVALIDI_SITO = "esef_sito_invalidi.json"
GIORNI_PACCHETTO_INVALIDO = 30


class PacchettoInvalidoNoto(ValueError):
    """Pacchetto gia' trovato invalido entro GIORNI_PACCHETTO_INVALIDO: non riscaricato (dichiarato)."""
_LOCK_INDICE = threading.Lock()  # fino a 4 run in parallelo scrivono lo stesso indice (revisione finale)
_IN_CORSO, _LOCK_IN_CORSO = set(), threading.Lock()


def esplorazione_in_corso(ticker, inizio=True):
    """Una sola esplorazione per titolo alla volta (route su richiesta): False se gia' in corso."""
    with _LOCK_IN_CORSO:
        if inizio:
            if ticker in _IN_CORSO:
                return False
            _IN_CORSO.add(ticker)
        else:
            _IN_CORSO.discard(ticker)
        return True


_FIRMA_REMOTA = ("etag", "last_modified", "content_length")


def _intestazioni_remote(url, hosts):
    """HEAD di controllo (REV_G2a R-5): {"etag", "last_modified", "content_length"} del file
    remoto. Solo https, solo host ammessi, IP pubblico, NESSUN redirect seguito, nel ritmo
    condiviso (1/s). Eccezione se non verificabile: chi chiama tiene il blocco e lo dice."""
    import requests
    from bellomberg.market_data import esef
    from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
    p = urlsplit(str(url or ""))
    if p.scheme != "https" or not p.hostname or p.hostname.lower() not in {h.lower() for h in hosts}:
        raise ValueError(f"HEAD di controllo rifiutata: host non ammesso o non https ({p.hostname})")
    if p.username is not None or p.password is not None:
        raise ValueError("credenziali negli URL non consentite")
    _richiedi_indirizzi_pubblici(p.hostname, p.port or 443)
    esef.attendi_esef()
    r = requests.head(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
    if r.status_code != 200:
        raise ValueError(f"HEAD di controllo: HTTP {r.status_code}")
    h = r.headers or {}
    return {"etag": h.get("ETag"), "last_modified": h.get("Last-Modified"), "content_length": h.get("Content-Length")}


def scarica_pacchetto_json(url, archivio, hosts, *, scarica_fn=None, forza=False):
    """Pacchetto dal sito -> xBRL-JSON nell'archivio: {"stato": "ok", "path", "sha256", ...}.

    Pacchetto max 80 MB (download_sicuro), XHTML max 200 MB, zip controllato (ixbrl_oim).
    Cache per URL e sha256 del pacchetto: lo stesso pacchetto non si riconverte.
    Pacchetto invalido: ricordato GIORNI_PACCHETTO_INVALIDO giorni, salvo `forza` o file
    remoto cambiato (ETag/Last-Modified/Content-Length via HEAD nel ritmo, REV_G2a R-5)."""
    from bellomberg.market_data import download_sicuro, ixbrl_oim
    archivio = Path(archivio)
    indice_path = archivio / INDICE_SITO
    indice = _leggi_json(indice_path) or {}
    voce = indice.get(url)
    if isinstance(voce, dict):
        try:
            if hashlib.sha256(Path(voce["path"]).read_bytes()).hexdigest() == voce["sha256"]:
                return {"stato": "ok", "url": url, "path": voce["path"], "sha256": voce["sha256"],
                        "pacchetto_sha256": voce.get("pacchetto_sha256"), "da_archivio": True}
        except (OSError, KeyError, TypeError):
            pass
    invalidi_path = archivio / INVALIDI_SITO
    noto = (_leggi_json(invalidi_path) or {}).get(url)
    if isinstance(noto, dict) and not forza:
        try:
            eta_giorni = (time.time() - float(noto["quando"])) / 86400
        except (KeyError, TypeError, ValueError):
            eta_giorni = None  # voce illeggibile: si riprova
        if eta_giorni is not None and 0 <= eta_giorni < GIORNI_PACCHETTO_INVALIDO:
            firma = {k: noto.get(k) for k in _FIRMA_REMOTA}
            cambiato, verifica = False, "file remoto senza ETag/Last-Modified: nessuna verifica possibile"
            if any(firma.values()):
                try:
                    remota = _intestazioni_remote(url, hosts)
                    cambiato = any(firma[k] and remota.get(k) != firma[k] for k in _FIRMA_REMOTA)
                    verifica = "file remoto invariato (ETag/Last-Modified)"
                except Exception as exc:
                    verifica = f"verifica del file remoto non riuscita ({type(exc).__name__}: {str(exc)[:120]})"
            if not cambiato:
                raise PacchettoInvalidoNoto(
                    f"pacchetto gia' trovato invalido il {date.fromtimestamp(float(noto['quando'])).isoformat()} "
                    f"(sha256 {str(noto.get('pacchetto_sha256'))[:12]}: {noto.get('motivo')}); {verifica}; "
                    f"non riscaricato fino a {GIORNI_PACCHETTO_INVALIDO} giorni dopo")
    pacchetto = (scarica_fn or download_sicuro.scarica_limitato)(
        url, archivio / "pacchetti", download_sicuro.MAX_PACCHETTO_ESEF, hosts)
    if pacchetto.get("stato") != "ok":
        raise ValueError(pacchetto.get("motivo") or "pacchetto non scaricato")
    try:
        dati = ixbrl_oim.converti_pacchetto(pacchetto["path"], max_xhtml_bytes=download_sicuro.MAX_XHTML_ESEF)
    except ixbrl_oim.PacchettoNonValido as exc:
        with _LOCK_INDICE:
            invalidi = _leggi_json(invalidi_path) or {}
            invalidi[url] = {"pacchetto_sha256": pacchetto.get("sha256"), "motivo": str(exc)[:300],
                             "quando": time.time(), **{k: pacchetto.get(k) for k in _FIRMA_REMOTA}}
            try:
                _scrivi_json(invalidi_path, invalidi)
            except OSError as errore_memoria:  # REV_G2a R-5: dichiarato, mai zitto
                raise ixbrl_oim.PacchettoNonValido(
                    f"{exc}; memoria del pacchetto invalido NON scritta ({type(errore_memoria).__name__}: "
                    f"{errore_memoria}): sara' riscaricato al prossimo giro") from exc
        raise
    finally:
        try:
            os.unlink(pacchetto["path"])  # serve solo il JSON convertito (lo sha del pacchetto resta)
        except OSError:
            pass
    grezzo = json.dumps(dati, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(grezzo).hexdigest()
    nome = re.sub(r"[^A-Za-z0-9._-]", "_", unquote(urlsplit(url).path.rsplit("/", 1)[-1]))[:100]
    dest = archivio / f"{os.path.splitext(nome)[0]}-{digest}.json"
    archivio.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        fd, tmp = tempfile.mkstemp(dir=archivio, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(grezzo)
        os.replace(tmp, dest)
    with _LOCK_INDICE:  # si rilegge sotto lock: le voci scritte da altri run restano
        indice = _leggi_json(indice_path) or {}
        indice[url] = {"path": str(dest), "sha256": digest, "pacchetto_sha256": pacchetto["sha256"]}
        try:
            _scrivi_json(indice_path, indice)
        except OSError:
            pass  # cache best-effort
    return {"stato": "ok", "url": url, "path": str(dest), "sha256": digest,
            "pacchetto_sha256": pacchetto["sha256"], "conversione": dati.get("conversione")}


def atteso_piu_recente(ultimo, oggi):
    """True se dopo l'esercizio `ultimo` (data di chiusura) dovrebbe gia' esserci il successivo:
    le relazioni annuali escono entro ~4 mesi dalla chiusura (130 giorni di margine)."""
    if not ultimo:
        return True
    try:
        chiusura = date.fromisoformat(str(ultimo)[:10])
    except ValueError:
        return True
    return oggi > chiusura + timedelta(days=365 + 130)


GLEIF = "https://api.gleif.org/api/v1/lei-records"
GLEIF_PAGINA = 20


GLEIF_MAX_BYTES = 2 * 1024 * 1024  # una pagina di record LEI: poche decine di KB
_GLEIF_INTERVALLO_S = 1.0
_GLEIF_LOCK = threading.Lock()
_GLEIF_ULTIMA = [0.0]


def attendi_gleif(*, percorso=None):
    """Ritmo condiviso tra processi per api.gleif.org (1 richiesta/s), stesso lock di SEC/ESEF.
    Il file sta accanto a quello ESEF (i test lo reindirizzano insieme)."""
    from bellomberg.market_data import esef
    from bellomberg.market_data.sec_edgar import _attendi
    _attendi(Path(percorso) if percorso else esef._ritmo_path().with_name(".gleif_ritmo"),
             _GLEIF_INTERVALLO_S, _GLEIF_LOCK, _GLEIF_ULTIMA)


def _gleif_scarica(nome):
    """GET GLEIF nel ritmo, UA di esef._headers (contatto SEC_CONTACT_EMAIL se c'e', altrimenti UA generico del progetto) e tetto di byte
    letto a flusso (REV_G2a R-3/R14: prima nessun ritmo, UA senza contatto, corpo senza tetto)."""
    import requests
    from bellomberg.market_data import esef
    ua = esef._headers()["User-Agent"]  # mai ContattoMancante: senza contatto UA generico (decisione PM 05/10)
    attendi_gleif()
    r = requests.get(GLEIF, params={"filter[fulltext]": nome, "page[size]": GLEIF_PAGINA},
                     headers={"Accept": "application/vnd.api+json", "User-Agent": ua},
                     timeout=20, stream=True)
    try:
        r.raise_for_status()
        pezzi, letti = [], 0
        for pezzo in r.iter_content(64 * 1024):
            letti += len(pezzo)
            if letti > GLEIF_MAX_BYTES:
                raise ValueError(f"risposta GLEIF oltre {GLEIF_MAX_BYTES // (1024 * 1024)} MB: scartata")
            pezzi.append(pezzo)
    finally:
        r.close()
    dati = json.loads(b"".join(pezzi).decode("utf-8")).get("data")
    return dati if isinstance(dati, list) else []


def gleif_lei_records(nome):
    """Ricerca gratuita per nome sull'API GLEIF (nessuna chiave): record LEI grezzi."""
    return _gleif_scarica(nome)


def consigliere_in_corso():
    """True se una run del Consigliere e' in corso (processo consigliere_multi): l'esplorazione
    dei siti aspetta il giro successivo. Senza psutil: False (il backend lo dichiara nel log)."""
    try:
        import psutil
    except ImportError:
        return False
    from bellomberg.core.processi_run import e_run_consigliere
    for p in psutil.process_iter(["cmdline"]):
        try:
            # revisione R-8 C6: la FORMA dell'argv, non la parola (una shell che la cita non e' una run)
            if e_run_consigliere(p.info.get("cmdline") or []):
                return True
        except Exception:
            continue
    return False


_RINVIO = "run del Consigliere in corso: siti esplorati al prossimo giro"


def _fermati(consigliere_fn, scadenza, orologio=time.monotonic):
    """Motivo per smettere di esplorare prima del prossimo titolo, None per continuare: la run del
    Consigliere si ricontrolla a ogni titolo (revisione finale) e il giro ha un tetto di tempo."""
    if (consigliere_fn or consigliere_in_corso)():
        return _RINVIO
    if scadenza is not None and orologio() > scadenza:
        return f"tempo massimo del giro ({TEMPO_GIRO_S} s) superato: altri siti al prossimo giro"
    return None


def scopri_profili(store, *, oggi=None, consigliere_fn=None, indice_fn=None, scopri_fn=None, escludi=(), scadenza=None):
    """Controllo giornaliero: per i profili ESEF a blocchi col repository fermo o senza
    depositi, esplora il sito dell'emittente (cache CACHE_GIORNI: al piu' un giro a settimana).
    Mai durante una run del Consigliere. [{"ticker", "pacchetti", "motivi"}]."""
    from bellomberg.market_data import esef
    oggi = oggi or date.today()
    esiti = []
    for riga in store.list_profiles():
        profilo = riga.get("profile") or {}
        ticker = riga.get("ticker")
        if (not riga.get("enabled") or ticker in escludi or profilo.get("esef_modo") != "blocchi"
                or not profilo.get("lei")):
            continue
        try:
            indice = (indice_fn or esef.indice_depositi)(profilo["lei"])
            # ultimo esercizio CON xBRL-JSON: un deposito senza JSON non e' confrontabile (revisione finale)
            ultimo = max((r["period_end"] for r in indice.get("righe") or []
                          if isinstance(r, dict) and r.get("period_end") and r.get("json_url")), default=None)
            if not atteso_piu_recente(ultimo, oggi):
                continue
            motivo = _fermati(consigliere_fn, scadenza)
            if motivo:
                esiti.append({"ticker": None, "rinviata": True, "motivi": [motivo]})
                break
            trovato = (scopri_fn or scopri)(ticker, lei=profilo["lei"], oggi=oggi)
            esiti.append({"ticker": ticker, "pacchetti": sum(1 for p in trovato.get("pacchetti") or []
                                                            if p.get("lei") == str(profilo["lei"]).upper()),
                          "motivi": (trovato.get("motivi") or [])[:5]})
        except Exception as exc:  # un sito non ferma gli altri
            esiti.append({"ticker": ticker, "pacchetti": 0, "motivi": [motivo_eccezione(exc)]})
    return esiti


# ------------------------------------------------------------------ PDF IR (Task 5)
# Decisione PM 05/10 (opzione B): per QUALUNQUE emittente senza documenti dall'archivio ufficiale
# (OAM/ESEF/SEC) valgono le relazioni periodiche in PDF pubblicate sul suo sito, sempre con
# l'origine dichiarata: mai presentate come deposito ufficiale. Nessun nome di societa' qui.
ORIGINE_SITO = "sito_emittente"
ETICHETTA_SITO = "sito dell'emittente, non archivio ufficiale (OAM)"
ETICHETTA_SITO_EN = "issuer website, not the official archive (OAM)"
MAX_PRIME_PAGINE = 3                     # PDF col nome generico o senza periodo: prima pagina letta, per esplorazione
MAX_PDF_PRIMA_PAGINA = 40 * 1024 * 1024  # come download_sicuro.MAX_PDF
MAX_SCARTATI = 25                        # scarti riportati col motivo (il totale resta contato)
MAX_DOCUMENTI = 40                       # documenti ammessi riportati con l'etichetta (il totale resta contato)
# Decisione PM 06/10 sera («piu' aperti: troppi blocchi bloccano solo noi»): si AMMETTE con un'etichetta
# onesta invece di scartare. Restano fuori SOLO: robots.txt che vieta e HTTP 401/403/429 (dichiarati, mai
# aggirati), i documenti di un'ALTRA entita', i PDF illeggibili, i non finanziari evidenti (ESG,
# governance: dichiarati). PDF su un altro host (CDN della pagina IR) ammessi «via <host>».
ETICHETTA_SITO_VIA = "sito dell'emittente via {host}, non archivio ufficiale (OAM)"
ETICHETTA_SITO_VIA_EN = "issuer website via {host}, not the official archive (OAM)"
ETICHETTA_SITO_IR = "sito IR dell'emittente {host} (scoperto dal sito {home}), non archivio ufficiale (OAM)"
# Revisione R-FONTI S2: un sito di gruppo trovato dal link «Group» non e' il sito IR dell'emittente finche' il
# documento non lo prova (soggetto in copertina, all'attivazione)
ETICHETTA_SITO_GRUPPO = ("sito di gruppo {host} linkato dal sito {home} (soggetto da verificare sul documento), "
                         "non archivio ufficiale (OAM)")
ETICHETTA_SITO_GRUPPO_EN = ("group website {host} linked from {home} (issuer to be verified on the document), "
                            "not the official archive (OAM)")
# Revisione R-FONTI S6: un PDF su un host che non e' dell'emittente entra solo se l'host e' una PIATTAFORMA IR
# riconosciuta (lista dichiarata: si confronta il primo nome del dominio registrabile), mai un dominio qualsiasi
PIATTAFORME_DOCUMENTI = ("q4cdn", "q4web", "q4inc", "mziq", "gcs-web", "notified", "euroland", "eqs")
# CDN GENERICI (main 07/10, «piu' aperti»): ospitano file di chiunque, quindi un PDF li' entra SOLO se il link
# sta su una pagina del sito dell'emittente; all'attivazione deve poi passare la copertina (soggetto) e non
# essere una nota di broker. Lista dichiarata (suffissi dell'host).
CDN_GENERICI = ("cloudfront.net", "akamaized.net", "akamaihd.net", "azureedge.net", "amazonaws.com",
                "storage.googleapis.com", "blob.core.windows.net", "fastly.net", "b-cdn.net", "cdn77.org",
                "sitecorecontenthub.cloud")  # DAM condiviso (prova dal vivo 06/10: PDF di un emittente via redirect)
ETICHETTA_CDN = "PDF su CDN esterno ({host}) linkato dal sito dell'emittente, non archivio ufficiale (OAM)"
ETICHETTA_CDN_EN = "PDF on an external CDN ({host}) linked from the issuer website, not the official archive (OAM)"
# Note di broker e di analisti: non sono documenti dell'emittente (nome, testo del link, indirizzo)
_RICERCA = re.compile(r"equity[\s_-]*research|research[\s_-]*(?:note|report|update)|initiat\w*[\s_-]*coverage"
                      r"|price[\s_-]*target|target[\s_-]*price|analyst[\s_-]*coverage|\bbroker|/research/"
                      # revisione R-SITI2 D2: lessico delle note IT/DE/FR/ES
                      r"|prezzo[\s_-]*obiettivo|ricerca[\s_-]*azionaria|kursziel|kaufempfehlung|anlageempfehlung"
                      r"|objectif[\s_-]*de[\s_-]*cours|precio[\s_-]*objetivo|/ricerca/|/analysten/", re.I)
ETICHETTA_SITO_IR_EN = "issuer IR website {host} (found from {home}), not the official archive (OAM)"
TIPI_DOCUMENTO = ("relazione", "comunicato_risultati", "estratto", "presentazione")
_RANGO_DOCUMENTO = {"relazione": 3, "comunicato_risultati": 2, "estratto": 1, "presentazione": 0}  # a parita' di periodo
_NOME_TIPO_DOCUMENTO = {"relazione": "relazione", "comunicato_risultati": "comunicato dei risultati",
                        "estratto": "estratto finanziario", "presentazione": "presentazione dei risultati"}
# Estratti finanziari (seguito main 06/10): cifre chiave, sintesi, schede dati. Ammessi come «estratto»,
# mai come relazione; l'ultimo del profilo li usa solo se non c'e' altro
_ESTRATTO = re.compile(r"key[\s_-]*figures|kennzahlen|summary|sintesi|fact[\s_-]*sheet|glance|data[\s_-]*(?:book|pack)"
                       r"|financial[\s_-]*(?:highlights|supplement|data)|supplement|\bkpis?\b", re.I)
# Presentazioni, slide, conference call per investitori: ammesse se legate a un periodo, etichettate
_PRESENTAZIONE = re.compile(
    r"presentation|pr[aä]e?sentation|pr[ée]sentation|presentazione|presentaci[oó]n|slides?\b|\bdeck\b|webcast"
    r"|conference[\s_-]*call|telefonkonferenz|\bcall\b|recap|trans\w{0,3}ipt|roadshow|capital[\s_-]*markets?[\s_-]*day"
    r"|\bcmd\b|analyst|investor[\s_-]*day|bilanzpressekonferenz"
    r"|prepared[\s_-]*remarks", re.I)  # testo letto nella call dei risultati (prova dal vivo 06/10)
# Comunicati (stampa, ad hoc, earnings release): ammessi se riguardano i risultati di un periodo
_COMUNICATO = re.compile(r"press|comunicato|\bnews\b|newsletter|ad[\s_-]*hoc|earnings|release", re.I)
# Non finanziari evidenti: fuori dal profilo finanziario, dichiarati
_NON_FINANZIARIO = re.compile(
    r"governance|remunerat|compensation|sustainab|esg\b|csr\b|non[\s_-]*financial|climate|sdgs?\b|slavery"
    r"|code[\s_-]*of|codice|policy|procedura|bylaws|statuto|proxy|agenda|minutes|verbale|convocazione|einladung"
    r"|nutzungsbedingungen|terms[\s_-]*of[\s_-]*use|stimmrecht|voting[\s_-]*rights"
    r"|informativa(?![\s_-]*(?:finanziaria|trimestrale|periodica|semestrale|annuale))|directions|ivass"
    r"|labou?r[\s_-]*report|green[\s_-]*bond|pill?ar|tax\b", re.I)  # «pilar3» (prova dal vivo 06/10)
# Parole da risultati: un nome generico senza nessuna di queste non e' un comunicato dei risultati
_RISULTATI = re.compile(r"results?|earnings|risultati|ergebnis|resultados?|r[ée]sultats|quarterly|trading[\s_-]*update",
                        re.I)
# Documento di un'ALTRA entita' (fondo pensione del gruppo): mai ammesso
_ALTRA_ENTITA_PDF = re.compile(r"pension", re.I)
# Finanziari ma non la relazione periodica (estratti, avvisi, prospetti): fuori, col motivo
_ESCLUDI_PDF = re.compile(
    r"notice|avviso|dividend|prospectus|prospetto|\brating\b|letter|lettera"
    r"|piano|business[\s_-]*plan|scissione|buy[\s_-]*back|acquisto[\s_-]*azioni|information[\s_-]*form"
    r"|\baif\b|circular|errata|corrigendum|auditor|pr[uü]fungsvermerk", re.I)
# Cartelle della sezione stampa: i PDF li' sono comunicati SULLA relazione, non la relazione
# (prova reale 05/10: «/Presse/News/…-Half-Yearly-Financial-Report-H1.pdf»). Non «media»:
# molti CMS tengono tutti i file sotto «/-/media/».
_PERCORSO_STAMPA = re.compile(
    r"/(?:presse?|press[\s_-]*releases?|pressemitteilungen|news(?:room)?|comunicati(?:[\s_-]*stampa)?|stampa"
    r"|sala[\s_-]*stampa|notizie|actualit[eé]s|noticias|communiqu[eé]s)(?=/)", re.I)
_TIPI_PDF = (
    ("trimestrale", re.compile(r"md\W?&?\W?a\b|mda\b|management.?s[\s_-]*discussion|quarter|trimestr|quartal"
                               r"|resoconto[\s_-]*intermedio|zwischenmitteilung"
                               r"|\bq[1-4]\b|[\s_-]q[1-4][\s_-]", re.I)),
    ("semestrale", re.compile(r"half[\s_-]*year|semestr|halbjahr|first[\s_-]*half|\bh1\b|\b1h\b|interim"
                              r"|semi[\s_-]*annual|halfjaar", re.I)),  # «1H_2026» (BASSI v2)
    ("annuale", re.compile(r"annual|annuale|annuel|bilancio|gesch(?:a|ä|ae)ftsbericht|rapport[\s_-]*annuel|jahresfinanz"
                           r"|registration[\s_-]*document|document[\s_-]*d.enregistrement|integrated[\s_-]*report"
                           r"|relazione[\s_-]*finanziaria|informe[\s_-]*anual|cuentas[\s_-]*anuales|jaarverslag"
                           r"|konzernabschluss|[aå]rsredovisning|vuosikertomus", re.I)),
)
# R-FONTI 10/10 (Opus 5.5): «Relazione finanziaria» senza aggettivo e' il nome italiano sia dell'annuale sia
# della semestrale (prova reale: «ING_Relazione_Finanziaria_30_giugno_2026.pdf», copertina «HALF-YEAR
# FINANCIAL REPORT AT 30 JUNE 2026», classificata annuale e scartata in verifica). Senza un'altra parola da
# annuale il tipo lo decidono il titolo della prima pagina e la data di chiusura nel nome; se non bastano o
# si contraddicono il tipo e' «da confermare» (dichiarato), mai annuale per difetto.
_RELAZIONE_GENERICA = re.compile(r"relazione[\s_-]*finanziaria", re.I)
# BASSI v2: «bilancio» da solo non e' una parola da annuale («Bilancio consolidato semestrale abbreviato»): prima
# «Relazione_Finanziaria_Bilancio_30_giugno_2026.pdf» usciva ANNUALE al 30/06; ora decide la chiusura nel nome.
_ANNUALE_ESPLICITO = re.compile(
    r"annual|annuale|annuel|bilancio[\s_-]*(?:d.esercizio|annuale)|gesch(?:a|ä|ae)ftsbericht|rapport[\s_-]*annuel"
    r"|jahresfinanz"
    r"|registration[\s_-]*document|document[\s_-]*d.enregistrement|integrated[\s_-]*report"
    r"|informe[\s_-]*anual|cuentas[\s_-]*anuales|jaarverslag"
    r"|konzernabschluss|[aå]rsredovisning|vuosikertomus", re.I)
# GENERALITA' UE (Opus 5.5): titoli delle relazioni anche in tedesco, francese, spagnolo e olandese (prima solo
# EN/IT: «Geschäftsbericht 2025 … Vergütungsbericht 120» finiva da confermare perche' il titolo finanziario non
# veniva riconosciuto). Titoli di DOCUMENTO, non parole sciolte: «Annual Remuneration Report» non e' annuale.
_TITOLO_TIPO = (
    ("semestrale", re.compile(r"half[\s_-]*year(?:ly)?[\s_-]+(?:condensed[\s_-]+)?(?:consolidated[\s_-]+)?"
                              r"(?:financial[\s_-]+)?(?:report|statements)|relazione[\s_-]+finanziaria[\s_-]+semestrale"
                              r"|semi[\s_-]*annual[\s_-]+(?:financial[\s_-]+)?report|bilancio[\s_-]+semestrale"
                              r"|halbjahres(?:finanz)?bericht|rapport[\s_-]+financier[\s_-]+semestriel"
                              r"|informe[\s_-]+financiero[\s_-]+semestral|halfjaar(?:bericht|verslag)", re.I)),
    ("annuale", re.compile(r"annual[\s_-]+(?:financial[\s_-]+)?report|relazione[\s_-]+finanziaria[\s_-]+annuale"
                           r"|bilancio[\s_-]+(?:consolidato[\s_-]+)?(?:d['’]esercizio|annuale)"
                           r"|gesch(?:ä|ae|a)ftsbericht|jahresfinanzbericht|konzernabschluss"
                           r"|document[\s_-]+d['’]enregistrement[\s_-]+universel|rapport[\s_-]+financier[\s_-]+annuel"
                           r"|informe[\s_-]+anual(?![\s_-]+(?:sobre|de[\s_-]+gobierno|de[\s_-]+remuneraciones))"
                           r"|cuentas[\s_-]+anuales|jaarverslag", re.I)),
    ("trimestrale", re.compile(r"quarterly[\s_-]+(?:financial[\s_-]+)?report|resoconto[\s_-]+intermedio"
                               r"|relazione[\s_-]+finanziaria[\s_-]+trimestrale", re.I)),
)
# BASSI v2: un nome da annuale («Annual_Report_2025.pdf») con la prima pagina di un'altra relazione (remunerazione,
# governo societario, sostenibilita') non e' la relazione finanziaria: tipo da confermare, non ammesso. Conta solo
# se il titolo NON finanziario viene prima di qualunque titolo da relazione finanziaria nella prima pagina.
_TITOLO_NON_FINANZIARIO = re.compile(
    r"remuneration[\s_-]+(?:report|policy)|report[\s_-]+on[\s_-]+remuneration|compensation[\s_-]+report"
    r"|relazione[\s_-]+(?:sulla[\s_-]+)?(?:politica[\s_-]+(?:in[\s_-]+materia[\s_-]+)?di[\s_-]+)?remunerazione"
    r"|relazione[\s_-]+sul(?:la)?[\s_-]+(?:governo[\s_-]+societario|politica[\s_-]+(?:in[\s_-]+materia[\s_-]+)?di"
    r"[\s_-]+remunerazione)|corporate[\s_-]+governance[\s_-]+report|sustainability[\s_-]+report"
    r"|verg(?:ü|ue)tungsbericht|rapport[\s_-]+sur[\s_-]+la[\s_-]+r(?:é|e)mun(?:é|e)ration"
    r"|informe[\s_-]+(?:anual[\s_-]+)?(?:sobre[\s_-]+)?(?:las[\s_-]+)?remuneraciones"
    r"|informe[\s_-]+anual[\s_-]+de[\s_-]+gobierno[\s_-]+corporativo|nachhaltigkeitsbericht"
    r"|rapport[\s_-]+sur[\s_-]+le[\s_-]+gouvernement[\s_-]+d['’]entreprise|remuneratie[\s_-]*(?:rapport|verslag)"
    r"|duurzaamheidsverslag", re.I)
# GENERALITA' UE v3 (Opus 5.5): un titolo da relazione seguito da un tema non finanziario e' non finanziario solo se
# il tema e' il SOGGETTO del titolo («Rapport financier annuel – rémunération des dirigeants», «Geschäftsbericht
# Nachhaltigkeit 2025»), non un sottotitolo («Geschäftsbericht – Nachhaltigkeit als Strategie», «Relazione
# finanziaria annuale – sostenibilità integrata») ne' un titolo che nomina anche i conti («… and Financial
# Statements», «… et états financiers»)
_TEMA_NON_FINANZIARIO = re.compile(
    r"(?:annual[\s_-]+report|rapport[\s_-]+financier[\s_-]+annuel|gesch(?:ä|ae|a)ftsbericht|jahresfinanzbericht"
    r"|relazione[\s_-]+finanziaria[\s_-]+annuale|informe[\s_-]+anual|jaarverslag)[\s_:–—-]+"
    r"(?:r(?:é|e)mun(?:é|e)ration|verg(?:ü|ue)tung|nachhaltigkeit|sustainability|gouvernance|governance"
    r"|remuneraciones|remunerazione|sostenibilit|duurzaamheid|bezoldiging)\w*", re.I)
_TEMA_SOGGETTO = re.compile(r"\s*(?:$|[,;:|(]|(?:19|20)\d\d|(?:des|de|du|der|of|del|della|dei|degli|van|von)\b)", re.I)
_CONTI_NEL_TITOLO = re.compile(r"financial[\s_-]+statements|(?:é|e)tats[\s_-]+financiers|konzernabschluss"
                               r"|jahresabschluss|bilancio|cuentas|jaarrekening|accounts|comptes", re.I)
# ...e la prima pagina di un comunicato che CITA la relazione («Pressemitteilung: … veröffentlicht
# Jahresfinanzbericht 2025») non e' la relazione: conta solo per i PDF col nome da relazione (i comunicati dei
# risultati col nome da comunicato restano ammessi come tali)
_COMUNICATO_PAGINA = re.compile(
    r"press[\s_-]+release(?!s)|pressemitteilung|presseinformation(?!en)|ad[\s_-]*hoc[\s_-]*mitteilung"
    r"|communiqu[ée][\s_-]+de[\s_-]+presse|comunicato[\s_-]+stampa|nota[\s_-]+de[\s_-]+prensa|persbericht", re.I)
# Riga d'indice (v2 dopo la review): il titolo seguito, entro poche parole (o un anno) o dopo puntini di guida, da un
# numero di pagina di 1-3 cifre. Non lo e' un giorno seguito dal nome di un mese («31 December 2025», «31.
# Dezember», «31 décembre») ne' un rimando normativo («art. 123-ter», «§ 162», «Article 89»). Senza puntini di
# guida serve una prova d'indice: un'intestazione («Contents», «Inhalt», «Sommaire»…) prima del titolo oppure almeno
# due coppie titolo-numero nella pagina. Nessuna soglia di posizione: un titolo non finanziario conta ovunque.
_NUMERO_PAGINA = r"(?P<num>\d{1,3})(?![\w]|\s*[-–/%°]|[.,]\d)"
_RIGA_INDICE = re.compile(r"(?P<tra>(?:\s+(?:[^\W\d_]+(?:['’][^\W\d_]+)?|(?:19|20)\d\d)){0,6}?)(?P<guida>(?:\s*[.·…]){2,}\s*|\s+)"
                          + _NUMERO_PAGINA)
_COPPIA_INDICE = re.compile(r"[^\W\d_]{3,}(?:(?:\s*[.·…]){2,}\s*|\s+)" + _NUMERO_PAGINA)
_INTESTAZIONE_INDICE = re.compile(r"\b(?:contents|inhalt(?:sverzeichnis)?|sommaire|sommario|indice|índice|inhoud"
                                  r"|inhoudsopgave)\b", re.I)
_RIMANDO_NORMATIVO = re.compile(r"(?:\b(?:art|artt|n|nr|no|comma|section|par|article|articles|artikel|art[ií]culo"
                                r"|articolo|articoli)\.?|§)\s*$", re.I)
_CONGIUNZIONE = re.compile(r"\s*(?:and|und|et|e|y|en|&|\+)\s+", re.I)


def _numero_di_pagina(testa, prima, numero):
    """Il numero (gruppo «num» del match) e' un numero di pagina: non un giorno seguito dal mese, non un rimando."""
    dopo = _senza_accenti(testa[numero.end("num"):numero.end("num") + 16])
    if re.match(r"\s*\.?\s*(?:" + _ALTERNATIVA_MESI + r")\b", dopo):
        return False
    return not _RIMANDO_NORMATIVO.search(testa[prima:numero.start("num")])


def _riga_indice(testa, inizio, fine):
    """True se il titolo [inizio, fine) e' una riga d'indice (vedi _RIGA_INDICE).

    v3: salvo i puntini di guida, un titolo a inizio pagina o prima dell'intestazione d'indice e' il titolo del
    documento, mai una riga d'indice («Remuneration Report 2025 Contents Introduction 2 …»); un anno fra titolo e numero vale solo coi
    puntini di guida («Inhalt Vergütungsbericht 2025 1 Einleitung 3» non e' un indice)."""
    m = _RIGA_INDICE.match(testa, fine)
    if not m or not _numero_di_pagina(testa, fine, m):
        return False
    if "." in m.group("guida") or "·" in m.group("guida") or "…" in m.group("guida"):
        return True  # puntini di guida: indice in ogni posizione
    if not testa[:inizio].strip():
        return False
    intestazione_prima = _INTESTAZIONE_INDICE.search(testa[:inizio])
    if not intestazione_prima and _INTESTAZIONE_INDICE.search(testa, fine):
        return False
    if re.search(r"\d", m.group("tra")):
        return False
    if intestazione_prima:
        return True
    coppie = sum(1 for c in _COPPIA_INDICE.finditer(testa) if _numero_di_pagina(testa, c.start(), c))
    return coppie >= 2


def _prima_pagina_non_finanziaria(testa, comunicato=False):
    """Il titolo non finanziario della prima pagina se viene PRIMA di ogni titolo da relazione, altrimenti None.

    Non contano i titoli non finanziari su una riga d'indice ne' quelli congiunti a un titolo da relazione
    («Sustainability Report and Annual Report»); un titolo da relazione dentro un titolo non finanziario
    («Rapport financier annuel – rémunération») non conta. `comunicato`: anche l'intestazione di un comunicato
    stampa vale come titolo non finanziario (PDF col nome da relazione)."""
    testa = testa or ""
    non_fin = list(_TITOLO_NON_FINANZIARIO.finditer(testa)) + [
        t for t in _TEMA_NON_FINANZIARIO.finditer(testa)
        if _TEMA_SOGGETTO.match(testa, t.end()) and not _CONTI_NEL_TITOLO.search(testa, t.end(), t.end() + 120)]

    def dentro(x):
        return any(n.start() <= x.start() < n.end() for n in non_fin)
    finanziari = sorted((x.start(), x) for _, rx in _TITOLO_TIPO for x in rx.finditer(testa) if not dentro(x))

    def congiunto(m):
        c = _CONGIUNZIONE.match(testa, m.end())
        return bool(c) and any(x.start() == c.end() for _, x in finanziari)
    validi = [m for m in non_fin if not _riga_indice(testa, m.start(), m.end()) and not congiunto(m)]
    if comunicato:
        # v3: solo come PRIMA intestazione della pagina (nelle prime battute, non una voce di menu «… | Press
        # releases | …») e se il titolo da relazione non apre una frase a se' dopo il comunicato
        c = _COMUNICATO_PAGINA.search(testa, 0, 30)
        primo_fin = finanziari[0][0] if finanziari else len(testa)
        if (c and "|" not in testa[max(0, c.start() - 3):c.end() + 3]
                and not re.search(r"[^\W\d]\.\s+[A-ZÀ-Ý]", testa[c.end():primo_fin + 1])):
            validi.append(c)
    if not validi:
        return None
    m = min(validi, key=lambda x: x.start())
    primo = finanziari[0][0] if finanziari else None
    return m.group(0) if primo is None or m.start() < primo else None


def _tipo_dal_titolo(testa):
    """Tipo dal titolo della relazione nella prima pagina (il primo per posizione), None se non c'e'."""
    trovati = [(m.start(), tipo) for tipo, rx in _TITOLO_TIPO for m in [rx.search(testa or "")] if m]
    return min(trovati)[1] if trovati else None


def mese_chiusura(chiusura):
    """Mese (1-12) della chiusura d'esercizio «MM-DD» o «AAAA-MM-GG»; None se assente o illeggibile."""
    try:
        mese = int(str(chiusura or "")[-5:-3])
    except ValueError:
        return None
    return mese if 1 <= mese <= 12 else None


def _tipo_dalla_chiusura(periodo, chiusura):
    """(tipo, frase) di una data di chiusura nel nome rispetto all'esercizio dell'emittente: annuale se cade nel mese
    di chiusura, semestrale se sei mesi prima/dopo, (None, motivo) se incoerente o con l'esercizio ignoto."""
    gg_mm = f"{periodo[8:10]}/{periodo[5:7]}"
    fy = mese_chiusura(chiusura)
    if fy is None:
        return None, (f"chiusura {gg_mm} nel nome ma esercizio dell'emittente ignoto (nessun deposito ESEF con la "
                      "chiusura): la sola data non decide fra annuale e semestrale")
    mese = mese_esercizio(periodo)  # 52/53 settimane: «2025-10-03» cade a settembre
    if mese == fy:
        return "annuale", f"chiusura {gg_mm} nel nome = chiusura dell'esercizio dell'emittente ({chiusura})"
    if mese == (fy + 5) % 12 + 1:
        return "semestrale", (f"chiusura {gg_mm} nel nome = fine del primo semestre dell'esercizio dell'emittente "
                              f"(chiusura {chiusura})")
    return None, (f"chiusura {gg_mm} nel nome incoerente con l'esercizio dell'emittente (chiusura {chiusura}): ne' "
                  "fine esercizio ne' fine semestre")


def _tipo_relazione_generica(periodo, base, testa, chiusura=None):
    """(tipo, base) di una «relazione finanziaria» senza aggettivo, (None, motivo) se resta da confermare.

    GENERALITA' UE (Opus 5.5): prima la data nel nome decideva da sola (30/06 -> semestrale, 31/12 -> annuale) e
    un emittente con l'esercizio al 30/06 vedeva l'annuale come semestrale. Ora: il titolo esplicito della prima
    pagina vince; la data nel nome decide solo se coerente con la chiusura dell'esercizio dell'emittente
    (`chiusura`, «MM-DD» dai depositi ESEF); con l'esercizio ignoto la sola data lascia il tipo da confermare."""
    da_data, perche_data = (None, None)
    if periodo and base == "data di chiusura nel nome del file":
        da_data, perche_data = _tipo_dalla_chiusura(str(periodo), chiusura)
    da_titolo = _tipo_dal_titolo(testa) if testa else None
    if da_titolo:
        nota = ""
        if da_data and da_data != da_titolo:
            nota = f" (prevale sulla data nel nome: {perche_data}, da relazione {da_data})"
        elif da_data:
            nota = " e dalla chiusura nel nome del file"
        return da_titolo, f"tipo {da_titolo} dal titolo della prima pagina{nota}"
    if da_data:
        return da_data, f"tipo {da_data} da una «relazione finanziaria» senza aggettivo: {perche_data}"
    if perche_data:
        return None, (f"tipo da confermare: «relazione finanziaria» senza aggettivo, {perche_data}"
                      + ("; prima pagina senza titolo da relazione" if testa is not None else ""))
    return None, ("tipo da confermare: «relazione finanziaria» senza aggettivo, senza data di chiusura nel nome"
                  + (" e senza titolo da relazione nella prima pagina" if testa is not None else ""))


# Parola da documento: senza, il nome e' generico («First Half 2026 results») e serve la prima pagina
_NOME_DOCUMENTO = re.compile(
    r"report|bericht|mitteilung|statement|relazione|resoconto|rapport|informe|bilancio|md\W?&?\W?a\b|mda\b"
    r"|management.?s[\s_-]*discussion|registration[\s_-]*document|document[\s_-]*d.enregistrement|jaarverslag"
    r"|konzernabschluss|accounts|comptes|cuentas|[aå]rsredovisning|vuosikertomus|financial[\s_-]*statements"
    r"|informativa[\s_-]*finanziaria", re.I)
# (non «abschluss» da solo: il «Jahresabschluss» della sola capogruppo non e' la relazione consolidata;
# prova reale 05/10: tre PDF scaricati per la prima pagina senza motivo)
_INGLESE = re.compile(r"(?:^|[\W_])(?:en|eng|english)(?:[\W_]|$)", re.I)
_TITOLO_INGLESE = re.compile(r"annual[\s_-]*report|half[\s_-]*year|quarterly|financial[\s_-]*report|interim[\s_-]*report"
                             r"|financial[\s_-]*statements", re.I)
_ANNO = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")
_DATA_DMY = re.compile(r"(?<!\d)(0[1-9]|[12]\d|3[01])(0[1-9]|1[0-2])(20[0-4]\d)(?!\d)")  # 30062026
_DATA_DMY_SEP = re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[._-](0?[1-9]|1[0-2])[._-](20[0-4]\d)(?!\d)")  # 23.05.2025
_DATA = re.compile(r"(?<!\d)(20[0-4]\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_MESI = {m: i + 1 for i, nomi in enumerate((
    "january gennaio januar janvier enero", "february febbraio februar fevrier febrero",
    "march marzo marz maerz mars", "april aprile avril abril", "may maggio mai mayo", "june giugno juni juin junio",
    "july luglio juli juillet julio", "august agosto aout", "september settembre septembre septiembre",
    "october ottobre oktober octobre octubre", "november novembre noviembre",
    "december dicembre dezember decembre diciembre")) for m in nomi.split()}
_ALTERNATIVA_MESI = "|".join(sorted(_MESI, key=len, reverse=True))
_DATA_MESE = re.compile(r"(?<!\d)([0-3]?\d)[\s_.-]*(?:de[\s_]+)?(" + _ALTERNATIVA_MESI
                        + r")[\s_.-]*(?:de[\s_]+)?(20[0-4]\d)(?!\d)", re.I)  # anche «31march2026» (prova reale)
_DATA_MESE_US = re.compile(r"(?<![a-z])(" + _ALTERNATIVA_MESI + r")[\s_.-]*([0-3]?\d)(?:st|nd|rd|th)?,?[\s_.-]*"
                           r"(20[0-4]\d)(?!\d)", re.I)  # «June 30, 2026»
_TRIMESTRE_AA = re.compile(r"(?<![a-z0-9])q([1-4])[\s_-]?(\d{2})(?!\d)", re.I)  # «Q226» = Q2 2026 (prova reale)
_TRIMESTRE = re.compile(r"\bq([1-4])\b|[\s_-]q([1-4])[\s_-]", re.I)
_FINE_TRIMESTRE = ("03-31", "06-30", "09-30", "12-31")
# «Q4-FY26», «FY25_Q2», «FY2026-Q3»: trimestre di un esercizio fiscale (prova dal vivo 06/10: esercizio a
# settembre). La chiusura non si deduce: periodo None, etichetta «Q4 FY2026» dichiarata
_FISCALE = re.compile(r"(?<![a-z0-9])(?:q([1-4])[\s_-]*fy[\s_-]*((?:20)?\d{2})|fy[\s_-]*((?:20)?\d{2})[\s_-]*q([1-4]))(?!\d)",
                      re.I)


def _senza_accenti(testo):
    return (testo.lower().replace("ä", "a").replace("é", "e").replace("û", "u").replace("è", "e")
            .replace("ü", "u").replace("ö", "o"))


def _fine_mese(anno, mese, giorno):
    try:
        return int(giorno) == calendar.monthrange(int(anno), int(mese))[1]
    except (ValueError, calendar.IllegalMonthError):
        return False


def _date_nel_testo(testo):
    """[(anno, mese, giorno, inizio)] delle date scritte nel testo (mese a parole, AAAA-MM-GG,
    GG.MM.AAAA, GGMMAAAA), nell'ordine di priorita' della prova reale."""
    t = _senza_accenti(testo)
    out = []
    for m in _DATA_MESE.finditer(t):
        out.append((int(m.group(3)), _MESI[m.group(2).lower()], int(m.group(1)), m.start()))
    for m in _DATA_MESE_US.finditer(t):
        out.append((int(m.group(3)), _MESI[m.group(1).lower()], int(m.group(2)), m.start()))
    numeriche = list(_DATA.finditer(t))
    for m in reversed(numeriche):  # l'ultima AAAA-MM-GG prima (in testa c'e' di solito la pubblicazione)
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), m.start()))
    for m in _DATA_DMY_SEP.finditer(t):
        out.append((int(m.group(3)), int(m.group(2)), int(m.group(1)), m.start()))
    if not numeriche:
        for m in _DATA_DMY.finditer(t):
            out.append((int(m.group(3)), int(m.group(2)), int(m.group(1)), m.start()))
    return out


def _periodo_dal_nome(nome, testo, tipo, chiusura_esercizio=None):
    """(periodo, base, tipo) dal nome del file e dal testo del link; periodo None se non si capisce.
    `chiusura_esercizio` («MM-DD», None = ignota): chiusura presunta dell'annuale col solo anno nel nome.

    Una data a fine TRIMESTRE e' la chiusura del periodo, purche' il suo anno non superi gli altri anni
    del nome; ogni altra data e' la pubblicazione (prova reale: «2026-07-02-…-Q2-…», «…_23.05.2025.pdf»
    sul bilancio 2024; revisione R-8 C5: «…-2026_31.07.2026», «Annual-Report-2025_2026-03-31») e il
    suo anno non conta come anno del periodo."""
    date_nome = _date_nel_testo(nome)
    anni_nome = [int(a) for a in _ANNO.findall(nome)]

    def chiusura(d):
        if not _fine_mese(*d[:3]) or d[1] not in (3, 6, 9, 12):
            return False
        altri = list(anni_nome)
        if d[0] in altri:
            altri.remove(d[0])
        for x in date_nome:  # gli anni delle altre date (pubblicazioni) non contano
            if x is not d and x[0] in altri:
                altri.remove(x[0])
        return not altri or d[0] <= max(altri)
    chiusure = [d for d in date_nome if chiusura(d)]
    if chiusure:
        a, m, g, _ = chiusure[0]
        return f"{a}-{m:02d}-{g:02d}", "data di chiusura nel nome del file", tipo
    nota = (" (data a fine mese nel nome trattata come pubblicazione)"
            if any(_fine_mese(*d[:3]) for d in date_nome) else "")
    if _TRIMESTRE_AA.search(nome) and not _ANNO.search(nome):
        q = _TRIMESTRE_AA.search(nome)
        tipo = "trimestrale" if tipo != "annuale" or q.group(1) != "4" else tipo
        return f"20{q.group(2)}-{_FINE_TRIMESTRE[int(q.group(1)) - 1]}", "trimestre e anno nel nome del file (trimestre solare presunto: esercizio non noto)", tipo
    anni = [int(a) for a in _ANNO.findall(testo)]
    for a, _, _, _ in date_nome:  # anni delle date di pubblicazione: tolti una volta ciascuno
        if a in anni:
            anni.remove(a)
    if not anni:
        return None, None, tipo
    anno = max(anni)
    q = _TRIMESTRE.search(" " + testo + " ")
    if tipo == "trimestrale" and q:
        return (f"{anno}-{_FINE_TRIMESTRE[int(q.group(1) or q.group(2)) - 1]}",
                "trimestre e anno nel nome o nel titolo (trimestre solare presunto: esercizio non noto)", tipo)
    if tipo == "trimestrale":
        return None, None, tipo  # trimestre ignoto: l'anno da solo non basta
    fy = mese_chiusura(chiusura_esercizio)
    if fy not in (None, 12):
        # GENERALITA' UE (Opus 5.5): esercizio non solare noto. L'annuale chiude nel mese dell'esercizio
        # (presunta, dichiarata); la semestrale col solo anno resta senza periodo (l'anno del semestre e'
        # ambiguo), lo decide la prima pagina
        if tipo == "semestrale":
            return None, None, tipo
        fine = f"{fy:02d}-{calendar.monthrange(anno, fy)[1]:02d}"
        return (f"{anno}-{fine}", f"anno nel nome o nel titolo (chiusura dell'esercizio dell'emittente al "
                f"{fine[3:]}/{fine[:2]} presunta)" + nota, tipo)
    if tipo == "semestrale":
        return f"{anno}-06-30", "anno nel nome o nel titolo (chiusura del semestre al 30/06 presunta)" + nota, tipo
    return f"{anno}-12-31", "anno nel nome o nel titolo (chiusura dell'esercizio al 31/12 presunta)" + nota, tipo


def _periodo_dalla_pagina(testa, tipo):
    """(periodo, base) dal testo della prima pagina: la piu' recente data a fine mese, altrimenti
    «<titolo del tipo> AAAA» / «AAAA <titolo>»; (None, None) se non si capisce."""
    chiusure = [d for d in _date_nel_testo(testa) if _fine_mese(*d[:3])]
    if chiusure:
        a, m, g, _ = max(chiusure)
        return f"{a}-{m:02d}-{g:02d}", "data di chiusura nella prima pagina"
    rx = dict(_TIPI_PDF)[tipo]
    for m in rx.finditer(testa):
        dopo = _ANNO.search(testa[m.end():m.end() + 40])
        prima = _ANNO.search(testa[max(0, m.start() - 8):m.start()])
        trovato = dopo or prima
        if trovato and not re.search(r"\d", testa[m.end():m.end() + dopo.start()] if dopo else ""):
            anno = int(trovato.group(1))
            if tipo == "trimestrale":
                q = _TRIMESTRE.search(" " + testa + " ")
                if not q:
                    return None, None
                return f"{anno}-{_FINE_TRIMESTRE[int(q.group(1) or q.group(2)) - 1]}", "trimestre e anno nella prima pagina (trimestre solare presunto: esercizio non noto)"
            fine = "06-30" if tipo == "semestrale" else "12-31"
            return f"{anno}-{fine}", f"titolo e anno nella prima pagina (chiusura al {fine[3:]}/{fine[:2]} presunta)"
    return None, None


def etichetta_via(host):
    """(etichetta, etichetta_en) di un PDF dell'emittente servito da un altro host (CDN della pagina IR)."""
    return ETICHETTA_SITO_VIA.format(host=host), ETICHETTA_SITO_VIA_EN.format(host=host)


def nome_documento(doc):
    """«relazione semestrale», «comunicato dei risultati trimestrale», «presentazione dei risultati …»."""
    return f"{_NOME_TIPO_DOCUMENTO.get(doc.get('tipo_documento') or 'relazione', 'relazione')} {doc.get('tipo')}"


def _pagina_vietata(prima_pagina):
    """Motivo se la prima pagina non si e' letta per robots.txt o per un sito che blocca i bot (fermi,
    mai aggirati), None altrimenti. Le voci di cache senza il flag si riconoscono dal tipo d'eccezione."""
    if not isinstance(prima_pagina, dict) or not prima_pagina.get("errore"):
        return None
    errore = str(prima_pagina["errore"])
    if prima_pagina.get("vietato") or errore.startswith(("PermissionError", "SitoBloccato")):
        return errore
    return None


def classifica_pdf(voce, *, prima_pagina=None, dominio=None, chiusura_esercizio=None):
    """Esito DICHIARATO di un link a PDF del sito: {"url", "testo", "ammesso", "tipo", "periodo",
    "base_periodo", "motivo", "nota", "serve_prima_pagina", "origine", "etichetta", "etichetta_en",
    "via_host", "tipo_documento", "periodo_stato", "periodi_visti"}.

    `chiusura_esercizio`: «MM-DD» della chiusura d'esercizio dell'emittente (chiusura_esercizio_emittente), None
    se ignota: decide se una data di chiusura nel nome di una relazione senza aggettivo e' annuale o semestrale.

    Riconoscimento su nome del file, testo del link e (se letta) prima pagina: `prima_pagina` =
    testo, oppure la voce di cache {"testo"} / {"errore", "vietato"}. Decisione PM 06/10 sera: si
    ammette con l'etichetta (tipo_documento relazione / comunicato_risultati / presentazione; periodo
    certo / da_confermare con le date viste); fuori SOLO robots.txt/blocchi, altre entita', non
    finanziari evidenti, estratti, e cio' che non e' riconoscibile come documento di un periodo."""
    url = voce["url"]
    parti = urlsplit(url)
    percorso = unquote(parti.path)
    nome = percorso.rsplit("/", 1)[-1]
    testo = f"{nome} {voce.get('testo') or ''}".replace("_", " ")
    out = {"url": url, "testo": (voce.get("testo") or nome)[:160], "ammesso": False, "tipo": None, "periodo": None,
           "base_periodo": None, "motivo": None, "nota": None, "serve_prima_pagina": False,
           "origine": ORIGINE_SITO, "etichetta": ETICHETTA_SITO, "etichetta_en": ETICHETTA_SITO_EN, "via_host": None,
           "sito_ir": None, "cdn_generico": False, "tipo_documento": None, "periodo_stato": None, "periodi_visti": [],
           "periodo_fiscale": None, "tipo_base": None}
    if (isinstance(dominio, tuple) and len(dominio) > 1 and stesso_dominio(url, dominio[1])
            and not stesso_dominio(url, dominio[0])):
        out["sito_ir"] = dominio[1]  # documento del sito IR scoperto (altro dominio): dichiarato
        gruppo = getattr(dominio, "gruppo", False)
        out["etichetta"] = (ETICHETTA_SITO_GRUPPO if gruppo else ETICHETTA_SITO_IR).format(host=dominio[1],
                                                                                           home=dominio[0])
        out["etichetta_en"] = (ETICHETTA_SITO_GRUPPO_EN if gruppo else ETICHETTA_SITO_IR_EN).format(
            host=dominio[1], home=dominio[0])

    def scarto(motivo, **altro):
        out.update(motivo=motivo, **altro)
        return out
    if dominio and not stesso_dominio(url, dominio):
        host = (parti.hostname or "").lower()
        pagina_ir = voce.get("pagina")
        # PDF su un altro host (q4cdn, gcs-web…): ammesso SOLO se linkato da una pagina del sito IR
        # dell'emittente (https, senza credenziali) E se l'host e' una piattaforma IR riconosciuta
        if not (pagina_ir and stesso_dominio(str(pagina_ir), dominio) and parti.scheme == "https" and host
                and parti.username is None and parti.password is None):
            return scarto(f"PDF su un altro dominio ({host}) non linkato da una pagina del sito IR dell'emittente: "
                          "provenienza non provata")
        if piattaforma_documenti(host):
            out["via_host"] = host
            out["etichetta"], out["etichetta_en"] = etichetta_via(host)
        elif cdn_generico(host):
            out.update(via_host=host, cdn_generico=True, etichetta=ETICHETTA_CDN.format(host=host),
                       etichetta_en=ETICHETTA_CDN_EN.format(host=host))
        else:
            return scarto(f"PDF su un altro dominio ({host}) che non e' una piattaforma IR riconosciuta "
                          f"({', '.join(PIATTAFORME_DOCUMENTI)}) ne' un CDN generico: puo' essere di terzi, escluso")
    m = _RICERCA.search(f"{testo} {parti.hostname or ''} {percorso}")
    if m:
        return scarto(f"nota di ricerca o di un broker («{m.group(0)}»): non e' un documento dell'emittente")
    vietata = _pagina_vietata(prima_pagina)
    if vietata:
        return scarto(f"prima pagina non letta ({vietata}): robots.txt/blocco del sito rispettato")
    m = _NON_FINANZIARIO.search(testo)
    if m:
        return scarto(f"non finanziario («{m.group(0)}»): fuori dal profilo finanziario, dichiarato")
    m = _ALTRA_ENTITA_PDF.search(testo)
    if m:
        return scarto(f"documento di un'altra entita' («{m.group(0)}»): non dell'emittente")
    m = _ESCLUDI_PDF.search(testo)
    if m:
        return scarto(f"non e' una relazione periodica («{m.group(0)}»)")
    presentazione = _PRESENTAZIONE.search(testo)
    estratto = _ESTRATTO.search(testo)
    stampa = _PERCORSO_STAMPA.search(percorso.rsplit("/", 1)[0] + "/")
    doc_tipo = ("presentazione" if presentazione else "estratto" if estratto else
                "comunicato_risultati" if (_COMUNICATO.search(testo) or stampa) else None)
    tipo = next((t for t, rx in _TIPI_PDF if rx.search(testo)), None)
    generico = not _NOME_DOCUMENTO.search(testo)
    if tipo is None and generico:
        if presentazione:
            return scarto(f"presentazione senza periodo di riferimento («{presentazione.group(0)}»): fuori dal "
                          "profilo, dichiarata")
        if estratto:
            return scarto(f"estratto senza periodo di riferimento («{estratto.group(0)}»): fuori dal profilo, "
                          "dichiarato")
        if doc_tipo:
            return scarto("comunicato non sui risultati di un periodo: fuori dal profilo, dichiarato")
        return scarto("non riconosciuto come documento periodico (nome del file e titolo)")
    pagina = prima_pagina.get("testo") if isinstance(prima_pagina, dict) else prima_pagina
    errore = prima_pagina.get("errore") if isinstance(prima_pagina, dict) else None
    testa = " ".join(str(pagina).split())[:2000] if pagina is not None else None
    note = []
    visti_titolo = None
    if tipo is None:
        # «Consolidated Financial Report 2026» (prova reale): documento, ma di che periodo? lo dice la copertina
        perche = "nome da documento ma senza il tipo di relazione"
        if testa is None:
            if errore:
                return scarto(f"{perche}; prima pagina non letta ({errore}): tipo non riconoscibile")
            return scarto(f"{perche}; prima pagina non letta: tipo non riconoscibile", serve_prima_pagina=True)
        if _PRESENTAZIONE.search(testa):
            doc_tipo = "presentazione"
        tipo = next((t for t, rx in _TIPI_PDF if rx.search(testa)), None)
        if tipo is None:
            return scarto("prima pagina senza il tipo di documento periodico: tipo non riconoscibile")
        periodo, base = _periodo_dalla_pagina(testa, tipo)
        if periodo is None:
            note.append("periodo di riferimento non dichiarato nel nome, nel titolo ne' nella prima pagina")
        generico = False  # tipo (e periodo) dalla copertina
    else:
        periodo, base, tipo = _periodo_dal_nome(nome, testo, tipo, chiusura_esercizio)
        fiscale = _FISCALE.search(testo)
        if fiscale and tipo == "trimestrale":
            q, anno = fiscale.group(1) or fiscale.group(4), fiscale.group(2) or fiscale.group(3)
            out["periodo_fiscale"] = f"Q{q} FY{anno if len(anno) == 4 else '20' + anno}"
            periodo, base = None, None
            note.append(f"trimestre fiscale {out['periodo_fiscale']}: chiusura non deducibile dal nome "
                        "(esercizio non solare)")
        fy = mese_chiusura(chiusura_esercizio)
        fuori_esercizio = base == "data di chiusura nel nome del file" and (
            str(periodo or "")[5:] == "06-30" if fy is None else mese_esercizio(periodo) != fy)
        if tipo == "annuale" and not _ANNUALE_ESPLICITO.search(testo) and (
                _RELAZIONE_GENERICA.search(testo) or fuori_esercizio):
            # BASSI v2: mai annuale con chiusura al 30/06 nel nome se nessuna parola dice «annuale»; GENERALITA' UE
            # (Opus 5.5): con l'esercizio noto, mai annuale con una chiusura nel nome fuori dal mese dell'esercizio
            deciso, perche = _tipo_relazione_generica(periodo, base, testa, chiusura_esercizio)
            if deciso is None:
                da_leggere = testa is None and not errore
                return scarto(perche + ("; prima pagina non letta" + (f" ({errore})" if errore else "")
                                        if testa is None else ""), serve_prima_pagina=da_leggere,
                              tipo="da_confermare")
            out["tipo_base"] = perche
            note.append(perche)
            if deciso != tipo and "presunt" in str(base):  # chiusura presunta dal solo anno: quella del tipo deciso
                periodo, base, deciso = _periodo_dal_nome(nome, testo, deciso, chiusura_esercizio)
            if (base == "data di chiusura nel nome del file" and testa
                    and _tipo_dalla_chiusura(str(periodo), chiusura_esercizio)[0] != deciso):
                # GENERALITA' UE (Opus 5.5): il tipo l'ha deciso il titolo, ma la data nel nome non e' verificata
                # sull'esercizio (ignoto o discorde): il periodo e' certo solo se la prima pagina porta la stessa
                # data di chiusura, altrimenti presunto e con le date viste (da confermare)
                sulla_pagina, base_pagina = _periodo_dalla_pagina(testa, deciso)
                if sulla_pagina == periodo and "presunt" not in str(base_pagina):
                    base += "; data di chiusura confermata dalla prima pagina"
                else:
                    base += "; chiusura nel nome non verificata sull'esercizio dell'emittente: presunta"
                    if sulla_pagina:
                        visti_titolo = sulla_pagina
            tipo = deciso
    out["tipo"] = tipo
    if testa is not None and tipo in ("annuale", "semestrale"):
        # v2: la copertina di un comunicato conta solo per i PDF col nome da relazione (non da comunicato)
        altra = _prima_pagina_non_finanziaria(testa, comunicato=not doc_tipo and not generico)
        if altra:
            return scarto(f"tipo da confermare: nome da relazione {tipo} ma la prima pagina e' «{altra}» (non la "
                          "relazione finanziaria)", tipo="da_confermare")
    visti = {visti_titolo} if visti_titolo else set()
    if generico or periodo is None:
        perche = ("nome generico, senza una parola da relazione" if generico
                  else "periodo di riferimento non dichiarato nel nome ne' nel titolo")
        if testa is None:
            note.append(f"{perche}; prima pagina non letta" + (f" ({errore})" if errore else ""))
            out["serve_prima_pagina"] = not errore
            if generico and not doc_tipo:
                # prova dal vivo 06/10: «…pilar3-q4-2020.pdf» (rischi) non e' un comunicato dei risultati:
                # senza una parola da risultati il nome generico non basta, decide la prima pagina
                if not _RISULTATI.search(testo):
                    return scarto(f"{perche} ne' da risultati; prima pagina non letta" + (f" ({errore})" if errore
                                  else ": da decidere sulla prima pagina"), serve_prima_pagina=not errore)
                doc_tipo = "comunicato_risultati"
                note.append("tipo di documento dedotto dal nome generico (da confermare)")
        else:
            if _PRESENTAZIONE.search(testa):
                doc_tipo = "presentazione"
            if generico and not (_NOME_DOCUMENTO.search(testa) and dict(_TIPI_PDF)[tipo].search(testa)):
                if not doc_tipo and not (_RISULTATI.search(testo) or _RISULTATI.search(testa)):
                    return scarto("nome generico e prima pagina senza titolo da relazione ne' da risultati")
                doc_tipo = doc_tipo or "comunicato_risultati"
            elif generico:
                doc_tipo = doc_tipo or "relazione"
            sulla_pagina, base_pagina = _periodo_dalla_pagina(testa, tipo)
            if periodo is None:
                periodo, base = sulla_pagina, base_pagina
                if periodo is None:
                    note.append("periodo di riferimento non dichiarato nel nome, nel titolo ne' nella prima pagina")
            elif sulla_pagina and sulla_pagina != periodo:
                visti.add(sulla_pagina)  # nome e copertina discordi: le due date viste, da confermare
                note.append(f"periodo del nome {periodo}, della prima pagina {sulla_pagina}: da confermare")
            else:
                base += "; titolo da relazione confermato dalla prima pagina"
                if sulla_pagina == periodo and "prima pagina" in str(base_pagina) and "presunt" not in str(base_pagina):
                    base += "; data di chiusura confermata dalla prima pagina"
    if periodo is not None:
        try:
            date.fromisoformat(periodo)
        except ValueError:
            note.append(f"periodo di riferimento non valido ({periodo})")
            periodo, base = None, None
    fy_doc = mese_chiusura(chiusura_esercizio)
    if (periodo and tipo == "semestrale" and periodo[5:7] in ("03", "09")
            and not re.search(r"half|semestr|halbjahr|\bh1\b|\b1h\b", testo, re.I)
            # v2: con l'esercizio noto vale la regola dei sei mesi (esercizi al 31/03 e al 30/09: il semestre chiude
            # a settembre o a marzo); il trimestre si presume solo fuori da fine esercizio e fine semestre
            and (fy_doc is None or mese_esercizio(periodo) not in (fy_doc, (fy_doc + 5) % 12 + 1))):
        tipo = "trimestrale"  # «interim report at 31 March»: un trimestre (prova reale)
    if periodo:
        visti.add(periodo)
    certo = bool(periodo) and len(visti) == 1 and (
        "presunt" not in str(base) or "data di chiusura confermata" in str(base))
    out.update(ammesso=True, tipo=tipo, periodo=periodo, base_periodo=base, tipo_documento=doc_tipo or "relazione",
               periodo_stato="certo" if certo else "da_confermare", periodi_visti=sorted(visti),
               nota="; ".join(note) or None)
    return out


def _documento_pdf(voce, prima_pagina=None):
    """{"url", "testo", "tipo", "periodo", ...} di un link a PDF IR, None se non e' una relazione
    ammessa (il motivo dello scarto lo da' classifica_pdf)."""
    esito = classifica_pdf(voce, prima_pagina=prima_pagina)
    return esito if esito["ammesso"] else None


def _parole_nome(url):
    nome = unquote(urlsplit(url).path).rsplit("/", 1)[-1].lower()
    return {w for w in re.findall(r"[a-z]{2,}", nome) if w not in ("pdf", "final", "vf", "en", "de", "it", "fr", "v")}


def _in_inglese(url):
    nome = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    return bool(_INGLESE.search(nome) or _TITOLO_INGLESE.search(nome.replace("_", " ")))


def scegli_pdf(pdf, *, oggi=None, prime_pagine=None, dominio=None, dopo=None, chiusura_esercizio=None):
    """Dai link a PDF trovati: il documento periodico piu' recente e l'omologo dell'anno prima
    (stesso tipo, periodo un anno prima ±20 giorni). None se nessuno e' una relazione ammessa.
    `dominio`: solo PDF del sito dell'emittente (gli altri scartati col motivo); `dopo`: solo
    relazioni con periodo successivo a quella data come «ultimo» (None se non ce ne sono).

    Ogni documento e la scelta portano l'origine dichiarata (ETICHETTA_SITO); `scartati` dice
    perche' gli altri PDF non sono stati ammessi (presentazioni, periodo ignoto, ...).
    `chiusura_esercizio`: «MM-DD» dell'esercizio dell'emittente (chiusura_voce), None se ignota."""
    oggi = oggi or date.today()
    prime_pagine = prime_pagine if isinstance(prime_pagine, dict) else {}
    esiti = [classifica_pdf(v, prima_pagina=prime_pagine.get(v["url"]), dominio=dominio,
                            chiusura_esercizio=chiusura_esercizio)
             for v in pdf or [] if isinstance(v, dict) and v.get("url")]
    docs, scartati = [], []
    for e in esiti:
        if e["ammesso"] and e["periodo"] and date.fromisoformat(e["periodo"]) > oggi:
            # periodo nel futuro (data letta male): ammesso con l'etichetta, mai scelto come ultimo
            e = {**e, "periodo_stato": "da_confermare",
                 "nota": "; ".join(x for x in (e.get("nota"), f"periodo {e['periodo']} nel futuro: mai scelto") if x)}
        (docs if e["ammesso"] else scartati).append(e)
    # collocabili: con un periodo non futuro (gli altri restano nella lista dei documenti, dichiarati)
    collocabili = [d for d in docs if d["periodo"] and date.fromisoformat(d["periodo"]) <= oggi]
    if not collocabili:
        return None
    ordine = {"annuale": 0, "semestrale": 1, "trimestrale": 2}  # a parita' di periodo: l'annuale

    def omologo(doc):
        fine = date.fromisoformat(doc["periodo"])
        try:
            attesa = fine.replace(year=fine.year - 1)
        except ValueError:  # 29 febbraio
            attesa = fine - timedelta(days=365)
        simili = [d for d in collocabili if d["tipo"] == doc["tipo"] and d["url"] != doc["url"]
                  and abs((date.fromisoformat(d["periodo"]) - attesa).days) <= 20]
        # stesso tipo di documento (relazione con relazione), poi il nome piu' simile (MD&A con MD&A)
        return max(simili, key=lambda d: (d["tipo_documento"] == doc["tipo_documento"],
                                          len(_parole_nome(d["url"]) & _parole_nome(doc["url"])),
                                          _in_inglese(d["url"]), d["periodo"])) if simili else None

    # a parita' di periodo: la relazione prima del comunicato e della presentazione, poi l'annuale
    chiave = lambda d: (d["periodo"], _RANGO_DOCUMENTO[d["tipo_documento"]], -ordine[d["tipo"]], _in_inglese(d["url"]))
    # prima un documento recente CON l'omologo dell'anno prima (serve la coppia), altrimenti il piu' recente
    scelti = [d for d in collocabili if not dopo or d["periodo"] > str(dopo)]
    if not scelti:
        return None  # nessuna relazione piu' recente di `dopo`: il chiamante lo dichiara
    # prova dal vivo 06/10: una «Q3 Analyst Recap» (pre-chiusura) scavalcava la semestrale. Il profilo si
    # ancora a relazioni e comunicati; le presentazioni solo se non c'e' altro (il piu' recente in
    # assoluto resta dichiarato in «piu_recente»)
    scelti = [d for d in scelti if d["tipo_documento"] not in ("presentazione", "estratto")] or scelti
    con_coppia = [d for d in scelti if omologo(d) and date.fromisoformat(d["periodo"]) >= oggi - timedelta(days=550)]
    ultimo = max(con_coppia or scelti, key=chiave)
    precedente = omologo(ultimo)
    fiscali = [d for d in docs if d.get("periodo_fiscale")]
    per_tipo = {}
    for d in docs:
        per_tipo[d["tipo_documento"]] = per_tipo.get(d["tipo_documento"], 0) + 1
    ordinati = sorted(docs, key=lambda d: (d["periodo"] or "", _RANGO_DOCUMENTO[d["tipo_documento"]]), reverse=True)
    return {"tipo": ultimo["tipo"], "ultimo": ultimo, "precedente": precedente, "candidati": len(docs),
            "tipo_documento": ultimo["tipo_documento"], "periodo_stato": ultimo["periodo_stato"],
            "via_host": ultimo["via_host"], "sito_ir_dominio": ultimo["sito_ir"],
            "origine": ORIGINE_SITO, "etichetta": ultimo["etichetta"], "etichetta_en": ultimo["etichetta_en"],
            "deposito_ufficiale": False,
            "piu_recente": _compatto(max(collocabili, key=chiave)), "ammessi_per_tipo": per_tipo,
            # trimestre fiscale piu' recente (senza data: dichiarato a parte, mai scelto come ultimo)
            "piu_recente_fiscale": _compatto(max(fiscali, key=lambda d: (d["periodo_fiscale"][4:],
                                                                        d["periodo_fiscale"][:2]))) if fiscali else None,
            "documenti": [_compatto(d) for d in ordinati[:MAX_DOCUMENTI]],
            "scartati": [{"url": e["url"], "motivo": e["motivo"]} for e in scartati[:MAX_SCARTATI]],
            "scartati_totale": len(scartati)}


def _compatto(d):
    """Voce di un documento ammesso con le sue etichette (lista «documenti»)."""
    return {k: d.get(k) for k in ("url", "testo", "tipo", "tipo_documento", "periodo", "periodo_stato",
                                  "periodi_visti", "periodo_fiscale", "base_periodo", "via_host", "sito_ir", "cdn_generico",
                                  "etichetta", "nota")}


def riepilogo_scarti(pdf, *, oggi=None, prime_pagine=None, dominio=None, chiusura_esercizio=None):
    """Frase dichiarata sui PDF del sito quando nessun documento si colloca: quanti e perche' (motivi
    raggruppati); i documenti ammessi senza un periodo collocabile sono contati a parte."""
    prime_pagine = prime_pagine if isinstance(prime_pagine, dict) else {}
    esiti = [classifica_pdf(v, prima_pagina=prime_pagine.get(v["url"]), dominio=dominio,
                            chiusura_esercizio=chiusura_esercizio)
             for v in pdf or [] if isinstance(v, dict) and v.get("url")]
    conta = {}
    for e in esiti:
        if not e["ammesso"]:
            gruppo = re.sub(r"\s*\(«[^»]*»\)|\s*\([^)]*\)", "", e["motivo"] or "")[:90]
            conta[gruppo] = conta.get(gruppo, 0) + 1
    gruppi = "; ".join(f"{n} {g}" for g, n in sorted(conta.items(), key=lambda x: -x[1])[:3])
    ammessi = sum(1 for e in esiti if e["ammesso"])
    testa = (f"nessun documento periodico ammesso tra {len(esiti)} PDF del {ETICHETTA_SITO}" if not ammessi else
             f"nessun documento con un periodo collocabile tra {len(esiti)} PDF del {ETICHETTA_SITO} "
             f"({ammessi} ammessi col periodo da confermare o nel futuro)")
    return testa + (f": {gruppi}" if gruppi else "")


def _linkato_dal_sito(voce, dominio):
    """Il PDF e' del dominio dell'emittente o linkato da una sua pagina IR (https) su una piattaforma IR."""
    return stesso_dominio(voce["url"], dominio) or (
        urlsplit(voce["url"]).scheme == "https" and bool(voce.get("pagina"))
        and stesso_dominio(str(voce["pagina"]), dominio)
        and (piattaforma_documenti(urlsplit(voce["url"]).hostname) or cdn_generico(urlsplit(voce["url"]).hostname)))


def cdn_generico(host):
    """L'host e' un CDN generico della lista dichiarata CDN_GENERICI (suffisso esatto dell'host)."""
    h = str(host or "").lower().rstrip(".")
    return any(h == c or h.endswith("." + c) for c in CDN_GENERICI)


def piattaforma_documenti(host):
    """L'host e' una piattaforma IR riconosciuta (PIATTAFORME_DOCUMENTI, primo nome del dominio registrabile)."""
    d = dominio_registrabile(host) or ""
    return d.split(".")[0] in PIATTAFORME_DOCUMENTI


def da_leggere_prima_pagina(pdf, dominio, *, limite=MAX_PRIME_PAGINE, chiusura_esercizio=None):
    """URL dei PDF (del dominio o linkati da una sua pagina IR) a cui manca la prima pagina per
    decidere tipo o periodo: i piu' recenti per anno nel nome, al massimo `limite` (gli altri restano
    col periodo da confermare o scartati col motivo)."""
    candidati = []
    for v in pdf or []:
        if not isinstance(v, dict) or not v.get("url") or not _linkato_dal_sito(v, dominio):
            continue
        if classifica_pdf(v, dominio=dominio, chiusura_esercizio=chiusura_esercizio)["serve_prima_pagina"]:
            anni = [int(a) for a in _ANNO.findall(unquote(v["url"]) + " " + str(v.get("testo") or ""))]
            candidati.append((max(anni, default=0), v["url"]))
    candidati.sort(key=lambda x: -x[0])
    return [u for _, u in candidati[:limite]]


def dominio_sito(sito):
    """Dominio registrabile del sito dell'emittente, None se il sito non e' noto."""
    return dominio_registrabile(urlsplit(str(sito or "")).hostname) if sito else None


def pdf_trovati(ticker, *, cache_dir=None, oggi=None):
    """PDF IR scelti dalla cache dell'ultima esplorazione (nessuna rete), con la data e l'origine
    dichiarata («sito dell'emittente, non archivio ufficiale»)."""
    voce = _leggi_json(_cache_dir(cache_dir) / _nome_file(ticker))
    if not voce:
        return None
    scelta = scegli_pdf(voce.get("pdf"), oggi=oggi, prime_pagine=voce.get("prime_pagine"),
                        dominio=domini_voce(voce), chiusura_esercizio=chiusura_voce(voce))
    return ({**scelta, "at": voce.get("at"), "sito": voce.get("sito"), "accesso": voce.get("accesso"),
             "sito_ir": voce.get("sito_ir"), "sito_ir_origine": voce.get("sito_ir_origine")}
            if scelta else None)


def impronta_sito(profilo, *, nav=None, head_fn=None):
    """Impronta leggera dei PDF di un profilo «sito dell'emittente» (revisione R-8 C4): una HEAD per
    ir_url, robots.txt rispettato, nel ritmo del Navigatore, nessun redirect seguito, IP pubblico.
    Firma = ETag, Last-Modified, Content-Length. Se una HEAD e' vietata, bloccata, fallisce o il sito
    non da' nessuna delle tre intestazioni: impronta NON confrontabile col motivo (run completo, mai un
    controllo saltato in silenzio). Forma: {"fonte": "sito", "varianti": {tipo: {"relazione": firma}}}."""
    tipo = profilo.get("tipo") or "?"
    urls = [u for u in profilo.get("ir_urls") or [] if isinstance(u, str)]

    def non_confrontabile(motivo):
        return {"fonte": "sito", "non_confrontabile": True, "motivo": motivo, "varianti": {}}
    if not urls:
        return non_confrontabile("profilo senza documenti del sito")
    firme, navi, nav_dato = [], {}, nav
    for url in urls:
        host = (urlsplit(url).hostname or "").lower()
        dom = dominio_registrabile(host) or host
        # un navigatore per host (PDF anche «via» il CDN della pagina IR: robots.txt di quell'host)
        if nav_dato is not None and getattr(nav_dato, "dominio", None) in (None, dom):
            nav = nav_dato
        else:
            nav = navi.setdefault(dom, Navigatore(dom))
        try:
            if not nav.consentito(url):
                stato, ignoto = nav.bloccato.get(host), nav.robots_ignoto.get(host)
                return non_confrontabile(f"{frase_blocco(stato)} su robots.txt" if stato else
                                         f"robots.txt non leggibile ({ignoto})" if ignoto else
                                         f"robots.txt vieta {urlsplit(url).path[:80]}")
            if nav.richieste:
                nav._dormi(nav.pausa)
            nav.richieste += 1
            if head_fn is None:
                import requests
                from bellomberg.market_data.lettore_trimestrali import UA, _richiedi_indirizzi_pubblici
                if urlsplit(url).scheme != "https" or not stesso_dominio(url, nav.dominio):
                    return non_confrontabile(f"documento fuori dal dominio del sito ({host})")
                _richiedi_indirizzi_pubblici(host, urlsplit(url).port or 443)
                r = requests.head(url, headers={"User-Agent": UA}, timeout=TIMEOUT_S, allow_redirects=False)
            else:
                r = head_fn(url)
        except Exception as exc:
            return non_confrontabile(f"HEAD non riuscita ({motivo_eccezione(exc, 120)})")
        if r.status_code in STATI_BLOCCO:
            return non_confrontabile(f"{frase_blocco(r.status_code)} sulla HEAD")
        if r.status_code != 200:
            return non_confrontabile(f"HEAD: HTTP {r.status_code}")
        h = r.headers or {}
        firma = [h.get("ETag"), h.get("Last-Modified"), h.get("Content-Length")]
        if not any(firma):
            return non_confrontabile("il sito non da' ETag, Last-Modified ne' Content-Length: nessun controllo leggero")
        firme.append([url] + firma)
    digest = hashlib.sha256(json.dumps(firme, sort_keys=True).encode("utf-8")).hexdigest()
    return {"fonte": "sito", "varianti": {tipo: {"relazione": digest}}, "firme": firme}


def scopri_senza_fonte(tickers, *, oggi=None, consigliere_fn=None, scopri_fn=None, scadenza=None):
    """Controllo giornaliero e attivazione: esplora i siti dei titoli «senza fonte» (cache
    CACHE_GIORNI). Nessun PDF scaricato: la stima resta un clic dell'utente."""
    esiti = []
    for t in tickers:
        motivo = _fermati(consigliere_fn, scadenza)
        if motivo:
            esiti.append({"ticker": None, "rinviata": True, "motivi": [motivo]})
            break
        try:
            trovato = (scopri_fn or scopri)(t, oggi=oggi)
            scelta = scegli_pdf(trovato.get("pdf"), oggi=oggi, prime_pagine=trovato.get("prime_pagine"),
                                dominio=domini_voce(trovato), chiusura_esercizio=chiusura_voce(trovato))
            esito = {"ticker": t, "pdf": bool(scelta), "motivi": (trovato.get("motivi") or [])[:5]}
            if trovato.get("accesso"):
                esito["accesso"] = trovato["accesso"]
            esiti.append(esito)
        except Exception as exc:
            esiti.append({"ticker": t, "pdf": False, "motivi": [motivo_eccezione(exc)]})
    return esiti


def scopri_giornaliero(store, *, escludi=(), oggi=None, consigliere_fn=None, pref_fn=None):
    """Passo del controllo giornaliero (e del giro dopo un'attivazione): siti dei profili ESEF
    fermi e dei titoli rimasti «senza fonte» all'ultima attivazione. Mai nella run del Consigliere."""
    from bellomberg.storage import filing_preferenze
    scadenza = time.monotonic() + TEMPO_GIRO_S
    esiti = scopri_profili(store, oggi=oggi, consigliere_fn=consigliere_fn, escludi=escludi, scadenza=scadenza)
    if any(e.get("rinviata") for e in esiti):
        return esiti
    # Profili creati dal sito dell'emittente (V8B 05/10): una relazione piu' recente sul sito (es. la
    # semestrale dopo l'annuale) diventa una nuova versione del profilo, verificata, prima dei run.
    for riga in store.list_profiles():
        if (not riga.get("enabled") or riga.get("ticker") in escludi
                or (riga.get("profile") or {}).get("origine_collegamento") != "sito_emittente"):
            continue
        motivo = _fermati(consigliere_fn, scadenza)
        if motivo:
            return esiti + [{"ticker": None, "rinviata": True, "motivi": [motivo]}]
        try:
            from bellomberg.market_data.filing_attivazione import aggiorna_dal_sito
            esiti.append(aggiorna_dal_sito(store, riga["ticker"]))
        except Exception as exc:  # un sito non ferma gli altri
            esiti.append({"ticker": riga["ticker"], "esito": "errore", "motivi": [motivo_eccezione(exc)]})
    try:
        pref = (pref_fn or filing_preferenze.carica)()
    except ValueError:
        return esiti  # preferenze illeggibili: solo i profili
    con_profilo = {r["ticker"] for r in store.list_profiles() if r.get("enabled")}
    senza = [t for t, e in sorted(filing_preferenze.esiti(pref).items())
             if (e or {}).get("esito") == "senza_fonte" and t not in con_profilo and t not in escludi]
    return esiti + scopri_senza_fonte(senza, oggi=oggi, consigliere_fn=consigliere_fn, scadenza=scadenza)
