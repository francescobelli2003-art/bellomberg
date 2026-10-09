"""Evidence eligibility for new weekly lessons; no price acquisition or return formulas."""
from copy import deepcopy
import json
import math

from bellomberg.storage.weekly_run_store import WeeklyRunBlocked, WeeklyRunStore, digest

KEY = 'reflection_policy'
POLICY = 'weekly-reflection36/1'
INPUT_STAGE = 'reflection_input_v1'
MEMORY_STAGE = 'reflection_memory_v1'
MIN_OUTCOMES = 36
UNSET = object()
NO_LESSON = 'Reflection operativa n.d.: nessuna lezione con gruppi attestati di almeno 36 esiti.'
LESSON_HEADER = '--- LEZIONE OPERATIVA ATTESTATA (minimo 36 esiti per gruppo; non garanzia) ---\n'


def enabled(contract):
    if KEY not in contract:
        return False
    if type(contract[KEY]) is not str or contract[KEY] != POLICY:
        raise WeeklyRunBlocked('Policy Reflection non compatibile')
    return True


def board_enabled(board):
    store = getattr(board, 'weekly_store', None)
    return (getattr(board, 'run_scope', None) == 'weekly' and store is not None
            and enabled(store.context.get('contract', {})))


def _count(value):
    return type(value) is int and value >= 0


def groups_for(scorecard):
    """Attest membership against existing aggregates, never fix or reweight them."""
    if not isinstance(scorecard, dict) or not isinstance(scorecard.get('details'), list):
        return {}, ['MISSING_OUTCOME_DETAILS']
    details = scorecard['details']
    ids = [r.get('id') if isinstance(r, dict) else None for r in details]
    if (any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids))
            or any(type(r.get('hit')) is not bool or not isinstance(r.get('specialists'), list)
                   or not all(isinstance(s, str) for s in r['specialists'])
                   or len(r['specialists']) != len(set(r['specialists'])) for r in details)):
        return {}, ['INVALID_OUTCOME_DETAILS']
    candidates = [('overall', scorecard.get('overall'), details)]
    for field, prefix, member in (('by_action', 'action', 'action'),
                                   ('by_confidence', 'confidence', 'confidence_bucket'),
                                   ('by_specialist', 'specialist', 'specialists')):
        table = scorecard.get(field, {})
        if not isinstance(table, dict):
            return {}, ['INVALID_GROUP_TABLE']
        for name, aggregate in table.items():
            if not isinstance(name, str) or not name.strip():
                return {}, ['INVALID_GROUP_ID']
            rows = [r for r in details if (name in r[member] if member == 'specialists'
                                          else r.get(member) == name)]
            candidates.append((prefix + ':' + name, aggregate, rows))
    eligible, excluded = {}, []
    for key, agg, rows in candidates:
        if (not isinstance(agg, dict) or not _count(agg.get('n'))
                or agg['n'] != len(rows)):
            excluded.append(key + ':INCONSISTENT_COUNT')
            continue
        if not rows:
            excluded.append(key + ':BELOW_36')
            continue
        if (not _count(agg.get('hits')) or agg['hits'] != sum(r['hit'] for r in rows)
                or any(type(agg.get(k)) not in (int, float) or not math.isfinite(agg[k])
                       for k in ('hit_rate_pct', 'avg_edge_pct'))):
            excluded.append(key + ':INCONSISTENT_AGGREGATE')
            continue
        if agg['n'] < MIN_OUTCOMES:
            excluded.append(key + ':BELOW_36')
            continue
        eligible[key] = {'n': agg['n'], 'hits': agg['hits'],
                         'hit_rate_pct': agg['hit_rate_pct'], 'avg_edge_pct': agg['avg_edge_pct'],
                         'decision_ids': sorted(r['id'] for r in rows)}
    return eligible, excluded


SYSTEM = """Sei il coach del comitato. Lezioni operative SOLO dai gruppi attestati forniti.
Almeno 36 esiti nel gruppo e' un requisito minimo, non una garanzia di affidabilita.
Ogni istruzione riguarda esclusivamente i group_ids dichiarati; confronti ammessi solo
fra gruppi presenti. Non inventare ticker, sottoinsiemi, causalita, benchmark o campioni.
Overall non autorizza consigli specifici per un titolo/azione/desk non attestato.
Rendimenti direzionali in valuta di quotazione, orizzonti 4w/1w, firme desk euristiche:
non sono P&L del conto, alpha o prova di indipendenza delle osservazioni.
Rispondi SOLO con JSON {"rows":[{"group_ids":["id esatto"],"instruction":"testo"}]}.
Da zero a sei righe; nessun markdown. Zero righe se non emerge una lezione supportata.
Non ripetere numeri non forniti e cita [src: scorekeeper] nelle istruzioni."""



def _groups_checked(scorecard):
    try:
        return groups_for(scorecard)
    except (TypeError, ValueError, KeyError):
        return {}, ['CHECK_UNAVAILABLE']


def _validate_input(store, evidence, priming):
    sc = priming.get('scorecard') if isinstance(priming, dict) else None
    groups, excluded = _groups_checked(sc)
    if (not isinstance(evidence, dict) or evidence.get('version') != POLICY
            or evidence.get('run_id') != store.run_id or evidence.get('memo_id') != store.memo_id
            or evidence.get('priming_sha256') != digest(priming)
            or evidence.get('scorecard_sha256') != digest(sc)
            or evidence.get('groups') != groups or evidence.get('excluded') != excluded):
        raise WeeklyRunBlocked('Dipendenze Reflection modificate')
    request = evidence.get('request')
    if request is not None:
        if evidence.get('request_cause') is not None:
            raise WeeklyRunBlocked('Richiesta Reflection contraddetta da causa di indisponibilita')
        try:
            valid = (bool(groups) and isinstance(request, dict)
                     and set(request) == {'model', 'thinking', 'max_tokens', 'system', 'user'}
                     and isinstance(request['model'], str) and bool(request['model'])
                     and type(request['max_tokens']) is int and request['max_tokens'] == 8000
                     and isinstance(request['thinking'], dict)
                     and isinstance(request['system'], str) and SYSTEM in request['system']
                     and json.loads(request['user']) == {'eligible_groups': groups})
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            raise WeeklyRunBlocked('Richiesta Reflection non coerente con i gruppi attestati')
    return groups


def prepare_input(store, *, request_factory):
    """Seal request before the journal; a saved request never re-reads configuration."""
    if not enabled(store.context.get('contract', {})):
        return None
    priming = store.get('priming')
    source_hash = digest(priming)
    saved = store.get(INPUT_STAGE)
    if saved is None and store.get('reflection') is not None:
        raise WeeklyRunBlocked('Reflection priva del checkpoint input originale')
    if saved is not None:
        _validate_input(store, saved, priming)
        return saved
    scorecard = priming.get('scorecard') if isinstance(priming, dict) else None
    groups, excluded = _groups_checked(scorecard)
    request = None
    request_cause = None
    if groups:
        try:
            request = request_factory(groups)
        except WeeklyRunBlocked:
            raise
        except Exception:
            request_cause = 'REQUEST_CONFIGURATION_UNAVAILABLE'
    payload = {'version': POLICY, 'run_id': store.run_id, 'memo_id': store.memo_id,
               'priming_sha256': source_hash, 'scorecard_sha256': digest(scorecard),
               'computed_at': scorecard.get('computed_at') if isinstance(scorecard, dict) else None,
               'groups': groups, 'excluded': excluded, 'request': request, 'request_cause': request_cause}
    _validate_input(store, payload, priming)
    store.complete(INPUT_STAGE, payload)
    return payload


def parse_rows(text, evidence):
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        return None
    rows = result.get('rows') if isinstance(result, dict) and set(result) == {'rows'} else None
    if not isinstance(rows, list) or len(rows) > 6:
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'group_ids', 'instruction'}:
            return None
        keys, instruction = row['group_ids'], row['instruction']
        if (not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys)
                or len(keys) != len(set(keys)) or any(k not in evidence['groups'] for k in keys)
                or not isinstance(instruction, str) or not instruction.strip()
                or len(instruction) > 1600 or '[src: scorekeeper]' not in instruction):
            return None
        out.append({'group_ids': keys, 'instruction': instruction.strip()})
    return out


def render_rows(rows, groups):
    return '\n'.join(f"{i}. [" + ', '.join(f'{key}; n={groups[key]["n"]}' for key in row['group_ids'])
                     + '] ' + row['instruction'] for i, row in enumerate(rows, 1))


def result_for(evidence, rows=None, *, cause=None):
    rows = rows or []
    text = render_rows(rows, evidence['groups'])
    return {'version': POLICY, 'input_sha256': digest(evidence), 'rows': rows,
            'lesson': text, 'text_sha256': digest(text),
            'status': 'generated' if rows else 'not_generated', 'cause': cause}


def verified_lesson(source):
    """Read the immutable producer input/result, checking membership against priming."""
    if not enabled(source.context.get('contract', {})):
        return None
    result = source.get('reflection')
    if result is None:
        return None
    evidence = source.get(INPUT_STAGE)
    priming = source.get('priming')
    if not isinstance(evidence, dict) or not isinstance(result, dict):
        raise WeeklyRunBlocked('Reflection priva di prova attestata')
    groups = _validate_input(source, evidence, priming)
    if result.get('version') != POLICY or result.get('input_sha256') != digest(evidence):
        raise WeeklyRunBlocked('Prova Reflection incoerente')
    rows = parse_rows(json.dumps({'rows': result.get('rows')}), evidence)
    if rows is None:
        raise WeeklyRunBlocked('Gruppi Reflection non attestati')
    if rows and (evidence.get('request') is None or result.get('cause') is not None):
        raise WeeklyRunBlocked('Lezione operativa Reflection senza richiesta valida o con causa di indisponibilita')
    text = render_rows(rows, groups)
    if result.get('lesson') != text or result.get('text_sha256') != digest(text):
        raise WeeklyRunBlocked('Testo Reflection modificato')
    if result.get('status') != ('generated' if rows else 'not_generated'):
        raise WeeklyRunBlocked('Stato Reflection incoerente')
    return text or None


def memory_projection(store):
    """Freeze a new request's read-time selection; old JSON is never rewritten or trusted."""
    if not enabled(store.context.get('contract', {})):
        return UNSET
    saved = store.get(MEMORY_STAGE)
    if saved is not None:
        if not isinstance(saved, dict) or saved.get('version') != POLICY:
            raise WeeklyRunBlocked('Proiezione Reflection non compatibile')
        selected = saved.get('selected')
        expected = NO_LESSON
        if selected is not None:
            if not isinstance(selected, dict) or type(selected.get('memo_id')) is not int:
                raise WeeklyRunBlocked('Riferimento Reflection non valido')
            source = WeeklyRunStore(store.db, selected['memo_id'])
            text = verified_lesson(source)
            if (not text or source.run_id != selected.get('run_id')
                    or digest(source.get('reflection')) != selected.get('reflection_sha256')):
                raise WeeklyRunBlocked('Riferimento Reflection modificato')
            expected = LESSON_HEADER + text
        if saved.get('block') != expected:
            raise WeeklyRunBlocked('Proiezione Reflection modificata')
        return expected
    block = NO_LESSON
    selected = None
    for memo in store.db.get_completed_weekly_memos(n=None):
        if memo['id'] == store.memo_id:
            continue
        # Historical completed memos may predate native run records.
        with store.db._conn() as conn:
            exists = conn.execute('SELECT 1 FROM weekly_runs WHERE memo_id=?', (memo['id'],)).fetchone()
        if not exists:
            continue
        source = WeeklyRunStore(store.db, memo['id'])
        text = verified_lesson(source)
        if text:
            block = LESSON_HEADER + text
            selected = {'memo_id': source.memo_id, 'run_id': source.run_id,
                        'reflection_sha256': digest(source.get('reflection'))}
            break
    store.complete(MEMORY_STAGE, {'version': POLICY, 'block': block, 'selected': selected})
    return block


def scorecard_for(store):
    priming = store.get('priming')
    return priming.get('scorecard') if isinstance(priming, dict) else None


def descriptive_groups_for(scorecard):
    """Additive Capo view: retain small valid groups, never alter the lesson producer.

    Eligibility is the existing groups_for verdict. Descriptive statistics still
    require the same real membership/count/hit evidence, including below 36.
    Unverified aggregates have null statistics and an explicit cause, not zeroes.
    """
    eligible, excluded = _groups_checked(scorecard)
    result = {'groups': {}, 'issues': list(excluded)}
    if any(':' not in reason for reason in excluded):
        return result  # Invalid/missing details or tables: no membership attested.
    details = scorecard['details']
    method = scorecard.get('method_note')
    method = method if isinstance(method, str) and method.strip() else 'n.d.: metodo non dichiarato'
    candidates = [('overall', scorecard.get('overall'), details)]
    for field, prefix, member in (('by_action', 'action', 'action'),
                                 ('by_confidence', 'confidence', 'confidence_bucket'),
                                 ('by_specialist', 'specialist', 'specialists')):
        for name, aggregate in scorecard.get(field, {}).items():
            rows = [r for r in details if (name in r[member] if member == 'specialists'
                                          else r.get(member) == name)]
            candidates.append((prefix + ':' + name, aggregate, rows))
    for key, aggregate, rows in candidates:
        valid = (isinstance(aggregate, dict) and _count(aggregate.get('n'))
                 and aggregate['n'] == len(rows) and _count(aggregate.get('hits'))
                 and aggregate['hits'] == sum(r['hit'] for r in rows)
                 and all(type(aggregate.get(k)) in (int, float) and math.isfinite(aggregate[k])
                         for k in ('hit_rate_pct', 'avg_edge_pct')))
        operational = valid and key in eligible
        issue = (None if operational else 'BELOW_36' if valid else 'UNVERIFIED_AGGREGATE')
        if not valid and key + ':UNVERIFIED_AGGREGATE' not in result['issues']:
            result['issues'].append(key + ':UNVERIFIED_AGGREGATE')
        result['groups'][key] = {
            'group_id': key, 'n': aggregate['n'] if valid else None,
            'hits': aggregate['hits'] if valid else None,
            'hit_rate_pct': aggregate['hit_rate_pct'] if valid else None,
            'avg_edge_pct': aggregate['avg_edge_pct'] if valid else None,
            'descriptive_only': not operational, 'operational_eligible': operational,
            'status': 'ATTESTED' if valid else 'UNVERIFIED', 'issue': issue,
            'method_note': method,
        }
    return result
