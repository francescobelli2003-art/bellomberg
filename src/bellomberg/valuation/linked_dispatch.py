"""Apply the same linked schedules to stand-alone and SOTP child workbooks."""
def apply_linked_model(wb, payload):
    method = payload.get('method')
    if method == 'operating_fcff':
        from .documented_formulas import operating_formula_ready, apply_operating_formulas
        if not operating_formula_ready(payload):
            return False
        from .documented_presentation import present_operating
        present_operating(wb, payload)
        apply_operating_formulas(wb, payload)
        return True
    modules = {
        'bank_residual_income': ('bank_formulas', 'apply_bank_formulas'),
        'insurance_pc_distributable_equity': ('insurance_formulas', 'apply_insurance_formulas'),
        'insurance_life_distributable_equity': ('insurance_formulas', 'apply_insurance_formulas'),
        'regulated_rab': ('rab_formulas', 'apply_rab_formulas'),
        'property_nav': ('property_formulas', 'apply_property_formulas'),
        'fund_nav': ('nav_formulas', 'apply_nav_formulas'),
        'digital_asset_nav': ('nav_formulas', 'apply_nav_formulas'),
        'resources_asset_dcf': ('finite_formulas', 'apply_finite_formulas'),
        'property_development_fcff': ('finite_formulas', 'apply_finite_formulas'),
        'managed_care_distributable_equity': ('managed_care_formulas', 'apply_managed_care_formulas'),
        'development_rnpv': ('development_formulas', 'apply_development_formulas'),
        'mixed_business_sotp': ('sotp_formulas', 'apply_sotp_formulas'),
    }
    if method not in modules:
        return False
    from importlib import import_module
    module, name = modules[method]
    return getattr(import_module('.'+module, __package__), name)(wb, payload)
