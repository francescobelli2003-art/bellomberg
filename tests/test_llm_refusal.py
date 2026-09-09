"""Test OFFLINE del lotto PRE-V6 26/07 (Opus 5): RIFIUTO DEL MODELLO DICHIARATO.

Classe di guasto coperta: dallo swap (48) il comitato gira su claude-opus-5 e la
fascia sintesi su claude-sonnet-5, che hanno i safeguard elevati. Un rifiuto NON
solleva: torna HTTP 200 con stop_reason="refusal" e content vuoto. Prima di questo
lotto quel caso diventava, a valle, un vuoto SENZA CAUSA:
  - reflection  -> return "" ZITTO (nessun log, nessuna riga di costo dichiarata)
  - action_table-> errore con la causa SBAGLIATA ("tool_choice ignorato?")
Entrambi violano la regola PM 14/07. Qui si verifica che ora la causa si dichiara.

Zero rete: client Anthropic finto (idioma della suite), scorekeeper stubbato,
percorso delle lezioni su tmp_path.
"""
import pytest

from bellomberg.core import llm_client
from bellomberg.core import llm_refusal


# ---------------------------------------------------------------- doppi di test
class _FakeUsage:
    input_tokens = 12
    output_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _StopDetails:
    type = "refusal"
    category = "cyber"
    explanation = "request declined by safety classifier"


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _ToolUseBlock:
    type = "tool_use"
    name = "emit_action_table"

    def __init__(self, rows):
        self.id = "toolu_fake"
        self.input = {"rows": rows, "table_found": True}


class _Resp:
    def __init__(self, stop_reason="end_turn", content=None, stop_details=None):
        self.stop_reason = stop_reason
        self.content = content if content is not None else []
        self.stop_details = stop_details
        self.usage = _FakeUsage()


def _fake_client_factory(resp):
    class _Messages:
        def create(self, **kw):
            return resp

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    return _Client


# ------------------------------------------------------------------- il modulo
def test_risposta_normale_non_e_un_rifiuto():
    for stop in ("end_turn", "tool_use", "max_tokens", "stop_sequence", None):
        assert llm_refusal.refusal_reason(_Resp(stop_reason=stop)) is None


def test_rifiuto_dichiara_tag_e_categoria():
    msg = llm_refusal.refusal_reason(_Resp("refusal", stop_details=_StopDetails()), "CAPO")
    assert llm_refusal.REFUSAL_TAG in msg
    assert "cyber" in msg
    assert "[CAPO]" in msg
    # la frase deve escludere ESPLICITAMENTE le due diagnosi sbagliate
    assert "NON e' un errore di rete" in msg
    assert "NON e' un output vuoto" in msg


def test_stop_details_come_dict_e_come_assente():
    # SDK che non tipizza ancora il campo: arriva un dict
    d = llm_refusal.refusal_reason(_Resp("refusal", stop_details={"category": "bio"}))
    assert "bio" in d
    # campo del tutto assente: si dichiara "n.d.", non si inventa e non si esplode
    n = llm_refusal.refusal_reason(_Resp("refusal"))
    assert "n.d." in n


def test_has_partial_text():
    assert llm_refusal.has_partial_text(_Resp("refusal", [_TextBlock("meta' analisi")])) is True
    assert llm_refusal.has_partial_text(_Resp("refusal", [])) is False
    assert llm_refusal.has_partial_text(_Resp("refusal", [_TextBlock("   ")])) is False


# --------------------------------------------------- call site: action_table (Sonnet 5)
def test_action_table_rifiuto_dichiara_la_causa_giusta(monkeypatch):
    import bellomberg.agents.action_table_extract as ate
    monkeypatch.setattr(llm_client, "OpenRouterClient",
                        _fake_client_factory(_Resp("refusal", stop_details=_StopDetails())))
    u = {}
    out = ate.extract_rows_structured("## ACTION TABLE\n| ADD | X | 1k | ora | ALTA |", u)
    assert llm_refusal.REFUSAL_TAG in out["error"]
    # la vecchia diagnosi era sbagliata: lo schema del tool non c'entra nulla
    assert "tool_choice ignorato" not in out["error"]
    assert u["status"] == "refusal"


def test_action_table_percorso_normale_intatto(monkeypatch):
    """Guardia di non-regressione: il ramo nuovo non deve toccare l'estrazione vera."""
    import bellomberg.agents.action_table_extract as ate
    rows = [{"action": "add", "ticker": "gamma.mi", "size_raw": "5.000EUR",
             "timing": "questa settimana", "confidence": "ALTA"}]
    monkeypatch.setattr(llm_client, "OpenRouterClient",
                        _fake_client_factory(_Resp("tool_use", [_ToolUseBlock(rows)])))
    u = {}
    out = ate.extract_rows_structured("## ACTION TABLE\n| ADD | GAMMA.MI |", u)
    assert "error" not in out
    assert out["rows"][0]["ticker"] == "GAMMA.MI"   # normalizzazione di sempre
    assert out["rows"][0]["action"] == "ADD"
    assert u["status"] == "ok"


# ----------------------------------------------------- call site: reflection (Sonnet 5)
def test_reflection_rifiuto_non_e_piu_muto(monkeypatch, tmp_path, capsys):
    from bellomberg.agents import reflection
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(reflection, "LESSONS_PATH", str(tmp_path / "lessons.json"))
    monkeypatch.setattr(scorekeeper, "compute_scorecard",
                        lambda *a, **k: {"overall": {"n": 74, "hit_rate_pct": 52.7}})
    monkeypatch.setattr(scorekeeper, "format_track_record_for_capo",
                        lambda sc, max_chars=2000: "TRACK RECORD: 74 call, hit 52,7%")
    monkeypatch.setattr(llm_client, "OpenRouterClient",
                        _fake_client_factory(_Resp("refusal", stop_details=_StopDetails())))
    u = {}
    lesson = reflection.generate_lesson("## ACTION TABLE\n| ADD | X |", memo_id=99, usage_out=u)
    # la lezione NON si scrive col testo del rifiuto (finirebbe nel priming successivo)
    assert lesson == ""
    # ...ma il buco non e' piu' zitto: log della run + status della riga di costo
    assert llm_refusal.REFUSAL_TAG in capsys.readouterr().out
    assert u["status"] == "refusal"
    assert u["api_calls"] == 1   # la chiamata E' stata pagata: resta dichiarata


def test_reflection_percorso_normale_intatto(monkeypatch, tmp_path):
    """Guardia di non-regressione: con risposta buona la lezione esce e si salva."""
    from bellomberg.agents import reflection
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(reflection, "LESSONS_PATH", str(tmp_path / "lessons.json"))
    monkeypatch.setattr(scorekeeper, "compute_scorecard",
                        lambda *a, **k: {"overall": {"n": 74, "hit_rate_pct": 52.7}})
    monkeypatch.setattr(scorekeeper, "format_track_record_for_capo",
                        lambda sc, max_chars=2000: "TRACK RECORD: 74 call, hit 52,7%")
    monkeypatch.setattr(llm_client, "OpenRouterClient",
                        _fake_client_factory(_Resp("end_turn", [_TextBlock("1. Lezione vera.")])))
    u = {}
    lesson = reflection.generate_lesson("## ACTION TABLE\n| ADD | X |", memo_id=99, usage_out=u)
    assert "Lezione vera" in lesson
    assert u["status"] == "ok"
