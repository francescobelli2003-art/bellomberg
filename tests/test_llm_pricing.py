"""llm_pricing: listino unico dei costi LLM — tariffe derivate dai
moltiplicatori, alias dated, modello fuori listino = buco dichiarato
(mai 0,00 di comodo), formato log italiano. Solo cost_usd: niente FX/rete.
"""
import pytest

from bellomberg.core.llm_pricing import cost_usd, format_eur, resolve_model


def test_cost_usd_listino_cache_alias_e_model_unknown():
    usage = {"in": 1_000_000, "out": 1_000_000,
             "cache_read": 1_000_000, "cache_write": 1_000_000}

    # Opus 4.8, TTL 1h: 5 (in) + 25 (out) + 0.5 (read 0.1x) + 10 (write 2.0x)
    r = cost_usd("claude-opus-4-8", usage, cache_ttl="1h")
    assert r["status"] == "ok"
    assert r["cost"] == pytest.approx(40.5)

    # alias dated -> id canonico del listino
    assert resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"

    # senza TTL i token di cache NON si prezzano e il breakdown lo dichiara
    r_nottl = cost_usd("claude-opus-4-8", usage, cache_ttl=None)
    assert r_nottl["cost"] == pytest.approx(30.0)
    assert "note" in r_nottl["breakdown"]

    # modello fuori listino: None + model_unknown, mai la tariffa di un "simile"
    ru = cost_usd("gpt-4o", usage)
    assert ru["cost"] is None and ru["status"] == "model_unknown"

    # formato log italiano (voce costi 21/07: "0,01 EUR", mai float grezzo)
    assert format_eur(0.01) == "0,01 EUR"
    assert format_eur(None) == "n.d."


def test_costo_consegnato_da_openrouter_vince_sul_listino():
    """05/09 (OpenRouter): se l'usage porta `cost_usd` (usage.cost dell'API, USD) e' LUI la
    misura, anche per uno slug che il listino di casa non conosce; senza, lo slug ignoto
    resta model_unknown (mai la tariffa di un «simile»)."""
    usage = {"in": 100, "out": 50, "cache_read": 30, "cache_write": 10, "cost_usd": 0.00123}
    r = cost_usd("z-ai/glm-5.3-flash", usage, cache_ttl=None)
    assert r["status"] == "ok"
    assert r["cost"] == pytest.approx(0.00123)
    assert r["breakdown"]["fonte"] == "openrouter_api"
    assert r["breakdown"]["tokens"]["in"] == 100
    # a listino noto ma costo consegnato: vince comunque la misura dell'API
    r2 = cost_usd("claude-opus-5", {"in": 1_000_000, "out": 0, "cost_usd": 4.0})
    assert r2["cost"] == pytest.approx(4.0) and r2["breakdown"]["fonte"] == "openrouter_api"
    # senza costo consegnato: slug ignoto = buco dichiarato
    r3 = cost_usd("z-ai/glm-5.3-flash", {"in": 100, "out": 50})
    assert r3["cost"] is None and r3["status"] == "model_unknown"
    # cost_usd=None esplicito vale «non consegnato»
    r4 = cost_usd("z-ai/glm-5.3-flash", {"in": 100, "out": 50, "cost_usd": None})
    assert r4["status"] == "model_unknown"


def test_listino_modelli_attivi_26_07():
    """Swap modelli PM 26/07: opus-5 e sonnet-5 A LISTINO (senza, il motore
    costi dichiarerebbe model_unknown su OGNI chiamata della run), vecchi
    modelli MAI rimossi (righe storiche), sonnet-5 al prezzo INTRO fino 31/08."""
    usage = {"in": 1_000_000, "out": 1_000_000}

    # Opus 5: stesso listino di Opus 4.8 (5 in + 25 out)
    r5 = cost_usd("claude-opus-5", usage)
    assert r5["status"] == "ok" and r5["cost"] == pytest.approx(30.0)

    # Sonnet 5: INTRO 2/10 fino al 31/08/2026 (poi 3/15 — voce flip a MASTER)
    rs = cost_usd("claude-sonnet-5", usage)
    assert rs["status"] == "ok" and rs["cost"] == pytest.approx(12.0)

    # i modelli precedenti restano prezzabili (run/log storici)
    assert cost_usd("claude-opus-4-8", usage)["status"] == "ok"
    assert cost_usd("claude-sonnet-4-6", usage)["status"] == "ok"
