# Documentation screenshots and illustrations

[Handbook](README.md)

## Screenshot gallery

These captures show the actual English interface with one synthetic DEMO
account. The capture tool adds the visible DEMO watermark. Analytics, histories
and research text are authored snapshots; capturing them does not rerun the
analytics pipeline or establish investment results. The data is not installed
into your portfolio. [Reproduction instructions](screenshots.md) describe the
fixture, clock, local service and capture checks.

| Destination | English screenshots |
| --- | --- |
| [F1 Command Center](pages/01-command-center.md) | [Overview](../assets/screenshots/dashboard-en.png) |
| [F2 Performance](pages/02-performance.md) | [Tearsheet](../assets/screenshots/performance-en.png) · [Attribution](../assets/screenshots/performance-attribution-en.png) · [Book & Risk](../assets/screenshots/performance-book-risk-en.png) |
| [F3 Watchlist](pages/03-watchlist.md) | [Watchlist](../assets/screenshots/watchlist-en.png) |
| [F4 Global Markets](pages/04-global-markets.md) | [Overview](../assets/screenshots/market-overview-en.png) · [Security](../assets/screenshots/market-security-en.png) · [Financials](../assets/screenshots/market-financials-en.png) |
| [F5 News Desk](pages/05-news-desk.md) | [Wire](../assets/screenshots/news-wire-en.png) · [Desk](../assets/screenshots/news-desk-en.png) |
| [F6 Fundamentals](pages/06-fundamentals.md) | [Valuations](../assets/screenshots/fundamentals-en.png) |
| [F7 Factor Lab](pages/07-factor-lab.md) | [Factors](../assets/screenshots/factor-lab-en.png) |
| [F8 Monte Carlo](pages/08-monte-carlo.md) | [Scenarios](../assets/screenshots/montecarlo-en.png) |
| [F9 Vol Deck](pages/09-vol-deck.md) | [Tools](../assets/screenshots/vol-deck-tools-en.png) · [Chain](../assets/screenshots/vol-deck-chain-en.png) · [Laboratory](../assets/screenshots/vol-deck-laboratory-en.png) |
| [F10 Edge Scanner](pages/10-edge-scanner.md) | [Signals](../assets/screenshots/edge-scanner-en.png) |
| [F11 Agent Chat](pages/11-agent-chat.md) | [Conversation](../assets/screenshots/agent-chat-en.png) |
| [F12 Agents Live](pages/12-agents-live.md) | [Run activity](../assets/screenshots/agents-live-en.png) |
| [F13 Agent Progress](pages/13-agent-progress.md) | [Metrics and history](../assets/screenshots/agent-progress-en.png) |
| [F14 Memo Archive](pages/14-memo-archive.md) | [Memo](../assets/screenshots/memo-archive-en.png) |
| [F15 Decisions](pages/15-decisions.md) | [Decisions](../assets/screenshots/decisions-en.png) |
| [F16 Trade Entry](pages/16-trade-entry.md) | [Ticket](../assets/screenshots/trade-entry-en.png) · [Local simulation](../assets/screenshots/trade-entry-after-en.png) · [Confirmation preview](../assets/screenshots/trade-entry-confirm-en.png) |
| [F17 Movements](pages/17-movements.md) | [Register](../assets/screenshots/movements-register-en.png) · [Timelines](../assets/screenshots/movements-trails-en.png) · [Transaction diary](../assets/screenshots/movements-diary-en.png) |
| [F18 Mandate and Journal](pages/18-mandate-journal.md) | [Mandate](../assets/screenshots/mandate-en.png) · [Journal](../assets/screenshots/journal-en.png) |
| [F19 Settings](pages/19-settings.md) | [Settings panel](../assets/screenshots/settings-en.png) |

## Illustrations

The separate SVG illustrations were drawn for Bellomberg.
They explain page structure and workflow using **entirely invented DEMO data**.
They are not screenshots, live product results, sample investments or evidence
that a provider returned a particular value. The application is available in
English and Italian: the illustrations show its Italian labels, while their
accessible titles, descriptions and footnotes, like this manual, are in English.

The visual language follows the terminal: dark blue-grey surfaces, restrained
gold for primary actions and cyan for comparative data. Each destination has a
different central view, such as a ledger, conversation, memo or option payoff.
These simplified layouts explain the relevant controls; exact spacing and
content in the running application can differ.

### Illustration gallery

| Destination | Illustration |
| --- | --- |
| [F1 Command Center](pages/01-command-center.md) | [Open SVG](../assets/product/01-command-center.svg) |
| [F2 Performance](pages/02-performance.md) | [Open SVG](../assets/product/02-performance.svg) |
| [F3 Watchlist](pages/03-watchlist.md) | [Open SVG](../assets/product/03-watchlist.svg) |
| [F4 Global Markets](pages/04-global-markets.md) | [Open SVG](../assets/product/04-global-markets.svg) |
| [F5 News Desk](pages/05-news-desk.md) | [Open SVG](../assets/product/05-news-desk.svg) |
| [F6 Fundamentals](pages/06-fundamentals.md) | [Open SVG](../assets/product/06-fundamentals.svg) |
| [F7 Factor Lab](pages/07-factor-lab.md) | [Open SVG](../assets/product/07-factor-lab.svg) |
| [F8 Monte Carlo](pages/08-monte-carlo.md) | [Open SVG](../assets/product/08-monte-carlo.svg) |
| [F9 Vol Deck](pages/09-vol-deck.md) | [Open SVG](../assets/product/09-vol-deck.svg) |
| [F10 Edge Scanner](pages/10-edge-scanner.md) | [Open SVG](../assets/product/10-edge-scanner.svg) |
| [F11 Agent Chat](pages/11-agent-chat.md) | [Open SVG](../assets/product/11-agent-chat.svg) |
| [F12 Agents Live](pages/12-agents-live.md) | [Open SVG](../assets/product/12-agents-live.svg) |
| [F13 Agent Progress](pages/13-agent-progress.md) | [Open SVG](../assets/product/13-agent-progress.svg) |
| [F14 Memo Archive](pages/14-memo-archive.md) | [Open SVG](../assets/product/14-memo-archive.svg) |
| [F15 Decisions](pages/15-decisions.md) | [Open SVG](../assets/product/15-decisions.svg) |
| [F16 Trade Entry](pages/16-trade-entry.md) | [Open SVG](../assets/product/16-trade-entry.svg) |
| [F17 Movements](pages/17-movements.md) | [Open SVG](../assets/product/17-movements.svg) |
| [F18 Mandate and Journal](pages/18-mandate-journal.md) | [Open SVG](../assets/product/18-mandate-journal.svg) |
| [F19 Settings](pages/19-settings.md) | [Open SVG](../assets/product/19-settings.svg) |

### Regenerate and verify illustrations

Run from the source checkout root:

```powershell
python docs/guide/build_mockups.py
python docs/guide/verify_docs.py
```

Both scripts use only the Python standard library. The generator contains its
own invented display values and writes the nineteen SVG files; it reads no
application configuration, database, report, provider or user portfolio.
The verifier checks local links, the navigation inventory and SVG structure.
Neither script starts the application or runs financial calculations.

SVGs include accessible titles and descriptions, contain no raster images or
external font/image links, and can be opened directly in a browser. They remain
illustrations; the PNG captures in the screenshot gallery show the app itself.
Use the synthetic capture fixture when reproducing documentation images, never
a personal portfolio.
