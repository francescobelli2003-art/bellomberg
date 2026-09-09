# Read the numbers and their limits

[Handbook](README.md)

## A missing measurement is not a zero

| Label | Read it as | Your next step |
| --- | --- | --- |
| `n.d.`, unavailable or missing | No usable measurement | Inspect the source or named requirement |
| `STALE` | A previously observed value is too old for the workflow | Check timestamp and refresh/source status |
| Partial coverage | Only part of the requested universe was measured | Inspect exclusions before comparing totals |
| Proxy | A disclosed substitute or modelling approximation | Check whether it is suitable for this question |
| Model / scenario | A calculation under specified assumptions | Change the assumptions and compare |
| Empty list | No returned rows | Check filters and provider availability; some legacy feeds do not fully distinguish these cases |
| Error / KO | A failed operation or validation | Read the reason; do not infer missing portfolio data was deleted |

The intention is to expose absent data, not manufacture convenient defaults.
That is not a guarantee that every legacy endpoint handles every failure equally.
Offline tests and the release checks have specific scopes.

## Performance, money and units

NAV is portfolio value including cash on the reported basis. A return measured
against cost is different from time-weighted return (TWR): deposits and withdrawals
must not be confused with investment gains or losses. Compare series with matching
dates, currency and return definitions. Attribution can use a different basis
from headline TWR; read the reconciliation instead of forcing the two to match.

Quote currency belongs to the listing. Pence and pounds are different units.
Never substitute another listing or an ADR just to make a feed return a price.
Missing FX makes dependent converted values unavailable or partial.

Read whether percentages are percentage points, returns or exposures. For
options, also check premium per underlying unit versus total contract premium,
contract multiplier and Greek units.

## Valuation and simulation

A fair value is the output of a model and its inputs; it is not an observed
market price. A sanity badge concerns implemented checks, not correctness of the
investment thesis. Bank, operating-company, regulated-asset and asset-holding
models have different assumptions.

Monte Carlo distributions are conditional on the estimated process and chosen
scenario. Tail events outside the model remain possible. Option strategy
scenarios use a specified European pricing model; quote freshness, execution
costs and early-exercise behavior require separate judgment.

## Agent scores and learning

A directional hit rate measures defined eligible outcomes. Always inspect the
sample count, horizon, excluded candidates and confidence interval. A result
can change when market prices evolve or previously unmeasurable calls mature.

Agent Progress draws comparable changes only when the stored cohort and method
permit them. Broken lines or “Non confrontabile” prevent a misleading continuous
improvement curve. The Capo's score represents committee decisions collectively;
a workflow role without independent attribution is not assigned an invented score.

A reflection is a saved suggestion for future work. The page explicitly separates
a documented lesson from its implementation and from proof of improved
performance. It does not fine-tune models or establish causal learning.

## Source tags and AI text

Numeric research claims are expected to carry `[src: tool]` tags. Follow the source,
date and assumptions. Model-generated text can still misunderstand a correct tool
result or overstate evidence. Distinguish facts, hypotheses, forecasts, your own
preferences and historical commentary. Keeping those distinctions visible is more
meaningful than claiming the system has no possible bias.
