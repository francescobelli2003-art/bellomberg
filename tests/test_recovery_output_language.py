"""Language guard before any paid recovery; old source texts are not translated."""
import pytest

from bellomberg.cli import regenerate_memo as recovery


@pytest.mark.parametrize("language", ["it", "en"])
def test_recovery_inherits_concordant_documented_language(language):
    assert recovery._recovery_language(language, [language, language]) == language


@pytest.mark.parametrize("memo,reports", [(None, [None]), ("unknown", [None]),
    ("it", ["en"]), ("it", [None]), (None, ["en"])])
def test_legacy_or_discordant_recovery_requires_explicit_choice(memo, reports):
    with pytest.raises(ValueError, match="--language"):
        recovery._recovery_language(memo, reports)


def test_explicit_choice_can_generate_new_output_from_unknown_sources():
    assert recovery._recovery_language(None, [None], "en") == "en"


def test_explicit_different_language_cannot_overwrite_known_historical_memo():
    with pytest.raises(ValueError, match="nuova versione"):
        recovery._recovery_language("it", ["it"], "en")


def test_invalid_choice_is_never_silently_normalized():
    with pytest.raises(ValueError):
        recovery._recovery_language(None, [], "EN")


@pytest.mark.parametrize("has_memo", [False, True])
def test_empty_recovery_before_language_resolution_is_bilingual(tmp_path, monkeypatch, capsys, has_memo):
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None))
    db = memory_db.MemoryDB(str(tmp_path / "empty.db"), str(tmp_path / "chroma"))
    if has_memo:
        with db._conn() as conn:
            conn.execute("INSERT INTO memos(timestamp,title,full_markdown) VALUES ('2001-01-01','Originale','Originale')")
    monkeypatch.setattr(recovery, "MemoryDB", lambda: db)
    capsys.readouterr()
    recovery.main()
    output = capsys.readouterr().out
    assert ("No saved reports" if has_memo else "No memo in the database") in output
    assert ("Nessun report" if has_memo else "Nessun memo") in output


def test_recovery_pipeline_uses_one_language_after_preference_changes(tmp_path, monkeypatch):
    from bellomberg.core.language import current_language, language_context
    from bellomberg.storage import memory_db, preferences
    from bellomberg.core import mandato_pm
    from bellomberg.portfolio import portfolio_risk, portfolio_montecarlo, sizing_engine, portfolio_analytics
    from bellomberg.agents import specialist_scores, action_validator
    from bellomberg.reporting import memo_linter, pdf_institutional, charts_quant, email_sender
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: self.__dict__.update(
        chroma_client=None, col_memos=None, col_decisions=None, col_feedback=None))
    db = memory_db.MemoryDB(db_path=str(tmp_path / "recovery.db"), chroma_path=str(tmp_path / "chroma"))
    with language_context("en"):
        memo_id = db.save_memo("Original English memo")
        db.save_specialist_report(memo_id, "macro", 2, "Original source: citazione italiana verbatim")
    monkeypatch.setattr(recovery, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", lambda: {})
    monkeypatch.setattr(db, "extract_and_save_decisions", lambda *_: None)
    monkeypatch.setattr(mandato_pm, "carica", lambda: {})
    class Blackboard:
        def __init__(self, **_): self.data = {}
        def mark_run_complete(self): pass
    monkeypatch.setattr(recovery, "Blackboard", Blackboard)
    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", lambda: {})
    monkeypatch.setattr(portfolio_montecarlo, "run_monte_carlo", lambda **_: {})
    monkeypatch.setattr(sizing_engine, "compute_sizing", lambda *_a, **_k: {})
    for name in ("quant_score", "macro_score", "crypto_score", "eventdesk_score", "fundamentals_score", "options_score"):
        monkeypatch.setattr(specialist_scores, name, lambda *_a, **_k: None)
    seen = []
    def capo(board, **_):
        seen.append(("capo", current_language()))
        assert board.data["macro"][2] == "Original source: citazione italiana verbatim"
        preferences.set_language_preference("it")  # changes while recovery runs
        return "New English memo", {}
    monkeypatch.setattr(recovery, "run_capo", capo)
    monkeypatch.setattr(memo_linter, "build_linter_block", lambda *_: "")
    monkeypatch.setattr(action_validator, "build_validator_block", lambda *_a, **_k: "")
    monkeypatch.setattr(action_validator, "detect_sanity_exclusions", lambda *_: ("", []))
    monkeypatch.setattr(portfolio_analytics, "compute_nav_history", lambda: {})
    def render(kind, **_):
        seen.append((kind, current_language()))
        return str(tmp_path / (kind + ".pdf"))
    monkeypatch.setattr(pdf_institutional, "build_institutional_memo", lambda **kw: render("pdf", **kw))
    monkeypatch.setattr(charts_quant, "build_quant_appendix_v2", lambda **kw: render("appendix", **kw))
    monkeypatch.setattr(email_sender, "email_configurata", lambda: True)
    monkeypatch.setattr(email_sender, "invia_email_multi_allegati", lambda **kw: render("email", **kw))
    for path_name in ("RESEARCH_NOTES_DIR", "MODELS_DIR", "REPORT_DIR"):
        monkeypatch.setattr(recovery, path_name, tmp_path / path_name)
    recovery.main()
    assert seen == [("capo", "en"), ("pdf", "en"), ("appendix", "en"), ("email", "en")]
    with db._conn() as conn:
        assert tuple(conn.execute("SELECT full_markdown,output_language FROM memos WHERE id=?", (memo_id,)).fetchone()) == ("New English memo", "en")
        assert conn.execute("SELECT content FROM specialist_reports").fetchone()[0] == "Original source: citazione italiana verbatim"
