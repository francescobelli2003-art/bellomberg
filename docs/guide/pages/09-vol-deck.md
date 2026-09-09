# F9 — Vol Deck

[Handbook](../README.md) · [Previous: F8](./08-monte-carlo.md) · [Next: F10](./10-edge-scanner.md)

![F9 Vol Deck: original synthetic DEMO mockup](../../assets/product/09-vol-deck.svg)

*DEMO illustration: invented instruments, values and text; not a screenshot.*

Use three connected views: **Surface**, **Desk** and **Chain–Strategie**.
The workflow makes catalogue coverage, observed contracts and theoretical
strategy values visible as separate things.

## Load the expirations you need
1. Enter the exact underlying ticker and press **CATALOGO**. No portfolio ticker
   or full chain is fetched merely by opening the page.
2. The catalogue loads progressively until the provider confirms its end.
   **Interrompi catalogo** pauses discovery; **Riprendi catalogo** continues it.
3. Choose **Scadenza finale**, or use the month, quarter, half-year and year
   shortcuts. Every available date up to that boundary is included. **Intera
   chain** selects the entire catalogue, including distant expirations and 0DTE.
   The expandable date list is for inspection; individual checkboxes are unnecessary.
4. Press **Carica chain e superficie**. Progress shows received pages, contracts
   and completed dates. There is no total expiration or contract cap. **Interrompi
   download** preserves the response already in flight; **Riprendi download**
   resumes at the saved cursor, including after a provider rate-limit error.
5. Read coverage for every requested expiry: loaded, partial, excluded or error,
   together with its reason.

This workflow applies to every underlying supported by your Polygon account.
An ETF ticker and an index ticker identify different contracts; the app does not
replace one with the other. Access failures remain explicit.

Download completeness and surface eligibility are separate. You can inspect
received contracts and calculate a partial surface while acquisition is paused.
Snapshots are retained in backend memory for one hour of inactivity and are lost
on backend restart. They are marked **STALE** after two minutes from the oldest
received page; complete does not mean real-time. Starting a new download refreshes
expired data. An unfinished ticker is paused when you move to another ticker;
returning to it in the same page session offers the preserved job.

A date in the catalogue is not proof of usable quotes at every strike. Zero- and
one-day dates stay visible even when excluded from the surface model.
The mesh leaves missing points open instead of drawing an invented bridge across
them. A sparse surface can be an honest account of sparse data.

## Read Surface and Desk
Rotate the surface and inspect the selected expirations, smile and term
structure. Check axes and units before comparing strikes or maturities.
Use **Carica contesto RV, IV rank, GEX, cone** when you also want that context;
those are additional data requests. Coverage can differ by panel.

## Inspect the chain and Greeks
1. Open **Chain–Strategie** and select an expiry from the catalogue.
2. Press **Carica chain**. Already downloaded contracts are read from memory;
   an expiry not yet acquired starts its own resumable download. Use **Contratti
   precedenti** and **Contratti successivi** to page through the stored rows.
   Table pagination does not request more provider data.
3. Filter calls, puts or strikes. Read bid, ask, implied volatility, delta,
   gamma, vega, theta and open interest. Filters search the entire downloaded
   expiry, including rows outside the currently visible page.
4. Select a contract to inspect its quality, source, quote timestamp, timeframe,
   multiplier and additional fields such as rho. **n.d.** is missing data.
5. Use **Buy** or **Sell** to add a leg to the simulator. These buttons do not
   place an order or change portfolio holdings.

A retrieved contract may have a delayed, stale, crossed or incomplete market.
Its source timestamp is distinct from the time at which you calculated a
theoretical value. Check its multiplier; never assume every contract uses the
same one.

## Build a strategy
You can create up to twelve legs manually or from the chain. For each leg,
supply direction, call/put, strike, days to expiry, quantity, multiplier,
premium **per underlying unit** and IV. Quantities are whole contracts.

Vertical, straddle and calendar templates start from your first leg. Unknown
premiums or volatility assumptions remain for you to enter; a template does not
manufacture an observed quote.

Set current spot, scenario spot, elapsed days, IV shock, rate, continuous
dividend yield, initial fees and currency. Missing required inputs block the
calculation. Use the field's stated units, especially annual rates, IV and days.

## Read the laboratory
- **Gold:** payoff at expiration, available when all legs share an expiry.
- **Cyan:** the theoretical scenario after your time and IV changes.
- **Dashed:** today's theoretical curve.
- **Price × time heatmap:** the same model across alternative scenarios.

Hover or use the chart's arrow controls to inspect values. Compare debit/credit,
break-even levels, maximum gain/loss or unbounded sides, and aggregated Greeks.
For different expirations there is no single common-expiry payoff; calendar
scenarios stop at or before the first expiry.

## Model boundaries
The simulator uses European Black–Scholes–Merton, ACT/365 time, continuous
dividend yield and constant per-leg IV plus the chosen shock. Intermediate
values are theoretical. It does not model American early exercise, discrete
dividends or intraday expiration mechanics.

Initial fees are included; slippage, exit costs, financing and tax are not.
A mathematically unbounded loss is not a calculated margin requirement.
Provider availability and quote quality remain separate from simulator
correctness. Record assumptions before treating a visual result as research.
The simulator runs locally and does not itself require an AI model call.
