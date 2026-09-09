"""I REGISTRI DEI TOOL senza il book: cio' che le description PROMETTONO e' cablato davvero.

Il lotto (05/09, chat 8c, criterio (1) lotto 3, MASTER §9-quinsexagies): le description e gli
schemi che partono verso l'API (`chat_tools.TOOL_DEFINITIONS`, registro vivo, e
`agent_tools.TOOLS_SCHEMA`, registro legacy) dicevano la COPERTURA di un tool con l'elenco
delle posizioni del PM. Ora la dicono come COMPORTAMENTO («copre i registrati; per gli altri
KO dichiarato»), e ogni comportamento scritto li' deve esistere nel codice, sennò lo schema
promette cio' che il tool non fa (lezione «testare l'helper non e' testare il cablaggio»).

PERCHE' PROPRIETA' E NON ELENCHI. `tests/` sta nel perimetro pubblico: una guardia che elenca
i simboli vietati li pubblica (lezione T9, 02/09). La MISURA della bonifica e' il cancello
(`scripts/verifica_pubblico.py`, controlli 9 e 10); qui si prova che le promesse reggono, con
slug e ticker inventati.
"""
import inspect

import pytest
import requests

from bellomberg.agents import agent_tools
from bellomberg.agents import chat_tools
from bellomberg.market_data import sec_edgar

# I parametri che designano UN TITOLO o un asset: un default li' e' una scelta fatta al posto
# di chi chiama (stessa proprieta' di tests/test_default_eseguibili.py sul registro vivo,
# estesa al registro legacy e all'asset del DEX).
PARAMETRI_TITOLO = ("ticker", "symbol", "focus_asset")


def _vivo(nome):
    for t in chat_tools.TOOL_DEFINITIONS:
        if t.get("name") == nome:
            return t
    raise AssertionError("tool assente dal registro vivo: " + nome)


def _legacy(nome):
    for t in agent_tools.TOOLS_SCHEMA:
        if t.get("name") == nome:
            return t
    raise AssertionError("tool assente dal registro legacy: " + nome)


@pytest.fixture(autouse=True)
def _negozio_istituzioni(monkeypatch, tmp_path):
    """Gli slug 13F sono un DATO del negozio privato (istituzioni.json): iniettato con slug
    inventati, cosi' la batteria non dipende dal book ne' dal negozio vero (05/09, lotto 5)."""
    from bellomberg.storage import negozi_privati
    p = tmp_path / "istituzioni.json"
    p.write_text('{"cik": {"esempio_capital": "0000000001", "altro_fondo": "0000000002"}}',
                 encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ISTITUZIONI", str(p))


def _niente_rete(monkeypatch):
    def _no(*a, **k):
        raise AssertionError("rete chiamata")
    monkeypatch.setattr(requests, "get", _no)
    monkeypatch.setattr(requests, "post", _no)


# --- 13F: uno slug sconosciuto e' un KO che elenca, non una lista vuota zitta ---------------

def test_13f_slug_sconosciuto_solleva_un_ko_che_elenca_gli_slug(monkeypatch):
    """Prima: `return []` con un `_log` che nessun modello legge, e il dispatcher rispondeva
    «count: 0» come se fosse una misura (regola 14/07: il buco si DICHIARA)."""
    _niente_rete(monkeypatch)
    with pytest.raises(sec_edgar.InvestitoreSconosciuto) as ei:
        sec_edgar.get_13f_holdings("slug_che_non_esiste")
    msg = str(ei.value)
    assert "sconosciuto" in msg and "slug_che_non_esiste" in msg, msg
    for slug in sec_edgar.istituzioni_13f()["cik"]:
        assert slug in msg, "lo slug %s non e' nell'elenco del KO: %s" % (slug, msg)


def test_13f_slug_sconosciuto_e_un_valueerror_per_i_chiamanti_generici(monkeypatch):
    _niente_rete(monkeypatch)
    with pytest.raises(ValueError):
        sec_edgar.get_13f_holdings("slug_che_non_esiste")


def test_dispatcher_13f_slug_sconosciuto_dichiara_il_ko_nel_payload(monkeypatch):
    """Il consumatore VERO (chat_tools.dispatch): un payload con `error` che elenca gli slug,
    mai «0 holdings»."""
    _niente_rete(monkeypatch)
    out = chat_tools.dispatch("get_13f_holdings", {"investor": "slug_che_non_esiste"})
    assert out.get("error"), out
    assert "sconosciuto" in out["error"], out["error"]
    for slug in sec_edgar.istituzioni_13f()["cik"]:
        assert slug in out["error"], (slug, out["error"])
    assert "count" not in out, "il KO non deve travestirsi da conteggio: %r" % (out,)
    assert out.get("_source") == "dispatcher.get_13f_holdings", out.get("_source")


def test_la_description_13f_promette_il_ko_che_elenca_e_non_un_elenco_di_gestori():
    """La description del registro vivo descrive il COMPORTAMENTO (slug sconosciuto = KO
    con l'elenco), non la lista dei gestori seguiti dal PM."""
    d = _vivo("get_13f_holdings")["description"]
    assert "sconosciuto" in d and "elenca" in d, d
    p = _vivo("get_13f_holdings")["input_schema"]["properties"]["investor"]["description"]
    assert "sconosciuto" in p, p


# --- il registro legacy: nessun parametro-titolo ha un default ------------------------------

def test_nessun_parametro_titolo_ha_un_default_nel_registro_legacy():
    """Stessa proprieta' del registro vivo (test_default_eseguibili), sul legacy: un default
    su `ticker`/`symbol`/`focus_asset` e' un titolo scelto al posto di chi chiama."""
    colpe = []
    for t in agent_tools.TOOLS_SCHEMA:
        props = (t.get("input_schema") or {}).get("properties") or {}
        for nome, spec in props.items():
            if nome in PARAMETRI_TITOLO and isinstance(spec, dict) and "default" in spec:
                colpe.append("%s.%s" % (t.get("name"), nome))
    assert not colpe, "parametri-titolo con default nel registro legacy: %s" % colpe


def test_la_firma_legacy_del_dex_non_sceglie_un_asset_al_posto_tuo():
    p = inspect.signature(agent_tools.tool_get_hyperliquid_intel).parameters["focus_asset"]
    assert not isinstance(p.default, str), "default %r: e' un asset scelto al posto tuo" % (p.default,)


# --- il tool del DEX senza asset: top perps DICHIARATI, non un focus scelto zitto ------------

class _Risposta:
    def __init__(self, corpo):
        self._corpo = corpo

    def raise_for_status(self):
        return None

    def json(self):
        return self._corpo


def _dex_finto(monkeypatch):
    universo = [{"name": "AAA"}, {"name": "BBB"}]
    ctx = {"markPx": "10", "prevDayPx": "9", "funding": "0.0001", "dayNtlVlm": "1000000",
           "openInterest": "5000", "premium": "0.001"}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Risposta([{"universe": universo}, [ctx, ctx]]))
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rete GET")))


def test_dex_senza_asset_dichiara_nessun_focus_e_da_i_top_perps(monkeypatch):
    _dex_finto(monkeypatch)
    out = agent_tools.tool_get_hyperliquid_intel(focus_asset=None, builder_dexs=False)
    assert not out.get("error"), out
    assert out.get("focus") is None, out.get("focus")
    assert out.get("focus_asset_detail") is None
    assert "nessun asset" in str(out.get("focus_note", "")).lower(), out.get("focus_note")
    assert [r["asset"] for r in out["top_10_perps_by_oi"]] == ["AAA", "BBB"]


def test_dex_con_asset_non_quotato_lo_dichiara_invece_di_tacere(monkeypatch):
    _dex_finto(monkeypatch)
    out = agent_tools.tool_get_hyperliquid_intel(focus_asset="ZZZQ", builder_dexs=False)
    assert out.get("focus_asset_detail") is None
    assert "ZZZQ" in str(out.get("focus_note", "")) and "non quotato" in str(out.get("focus_note", "")), out.get("focus_note")


def test_dex_con_asset_quotato_lo_dettaglia(monkeypatch):
    _dex_finto(monkeypatch)
    out = agent_tools.tool_get_hyperliquid_intel(focus_asset="bbb", builder_dexs=False)
    assert out["focus_asset_detail"]["asset"] == "BBB"
    assert "focus_note" not in out, out.get("focus_note")


def test_il_suggerimento_esterno_del_dex_rimanda_alle_dat_registrate(monkeypatch):
    """Il payload del tool legacy portava due posizioni del PM per nome nel suggerimento
    «per mNAV usa get_dat_metrics»: ora rimanda alla REGOLA (DAT registrate, altre = KO)."""
    _dex_finto(monkeypatch)
    out = agent_tools.tool_get_hyperliquid_intel(focus_asset=None, builder_dexs=False)
    hint = out.get("external_data_sources_hint", "")
    assert "get_dat_metrics" in hint and "registrat" in hint and "KO" in hint, hint


# --- coperture come COMPORTAMENTO, e il comportamento esiste ---------------------------------

def test_le_description_di_copertura_dichiarano_il_comportamento():
    """Le quattro description che dicevano «oggi coperto: <posizione>» dicono ora cosa fa il
    tool con un ticker non registrato."""
    assert "KO dichiarato" in _vivo("get_dat_metrics")["description"]
    assert "nessun default" in _vivo("get_dat_metrics")["input_schema"]["properties"]["ticker"]["description"]
    assert "errore dichiarato" in _vivo("get_cef_nav")["description"]
    assert "nessun default" in _vivo("get_cef_nav")["input_schema"]["properties"]["ticker"]["description"]
    assert "errore dichiarato" in _vivo("get_cef_lookthrough")["description"]
    assert "registrat" in _vivo("get_valuation")["description"].lower()
    assert "`kind`" in _vivo("get_valuation")["input_schema"]["properties"]["nav_target"]["description"]


def test_dat_non_registrata_e_fondo_non_configurato_sono_ko_prima_della_rete(monkeypatch):
    """Il cablaggio della promessa: i tre tool rifiutano un ticker inventato SENZA toccare la
    rete (la guardia sta prima della fetch)."""
    _niente_rete(monkeypatch)
    for nome, arg in (("get_dat_metrics", {"ticker": "ZZZQ"}),
                      ("get_cef_nav", {"ticker": "ZZZQ.L"}),
                      ("get_cef_lookthrough", {"ticker": "ZZZQ.L"})):
        out = chat_tools.dispatch(nome, arg)
        # il dispatcher avvolge con `_stamp`: l'errore del motore sta in data.error, quello
        # della guardia del dispatcher in error. Entrambi sono KO dichiarati, visibili al modello.
        dati = out.get("data") if isinstance(out.get("data"), dict) else {}
        err = out.get("error") or dati.get("error")
        assert err, (nome, out)
        assert "ZZZQ" in str(err), (nome, err)


def test_lo_schema_legacy_del_dex_descrive_il_focus_come_opzionale_senza_default():
    spec = _legacy("get_hyperliquid_intel")["input_schema"]["properties"]["focus_asset"]
    assert "default" not in spec, spec
    assert "nessun default" in spec["description"], spec["description"]
