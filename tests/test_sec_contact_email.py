"""B2 (02/09, pubblicazione): la SEC e filings.xbrl.org vogliono un contatto
nello User-Agent. Prima era l'email del PM cablata (sec_edgar.py:30 come
default, esef.py:29 letterale). Ora viene da SEC_CONTACT_EMAIL, letta A OGNI
CHIAMATA (non a import: cosi' .env e monkeypatch valgono senza reload); se
manca, errore DICHIARATO (regola 14/07), non un contatto finto.

Zero rete: `_headers()` solleva PRIMA che `requests.get` parta.
"""
import os

import pytest

from bellomberg.market_data import esef
from bellomberg.market_data import sec_edgar

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 06/09 (a2, criterio 3): i due test sul 13F DICHIARAVANO il negozio delle istituzioni.
# `get_13f_holdings` risolve lo slug PRIMA di controllare il contatto (sec_edgar.py:434), quindi
# in un clone senza `data/` sollevava `InvestitoreSconosciuto` — comportamento GIUSTO del codice,
# test sbagliato: era scritto su una macchina che il negozio ce l'ha. Erano gli unici due rossi
# della suite dentro il tree esportato. Un test non deve dipendere dai dati privati di qualcuno
# per provare una cosa che con quei dati non c'entra: qui il soggetto e' il contatto mancante.
# Lo slug e il CIK sono INVENTATI (e cosi' un nome vero esce da un test che viene pubblicato).
_ISTITUZIONI_FINTE = {"cik": {"esempiofondo": "0000000000"}, "origine": "test", "motivo": ""}


def _negozio_istituzioni_finto(monkeypatch):
    """Il negozio che il test si porta da solo, invece di pretenderlo dal disco."""
    monkeypatch.setattr(sec_edgar, "istituzioni_13f", lambda: _ISTITUZIONI_FINTE)


@pytest.mark.parametrize("modulo", [sec_edgar, esef])
def test_senza_contatto_errore_dichiarato(monkeypatch, modulo):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    with pytest.raises(modulo.ContattoMancante) as e:
        modulo._headers()
    assert "SEC_CONTACT_EMAIL" in str(e.value)


@pytest.mark.parametrize("modulo", [sec_edgar, esef])
def test_con_contatto_header_lo_contiene(monkeypatch, modulo):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "analista@example.com")
    h = modulo._headers()
    assert "analista@example.com" in h["User-Agent"]
    assert "gmail" not in h["User-Agent"]


def test_contatto_vuoto_vale_assente(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "   ")
    with pytest.raises(sec_edgar.ContattoMancante):
        sec_edgar._headers()


def test_lookup_cik_senza_contatto_dichiara_e_non_crasha(monkeypatch):
    """Cablaggio: la funzione PUBBLICA torna None col motivo, niente traceback,
    e NON chiama la rete (il contatto manca prima della richiesta)."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    chiamate = []
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: chiamate.append(a) or (_ for _ in ()).throw(AssertionError("rete chiamata")))
    sec_edgar._CIK_CACHE.clear()
    motivo = []
    assert sec_edgar.lookup_cik("AAPL", motivo=motivo) is None
    assert any("SEC_CONTACT_EMAIL" in r for r in motivo), motivo
    assert chiamate == []


def test_email_personale_sparita_dai_moduli():
    # `probe_purr_sec.py` e' uno strumento privato (i probe_* non escono nel repo pubblico):
    # dove non c'e' non si finge di averlo guardato, si dichiara quali file mancano (P2/T9)
    guardati, assenti = [], []
    for f in ("src/bellomberg/market_data/sec_edgar.py",
              "src/bellomberg/market_data/esef.py",
              "src/bellomberg/core/config.py",
              "src/bellomberg/market_data/sec_xbrl.py",
              "tools/diagnostics/probe_purr_sec.py"):
        if not os.path.exists(os.path.join(REPO, f)):
            assenti.append(f)
            continue
        guardati.append(f)
        testo = open(os.path.join(REPO, f), encoding="utf-8").read()
        assert "@gmail.com" not in testo, f
        assert "import HEADERS" not in testo and "sec_edgar.HEADERS" not in testo, f
    assert len(guardati) >= 4, "guardati solo %r, assenti %r" % (guardati, assenti)


# ---------------------------------------------------------------------------
# Dalla review (02/09): il verso POSITIVO del cablaggio, lo sweep statico, la
# regressione fuori dal diff (sec_xbrl importava HEADERS dentro un except muto),
# e la regola 14/07 END-TO-END: senza contatto e senza nessuno che raccolga il
# motivo, l'errore di CONFIGURAZIONE risale al chiamante (che lo dichiara), non
# diventa una lista vuota che sembra «nessun filing».
# ---------------------------------------------------------------------------
class _Risposta:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_con_contatto_lookup_cik_manda_lo_user_agent(monkeypatch):
    """Verso positivo: l'header di _headers() arriva DAVVERO a requests.get."""
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "analista@example.com")
    visti = []

    def _get(url, **k):
        visti.append(k.get("headers"))
        return _Risposta({"0": {"ticker": "AAPL", "cik_str": 320193}})

    monkeypatch.setattr(sec_edgar.requests, "get", _get)
    sec_edgar._CIK_CACHE.clear()
    assert sec_edgar.lookup_cik("AAPL") == "0000320193"
    assert visti and "analista@example.com" in visti[0]["User-Agent"], visti


def test_ogni_richiesta_dei_due_moduli_usa_headers_dinamici():
    """Ogni chiamata HTTP deve ricevere il contatto dinamico, anche se ne aggiungiamo."""
    import ast
    for f in ("src/bellomberg/market_data/sec_edgar.py", "src/bellomberg/market_data/esef.py"):
        with open(os.path.join(REPO, f), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        chiamate = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "requests" and n.func.attr in ("get", "head")]
        assert chiamate
        for chiamata in chiamate:
            header = next((k.value for k in chiamata.keywords if k.arg == "headers"), None)
            assert isinstance(header, ast.Call) and isinstance(header.func, ast.Name)
            assert header.func.id == "_headers", (f, chiamata.lineno)


def test_sec_xbrl_companyfacts_usa_il_contatto(monkeypatch, tmp_path):
    """Regressione trovata dalla review: sec_xbrl.py importava HEADERS (sparito)
    dentro un except muto → None → «companyfacts non disponibile» ANCHE col
    contatto presente, mascherato 7 giorni dalla cache su disco."""
    from bellomberg.market_data import sec_xbrl
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "analista@example.com")
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path))
    visti = []

    def _get(url, **k):
        visti.append(k.get("headers"))
        return _Risposta({"facts": {}})

    monkeypatch.setattr("requests.get", _get)   # sec_xbrl importa requests DENTRO la funzione
    assert sec_xbrl._fetch_companyfacts("0000320193") == {"facts": {}}
    assert visti and "analista@example.com" in visti[0]["User-Agent"], visti


def test_insider_senza_contatto_e_senza_motivo_risale_al_chiamante(monkeypatch):
    """Regola 14/07 end-to-end: chat_tools/agent_tools chiamano get_insider_trades
    SENZA `motivo`; una lista vuota sarebbe «nessun insider trade» MUTO. L'errore
    di configurazione deve risalire (i chiamanti hanno except che dichiarano)."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete chiamata")))
    sec_edgar._CIK_CACHE.clear()
    with pytest.raises(sec_edgar.ContattoMancante):
        sec_edgar.get_insider_trades("AAPL", days=30)


def test_lookup_cik_con_motivo_raccoglie_senza_sollevare(monkeypatch):
    """Il verso che RESTA: chi passa `motivo` (chat_tools:1097) riceve None + testo."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    sec_edgar._CIK_CACHE.clear()
    motivo = []
    assert sec_edgar.lookup_cik("AAPL", motivo=motivo) is None
    assert motivo and "SEC_CONTACT_EMAIL" in motivo[0]


def test_13f_senza_contatto_risale_al_chiamante(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    _negozio_istituzioni_finto(monkeypatch)
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete chiamata")))
    with pytest.raises(sec_edgar.ContattoMancante):
        sec_edgar.get_13f_holdings("esempiofondo")


def test_esef_senza_contatto_dice_cosa_fare(monkeypatch):
    """Finding 5: esef conservava solo type(e).__name__ («ContattoMancante»), che a
    uno sconosciuto non dice nulla. La nota deve portare l'istruzione."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    lei, nota = esef.resolve_lei("XYZQ.MI", company_name="Xyzq Spa")
    assert lei is None and "SEC_CONTACT_EMAIL" in nota, nota


def test_dispatcher_chat_13f_senza_contatto_dichiara_nel_payload(monkeypatch):
    """Il consumatore VERO (chat_tools.dispatch): niente «0 holdings» muto,
    un payload con `error` che dice cosa manca."""
    from bellomberg.agents import chat_tools
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    _negozio_istituzioni_finto(monkeypatch)
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete chiamata")))
    out = chat_tools.dispatch("get_13f_holdings", {"investor": "esempiofondo"})
    assert "SEC_CONTACT_EMAIL" in str(out.get("error", "")), out


def test_agent_tools_insider_senza_contatto_dichiara_nel_payload(monkeypatch):
    """Il consumatore VERO del comitato (agent_tools.tool_get_insider_trades)."""
    from bellomberg.agents import agent_tools
    from bellomberg.market_data import finnhub_news
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    monkeypatch.setattr(finnhub_news, "fetch_insider_trades", lambda *a, **k: [])
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete chiamata")))
    out = agent_tools.tool_get_insider_trades("AAPL", days=30)
    assert "SEC_CONTACT_EMAIL" in str(out.get("error", "")), out


def test_messaggio_dice_perche_e_che_basta_una_email_valida(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    with pytest.raises(sec_edgar.ContattoMancante) as e:
        sec_edgar._headers()
    assert "qualsiasi email valida" in str(e.value)
