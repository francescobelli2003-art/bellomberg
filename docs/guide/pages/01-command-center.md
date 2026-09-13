# F1 — Command Center

[Handbook](../README.md) · [Next: F2](./02-performance.md)

![F1 Command Center: original synthetic DEMO mockup](../../assets/product/01-command-center.svg)

*DEMO illustration: invented instruments, values and text; not a screenshot.*

Start here to answer “What do I own, how has it moved, and what needs attention?”
The dashboard combines portfolio totals, holdings, the portfolio curve and
research controls. It reads the SQLite book; a watchlist is a separate list.

## Use it
1. Check the last update time and any unavailable or stale fields before reading
   a change as a fresh market move.
2. Inspect each position's quantity, quote currency, market value and gain or
   loss. Open a holding to continue in **Global Markets**.
3. Switch the portfolio curve between quota/performance and wealth where those
   controls are available. Deposits can increase wealth without generating an
   investment return; use [Performance](02-performance.md) for the distinction.
4. Read Macro Pulse as market context. Its displayed instruments are a display
   preference, not a second portfolio.
5. Before starting the **Consigliere**, complete your [mandate](18-mandate-journal.md),
   reconcile cash and verify provider configuration. Follow execution in
   [Agents Live](12-agents-live.md).

## What a new installation shows
An empty portfolio has no positions or investment track record. Before the first
deposit, cash is explicitly uninitialized; a displayed zero with that warning is
not a measured balance. Record cash and actual trades in
[Trade Entry](16-trade-entry.md). For a holding whose purchase history is unknown,
use its separate **Opening position** workflow and document the balance known
as of a date. Do not invent historical purchases to populate a chart.

A refresh asks the relevant services for data; it is not a trade or a deposit.
An unknown quote, missing FX rate or incomplete history can prevent reliable
totals. Investigate the source rather than replacing missing figures with zero.
The summary is a starting point: inspect the ledger before correcting a number.
