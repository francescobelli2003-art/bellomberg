"""One continuing-year transport contract, matching the existing capital engine."""
from .distributable_equity import CAPITAL_FIELDS, SUB_FIELDS, SUB_PATHS, CASH_SIGNS


def terminal_schema(*, allow_retained_flows=False):
    def obj(fields):
        return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}
    number = {'type': 'number'}
    text = {'type': 'string', 'minLength': 1}
    path = {'type': 'array', 'items': number, 'minItems': 1, 'maxItems': 1}
    sub = obj({key: text if key == 'id' else path if key in SUB_PATHS else number for key in sorted(SUB_FIELDS)})
    text_keys = {'distribution_policy', 'reconciliation_basis', 'upstream_approval_basis', 'terminal_basis'}
    path_keys = {'parent_cash_minimum', 'parent_gaap_net_income', 'consolidation_adjustments', 'discount_periods'}
    capital = {key: text if key in text_keys else path if key in path_keys else number for key in sorted(CAPITAL_FIELDS)}
    capital.update(distribution_policy={'const': 'full_sweep_after_buffers'}, terminal_equity={'const': 0},
        parent_cash_flows=obj({key: path for key in CASH_SIGNS}),
        subsidiaries={'type': 'array', 'minItems': 1, 'items': sub,
                      'description': 'List of legal entities, each with its explicit id; never a map keyed by entity.'})
    constraint = obj({'id': text, **{key: path for key in ('exposure', 'ratio', 'buffer', 'absolute_floor')},
                      'terminal_requirement': number})
    entity_constraints = obj({'basis': {'const': 'common_equity'},
                              'constraints': {'type': 'array', 'minItems': 1, 'items': constraint}})
    liquidity = obj({'opening_cash': number, **{key: path for key in (
        'operating_cash', 'investing_cash', 'financing_cash', 'parent_fees_paid', 'parent_tax_paid')}})
    result = obj({'capital': obj(capital),
                'capital_constraints': {'type': 'object', 'minProperties': 1, 'additionalProperties': entity_constraints},
                'liquidity_bridge': {'type': 'object', 'minProperties': 1, 'additionalProperties': liquidity}})
    if allow_retained_flows:
        # Optional: absence retains the existing proportional-stock contract.
        result['properties']['statutory_projection'] = {'const': 'retained_flows_at_g'}
    return result
