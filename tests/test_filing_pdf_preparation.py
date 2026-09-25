"""PDF Filing Diff evidence survives the common preparation boundary."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from io import BytesIO
import re

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


URL = 'https://issuer.example/synthetic/report.pdf'


def pdf_report(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    lines = ['Synthetic Industries SE', 'Half-year report', 'Consolidated financial statements',
             'For the six-month period ended June 30, 2025', 'Risk factors',
             'Business exposure is described here.', 'Management discussion']
    stream.set_data(('BT /F1 12 Tf 72 720 Td 16 TL ' +
        ' '.join(f'({line}) Tj T*' for line in lines) + ' ET').encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    path = tmp_path / 'synthetic.pdf'
    path.write_bytes(output.getvalue())
    return path


def profile():
    return {'ticker': 'SYNTH.DE', 'emittente_id': 'EMITTENTE:SYNTHETIC-INDUSTRIES',
        'lingua': 'en', 'tipo': 'semestrale', 'perimetro': 'consolidato',
        'verifica': {'emittente': r'Synthetic Industries SE', 'lingua': r'financial statements',
            'tipo': r'Half-year report', 'perimetro': r'Consolidated financial statements',
            'periodo': r'For the (?P<mesi>[a-z]+)-month period ended (?P<fine>[A-Za-z]+ \d{1,2}, \d{4})'},
        'sezioni': {'rischi': {'inizio': 'Risk factors', 'fine': 'Management discussion'}}}


def verified(tmp_path):
    from bellomberg.market_data.filing_verifica import verifica_documento
    path = pdf_report(tmp_path)
    result = verifica_documento(path, url=URL, profilo=profile())
    assert result['stato'] == 'ok', result
    return path, result['documento']


def acquired(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    path, doc = verified(tmp_path)
    candidate = {'stato': 'verificato', 'path': str(path), 'sha256': doc['sha256'],
        'url': URL, 'filed_date': '2025-08-05', 'metadati': doc['metadati'],
        'filing_verification': doc['filing_verification']}
    result = collect_documents('SYNTH.DE', as_of='2025-09-01', archive_root=tmp_path,
        filing_results=[{'ticker': 'SYNTH.DE', 'candidati': [candidate]}],
        catalog=lambda _: {'stato': 'non_disponibile', 'documenti': [], 'motivi': ['No SEC coverage']})
    assert len(result['documents']) == 1, result
    return result['documents'][0]


def test_pdf_verifier_emits_replayable_profile_evidence(tmp_path):
    _, doc = verified(tmp_path)
    proof = doc['filing_verification']
    assert proof['schema'] == 'filing_pdf_v1'
    assert proof['rules'] == profile()['verifica']
    assert proof['metadata'] == doc['metadati']
    assert proof['proofs'] == doc['prove_verifica']
    assert proof['identity_basis'] == 'curated_filing_profile'
    assert proof['security_identity_verified'] is False


def test_pipeline_carries_pdf_evidence_without_inventing_publication(tmp_path, monkeypatch):
    from bellomberg.market_data import filing_pipeline as pipeline
    path = pdf_report(tmp_path)
    monkeypatch.setattr(pipeline, '_raccogli', lambda *_: ([{'fonte': 'IR', 'url': URL,
        'catalogo': {}, 'hosts': {'issuer.example'}, 'motivo_preliminare': None}], [], [], {}))
    monkeypatch.setattr(pipeline, '_scarica', lambda *_: {'path': str(path),
        'sha256': sha256(path.read_bytes()).hexdigest()})
    result = pipeline.esegui_profilo(profile(), archivio=tmp_path, oggi='2025-09-01')
    candidate = result['candidati'][0]
    assert candidate['stato'] == 'verificato'
    assert candidate['filing_verification']['schema'] == 'filing_pdf_v1'
    assert not candidate.get('filed_date')


def test_common_collector_and_catalog_keep_pdf_evidence_and_page_hashes(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog
    doc = acquired(tmp_path)
    assert doc['metadata']['report_start'] == '2025-01-01'
    assert doc['metadata']['report_date'] == '2025-06-30'
    assert not doc['metadata'].get('accession') and not doc['metadata'].get('form')
    assert len(doc['page_references']) == 1
    catalog, issues, provenance = _catalog([doc], date(2025, 9, 1))
    assert not issues, issues
    assert catalog[doc['id']]['filing_verification'] == doc['filing_verification']
    assert provenance[doc['id']]['filing_verification'] == doc['filing_verification']
    assert provenance[doc['id']]['document_sha256_status'] == 'declared_not_rechecked'


@pytest.mark.parametrize('mutation', ['issuer', 'period', 'start', 'proof', 'rule', 'page', 'missing'])
def test_catalog_rejects_inconsistent_pdf_evidence(tmp_path, mutation):
    from bellomberg.valuation.input_preparation import _catalog
    doc = deepcopy(acquired(tmp_path))
    if mutation == 'issuer':
        doc['metadata']['emittente_id'] = 'EMITTENTE:OTHER'
    elif mutation == 'period':
        doc['metadata']['report_date'] = '2025-03-31'
    elif mutation == 'start':
        doc['filing_verification']['metadata']['periodo_inizio'] = '2025-04-01'
    elif mutation == 'proof':
        doc['filing_verification']['proofs']['emittente']['inizio'] += 1
    elif mutation == 'rule':
        doc['filing_verification']['rules']['emittente'] = 'Other Industries'
    elif mutation == 'page':
        doc.pop('page_references')
    else:
        doc.pop('filing_verification')
    catalog, issues, _ = _catalog([doc], date(2025, 9, 1))
    assert not catalog and issues
    assert issues[0]['code'] == 'invalid_filing_verification'


def test_collector_rejects_claimed_metadata_not_proved_by_archived_pdf(tmp_path):
    from bellomberg.valuation.valuation_sources import collect_documents
    path, doc = verified(tmp_path)
    doc['metadati']['periodo_fine'] = '2025-03-31'
    candidate = {'stato': 'verificato', 'path': str(path), 'sha256': doc['sha256'],
        'url': URL, 'filed_date': '2025-08-05', 'metadati': doc['metadati'],
        'filing_verification': doc['filing_verification']}
    result = collect_documents('SYNTH.DE', as_of='2025-09-01', archive_root=tmp_path,
        filing_results=[{'ticker': 'SYNTH.DE', 'candidati': [candidate]}],
        catalog=lambda _: {'stato': 'ok', 'documenti': [], 'motivi': []})
    assert result['documents'] == []
    assert 'verification' in str(result['issues'])


@pytest.mark.parametrize('field,value', [('report_start', '2025-04-01'),
                                       ('filing_verification', 'unknown_schema')])
def test_collector_never_overwrites_conflicting_verification_metadata(tmp_path, field, value):
    from bellomberg.valuation.valuation_sources import collect_documents
    path, doc = verified(tmp_path)
    doc['metadati'][field] = value
    candidate = {'stato': 'verificato', 'path': str(path), 'sha256': doc['sha256'],
        'url': URL, 'filed_date': '2025-08-05', 'metadati': doc['metadati'],
        'filing_verification': doc['filing_verification']}
    result = collect_documents('SYNTH.DE', as_of='2025-09-01', archive_root=tmp_path,
        filing_results=[{'ticker': 'SYNTH.DE', 'candidati': [candidate]}],
        catalog=lambda _: {'stato': 'ok', 'documenti': [], 'motivi': []})
    assert result['documents'] == []
    assert 'verification' in str(result['issues'])


def test_invalid_filing_evidence_stops_the_actual_preparer_before_paid_proposer(tmp_path):
    from bellomberg.valuation.input_preparation import prepare_method_inputs
    from test_input_preparation import _bundle
    doc = acquired(tmp_path)
    doc['filing_verification']['proofs']['periodo']['testo'] = 'Fabricated period'
    result = prepare_method_inputs(_bundle(), documents=[doc],
        propose=lambda *_: pytest.fail('invalid PDF evidence reached a paid proposer'))
    assert result['status'] == 'incomplete' and result['proposal'] is None
    assert any(x['code'] == 'invalid_filing_verification' for x in result['issues'])


@pytest.mark.parametrize('text,pattern,start,end', [
    ('period from 1 January to 30 June 2025',
     r'period from (?P<inizio>\d+ \w+) to (?P<fine>\d+ \w+) (?P<anno>\d{4})', '2025-01-01', '2025-06-30'),
    ('1/1-30/6 /2025',
     r'(?P<giorno_inizio>\d+)/(?P<mese_inizio>\d+)-(?P<giorno_fine>\d+)/(?P<mese_fine>\d+) /(?P<anno>\d{4})',
     '2025-01-01', '2025-06-30')])
def test_explicit_common_year_range_preserves_its_date_derivation(text, pattern, start, end):
    from bellomberg.market_data.filing_verifica import _periodo_testuale
    a, b, proof = _periodo_testuale(re.fullmatch(pattern, text), 'semestrale')
    assert (a.isoformat(), b.isoformat()) == (start, end)
    assert proof['regola'] == 'anno_comune_esplicito'
    assert proof['anno'] == 2025


@pytest.mark.parametrize('text', ['1/1-30/6/25', '1/13-30/6/2025', '31/2-30/6/2025', '1/7-30/6/2025'])
def test_common_year_cannot_guess_century_repair_date_or_infer_previous_year(text):
    from bellomberg.market_data.filing_verifica import _periodo_testuale
    pattern = r'(?P<giorno_inizio>\d+)/(?P<mese_inizio>\d+)-(?P<giorno_fine>\d+)/(?P<mese_fine>\d+)/(?P<anno>\d+)'
    with pytest.raises(ValueError):
        _periodo_testuale(re.fullmatch(pattern, text), 'semestrale')


def test_numeric_month_order_is_not_inferred_from_generic_start_end_names():
    from bellomberg.market_data.filing_verifica import _periodo_testuale
    pattern = r'(?P<inizio>[\d/]+)-(?P<fine>[\d/]+)/(?P<anno>\d{4})'
    with pytest.raises(ValueError):
        _periodo_testuale(re.fullmatch(pattern, '1/1-6/30/2025'), 'semestrale')
