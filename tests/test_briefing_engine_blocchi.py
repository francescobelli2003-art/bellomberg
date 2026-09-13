# -*- coding: utf-8 -*-
"""Briefing 11/09 (data/briefing.log, slot 11:30 e 19:30): `msg.content[0].text` con il primo
blocco di tipo thinking -> AttributeError -> `{"error": ...}` -> il .bat stampava
«direct OK evening chars= 0» ed usciva 0: un fallimento vero loggato come OK (MASTER 7941).

Qui, con un client FINTO montato su llm_client (zero rete, zero costi, cache in tmp):
- il testo del briefing viene dai SOLI blocchi di tipo text, qualunque sia la loro posizione;
- una risposta senza blocchi di testo e' un errore DICHIARATO (log + chiave error), non un
  briefing vuoto salvato in cache;
- il ramo «direct» dei .bat (main_direct) esce 0 SOLO con un briefing non vuoto.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from bellomberg.cli import briefing_engine as be
import bellomberg.core.llm_client as lc


@pytest.fixture
def motore_isolato(monkeypatch, tmp_path):
    """Nessuna fonte viva: news/macro/portafoglio/prompt stubbati, cache in tmp, modello di prova."""
    monkeypatch.setattr(be, "CACHE_PATH", str(tmp_path / "briefing_cache.json"))
    monkeypatch.setattr(be, "_fetch_recent_news", lambda **kw: [])
    monkeypatch.setattr(be, "_fetch_macro_snapshot", lambda: {})
    monkeypatch.setattr(be, "_fetch_portfolio_snapshot", lambda: {})
    monkeypatch.setattr(be, "_build_briefing_prompt", lambda *a, **k: "prompt di prova")
    monkeypatch.setattr(lc, "modello", lambda funzione, agente=None, round_n=None: "modello-di-prova")
    return tmp_path


def _monta_client(monkeypatch, blocchi):
    risposta = NS(content=blocchi, usage=NS(input_tokens=10, output_tokens=20), stop_reason="end_turn")
    chiamate = []

    def _crea(**kw):
        chiamate.append(kw)
        return risposta

    monkeypatch.setattr(lc, "OpenRouterClient", lambda **kw: NS(messages=NS(create=_crea)))
    return chiamate


TESTO = "## MARKET TONE\nRisk-off moderato.\n\n## MACRO HIGHLIGHTS\nNulla di nuovo."


def test_il_testo_viene_dai_blocchi_text_anche_se_il_primo_blocco_e_thinking(motore_isolato, monkeypatch):
    """La forma esatta dell'11/09: reasoning forzato -> ThinkingBlock in testa, TextBlock dopo."""
    _monta_client(monkeypatch, [lc.ThinkingBlock("penso..."), lc.TextBlock(TESTO)])
    r = be.generate_briefing("evening")
    assert "error" not in r, r
    assert r["briefing_md"] == TESTO
    assert r["period"] == "evening" and r["tokens_in"] == 10 and r["tokens_out"] == 20
    cache = json.loads((motore_isolato / "briefing_cache.json").read_text(encoding="utf-8"))
    assert cache["evening"]["briefing_md"] == TESTO and cache["_latest"] == "evening"


def test_piu_blocchi_di_testo_si_concatenano_e_gli_altri_tipi_si_ignorano(motore_isolato, monkeypatch):
    _monta_client(monkeypatch, [lc.TextBlock("prima parte"), lc.ThinkingBlock("x"),
                                NS(type="tool_use", id="t1", name="f", input={}), lc.TextBlock("seconda parte")])
    r = be.generate_briefing("morning")
    assert r["briefing_md"] == "prima parte\nseconda parte"


def test_risposta_senza_blocchi_di_testo_e_un_errore_dichiarato_non_un_briefing_vuoto(motore_isolato, monkeypatch, capsys):
    _monta_client(monkeypatch, [lc.ThinkingBlock("solo ragionamento, nessun testo")])
    r = be.generate_briefing("midday")
    assert "error" in r, r
    assert "senza testo" in r["error"] and "thinking" in r["error"], r["error"]
    assert not (r.get("briefing_md") or ""), "un briefing vuoto non deve passare per generato"
    assert r["period"] == "midday"
    assert not (motore_isolato / "briefing_cache.json").exists(), "niente in cache: il PM rileggerebbe un vuoto come fresco"
    out = capsys.readouterr().out
    assert "SENZA blocchi di testo" in out and "thinking" in out, out


def test_risposta_con_contenuto_vuoto_e_un_errore_dichiarato(motore_isolato, monkeypatch):
    _monta_client(monkeypatch, [])
    r = be.generate_briefing("afternoon")
    assert "error" in r and "senza testo" in r["error"] and "nessuno" in r["error"], r


def test_blocco_text_con_testo_vuoto_non_e_un_briefing(motore_isolato, monkeypatch):
    _monta_client(monkeypatch, [lc.TextBlock("   \n  ")])
    r = be.generate_briefing("morning")
    assert "error" in r, r


def test_errore_non_sovrascrive_l_ultimo_briefing_valido(motore_isolato, monkeypatch):
    cache_path = motore_isolato / "briefing_cache.json"
    cache_path.write_text(json.dumps({
        "morning": {"briefing_md": "briefing precedente", "generated_at": "2026-09-10T07:30:00"},
        "_latest": "morning", "_latest_at": "2026-09-10T07:30:00",
    }), encoding="utf-8")
    prima = cache_path.read_bytes()
    _monta_client(monkeypatch, [lc.TextBlock(" \n ")])

    r = be.generate_briefing("morning")

    assert "error" in r
    assert cache_path.read_bytes() == prima, "il guasto non deve rendere fresca la cache precedente"


# ---------------------------------------------------------------------------
# main_direct: cio' che il .bat chiama nel ramo fallback. Exit 0 SOLO con testo.
# ---------------------------------------------------------------------------
def test_main_direct_esce_1_e_scrive_KO_quando_il_briefing_ha_error(monkeypatch, capsys):
    monkeypatch.setattr(be, "generate_briefing", lambda period=None: {
        "error": "risposta del modello senza testo (blocchi: thinking)", "period": "evening"})
    rc = be.main_direct()
    out = capsys.readouterr().out
    assert rc == 1
    assert "direct KO" in out and "evening" in out and "senza testo" in out, out
    assert "direct OK" not in out, out


def test_main_direct_esce_1_con_zero_caratteri_anche_senza_error(monkeypatch, capsys):
    monkeypatch.setattr(be, "generate_briefing", lambda period=None: {"period": "midday", "briefing_md": ""})
    rc = be.main_direct()
    out = capsys.readouterr().out
    assert rc == 1
    assert "direct KO" in out and "chars= 0" in out, out


def test_main_direct_esce_0_con_la_riga_di_sempre_quando_il_briefing_c_e(monkeypatch, capsys):
    monkeypatch.setattr(be, "generate_briefing", lambda period=None: {"period": "midday", "briefing_md": TESTO})
    rc = be.main_direct()
    out = capsys.readouterr().out
    assert rc == 0
    assert f"direct OK midday chars= {len(TESTO)}" in out, out


def test_main_direct_passa_lo_slot_richiesto(monkeypatch):
    visti = []

    def _finto(period=None):
        visti.append(period)
        return {"period": period, "briefing_md": TESTO}

    monkeypatch.setattr(be, "generate_briefing", _finto)
    assert be.main_direct("morning") == 0
    assert visti == ["morning"]


def test_main_direct_non_propaga_una_pipe_morta(monkeypatch):
    """Stessa classe di test_log_pipe_morta: il .bat redirige stdout su file; se la pipe
    e' morta l'esito deve restare l'exit code, non un OSError."""
    import builtins
    monkeypatch.setattr(be, "generate_briefing", lambda period=None: {"period": "midday", "briefing_md": TESTO})

    def _pipe_morta(*a, **k):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(builtins, "print", _pipe_morta)
    assert be.main_direct() == 0


@pytest.mark.parametrize("testo", [" \n ", None, 123])
def test_main_direct_rifiuta_testo_non_utilizzabile(monkeypatch, capsys, testo):
    monkeypatch.setattr(be, "generate_briefing", lambda period=None: {
        "period": "midday", "briefing_md": testo})
    assert be.main_direct() == 1
    out = capsys.readouterr().out
    assert "direct KO" in out and "direct OK" not in out


def test_main_direct_dichiara_anche_l_eccezione_del_motore(monkeypatch, capsys):
    def _fallisce(period=None):
        raise RuntimeError("fonte di prova indisponibile")

    monkeypatch.setattr(be, "generate_briefing", _fallisce)
    assert be.main_direct("evening") == 1
    out = capsys.readouterr().out
    assert "direct KO evening" in out and "fonte di prova indisponibile" in out


def test_cli_esce_1_su_periodo_non_valido_senza_toccare_fonti(tmp_path):
    ambiente = os.environ.copy()
    ambiente["BELLOMBERG_DATA_DIR"] = str(tmp_path / "dati-cli")
    ambiente["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "bellomberg.cli.briefing_engine", "periodo-inesistente"],
        env=ambiente, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["error"] == "unknown period: periodo-inesistente"
    assert not (tmp_path / "dati-cli" / "briefing_cache.json").exists()


@pytest.mark.parametrize("language", ["it", "en"])
@pytest.mark.parametrize("content", ["{truncated", "[]", '{"_languages_v1": null}',
                                      '{"_latest":"morning"}',
                                      '{"_latest":"morning","morning":{"briefing_md":""}}'])
def test_cache_illeggibile_e_dichiarata_senza_spesa_o_sovrascrittura(motore_isolato, monkeypatch, language, content):
    from pathlib import Path
    path = Path(be.CACHE_PATH + (".en.v1.json" if language == "en" else ""))
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    calls = _monta_client(monkeypatch, [lc.TextBlock("not to be generated")])
    view = be.get_current_briefing(language=language)
    assert view.get("error_code") == "briefing_cache_unreadable", view
    assert view["error"] and view["stale"] and view["generated_at"] is None
    assert ("NON LEGGIBILE" if language == "it" else "UNREADABLE") in view["briefing_md"]
    assert "not been generated" not in view["briefing_md"] and "ancora stato generato" not in view["briefing_md"]
    monkeypatch.setattr(be, "_fetch_recent_news", lambda **kw: pytest.fail("corrupt cache must fail before sources or model"))
    result = be.generate_briefing("morning", language=language)
    assert result.get("error_code") == "briefing_cache_unreadable", result
    assert be.main_direct("morning", language=language) == 1
    assert path.read_bytes() == before and not calls


@pytest.mark.parametrize("language", ["it", "en"])
def test_cache_assente_resta_assenza_senza_creare_file(motore_isolato, language):
    before = set(motore_isolato.iterdir())
    result = be.get_current_briefing(language=language)
    assert "error" not in result and result["generated_at"] is None
    assert set(motore_isolato.iterdir()) == before


def test_permesso_cache_negato_e_un_guasto_non_assenza(motore_isolato, monkeypatch):
    import builtins
    original_open = builtins.open
    def denied(file, *args, **kwargs):
        if str(file) == be.CACHE_PATH:
            raise PermissionError("synthetic access denied")
        return original_open(file, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", denied)
    result = be.get_current_briefing(language="en")  # another physical file is still absent
    assert "error" not in result
    result = be.get_current_briefing(language="it")
    assert result.get("error_code") == "briefing_cache_unreadable"
    assert "synthetic access denied" in result["error"]


def test_corruzione_cache_durante_modello_non_viene_sovrascritta(motore_isolato, monkeypatch):
    from pathlib import Path
    calls = []
    def create(**kw):
        calls.append(kw)
        Path(be.CACHE_PATH).write_text("{concurrent corruption", encoding="utf-8")
        return NS(content=[lc.TextBlock(TESTO)], usage=NS(input_tokens=10, output_tokens=20))
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    result = be.generate_briefing("morning", language="it")
    assert result.get("error_code") == "briefing_cache_unreadable"
    assert Path(be.CACHE_PATH).read_text(encoding="utf-8") == "{concurrent corruption"
    assert len(calls) == 1
