"""Real synthetic PDF widgets, not issuer data or hand-filled method records."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from bellomberg.valuation.document_evidence import pdf_document
from bellomberg.valuation.regulatory_evidence import normalize_regulatory_pdf


def _pdf(tmp_path, *, missing=False, printed_date='', supplemental=False):
    from reportlab.pdfgen import canvas
    path = tmp_path / 'synthetic-parent.pdf'
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for y, text in [(760, 'FR Y-9SP'), (740, 'Board of Governors of the Federal Reserve System'),
                    (720, 'Parent Company Only Financial Statements for Small Holding Companies'),
                    (680, 'Date of Report:' + printed_date), (590, 'Legal Title of Holding Company (RSSD 9017)')]:
        c.drawString(20, y, text)
    c.acroForm.textfield(name='TEXT9999[0]', value='June 30, 2026', x=260, y=675, width=190, height=18)
    c.acroForm.textfield(name='NM_LGL[0]', value='Synthetic Parent', x=260, y=615, width=190, height=18)
    c.showPage()
    for y, text in [(760, 'FR Y-9SP'), (740, 'Schedule SC - Balance Sheet'),
                    (720, 'Dollar Amounts in Thousands BHSP Amount'), (700, 'Assets'),
                    (610, 'Liabilities and Equity Capital')]:
        c.drawString(20, y, text)
    rows = [
        (680, 'BHSP5993', 'a. Balances with subsidiary or affiliated depository institutions', '1.a.', '8000'),
        (650, 'BHSP0010', 'b. Balances with unrelated depository institutions', '1.b.', '' if missing else '2000'),
        (590, 'BHSP3283', 'a. Perpetual preferred stock (including related surplus)', '16.a.', '0'),
        (560, 'BHSP3210', 'f. Total equity capital (sum of items 16.a through 16.e)', '16.f.', '12000')]
    if supplemental:
        rows.extend([(500, 'BHSP3239', 'a. Equity investment', '4.a.', '9000'),
                     (470, 'BHSP0088', 'a. Equity investment', '5.a.', '')])
    c.setFont('Helvetica', 7)
    for y, code, label, item, value in rows:
        c.drawString(20, y, label + ' ........ ' + code[4:] + ' ' + item)
        c.acroForm.textfield(name=code + '[0]', value=value, x=490, y=y - 5, width=70, height=16)
    c.save()
    return path


def _document(tmp_path, **kwargs):
    path = _pdf(tmp_path, **kwargs)
    rawsha = sha256(path.read_bytes()).hexdigest()
    return pdf_document(path, archive_root=tmp_path, cutoff='2026-09-23', retrieval={
        'url': 'https://example.test/parent.pdf', 'document_sha256': rawsha,
        'retrieved_at': '2026-09-23T10:00:00Z'})


def _normalize(doc):
    return normalize_regulatory_pdf(doc, expected_form='FR Y-9SP',
        expected_entity='Synthetic Parent', expected_report_date='2026-06-30')


def test_original_widget_values_are_read_without_fabricating_page_text(tmp_path):
    doc = _document(tmp_path)
    assert 'Synthetic Parent' not in doc['text']  # PDF page stream omits widgets
    result = _normalize(doc)
    assert result['status'] == 'ready', result['issues']
    facts = json.loads(result['documents'][0]['text'])['facts']
    assert [(f['concept'], f['value']) for f in facts] == [
        ('BHSP5993', 8000), ('BHSP0010', 2000), ('BHSP3283', 0), ('BHSP3210', 12000)]
    assert all(f['proof']['form_field']['page'] == 2 for f in facts)
    assert all(f['proof']['form_field']['source_document_sha256'] == doc['id'] for f in facts)
    assert result['documents'][0]['published_at'] is None


def test_blank_form_field_does_not_turn_into_zero(tmp_path):
    result = _normalize(_document(tmp_path, missing=True))
    assert result['status'] == 'incomplete' and not result['documents']
    assert 'BHSP0010' in result['issues'][0]['reason']


@pytest.mark.parametrize('with_xfa', [False, True])
def test_native_supplemental_widgets_preserve_blank_without_suppressing_other_balances(tmp_path, with_xfa):
    doc = _document(tmp_path, supplemental=True)
    if with_xfa:
        from bellomberg.valuation.pdf_form_evidence import _hash
        packet = doc['pdf_form_fields']
        packet['xfa_present'] = True
        for row in packet['fields']:
            row['xfa_values'] = [row['value']]
        packet['sha256'] = _hash({k: v for k, v in packet.items() if k != 'sha256'})
    result = _normalize(doc)
    assert result['status'] == 'ready', result['issues']
    body = json.loads(result['documents'][0]['text'])
    fact = next(row for row in body['facts'] if row['concept'] == 'BHSP3239')
    assert fact['value'] == 9000 and fact['proof']['form_field']['value'] == '9000'
    assert body['unavailable_fields']['BHSP0088'] == 'blank_amount'
    assert not any(row['concept'] == 'BHSP0088' for row in body['facts'])


def test_blank_supplemental_widget_still_requires_matching_xfa(tmp_path):
    from bellomberg.valuation.pdf_form_evidence import _hash
    doc = _document(tmp_path, supplemental=True)
    packet = doc['pdf_form_fields']
    packet['xfa_present'] = True
    for row in packet['fields']:
        row['xfa_values'] = [row['value']]
        if row['field'] == 'BHSP0088':
            row['xfa_values'] = ['100']
    packet['sha256'] = _hash({k: v for k, v in packet.items() if k != 'sha256'})
    result = _normalize(doc)
    assert result['status'] == 'incomplete'
    assert 'AcroForm and XFA' in result['issues'][0]['reason']


def test_printed_date_cannot_conflict_with_field_date(tmp_path):
    result = _normalize(_document(tmp_path, printed_date=' December 31, 2025'))
    assert result['status'] == 'incomplete' and 'printed report date differs' in result['issues'][0]['reason']


def test_source_catalog_recompiles_original_form_fields(tmp_path):
    from bellomberg.valuation.input_preparation import _catalog, _day
    doc = _document(tmp_path)
    result = _normalize(doc)
    assert result['status'] == 'ready', result['issues']
    normalized = result['documents'][0]
    catalog, issues, _ = _catalog([doc, normalized], _day('2026-09-23'))
    assert not issues and normalized['id'] in catalog
    altered = deepcopy(normalized)
    body = json.loads(altered['text'])
    body['facts'][0]['value'] += 1
    altered['text'] = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    altered['sha256'] = sha256(altered['text'].encode()).hexdigest()
    catalog, issues, _ = _catalog([doc, altered], _day('2026-09-23'))
    assert issues and altered['id'] not in catalog


@pytest.mark.parametrize('change', ['stale_hash', 'source', 'name', 'page', 'duplicate', 'entity', 'date', 'non_numeric', 'xfa_missing', 'xfa_conflict'])
def test_ambiguous_or_conflicting_field_evidence_is_rejected(tmp_path, change):
    from bellomberg.valuation.pdf_form_evidence import _hash
    doc = _document(tmp_path)
    packet = doc['pdf_form_fields']
    rows = {row['field']: row for row in packet['fields']}
    if change == 'source': packet['source_document_sha256'] = '0' * 64
    elif change == 'name': rows['BHSP5993']['name'] = 'BHSP0010[0]'
    elif change == 'page': rows['BHSP5993']['page'] = 1
    elif change == 'duplicate': packet['fields'].append(deepcopy(rows['BHSP5993']))
    elif change == 'entity': rows['NM_LGL']['value'] = 'Different Parent'
    elif change == 'date': rows['TEXT9999']['value'] = 'December 31, 2025'
    elif change == 'non_numeric': rows['BHSP5993']['value'] = 'N/A'
    elif change == 'stale_hash': rows['BHSP5993']['value'] = '9000'
    else:
        packet['xfa_present'] = True
        for row in packet['fields']: row['xfa_values'] = [row['value']]
        rows['BHSP5993']['xfa_values'] = [] if change == 'xfa_missing' else ['9000']
    if change != 'stale_hash':
        packet['sha256'] = _hash({k: v for k, v in packet.items() if k != 'sha256'})
    result = _normalize(doc)
    assert result['status'] == 'incomplete' and not result['documents']


@pytest.mark.parametrize('mode', ['conflict', 'malformed', 'entities'])
def test_real_xfa_dataset_is_compared_with_native_pdf_fields(tmp_path, mode):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject, TextStringObject
    path = _pdf(tmp_path)
    writer = PdfWriter(clone_from=PdfReader(path))
    fields = writer.get_fields()
    xml = '<datasets>' + ''.join('<' + name.split('[')[0] + '>' +
        ('9999' if name.startswith('BHSP5993') else field['/V']) + '</' + name.split('[')[0] + '>'
        for name, field in fields.items()) + '</datasets>'
    if mode == 'malformed': xml = '<datasets>'
    if mode == 'entities': xml = '<!DOCTYPE datasets [<!ENTITY value "9999">]><datasets/>'
    stream = DecodedStreamObject()
    stream.set_data(xml.encode())
    writer.root_object['/AcroForm'].get_object()[NameObject('/XFA')] = ArrayObject([
        TextStringObject('datasets'), writer._add_object(stream)])
    changed = tmp_path / 'xfa-parent.pdf'
    writer.write(changed)
    kwargs = dict(archive_root=tmp_path, cutoff='2026-09-23', retrieval={
        'url': 'https://example.test/parent.pdf', 'document_sha256': sha256(changed.read_bytes()).hexdigest(),
        'retrieved_at': '2026-09-23T10:00:00Z'})
    if mode != 'conflict':
        with pytest.raises(ValueError, match='PDF XFA dataset'):
            pdf_document(changed, **kwargs)
        return
    doc = pdf_document(changed, **kwargs)
    result = _normalize(doc)
    assert result['status'] == 'incomplete' and 'AcroForm and XFA' in result['issues'][0]['reason']
