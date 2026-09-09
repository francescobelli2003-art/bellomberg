"""I DEFAULT ESEGUIBILI: un tool che, chiamato a vuoto, sceglieva un titolo del PM.

Il difetto (04/09, Opus 5, lotto 139). `get_cef_lookthrough` dichiarava nello schema
`"default": "<un fondo del book>"` e NON aveva `required`: il modello poteva invocarlo
senza argomenti e ricevere NAV, sconto e holdings di una posizione vera. Il gemello
`get_dat_metrics` aveva il required ma lo stesso ripiego nel dispatcher, e le due firme
di libreria (`cef_lookthrough.get_lookthrough`, `dat_metrics.get_dat_metrics`) portavano
il titolo come valore predefinito del parametro.

Cura: argomento mancante = **KO dichiarato** (regola PM 14/07, niente ripieghi zitti),
mai un titolo scelto al posto di chi chiama.

PERCHE' QUESTE PROVE ASSERISCONO UNA PROPRIETA' E NON UN ELENCO. `tests/` sta nel
perimetro pubblico: una guardia che elenca i simboli vietati LI PUBBLICA (lezione T9,
02/09). Qui si misura «nessun parametro-titolo ha un default» e «a vuoto il tool
dichiara», che sono veri senza nominare una sola posizione.
"""
import inspect

from bellomberg.valuation import cef_lookthrough
from bellomberg.agents import chat_tools
from bellomberg.valuation import dat_metrics

CURATI = ("get_cef_lookthrough", "get_dat_metrics")
# I parametri che designano UN TITOLO: un default qui e' una scelta fatta al posto
# dell'utente. `market`/`benchmark`/`period` no: sono assi di mercato, non posizioni.
PARAMETRI_TITOLO = ("ticker", "symbol")


def _schema(nome):
    for t in chat_tools.TOOL_DEFINITIONS:
        if t.get("name") == nome:
            return t
    raise AssertionError("tool assente dal registro vivo: " + nome)


def test_nessun_parametro_titolo_ha_un_default_nel_registro_vivo():
    """La PROPRIETA' che vale per tutti i 51 schemi, non solo per i due curati: se un
    domani qualcuno rimette un default su un `ticker`, questa cade."""
    colpe = []
    for t in chat_tools.TOOL_DEFINITIONS:
        props = (t.get("input_schema") or {}).get("properties") or {}
        for nome, spec in props.items():
            if nome in PARAMETRI_TITOLO and isinstance(spec, dict) and "default" in spec:
                colpe.append("%s.%s" % (t.get("name"), nome))
    assert not colpe, "parametri-titolo con default: %s" % colpe


def test_i_due_tool_curati_pretendono_il_ticker():
    for nome in CURATI:
        req = (_schema(nome).get("input_schema") or {}).get("required") or []
        assert "ticker" in req, "%s: ticker non obbligatorio (%s)" % (nome, req)


def test_dispatch_senza_ticker_dichiara_il_ko():
    """Il ramo di guasto e' RAGGIUNGIBILE: si chiama davvero il dispatcher. La guardia
    sta prima dell'import del modulo a valle, quindi il KO non dipende da quel modulo."""
    for nome in CURATI:
        out = chat_tools.dispatch(nome, {})
        assert out.get("error"), (nome, out)
        assert "nessun default" in out["error"], (nome, out["error"])
        assert out.get("_source") == "dispatcher." + nome, (nome, out.get("_source"))


def test_dispatch_col_ticker_bianco_non_e_un_ticker():
    out = chat_tools.dispatch("get_cef_lookthrough", {"ticker": "   "})
    assert out.get("error") and "nessun default" in out["error"], out


def test_le_firme_di_libreria_non_hanno_piu_un_titolo_di_default():
    for fn in (cef_lookthrough.get_lookthrough, dat_metrics.get_dat_metrics):
        p = inspect.signature(fn).parameters["ticker"]
        assert p.default is inspect.Parameter.empty, "%s: default %r" % (fn.__name__, p.default)


def test_get_dat_metrics_vuoto_dichiara_invece_di_scegliere():
    out = dat_metrics.get_dat_metrics("")
    assert out.get("error"), out
    assert "nessun default" in out["error"], out["error"]


def test_il_timbro_del_dispatcher_dice_la_fonte_che_ha_risposto(monkeypatch):
    """Review 05/09 (lotto 2b): `_source` di get_dat_metrics era cablato («<- strategy.com/
    hypurrintel») per OGNI esito, anche per l'errore «non e' una DAT dichiarata» che nessuna
    fonte ha prodotto. Ora il timbro legge `nav_fonte` dal payload, e senza payload lo dice."""
    monkeypatch.setattr(dat_metrics, "get_dat_metrics",
                        lambda ticker=None, negozio=None: {"error": "non e' una DAT dichiarata"})
    out = chat_tools.dispatch("get_dat_metrics", {"ticker": "ZZZQ"})
    assert "nessuna fonte" in out["_source"] and "strategy" not in out["_source"]
    monkeypatch.setattr(dat_metrics, "get_dat_metrics",
                        lambda ticker=None, negozio=None: {"ticker": "ZZZQ", "nav_fonte": "fonte-finta.example"})
    out2 = chat_tools.dispatch("get_dat_metrics", {"ticker": "ZZZQ"})
    assert "fonte-finta.example" in out2["_source"]
