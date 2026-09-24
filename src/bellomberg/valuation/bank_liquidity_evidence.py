"""Opening cash remains a historical fact inside a forecast liquidity contract."""


def prove_opening_cash(item, evidence, period, currency, legal, *, prove):
    try:
        entities = {row['id'] for row in legal['subsidiaries']}
        bridges, facts = item.get('value'), item.get('facts')
        if (not isinstance(bridges, dict) or set(bridges) != entities
                or not isinstance(facts, dict) or set(facts) != entities):
            raise ValueError('historical opening cash proof required for each legal bank; no group-minus-parent estimate')
        catalog = {doc['id']: doc for doc in evidence}
        allowed = {'evidence_ids', 'quoted_value', 'quoted_unit', 'evidence_pointer',
                   'evidence_quote', 'period_quote'}
        for entity in sorted(entities):
            fact = facts[entity]
            if not isinstance(fact, dict) or set(fact) - allowed:
                raise ValueError('opening cash fact contains unconsumed fields')
            ids = fact.get('evidence_ids')
            if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in catalog for i in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError('opening cash fact must cite distinct documents included in the driver')
            proof = {**fact, 'value': bridges[entity]['opening_cash']}
            error = prove('liquidity_opening_cash', proof, [catalog[i] for i in ids], currency + ' million',
                          period, expected_entity=entity)
            if error:
                raise ValueError(entity + ': ' + error)
        return None
    except (ValueError, TypeError, KeyError) as exc:
        return str(exc)
