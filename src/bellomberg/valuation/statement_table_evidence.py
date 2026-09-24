"""Printed consolidated statements with dated columns and original cell proofs.

This bounded HTML reader preserves missing/ambiguous cells. It does not create
XBRL tags, estimates, complete working-capital bridges or valuation records.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import calendar
import json
import re

from .preparation_exhibits import _sec_parts

NORMALIZER = 'statement_tables_v1'
PREFIX = 'statement-tables-'
TAXONOMY = 'reported-statement'
_HEADING = re.compile(r'CONSOLIDATED\s+(?:CONDENSED\s+INTERIM\s+)?'
    r'(INCOME\s+STATEMENT\s*S|STATEMENT\s*S\s+OF\s+(?:INCOME|FINANCIAL\s+POSITION|CASH\s+FLOWS)|BALANCE\s+SHEETS)\s*$', re.I)
_MONTHS = 'January February March April May June July August September October November December'.split()
_DAY = r'(' + '|'.join(_MONTHS) + r')\s+(\d{1,2}),?'
_DURATION = re.compile(r'(?:For the )?(Year|Three-month period|Six-month period|Nine-month period|Twelve-month period) ended '+_DAY+r'\s*$', re.I)
_INSTANT = re.compile(r'(?:At |As of )?'+_DAY+r'\s+(\d{4})$', re.I)
_UNITS = re.compile(r'all amounts in (thousands|millions) of (U\.S\. dollars|euros|pounds sterling), unless otherwise stated', re.I)
_SECTIONS = ('Current assets', 'Non-current assets', 'Current liabilities', 'Non-current liabilities')
_NONMONETARY = re.compile(r'\b(number|weighted|per share|per ADS|percentage|margin|ratio)\b|%', re.I)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identity(source):
    meta = source['metadata']; cik, accession, _ = _sec_parts(source['url'])
    on = date.fromisoformat(meta['report_date'])
    if (meta.get('emittente_id') != 'CIK:' + cik.zfill(10)
            or meta.get('accession', '').replace('-', '') != accession
            or meta.get('form') not in ('6-K', '6-K/A', '20-F', '20-F/A', '10-K', '10-Q')
            or not isinstance(meta.get('issuer'), str) or not meta['issuer'].strip()
            or on.isoformat() != meta['report_date'] or on > date.fromisoformat(source['published_at'])
            or source['sha256'] != sha256(source['text'].encode()).hexdigest()
            or not re.fullmatch(r'[0-9a-f]{64}', source['id']) or source['document_sha256'] != source['id']):
        raise ValueError('statement source identity, period or text hash differs')
    return meta, cik.zfill(10), accession


def extract_statement_packet(source, raw):
    """Acquire layout from the same hash-verified bytes as the primary text."""
    from bs4 import BeautifulSoup, NavigableString
    _identity(source)
    if sha256(raw).hexdigest() != source['document_sha256']:
        raise ValueError('statement bytes differ from verified source')
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    if estrai_testo('statement.html', contenuto=raw).get('testo') != source['text']:
        raise ValueError('statement text differs from original HTML extraction')
    soup = BeautifulSoup(raw, 'html.parser'); tables = []
    for index, table in enumerate(soup.find_all('table')):
        if table.find_parent('table'):
            continue
        preceding, length = [], 0
        for node in table.previous_elements:
            if isinstance(node, NavigableString) and node.find_parent(['script', 'style']) is None:
                preceding.append(str(node)); length += len(str(node))
                if length > 2400:
                    break
        context = ' '.join(' '.join(reversed(preceding)).split())
        if not _HEADING.search(context):
            continue
        rows = []
        for tr in table.find_all('tr'):
            if tr.find_parent('table') is table:
                rows.append([{'text': ' '.join(cell.get_text(' ', strip=True).split()),
                              'colspan': cell.get('colspan', '1'), 'rowspan': cell.get('rowspan', '1')}
                             for cell in tr.find_all(['td', 'th'], recursive=False)])
        tables.append({'index': index, 'context': context, 'rows': rows})
    packet = {'format': NORMALIZER, 'source_document_sha256': source['id'],
              'source_text_sha256': source['sha256'], 'tables': tables}
    packet['sha256'] = sha256(_json(packet).encode()).hexdigest()
    return packet


def _grid(rows):
    """Honor spans; never align columns by deleting blank cells."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ValueError('unsupported statement row count')
    occupied, result = {}, []
    for row_index, row in enumerate(rows):
        cells, column = [], 0
        for index, cell in enumerate(row):
            while (row_index, column) in occupied:
                column += 1
            width, height = int(cell['colspan']), int(cell['rowspan'])
            if not 1 <= width <= 128 or not 1 <= height <= 50 or column + width > 256:
                raise ValueError('unsupported statement cell span')
            item = {**cell, 'column': column, 'end_column': column + width, 'cell_index': index}
            for y in range(row_index, row_index + height):
                for x in range(column, column + width):
                    if (y, x) in occupied:
                        raise ValueError('overlapping statement cells')
                    occupied[y, x] = item
            cells.append(item); column += width
        result.append(cells)
    return result


def _period(month, day, year, months=None):
    end = date(int(year), [m.lower() for m in _MONTHS].index(month.lower()) + 1, int(day))
    if months is None:
        return {'end': end.isoformat()}
    if end.day != calendar.monthrange(end.year, end.month)[1]:
        raise ValueError('calendar-month duration requires an explicit month-end')
    first = end.year * 12 + end.month - months
    return {'start': date(first // 12, first % 12 + 1, 1).isoformat(), 'end': end.isoformat()}


def _columns(rows, role):
    columns, last_header = [], -1
    for row_index, row in enumerate(rows[:3]):
        for cell in row:
            instant, duration = _INSTANT.fullmatch(cell['text']), _DURATION.fullmatch(cell['text'])
            if 'ended' in cell['text'].lower() and not duration:
                raise ValueError('unsupported statement duration header')
            if instant and role == 'balance':
                columns.append((cell['column'], cell['end_column'], _period(*instant.groups())))
                last_header = max(last_header, row_index)
            elif duration and role != 'balance':
                count = {'year': 12, 'three-month period': 3, 'six-month period': 6,
                         'nine-month period': 9, 'twelve-month period': 12}[duration[1].lower()]
                years = [c for c in rows[row_index + 1] if cell['column'] <= c['column'] < cell['end_column']
                         and re.fullmatch(r'20\d{2}', c['text'])]
                if not years:
                    raise ValueError('duration header has no explicitly aligned years')
                for i, year in enumerate(years):
                    right = years[i + 1]['column'] if i + 1 < len(years) else cell['end_column']
                    columns.append((year['column'], right, _period(duration[2], duration[3], year['text'], count)))
                last_header = max(last_header, row_index + 1)
    if not columns or any(a[0] < b[1] and b[0] < a[1] for i, a in enumerate(columns) for b in columns[i+1:]):
        raise ValueError('missing or overlapping explicit statement periods')
    return columns, last_header


def _number(cell, row, *, exact=False):
    text = cell['text'].replace(' ', '')
    if text.startswith('(') and not text.endswith(')'):
        following = [c for c in row if c['column'] == cell['end_column']]
        if following and following[0]['text'] == ')':
            text += ')'
    if not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?|\((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\)', text):
        return None  # Dashes and blanks are not zero observations.
    value = Decimal(text.replace(',', '').strip('()'))
    if text.startswith('('):
        value = value.copy_negate()
    return value if exact else int(value) if value == value.to_integral_value() else float(value)


def _section_subtotals(rows, columns):
    """Resolve a terminal line/subtotal pair only from a complete section sum."""
    resolved = {}
    for start, heading in enumerate(rows):
        if not heading or heading[0]['text'] not in _SECTIONS:
            continue
        section, block, boundary = heading[0]['text'], [], None
        for index in range(start + 1, len(rows)):
            row = rows[index]
            label = row[0]['text'] if row else ''
            if label in (*_SECTIONS, 'ASSETS', 'EQUITY', 'LIABILITIES') or label.startswith('Total '):
                boundary = index
                break
            if any(c['text'] for c in row):
                block.append(index)
        if boundary is None or len(block) < 2:
            continue
        for left, right, _ in columns:
            if any(c['text'] for c in heading if left <= c['column'] < right):
                continue
            terms, pairs = [], []
            for index in block:
                row = rows[index]
                if row[0]['column'] != 0 or not row[0]['text'] or _NONMONETARY.search(row[0]['text']):
                    break
                cells = [c for c in row if c['column'] < right and c['end_column'] > left]
                if any(c['column'] < left or c['end_column'] > right for c in cells):
                    break
                values = [(c, _number(c, row, exact=True)) for c in cells]
                numbers = [(c, n) for c, n in values if n is not None]
                # Only an adjacent closing parenthesis may accompany a number.
                if any(c['text'] and n is None and not (c['text'] == ')' and any(
                        p['end_column'] == c['column'] and p['text'].startswith('(')
                        and not p['text'].endswith(')') for p, _ in numbers)) for c, n in values):
                    break
                if len(numbers) != (2 if index == block[-1] else 1):
                    break
                pairs.append(numbers[0])
                c, _ = numbers[0]
                terms.append({'row_index': index, 'cell_index': c['cell_index'], 'column': c['column'],
                              'cell_text': c['text'], 'value': _number(c, row)})
            else:
                cell, _ = pairs[-1]
                subtotal, total = numbers[1]
                if (any(c['column'] != cell['column'] for c, _ in pairs)
                        or subtotal['column'] <= cell['column']
                        or sum(Fraction(n) for _, n in pairs) != Fraction(total)):
                    continue
                proof = {'section': section, 'heading_row_index': start, 'boundary_row_index': boundary,
                         'terms': terms, 'subtotal': {'row_index': block[-1], 'cell_index': subtotal['cell_index'],
                         'column': subtotal['column'], 'cell_text': subtotal['text'],
                         'value': _number(subtotal, rows[block[-1]])}}
                resolved[block[-1], left] = (cell, _number(cell, rows[block[-1]]), proof)
    return resolved


def _concept(label, role, section):
    if role == 'income' and label in ('Net sales', 'Revenue', 'Revenues'):
        return 'Revenue'
    current = {'Current assets': {'Inventories, net': 'Inventories', 'Inventories': 'Inventories',
                                  'Trade receivables, net': 'TradeReceivables'},
               'Current liabilities': {'Trade payables': 'TradePayables',
                                       'Customer advances': 'ContractLiabilities'}}
    return current.get(section, {}).get(label, 'label:' + label) if role == 'balance' else 'label:' + label


def normalize_statement_tables(source):
    try:
        meta, cik, accession = _identity(source)
        packet = deepcopy(source['statement_table_fields']); supplied = packet.pop('sha256')
        if (packet.get('format') != NORMALIZER or sha256(_json(packet).encode()).hexdigest() != supplied
                or packet['source_document_sha256'] != source['id'] or packet['source_text_sha256'] != source['sha256']):
            raise ValueError('statement layout packet changed')
        facts, roles = [], set()
        coverage = {'missing_cells': 0, 'ambiguous_cells': 0, 'excluded_nonmonetary_rows': 0,
                    'excluded_reconciliation_rows': 0, 'resolved_subtotal_cells': 0}
        for table in packet['tables']:
            compact = lambda text: re.sub(r'\s+', '', text)
            literal = compact(' '.join(c['text'] for row in table['rows'] for c in row))
            if not literal or literal not in compact(source['text']):
                raise ValueError('statement cell text differs from the original source')
            heading = _HEADING.search(table['context'])
            if not heading:
                raise ValueError('consolidated statement heading missing')
            title = heading[1].lower()
            role = 'cash_flow' if 'cash' in title else 'balance' if 'position' in title or 'balance' in title else 'income'
            if role in roles:
                raise ValueError('multiple primary tables for the same statement; explicit selection required')
            roles.add(role)
            units = {(m[1].lower(), m[2].lower()) for m in _UNITS.finditer(table['context'])}
            if len(units) != 1:
                raise ValueError('unique explicit monetary scale/currency required')
            scale, currency = next(iter(units))
            unit = {'u.s. dollars': 'USD', 'euros': 'EUR', 'pounds sterling': 'GBP'}[currency] + ' ' + scale[:-1]
            rows = _grid(table['rows']); columns, last_header = _columns(rows, role)
            headers = ' '.join(c['text'] for row in rows[:last_header+1] for c in row)
            if re.search(r'\b(adjusted|pro forma|segment|percentage|percent|ratio)\b|%', headers, re.I):
                raise ValueError('non-reported or non-monetary statement columns unsupported')
            if max(col[2]['end'] for col in columns) != meta['report_date']:
                raise ValueError('statement columns do not end at the verified reporting date')
            section = None
            subtotals = _section_subtotals(rows, columns) if role == 'balance' else {}
            for row_index, row in enumerate(rows):
                if row_index <= last_header or not row or row[0]['column'] != 0:
                    continue
                label = row[0]['text']
                if not label:
                    continue
                # The cash-flow table often ends with an instant-balance
                # reconciliation and a second date header. Those are not flows
                # for the duration at the top of the table (nor monetary years).
                if role == 'cash_flow' and re.search(
                        r'^(?:Movement in|Reconciliation of|Cash and cash equivalents(?:\s+at)?$|At (?:the )?(?:beginning|end)|At (?:'+
                        '|'.join(_MONTHS)+r')\b)', label, re.I):
                    coverage['excluded_reconciliation_rows'] += len(rows) - row_index
                    break
                if label in (*_SECTIONS, 'EQUITY'):
                    section = label
                if _NONMONETARY.search(label):
                    coverage['excluded_nonmonetary_rows'] += 1
                    continue
                for left, right, period in columns:
                    cells = [(cell, _number(cell, row)) for cell in row if left <= cell['column'] < right]
                    values = [(cell, number) for cell, number in cells if number is not None]
                    subtotal_proof = {}
                    if (row_index, left) in subtotals:
                        cell, number, proof = subtotals[row_index, left]
                        values = [(cell, number)]
                        subtotal_proof = {'section_subtotal': proof}
                        coverage['resolved_subtotal_cells'] += 1
                    if len(values) != 1:
                        coverage['ambiguous_cells' if values else 'missing_cells'] += 1
                        continue
                    cell, number = values[0]
                    facts.append({'taxonomy': TAXONOMY, 'concept': _concept(label, role, section),
                        'label': label, 'value': number, 'unit': unit, **period, 'entity': meta['issuer'],
                        'scope': 'consolidated', 'statement': role, 'section': section,
                        'proof': {'source_document_id': source['id'], 'packet_sha256': supplied,
                                  'table_index': table['index'], 'row_index': row_index,
                                  'cell_index': cell['cell_index'], 'column': cell['column'], 'cell_text': cell['text'],
                                  **subtotal_proof}})
        if roles != {'income', 'balance', 'cash_flow'} or not any(f['concept'] == 'Revenue' for f in facts):
            raise ValueError('income, balance sheet, cash flow and reported revenue required')
        text = _json({'issuer': meta['issuer'], 'facts': facts})
        doc = {'id': PREFIX + source['id'], 'url': source['url'], 'published_at': source['published_at'],
               'document_sha256': source['id'], 'text': text, 'sha256': sha256(text.encode()).hexdigest(),
               'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER, 'source_document_id': source['id'],
                   'emittente_id': 'CIK:' + cik, 'accession': accession, 'entity': meta['issuer'],
                   'scope': 'consolidated', 'report_date': meta['report_date'], 'coverage': coverage,
                   'limitation': 'Printed monetary observations only; omitted/ambiguous cells are not zero. No XBRL tags, NWC completeness or forecasts inferred.'}}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        return {'status': 'incomplete', 'documents': [], 'issues': [{'source': NORMALIZER, 'reason': str(exc)}]}


def collect_statement_tables(documents, archive_root):
    from pathlib import Path
    root = Path(archive_root).resolve()
    result = {'status': 'unavailable', 'documents': [], 'packets': {}, 'issues': []}
    for source in documents:
        try:
            path = Path(source['archive_path']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('statement source outside verified archive')
            packet = extract_statement_packet(source, path.read_bytes())
            normalized = normalize_statement_tables({**source, 'statement_table_fields': packet})
            result['issues'].extend(normalized['issues'])
            if normalized['status'] == 'ready':
                result['packets'][source['id']] = packet
                result['documents'].extend(normalized['documents'])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            result['issues'].append({'source': source.get('id'), 'reason': str(exc)})
    if result['documents']:
        result['status'] = 'partial' if result['issues'] else 'ready'
    return result
