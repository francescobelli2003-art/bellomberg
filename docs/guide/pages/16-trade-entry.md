# F16 — Trade Entry

[Handbook](../README.md) · [Previous: F15](./15-decisions.md) · [Next: F17](./17-movements.md)

![F16 Trade Entry: original synthetic DEMO mockup](../../assets/product/16-trade-entry.svg)

*DEMO illustration: invented instruments, values and text; not a screenshot.*

Record transactions you have actually executed and maintain the cash ledger.
Bellomberg is not sending an order to a broker from this page.

## Record a trade
1. Select the exact ticker and the appropriate action: BUY, ADD, TRIM, SELL or
   DIVIDEND.
2. Enter the date, quantity, execution price and currency requested by the form.
   Review decimal separators and the resulting preview before confirming.
3. For a dividend, enter the number of shares and cash in **EUR per share**.
   The currency is locked to EUR for this action. Do not paste a total dividend
   amount or a market price into the per-share field.
4. Review the effect on the position and cash, then save.
5. Check the result in [Movements](17-movements.md) and reconcile the portfolio.

## Record cash
Use the deposit/withdrawal controls for capital flows. Preview the amount, date
and reason, then confirm. An opening deposit establishes your starting capital;
a later contribution is not trading profit.

If a request times out after submission, inspect the ledger before repeating
it. A missing acknowledgement does not prove that a transaction failed, and
repeating a successful request can duplicate it.

## CSV imports are a separate workflow
The optional [CSV importer](../../../tools/ops/importa_trade_csv.py) accepts
columns data, ticker, azione, quantita, prezzo, valuta and note. Review
[its synthetic example](../../../tools/ops/esempio_trade.csv) before adapting a
copy. The script defaults to a preview; application requires its explicit flag.

The importer is **not equivalent to this page's cash-aware transaction flow**:
it does not update cash automatically, and legacy realised-EUR values can require
separate reconstruction. Even preview constructs the database abstraction and
can apply schema migrations. Applied imports back up the database and refuse an
active backend, but a multi-row failure can leave partial progress.
Reconcile before retrying; never experiment on your only live database.

For an existing cash ledger, do not repeat an opening-cash migration just because
the application was updated.
