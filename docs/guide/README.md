# The Bellomberg handbook

This handbook explains the source distribution and its current interface. The
app lets you choose English or Italian at first login and in Settings. Historical
research keeps its original text. This guide is in English; the glossary also
explains Italian labels. Function numbers follow the shared navigation registry.
**F19 opens the settings panel; it is not a separate research page.**

Every illustration is an original **DEMO mockup** made with invented instruments,
text and values. These are explanatory diagrams, not screenshots, investment
results or data shipped into your portfolio.

## Start here

1. [Install and launch](installation.md): prerequisites, Windows setup, first
   login, desktop packaging and the limits of other operating systems.
2. [Configure your data and services](configuration.md): credentials, runtime
   folders, optional sources, instrument metadata and your own mandate.
3. [Complete a first research cycle](first-session.md): enter a book, inspect
   data, ask a desk, run the committee and review its decisions.
4. [Read the data correctly](reading-data.md): unavailable values, currencies,
   forecasts, attribution and the meaning of scores.
5. [Update and protect your data](updates.md): safe application updates, backups
   and a separate workflow for publishing code.
6. [Troubleshoot](troubleshooting.md): startup, authentication, providers and
   missing history.

## Every destination

| Key | Menu destination | Open it when you want to… |
| --- | --- | --- |
| F1 | [Command Center](pages/01-command-center.md) | Review the portfolio and start a research run |
| F2 | [Performance](pages/02-performance.md) | Separate investment performance from cash flows |
| F3 | [Watchlist](pages/03-watchlist.md) | Keep companies under observation without buying them |
| F4 | [Global Markets](pages/04-global-markets.md) | Search securities, inspect charts and company data |
| F5 | [News Desk](pages/05-news-desk.md) | Read news, briefings and event calendars |
| F6 | [Fundamentals](pages/06-fundamentals.md) | Inspect generated valuation models and assumptions |
| F7 | [Factor Lab](pages/07-factor-lab.md) | Examine shared portfolio exposures and model quality |
| F8 | [Monte Carlo](pages/08-monte-carlo.md) | Compare a portfolio scenario with the current book |
| F9 | [Vol Deck](pages/09-vol-deck.md) | Inspect expirations, Greeks and option strategies |
| F10 | [Edge Scanner](pages/10-edge-scanner.md) | Investigate quantitative signals and their evidence |
| F11 | [Agent Chat](pages/11-agent-chat.md) | Ask a specialist about a position or a research question |
| F12 | [Agents Live](pages/12-agents-live.md) | Follow the committee while it is working |
| F13 | [Agent Progress](pages/13-agent-progress.md) | Review saved scores, uncertainty and run lessons |
| F14 | [Memo Archive](pages/14-memo-archive.md) | Read previous memos and their linked decisions |
| F15 | [Decisions](pages/15-decisions.md) | Record your response, feedback and vetoes |
| F16 | [Trade Entry](pages/16-trade-entry.md) | Record cash movements and executed trades |
| F17 | [Movements](pages/17-movements.md) | Inspect the trade and cash ledger over time |
| F18 | [Mandate and Journal](pages/18-mandate-journal.md) | Define the committee's rules and maintain your private diary |
| F19 | [Settings](pages/19-settings.md) | Inspect backups, tasks, backend status and model configuration |

## Move around quickly

Press **Ctrl+K** (or **⌘K** where available), type a page name or an exact function
label such as `F18`, use ↑/↓, then press Enter. Escape closes the palette. All
destinations are listed, including F16–F19; a keyboard with only twelve function
keys can still reach every destination through the palette or menu.

Type a ticker from your current book to find its Market, News and Trade Entry
actions. A general query can open Global Markets. Ticker actions are loaded from
your portfolio, not from a built-in list. The palette also includes refresh,
backup, latest-memo and committee commands. A command that requests fresh news,
models or a committee run may consume your own provider quota or credits.

## The words used in the app

| App label | Meaning |
| --- | --- |
| Consigliere / Comitato | The research committee |
| Capo | Chief agent; synthesis of the committee's work |
| Mandato | Your declared investment constraints and preferences |
| Diario | Your private investment journal |
| Tesi | Investment thesis |
| Cassa | Cash |
| Movimenti | Recorded trades and cash movements |
| Aggiorna / Rileggi | Refresh / read again |
| Salva | Save |
| Archivia / Ripristina | Archive / restore |
| n.d. | Not available; not a measured zero |

## Reference

- [Documented sector valuations](sector-valuations.md): the record contract behind
  the methods shown in **F6 Fundamentals**, and why a recognized method still
  yields `FV n.d.` when its records are missing.
- [Changelog](../../CHANGELOG.md): what each published version contains.

For contributors: [architecture](../ARCHITETTURA.md),
[contribution guide](../../CONTRIBUTING.md), [support channels](../../SUPPORT.md)
and [documentation assets](assets.md).
