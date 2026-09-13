"""The same eight deterministic checks for Italian and English synthetic memos."""
import pytest

from bellomberg.core.language import language_context
from bellomberg.reporting import memo_linter as ml
from bellomberg.agents import action_validator as av
from bellomberg.storage.memory_db import _parse_eur_amount
from test_riproposta_dopo_esecuzione import db, _semina, MEMO_ADD, MEMO_BUY_NUOVO, SIZING_LIBERO, SIZING_VINCOLATO


@pytest.mark.parametrize("lang,memo", [
    ("it", "Acquistare 25k (9,7% del NAV)."),
    ("en", "Buy 25k (9.7% of NAV)."),
])
def test_nav_percentage_check_in_both_languages(lang, memo):
    with language_context(lang):
        assert len(ml._check_nav_percentages(memo, 100000)) == 1
        assert ml._check_nav_percentages(memo.replace("9,7", "25").replace("9.7", "25"), 100000) == []


@pytest.mark.parametrize("lang,heading", [("it", "Tabella Scenari"), ("en", "Scenario Table")])
def test_scenario_sum_check_in_both_languages(lang, heading):
    memo = f"## 10. {heading}\n| Case | Probability |\n|---|---|\n| Base | 70% |\n| Bear | 50% |\n"
    with language_context(lang):
        assert len(ml._check_scenario_sum(memo)) == 1
        assert ml._check_scenario_sum(memo.replace("50%", "30%")) == []


@pytest.mark.parametrize("lang,phrase", [("it", "cash disponibile"), ("en", "available cash")])
def test_cash_quadrature_check_in_both_languages(lang, phrase):
    with language_context(lang):
        assert len(ml._check_cash_quadrature(f"{phrase}: 30k", 10000)) == 1
        assert ml._check_cash_quadrature(f"{phrase}: 10k", 10000) == []


@pytest.mark.parametrize("lang,phrase", [("it", "dai massimi"), ("en", "from highs")])
def test_drawdown_consistency_check_in_both_languages(lang, phrase):
    memo = f"SYNTHINDEX -10% {phrase}.\nSYNTHINDEX -14% {phrase}."
    with language_context(lang):
        assert len(ml._check_drawdown_duplicati(memo)) == 1
        assert ml._check_drawdown_duplicati(memo.replace("-14", "-10")) == []


@pytest.mark.parametrize("first,second,phrase", [("OGGI", "IERI", "dai massimi"),
                                                ("TODAY", "YESTERDAY", "from highs")])
def test_drawdown_identifies_ticker_past_bilingual_time_words(first, second, phrase):
    memo = f"SYNTHINDEX {first} -10% {phrase}.\nSYNTHINDEX {second} -14% {phrase}."
    warnings = ml._check_drawdown_duplicati(memo)
    assert len(warnings) == 1
    assert "SYNTHINDEX" in warnings[0]


@pytest.mark.parametrize("lang,reference", [("it", "Decisione"), ("en", "Decision")])
def test_source_coverage_check_in_both_languages(lang, reference):
    memo = "\n".join(f"Allocate {n}k EUR [{reference} #{n}]" for n in (3, 4, 5))
    with language_context(lang):
        assert ml._check_src_coverage(memo) == []
        assert len(ml._check_src_coverage("\n".join(f"Allocate {n}k EUR" for n in (3, 4, 5)))) == 1


@pytest.mark.parametrize("lang,tag", [("it", "SOPRA POLICY"), ("en", "[OVER-POLICY]")])
def test_sizing_override_marker_in_both_languages(lang, tag):
    memo = MEMO_BUY_NUOVO.replace("2.000 a mercato, 5.000 dopo il FOMC", tag + ": explicit reason")
    with language_context(lang):
        block = av.build_validator_block(memo, SIZING_VINCOLATO, None)
        row = next(row for row in block.splitlines() if row.startswith("- ") and "NUOVO.MI" in row)
        assert ("DEROGA DICHIARATA" if lang == "it" else "DECLARED OVERRIDE") in row
        without = av.build_validator_block(MEMO_BUY_NUOVO, SIZING_VINCOLATO, None)
        assert ("deroga NON dichiarata" if lang == "it" else "override NOT declared") in without


@pytest.mark.parametrize("lang,tag", [("it", "NOVITA'"), ("en", "[NEW-FACT]")])
def test_new_fact_marker_after_execution_in_both_languages(db, lang, tag):
    _semina(db)
    memo = MEMO_ADD.replace("a mercato entro il 18/09", tag + ": explicit new fact")
    with language_context(lang):
        marker = "RIPROPOSTA POST-ESECUZIONE" if lang == "it" else "REPROPOSED AFTER EXECUTION"
        assert marker not in av.build_validator_block(memo, SIZING_LIBERO, db)
        assert marker in av.build_validator_block(MEMO_ADD, SIZING_LIBERO, db)


def test_validator_english_warning_keeps_original_feedback_verbatim(db):
    _semina(db)
    original = "Parole storiche: deroga NON dichiarata, il PM decide."
    with db._conn() as conn:
        conn.execute("UPDATE decisions SET pm_feedback=?", (original,))
    with language_context("en"):
        block = av.build_validator_block(MEMO_ADD, None, db)
    assert "SIZING CHECK NOT PERFORMED" in block
    assert "DIRECT PM feedback" in block
    assert original in block


@pytest.mark.parametrize("it,en,amount", [
    ("5 mila", "5 thousand", 5000), ("2 milioni", "2 million", 2000000),
    ("1,2 mld", "1.2 bn", 1200000000), ("1 miliardo", "1 billion", 1000000000),
    ("2 mln", "2 mm", 2000000), ("1.234,56 EUR", "1,234.56 EUR", 1234.56),
])
def test_eur_amounts_preserve_identical_values_in_both_languages(it, en, amount):
    with language_context("it"):
        assert _parse_eur_amount(it) == amount
    with language_context("en"):
        assert _parse_eur_amount(en) == amount


@pytest.mark.parametrize("header", ["Action", "Azione"])
def test_action_header_is_never_a_decision(header):
    memo = MEMO_ADD.replace("| Action |", f"| {header} |")
    rows = av._parse_action_rows(memo)
    assert len(rows) == 1 and rows[0]["action"] == "ADD"


@pytest.mark.parametrize("header", ["Action", "Azione"])
def test_database_fallback_never_persists_action_header(db, monkeypatch, header):
    from bellomberg.agents import action_table_extract
    monkeypatch.setattr(action_table_extract, "extract_rows_structured",
                        lambda *a, **kw: {"ok": False, "error": "synthetic unavailable client"})
    memo = MEMO_ADD.replace("| Action |", f"| {header} |")
    memo_id = db.save_memo(memo, title="synthetic bilingual fallback")
    db.extract_and_save_decisions(memo_id, memo)
    with db._conn() as conn:
        rows = conn.execute("SELECT action,ticker FROM decisions WHERE memo_id=?", (memo_id,)).fetchall()
    assert [tuple(row) for row in rows] == [("ADD", "ALFA.DE")]
