"""Mandate presentation: schema and every policy branch retain the same values."""
from copy import deepcopy
import itertools

from bellomberg.core import mandato_pm as mp
from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload
from bellomberg.core.presentation import error_text, join_messages


def test_schema_descriptions_are_bilingual_without_changing_the_47_field_contracts():
    with language_context('it'):
        schema = mp.descrizione_campi()
    english = render_payload(schema, language='en')
    assert len(schema) == len(english) == 47
    assert english['orizzonte_anni']['descrizione'] == 'Average holding period of a position.'
    assert schema['orizzonte_anni']['descrizione'] == 'Orizzonte medio di detenzione di una posizione.'
    for name, original in mp.CAMPI.items():
        for key in original:
            if key not in ('descrizione', 'unita'):
                assert schema[name][key] == english[name][key] == original[key]


def test_new_preview_captures_language_preserves_original_notes_and_does_not_change_fingerprint():
    values = deepcopy(mp.ESEMPIO)
    original = 'Questa nota originale contiene rischio, cash e 1,25: α.'
    values['note']['note_per_il_comitato'] = original
    values['profilo']['broker'] = 'Intermediario originale'
    with language_context('it'):
        it = mp.anteprima(values)
    with language_context('en'):
        en = mp.anteprima(values)
    assert it['impronta'] == en['impronta'] == mp.impronta(values)
    assert en['output_language'] == 'en' and it['output_language'] == 'it'
    assert 'PM MANDATE' in en['testo'] and 'MANDATO DEL PM' in it['testo']
    assert 'RISK ACCEPTED' in en['testo'] and 'RISCHIO ACCETTATO' not in en['testo']
    assert original in en['testo'] and original in it['testo']
    assert 'Intermediario originale' in en['testo']


def test_policy_vectors_have_identical_normalized_values_and_field_errors_in_both_languages():
    for policy, enabled, pair in itertools.product(('prudente', 'neutra', 'aggressiva'), (True, False), (0, 1, 2)):
        values = deepcopy(mp.ESEMPIO)
        values['cassa']['politica_impiego'] = policy
        values['opzioni']['opzioni_abilitate'] = enabled
        values['opzioni']['strumenti_ammessi'] = ['put_hedge'] if enabled else []
        values['disciplina']['pair_trade_per_memo'] = pair
        results = []
        for language in ('it', 'en'):
            with language_context(language):
                normalized, errors = mp.valida(values)
                results.append((normalized, [str(e).split(':')[0] for e in errors], mp.sizing_params(normalized)))
        assert results[0] == results[1]
    values['rischio']['var99_1g_pct'] = 'bad input'
    with language_context('en'):
        _, errors = mp.valida(values)
    assert any('var99_1g_pct: expected a number' in e for e in errors)


def test_cached_invalid_mandate_error_retains_nested_language_variants():
    values = deepcopy(mp.ESEMPIO)
    values['rischio']['var99_1g_pct'] = 'bad input'
    with language_context('it'):
        _, errors = mp.valida(values)
        failure = mp.MandatoMancante('synthetic-profile.json', 'incompleto',
                                    join_messages('; ', errors), ['var99_1g_pct'])
        cached = error_text(failure)
    english = render_payload(cached, language='en')
    assert 'expected a number' in english
    assert 'atteso un numero' not in english
    assert 'synthetic-profile.json' in english
