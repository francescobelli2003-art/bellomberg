# Documented sector valuations

The economic resolver selects a method from sourced business evidence, independently
of the ticker and portfolio. A recognized method is not a usable fair value. Missing
data, unsupported accounting conventions, stale sources or failed reconciliations
produce acquisition tasks and **FV n.d.** through tools, research, cache and F17.

This guide describes the documented adapters being delivered in the sector plan.
Managed care retains its [existing input contract](../managed-care-inputs.md).
Runtime activation and metadata migration are separate operations.

## Common record contract

Supply `method_records` to `get_valuation` or `build_dcf_model`, or return the same
records from the injectable `method_inputs` provider. Existing filings, statements,
guidance and consensus remain available in the acquisition snapshot. An analyst
maps their information to the consumed driver records; raw filings do not certify
a forecast automatically. The tools publish each adapter's driver mapping.

Each record has `field`, `driver`, `scenario`, `value`, `entity`, `period`, `unit`,
`accounting_basis`, `source_id` (a source URL), `as_of`, `valid_until`, `kind` and
`rationale`. `kind` distinguishes `historical`, `company_guidance` and
`analyst_estimate`. A proxy is retained as evidence but cannot certify a usable FV.
An explicit analyst set replaces the previous analyst set while preserving its
history; independent provider records remain, and contradictory records block use.
Duplicate drivers, extra fields and non-consumed inputs are errors.

The following shared structures apply to the documented FCFF adapter. They do not
replace the distinct managed-care structures:

| Driver (model scope) | Exact value keys | Record field / basis / unit |
|---|---|---|
| `perimeter` | `entity`, ISO `currency`, `share_class` | valuation_perimeter / scope / contract |
| `calendar` | `valuation_date`, `periods` as `[{start,end},...]`, `discount_convention` | valuation_perimeter / calendar / contract |
| `quotation` | `financial_currency`, `quote_currency`, `quote_unit`, `quote_units_per_currency`, `financial_to_quote_rate`, `shares_per_quote`, `share_class`, `price`, `price_as_of` | quotation_units / quotation / contract |

Opening records use `period=valuation_date`. Future records and the perimeter and
calendar structures use the exact `start/end` periods joined by `|`. All records
refer to the perimeter entity. Monetary values are in millions of the financial
currency, unit `<ISO currency> million`; shares use `million shares`.

Periods must be contiguous after opening balances. `annual_end` discounts whole
fiscal years at integer intervals; `ACT/365F` uses actual date differences divided
by 365. The FCFF adapter requires complete annual fiscal periods under either
convention: a stub is rejected rather than implicitly annualized. Non-calendar
fiscal years are supported. There is no fixed forecast length.

Price and opening balances have the same valuation date. The information cutoff
may be later and is recorded separately. This is a scenario calculation at the
opening cutoff, not an observed historical fair value or an implicit comparison
against today's price. FX, quotation scale and share-class conversions are
explicit. GBX/GBp is accepted only as GBP multiplied by 100.

Documented adapters additionally retain a `market_quote/1` block from the same
hashed acquisition snapshot. Its only price source is the profile's
`regularMarketPrice`, paired with `regularMarketTime`, instrument identity and
exchange time zone. Missing, stale or incompatible observations have an explicit
status and no observed-price upside. There is no substitute from the portfolio,
model quotation or another price field. A different financial/quote currency
leaves that upside unavailable: the model's historical FX rate is not rolled.

`upside_pct` remains the comparison with the model-date price. The separate
`market_quote.upside_base_pct` compares the historical base FV with the observed
price; it does not roll the FV forward. Scenario observations and source metadata
are retained in the payload, sidecar and workbook. API/committee/email/score views
recheck freshness without changing the stored observation. Freshness uses the
previous weekday, not an exchange holiday calendar; it is an observation-age
rule, not a live-market guarantee. The model's sanity checks and score calculation
continue to use its historical price. Unsupported legacy observations are shown
as unavailable.

`analysis_context.scenario_rationale` supplies explicit `bear`, `base`, `bull`
explanations. Assumption changes require a new generation. Prior acquisition
snapshots remain immutable. Revision attribution that is not reconciled to this
adapter is unavailable; a textual revision list does not create an FV bridge.

## Operating FCFF

The adapter reuses `_scenario_numbers` and `_dcf_value` in `dcf_buyside_v3`.
It does not call calibration, taxonomy priors, peer seed lists, automatic scenario
fades, beta/WACC clamps, median method blends or fixed scenario probabilities.
No premium or growth rate follows from the sector's name.

The tool's mapping provides each field, unit, basis, timing, shape and scope.
All listed scalar/path records are mandatory, including an explicit zero:

| Scope | Drivers | Interpretation |
|---|---|---|
| model/opening | `historical_revenue`, `opening_nwc`, `shares` | Reconciled opening revenue, working capital and diluted denominator |
| model/future | `capdev_amortization_years` | Explicit positive integral useful life, not a sector default |
| each scenario/future | `revenue_growth`, `gross_margin`, `rnd_pct`, `sga_pct`, `capdev_pct`, `da_tan_pct`, `tax_rate`, `capex_pct`, `nwc_pct` | Paths for every fiscal period, ratios in decimal units |
| each scenario/future | `opening_intangible_amortization` | Monetary amortization path of opening capitalized research |
| each scenario/future | `wacc`, `terminal_growth`, `terminal_ronic` | Explicit sourced rates; invalid relations are rejected, never clamped |
| each scenario/future | `net_debt`, `equity_adjustments` | Opening EV-to-common-equity bridge amounts, counted once; no rounding to tenths |
| each scenario/future | `revenue_build`, `accounting_policies`, `terminal_bridge` | Exact structured contracts below |

The opening EV/equity bridge is a scenario assumption at the common opening
valuation date; it is not forecast terminal debt. The whole-forecast record
period identifies the scenario in which that bridge is used.

The volume/price form of `revenue_build` has exactly `basis`, `volume`, `unit_price`, `utilization`,
`other_revenue`. Each numeric key is a complete path. Volume times unit price
times utilization plus other revenue must equal the engine's projected revenue
in every period. The product is measured in millions of financial currency;
`unit_price` must therefore use millions of currency per volume unit. Utilization
is a fraction from zero to one. Other revenue is separately sourced and measured
in the same monetary units, not an unexplained plug to hide an inconsistent build.

| Profiles | Revenue build basis |
|---|---|
| Software, telecom | `subscribers_arpu` |
| Payment networks/processors, fee brokers | `transactions_fee` |
| Fee asset managers | `aum_fee` |
| Media | `audience_yield` |
| Semiconductor foundries, merchant/contracted generation | `capacity_utilization_price` |
| Mature pharma | `product_volume_price` |
| Hospitals | `patients_yield` |
| Hardware, other semiconductors, manufacturing, services, consumer, medtech, energy services | `units_price` |

These are supported builds, not proof that every company in a profile fits them.
Multi-business assets, finite concessions, development assets and resource
reserves require their applicable methods. A cohort, capacity, backlog or product
analysis must reconcile upstream to these consumed amounts. Its unsupported extra
fields cannot be attached and silently ignored.

Manufacturing also accepts `segment_guidance`. This alternative has exactly
`basis` and `segments`, with at least two operating segments and at most one
explicit consolidation item. Each item has exactly `segment_id`, `label`,
`role` (`segment` or `consolidation`), `base`, `base_evidence`, `growth` and
`growth_evidence`. A segment base must be positive; a consolidation base may be
negative but cannot be zero. Omit consolidation when there is none.

`base_evidence` has exactly `kind` (`historical`), `sources`,
`observation_period` (`start`/`end` ISO dates) and `derivation`. Its observation
period is the whole year ending on the valuation date. Each source has exactly
`source_id` (URL) and a nonempty `locator`. The same segment IDs, labels, roles,
bases and base evidence must appear in all scenarios; their order is immaterial.
Their sum must reconcile to opening historical revenue.

`growth` contains one finite rate greater than -100% per model period.
`growth_evidence` has one item per period, each with exactly `kind`, `sources`,
`guidance_period` and `derivation`. The source structure is the same as above;
`guidance_period` is either a `start`/`end` pair or null. `company_guidance` is
accepted only for operating segments when that period exactly matches the model
period. Interpolation, a change of period, or consolidation requires
`analyst_estimate`. If any cell is an estimate, both the `revenue_build` and
aggregate `revenue_growth` record kinds must also be `analyst_estimate`.

For every period, opening segment revenue compounded by its declared growth must
sum to group revenue compounded by the group's declared growth. The adapter
checks the evidence structure and arithmetic; it cannot authenticate a source or
decide whether an economic assumption is sound. There is no inferred segment,
balancing residual, seasonal split or forecast. The existing volume/price form,
annual calendar and valuation method version remain unchanged.

`accounting_policies` has exactly these supported values:

```json
{"tax":"no_loss_tax_credit","sbc":"included_in_operating_costs",
 "leases":"operating_rent_in_costs","research":"expensed_except_explicit_capdev",
 "cycle":"explicit_forecast","patents":"explicit_forecast"}
```

This constrains calculation conventions, not source truth. SBC remains an
operating economic cost; lease rents remain in costs and the opening bridge must
not deduct the same operating-lease obligation again. NOL credits, finance-lease
models or a different research accounting convention require an explicit
reconciliation into the supported basis; unsupported conventions are blocked.
Capitalized development cannot exceed the R&D cost from which it is removed.

`terminal_bridge` contains exactly `normalized_ebit`,
`capitalized_research_adjustment`, `cycle_adjustment`, `expiring_product_loss`,
`replacement_product_income`, `other_adjustment`, all monetary scalars.
The normalized EBIT must reconcile to the final forecast EBIT plus these signed
adjustments, subtracting expiring-product loss. Loss and replacement income are
nonnegative. All inputs require source, date and rationale; a cycle or patent
label alone is insufficient. Unknown material expiries or replacement economics
remain missing inputs rather than a perpetual continuation assumption.

Research is fully expensed in normalized terminal earnings: the capitalized
research adjustment must equal final research amortization minus final
capitalized development. This prevents an incomplete amortization ramp in a short
forecast from being perpetuated. The existing terminal formula applies explicit
growth, final-period tax and reinvestment `g/RONIC` to normalized EBIT. WACC and
RONIC must exceed growth; growth at or below minus 100% is invalid. No implicit
fade, floor, scenario weighting, comparable multiple or extra equity bridge is
introduced.

Excel is an immutable numeric snapshot with shared quality and acquisition sheets.
It does not claim that editing a cell recalculates a second model. Regeneration
creates new UUID workbook/sidecar files and preserves previous files. The same
snapshot and value travel through cache, research, database metadata, committee,
score and F17. A blocked value is suppressed in nested calculation details too.

Reading a stored snapshot uses its own information cutoff: a documented value
does not become unavailable solely because another calendar day has passed.
An explicit cutoff or current cache identity must still match exactly. Missing
records, stale evidence at that cutoff and blocking sanity findings still reject
the value. The committee receives both the valuation date and information cutoff.
This does not update historical prices or assumptions; the existing score limit
for sidecars remains thirty days from their generation timestamp.

Methodological reference: [Damodaran's industry and lifecycle valuation materials](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook.htm).
Financial-service businesses use equity and capital constraints, as discussed in
[Valuing Financial Service Firms](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/finfirm09.pdf);
they do not inherit these operating cash-flow assumptions.

## Bank and balance-sheet credit: version 2

`bank_residual_income` supports documented common-equity cases for banks,
balance-sheet lenders and mortgage lenders. The classification alone certifies
none of their regulatory inputs. The current accounting scope is GAAP with zero
OCI/FX; an incompatible basis is a precise acquisition/valuation block. A future
IFRS or nonzero-OCI bridge must be implemented and tested before it is usable.
The inherited ledger evaluates period ends, not intraperiod liquidity.

Use the common perimeter/calendar/quotation records above. The exact bank schema
is `bank_adapter.SCHEMA`; the tool publishes it. Monetary amounts are millions of
the financial currency. Opening balances use the valuation date, forecast paths
use the joined start/end spans, and terminal contracts use the final period end.

| Records | Consumption |
|---|---|
| `legal_structure` | Parent entity, distinct subsidiary IDs/regimes, `capital_basis=common_equity`, `accounting_basis=GAAP` |
| `opening_common_equity`, `opening_parent_equity`, `opening_consolidation_adjustments`, `opening_intangibles` | Consolidation and tangible/common-book bridge; no net-cash addition |
| Net interest and fee income, operating expenses, credit losses, taxes, other income, preferred/minorities, OCI | Explicit common-income paths reconciled to legal entities and parent |
| `capital.*` | Every existing distributable-equity ledger input, bound to the actual entity and period |
| `capital_constraints` | Every applicable sourced common-equity requirement, calculated and reconciled to required capital |
| `liquidity_bridge` | Opening liquid cash plus operating/investing/financing movements and parent remittances |
| `funding_capacity`, `ownership_policy` | Explicit capacity for negative shareholder cash; only `pro_rata_existing_shareholders` currently supported |
| `terminal_roe`, `terminal_growth`, `terminal_ledger` | Continuing return/book/distribution and capital/liquidity sustainability |

`capital.*` uses the existing `CAPITAL_FIELDS`, `SUB_FIELDS` and `CASH_SIGNS`
contract in `distributable_equity.py`, without omitted or unknown fields.
`capital_inputs` derives the exact record mapping from the existing descriptor.
`capital.terminal_equity` belongs to `terminal_assumptions` on a valuation basis.
Parent ledger records identify the parent; subsidiary records identify the
corresponding legal entity; Ke/shares/discount intervals identify the group.

`capital_constraints` maps each subsidiary ID to exactly `basis=common_equity`
and `constraints`. Each constraint contains `id`, forecast paths `exposure`,
`ratio`, `buffer`, `absolute_floor`, plus scalar `terminal_requirement`.
The binding requirement is the maximum of the explicitly supplied exposure ×
ratio + buffer and absolute floors across applicable constraints. No regulatory
ratio or jurisdiction default is provided. Completeness of applicable legal
constraints is an analyst/source assertion, not independent legal certification.

`liquidity_bridge` maps each subsidiary to `opening_cash` and paths
`operating_cash`, `investing_cash`, `financing_cash`, `parent_fees_paid`,
`parent_tax_paid`. Transfers reconcile to the parent receipts. Distributions
consume cash and common capital once; required shareholder contributions are
included as negative cash flows and cannot exceed documented funding capacity.

`terminal_ledger` contains exactly `capital`, `capital_constraints` and
`liquidity_bridge`, describing the next full year using the same contracts.
Its balances start at forecast closing balances, discount interval is one year,
and the one-year checking ledger's terminal equity is explicitly zero. It is a
cash/capital proof, not an additional value in the headline valuation. Income is
closing common book × terminal ROE. Retained income must reconcile to book
growth; capital, cash, debt and the following capital requirement must sustain
the declared continuing growth. Negative continuing shareholder cash is blocked.

The existing bank engine computes residual income with the actual distributions
and discount intervals. It must equal the existing ledger's shareholder cash PV
plus continuing equity. Continuing equity is independently reconciled to closing
common book × `(ROE - g)/(Ke - g)` using the existing justified-book primitive.
The headline uses residual income; there is no median or weighted blend of
methods. The tangible-book bridge is disclosed and is not substituted for the
common-book base. Rates/terminal assumptions are explicit; unsupported fade,
capital instruments, ownership changes or accounting bases remain FV unavailable.
Continuing cash, debt, capital and requirements scale with the declared growth;
structural leverage, buffer or asset-mix changes must remain in the explicit
forecast instead of being hidden in the terminal period.

## Regulated networks: version 2

`regulated_rab` requires the resolver's regulator and RAB evidence, plus a dated
`regime` contract: regulator, jurisdiction, decision ID, review start/end, return
basis (`real` or `nominal`), tax basis (`pretax`) and return base
(`opening_recognized`). The regulator must match the acquired classification.
The evidence and explicit regime assumptions must cover the forecast and the
continuing checking year. Missing or expired data block the value. These fields
are sourced case assumptions, not a bundled regulatory rule or jurisdiction rate.

The supported return is on the opening recognized base. Nominal return cannot
also index the base. Real return may accompany explicit base indexation; this
does not convert the cash flow to real currency. Vanilla/post-tax returns,
average-base conventions and an additional tax allowance on a pre-tax return
require a separate tested reconciliation and currently block valuation.

The exact record schema is `rab_adapter.SCHEMA` and is included in the public
tool contract. Opening RAB, debt, cash, working capital and diluted shares are
explicit. The `claims` contract declares all interest-bearing claims in debt,
ordinary equity with no other senior/minority claims, full sweep after minimum
cash and pro-rata ownership. Incompatible claims require an acquisition/bridge.
Negative working-capital stocks and cash tax credits are outside this adapter's
supported accounting scope; they cannot create unbounded financing.

Each scenario supplies allowed return/indexation, recognized investment,
regulatory/book depreciation, asset disposals, cash investment, allowed and
actual expenses, incentives, tax, interest expense/income, working-capital
changes, debt movements, minimum cash, permitted distributions and funding
capacity. None is supplied by a geographic default or payout ratio. Disposal
cash must reconcile to the disposed recognized base and an explicit
`disposal_realization_multiple`. Recognized investment is deliberately distinct
from cash capex: recognition lags and eligibility must be documented in those
amounts, not hidden by copying one into the other.
`opening_unrecognized_investment` identifies already-paid investment awaiting
recognition. It rolls forward by cash capex minus recognized additions, cannot
become negative, and must sustain continuing growth. This prevents recognition
of unpaid replacement assets. Grants, disallowances, capitalized financing or
other nontiming differences require a separate bridge and are not supported by
this timing-only contract.

The existing RAB engine owns both the shared asset rollforward and the documented
cash ledger. Regulatory revenue contains allowed return, depreciation, expense
allowance and incentives. Income after cash tax reconciles to cash using book depreciation,
actual cash capex, working capital, disposal receipts and financing. Distributions
must fit available cash, permission and explicit funding capacity; closing RAB,
debt and working capital must remain valid. Interest income is explicit and is
never inferred by applying debt cost to net cash.
This is a regulatory cash-tax income bridge, not reported GAAP/IFRS earnings;
that distinction is also carried in the calculation output.

`continuing` supplies one full year of the same numeric drivers. Closing RAB,
debt, cash and working capital must support declared terminal growth and positive
shareholder cash. The existing DDM primitive discounts actual shareholder flows
and the continuing dividend value, using the explicit Ke/calendar/g. No second
net-debt deduction, EV/RAB premium, peer median or scenario weighting is applied.
Structural changes stay in the forecast. The ledger checks annual period ends;
it does not certify intrayear funding or authenticate legal permissions.

The distinction between regulatory return, expenditure and financing is
consistent with [Ofgem's explanation of regulatory financial performance](https://www.ofgem.gov.uk/data/riio-2-regulatory-performance-data-2024).
No numeric regulatory assumption is imported from that reference.

## Funds, investment holdings and digital-asset NAV: version 2

These methods use a dated snapshot: `calendar.periods=[]` and
`calendar.discount_convention=snapshot`. Every record's period is the valuation
date. There is no discount-rate requirement, invented forecast year, implicit
premium or private vehicle default. Missing targets leave fair value unavailable.
The model comparison uses the quotation at that same date. The separate observed
quote comparison follows the common rules above and never substitutes a child
asset's quote for its parent's quoted instrument.

The exact source-bound contracts are `nav_adapter.FUND` and `nav_adapter.DIGITAL`,
also exposed by the public tools. Every scenario supplies a positive `nav_target`
and `target_basis`. Fund/holding targets use `equity_nav`; digital treasuries may
explicitly use either `equity_nav` or `gross_assets_ev`. The latter applies the
target to gross assets before senior claims, and is not interchangeable with a
premium on common NAV. No automatic target, blend or geography-based premium is
used. The existing NAV engine owns the common asset-to-equity arithmetic.

Fund/holding inputs contain the issuer's `publication` (publisher, publication
date, valuation date, common-equity-net basis and share class), the
`reported_nav_per_share`, `reported_nav_precision`, shares, components and policy.
Publication, reported NAV and its precision must be historical observations;
an analyst estimate or guidance cannot be labelled an actual official NAV.
This verifies the documentary contract, not the authenticity of a submitted URL.
Components contain gross assets, cash, debt, preferred claims, other liabilities,
accrued fees, distributions payable, tax and signed equity adjustments, all in
millions of the reporting currency. The common NAV must reconcile to the
published amount at its stated half-up precision. Rounding for publication does
not reduce calculation precision before FX, quotation units or share ratios.
The supported policy explicitly includes all claims/fees/payables in those
components, with basic common shares and no unmodelled dilution. An investment
holding without an attributable published NAV remains an acquisition case; SOTP
is the separate route for genuinely mixed businesses.

Digital inputs require matched sets of asset holdings and prices. Each holding
identifies quantity in millions of asset units, custody and ownership fraction;
each price identifies its currency, financial-currency FX and valuation date.
Other operating assets are explicitly valued in the components, with no default
zero. Gross assets reconcile these holdings and operating assets. Shared cash,
debt, preferred and other claims bridge to common equity once.

`capitalization` contains `basic_shares`, explicit `conversions` and `warrants`,
`basis=basic|if_converted`, `debt_basis=par_before_conversion` and
`cash_basis=before_exercise`. Basic mode supports no unmodelled dilutive claims.
Each converted claim has a unique ID, shares and nominal face value; each warrant
has an ID, shares and strike. They also require
`exercise=voluntary_in_the_money`, `exercisable_from` and `expires_on`. Shares
are millions; face value is millions of the financial currency, and strike is
financial currency per underlying share. Claims must be immediately exercisable
and economically in the money against the correctly converted underlying-share
quotation. Otherwise this fully-converted headline is unavailable. Mandatory,
future, contingent or out-of-the-money claims require a separate tested model.

Converted nominal debt is removed once; warrant exercise adds cash once; both
add the explicitly reconciled diluted shares. This is an immediately available
if-converted valuation convention, not evidence that holders have exercised.
Debt measured on a different basis requires an explicit reconciliation before
using this adapter. Negative asset quantities and unmodelled short/derivative
positions are outside the supported contract.

Fresh-process tests measure no personal-registry reads while importing the
common engine and generating a NAV workbook. Legacy registry aliases remain
available to explicit historical callers and are acquired lazily. All snapshots
still use the common tool/cache/research/persistence/score/committee/F17 chain.

## Property/casualty insurance

`insurance_pc_distributable_equity` uses the shared legal-capital ledger;
it does not call the bank valuation formula. The common insurance record schema
is published in `method_records_schema()`. Entity records are named
`insurance.<legal index>.<driver>` and belong to that legal entity. Each entity
supplies its opening balance and product policy, plus every annual driver in
`PC_PATHS`; every scenario also supplies consolidated income, terminal income
and one continuing year of economics, capital constraints and cash movements.
The calculated entity income and every cash category must match the ledger.
No reported combined ratio, embedded value or distributable-income label can
replace this reconciliation.

The implemented product is short-tail, undiscounted GAAP, wholly owned ordinary
equity, with a constant single quota-share treaty. The product record names the
counterparty (`none` only when no reinsurance exists), identifies coverage of
opening and future claims, and separates opening recoverables on already-paid
claims. Opening ceded unearned premiums and recoverables reconcile to the treaty.
Written, earned and collected premiums separately change receivables and
unearned premiums. Incurred claims include current loss experience, catastrophe
costs and prior development; payments change reserves. Prior releases cannot
exceed opening reserves. Continuing value does not capitalize recurring prior
reserve releases without a separately modelled reserve-cohort bridge.

Ceded premiums, claim recoveries, payments and impairment are separately consumed.
Investment yield applies to opening amortized-cost assets for complete annual
periods; purchases, carrying cost sold, proceeds and impairment change the asset
balance once. Tax cash equals tax expense with no deferred taxes. Other liabilities
are operating payables: increases cannot exceed accrued operating expenses and
closing payables cannot be negative. No subsidiary borrowing, OCI, commissions,
participating claims or excess-of-loss treaty is inferred. Such cases require
additional supported inputs and remain unavailable under this contract.

The common-capital requirement is acquired for the stated jurisdiction and
regime, alongside every applicable constraint and management buffer. There is
no universal insurance capital ratio. The [NAIC RBC overview](https://content.naic.org/insurance-topics/risk-based-capital)
explains why insurance risk and capital frameworks differ by business.
Continuing equity is priced only from reconciled shareholder cash after capital,
liquidity, parent costs and debt; common book or investment assets are not added
again. Unsupported or missing inputs produce named acquisitions and unavailable FV.

## Life insurance

`insurance_life_distributable_equity` uses the same capital, cash, quotation and
consumer contract as PC, with a separate life projection. It supports a homogeneous
pool of annually renewable term protection on a documented GAAP basis, with renewal
at the stated premiums and death cover. Participation, investment guarantees,
maturity and surrender benefits, reinsurance, DAC, CSM, UPR, deferred taxes and OCI
are outside this product contract. Lapse is an exit from coverage, without a cash
surrender payment. A participating or savings insurer cannot use this model merely
by supplying an insurance classification.

`LIFE_SCHEMA` is published through the same method-record tool schema. Policy counts
are in `million policies`; premiums, benefits and per-policy costs use the reporting
currency `per policy`. New policies enter at the beginning of each complete annual
period. Premiums and administrative costs apply to opening plus new policies;
acquisition costs apply only to new policies. Deaths occur before end-of-period
lapses. Their separately documented probabilities determine the closing policy
population. There are no demographic priors or automatic production replacements.

The opening balance separates investments, cash, future-coverage `policy_reserve`,
already-incurred `claims_payable`, operating payables and in-force policies. Income
deducts incurred death benefits and the change in the externally acquired GAAP
policy reserve. Cash deducts claims actually paid; the reserve change is not deducted
again. Investment movements use the same pure helper as PC. Closing cash, GAAP
equity, statutory capital, constraints and distributions must reconcile by entity.

Each scenario's `reserve_report` names the actuarial projection and its reserve
basis. It matches opening policies/reserves, forecast and continuing production,
mortality, lapse, premiums, death cover, policy costs and closing reserves to the
actual consumed case. The engine reconciles that acquired reserve projection; it
does not independently calculate or certify actuarial reserves. No future-coverage
reserve is permitted without in-force policies, while incurred claims can remain
payable. Accounting profit metrics are not substitute cash flows; for example,
[IFRS 17 key terms](https://www.ifrs.org/supporting-implementation/supporting-materials-by-ifrs-standards/ifrs-17/key-terms/)
distinguish CSM from recognized earnings.

Continuing value requires the policy population, reserves, assets, cash and capital
to sustain the declared growth. The continuing regime keeps probabilities and
per-policy prices/costs constant and scales new policy quantities and aggregate
amounts with population. A second projected continuing year verifies this relation.
Higher required reserves retain cash before it can reach shareholders; releases
affect the correct period. Annual ledgers do not certify intra-year capital available
at new-business inception. A business requiring that proof remains subject to an
additional liquidity/capital review. Company run-off, finite guarantees and other
products require a distinct supported projection, not an assumed zero terminal value.

## Stabilized property owners and equity REITs

`property_nav` values wholly owned freehold properties at the opening NAV date.
It separately reconciles one forward year of income and cash; that cash is not
added to the opening NAV. Calendar uses the existing `snapshot` convention with
no discount periods or invented Ke. The source-bound `forward_year` record supplies
`start` and `end` for the full following year. Opening structures use the NAV date;
scenario operating records use that explicit forward-year period.

| Record | Required contents |
|---|---|
| `property_scope` | Property IDs, freehold tenure, ownership fraction, `million square metres` area unit |
| `property_income` | Per property: `area_m`, `annual_rent_per_area`, occupancy, other income, recoveries, cash operating costs, cash lease incentives, straight-line rent, property depreciation |
| `property_capex` | Per property: maintenance, tenant improvements, leasing costs, expansion and backlog |
| `property_values` | Per property: cap rate, stabilized NOI, NOI/capex convention and comparable basis |
| `ffo_bridge` | Net income, FFO, AFFO, central cash cost, cash tax, asset-sale gains and property impairments |
| `components` | Opening cash, debt settlement value, preferred, other liabilities, accrued fees, distributions payable, tax and equity adjustments |
| `debt_schedule` | Named loans with face, settlement value, annual coupon, maturity and contractual basis |
| `funding` | Minimum cash, shareholder distribution, dated committed refinancing and pro-rata shareholder contribution |
| Valuation assumptions | Explicit central-cost multiple, NAV target and `equity_nav` target basis |

The full field/unit/scope mapping is exposed through `method_records_schema()`;
the supported policy strings are in `real_estate_adapter.POLICY`. Scenario income
and valuation assumptions cannot be labelled historical observations.

Cash rent is area times annual rent per area times occupancy. Other income and
recoveries are added; property operating costs and cash incentives are deducted.
The resulting cash NOI must equal stabilized NOI for the capitalization case.
The selected cap rate must use that same cash NOI before recurring capex, with
comparable capex/tenure treatment and an explicitly documented case assumption.
NOI must fund recurring maintenance, tenant improvements and leasing costs.
Unstabilized assets or structurally negative recurring cash require another
projection; the model does not create a positive value by capitalizing gross rent.

Asset value is stabilized NOI divided by its sourced cap rate, less backlog not
already reflected in the comparable valuation. Backlog is assumed cured before
forward income without a letting delay, and must fit within opening cash after
the minimum cash reserve. A later loan or later rent cannot finance that initial
cure. Delayed works, lease-up and developments need a dated project projection.
Expansion, acquisitions, disposals and property impairments are outside this
stable-portfolio contract, rather than treated as zero without a source.

The supported GAAP bridge adds property depreciation back to net income for FFO.
AFFO then removes straight-line rent and recurring capex; it is reconciled to the
submitted issuer/case definition, not presented as a universal reporting standard.
[Nareit FFO](https://www.reit.com/glossary/funds-operation-ffo) and
[Nareit AFFO](https://www.reit.com/glossary/adjusted-funds-operations-affo) provide
the reporting context. Other adjustments require an explicitly supported bridge.
Central costs exclude property opex, interest, tax and capex. Recurring corporate
costs and current corporate cash taxes are capitalized together using the explicit
central-cost multiple, with no default geography factor. Current costs and taxes
equal expense, exclude settlement of opening payables and include no new accruals
or deferred tax. Property taxes belong to property opex; opening tax payable is a
separate claim. These distinctions prevent both omitted corporate tax and duplicate
payments of the opening liability.

The existing NAV engine deducts the capitalized central cost and opening claims
once and applies the explicit equity NAV target. Loans use contractual settlement
value at or above face; a depressed own-credit market value cannot create equity
NAV by cancelling a payable principal amount. The loan schedule must reconcile
to the NAV debt component. The implemented loan contract is fixed-rate bullet,
full-year coupon, with maturity at forward-year end or later. Earlier maturities
require an event calendar, coupon accruals and funds available at the due date.

Year-end maturities, backlog, other opening payables and shareholder distributions
are paid in the separate cash bridge. A refinancing must identify its commitment,
amount, exact availability date, subsequent maturity and fee-free draw terms, and
match a loan due that day. Equity funding is restricted to documented pro-rata
contributions by existing holders; new-share issuance requires a dilution model.
There are explicitly no unmodelled covenants, guarantees or restricted cash in
this contract. Such restrictions, borrowing pools, leasehold claims or other
capital structures require further inputs and remain unavailable. This is an
annual affordability check, not proof of intra-year or lifetime solvency.

Mortgage REITs remain in the credit family when the evidence identifies lending.
A fund classification retains its vehicle method; owning property does not by
itself change an ETF/CEF into a property operating company. Property developers
use their distinct finite-project route.

## Prior-free scope and comparable evidence

The documented routes consume only the published driver contracts. Calibration
tables, peer seeds, default beta/payout/growth, geography bands and automatic
method medians are not consumed. Historical engines/files remain preserved.
Passing legacy `peers`, weights or unsupported accounting inputs to a documented
route produces an explicit acquisition and unavailable fair value; those inputs
cannot silently become a parallel calculation. A separate comparable analysis
must establish economics, geography, accounting basis and source dates upstream;
this release does not certify the old automatic peer selector as documented
evidence or use it to override the calculated fair value.


## Finite property development

`property_development` consumes individually identified, wholly owned freehold
projects with unconditional permits. `units_total` means the **remaining lots
still to be delivered at the opening date**, with opening tax inventory and
receivables for that same unsold perimeter. The forecast supplies the remaining
construction budget, annual construction payments, completion period, lots
and prices delivered, operating costs, receivables and allocated capitalized
interest. All lots must be delivered and all inventory/receivables cleared
within the sourced horizon. No terminal resale or perpetuity is inferred.

The supported tax perimeter is one taxpayer in one jurisdiction with full
current project profit/loss offset, no NOL refund, deduction limits or deferred
tax. Each project identifies that taxpayer and jurisdiction. Opening unlevered
and financed tax bases are separate; future capitalized interest cannot exceed
actual debt coupons and is recovered through inventory cost of sales. Other
SPVs, prepayments, conditional permits and tax regimes require acquisitions.

Costs occur at each period's start and deliveries/collections at its end.
Existing fixed coupon bullet debt must mature at a documented period end within
the finite horizon, with settlement value at least par. No new borrowing,
covenants, guarantees or certain financing fees are supported. Opening cash,
operating buffers and irrevocable existing-shareholder pro-rata funding
commitments are explicit. Draws occur only when needed; unused capacity does
not create cash or shares. Excess cash is distributed at the correct boundary
and the final buffer is released. This tests the supplied timing case, not
continuous intrayear liquidity or outside financing availability.

Unlevered FCFF uses the existing DCF primitive with an explicit zero terminal.
APV adds acquired expected usable tax benefits at their documented discount
rate, then deducts acquired after-tax expected financing deadweight costs.
`ku`, the expected shield and its rate, and `financing_cost_pv` must be sourced;
zero requires an explicit record. Financing costs exclude debt service or
costs already in project cash flows or Ku. The engine neither estimates default
probability nor assumes the full theoretical tax shield will be received.
The separate financed ledger reconciles cash taxes, construction funding,
debt service and distributions. Unsupported equity dilution returns FV n.d.

Method references: [Damodaran, APV](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/apv.htm)
for separately valued unlevered operations, tax benefits and financing costs;
[IAS 23](https://www.ifrs.org/issued-standards/list-of-standards/ias-23-borrowing-costs/)
for why qualifying-asset borrowing costs require explicit capitalization input.


## Producing resource assets

`resources_asset_dcf` integrates finite producing assets with explicit
recoverable reserves, reserve classification/standard/report, rights expiry,
annual saleable output and sourced year-over-year volume declines. Reserves
and output are gross before the cash royalty and must share the price unit:
million barrels with currency per barrel, or million tonnes with currency per
tonne. No BOE-to-oil-price proxy, instantaneous-flow decline interpretation,
reserve growth or reserve additions are inferred. This scope uses sustaining
capex for existing production; exploration and development need other inputs.

Each scenario supplies price, variable/fixed cash cost, cash royalty as a
fraction of gross realized revenue, sustaining capex and tax allowances.
Output is sold and collected in the same period; there are no inventories,
receivables, processing losses, PSC, JV, hedges or other operating claims.
Positive production requires nonnegative operating margin; an uneconomic
scenario requires a revised economic-limit/abandonment plan. The engine does
not certify a reserve report or optimize the extraction schedule.

Cumulative production plus explicitly abandoned remaining reserves must equal
the opening recoverable inventory. Production after closure or rights expiry
blocks FV. Each asset has a documented closure period, **nominal future cash
restoration payment**, full obligation perimeter and current tax deductibility.
The payment is not a discounted ARO balance; no additional ARO claim is deducted
from equity. Tax basis and capital allowances exclude that future restoration
deduction. Capital allowances reconcile to opening tax basis plus capex and
must exhaust the supported tax basis by the finite end. Single taxpayer and
jurisdiction, current cash tax with no NOL refunds or deferred tax, are required.

The shared finite APV/funding ledger applies. Costs/capex occur at period start;
sales less royalty and restoration, cash taxes and debt service at period end.
Only unrestricted cash is available; escrow, restoration funds, guarantees,
new borrowing and outside equity dilution are unsupported acquisitions.
A sourced `debt_schedule` of `{"no_debt": true}` explicitly attests no debt;
an empty contract does not. No salvage or terminal perpetuity is assumed:
residual reserves are explicitly abandoned without proceeds/future recovery.

Method references: [SEC Oil and Gas Rules](https://www.sec.gov/rules-regulations/staff-guidance/corporation-finance-interpretations/oil-gas-rules)
for distinct reserve categories, economic producibility and decline evidence;
[IFRIC 1](https://www.ifrs.org/issued-standards/list-of-standards/ifric-1-changes-in-existing-decommissioning-restoration-and-similar-liabilities/)
for the distinction between restoration obligations, related assets and cash.


## Conditional licensed development (rNPV)

`development_rnpv` covers a single wholly owned precommercial licensor with
explicit, ordered development stages. Each scenario has a stage period,
start-of-period development cost, end-of-period success milestones received
and paid, failure wind-down payment and probability **conditional on entering
that stage**. Failure is absorbing. Success enters the next stage under the
source plan; the engine does not optimize abandonment or invent a new trial.

Complete failure/success histories are valued separately. Costs are incurred
when a stage is reached, not multiplied by the probability of final approval.
Tax is calculated on each history before aggregation, with no averaging of
taxable profit between mutually exclusive outcomes. Failure releases the cash
buffer and winds down at the failed stage's end. A mathematically impossible
branch creates no cash flow or financing requirement; malformed source inputs
still fail validation. Positive probability underflow is reported as KO.

After final success, licensed units times the licensee's net price (after
rebates) define licensee net sales. Incoming royalty rates apply to those sales;
outgoing royalty rates apply to incoming royalty receipts. The licensee's
product sales are not the licensor's own revenue. Commercial amounts are
conditional on success, without an additional technical probability haircut.
The acquired discount rate and risk contract exclude the modeled transition
risk; other market/commercial risk remains a sourced discount-rate judgment.

Patent, royalty-contract and economic expiry must coincide at the supplied
finite horizon. The contract explicitly excludes residual receipts/assets;
patent expiry alone does not establish that royalties stop. Earlier expiry,
delayed stages, royalty terms and final wind-down costs require updated source
records. The supported taxpayer has current deductibility of acquired R&D,
milestone, royalty and other paid costs, no NOL benefit/refund, deferred tax or
opening tax basis. Accounting expense treatment alone does not prove that tax
rule. Manufacturing, capex, inventory, receivables and debt require a different
case; there is no automatic generic DCF for them.

Funding must cover every reachable history, with terms known at period start
and irrevocable capacity available for all reachable outcomes in that period.
The same source commitment applies across histories, preventing financing terms
that anticipate a later outcome. Two modes are supported: pro-rata calls on
then-existing ordinary shareholders, or outside ordinary shares at a sourced
positive fixed `issue_price` in financial currency per share. Cash capacity is
in currency millions; new shares in millions equal actual draw divided by that
price. No fees, preferences, options, warrants or implicit future issue price
are supported. Price and capacity are contractual inputs, not engine forecasts.

The shared ledger updates shares at each start/end draw. Original shareholders
pay their share of pro-rata calls and receive their ownership fraction of each
distribution; outside investors fund their own new shares. Previously paid cash
is not diluted retroactively, and financing proceeds are not added again to FV.
An acquired issue price can be accretive or dilutive; the engine imposes no
universal haircut. Shared `present_value` discounts original-holder cash flows,
then the tree aggregates raw values using conditional outcome probabilities.

Method references: [Damodaran, probabilistic valuation](https://pages.stern.nyu.edu/~adamodar/pdfiles/val3ed/c33.pdf)
for complete histories and avoiding duplicate risk adjustments;
[IAS 38](https://www.ifrs.org/issued-standards/list-of-standards/ias-38-intangible-assets/)
for why research/development accounting requires a separate, explicit policy.

## Mixed businesses: documented equity SOTP

`mixed_business_sotp` calls the registered, documented adapter for each actual
segment. It uses each segment's raw, unrounded common-equity value in its
financial currency. Child workbooks, snapshots and generation IDs are retained;
the parent sidecar includes independently checked child results. A missing or
blocked material segment blocks the entire parent FV. The legacy optional
spreadsheet comparison remains a separate calculator.

Scope is a standalone holding company with ordinary, fully paid participations
in independent legal perimeters. All reported businesses and participations must
appear in both the profile evidence and the sourced segment manifest. Nested
SOTP, cross-holdings, guarantees, intercompany loans/trading and contractual
parent support are outside this adapter. Internal subsidiary/parent movements
within an individual bank or insurer remain inside that child's own ledger.

Common perimeter/calendar/quotation/shares records are joined by these records:

| Driver | Scope | Consumed contract |
|---|---|---|
| `policy` | model, forecast | Exact sourced scope, capital-call and central-cost policies in `sotp_adapter.POLICY` |
| `segments` | model, opening | Segment ID, entity, complete legal-entity list, profile ID, ordinary share class and `held_common_shares_m` |
| `children` | model, opening | Full immutable acquired bundles, including each child's documented driver records |
| `parent_balance` | model, opening | Standalone parent identity, unrestricted cash, debt, preferred, other liabilities, investments at book, other assets, common equity, basis and source |
| `segment_fx` | each scenario, opening | Each child's financial currency, explicit conversion to parent currency and matching valuation date |
| `claims` | each scenario, opening | Instrument inventory, allocation, legal entity, currency, amount, role, measurement basis, exact child record reference and source |
| `central_cash_cost` | each scenario, forecast | Newly incurred parent after-tax expense paid currently, excluding all opening claim settlements, interest and child costs; no new accruals or deferrals |
| `central_discount_rate`, `central_growth` | each scenario, forecast | Explicit discount rate and growth of the final normalized annual cost |

Standalone assets less liabilities must reconcile to common equity. Investment
book values establish the perimeter and balance reconciliation; the SOTP replaces
them with owned segment values. Other parent assets require acquisition of a
supported component instead of an unexplained adjustment.

Each inventory `claim_id` identifies an instrument, not its creditor; IDs are
unique across allocations. `record_ref` names `driver`, `scenario` and a path
inside the actual consumed record. Child claims reconcile to their existing
model bridge, including explicit zero categories. For an operating child,
separate gross debt and cash must reconcile to net debt. For banks/insurers the
reference concerns the subsidiary holding parent's opening debt/cash: deposits
and other liabilities already embedded in common book equity are not an EV
bridge. This is an inventory of consumed bridge inputs, not certification of
all consolidated liabilities.

Only the standalone parent's cash and claims enter the final bridge. Parent
debt settlement cannot be below its book amount. Central cost PV reuses the
existing DCF primitive with complete annual periods and sourced terminal growth;
opening accrued expenses already deducted as claims cannot enter that cost path.
No synergy premium, holding discount or geographical premium is supplied.

Ownership uses held ordinary shares divided by the child's consumed valuation
denominator. A digital treasury's denominator can include dilution, but held
ordinary shares cannot exceed the basic shares already issued. Owning warrants
or convertible claims requires a separate supported contract. rNPV uses value
attributable to original holders, after its event-specific financing dilution.
The child's quoted currency, penny scale or ADR ratio does not change the
ordinary-equity component. Parent FX is applied exactly once.

Positive modeled shareholder funding blocks aggregation until an aggregate
parent funding ledger is acquired. The gate checks child capital/cash ledgers,
including irrevocable existing-holder commitments even when still undrawn,
and rNPV checks original-holder calls on every reachable branch. Operating FCFF
without a financing ledger is accepted only with nonnegative FCFF and no
positive net debt; this limited screen and the sourced no-support policy do not
certify indefinite parent or child solvency. No partial sum becomes a usable FV.
Each child also needs the quotation required by its own common contract;
unquoted divisions do not receive automatic SOTP coverage.

Reference: [Damodaran, valuing cross-holdings](https://pages.stern.nyu.edu/adamodar/New_Home_Page/valquestions/valcrosshold.htm).

Documented Excel snapshots preserve every supplied period and flatten nested
contracts into source paths. Text exceeding the Excel cell limit continues on
explicitly numbered rows; source text is stored as text, never as a formula.

## Coverage and reproducible acceptance

Integrated means that an actual adapter reaches the common consumers. It does
not certify an issuer's data, country coverage, accounting basis or economic
forecast. These are the supported contracts; their restrictions above remain
binding for every classification in the table.

| Economic profile | Method / principal source requirements | Case acceptance |
|---|---|---|
| Payments networks/processors, fee managers/brokers, software/hardware, semiconductors including fabless/foundry/equipment, manufacturing/services, telecom/media, consumer discretionary/staples, mature pharma/medtech/hospitals, energy services and merchant/contracted generation | Operating FCFF; sourced revenue build, cost/cash-tax/reinvestment policies, capitalized development and explicit terminal/risk assumptions | Complete annual operating contract; incompatible capital, revenue or accounting structures require acquisition |
| Banks, balance-sheet lenders, mortgage lenders | Residual income with common-book/capital/cash reconciliation; parent/subsidiary legal constraints | Documented funding and distributable equity; a sector label supplies no capital ratio |
| Managed care | Premium/medical-cost/expense model plus legal-entity capital and parent cash ledger | Existing [managed-care input contract](../managed-care-inputs.md); incomplete legal capital means FV unavailable |
| Property/casualty insurers | Short-tail GAAP insurance cash/book model and distributable equity | Constant quota-share scope; unsupported reinsurance or reserve basis is not inferred |
| Life insurers | Annual renewable-term GAAP policy/reserve model and distributable equity | Source actuarial reserves required; savings, participation, CSM/embedded-value products need another documented contract |
| Regulated networks | Recognized RAB, acquired regulatory returns and actual capital/cash path | Fixed sourced regime and explicit continuing-period reconciliation |
| Closed-end funds and investment holdings | Published common-equity NAV and explicit target | Reconciled dated NAV; operating or mixed holdings cannot obtain a default holding premium |
| Digital treasuries | Assets/custody/prices, claims and explicit basic/if-converted capital | Dated positions and economically available conversion/exercise terms |
| Property owners and equity REITs | Property NOI/cap-rate NAV with cash/debt and FFO/AFFO reconciliation | Stabilized wholly owned freehold assets; mortgage REITs use lending economics |
| Property developers | Finite project FCFF/APV, inventory/tax and funding ledger | Permitted remaining units, complete costs/deliveries and one taxpayer/jurisdiction |
| Producing resources | Finite asset FCFF/APV, recoverable reserves, prices/costs and closure | Producing wholly owned assets; new development, JV/PSC and hedges need further contracts |
| Precommercial development/licensing | Conditional rNPV and event-specific original-holder funding cash flows | Finite single-licensor scope, acquired stages/probabilities/rights/tax/funding |
| Mixed groups | Separately checked child equity valuations and standalone parent bridge | Complete independent participations; no omitted segment or implicit parent financing |
| ETFs, ETNs, commodities and cryptocurrencies | Existing exposure analysis | Exposure/risk output; no corporate DCF fair value invented |

The reusable suite `tests/test_sector_public_contract.py` checks each documented
family with external symbols, renaming, different holdings/mandates, changed
business evidence, stale data, legacy caches, changed methods, unread inputs and
nested blocked outputs. `test_documented_consumers.py` and the existing managed
care integration suite exercise real workbooks/sidecars, tool/build/cache,
RESEARCH, temporary SQLite persistence, score, committee output, the F17 endpoint
and React rendering. Economic oracles and domain boundaries remain in the
family-specific tests. All numerical fixtures are synthetic.

Blocked cases also hide derived NAV, terminal equity and present-value components
inside calculation details. Original acquired records, observed NAV and book/cash
ledgers remain available as evidence; they are not a usable valuation.

Country or listing does not choose an economic premium or a fallback model.
Financial statements, regulatory returns, NAV reports, asset rights, actuarial
reserves and forecast assumptions must actually be acquired for the particular
entity, period and basis. Existing providers expose raw profile/financial/
filing/guidance/consensus data; they do not automatically manufacture the complete
`method_inputs` forecast contract. A successful live profile lookup alone cannot
produce a usable FV. Unsupported or unavailable sources retain precise
acquisition tasks rather than apparent coverage.

Public-clone checks must resolve Python modules from that clone's `src`, use an
empty data directory and install test dependencies from the committed lockfile.
The Python suite also runs React contract tests and therefore needs Node plus
the app's development dependencies. These checks need no personal database,
private model registry, keys or live model calls. Live source checks are separate
from the repeatable synthetic suite.

Existing databases require the separate additive metadata procedure
`tools/migrations/migra_valuation_metadata.py`. Its default dry-run preserves
the source, rehearses the DDL on a temporary copy and reports measured statement
counts and all-table/schema fingerprints. `--apply` requires a quiet runtime,
creates a coherent exclusive backup, repeats the rehearsal, checks for source
drift under a write lock, and validates schema, references and unchanged existing
tables before commit. It does not backfill historical valuations or activate the
application. Preserve the receipt and all previous generations.
