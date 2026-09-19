"""Economic methods and their input contracts, independent of holdings and providers.

S1 selects a method; it does not acquire inputs or assess a fair value. ``integrated``
records an available application adapter; versioned record adapters additionally
require source-bound consumption and case reconciliations, not just classification. Non-integrated profiles cannot
request a legacy route. All economic assumptions remain sourced case inputs.
"""

from copy import deepcopy
import json
from typing import Any


CONTRACT_VERSION = "1"
REGISTRY_VERSION = "1"
_DECISION_STATUSES = frozenset({"resolved", "ambiguous", "unknown", "source_error"})
_RECORD_METADATA = ("entity", "period", "unit", "accounting_basis", "source_id", "as_of")
_FINANCIAL_PERIODS = (
    "Historical fiscal periods identified by start/end dates and fiscal calendar.",
    "Forecast intervals explicitly supplied by the case, with no fixed horizon.",
    "Valuation cutoff and source publication dates must be recorded separately.",
)
_FINANCIAL_SOURCES = (
    "Issuer filings or audited/statutory financial statements.",
    "Dated issuer guidance or provider consensus with attributable underlying source.",
    "Explicit analyst assumptions identified separately from reported observations.",
)


def _requirements(fields, *, drivers, reconciliations, periods=_FINANCIAL_PERIODS,
                  sources=_FINANCIAL_SOURCES):
    return {
        "fields": [{"field": field, "required": True, "description": description}
                   for field, description in fields],
        "record_metadata": list(_RECORD_METADATA),
        "periods": list(periods),
        "sources": list(sources),
        "reconciliations": list(reconciliations),
        "drivers": list(drivers),
        "scenarios": ["Case assumptions and alternatives with evidence and sensitivity."],
    }


_EQUITY_INPUTS = (
    ("valuation_perimeter", "Issuer, consolidated and legal-entity perimeter and share classes."),
    ("discount_rate_inputs", "Sourced currency-consistent discount-rate inputs and assumptions."),
    ("diluted_shares", "Diluted share denominator and share-class/ADR reconciliation."),
    ("quotation_units", "Reporting and quotation currencies, units and verified conversion."),
)
_CAPITAL_INPUTS = (
    ("legal_entity_capital", "Capital, required levels and buffers for each legal entity and regime."),
    ("legal_entity_liquidity", "Cash liquidity, restrictions and transfers by legal entity."),
    ("parent_ledger", "Parent cash, debt service, expenses, funding and distribution ledger."),
)
_CAPITAL_RECONCILIATIONS = (
    "Consolidated earnings to legal-entity earnings without duplicating subsidiaries.",
    "Opening capital plus earnings, transfers and other changes to closing capital.",
    "Distributions respect both entity capital and liquidity restrictions.",
    "Subsidiary transfers and parent debt/cash counted once in shareholder flows.",
)


_METHODS = {
    "operating_fcff": {
        "rationale": "Operating cash generation and reinvestment support enterprise FCFF valuation.",
        "support_status": "integrated", "legacy_route": "operating",
        "adapter": "bellomberg.valuation.operating_adapter:generate_operating", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("historical_financials", "Income, balance-sheet and cash-flow history on a reconciled basis."),
            ("revenue_drivers", "Evidence-based volume, price, mix or recurring-revenue forecast."),
            ("margin_drivers", "Operating costs, margins, taxes and material accounting adjustments."),
            ("reinvestment", "Capital expenditure, depreciation and working-capital investment."),
            ("terminal_assumptions", "Justified continuing economics or explicit finite-asset termination."),
            ("enterprise_equity_bridge", "Cash, debt, leases, minorities and other claims counted once."),
        ), drivers=("revenue", "operating_profit", "tax", "reinvestment", "terminal_economics"),
            reconciliations=("Reported earnings to operating cash flow and FCFF.",
                             "Enterprise value to equity value and per-share units.")),
    },
    "bank_residual_income": {
        "rationale": "Balance-sheet credit economics require equity returns and capital retention.",
        "support_status": "integrated", "legacy_route": "bank",
        "adapter": "bellomberg.valuation.bank_adapter:generate_bank", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + _CAPITAL_INPUTS + (
            ("forecast_periods", "Explicit forecast intervals and calendar-consistent discounting."),
            ("tangible_book_equity", "Opening equity and tangible-book reconciliation by share class."),
            ("regulatory_capital", "Applicable regulatory regime, risk exposures and capital requirements."),
            ("equity_return_path", "Forecast earnings, credit losses and return on a consistent equity base."),
            ("capital_retention", "Growth, required capital and permitted shareholder distributions."),
            ("terminal_assumptions", "Case-sourced franchise fade and sustainable terminal economics."),
        ), drivers=("earnings", "credit_losses", "equity_returns", "capital_growth", "distributions"),
            reconciliations=("Earnings, retained capital and distributions reconcile to closing equity.",
                             "Book-equity basis reconciles to regulatory and tangible capital.")),
    },
    "managed_care_distributable_equity": {
        "rationale": "Health-plan earnings require an entity capital/liquidity bridge to shareholder cash.",
        "support_status": "integrated", "legacy_route": "managed_care", "method_version": "2",
        "adapter": "bellomberg.valuation.managed_care_adapter:generate_managed_care", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + _CAPITAL_INPUTS + (
            ("premium_revenue", "Actual and forecast premium amounts by segment; membership/rate models must reconcile upstream to these consumed amounts."),
            ("medical_costs", "Actual claims and forecast segment MCR on premium revenue, plus separately specified other medical costs."),
            ("operating_expenses", "Administrative costs, investment income, interest and tax bridge."),
            ("accounting_bridge", "Reported earnings to adjusted earnings with no unconsumed adjustments."),
            ("forecast_periods", "Actual/forecast boundary, fiscal calendar and explicit discount intervals."),
        ), drivers=("premium_revenue", "medical_cost_ratio", "expenses", "distributable_capital"),
            reconciliations=_CAPITAL_RECONCILIATIONS + ("Reported/adjusted income and actual/forecast cutoff reconcile.",)),
    },
    "insurance_pc_distributable_equity": {
        "rationale": "Property/casualty insurance needs claims, reserves and entity capital before distributions.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.insurance_adapter:generate_insurance", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + _CAPITAL_INPUTS + (
            ("premiums", "Written/earned premiums and growth on a stated gross/net basis."),
            ("claims_reserves", "Claims development, reserve adequacy and catastrophe exposure."),
            ("reinsurance", "Reinsurance recoverables, ceded premiums and counterparties."),
            ("investment_income", "Investment assets, cash yield and realized/unrealized accounting."),
            ("expenses_tax", "Expense and tax forecasts consistent with underwriting and investments."),
            ("regulatory_capital", "Source-calculated insurance common-equity requirements, applicable regime and buffers."),
            ("capital_retention", "Permitted and funded shareholder distributions."),
            ("forecast_periods", "Explicit forecast intervals and calendar-consistent discounting."),
            ("accounting_bridge", "Entity balance sheets and consolidated income reconciled to the capital ledger."),
            ("terminal_assumptions", "Insurance economics, capital and cash supporting continuing distributions."),
        ), drivers=("premiums", "claims", "expenses", "reinsurance", "investments", "capital"),
            reconciliations=_CAPITAL_RECONCILIATIONS + ("Claims, reserves and reinsurance reconcile without duplication.",)),
    },
    "insurance_life_distributable_equity": {
        "rationale": "Life insurance requires in-force/new-business earnings and capital-release projections.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.insurance_adapter:generate_insurance", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + _CAPITAL_INPUTS + (
            ("in_force_business", "In-force cash flows, guarantees, policyholder behavior and liabilities."),
            ("new_business", "New-business volumes, acquisition costs and required capital."),
            ("actuarial_assumptions", "Mortality, morbidity, lapse and discount assumptions with sources."),
            ("investment_income", "Asset returns, duration and asset/liability mismatch."),
            ("regulatory_capital", "Source-calculated insurance common-equity requirements and buffers."),
            ("capital_retention", "Permitted and funded distributions with explicit ownership."),
            ("insurance_liabilities", "Actuarial GAAP reserves and paid/outstanding claims, distinct from profit metrics."),
            ("expenses_tax", "Cash expenses and tax with stated accounting treatment."),
            ("forecast_periods", "Annual periods and explicit discount intervals."),
            ("terminal_assumptions", "Sustainable in-force, production, reserves, assets and capital."),
            ("accounting_bridge", "Accounting profit, CSM or embedded-value metrics reconciled to cash."),
        ), drivers=("in_force", "new_business", "actuarial_experience", "investments", "capital_release"),
            reconciliations=_CAPITAL_RECONCILIATIONS + ("Accounting or embedded-value measures are not cash by assumption.",)),
    },
    "regulated_rab": {
        "rationale": "A recognized regulated asset base and documented return regime support network valuation.",
        "support_status": "integrated", "legacy_route": "rab",
        "adapter": "bellomberg.valuation.rab_adapter:generate_rab", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("regulatory_regime", "Jurisdiction, regulator, allowed-revenue rules and review period."),
            ("recognized_regulatory_base", "Recognized opening asset base and scope, not accounting PPE by proxy."),
            ("allowed_return", "Allowed return, inflation/indexation and tax conventions from the regulator."),
            ("network_investment", "Recognized capex, depreciation, disposals and asset-base roll-forward."),
            ("cash_distributions", "Earnings, financing and sustainable cash distribution bridge."),
            ("terminal_assumptions", "Explicit continuing cash, asset base and financing stability."),
        ), drivers=("recognized_asset_base", "allowed_return", "capex", "depreciation", "distributions"),
            reconciliations=("Opening regulatory base plus eligible investment minus depreciation/disposals.",
                             "Regulatory earnings reconcile to financing and shareholder distributions.")),
    },
    "fund_nav": {
        "rationale": "An investment vehicle is assessed through attributable NAV and its quotation units.",
        "support_status": "integrated", "legacy_route": "mnav",
        "adapter": "bellomberg.valuation.nav_adapter:generate_nav", "method_version": "2", "record_adapter": True,
        "requirements": _requirements((
            ("valuation_perimeter", "Vehicle and underlying holding perimeter."),
            ("nav_source", "Official NAV publisher, publication date and accessible source identity."),
            ("nav_per_share", "Reported NAV, per-share denominator and NAV valuation date."),
            ("quotation_units", "NAV/quotation currencies and units with verified conversion."),
            ("vehicle_liabilities", "Debt, expenses and other claims included or excluded from NAV."),
            ("distribution_policy", "Income distributions, fees and dilution affecting NAV comparability."),
            ("diluted_shares", "Verified common-share denominator on the declared NAV basis."),
            ("valuation_target", "Explicit analyst NAV target and its economic basis; no assumed parity."),
        ), drivers=("underlying_nav", "fees", "liabilities", "share_count"),
            reconciliations=("Underlying asset values, liabilities and share count reconcile to reported NAV.",
                             "NAV and market quotation use the same currency and per-share units."),
            sources=("Official vehicle NAV publications and financial reports.",
                     "Dated market quotation with currency/unit provenance.",
                     "A valuation premium/discount target, if any, is a separate documented analyst view.")),
    },
    "digital_asset_nav": {
        "rationale": "Digital-asset treasury value needs verified holdings, claims and dilution.",
        "support_status": "integrated", "legacy_route": "mnav",
        "adapter": "bellomberg.valuation.nav_adapter:generate_nav", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(tuple(f for f in _EQUITY_INPUTS if f[0]!='discount_rate_inputs') + (
            ("valuation_target", "Explicit equity-NAV or gross-asset EV target; no assumed premium."),
            ("treasury_holdings", "Verified units, asset identity, custodian/perimeter and disclosure date."),
            ("asset_prices", "Dated prices matching each underlying asset and quotation unit."),
            ("treasury_liabilities", "Debt, preferred claims, cash and operating assets reconciled once."),
            ("dilution_terms", "Common shares, convertible claims, issuance and financing terms."),
        ), drivers=("asset_holdings", "asset_prices", "financing_claims", "dilution"),
            reconciliations=("Gross assets to common equity NAV after senior claims.",
                             "Basic and diluted share/claim bases reconcile to the selected NAV convention.")),
    },
    "exposure_analysis": {
        "rationale": "A pooled or direct asset exposure requires holdings/risk analysis rather than company DCF.",
        "support_status": "integrated", "legacy_route": "etf_passive",
        "adapter": "bellomberg.valuation.dcf_engine:generate_valuation",
        "requirements": _requirements((
            ("instrument_identity", "Instrument type, legal structure, underlying and issuer."),
            ("exposure_terms", "Holdings/index or direct asset exposure, leverage and replication terms."),
            ("fees_and_risks", "Costs, issuer/counterparty or custody risks and liquidity."),
            ("quotation_units", "Price currency and units; underlying/vehicle distinctions."),
        ), drivers=("underlying_exposure", "fees", "leverage", "tracking"),
            reconciliations=("Holdings, leverage and terms reconcile to the stated exposure.",),
            sources=("Official prospectus, holdings or underlying specifications.",
                     "Dated price and exposure data from an identified provider.")),
    },
    "property_nav": {
        "rationale": "Property economics need cash-rent/NOI and asset NAV with recurring capex reconciled.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.real_estate_adapter:generate_property", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(tuple(f for f in _EQUITY_INPUTS if f[0]!="discount_rate_inputs") + (
            ("valuation_target", "Explicit equity NAV target with no default premium or parity."),
            ("property_income", "Rents, occupancy, lease incentives and property operating expenses."),
            ("property_capex", "Maintenance, tenant improvements, leasing costs and expansion separated."),
            ("property_nav", "Asset valuations, comparable cap rates and dated assumptions."),
            ("property_debt", "Property/corporate debt, maturities and unencumbered claims."),
            ("ffo_affo_bridge", "Reported FFO/AFFO reconciled to recurring distributable cash."),
        ), drivers=("rent", "occupancy", "property_expenses", "recurring_capex", "cap_rates"),
            reconciliations=("NOI, FFO/AFFO and recurring cash are explicitly reconciled.",
                             "Property NAV to common equity deducts each liability once.")),
    },
    "property_development_fcff": {
        "rationale": "Property-development projects require finite project cash flows and financing.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.property_development_adapter:generate_property_development", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("project_inventory", "Project ownership, status, land, permissions and finite timeline."),
            ("project_cash_flows", "Construction cost, sales or rent, working capital and tax by period."),
            ("project_funding", "Debt drawdown, funding needs, guarantees and residual claims."),
        ), drivers=("project_timing", "construction_cost", "sales", "funding"),
            reconciliations=("Finite project sales and remaining assets are counted once.",
                             "Project financing reconciles to the enterprise/equity valuation basis.")),
    },
    "resources_asset_dcf": {
        "rationale": "Extractive assets require production, depletion and end-of-life obligations.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.resources_adapter:generate_resources", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("reserves_resources", "Recoverable reserves/resources, classification, rights and finite asset life."),
            ("production_prices", "Volumes, decline/recovery assumptions and sourced commodity-price scenarios."),
            ("resource_costs", "Operating cost, royalties, taxes, maintenance and development capex."),
            ("closure_obligations", "Decommissioning, restoration and other terminal liabilities."),
            ("enterprise_equity_bridge", "Debt, cash, minorities and asset ownership reconciled once."),
        ), drivers=("production", "prices", "depletion", "costs", "capex", "closure"),
            reconciliations=("Cumulative extraction reconciles to recoverable reserves and replenishment.",
                             "Finite asset economics and end-of-life obligations reconcile to equity.")),
    },
    "development_rnpv": {
        "rationale": "Precommercial assets require conditional outcome cash flows and development funding.",
        "support_status": "integrated", "legacy_route": None, "adapter": "bellomberg.valuation.development_adapter:generate_development", "method_version": "2", "record_adapter": True,
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("asset_stages", "Asset identity, rights, development stage, milestones and timing."),
            ("conditional_probabilities", "Sourced conditional transition probabilities and scenario structure."),
            ("development_cash_flows", "Costs, milestones, royalties and commercial flows for each outcome."),
            ("development_funding", "Cash runway, financing needs and dilution by scenario."),
            ("asset_life", "Patent, exclusivity or concession expiry and resulting finite cash flows."),
        ), drivers=("stage_transitions", "timing", "development_cost", "commercial_outcomes", "funding"),
            reconciliations=("Outcome probabilities are conditional and branches are not double-counted.",
                             "Risk is not penalized twice through probabilities and discount assumptions.")),
    },
    "mixed_business_sotp": {
        "rationale": "Materially different business economics require segment-specific methods and a group bridge.",
        "support_status": "integrated", "legacy_route": None, "method_version": "2", "record_adapter": True,
        "adapter": "bellomberg.valuation.dcf_engine:_compute_sotp",
        "requirements": _requirements(_EQUITY_INPUTS + (
            ("segment_perimeters", "Complete segment ownership, activity and reporting perimeters."),
            ("segment_valuations", "Each material segment's method, evidence and EV/equity valuation basis."),
            ("central_costs", "Group costs not already included in segment cash flows."),
            ("group_equity_bridge", "Intercompany elimination, cash/debt, minorities and cross-holdings."),
        ), drivers=("segment_economics", "ownership", "central_costs", "group_claims"),
            reconciliations=("Material segment coverage is complete before a total is usable.",
                             "No EV/equity mixing, intercompany duplication or double-counted debt.")),
    },
}

_OPERATING_PROFILES = (
    "payment_network", "payment_processor", "fee_asset_manager", "broker_fee", "software",
    "hardware", "semiconductors", "semiconductor_fabless", "semiconductor_foundry",
    "semiconductor_equipment", "manufacturing", "services", "telecom", "media",
    "consumer_discretionary", "consumer_staples", "pharma_mature", "medtech", "hospital",
    "energy_services", "merchant_generation", "contracted_generation",
)
_PROFILE_METHOD = {profile: "operating_fcff" for profile in _OPERATING_PROFILES}
_PROFILE_METHOD.update({
    "bank": "bank_residual_income", "balance_sheet_lender": "bank_residual_income",
    "mortgage_lender": "bank_residual_income", "managed_care": "managed_care_distributable_equity",
    "insurance_pc": "insurance_pc_distributable_equity", "insurance_life": "insurance_life_distributable_equity",
    "regulated_network": "regulated_rab", "property_owner": "property_nav",
    "property_developer": "property_development_fcff", "resources": "resources_asset_dcf",
    "development_asset": "development_rnpv", "mixed_business": "mixed_business_sotp",
    "cef": "fund_nav", "investment_holding": "fund_nav", "dat": "digital_asset_nav",
    "etf": "exposure_analysis", "etn": "exposure_analysis",
    "commodity": "exposure_analysis", "crypto": "exposure_analysis",
})
# Profile exceptions remain explicit; each integrated record adapter still
# rejects unsupported accounting/regulatory cases or unavailable source inputs.
_PROFILE_SUPPORT = {}
_LEGACY_OPERATING_PROFILES = frozenset({"software", "hardware", "semiconductors", "manufacturing",
                                       "pharma_mature", "medtech", "energy_services", "fee_asset_manager"})
# All operating profiles consume the documented FCFF contract. Data and
# accounting-policy compatibility are assessed for each case by the adapter.


def get_method_requirements(method_id: str) -> dict[str, Any]:
    """Return an isolated input schema; an unregistered method is an explicit error."""
    if not isinstance(method_id, str) or method_id not in _METHODS:
        raise ValueError(f"unknown method: {method_id!r}")
    from .analysis_standard import forecast_standard
    return {"method_id": method_id, "method_version": _METHODS[method_id].get("method_version", "1"), "registry_version": REGISTRY_VERSION,
            **deepcopy(_METHODS[method_id]["requirements"]), "analysis_standard": forecast_standard(method_id)}


def is_record_method(decision):
    """Current documented adapter contract, shared by provenance consumers."""
    if not isinstance(decision, dict):
        return False
    method_id = decision.get('method_id')
    method = _METHODS.get(method_id) if isinstance(method_id, str) else None
    return bool(method and method.get('record_adapter') and
                decision.get('method_version') == method.get('method_version', '1'))


def _validated_profile(raw):
    """Reject malformed contract fields without leaking non-JSON values downstream."""
    profile, errors = {}, []
    scalar_fields = ("profile_id", "profile_version", "instrument", "sector", "subsector",
                     "as_of", "input_fingerprint", "rationale", "decision_status")
    list_fields = ("segments", "evidence_ids", "evidence", "issues", "missing_fields", "excluded_alternatives",
                   "candidate_profiles")
    for field in (*scalar_fields, *list_fields, "economic_features"):
        default = None if field in scalar_fields else {} if field == "economic_features" else []
        value = raw.get(field, default)
        valid = (value is None or isinstance(value, str)) if field in scalar_fields else (
            isinstance(value, dict) if field == "economic_features" else isinstance(value, list))
        if valid and field in ("evidence_ids", "missing_fields"):
            valid = all(isinstance(item, str) and item.strip() for item in value)
        if valid and field in ("segments", "evidence", "issues"):
            valid = all(isinstance(item, dict) for item in value)
        if valid and field == "issues":
            valid = all(isinstance(item.get("code"), str) and isinstance(item.get("message"), str)
                        and isinstance(item.get("blocking"), bool) for item in value)
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            errors.append({"code": "invalid_profile", "field": field, "blocking": True,
                           "message": "Economic profile field is malformed: " + field})
            value = default
        profile[field] = deepcopy(value)
    profile["issues"].extend(errors)
    return profile


def select_valuation_method(profile: dict[str, Any]) -> dict[str, Any]:
    """Select from economic evidence only; classification never validates calculator inputs.

    ``missing_fields`` lists input requirements still to be assessed by the common
    acquisition pipeline. Unknown/conflicting evidence yields no method or route.
    Profile-specific readiness is stricter than generic calculator availability.
    """
    if not isinstance(profile, dict):
        raise ValueError("profile must be a resolved economic-profile dictionary")
    profile = _validated_profile(profile)
    status = profile.get("decision_status")
    issues = deepcopy(profile.get("issues") or [])
    missing = list(profile.get("missing_fields") or [])
    if status not in _DECISION_STATUSES:
        status = "source_error"
        issues.append({"code": "invalid_decision_status", "field": "decision_status", "blocking": True,
                       "message": "Economic profile has an invalid or missing decision status."})
    elif status == "resolved" and any(issue.get("blocking") for issue in issues):
        status = "source_error"
        issues.append({"code": "inconsistent_profile_status", "field": "decision_status", "blocking": True,
                       "message": "Resolved economic profile still contains a blocking issue."})
    result = {key: deepcopy(profile.get(key)) for key in (
        "profile_id", "profile_version", "instrument", "sector", "subsector", "economic_features",
        "segments", "evidence_ids", "evidence", "as_of", "input_fingerprint", "excluded_alternatives",
        "candidate_profiles")}
    result.update({"contract_version": CONTRACT_VERSION, "registry_version": REGISTRY_VERSION,
                   "method_id": None, "method_version": None, "support_status": None,
                   "adapter": None, "legacy_route": None, "decision_status": status,
                   "rationale": profile.get("rationale"), "issues": issues,
                   "missing_fields": missing, "requirements_status": "not_assessed"})
    if status != "resolved":
        return result
    method_id = _PROFILE_METHOD.get(profile.get("profile_id"))
    if method_id is None:
        result["decision_status"] = "unknown"
        result["missing_fields"] = list(dict.fromkeys(missing + ["profile_id"]))
        result["issues"].append({"code": "unmapped_profile", "field": "profile_id", "blocking": True,
                                 "message": "Economic profile has no registered method; no default valuation."})
        return result
    method = _METHODS[method_id]
    support = _PROFILE_SUPPORT.get(profile["profile_id"], method["support_status"])
    required = [field["field"] for field in method["requirements"]["fields"] if field["required"]]
    result.update({"method_id": method_id, "method_version": method.get("method_version", "1"), "support_status": support,
                   "adapter": method["adapter"] if support != "planned" else None,
                   "legacy_route": method["legacy_route"] if support == "integrated" else None,
                   "rationale": " ".join(filter(None, (profile.get("rationale"), method["rationale"]))),
                   "missing_fields": list(dict.fromkeys(missing + required)),
                   "support_note": ("Common managed-care adapter consumes documented records; each result still requires complete data and the usability gate."
                                    if method_id == "managed_care_distributable_equity" else
                                    "Common adapter consumes documented records; each case still requires supported economics, complete data and the usability gate."
                                    if method.get('record_adapter') and support == 'integrated' else
                                    "Legacy application adapter exists; S2 common pipeline and input quality are not certified."
                                    if support == "integrated" else
                                    "A calculator exists; this economic profile has no complete application adapter."
                                    if support == "calculator_only" else
                                    "A dedicated adapter is planned; no valuation is available.")})
    return result


# Explicit copies of authored registry descriptions only. Never used to look up
# provider/user text or to mutate decision, acquisition or fingerprint inputs.
_REGISTRY_DISPLAY_IT = {
    'Historical fiscal periods identified by start/end dates and fiscal calendar.': 'Periodi storici identificati da date iniziali/finali e calendario fiscale.',
    'Forecast intervals explicitly supplied by the case, with no fixed horizon.': 'Intervalli previsionali espliciti del caso, senza orizzonte fisso.',
    'Valuation cutoff and source publication dates must be recorded separately.': 'Cutoff della valutazione e date di pubblicazione delle fonti registrati separatamente.',
    'Issuer filings or audited/statutory financial statements.': 'Comunicazioni societarie o bilanci certificati/statutari.',
    'Dated issuer guidance or provider consensus with attributable underlying source.': 'Guidance societaria datata o consensus del provider con fonte sottostante attribuibile.',
    'Explicit analyst assumptions identified separately from reported observations.': 'Assunzioni esplicite dell’analista distinte dalle osservazioni riportate.',
    'Case assumptions and alternatives with evidence and sensitivity.': 'Assunzioni del caso e alternative con evidenze e sensibilità.',
    'Issuer, consolidated and legal-entity perimeter and share classes.': 'Emittente, perimetro consolidato e delle entità legali, classi azionarie.',
    'Sourced currency-consistent discount-rate inputs and assumptions.': 'Input e assunzioni del tasso di sconto documentati e coerenti con la valuta.',
    'Diluted share denominator and share-class/ADR reconciliation.': 'Denominatore azionario diluito e riconciliazione classi azionarie/ADR.',
    'Reporting and quotation currencies, units and verified conversion.': 'Valute e unità di bilancio e quotazione, conversione verificata.',
    'Capital, required levels and buffers for each legal entity and regime.': 'Capitale, requisiti e buffer per ciascuna entità legale e regime.',
    'Cash liquidity, restrictions and transfers by legal entity.': 'Liquidità di cassa, restrizioni e trasferimenti per entità legale.',
    'Parent cash, debt service, expenses, funding and distribution ledger.': 'Libro della capogruppo: cassa, servizio del debito, spese, finanziamenti e distribuzioni.',
    'Consolidated earnings to legal-entity earnings without duplicating subsidiaries.': 'Utili consolidati riconciliati con quelli delle entità legali senza duplicare le controllate.',
    'Opening capital plus earnings, transfers and other changes to closing capital.': 'Capitale iniziale più utili, trasferimenti e altre variazioni riconciliato con quello finale.',
    'Distributions respect both entity capital and liquidity restrictions.': 'Distribuzioni rispettose dei vincoli di capitale e liquidità delle entità.',
    'Subsidiary transfers and parent debt/cash counted once in shareholder flows.': 'Trasferimenti delle controllate e debito/cassa della capogruppo conteggiati una volta nei flussi agli azionisti.',
    'Operating cash generation and reinvestment support enterprise FCFF valuation.': 'Generazione di cassa operativa e reinvestimenti sostengono la valutazione FCFF dell’impresa.',
    'Income, balance-sheet and cash-flow history on a reconciled basis.': 'Storico di conto economico, stato patrimoniale e flussi di cassa riconciliati.',
    'Evidence-based volume, price, mix or recurring-revenue forecast.': 'Previsione documentata di volumi, prezzi, mix o ricavi ricorrenti.',
    'Operating costs, margins, taxes and material accounting adjustments.': 'Costi operativi, margini, imposte e rettifiche contabili rilevanti.',
    'Capital expenditure, depreciation and working-capital investment.': 'Investimenti, ammortamenti e investimento nel capitale circolante.',
    'Justified continuing economics or explicit finite-asset termination.': 'Economia a regime motivata o termine esplicito degli asset a vita finita.',
    'Cash, debt, leases, minorities and other claims counted once.': 'Cassa, debito, leasing, minoranze e altri diritti conteggiati una volta.',
    'Reported earnings to operating cash flow and FCFF.': 'Utili riportati riconciliati con flusso di cassa operativo e FCFF.',
    'Enterprise value to equity value and per-share units.': 'Valore d’impresa riconciliato con valore del capitale e unità per azione.',
    'Balance-sheet credit economics require equity returns and capital retention.': 'L’economia del credito in bilancio richiede rendimenti del capitale e capitale trattenuto.',
    'Explicit forecast intervals and calendar-consistent discounting.': 'Intervalli previsionali espliciti e attualizzazione coerente con il calendario.',
    'Opening equity and tangible-book reconciliation by share class.': 'Capitale iniziale e riconciliazione del patrimonio tangibile per classe azionaria.',
    'Applicable regulatory regime, risk exposures and capital requirements.': 'Regime normativo applicabile, esposizioni al rischio e requisiti patrimoniali.',
    'Forecast earnings, credit losses and return on a consistent equity base.': 'Previsione di utili, perdite su crediti e rendimento su una base patrimoniale coerente.',
    'Growth, required capital and permitted shareholder distributions.': 'Crescita, capitale richiesto e distribuzioni consentite agli azionisti.',
    'Case-sourced franchise fade and sustainable terminal economics.': 'Evoluzione del vantaggio competitivo e sostenibilità terminale documentate dal caso.',
    'Earnings, retained capital and distributions reconcile to closing equity.': 'Utili, capitale trattenuto e distribuzioni riconciliati con il patrimonio finale.',
    'Book-equity basis reconciles to regulatory and tangible capital.': 'Patrimonio contabile riconciliato con capitale normativo e tangibile.',
    'Health-plan earnings require an entity capital/liquidity bridge to shareholder cash.': 'Gli utili dei piani sanitari richiedono un ponte da capitale/liquidità delle entità alla cassa degli azionisti.',
    'Actual and forecast premium amounts by segment; membership/rate models must reconcile upstream to these consumed amounts.': 'Premi effettivi e previsti per segmento; i modelli iscritti/tariffe devono riconciliarsi a monte con questi importi utilizzati.',
    'Actual claims and forecast segment MCR on premium revenue, plus separately specified other medical costs.': 'Sinistri effettivi e MCR previsto per segmento sui premi, più altri costi medici specificati separatamente.',
    'Administrative costs, investment income, interest and tax bridge.': 'Ponte di costi amministrativi, proventi finanziari, interessi e imposte.',
    'Reported earnings to adjusted earnings with no unconsumed adjustments.': 'Utili riportati riconciliati con quelli rettificati senza rettifiche non utilizzate.',
    'Actual/forecast boundary, fiscal calendar and explicit discount intervals.': 'Confine consuntivo/previsionale, calendario fiscale e intervalli di sconto espliciti.',
    'Reported/adjusted income and actual/forecast cutoff reconcile.': 'Riconciliazione di utili riportati/rettificati e cutoff consuntivo/previsionale.',
    'Property/casualty insurance needs claims, reserves and entity capital before distributions.': 'Le assicurazioni danni richiedono sinistri, riserve e capitale delle entità prima delle distribuzioni.',
    'Written/earned premiums and growth on a stated gross/net basis.': 'Premi contabilizzati/di competenza e crescita su base lorda/netta dichiarata.',
    'Claims development, reserve adequacy and catastrophe exposure.': 'Sviluppo dei sinistri, adeguatezza delle riserve ed esposizione catastrofale.',
    'Reinsurance recoverables, ceded premiums and counterparties.': 'Crediti da riassicurazione, premi ceduti e controparti.',
    'Investment assets, cash yield and realized/unrealized accounting.': 'Attività finanziarie, rendimento di cassa e contabilizzazione realizzato/non realizzato.',
    'Expense and tax forecasts consistent with underwriting and investments.': 'Previsioni di costi e imposte coerenti con sottoscrizione e investimenti.',
    'Source-calculated insurance common-equity requirements, applicable regime and buffers.': 'Requisiti di capitale azionario assicurativo calcolati dalla fonte, regime applicabile e buffer.',
    'Permitted and funded shareholder distributions.': 'Distribuzioni agli azionisti consentite e finanziate.',
    'Entity balance sheets and consolidated income reconciled to the capital ledger.': 'Bilanci delle entità e utile consolidato riconciliati con il libro del capitale.',
    'Insurance economics, capital and cash supporting continuing distributions.': 'Economia assicurativa, capitale e cassa a sostegno delle distribuzioni a regime.',
    'Claims, reserves and reinsurance reconcile without duplication.': 'Sinistri, riserve e riassicurazione riconciliati senza duplicazioni.',
    'Life insurance requires in-force/new-business earnings and capital-release projections.': 'Le assicurazioni vita richiedono proiezioni di utili del portafoglio in essere/nuova produzione e rilascio di capitale.',
    'In-force cash flows, guarantees, policyholder behavior and liabilities.': 'Flussi in essere, garanzie, comportamento degli assicurati e passività.',
    'New-business volumes, acquisition costs and required capital.': 'Volumi di nuova produzione, costi di acquisizione e capitale richiesto.',
    'Mortality, morbidity, lapse and discount assumptions with sources.': 'Ipotesi documentate di mortalità, morbilità, riscatti e attualizzazione.',
    'Asset returns, duration and asset/liability mismatch.': 'Rendimenti degli attivi, duration e disallineamento attività/passività.',
    'Source-calculated insurance common-equity requirements and buffers.': 'Requisiti di capitale azionario assicurativo e buffer calcolati dalla fonte.',
    'Permitted and funded distributions with explicit ownership.': 'Distribuzioni consentite e finanziate con quota di proprietà esplicita.',
    'Actuarial GAAP reserves and paid/outstanding claims, distinct from profit metrics.': 'Riserve attuariali GAAP e sinistri pagati/da pagare, distinti dalle metriche di utile.',
    'Cash expenses and tax with stated accounting treatment.': 'Spese di cassa e imposte con trattamento contabile dichiarato.',
    'Annual periods and explicit discount intervals.': 'Periodi annuali e intervalli di sconto espliciti.',
    'Sustainable in-force, production, reserves, assets and capital.': 'Portafoglio in essere, produzione, riserve, attivi e capitale sostenibili.',
    'Accounting profit, CSM or embedded-value metrics reconciled to cash.': 'Utile contabile, CSM o valore intrinseco riconciliati con la cassa.',
    'Accounting or embedded-value measures are not cash by assumption.': 'Le misure contabili o di valore intrinseco non sono assunte equivalenti alla cassa.',
    'A recognized regulated asset base and documented return regime support network valuation.': 'Una base di attivi regolati riconosciuta e un regime di rendimento documentato sostengono la valutazione della rete.',
    'Jurisdiction, regulator, allowed-revenue rules and review period.': 'Giurisdizione, regolatore, regole dei ricavi consentiti e periodo di revisione.',
    'Recognized opening asset base and scope, not accounting PPE by proxy.': 'Base iniziale riconosciuta e perimetro, senza usare immobilizzazioni contabili come proxy.',
    'Allowed return, inflation/indexation and tax conventions from the regulator.': 'Rendimento consentito, inflazione/indicizzazione e convenzioni fiscali del regolatore.',
    'Recognized capex, depreciation, disposals and asset-base roll-forward.': 'Investimenti riconosciuti, ammortamenti, dismissioni e variazione della base di attivi.',
    'Earnings, financing and sustainable cash distribution bridge.': 'Ponte tra utili, finanziamento e distribuzione sostenibile di cassa.',
    'Explicit continuing cash, asset base and financing stability.': 'Cassa a regime, base di attivi e stabilità dei finanziamenti esplicite.',
    'Opening regulatory base plus eligible investment minus depreciation/disposals.': 'Base regolata iniziale più investimenti ammissibili meno ammortamenti/dismissioni.',
    'Regulatory earnings reconcile to financing and shareholder distributions.': 'Utili regolati riconciliati con finanziamento e distribuzioni agli azionisti.',
    'An investment vehicle is assessed through attributable NAV and its quotation units.': 'Un veicolo d’investimento viene valutato tramite NAV attribuibile e unità di quotazione.',
    'Vehicle and underlying holding perimeter.': 'Perimetro del veicolo e delle partecipazioni sottostanti.',
    'Official NAV publisher, publication date and accessible source identity.': 'Editore del NAV ufficiale, data di pubblicazione e identità accessibile della fonte.',
    'Reported NAV, per-share denominator and NAV valuation date.': 'NAV riportato, denominatore per azione e data di valutazione del NAV.',
    'NAV/quotation currencies and units with verified conversion.': 'Valute e unità di NAV/quotazione con conversione verificata.',
    'Debt, expenses and other claims included or excluded from NAV.': 'Debito, spese e altri diritti inclusi o esclusi dal NAV.',
    'Income distributions, fees and dilution affecting NAV comparability.': 'Distribuzioni di reddito, commissioni e diluizione che incidono sulla confrontabilità del NAV.',
    'Verified common-share denominator on the declared NAV basis.': 'Denominatore di azioni ordinarie verificato sulla base NAV dichiarata.',
    'Explicit analyst NAV target and its economic basis; no assumed parity.': 'Obiettivo NAV esplicito dell’analista e sua base economica; nessuna parità presunta.',
    'Underlying asset values, liabilities and share count reconcile to reported NAV.': 'Valori degli attivi sottostanti, passività e numero di azioni riconciliati con il NAV riportato.',
    'NAV and market quotation use the same currency and per-share units.': 'NAV e quotazione di mercato utilizzano le stesse valute e unità per azione.',
    'Official vehicle NAV publications and financial reports.': 'Pubblicazioni ufficiali del NAV del veicolo e relazioni finanziarie.',
    'Dated market quotation with currency/unit provenance.': 'Quotazione di mercato datata con provenienza di valuta/unità.',
    'A valuation premium/discount target, if any, is a separate documented analyst view.': 'Un eventuale obiettivo di premio/sconto è una distinta tesi documentata dell’analista.',
    'Digital-asset treasury value needs verified holdings, claims and dilution.': 'La tesoreria di asset digitali richiede partecipazioni, diritti e diluizione verificati.',
    'Explicit equity-NAV or gross-asset EV target; no assumed premium.': 'Obiettivo esplicito di NAV del capitale o EV degli attivi lordi; nessun premio presunto.',
    'Verified units, asset identity, custodian/perimeter and disclosure date.': 'Unità verificate, identità degli attivi, depositario/perimetro e data di pubblicazione.',
    'Dated prices matching each underlying asset and quotation unit.': 'Prezzi datati coerenti con ogni attivo sottostante e unità di quotazione.',
    'Debt, preferred claims, cash and operating assets reconciled once.': 'Debito, diritti privilegiati, cassa e attivi operativi riconciliati una volta.',
    'Common shares, convertible claims, issuance and financing terms.': 'Azioni ordinarie, diritti convertibili, condizioni di emissione e finanziamento.',
    'Gross assets to common equity NAV after senior claims.': 'Attivi lordi riconciliati con NAV del capitale ordinario dopo i diritti prioritari.',
    'Basic and diluted share/claim bases reconcile to the selected NAV convention.': 'Basi azionarie/diritti semplici e diluite riconciliate con la convenzione NAV scelta.',
    'A pooled or direct asset exposure requires holdings/risk analysis rather than company DCF.': 'Un’esposizione collettiva o diretta richiede analisi di partecipazioni/rischio anziché un DCF societario.',
    'Instrument type, legal structure, underlying and issuer.': 'Tipo di strumento, struttura legale, sottostante ed emittente.',
    'Holdings/index or direct asset exposure, leverage and replication terms.': 'Partecipazioni/indice o esposizione diretta, leva e condizioni di replica.',
    'Costs, issuer/counterparty or custody risks and liquidity.': 'Costi, rischi di emittente/controparte o custodia e liquidità.',
    'Price currency and units; underlying/vehicle distinctions.': 'Valuta e unità del prezzo; distinzione sottostante/veicolo.',
    'Holdings, leverage and terms reconcile to the stated exposure.': 'Partecipazioni, leva e condizioni riconciliate con l’esposizione dichiarata.',
    'Official prospectus, holdings or underlying specifications.': 'Prospetto ufficiale, partecipazioni o specifiche del sottostante.',
    'Dated price and exposure data from an identified provider.': 'Prezzi ed esposizione datati di un provider identificato.',
    'Property economics need cash-rent/NOI and asset NAV with recurring capex reconciled.': 'L’economia immobiliare richiede canoni di cassa/NOI e NAV degli attivi con investimenti ricorrenti riconciliati.',
    'Explicit equity NAV target with no default premium or parity.': 'Obiettivo esplicito di NAV del capitale senza premio o parità predefiniti.',
    'Rents, occupancy, lease incentives and property operating expenses.': 'Canoni, occupazione, incentivi contrattuali e costi operativi degli immobili.',
    'Maintenance, tenant improvements, leasing costs and expansion separated.': 'Manutenzione, migliorie degli inquilini, costi di locazione ed espansione separati.',
    'Asset valuations, comparable cap rates and dated assumptions.': 'Valutazioni degli attivi, tassi di capitalizzazione comparabili e ipotesi datate.',
    'Property/corporate debt, maturities and unencumbered claims.': 'Debito immobiliare/societario, scadenze e diritti non gravati da vincoli.',
    'Reported FFO/AFFO reconciled to recurring distributable cash.': 'FFO/AFFO riportati riconciliati con la cassa ricorrente distribuibile.',
    'NOI, FFO/AFFO and recurring cash are explicitly reconciled.': 'NOI, FFO/AFFO e cassa ricorrente riconciliati esplicitamente.',
    'Property NAV to common equity deducts each liability once.': 'Dal NAV immobiliare al capitale ordinario, ogni passività si deduce una volta.',
    'Property-development projects require finite project cash flows and financing.': 'I progetti di sviluppo immobiliare richiedono flussi di progetto finiti e finanziamenti.',
    'Project ownership, status, land, permissions and finite timeline.': 'Proprietà, stato, terreni, permessi e durata finita del progetto.',
    'Construction cost, sales or rent, working capital and tax by period.': 'Costi di costruzione, vendite o canoni, circolante e imposte per periodo.',
    'Debt drawdown, funding needs, guarantees and residual claims.': 'Utilizzo del debito, fabbisogno finanziario, garanzie e diritti residui.',
    'Finite project sales and remaining assets are counted once.': 'Vendite di progetto finite e attivi residui conteggiati una volta.',
    'Project financing reconciles to the enterprise/equity valuation basis.': 'Finanziamento del progetto riconciliato con la base di valutazione impresa/capitale.',
    'Extractive assets require production, depletion and end-of-life obligations.': 'Gli attivi estrattivi richiedono produzione, esaurimento e obblighi di fine vita.',
    'Recoverable reserves/resources, classification, rights and finite asset life.': 'Riserve/risorse recuperabili, classificazione, diritti e vita finita degli attivi.',
    'Volumes, decline/recovery assumptions and sourced commodity-price scenarios.': 'Volumi, ipotesi di declino/recupero e scenari documentati dei prezzi delle materie prime.',
    'Operating cost, royalties, taxes, maintenance and development capex.': 'Costi operativi, royalties, imposte, investimenti di manutenzione e sviluppo.',
    'Decommissioning, restoration and other terminal liabilities.': 'Dismissione, ripristino e altre passività terminali.',
    'Debt, cash, minorities and asset ownership reconciled once.': 'Debito, cassa, minoranze e proprietà degli attivi riconciliati una volta.',
    'Cumulative extraction reconciles to recoverable reserves and replenishment.': 'Estrazione cumulata riconciliata con riserve recuperabili e reintegro.',
    'Finite asset economics and end-of-life obligations reconcile to equity.': 'Economia a vita finita e obblighi di fine vita riconciliati con il capitale.',
    'Precommercial assets require conditional outcome cash flows and development funding.': 'Gli attivi precommerciali richiedono flussi condizionati agli esiti e finanziamenti dello sviluppo.',
    'Asset identity, rights, development stage, milestones and timing.': 'Identità dell’attivo, diritti, fase di sviluppo, traguardi e tempi.',
    'Sourced conditional transition probabilities and scenario structure.': 'Probabilità condizionate di transizione e struttura degli scenari documentate.',
    'Costs, milestones, royalties and commercial flows for each outcome.': 'Costi, traguardi, royalties e flussi commerciali per ciascun esito.',
    'Cash runway, financing needs and dilution by scenario.': 'Autonomia di cassa, fabbisogno finanziario e diluizione per scenario.',
    'Patent, exclusivity or concession expiry and resulting finite cash flows.': 'Scadenza di brevetti, esclusiva o concessioni e conseguenti flussi finiti.',
    'Outcome probabilities are conditional and branches are not double-counted.': 'Le probabilità degli esiti sono condizionate e i rami non sono conteggiati due volte.',
    'Risk is not penalized twice through probabilities and discount assumptions.': 'Il rischio non viene penalizzato due volte tramite probabilità e ipotesi di sconto.',
    'Materially different business economics require segment-specific methods and a group bridge.': 'Attività con economie sostanzialmente diverse richiedono metodi per segmento e un ponte di gruppo.',
    'Complete segment ownership, activity and reporting perimeters.': 'Perimetri completi di proprietà, attività e rendicontazione dei segmenti.',
    "Each material segment's method, evidence and EV/equity valuation basis.": 'Metodo, evidenze e base di valutazione EV/capitale di ogni segmento rilevante.',
    'Group costs not already included in segment cash flows.': 'Costi di gruppo non già inclusi nei flussi dei segmenti.',
    'Intercompany elimination, cash/debt, minorities and cross-holdings.': 'Elisioni infragruppo, cassa/debito, minoranze e partecipazioni incrociate.',
    'Material segment coverage is complete before a total is usable.': 'Copertura completa dei segmenti rilevanti prima di utilizzare un totale.',
    'No EV/equity mixing, intercompany duplication or double-counted debt.': 'Nessuna commistione EV/capitale, duplicazione infragruppo o doppio conteggio del debito.',
}


def method_presentation(decision):
    """Current registry labels, separate from immutable acquired/stored identity."""
    from bellomberg.core.presentation import message, render_payload
    method_id = decision.get("method_id") if isinstance(decision, dict) else None
    method = _METHODS.get(method_id) if isinstance(method_id, str) else None
    result = {"requirements_display": None,
              "decision_display": {"method_rationale": None, "support_note": None,
                                   "registry_version": REGISTRY_VERSION}}
    if method is None:
        return result

    def describe(authored):
        # A missing translation is a programming error, not a source-text fallback.
        return message(_REGISTRY_DISPLAY_IT[authored], authored)

    requirements = get_method_requirements(method_id)
    for field in requirements["fields"]:
        field["description"] = describe(field["description"])
    for key in ("periods", "sources", "reconciliations", "scenarios"):
        requirements[key] = [describe(item) for item in requirements[key]]
    support = decision.get("support_status")
    if method_id == "managed_care_distributable_equity":
        note = message("L’adattatore comune managed-care utilizza record documentati; ogni risultato richiede ancora dati completi e il gate di utilizzabilità.",
                       "Common managed-care adapter consumes documented records; each result still requires complete data and the usability gate.")
    elif method.get("record_adapter") and support == "integrated":
        note = message("L’adattatore comune utilizza record documentati; ogni caso richiede ancora economia supportata, dati completi e il gate di utilizzabilità.",
                       "Common adapter consumes documented records; each case still requires supported economics, complete data and the usability gate.")
    elif support == "integrated":
        note = message("Esiste un adattatore applicativo legacy; la pipeline comune S2 e la qualità degli input non sono certificate.",
                       "Legacy application adapter exists; S2 common pipeline and input quality are not certified.")
    elif support == "calculator_only":
        note = message("Esiste un calcolatore; questo profilo economico non ha un adattatore applicativo completo.",
                       "A calculator exists; this economic profile has no complete application adapter.")
    elif support == "planned":
        note = message("È previsto un adattatore dedicato; nessuna valutazione disponibile.",
                       "A dedicated adapter is planned; no valuation is available.")
    else:
        note = None
    result["requirements_display"] = requirements
    result["decision_display"].update(method_rationale=describe(method["rationale"]), support_note=note)
    return render_payload(result)
