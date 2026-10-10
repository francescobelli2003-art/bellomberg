"""Scenario arithmetic uses independent, synthetic Markdown tables (R03)."""
import pytest

from bellomberg.core.language import language_context
from bellomberg.reporting.memo_linter import _check_scenario_sum, build_linter_block


def table(rows, header="Scenario | Probabilita", heading="Tabella Scenari"):
    return (f"## {heading}\n| {header} |\n|"
            + "|".join("---" for _ in header.split("|")) + "|\n"
            + "".join(f"| {row} |\n" for row in rows))


@pytest.mark.parametrize("total", ["Totale", " TOTAL ", "**Totale:**", "_total_"])
def test_total_is_not_a_fourth_scenario(total):
    assert _check_scenario_sum(table(["A | 60%", "B | 40%", f"{total} | 100%"])) == []


@pytest.mark.parametrize("separator", ["\n", "\n### Seconda distribuzione\n", "\n## Scenario Table\n"])
def test_independent_tables_are_not_added_together(separator):
    first = table(["A | 60%", "B | 40%", "Totale | 100%"])
    second = table(["C | 70%", "D | 30%", "Total | 100%"],
                   header="Case | Impact | Probability").replace(
                       "C | 70%", "C | -12% | 70%").replace(
                       "D | 30%", "D | +7% | 30%").replace(
                       "Total | 100%", "Total | n.d. | 100%")
    assert _check_scenario_sum(first + separator + second.split("\n", 1)[1]) == []


def test_opposite_errors_in_independent_tables_do_not_cancel():
    first = table(["A | 30%", "B | 30%"])
    second = table(["C | 70%", "D | 70%"])
    warnings = _check_scenario_sum(first + "\n" + second)
    assert len(warnings) == 2
    assert "60%" in warnings[0] and "140%" in warnings[1]


def test_wrong_declared_total_is_checked_separately():
    warnings = _check_scenario_sum(table(["A | 60%", "B | 40%", "Totale | 80%"]))
    assert len(warnings) == 1
    assert "totale dichiarato" in warnings[0].lower()
    assert "80%" in warnings[0] and "100%" in warnings[0]


def test_correct_declared_total_does_not_hide_wrong_distribution():
    warnings = _check_scenario_sum(table(["A | 60%", "B | 60%", "Totale | 120%"]))
    assert len(warnings) == 1
    assert "sommano 120%" in warnings[0]


def test_total_in_a_sentence_is_a_scenario_not_a_total_row():
    warnings = _check_scenario_sum(table(["Perdita totale del mercato | 25%", "Base | 45%",
                                           "Rialzo | 30%", "Totale | 100%"]))
    assert warnings == []


# PM 09/10: band aligned to the Capo prompt (capo.py ~186, "~100% (98-102)").
@pytest.mark.parametrize("second, clean", [("37,9", False), ("38", True), ("42", True), ("42,1", False)])
def test_tolerance_matches_capo_prompt_band(second, clean):
    warnings = _check_scenario_sum(table(["A | 60%", f"B | {second}%"]))
    assert (warnings == []) is clean


def test_tolerance_constant_is_the_capo_prompt_band():
    import re
    from pathlib import Path
    from bellomberg.reporting import memo_linter
    assert memo_linter.SCENARI_SUM_RANGE == (98.0, 102.0)
    # Review D6: the band is bound to the Capo prompt text, not only to a literal.
    capo = Path(memo_linter.__file__).resolve().parents[1] / "agents" / "capo.py"
    found = re.findall(r"sommare ~100% \((\d+)-(\d+)\)", capo.read_text(encoding="utf-8"))
    assert [tuple(float(x) for x in pair) for pair in found] == [memo_linter.SCENARI_SUM_RANGE]


@pytest.mark.parametrize("declared, clean", [("97,9", False), ("98", True), ("102", True), ("102,1", False)])
def test_declared_total_residual_uses_the_same_band(declared, clean):
    warnings = _check_scenario_sum(table(["A | 60%", "B | 40%", f"Totale | {declared}%"]))
    assert (warnings == []) is clean


def test_decimal_comma_and_impact_percentages_do_not_contaminate_probability():
    assert _check_scenario_sum(table(
        ["A | -15% | 62,5%", "B | +21% | 37,5%", "Totale | +6% | 100,0%"],
        header="Scenario | Impatto | Probabilità")) == []


def test_qualitative_table_does_not_infer_probabilities_from_impact():
    assert _check_scenario_sum(table(["A | alta | -15%", "B | bassa | +12%"],
                                     header="Scenario | Probabilità | Impatto")) == []


@pytest.mark.parametrize("language, heading, header, note, marker", [
    ("it", "Tabella Scenari", "Scenario | Probabilita condizionata", "", "non applicabile"),
    ("en", "Scenario Table", "Scenario | Conditional probability", "", "not applicable"),
    ("it", "Tabella Scenari", "Scenario | Probabilita", "Probabilita condizionate su denominatori differenti.\n", "non applicabile"),
    ("en", "Scenario Table", "Scenario | Probability", "Conditional probabilities with different denominators.\n", "not applicable"),
])
def test_conditional_probabilities_are_explicitly_not_applicable(language, heading, header, note, marker):
    memo = table(["A | 70%", "B | 80%"], header, heading)
    memo = memo.replace(f"## {heading}\n", f"## {heading}\n{note}")
    with language_context(language):
        warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and marker in warnings[0].lower()
    assert "150%" not in warnings[0]


def test_different_denominator_column_is_explicitly_not_applicable():
    warnings = _check_scenario_sum(table(
        ["A | 70% | Evento X", "B | 80% | Evento Y"], "Scenario | Prob | Denominatore"))
    assert len(warnings) == 1 and "non applicabile" in warnings[0].lower()


def test_conditional_table_does_not_suppress_next_ordinary_table():
    conditional = table(["A | 70%", "B | 80%"], "Scenario | Prob condizionata")
    ordinary = table(["C | 65%", "D | 65%"]).split("\n", 1)[1]
    warnings = _check_scenario_sum(conditional + "\n" + ordinary)
    assert len(warnings) == 2
    assert "non applicabile" in warnings[0].lower() and "130%" in warnings[1]


def test_english_warning_and_non_scenario_section_scope():
    with language_context("en"):
        warnings = _check_scenario_sum(table(["A | 60%", "B | 70%"], "Scenario | Probability", "Scenario Table"))
    assert len(warnings) == 1 and "probabilities total 130%" in warnings[0]
    assert _check_scenario_sum(table(["A | 60%", "B | 70%"], heading="Other analysis")) == []


@pytest.mark.parametrize("declared, mismatch", [("80", True), ("100", False)])
def test_single_scenario_checks_declared_total(declared, mismatch):
    warnings = _check_scenario_sum(table(["Unico esito | 100%", f"Totale | {declared}%"]))
    assert bool(warnings) is mismatch
    if mismatch:
        assert "totale dichiarato 80%" in warnings[0] and "100%" in warnings[0]


@pytest.mark.parametrize("label, note", [
    ("Aiuto condizionato", ""),
    ("Conditional aid", ""),
    ("Aiuto", "L'aiuto e' condizionato a una riforma.\n"),
    ("Aid", "Conditional aid depends on a reform.\n"),
])
def test_conditional_event_does_not_suppress_ordinary_probability_sum(label, note):
    memo = table([f"{label} | 60%", "Nessun aiuto | 60%"])
    memo = memo.replace("## Tabella Scenari\n", "## Tabella Scenari\n" + note)
    warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "sommano 120%" in warnings[0]


@pytest.mark.parametrize("probability", ["30–40%", "30 – 40%", "30-40%", "30—40%", "30%–40%"])
def test_probability_interval_is_explicitly_not_a_point_estimate(probability):
    warnings = _check_scenario_sum(table([f"A | {probability}", "B | 60%"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


@pytest.mark.parametrize("probability, expected", [("+60%", "sommano 120%"), ("-60%", "non applicabile")])
def test_signed_numeric_probabilities_never_look_qualitative(probability, expected):
    warnings = _check_scenario_sum(table([f"A | {probability}", f"B | {probability}"]))
    assert len(warnings) == 1 and expected in warnings[0]


@pytest.mark.parametrize("first, second", [("-10%", "110%"), ("101%", "0%"),
                                          ("- 5%", "95%"), ("−5%", "95%")])
def test_invalid_individual_probability_cannot_pass_sum_tolerance(first, second):
    warnings = _check_scenario_sum(table([f"A | {first}", f"B | {second}"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


def test_conditional_notation_in_probability_cell_remains_not_applicable():
    warnings = _check_scenario_sum(table([r"A | P(A\|X) = 60%", r"B | P(B\|Y) = 60%"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


@pytest.mark.parametrize("note, first, second", [
    ("", r"P(A\|X)", r"P(B\|Y)"),
    ("Probabilities are conditional on different events.\n", "A", "B"),
])
def test_explicit_conditional_declaration_in_labels_or_prose(note, first, second):
    memo = table([f"{first} | 60%", f"{second} | 60%"],
                 "Scenario | Probability", "Scenario Table")
    memo = memo.replace("## Scenario Table\n", "## Scenario Table\n" + note)
    with language_context("en"):
        warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "not applicable" in warnings[0]
    assert "120%" not in warnings[0]


def test_warning_reaches_real_pdf_renderer(tmp_path, monkeypatch):
    import pymupdf
    from bellomberg.reporting import pdf_institutional as renderer

    # The fixture has no portfolio or NAV history: only the memo body is under test.
    monkeypatch.setattr(renderer, "_gen_charts", lambda *args: (None, None, None))

    memo = table(["A | 60%", "B | 40%", "Total | 80%"],
                 "Scenario | Probability", "Scenario Table")
    block = build_linter_block(memo, {}, language="en")
    path = tmp_path / "synthetic_scenario_warning.pdf"
    with language_context("en"):
        assert renderer.build_institutional_memo(memo + "\n" + block, output_path=str(path),
                                                 title_date="Synthetic test")
    with pymupdf.open(path) as document:
        rendered = " ".join(page.get_text() for page in document)
        for page in document:
            if "MEMO LINTER" in page.get_text():
                page.get_pixmap().save(str(tmp_path / "synthetic_scenario_warning.png"))
    (tmp_path / "synthetic_scenario_warning.txt").write_text(rendered, encoding="utf-8")
    assert "MEMO LINTER" in rendered
    assert "declared total" in rendered.lower() and "80%" in rendered and "100%" in rendered
    assert "180%" not in rendered


# --- R03 follow-up (Opus 5.5): a-d of the adversarial re-check -----------------

@pytest.mark.parametrize("label", ["Totale scenari", "Somma", "Tot.", "Total probability",
                                   "**Somma probabilita':**", "TOT", "Totale (arrotondato)",
                                   "Somma ponderata", "Totali", "Total (rounded)"])
def test_total_synonyms_are_not_counted_as_scenarios(label):
    assert _check_scenario_sum(table(["A | 55%", "B | 45%", f"{label} | 100%"])) == []


@pytest.mark.parametrize("label", ["Totale scenari", "Somma", "Tot.", "Total probability"])
def test_total_synonym_still_checks_its_declared_value(label):
    warnings = _check_scenario_sum(table(["A | 55%", "B | 45%", f"{label} | 85%"]))
    assert len(warnings) == 1 and "totale dichiarato 85%" in warnings[0]


@pytest.mark.parametrize("label", ["Totalmente avverso", "Sommario rischi", "Totem", "Tottenham"])
def test_words_that_only_start_like_total_remain_scenarios(label):
    warnings = _check_scenario_sum(table([f"{label} | 35%", "B | 45%", "C | 40%"]))
    assert len(warnings) == 1 and "sommano 120%" in warnings[0]


# Review D1: a total word followed by a non-qualifier is a scenario name.
@pytest.mark.parametrize("rows", [
    ["Bull | 30%", "Base | 50%", "Total loss | 20%"],
    ["Total return bull | 30%", "Base | 50%", "Bear | 20%"],
    ["Rialzo | 30%", "Base | 50%", "Totale perdita | 20%"],
    ["Rialzo | 30%", "Base | 50%", "Somma zero (stallo) | 20%"],
    ["Somma-zero | 30%", "B | 30%", "C | 40%"],
])
def test_scenario_names_starting_with_a_total_word_are_scenarios(rows):
    assert _check_scenario_sum(table(rows)) == []


def test_total_loss_scenario_still_counts_in_a_wrong_sum():
    warnings = _check_scenario_sum(table(["Bull | 40%", "Base | 50%", "Total loss | 25%"]))
    assert len(warnings) == 1 and "sommano 115%" in warnings[0] and "40 + 50 + 25" in warnings[0]


@pytest.mark.parametrize("value", ["—", "n.d.", "-"])
def test_unreadable_total_does_not_switch_off_the_scenario_sum(value):
    warnings = _check_scenario_sum(table(["A | 45%", "B | 35%", f"Totale | {value}"]))
    assert any("sommano 80%" in w for w in warnings), warnings
    assert any(f'totale dichiarato non leggibile ("{value}")' in w for w in warnings), warnings
    assert not any("non applicabile" in w for w in warnings), warnings


def test_unreadable_total_with_correct_scenarios_is_declared_not_hidden():
    warnings = _check_scenario_sum(table(["A | 45%", "B | 55%", "Totale | n.d."]))
    assert len(warnings) == 1 and 'totale dichiarato non leggibile ("n.d.")' in warnings[0]


# Review D5: a total row with an EMPTY probability cell declares nothing.
def test_empty_probability_on_total_row_is_not_noise():
    header = "Scenario | Probabilità | Upside | Contributo EV"
    rows = ["Bull | 30% | +40% | +12%", "Base | 50% | +10% | +5%", "Bear | 20% | -30% | -6%"]
    assert _check_scenario_sum(table(rows + ["**Totale / EV** | | | **+11%**"], header)) == []
    assert _check_scenario_sum(table(["A | 45%", "B | 55%", "Totale |  "])) == []


def test_empty_probability_on_total_row_does_not_hide_a_wrong_sum():
    warnings = _check_scenario_sum(table(["A | 45%", "B | 35%", "Totale |  "]))
    assert len(warnings) == 1 and "sommano 80%" in warnings[0]


# Review D3: a declared total with no readable scenario probability is not a clean table.
def test_total_without_scenario_probabilities_is_not_applicable():
    warnings = _check_scenario_sum(table(["A | n.d.", "B | n.d.", "Totale | 100%"]))
    assert len(warnings) == 1 and "non applicabile" in warnings[0]


# Pre-existing (review): "| | Totale | 100%" without a scenario header column.
def test_total_word_in_second_column_with_empty_first_cell():
    assert _check_scenario_sum(table(["Bull | x | 60%", "Bear | y | 40%", " | Totale | 100%"],
                                     header="Nome | Driver | Probabilità")) == []


def test_unreadable_total_english_message():
    with language_context("en"):
        warnings = _check_scenario_sum(table(["A | 45%", "B | 35%", "Total | n/a"],
                                             "Scenario | Probability", "Scenario Table"))
    assert any("probabilities total 80%" in w for w in warnings), warnings
    assert any("declared total is not readable" in w for w in warnings), warnings


# Levels 2-6, as before T3 (the old regex was not anchored); "#" alone is the memo title.
@pytest.mark.parametrize("hashes", ["##", "###", "####", "#####", "######"])
def test_scenario_heading_levels_two_to_six(hashes):
    memo = table(["A | 65%", "B | 65%"]).replace("## Tabella Scenari", f"{hashes} 10. Tabella Scenari")
    warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "sommano 130%" in warnings[0]


def test_h3_scenario_section_stops_at_next_same_level_heading():
    memo = (table(["A | 55%", "B | 45%"]).replace("## Tabella Scenari", "### Tabella Scenari")
            + "\n### 11. Rischi\n" + table(["X | 65%", "Y | 65%"]).split("\n", 1)[1])
    assert _check_scenario_sum(memo) == []


def test_deeper_heading_inside_scenario_section_stays_inside():
    memo = (table(["A | 55%", "B | 45%"]) + "\n### Sotto-scenario macro\n"
            + table(["X | 65%", "Y | 55%"]).split("\n", 1)[1])
    warnings = _check_scenario_sum(memo)
    assert len(warnings) == 1 and "(tabella 2)" in warnings[0] and "sommano 120%" in warnings[0]


def test_problem_column_is_not_the_probability_column():
    warnings = _check_scenario_sum(table(["A | tassi | 65%", "B | dazi | 65%"],
                                         header="Scenario | Problema chiave | Probabilita"))
    assert len(warnings) == 1 and "sommano 130%" in warnings[0]


@pytest.mark.parametrize("header", ["Scenario | Prob.", "Scenario | Prob", "Scenario | Probability (%)",
                                    "Scenario | Probabilità", "Scenario | %", "Scenario | Probab. %",
                                    "Scenario | Probab", "Scenario | Probabile", "Scenario | Probs",
                                    "Scenario | P (%)", "Scenario | P(%)"])
def test_probability_header_tokens(header):
    warnings = _check_scenario_sum(table(["A | 65%", "B | 65%"], header=header))
    assert len(warnings) == 1 and "130%" in warnings[0]


def test_probability_word_beats_a_looser_prob_header():
    warnings = _check_scenario_sum(table(["A | x | 65%", "B | y | 65%"],
                                         header="Scenario | Probabile esito | Probabilità"))
    assert len(warnings) == 1 and "sommano 130%" in warnings[0]


@pytest.mark.parametrize("probability_header", ["Probabilita", "Prob.", "Probs"])
def test_bare_percent_header_is_only_a_fallback(probability_header):
    warnings = _check_scenario_sum(table(["A | 10% | 65%", "B | 20% | 65%"],
                                         header=f"Scenario | % | {probability_header}"))
    assert len(warnings) == 1 and "65 + 65" in warnings[0]


@pytest.mark.parametrize("language, marker", [("it", "colonna probabilita' non riconosciuta"),
                                              ("en", "probability column not recognised")])
def test_scenario_table_without_probability_column_is_declared(language, marker):
    with language_context(language):
        warnings = _check_scenario_sum(table(["A | tassi | 65%", "B | dazi | 65%"],
                                             header="Scenario | Problema chiave | Peso"))
    assert len(warnings) == 1 and marker in warnings[0]


def test_percent_header_does_not_steal_the_probability_column():
    warnings = _check_scenario_sum(table(["A | +40% | 65%", "B | -10% | 65%"],
                                         header="Scenario | Upside % | Probabilita"))
    assert len(warnings) == 1 and "sommano 130%" in warnings[0]


@pytest.mark.parametrize("rows, expected", [
    (["A | 1.234,5%", "B | 40%"], "non applicabile"),
    (["A | 33,3%", "B | 33.3%", "C | 33,4%"], None),
    (["Totale | 100%", "A | 55%", "B | 45%"], None),
])
def test_cases_that_already_worked_stay_unchanged(rows, expected):
    warnings = _check_scenario_sum(table(rows))
    if expected is None:
        assert warnings == []
    else:
        assert len(warnings) == 1 and expected in warnings[0]


# --- R03 third round (Opus 5.5): side tables, total qualifiers, N7/N9 -------------

MAIN_OK = ["Bull | 30%", "Base | 50%", "Bear | 20%"]


@pytest.mark.parametrize("header, rows", [
    ("Scenario | Trigger | Azione", ["Bull | utili sopra attese | aumentare", "Bear | guidance | ridurre"]),
    ("Scenario | Catalizzatore | Data", ["Bull | Q3 earnings | 2026-11-05", "Bear | FDA | 2027-01"]),
    ("Scenario | Prezzo target | Upside", ["Bull | 180 | +45%", "Base | 140 | +12%", "Bear | 90 | -28%"]),
    ("Scenario | Ricavi 2027 | Margine EBIT %", ["Bull | 12 mld | 35%", "Base | 10 mld | 30%",
                                                 "Bear | 8 mld | 22%"]),
    ("Rischio | Mitigazione", ["Tassi | copertura", "Dazi | fornitori"]),
    ("Scenario | Target | Upside (%)", ["Bull | 180 | 45%", "Bear | 90 | -28%"]),
    # Rule (a): a %-looking side table is not declared when the section has a distribution.
    ("Scenario | Quota ricavi", ["Bull | 30%", "Base | 50%", "Bear | 30%"]),
])
def test_side_tables_next_to_the_distribution_are_not_noise(header, rows):
    side = table(rows, header).split("\n", 1)[1]
    assert _check_scenario_sum(table(MAIN_OK) + "\n" + side) == []
    wrong = table(["Bull | 60%", "Base | 60%"]) + "\n" + side
    warnings = _check_scenario_sum(wrong)
    assert len(warnings) == 1 and "sommano 120%" in warnings[0]


@pytest.mark.parametrize("header", ["Scenario | Peso", "Scenario | Likelihood", "Scenario | Odds",
                                    "Scenario | Chance", "Scenario | Pesatura %", "Scenario | Stima"])
def test_unrecognised_distribution_table_is_still_declared(header):
    warnings = _check_scenario_sum(table(["Bull | 30%", "Base | 50%", "Bear | 30%"], header))
    assert len(warnings) == 1 and "colonna probabilita' non riconosciuta" in warnings[0]


def test_transposed_distribution_is_still_declared():
    warnings = _check_scenario_sum(table(["Probabilità | 30% | 50% | 30%", "Target | 180 | 140 | 90"],
                                         "Metrica | Bull | Base | Bear"))
    assert len(warnings) == 1 and "colonna probabilita' non riconosciuta" in warnings[0]


def test_lone_upside_table_is_not_a_distribution():
    assert _check_scenario_sum(table(["Bull | +45%", "Base | +10%", "Bear | -30%"],
                                     "Scenario | Upside %")) == []


@pytest.mark.parametrize("label", ["Totale dei casi", "Totale casi", "Total check", "Totale ✓",
                                   "Totale =", "Totale di probabilita"])
def test_more_total_qualifiers(label):
    assert _check_scenario_sum(table(["A | 30%", "B | 50%", "C | 20%", f"{label} | 100%"])) == []


@pytest.mark.parametrize("label", ["Total (wipeout)", "Totale (azzeramento)"])
def test_outcome_in_parentheses_keeps_the_row_a_scenario(label):
    assert _check_scenario_sum(table(["Bull | 30%", "Base | 50%", f"{label} | 20%"])) == []


def test_bare_p_header_is_the_probability_column():
    warnings = _check_scenario_sum(table(["A | 65%", "B | 65%"], header="Scenario | P"))
    assert len(warnings) == 1 and "sommano 130%" in warnings[0]


@pytest.mark.parametrize("header, rows", [
    ("Scenario | Peso", ["Bull | 0,3", "Base | 0,5", "Bear | 0,2"]),
    ("Scenario | Likelihood", ["Bull | high", "Base | medium", "Bear | low"]),
])
def test_weight_header_without_percentages_is_still_declared(header, rows):
    warnings = _check_scenario_sum(table(rows, header))
    assert len(warnings) == 1 and "colonna probabilita' non riconosciuta" in warnings[0]


@pytest.mark.parametrize("header, rows", [
    ("Scenario | Margine EBIT %", ["Bull | 35%", "Base | 30%", "Bear | 22%"]),
    ("Scenario | Variazione", ["Bull | +30%", "Base | +50%", "Bear | -10%"]),
    ("Scenario | Crescita", ["Bull | 8%", "Base | 12%", "Bear | 5%"]),
    ("Scenario | Quota estero", ["Base | 70%", "Bear | n.d."]),
])
def test_lone_non_distribution_tables_are_silent(header, rows):
    assert _check_scenario_sum(table(rows, header)) == []
