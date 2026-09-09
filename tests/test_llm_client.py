"""llm_client — OpenRouter con la superficie SDK Anthropic che Bellomberg usa (05/09, ordine PM:
«invece che la apikey di Anthropic quella di OpenRouter, con diversi modelli per funzione»).

Suite OFFLINE: ogni chiamata HTTP passa da un httpx.MockTransport che cattura il corpo della
richiesta e risponde con un JSON o un flusso SSE finti. Zero rete, zero DB.

Cosa e' pinnato qui (spec docs/superpowers/specs/2026-09-05-openrouter-migrazione-design.md):
  - la RISOLUZIONE dei modelli dal .env con la precedenza scritta dal PM (override per
    agente/desk -> variabile base), e variabile base assente = errore col NOME (regola 14/07);
  - la TRADUZIONE della richiesta (system/messages/tools/tool_choice/thinking/cache_control)
    nel formato chat/completions, e della risposta nella forma SDK che i call site leggono
    (content a blocchi, stop_reason, usage.input_tokens... con la semantica Anthropic);
  - lo STREAMING (eventi content_block_* per la chat, get_final_message per il Capo);
  - errori HTTP con status_code (i retry a mano dei call site li guardano) e retry del client.
"""
import asyncio
import json
import os

import httpx
import pytest

from bellomberg.core import llm_client


# ---------------------------------------------------------------- utilita' di prova
def _env_modelli(monkeypatch, **extra):
    """Un .env finto COMPLETO (tutte le variabili base), piu' gli override passati."""
    base = {
        "OPENROUTER_API_KEY": "sk-or-finta",
        "CHAT_MODEL": "z-ai/glm-finto-flash",
        "CHAT_MAX_TOKENS": "12000",
        "CONSIGLIERE_MODEL": "z-ai/glm-finto-flash",
        "CONSIGLIERE_R0_MODEL": "z-ai/glm-finto-r0",
        "CAPO_MODEL": "anthropic/claude-finto",
        "RED_TEAM_MODEL": "meta/finto-spark",
        "REFLECTION_MODEL": "deepseek/finto-flash",
        "ACTION_EXTRACTOR_MODEL": "deepseek/finto-flash:exacto",
        "BRIEFING_MODEL": "z-ai/glm-finto-flash",
        "NEWS_CLASSIFIER_MODEL": "deepseek/finto-flash",
    }
    # gli override del .env VERO (load_dotenv) non devono entrare: vuoto = assente
    for k in ("CHAT_CAPO_MODEL", "CHAT_MACRO_MODEL", "CHAT_QUANT_MODEL", "CHAT_OPTIONS_MODEL",
              "CHAT_FUNDAMENTALS_MODEL", "CHAT_CRYPTO_MODEL", "CHAT_EVENTDESK_MODEL",
              "CHAT_POLITICS_MODEL", "CHAT_NEWS_MODEL", "CONSIGLIERE_MACRO_MODEL",
              "CONSIGLIERE_QUANT_MODEL", "CONSIGLIERE_OPTIONS_MODEL",
              "CONSIGLIERE_FUNDAMENTALS_MODEL", "CONSIGLIERE_CRYPTO_MODEL",
              "CONSIGLIERE_EVENTDESK_MODEL"):
        base.setdefault(k, "")
    base.update(extra)
    for k, v in base.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def _risposta_json(content="Testo finto.", tool_calls=None, finish_reason="stop",
                   usage=None, refusal=None, reasoning=None, extra_msg=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if refusal is not None:
        msg["refusal"] = refusal
    if reasoning is not None:
        msg["reasoning"] = reasoning
    if extra_msg:
        msg.update(extra_msg)
    corpo = {
        "id": "gen-finto-1", "model": "z-ai/glm-finto-flash", "provider": "ProviderFinto",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
    }
    if usage is not None:
        corpo["usage"] = usage
    return corpo


def _usage_pieno():
    return {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            "cost": 0.00123,
            "prompt_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 10},
            "completion_tokens_details": {"reasoning_tokens": 20}}


class _Trasporto:
    """MockTransport che cattura i corpi delle richieste e risponde in sequenza."""

    def __init__(self, risposte):
        self.richieste = []
        self.headers = []
        self._risposte = list(risposte)

    def __call__(self, request):
        self.richieste.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        r = self._risposte.pop(0) if len(self._risposte) > 1 else self._risposte[0]
        if callable(r):
            return r(request)
        return r

    def mock(self):
        return httpx.MockTransport(self)


def _sse(*chunk):
    """Corpo SSE come lo manda OpenRouter: commento iniziale, data: ..., [DONE]."""
    righe = [": OPENROUTER PROCESSING", ""]
    for c in chunk:
        righe.append("data: " + json.dumps(c))
        righe.append("")
    righe += ["data: [DONE]", ""]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content="\n".join(righe).encode("utf-8"))


def _chunk(delta=None, finish_reason=None, usage=None, error=None):
    c = {"id": "gen-finto-s", "model": "z-ai/glm-finto-flash", "provider": "ProviderFinto",
         "choices": [{"index": 0, "delta": delta if delta is not None else {},
                      "finish_reason": finish_reason}]}
    if usage is not None:
        c["usage"] = usage
    if error is not None:
        c["error"] = error
    return c


def _client(monkeypatch, trasporto, **kw):
    _env_modelli(monkeypatch)
    return llm_client.OpenRouterClient(trasporto=trasporto.mock(), **kw)


# ================================================================ 1. risoluzione dei modelli
def test_modello_chat_override_per_agente_poi_variabile_base(monkeypatch):
    _env_modelli(monkeypatch, CHAT_QUANT_MODEL="z-ai/glm-finto-quant")
    assert llm_client.modello("chat", "quant") == "z-ai/glm-finto-quant"
    assert llm_client.modello("chat", "macro") == "z-ai/glm-finto-flash"   # nessun override
    assert llm_client.modello("chat") == "z-ai/glm-finto-flash"


def test_modello_chat_variabile_base_assente_e_un_errore_col_nome(monkeypatch):
    _env_modelli(monkeypatch, CHAT_MODEL=None)
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as e:
        llm_client.modello("chat", "macro")
    assert "CHAT_MODEL" in str(e.value)


def test_modello_variabile_vuota_vale_assente(monkeypatch):
    _env_modelli(monkeypatch, CAPO_MODEL="   ")
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as e:
        llm_client.modello("capo")
    assert "CAPO_MODEL" in str(e.value)


def test_modello_consigliere_r0_r1_r2(monkeypatch):
    _env_modelli(monkeypatch, CONSIGLIERE_QUANT_MODEL="z-ai/glm-finto-pieno")
    assert llm_client.modello("consigliere", "quant", round_n=0) == "z-ai/glm-finto-r0"
    assert llm_client.modello("consigliere", "quant", round_n=1) == "z-ai/glm-finto-pieno"
    assert llm_client.modello("consigliere", "quant", round_n=2) == "z-ai/glm-finto-pieno"
    assert llm_client.modello("consigliere", "macro", round_n=1) == "z-ai/glm-finto-flash"


def test_modello_consigliere_r0_assente_non_ripiega_sul_pieno(monkeypatch):
    _env_modelli(monkeypatch, CONSIGLIERE_R0_MODEL=None)
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as e:
        llm_client.modello("consigliere", "quant", round_n=0)
    assert "CONSIGLIERE_R0_MODEL" in str(e.value)


def test_modello_funzioni_singole(monkeypatch):
    _env_modelli(monkeypatch)
    assert llm_client.modello("capo") == "anthropic/claude-finto"
    assert llm_client.modello("red_team") == "meta/finto-spark"
    assert llm_client.modello("reflection") == "deepseek/finto-flash"
    assert llm_client.modello("action_extractor") == "deepseek/finto-flash:exacto"
    assert llm_client.modello("briefing") == "z-ai/glm-finto-flash"
    assert llm_client.modello("news_classifier") == "deepseek/finto-flash"
    with pytest.raises(ValueError):
        llm_client.modello("funzione-che-non-esiste")


def test_chat_max_tokens_intero_da_env(monkeypatch):
    _env_modelli(monkeypatch)
    assert llm_client.chat_max_tokens() == 12000
    _env_modelli(monkeypatch, CHAT_MAX_TOKENS=None)
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as e:
        llm_client.chat_max_tokens()
    assert "CHAT_MAX_TOKENS" in str(e.value)
    _env_modelli(monkeypatch, CHAT_MAX_TOKENS="dodicimila")
    with pytest.raises(llm_client.ConfigurazioneLLMMancante):
        llm_client.chat_max_tokens()


def test_variabili_mancanti_elenca_solo_le_assenti(monkeypatch):
    _env_modelli(monkeypatch)
    assert llm_client.variabili_mancanti() == []
    _env_modelli(monkeypatch, OPENROUTER_API_KEY=None, RED_TEAM_MODEL="")
    assert llm_client.variabili_mancanti() == ["OPENROUTER_API_KEY", "RED_TEAM_MODEL"]


def test_chiave_assente_ferma_il_client_alla_costruzione(monkeypatch):
    _env_modelli(monkeypatch, OPENROUTER_API_KEY=None)
    with pytest.raises(llm_client.ConfigurazioneLLMMancante) as e:
        llm_client.OpenRouterClient()
    assert "OPENROUTER_API_KEY" in str(e.value)


# ================================================================ 2. traduzione della richiesta
def test_richiesta_system_stringa_messaggi_e_intestazioni(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t, timeout=123.0, max_retries=0)
    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=800, system="Sei il Capo.",
                      messages=[{"role": "user", "content": "Ciao"}])
    corpo = t.richieste[0]
    assert corpo["model"] == "z-ai/glm-finto-flash"
    assert corpo["max_tokens"] == 800
    assert corpo["messages"] == [{"role": "system", "content": "Sei il Capo."},
                                 {"role": "user", "content": "Ciao"}]
    assert "tools" not in corpo and "tool_choice" not in corpo and "reasoning" not in corpo
    assert "stream" not in corpo or corpo["stream"] is False
    # parametri che oggi NON partono verso Anthropic non devono comparire (pre-flight 26/07)
    for vietato in ("temperature", "top_p", "top_k", "stop"):
        assert vietato not in corpo
    h = t.headers[0]
    assert h["authorization"] == "Bearer sk-or-finta"
    assert h["x-title"] == "Bellomberg"
    assert "http-referer" in h
    assert c.timeout == 123.0 and c.max_retries == 0


def test_richiesta_tools_schema_e_tool_choice(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    tool = {"name": "get_x", "description": "prende x",
            "input_schema": {"type": "object", "properties": {"t": {"type": "string"}},
                             "required": ["t"]},
            "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                      messages=[{"role": "user", "content": "u"}], tools=[tool],
                      tool_choice={"type": "none"})
    corpo = t.richieste[0]
    assert corpo["tools"] == [{"type": "function", "function": {
        "name": "get_x", "description": "prende x",
        "parameters": {"type": "object", "properties": {"t": {"type": "string"}},
                       "required": ["t"]}}}]
    assert corpo["tool_choice"] == "none"

    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                      messages=[{"role": "user", "content": "u"}], tools=[tool],
                      tool_choice={"type": "tool", "name": "get_x"})
    assert t.richieste[1]["tool_choice"] == {"type": "function", "function": {"name": "get_x"}}

    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                      messages=[{"role": "user", "content": "u"}], tools=[tool],
                      tool_choice={"type": "any"})
    assert t.richieste[2]["tool_choice"] == "required"


def test_richiesta_thinking_diventa_reasoning(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    base = dict(model="deepseek/finto-flash", max_tokens=10, system="s",
                messages=[{"role": "user", "content": "u"}])
    c.messages.create(thinking={"type": "adaptive"}, **base)
    assert t.richieste[0]["reasoning"] == llm_client.REASONING_ADAPTIVE == {"effort": "medium"}
    c.messages.create(thinking={"type": "disabled"}, **base)
    assert t.richieste[1]["reasoning"] == llm_client.REASONING_DISABLED == {"enabled": False}
    c.messages.create(thinking={"type": "enabled", "budget_tokens": 2048}, **base)
    assert t.richieste[2]["reasoning"] == {"max_tokens": 2048}
    c.messages.create(**base)
    assert "reasoning" not in t.richieste[3]


def test_adaptive_su_glm_omette_il_parametro(monkeypatch):
    """Misurato 05/09 (tetto 800): su z-ai/glm-5.3-flash QUALUNQUE `effort` azzera il
    ragionamento (0 token), solo l'assenza del parametro lo accende (522 token): «adaptive»
    su uno slug `z-ai/` = nessun campo reasoning (il tetto resta max_tokens)."""
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                      messages=[{"role": "user", "content": "u"}], thinking={"type": "adaptive"})
    assert "reasoning" not in t.richieste[0]


def test_ragionamento_obbligatorio_ritenta_con_minimal_e_lo_dichiara(monkeypatch):
    """Misurato 05/09: GLM e Gemini rispondono 400 «Reasoning is mandatory for this endpoint
    and cannot be disabled» a {"enabled": false}; con `effort: minimal` ragionano 0 token.
    La cura: UN retry con minimal, dichiarato in usage.reasoning_forzato e nel log; il
    modello viene ricordato e la chiamata dopo parte gia' con minimal (una richiesta sola)."""
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()
    rifiuto = httpx.Response(400, json={"error": {"code": 400, "message":
        "Reasoning is mandatory for this endpoint and cannot be disabled."}})
    t = _Trasporto([rifiuto, httpx.Response(200, json=_risposta_json(usage=_usage_pieno())),
                    httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t, max_retries=0)
    base = dict(model="google/gemini-finto", max_tokens=10, system="s",
                messages=[{"role": "user", "content": "u"}], thinking={"type": "disabled"})
    r = c.messages.create(**base)
    assert len(t.richieste) == 2
    assert t.richieste[0]["reasoning"] == {"enabled": False}
    assert t.richieste[1]["reasoning"] == llm_client.REASONING_MINIMO == {"effort": "minimal"}
    assert r.usage.reasoning_forzato == "minimal"
    assert "google/gemini-finto" in llm_client.RAGIONAMENTO_OBBLIGATORIO
    # seconda chiamata: parte gia' con minimal, una richiesta sola, sempre dichiarato
    r2 = c.messages.create(**base)
    assert len(t.richieste) == 3 and t.richieste[2]["reasoning"] == {"effort": "minimal"}
    assert r2.usage.reasoning_forzato == "minimal"
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()


def test_altri_400_non_vengono_ritentati_con_minimal(monkeypatch):
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()
    t = _Trasporto([httpx.Response(400, json={"error": {"code": 400, "message": "bad prompt"}})])
    c = _client(monkeypatch, t, max_retries=0)
    with pytest.raises(llm_client.APIStatusError):
        c.messages.create(model="google/gemini-finto", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}], thinking={"type": "disabled"})
    assert len(t.richieste) == 1
    assert not llm_client.RAGIONAMENTO_OBBLIGATORIO


def test_ragionamento_obbligatorio_anche_in_streaming(monkeypatch):
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()
    rifiuto = httpx.Response(400, json={"error": {"code": 400, "message":
        "Reasoning is mandatory for this endpoint and cannot be disabled."}})
    t = _Trasporto([rifiuto, _sse(_chunk({"content": "ok"}),
                                  _chunk({}, finish_reason="stop", usage=_usage_pieno()))])
    c = _client(monkeypatch, t, max_retries=0)
    with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                           messages=[{"role": "user", "content": "u"}],
                           thinking={"type": "disabled"}) as s:
        r = s.get_final_message()
    assert len(t.richieste) == 2 and t.richieste[1]["reasoning"] == {"effort": "minimal"}
    assert r.usage.reasoning_forzato == "minimal" and r.content[0].text == "ok"
    llm_client.RAGIONAMENTO_OBBLIGATORIO.clear()


def test_richiesta_cache_control_conservato_solo_per_anthropic(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    cc = {"type": "ephemeral", "ttl": "1h"}
    sistema = [{"type": "text", "text": "Sei un desk.", "cache_control": cc}]
    msgs = [{"role": "user", "content": [{"type": "text", "text": "fatti", "cache_control": cc}]}]
    tool = {"name": "get_x", "description": "d", "input_schema": {"type": "object", "properties": {}},
            "cache_control": cc}
    c.messages.create(model="anthropic/claude-finto", max_tokens=10, system=sistema,
                      messages=msgs, tools=[tool],
                      extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"})
    corpo = t.richieste[0]
    assert corpo["messages"][0] == {"role": "system",
                                    "content": [{"type": "text", "text": "Sei un desk.",
                                                 "cache_control": cc}]}
    assert corpo["messages"][1] == {"role": "user",
                                    "content": [{"type": "text", "text": "fatti",
                                                 "cache_control": cc}]}
    # sui tools il cache_control non ha equivalente: si toglie anche per Anthropic
    assert "cache_control" not in json.dumps(corpo["tools"])
    # l'header beta Anthropic NON parte verso OpenRouter
    assert "anthropic-beta" not in t.headers[0]

    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system=sistema,
                      messages=msgs, tools=[tool])
    corpo = t.richieste[1]
    assert corpo["messages"][0] == {"role": "system", "content": "Sei un desk."}
    assert corpo["messages"][1] == {"role": "user", "content": "fatti"}
    assert "cache_control" not in json.dumps(corpo)


def test_richiesta_tool_loop_nella_forma_openai(monkeypatch):
    """La storia del tool loop e' scritta dai call site nella forma Anthropic (assistant a
    blocchi OGGETTO come li ha resi la risposta, tool_result dentro un messaggio user, nudge
    text in coda allo stesso user): deve uscire come assistant.tool_calls + role tool + user."""
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    blocchi_assistant = [
        llm_client.ThinkingBlock("ragiono..."),
        llm_client.TextBlock("Chiamo un tool."),
        llm_client.ToolUseBlock(id="call_1", name="get_x", input={"t": "ABC"}),
        {"type": "tool_use", "id": "call_2", "name": "get_y", "input": {}},
    ]
    msgs = [
        {"role": "user", "content": "Analizza."},
        {"role": "assistant", "content": blocchi_assistant},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "{\"x\": 1}"},
            {"type": "tool_result", "tool_use_id": "call_2",
             "content": [{"type": "text", "text": "esito y"}],
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "LIMITE ITERAZIONI: scrivi il report."},
        ]},
    ]
    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s", messages=msgs)
    out = t.richieste[0]["messages"]
    assert out[0] == {"role": "system", "content": "s"}
    assert out[1] == {"role": "user", "content": "Analizza."}
    assert out[2] == {"role": "assistant", "content": "Chiamo un tool.", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "get_x", "arguments": "{\"t\": \"ABC\"}"}},
        {"id": "call_2", "type": "function", "function": {"name": "get_y", "arguments": "{}"}},
    ]}
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": "{\"x\": 1}"}
    assert out[4] == {"role": "tool", "tool_call_id": "call_2", "content": "esito y"}
    assert out[5] == {"role": "user", "content": "LIMITE ITERAZIONI: scrivi il report."}
    assert "ragiono" not in json.dumps(out)


def test_richiesta_assistant_solo_tool_calls_ha_content_nullo(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "content": [
                llm_client.ToolUseBlock(id="call_9", name="get_x", input={"a": 1})]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_9", "content": "ok"}]}]
    c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s", messages=msgs)
    out = t.richieste[0]["messages"]
    assert out[2]["content"] is None and out[2]["tool_calls"][0]["id"] == "call_9"
    assert out[3] == {"role": "tool", "tool_call_id": "call_9", "content": "ok"}
    assert len(out) == 4


# ================================================================ 3. traduzione della risposta
def test_risposta_testo_stop_e_usage_con_semantica_anthropic(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(
        content="Memo.", usage=_usage_pieno(), reasoning="pensiero"))])
    c = _client(monkeypatch, t)
    r = c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert r.stop_reason == "end_turn"
    assert r.stop_details is None
    assert r.id == "gen-finto-1" and r.model == "z-ai/glm-finto-flash" and r.role == "assistant"
    tipi = [b.type for b in r.content]
    assert tipi == ["thinking", "text"]
    assert r.content[1].text == "Memo." and r.content[0].thinking == "pensiero"
    assert "".join(b.text for b in r.content if hasattr(b, "text")) == "Memo."
    # input_tokens = prompt - cache (Anthropic li conta a parte; chat_engine.py:629 lo assume)
    assert r.usage.input_tokens == 60
    assert r.usage.cache_read_input_tokens == 30
    assert r.usage.cache_creation_input_tokens == 10
    assert r.usage.output_tokens == 50
    assert r.usage.reasoning_tokens == 20
    assert r.usage.cost_usd == pytest.approx(0.00123)


def test_risposta_tool_calls_diventano_blocchi_tool_use(monkeypatch):
    tc = [{"id": "call_1", "type": "function",
           "function": {"name": "get_x", "arguments": "{\"t\": \"ABC\", \"n\": 2}"}},
          {"id": "call_2", "type": "function",
           "function": {"name": "get_y", "arguments": "{\"t\": "}}]   # JSON troncato
    t = _Trasporto([httpx.Response(200, json=_risposta_json(
        content=None, tool_calls=tc, finish_reason="tool_calls", usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    r = c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert r.stop_reason == "tool_use"
    assert [b.type for b in r.content] == ["tool_use", "tool_use"]
    assert r.content[0].id == "call_1" and r.content[0].name == "get_x"
    assert r.content[0].input == {"t": "ABC", "n": 2}
    # input illeggibile: lo stesso marcatore che la chat gia' gestisce senza dispatch
    assert r.content[1].input.get("__input_parse_error__") is True


def test_risposta_length_e_refusal(monkeypatch):
    t = _Trasporto([
        httpx.Response(200, json=_risposta_json(content="tronc", finish_reason="length",
                                                usage=_usage_pieno())),
        httpx.Response(200, json=_risposta_json(content="", finish_reason="content_filter",
                                                usage=_usage_pieno())),
        httpx.Response(200, json=_risposta_json(content=None, finish_reason="stop",
                                                refusal="Non posso aiutare.",
                                                usage=_usage_pieno())),
    ])
    c = _client(monkeypatch, t)
    base = dict(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                messages=[{"role": "user", "content": "u"}])
    r1 = c.messages.create(**base)
    assert r1.stop_reason == "max_tokens"
    r2 = c.messages.create(**base)
    assert r2.stop_reason == "refusal" and r2.stop_details.category == "content_filter"
    assert r2.content == []
    r3 = c.messages.create(**base)
    assert r3.stop_reason == "refusal"
    assert r3.stop_details.explanation == "Non posso aiutare."


def test_risposta_usage_assente_resta_none_non_zero(monkeypatch):
    t = _Trasporto([
        httpx.Response(200, json=_risposta_json(content="x")),
        httpx.Response(200, json=_risposta_json(content="x", usage={
            "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})),
    ])
    c = _client(monkeypatch, t)
    base = dict(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                messages=[{"role": "user", "content": "u"}])
    assert c.messages.create(**base).usage is None
    u = c.messages.create(**base).usage
    # Senza dettagli cache conosciamo il totale prompt, non la parte non cachata.
    assert u.input_tokens is None and u.prompt_tokens == 7 and u.output_tokens == 3
    assert u.cache_read_input_tokens is None and u.cache_creation_input_tokens is None
    assert u.cost_usd is None and u.reasoning_tokens is None


def test_risposta_errore_nel_corpo_a_200_solleva(monkeypatch):
    t = _Trasporto([httpx.Response(200, json={"error": {"code": 502, "message": "modello giu'"}})])
    c = _client(monkeypatch, t)
    with pytest.raises(llm_client.APIStatusError) as e:
        c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert e.value.status_code == 502 and "modello giu'" in str(e.value)


# ================================================================ 4. errori HTTP e retry
def test_errore_http_porta_status_code_e_messaggio_verbatim(monkeypatch):
    t = _Trasporto([httpx.Response(401, json={"error": {"code": 401, "message": "chiave non valida",
                                                        "metadata": {"provider_name": "X"}}})])
    c = _client(monkeypatch, t, max_retries=2)
    with pytest.raises(llm_client.APIStatusError) as e:
        c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert e.value.status_code == 401
    assert "chiave non valida" in str(e.value)
    assert len(t.richieste) == 1   # un 401 non si ritenta


def test_retry_su_429_poi_200(monkeypatch):
    t = _Trasporto([httpx.Response(429, json={"error": {"code": 429, "message": "rate"}}),
                    httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    c = _client(monkeypatch, t, max_retries=1)
    r = c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert r.stop_reason == "end_turn"
    assert len(t.richieste) == 2


def test_retry_esauriti_rilancia_con_lo_status(monkeypatch):
    t = _Trasporto([httpx.Response(503, json={"error": {"code": 503, "message": "no provider"}})])
    monkeypatch.setattr(llm_client.time, "sleep", lambda s: None)
    c = _client(monkeypatch, t, max_retries=1)
    with pytest.raises(llm_client.APIStatusError) as e:
        c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    assert e.value.status_code == 503
    assert len(t.richieste) == 2   # tentativo + 1 retry


def test_errore_di_rete_diventa_api_connection_error(monkeypatch):
    def esplode(request):
        raise httpx.ConnectError("rete giu'")
    t = _Trasporto([esplode])
    c = _client(monkeypatch, t, max_retries=0)
    with pytest.raises(llm_client.APIConnectionError):
        c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])


# ================================================================ 5. streaming
def test_stream_sync_get_final_message_ricompone_il_messaggio(monkeypatch):
    t = _Trasporto([_sse(
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"content": "Memo "}),
        _chunk({"content": "finto."}),
        _chunk({"content": ""}, finish_reason="stop"),
        _chunk({"content": ""}, finish_reason="stop", usage=_usage_pieno()),
    )])
    c = _client(monkeypatch, t)
    with c.messages.stream(model="anthropic/claude-finto", max_tokens=64000,
                           thinking={"type": "adaptive"}, system="s",
                           messages=[{"role": "user", "content": "u"}]) as s:
        r = s.get_final_message()
    assert t.richieste[0]["stream"] is True
    assert t.richieste[0]["reasoning"] == {"effort": "medium"}
    assert r.stop_reason == "end_turn"
    assert "".join(b.text for b in r.content if hasattr(b, "text")) == "Memo finto."
    assert r.usage.input_tokens == 60 and r.usage.output_tokens == 50
    assert r.usage.cost_usd == pytest.approx(0.00123)


def test_stream_eventi_testo_e_tool_nella_forma_sdk(monkeypatch):
    t = _Trasporto([_sse(
        _chunk({"content": "Guardo "}),
        _chunk({"content": "i dati."}),
        _chunk({"tool_calls": [{"index": 0, "id": "call_7", "type": "function",
                                "function": {"name": "get_x", "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"t\": "}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "\"ABC\"}"}}]}),
        _chunk({}, finish_reason="tool_calls"),
        _chunk({}, finish_reason="tool_calls", usage=_usage_pieno()),
    )])
    c = _client(monkeypatch, t)
    eventi = []
    with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=100, system="s",
                           messages=[{"role": "user", "content": "u"}]) as s:
        for ev in s:
            eventi.append(ev)
        r = s.get_final_message()
    tipi = [(e.type, getattr(getattr(e, "content_block", None), "type", None)
             or getattr(getattr(e, "delta", None), "type", None)) for e in eventi]
    assert tipi == [
        ("content_block_start", "text"),
        ("content_block_delta", "text_delta"),
        ("content_block_delta", "text_delta"),
        ("content_block_stop", None),
        ("content_block_start", "tool_use"),
        ("content_block_delta", "input_json_delta"),
        ("content_block_delta", "input_json_delta"),
        ("content_block_stop", None),
    ]
    assert eventi[1].delta.text == "Guardo " and eventi[2].delta.text == "i dati."
    assert eventi[4].content_block.id == "call_7" and eventi[4].content_block.name == "get_x"
    assert eventi[5].delta.partial_json + eventi[6].delta.partial_json == "{\"t\": \"ABC\"}"
    assert r.stop_reason == "tool_use"
    assert r.content[0].text == "Guardo i dati."
    assert r.content[1].type == "tool_use" and r.content[1].input == {"t": "ABC"}
    assert r.usage.output_tokens == 50


def test_stream_errore_nel_corpo_a_200_solleva(monkeypatch):
    t = _Trasporto([_sse(
        _chunk({"content": "inizio"}),
        _chunk({}, finish_reason="error", error={"code": 502, "message": "provider caduto"}),
    )])
    c = _client(monkeypatch, t)
    with pytest.raises(llm_client.APIStatusError) as e:
        with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=100, system="s",
                               messages=[{"role": "user", "content": "u"}]) as s:
            s.get_final_message()
    assert e.value.status_code == 502 and "provider caduto" in str(e.value)


def test_stream_chiuso_senza_finish_reason_solleva(monkeypatch):
    corpo = "data: " + json.dumps(_chunk({"content": "meta'"})) + "\n\n"
    t = _Trasporto([httpx.Response(200, headers={"content-type": "text/event-stream"},
                                   content=corpo.encode("utf-8"))])
    c = _client(monkeypatch, t)
    with pytest.raises(llm_client.APIConnectionError):
        with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=100, system="s",
                               messages=[{"role": "user", "content": "u"}]) as s:
            s.get_final_message()


def test_stream_http_errore_prima_del_corpo(monkeypatch):
    t = _Trasporto([httpx.Response(402, json={"error": {"code": 402, "message": "crediti finiti"}})])
    c = _client(monkeypatch, t)
    with pytest.raises(llm_client.APIStatusError) as e:
        with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=100, system="s",
                               messages=[{"role": "user", "content": "u"}]) as s:
            s.get_final_message()
    assert e.value.status_code == 402


def test_stream_async_eventi_e_final_message(monkeypatch):
    t = _Trasporto([_sse(
        _chunk({"content": "Ciao "}),
        _chunk({"content": "PM."}),
        _chunk({}, finish_reason="stop", usage=_usage_pieno()),
    )])
    _env_modelli(monkeypatch)
    c = llm_client.AsyncOpenRouterClient(trasporto=t.mock())

    async def corsa():
        eventi = []
        async with c.messages.stream(model="z-ai/glm-finto-flash", max_tokens=100,
                                     thinking={"type": "disabled"}, system="s",
                                     messages=[{"role": "user", "content": "u"}]) as s:
            async for ev in s:
                eventi.append(ev)
            fin = await s.get_final_message()
        return eventi, fin

    eventi, fin = asyncio.run(corsa())
    assert t.richieste[0]["reasoning"] == {"enabled": False}
    assert [e.type for e in eventi] == ["content_block_start", "content_block_delta",
                                        "content_block_delta", "content_block_stop"]
    assert fin.stop_reason == "end_turn"
    assert fin.content[0].text == "Ciao PM."
    assert fin.usage.input_tokens == 60


def test_async_create_non_streaming(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(content="ok", usage=_usage_pieno()))])
    _env_modelli(monkeypatch)
    c = llm_client.AsyncOpenRouterClient(trasporto=t.mock())
    r = asyncio.run(c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                                      messages=[{"role": "user", "content": "u"}]))
    assert r.content[0].text == "ok" and r.usage.cost_usd == pytest.approx(0.00123)


# ================================================================ 6. usage per la contabilita'
def test_somma_costo_accumula_e_dichiara_il_buco():
    u1 = llm_client.Usage(cost_usd=0.001)
    u2 = llm_client.Usage(cost_usd=0.002)
    senza = llm_client.Usage(cost_usd=None)
    assert llm_client.somma_costo(0.0, u1) == pytest.approx(0.001)
    assert llm_client.somma_costo(llm_client.somma_costo(0.0, u1), u2) == pytest.approx(0.003)
    # una risposta senza costo rende il totale un buco, e il buco resta anche dopo
    assert llm_client.somma_costo(0.001, senza) is None
    assert llm_client.somma_costo(None, u1) is None
    assert llm_client.somma_costo(0.0, None) is None
    # i finti dei test (usage senza attributo cost_usd) danno None, mai 0
    assert llm_client.somma_costo(0.0, object()) is None


def test_modello_o_buco_dichiara_la_variabile(monkeypatch):
    _env_modelli(monkeypatch, CAPO_MODEL=None)
    assert llm_client.modello_o_buco("capo") == "n.d. (CAPO_MODEL assente)"
    assert llm_client.modello_o_buco("chat", "quant") == "z-ai/glm-finto-flash"


def test_usage_to_dict_ha_le_chiavi_che_record_usage_legge(monkeypatch):
    t = _Trasporto([httpx.Response(200, json=_risposta_json(usage=_usage_pieno()))])
    c = _client(monkeypatch, t)
    r = c.messages.create(model="z-ai/glm-finto-flash", max_tokens=10, system="s",
                          messages=[{"role": "user", "content": "u"}])
    d = r.usage.to_dict()
    assert d["input_tokens"] == 60 and d["output_tokens"] == 50
    assert d["cache_read_input_tokens"] == 30 and d["cache_creation_input_tokens"] == 10
    assert d["cost_usd"] == pytest.approx(0.00123) and d["reasoning_tokens"] == 20
