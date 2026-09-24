"""Read PDF text widgets separately from page text; never flatten or fill blanks."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import re
from xml.etree import ElementTree


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def extract_pdf_form_fields(raw):
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(raw))
    acro = reader.trailer['/Root'].get('/AcroForm')
    if acro is None:
        return None
    acro = acro.get_object()
    tree = reader.get_fields() or {}
    xfa = acro.get('/XFA')
    dataset = None
    if xfa is not None:
        if not isinstance(xfa, list) or len(xfa) % 2 or xfa[::2].count('datasets') != 1:
            raise ValueError('PDF XFA datasets absent or ambiguous')
        data = xfa[xfa.index('datasets') + 1].get_object().get_data()
        if len(data) > 5_000_000 or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
            raise ValueError('PDF XFA dataset unsupported or unsafe')
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError as exc:
            raise ValueError('PDF XFA dataset malformed') from exc
        dataset = {}
        for node in root.iter():
            if len(node) == 0:
                dataset.setdefault(node.tag.split('}')[-1], []).append(node.text or '')
    rows = []
    for page_number, page in enumerate(reader.pages, 1):
        for reference in page.get('/Annots', []):
            widget = reference.get_object()
            if widget.get('/Subtype') != '/Widget' or int(widget.get('/F', 0)) & (1 | 2 | 32):
                continue  # invisible, hidden or no-view widgets cannot prove displayed facts
            node, parts, seen, value, kind = widget, [], set(), None, None
            has_value = False
            while node is not None:
                if id(node) in seen or len(seen) >= 32:
                    raise ValueError('PDF field parent cycle or unsupported depth')
                seen.add(id(node))
                if node.get('/T') is not None:
                    parts.append(str(node['/T']))
                if kind is None:
                    kind = node.get('/FT')
                if not has_value and '/V' in node:
                    value, has_value = node['/V'], True
                parent = node.get('/Parent')
                node = parent.get_object() if parent is not None else None
            if kind != '/Tx':
                continue
            name = '.'.join(reversed(parts))
            if (not name or name not in tree or tree[name].get('/FT') != '/Tx'
                    or tree[name].get('/V') != value or value is not None and not isinstance(value, str)):
                raise ValueError('PDF widget differs from its qualified AcroForm field')
            leaf = re.sub(r'\[\d+\]$', '', parts[0])
            rows.append({'name': name, 'field': leaf, 'page': page_number,
                         'value': str(value) if value is not None else None,
                         'xfa_values': deepcopy(dataset.get(leaf, [])) if dataset is not None else None})
    if not rows:
        return None
    payload = {'format': 'pdf_text_widgets_v1', 'source_document_sha256': sha256(raw).hexdigest(),
               'xfa_present': dataset is not None, 'fields': rows}
    return {**payload, 'sha256': _hash(payload)}


def form_field(document, name, page, *, allow_blank=False):
    # Optional observations may report a blank, but still verify packet and XFA.
    """Return one pinned field on the named physical page, with XFA agreement."""
    packet = document.get('pdf_form_fields')
    if not isinstance(packet, dict) or set(packet) != {'format', 'source_document_sha256', 'xfa_present', 'fields', 'sha256'}:
        raise ValueError('PDF form evidence packet missing or malformed')
    payload = {k: v for k, v in packet.items() if k != 'sha256'}
    if (packet['format'] != 'pdf_text_widgets_v1' or packet['source_document_sha256'] != document['document_sha256']
            or packet['sha256'] != _hash(payload) or type(packet['xfa_present']) is not bool
            or not isinstance(packet['fields'], list)):
        raise ValueError('PDF form evidence hash, format or source differs')
    selected = []
    for row in packet['fields']:
        if not isinstance(row, dict) or set(row) != {'name', 'field', 'page', 'value', 'xfa_values'}:
            raise ValueError('PDF form field evidence malformed')
        if (not isinstance(row['name'], str) or not row['name']
                or re.sub(r'\[\d+\]$', '', row['name'].rsplit('.', 1)[-1]) != row['field']
                or type(row['page']) is not int or not 1 <= row['page'] <= len(document['page_references'])
                or row['value'] is not None and not isinstance(row['value'], str)):
            raise ValueError('PDF field name, value or physical page invalid')
        if row['field'] == name and row['page'] == page:
            selected.append(row)
    if len(selected) != 1:
        raise ValueError(name + ': unique displayed PDF field absent or duplicated')
    row = selected[0]
    value = row['value']
    if not isinstance(value, str) or (not value.strip() and not allow_blank):
        raise ValueError(name + ': PDF field blank; no zero inferred')
    if packet['xfa_present'] and (not isinstance(row['xfa_values'], list) or not row['xfa_values']
            or any(not isinstance(v, str) or v.strip() != value.strip() for v in row['xfa_values'])):
        raise ValueError(name + ': AcroForm and XFA values absent or conflicting')
    return value.strip(), {**deepcopy(row), 'source_document_sha256': document['document_sha256'],
                          'packet_sha256': packet['sha256'], 'basis': 'original_pdf_text_widget'}
