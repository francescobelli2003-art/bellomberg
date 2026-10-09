"""Bounded Capo scorecard declarations; not a financial or free-prose certifier.

Only the new evidence-followup contract calls this module. The producer of
Reflection36, its requests, saved lessons and historical Capo requests are intact.
"""
from hashlib import sha256
import json

from bellomberg.core.reflection_policy import descriptive_groups_for, MIN_OUTCOMES

POLICY = 'capo-scorecard-scope/1'
OPEN = '<scorecard_bindings>'
CLOSE = '</scorecard_bindings>'
USES = {'description', 'sizing', 'selection', 'ranking', 'action_rule', 'operational_lesson'}
INSTRUCTIONS = """
SCORECARD SCOPE capo-scorecard-scope/1 [src: scorekeeper]: queste regole prevalgono
sulle istruzioni generiche PESA LE FIRME SUL TRACK RECORD, pesa le voci, alza la
soglia dei BUY e ALTA deve battere MEDIA/BASSA. Usa i group_id esatti e il loro n.
Sotto 36 esiti attestati nel SINGOLO gruppo: SOLO descrizione; conserva le statistiche
ma nessuna lezione operativa o modifica a sizing, selezione, ranking o regole d'azione.
Overall non presta il campione a BUY, altri gruppi, titoli o sottoinsiemi. Confronti
operativi richiedono OGNI gruppo coinvolto operational_eligible=true. Un gruppo
UNVERIFIED non attesta neppure le statistiche; dichiara la lacuna. La soglia minima
non prova indipendenza, significativita', causalita', alpha o P&L del conto: conserva
method_note e i limiti degli orizzonti/valuta/firme euristiche dello scorekeeper.
Per OGNI uso del track record riporta TUTTI i group_ids coinvolti, anche quelli
oggetto della raccomandazione, in UN solo blocco finale:
<scorecard_bindings>{"rows":[{"group_ids":["action:BUY"],"use":"description",
"instruction":"estratto letterale della frase presente nel memo"}]}</scorecard_bindings>
Grammatica chiusa: sole chiavi rows/group_ids/use/instruction; 0..32 righe,
instruction 1..1600 caratteri, gruppi non vuoti e senza duplicati. use e' esattamente
description, sizing, selection, ranking, action_rule oppure operational_lesson.
Zero righe se nessun uso. Il controllo verifica questi binding e solo le frasi
esplicite documentate, NON il significato economico o le parafrasi: resto NOT_ASSESSED.
"""

# Deliberately closed, full-line prose forms. No substring/negation/LLM inference.
# An unlisted paraphrase, quotation, conditional or multi-group comparison remains
# NOT_ASSESSED; recognizing one line never certifies surrounding text.
PROSE_FORMS = {
    'un aumento del sizing.': 'sizing',
    'una modifica della selezione.': 'selection',
    'una modifica del ranking.': 'ranking',
    "una modifica delle regole d'azione.": 'action_rule',
    'una lezione operativa.': 'operational_lesson',
}


def project(scorecard):
    return {'policy': POLICY, 'minimum_outcomes': MIN_OUTCOMES,
            **descriptive_groups_for(scorecard), 'semantic_scope': 'NOT_ASSESSED'}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _binding_code(row, groups):
    if (not isinstance(row, dict) or set(row) != {'group_ids', 'use', 'instruction'}
            or not isinstance(row['use'], str) or row['use'] not in USES
            or not isinstance(row['instruction'], str) or not row['instruction'].strip()
            or len(row['instruction']) > 1600
            or not isinstance(row['group_ids'], list) or not row['group_ids']
            or not all(isinstance(k, str) and k for k in row['group_ids'])):
        return 'INVALID_BINDING'
    keys = row['group_ids']
    if len(keys) != len(set(keys)):
        return 'DUPLICATE_GROUP'
    if any(key not in groups for key in keys):
        return 'UNKNOWN_GROUP'
    if any(groups[key]['status'] != 'ATTESTED' for key in keys):
        return 'GROUP_UNVERIFIED'
    if row['use'] != 'description' and any(not groups[key]['operational_eligible'] for key in keys):
        return 'GROUP_NOT_OPERATIONAL'
    return None


def assess(raw, scope):
    """Reject individual declarations only; never rewrite raw or block the memo.

    An accepted binding attests group membership/threshold and a literal link to
    the memo. It does NOT establish that the model classified that prose honestly.
    """
    result = {'policy': POLICY, 'raw_sha256': sha256(raw.encode('utf-8')).hexdigest(),
              'status': 'NOT_ASSESSED', 'binding_status': 'ABSENT',
              'accepted_bindings': [], 'rejected_bindings': [], 'explicit_prose': [],
              'prose_scope': 'NOT_ASSESSED', 'semantic_scope': 'NOT_ASSESSED'}
    prose = raw
    bindings = []
    if OPEN in raw or CLOSE in raw:
        result['binding_status'] = 'INVALID'
        try:
            if raw.count(OPEN) != 1 or raw.count(CLOSE) != 1:
                raise ValueError('envelope count')
            start, finish = raw.index(OPEN), raw.index(CLOSE)
            if finish < start or finish - start > 64000:
                raise ValueError('envelope order/length')
            prose = raw[:start] + raw[finish + len(CLOSE):]
            payload = json.loads(raw[start + len(OPEN):finish], object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {'rows'}
                    or not isinstance(payload['rows'], list) or len(payload['rows']) > 32):
                raise ValueError('rows schema')
            result['binding_status'] = 'VALID'
            bindings = payload['rows']
        except (ValueError, TypeError, RecursionError):
            result['rejected_bindings'].append({'code': 'INVALID_BINDING_BLOCK'})
    for number, line in enumerate(prose.splitlines(), 1):
        line = line.strip()
        prefix = 'Il track record di '
        if not line.startswith(prefix):
            continue
        group, separator, suffix = line[len(prefix):].partition(' giustifica ')
        if separator and suffix in PROSE_FORMS:
            row = {'group_ids': [group], 'use': PROSE_FORMS[suffix], 'instruction': line}
            code = _binding_code(row, scope['groups'])
            result['explicit_prose'].append({'line': number, **row,
                'status': 'REJECTED' if code else 'DECLARED_SCOPE_CHECKED', 'code': code})
    for index, row in enumerate(bindings):
        code = _binding_code(row, scope['groups'])
        if code is None and row['instruction'] not in prose:
            code = 'INSTRUCTION_NOT_IN_MEMO'
        if code is None:
            # Reconcile only full-line prose already recognized above. A model's
            # JSON declaration cannot override the known group/use of that line.
            instruction_lines = {line.strip() for line in row['instruction'].splitlines()}
            for covered in result['explicit_prose']:
                if covered['instruction'] not in instruction_lines:
                    continue
                if covered['code']:
                    code = covered['code']
                elif (row['use'] != covered['use']
                      or not set(covered['group_ids']).issubset(row['group_ids'])):
                    code = 'EXPLICIT_SCOPE_MISMATCH'
                if code:
                    break
        if code:
            result['binding_status'] = 'INVALID'
            result['rejected_bindings'].append({'row': index, 'code': code})
        else:
            result['accepted_bindings'].append(row)
    if result['rejected_bindings'] or any(r['code'] for r in result['explicit_prose']):
        result['status'] = 'REJECTED_BINDINGS'
    elif result['binding_status'] == 'VALID' or result['explicit_prose']:
        result['status'] = 'BINDINGS_CHECKED'
    return result


def notice(result):
    state = ('binding operativo rifiutato; nessun uso autorizzato dal binding respinto'
             if result['status'] == 'REJECTED_BINDINGS' else
             'binding dichiarati controllati solo per gruppo e soglia' if result['status'] == 'BINDINGS_CHECKED'
             else 'binding assenti; uso del track record NOT_ASSESSED')
    codes = sorted({r['code'] for r in result['rejected_bindings'] + result['explicit_prose'] if r.get('code')})
    return ('\n\n> [SCORECARD: ' + state + (('; cause: ' + ', '.join(codes)) if codes else '')
            + '. Gruppi con meno di 36 esiti non abilitano sizing, selezione, ranking, '
            'regole o lezioni operative'
            + '. Controllo limitato ai binding strutturati e alle frasi esatte riconosciute; '
            'prosa restante e significato economico NOT_ASSESSED. Testo originale conservato.]')


def format_track_record_scope_for_capo(scope) -> str:
    """New-contract projection only: no refetch, ranking, truncation or instruction."""
    return ('--- SCORECARD: GRUPPI E LIMITI DI UTILIZZO [src: scorekeeper] ---\n'
            '<scorecard_scope>' + json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                              allow_nan=False) + '</scorecard_scope>')
