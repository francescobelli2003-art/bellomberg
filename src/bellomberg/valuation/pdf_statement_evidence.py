"""Bounded consolidated PDF statements: geometric columns plus literal proofs.

No OCR, automatic page exclusions, inferred zeros or economic classifications.
The packet is acquired from original PDF bytes and replayed against primary text.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from math import isfinite
import re

from .filing_pdf_evidence import verify_filing_pdf

FORMAT = 'pdf_statement_pages_v1'
_TITLES = {'income': r'Consolidated\s+(?:Income Statements|Statements? of Income)',
    'balance': r'Consolidated\s+Statements? of Financial Position',
    'cash_flow': r'Consolidated\s+Statements? of Cash Flows'}
_NUM = re.compile(r'(?:\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?|[-–—])$')
_MONTHS = 'January February March April May June July August September October November December'.split()


def _roles(text):
    return [role for role, pattern in _TITLES.items() if re.search(pattern,text,re.I)]


def extract_pdf_statement_packet(source, raw):
    import pdfplumber
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    from .statement_table_evidence import _json
    verify_filing_pdf(source)
    if (sha256(raw).hexdigest()!=source['document_sha256'] or
            estrai_testo('statements.pdf',contenuto=raw).get('testo')!=source['text']):
        raise ValueError('PDF statement bytes/text differ from primary source')
    pages=[]
    with pdfplumber.open(BytesIO(raw)) as pdf:
        for ref in source['page_references']:
            text=source['text'][ref['inizio']:ref['fine']]
            if len(_roles(text))!=1 or not re.search(r'(?:€|EUR|USD|GBP)\s+(?:million|thousand)\b',text):
                continue
            page=pdf.pages[ref['pagina']-1]
            words=page.extract_words(x_tolerance=1,y_tolerance=1,expand_ligatures=False)
            if any(not w.get('upright',True) for w in words):
                raise ValueError('rotated PDF statement text unsupported')
            pages.append({'page':ref['pagina'],'width':page.width,'height':page.height,
                'words':[{k:w[k] for k in ('text','x0','top','x1','bottom')} for w in words]})
            page.close()
    packet={'format':FORMAT,'source_document_sha256':source['id'],
        'source_text_sha256':source['sha256'],'pages':pages}
    packet['sha256']=sha256(_json(packet).encode()).hexdigest()
    return packet


def _same_line(a,b):
    # Baseline overlap, bounded by the printed font-box heights.
    return abs(a['bottom']-b['bottom']) <= min(a['bottom']-a['top'],b['bottom']-b['top'])*.2


def _box(words):
    return [min(w['x0'] for w in words),min(w['top'] for w in words),
            max(w['x1'] for w in words),max(w['bottom'] for w in words)]


def _center(word):
    return (word['x0']+word['x1'])/2


def _axes(words, role, metadata):
    units=[]
    for currency in words:
        if currency['text'] not in ('€','EUR','USD','GBP'): continue
        scale=[w for w in words if w['text'] in ('million','thousand') and _same_line(w,currency)
               and currency['x1']<=w['x0']<=currency['x1']+2*(w['bottom']-w['top'])]
        if len(scale)==1: units.append((currency,scale[0]))
    if len(units)!=1: raise ValueError('unique PDF monetary unit header required')
    currency,scale=units[0];height=scale['bottom']-scale['top']
    header=[w for w in words if scale['top']-3*height<=w['top']<=scale['bottom']+height
            and w['x0']>scale['x1']]
    columns=[]
    for word in header:
        if role!='balance' and word['text'] in ('Q2','H1'):
            years=[w for w in header if re.fullmatch(r'\d{4}',w['text']) and _same_line(word,w)
                and 0<=w['x0']-word['x1']<=height*1.5]
            if len(years)!=1: raise ValueError('PDF duration column year missing/ambiguous')
            year=years[0];value=int(year['text'])
            if (metadata['tipo']!='semestrale' or metadata['report_start'][5:]!='01-01'
                    or metadata['report_date'][5:]!='06-30'):
                raise ValueError('Q2/H1 requires verified January-June reporting calendar')
            columns.append({'x':(_box([word,year])[0]+_box([word,year])[2])/2,
                'header':[word,year],'period':{'start':f'{value}-'+('04-01' if word['text']=='Q2' else '01-01'),
                    'end':f'{value}-06-30'}})
        elif role=='balance' and word['text'].rstrip('.').lower() in {m.lower() for m in _MONTHS}|{m[:3].lower() for m in _MONTHS}:
            month=next(i for i,m in enumerate(_MONTHS,1) if word['text'].rstrip('.').lower() in (m.lower(),m[:3].lower()))
            days=[w for w in header if re.fullmatch(r'\d{1,2},',w['text']) and _same_line(word,w)
                and 0<=w['x0']-word['x1']<=height*1.5]
            if len(days)!=1: raise ValueError('PDF balance date day missing/ambiguous')
            day=days[0];right=day['x1']
            years=[w for w in header if re.fullmatch(r'\d{4}',w['text'])
                and word['top']<=w['top']<=word['bottom']+2*height
                and word['x0']-height<=_center(w)<=right+height]
            if len(years)!=1: raise ValueError('PDF balance column year missing/ambiguous')
            year=years[0]
            columns.append({'x':_center(year),'header':[word,day,year],
                'period':{'end':date(int(year['text']),month,int(day['text'][:-1])).isoformat()}})
    columns.sort(key=lambda c:c['x'])
    if not 2<=len(columns)<=4 or max(c['period']['end'] for c in columns)!=metadata['report_date']:
        raise ValueError('PDF dated column coverage unsupported')
    if len({tuple(sorted(c['period'].items())) for c in columns})!=len(columns):
        raise ValueError('duplicate PDF reporting column')
    gaps=[b['x']-a['x'] for a,b in zip(columns,columns[1:])]
    if min(gaps)<=2*height: raise ValueError('PDF headers overlap')
    bounds=[columns[0]['x']-gaps[0]/2]+[(a['x']+b['x'])/2 for a,b in zip(columns,columns[1:])]+[columns[-1]['x']+gaps[-1]/2]
    bottom=max(w['bottom'] for c in columns for w in c['header'])
    return columns,bounds,bottom,('EUR' if currency['text']=='€' else currency['text'])+' '+scale['text'],[currency,scale]


def _literal(text, words, cursor=0):
    """Whitespace-only comparison retains the exact primary-text quote/offset."""
    indices=[i for i,c in enumerate(text) if not c.isspace()]
    compact=''.join(text[i] for i in indices)
    target=''.join(''.join(w['text'].split()) for w in words)
    position=compact.find(target,cursor)
    if not target or position<0: raise ValueError('PDF geometry text differs from primary reading order')
    start,end=indices[position],indices[position+len(target)-1]+1
    return {'start':start,'end':end,'quote':text[start:end]},position+len(target)


def _number(text):
    if text in ('-','–','—'): return None
    if not _NUM.fullmatch(text) or text.startswith('(')!=text.endswith(')'):
        raise ValueError('unsupported printed monetary number')
    value=Decimal(text.strip('()').replace(',',''))
    if text.startswith('('): value=-value
    return int(value) if value==value.to_integral() else float(value)


def normalize_pdf_statements(source):
    from .statement_table_evidence import _json, _concept, NORMALIZER, PREFIX, TAXONOMY, _NONMONETARY
    try:
        verify_filing_pdf(source)
        packet=deepcopy(source['statement_table_fields']);digest=packet.pop('sha256')
        if (packet['format']!=FORMAT or digest!=sha256(_json(packet).encode()).hexdigest()
                or packet['source_document_sha256']!=source['id'] or packet['source_text_sha256']!=source['sha256']):
            raise ValueError('PDF statement packet changed')
        facts=[];roles=set();seen_pages=set()
        coverage={'missing_cells':0,'excluded_nonmonetary_rows':0,'excluded_reconciliation_rows':0,
                  'unlabeled_rows':0,'unclassified_text_rows':0,'excluded_after_per_share_header_rows':0}
        entity=source['metadata']['emittente_id']
        for page in packet['pages']:
            number=page['page']
            if type(number) is not int or number in seen_pages: raise ValueError('duplicate/invalid PDF page')
            seen_pages.add(number)
            ref=source['page_references'][number-1]
            if number!=ref['pagina']: raise ValueError('PDF page identity mismatch')
            text=source['text'][ref['inizio']:ref['fine']]
            page_roles=_roles(text)
            if len(page_roles)!=1 or page_roles[0] in roles: raise ValueError('multiple/ambiguous primary PDF statements')
            role=page_roles[0];roles.add(role)
            words=page['words']
            for w in words:
                if (not isinstance(w['text'],str) or not w['text'].strip() or
                    any(type(w[k]) not in (int,float) or not isfinite(w[k]) for k in ('x0','x1','top','bottom')) or
                    not 0<=w['x0']<w['x1']<=page['width'] or not 0<=w['top']<w['bottom']<=page['height']):
                    raise ValueError('invalid PDF word geometry')
            columns,bounds,top,unit,unit_words=_axes(words,role,source['metadata'])
            unit_proof,_=_literal(text,unit_words)
            header_proof,cursor=_literal(text,[w for c in columns for w in c['header']],0)
            body=sorted([w for w in words if w['top']>top],key=lambda w:(w['bottom'],w['x0']))
            rows=[]
            for w in body:
                if not rows or not _same_line(rows[-1][0],w): rows.append([])
                rows[-1].append(w)
            pending=[];section=None
            for row_index,row in enumerate(rows):
                row.sort(key=lambda w:w['x0'])
                label_words=[w for w in row if w['x1']<bounds[0]]
                if role=='income' and re.search(r'\b(?:earnings|income) per (?:share|ADS)\b',
                        ' '.join(w['text'] for w in label_words),re.I):
                    # A new per-share unit terminates this monetary block;
                    # nested Basic/Diluted lines cannot inherit millions.
                    coverage['excluded_after_per_share_header_rows']+=len(rows)-row_index
                    break
                cells=[[] for _ in columns]
                for w in row:
                    if w in label_words: continue
                    matches=[i for i in range(len(columns)) if bounds[i]<=w['x0']<w['x1']<=bounds[i+1]]
                    if len(matches)!=1 or not _NUM.fullmatch(w['text']):
                        raise ValueError('PDF body cell does not belong to one monetary column')
                    cells[matches[0]].append(w)
                if not any(cells):
                    label=' '.join(w['text'] for w in label_words)
                    if label in ('Current assets','Noncurrent assets','Current liabilities','Noncurrent liabilities','Equity'):
                        section=label;pending=[]
                    else: pending.extend(label_words);coverage['unclassified_text_rows']+=1
                    continue
                if any(len(c)>1 for c in cells): raise ValueError('multiple numbers in a PDF monetary cell')
                label_words=pending+label_words;pending=[]
                label=' '.join(w['text'] for w in label_words) or None
                row_words=label_words+[w for c in cells for w in c]
                literal,cursor=_literal(text,row_words,cursor)
                if (text[:literal['start']].rsplit('\n',1)[-1].strip()
                        or text[literal['end']:].split('\n',1)[0].strip()):
                    raise ValueError('PDF monetary row omits original text/cells')
                if label and _NONMONETARY.search(label): coverage['excluded_nonmonetary_rows']+=1;continue
                if role=='cash_flow' and label and re.search(r'cash and cash equivalents at (?:beginning|end)',label,re.I):
                    coverage['excluded_reconciliation_rows']+=1;continue
                if label is None: coverage['unlabeled_rows']+=1
                for index,(column,cell) in enumerate(zip(columns,cells)):
                    value=_number(cell[0]['text']) if cell else None
                    if value is None: coverage['missing_cells']+=1;continue
                    facts.append({'taxonomy':TAXONOMY,'concept':_concept(label,role,section) if label else f'unlabeled:{number}:{literal["start"]}',
                        'label':label,'value':value,'unit':unit,**column['period'],'entity':entity,'scope':'consolidated',
                        'statement':role,'section':section,'proof':{'source_document_id':source['id'],'packet_sha256':digest,
                            'page':number,'page_sha256':ref['sha256'],'column':index,'cell_text':cell[0]['text'],
                            'cell_bbox':_box(cell),'row_kind':'labeled' if label else 'unlabeled',
                            'row_literal':literal,'header_literal':header_proof,'unit_literal':unit_proof}})
        if roles!={'income','balance','cash_flow'} or not any(f['concept']=='Revenue' for f in facts):
            raise ValueError('PDF income, balance, cash flows and reported revenue required')
        body=_json({'issuer':entity,'facts':facts})
        doc={'id':PREFIX+source['id'],'url':source['url'],'published_at':source['published_at'],
            'document_sha256':source['id'],'text':body,'sha256':sha256(body.encode()).hexdigest(),'origin':NORMALIZER,
            'metadata':{'normalizer':NORMALIZER,'source_document_id':source['id'],'emittente_id':entity,
                'entity':entity,'scope':'consolidated','report_date':source['metadata']['report_date'],'coverage':coverage,
                'source_format':FORMAT,'limitation':'Printed PDF observations only; unlabeled rows remain unlabeled. No complete NWC, security identity or economic classification inferred.'}}
        return {'status':'ready','documents':[doc],'issues':[]}
    except (ValueError,TypeError,KeyError,IndexError,ArithmeticError) as exc:
        return {'status':'incomplete','documents':[],'issues':[{'source':FORMAT,'reason':str(exc)}]}
