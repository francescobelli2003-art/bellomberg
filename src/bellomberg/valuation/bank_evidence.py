"""Derived opening reconciliations across explicitly bound bank reporting scopes."""
from math import fsum, isclose
from datetime import date

from .input_evidence import structured_fact_proof, _finite, entity_name_key, same_entity_name


CONSOLIDATION_DISCLOSURE = (
    "Riconciliazione aggregata dei valori riportati: consolidato meno parent-only "
    "meno controllate. Non identifica le singole eliminazioni e non attribuisce "
    "differenze ad arrotondamenti. ")


def is_consolidation(item):
    calculation = item.get('calculation') if isinstance(item, dict) else None
    return isinstance(calculation, dict) and calculation.get('operation') == 'consolidation_equity'


def consolidation_proof(item, evidence, unit, period, *, group_entity, context, prove, scale):
    """Prove the reported aggregate; never assert ownership or individual entries.

    Legal perimeter remains a sourced modeling decision. This arithmetic admits
    SEC GAAP group equity, normalized parent equity and normalized FDIC bank equity
    at exactly one opening date. All amounts must match the proposed model; an AI
    cannot pick a different scenario balance just to close its reconciliation.
    """
    try:
        if not isinstance(context, dict) or not isinstance(group_entity, str) or not group_entity.strip():
            raise ValueError('riconciliazione consolidata: contesto bancario ed emittente richiesti')
        if date.fromisoformat(period).isoformat() != period:
            raise ValueError('data iniziale ISO richiesta per tutti i saldi')
        model, scenarios = context['model'], context['scenarios']
        legal = model['legal_structure']['value']
        parent = legal['parent_entity']
        banks = [sub['id'] for sub in legal['subsidiaries']]
        if (legal.get('capital_basis') != 'common_equity' or legal.get('accounting_basis') != 'GAAP'
                or not isinstance(parent, str) or not parent.strip() or not banks
                or len({entity_name_key(b) for b in banks}) != len(banks)
                or any(not isinstance(b, str) or not b.strip()
                    or same_entity_name(b, parent) or same_entity_name(b, group_entity) for b in banks)):
            raise ValueError('perimetro GAAP/common-equity e controllate distinte richiesti')
        calculation = item['calculation']
        if set(calculation) != {'operation', 'terms'} or calculation['operation'] != 'consolidation_equity':
            raise ValueError('operazione di consolidamento incompleta o campi non consumati')
        terms = calculation['terms']
        if (not isinstance(terms, dict) or set(terms) != {'group', 'parent', 'subsidiaries'}
                or not isinstance(terms['subsidiaries'], dict) or set(terms['subsidiaries']) != set(banks)):
            raise ValueError('saldi di gruppo, parent e tutte le controllate dichiarate richiesti')
        catalog = {doc['id']: doc for doc in evidence}
        used, amounts = set(), {}
        roles = [('group', group_entity, terms['group']), ('parent', parent, terms['parent'])]
        roles += [('subsidiary.' + str(i), b, terms['subsidiaries'][b]) for i, b in enumerate(banks)]
        for role, entity, fact in roles:
            if not isinstance(fact, dict) or set(fact) != {'value', 'evidence_ids', 'calculation'}:
                raise ValueError(role + ': saldo con value/evidence_ids/calculation esatti richiesto')
            ids = fact['evidence_ids']
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in catalog:
                raise ValueError(role + ': una fonte di saldo normalizzata richiesta')
            if not isinstance(fact['calculation'], dict) or fact['calculation'].get('operation') != 'sum':
                raise ValueError(role + ': ponte common-equity con componenti firmate richiesto')
            doc = catalog[ids[0]]
            metadata = doc.get('metadata') or {}
            if role == 'group':
                if not doc['id'].startswith('xbrl-') or not str(metadata.get('emittente_id', '')).startswith('CIK:'):
                    raise ValueError('group: fonte SEC consolidata richiesta')
                problem = structured_fact_proof(fact, [doc], unit, period, scale=scale,
                    expected_entity=entity, concept_signs={('us-gaap', 'StockholdersEquity'): 1,
                                                         ('us-gaap', 'PreferredStockValue'): -1})
            else:
                expected = ('regulatory_pdf_v1', 'sec_parent_inline_v1') if role == 'parent' else ('fdic_financials_v1',)
                if metadata.get('normalizer') not in expected:
                    raise ValueError(role + ': fonte regolamentare del perimetro errato')
                driver = 'opening_parent_equity' if role == 'parent' else (
                    'capital.subsidiaries.' + str(banks.index(entity)) + '.opening_gaap_equity')
                problem = prove(driver, fact, [doc], unit, period, expected_entity=entity)
            if problem:
                raise ValueError(role + ': ' + problem)
            used.add(ids[0])
            amounts[role] = fact['value']
        declared = item.get('evidence_ids')
        if (not isinstance(declared, list) or any(not isinstance(i, str) for i in declared)
                or len(declared) != len(set(declared)) or set(declared) != used):
            raise ValueError('fonti dichiarate diverse dall\'unione esatta dei saldi')
        def same(value, expected):
            return _finite(value) and isclose(value, expected, rel_tol=1e-10, abs_tol=1e-8)
        for driver, role in (('opening_common_equity', 'group'), ('opening_parent_equity', 'parent')):
            if not same(model[driver]['value'], amounts[role]):
                raise ValueError(driver + ': saldo del modello diverso dal ponte documentato')
        for values in scenarios.values():
            for i, bank in enumerate(banks):
                driver = 'capital.subsidiaries.' + str(i) + '.opening_gaap_equity'
                # In staged preparation, absent future inputs stay missing. When
                # present, every scenario must use the same observed opening.
                if driver in values and not same(values[driver]['value'], amounts['subsidiary.' + str(i)]):
                    raise ValueError(driver + ': saldo dello scenario diverso dal ponte documentato')
        expected = fsum([amounts['group'], -amounts['parent'],
                         *(-amounts['subsidiary.' + str(i)] for i in range(len(banks)))])
        if not same(item.get('value'), expected):
            raise ValueError('rettifica consolidata diversa dalla riconciliazione dei saldi riportati')
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError) as exc:
        return str(exc)
    return None
