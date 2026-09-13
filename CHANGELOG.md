# Changelog

All notable changes to the published Bellomberg source are recorded in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version number is the one declared in `pyproject.toml` and `app/package.json`.

The public history consists of `sync` commits exported from the maintainer's
private repository. A version entry summarizes what those syncs contain; it is
not a list of commits. A prepared entry is not evidence of publication; check
the repository's Releases page for the published tag and any attached assets.

## [Unreleased]

## [0.8.0] - Unreleased

### Added

- Explicit English/Italian selection at first login and in Settings. New
  research captures the selected language; existing memos and source quotations
  retain their original content and language provenance.
- Dated trade previews with a confirmation token, chronological position replay,
  historical exchange-rate labels and explicit links to recorded decisions.
- Documented opening positions for existing holdings, separate from purchases,
  cash movements and invented historical NAV. Previews expire and reject a book
  that changed; uncertain confirmation results require readback.
- A first-installation check using the installed Python entry point and an empty
  isolated profile. The guide covers language selection, ledger setup and the
  actual limits of macOS source support.
- Most desktop pages, dialogs, charts and error states follow the selected
  language. Changing language keeps open drafts and does not request provider
  data again; tickers, identifiers, provider text and personal notes are not
  translated.
- New memos, PDF reports, charts, e-mail bodies and Excel workbooks are
  generated in the selected language. Formulas, identifiers, thresholds and
  source quotations are unchanged. News summaries and briefings are stored per
  language; opening an older item does not request a translation.
- Additive database migrations record how a trade date was entered, how a trade
  is linked to a decision, the source of the exchange rate applied (historical,
  current, or none for a EUR trade) and the language of newly generated output.
  Existing rows keep these fields empty (unknown) instead of receiving an
  assumed value.
- `tools/migrations/marca_ora_convenzionale.py` marks legacy trades recorded at
  exactly noon as date-only entries. It previews by default, writes only with
  `--apply`, and leaves a receipt for a targeted reversal.
- F13 Agent Progress gives an exact reading on the history chart, by pointer or
  keyboard: hit rate, sample size, measurement date, 95% interval and quality,
  with `n.d.` for each missing value. Reading a point does not change the
  selection.
- Before the first round, the committee sends a minimal request to each
  configured model and lists any failure in the run's health report; the run
  continues. The red-team review records the cause of an API error.
- Committee e-mails state each requested valuation (fair value, or `n.d.` with
  its reason) and name the attached Excel models, or say that none is attached.
- `docs/guide/build_wiki.py` generates Wiki pages, sidebar and footer from the
  handbook without publishing anything; `--check` reports missing, changed and
  unexpected files. `app/tools/capture-docs.cjs` and
  `docs/guide/verify_screenshots.py` capture the real interface with invented
  data and verify screenshot provenance.
- Code of conduct, support page, issue forms for bug reports and feature
  requests, discussion forms for Q&A and Ideas, and a pull request template.
- CI runs the Linux suite on Python 3.12 and 3.14 and adds a macOS source job.

### Changed

- F9 Vol Deck is organized in four tabs: Acquisition, Tools, Chain and
  Laboratory. The 3D surface keeps its controls. Outside Acquisition, a status
  strip appears when something must be declared (catalogue or download errors,
  an incomplete, interrupted or STALE download, surface coverage and the reason for each
  uncovered expiry), with a **Coverage and sources** button back to Acquisition.
- F18 Mandate and Journal use a new layout. The mandate shows one of its seven
  sections at a time, each marked with errors, the number of missing required
  fields, or completion; a recovered draft without a recorded number format is
  flagged. The journal lists earlier versions in a column beside the editor.
- When a specialist desk retries after an empty model response, its tools stay
  available. An analysis round after reconnaissance that ends without any tool
  call is marked in the report
  and reaches the memo. The chief agent reads each desk's latest useful round.
- The Events desk's political score uses only open, relevant prediction
  markets, the most traded per theme; themes without a market are `n.d.`. The
  desk also reports how many news sources returned nothing and names them.
- Valuation tool results give desks and agent chat the fair value, gate result
  and information cutoff before acquisition data. The fundamentals tool asks for
  the records and scenario rationale of the selected method.
- In the committee's context, past decisions linked to recorded trades state
  whether they were executed, skipped or expired and the executed amount against
  the proposal. The action
  validator flags a proposal that repeats a recent execution and applies the
  stress budget to new names as well.

### Fixed

- A documented valuation remains usable after its generation date when no
  separate valuation reference is requested. Explicit references remain strict;
  missing documentation still blocks fair value.
- Briefing failures now return a failing process result. Unreadable caches are
  preserved and reported before attempting generation.
- Numeric input follows an explicit language, rejects ambiguous grouping, and
  preserves the meaning of research drafts when the interface language changes.
- Saved-memo counts use evidence from the returned sample instead of subtracting
  lists with different pagination limits. A real zero NAV remains zero.
- Windows scheduled-task actions can use the existing hidden launcher while
  preserving triggers, task settings, logs and exit results. Applying task
  changes requires the operator's administrator approval.
- The login screen displays the package version instead of a decorative number.
- The Vol Deck cone panel names the actual state of its context request
  (loading or failed) and no longer presents client-side errors as declared by
  the backend.
- The scheduled database backup no longer aborts in its fallback path (backend
  not running) before verifying the archive and pruning old backups.
- macOS source runs: PDF renderers look for fonts in macOS folders and declare
  typographic fallbacks; Windows-only Excel recalculation and the Damodaran
  conversion say so before starting; the desktop prefers `.venv/bin/python`,
  honors an explicit interpreter and keeps its backend when the last window
  closes.
- Performance shows failures of its drawdown, liquidity, concentration, VaR and
  metrics services instead of omitting the panels. The dashboard leaves the
  loading state after a portfolio error and does not report zero decisions when
  the read fails.
- ESEF entity lookup retries a company name without its legal form.
- SEC ownership filings filed as `SCHEDULE 13D` or `SCHEDULE 13G` (the form
  names EDGAR uses since December 2024) are recognized again.
- Settings labels that had lost accents or separators are restored, and a test
  rejects catalogue values where a character was replaced by `?`.
- The release export runs its test suite on a verified copy, installs the
  frontend dependencies from the exported lockfile as CI does, and removes
  private configuration names from the suite environment.
- A trade-entry field keeps the number format it was written in until it is
  cleared; the desktop test now follows that rule.

### Known limits

- Opening balances do not reconstruct earlier purchases or returns. Attribution
  declares unsupported opening-balance coverage; official performance requires
  actual covered NAV observations.
- macOS source CI is provided, but this release preparation has not been
  validated on a physical Mac. No macOS bundle, signing, notarization or Linux
  installer is certified. Windows desktop packages still require a separate
  Python backend, dependencies and configuration.
- Provider access and AI generation use the operator's accounts. Offline replay
  tests do not demonstrate live research quality or delivered email.
- Documentation illustrations remain synthetic DEMO mockups made before
  language selection and the F9, F13 and F18 layouts of this version: they show
  Italian labels, and the F9 illustration shows the former views. Captured
  screenshots are not yet part of the handbook.
- Language coverage has not been certified screen by screen. Provider text,
  source quotations, personal notes and records created before this version are
  not translated; records without language metadata remain unknown.

## [0.7.0] - 2026-09-10

First public release. The codebase already carried this version number before
publication; this entry describes the published state as of the sync of
2026-09-10.

### Added

- **Python package** `bellomberg` under `src/bellomberg/` with the installed
  entry points `bellomberg-api`, `bellomberg-committee`, `bellomberg-prices`,
  `bellomberg-briefing` and `bellomberg-recover-memo`; thin root launchers kept
  for operational compatibility.
- **Portfolio ledger** in a single SQLite database: positions, trades, cash
  movements and cash state recorded through the application, with TWR,
  benchmark comparison, drawdown, attribution, concentration, factor exposures,
  risk estimates and Monte Carlo scenarios.
- **Research pages**: security search with charts, financials and holders;
  watchlist with notes; news desk with briefings and event calendars; quantitative
  signals with their supporting data; SEC EDGAR and ESEF filing readers, plus a
  filing-comparison module for US and European documents (usable from Python and
  the command line; not yet wired into a page).
- **Valuation**: DCF engines (conventional, bank, regulated-asset, mNAV and
  closed-end-fund cases), a DCF quality sheet, and documented sector methods
  (banks, insurance and life, regulated assets, real estate and development,
  finite resources, NAV, sum-of-the-parts, managed care, operating cash flow)
  selected by an economic resolver; missing records produce an explicit
  `FV n.d.` instead of a number. Contract described in
  `docs/guide/sector-valuations.md`.
- **Options**: progressive option-chain download with pause and resume, choice
  of a final expiration or the whole provider catalogue, quotes and Greeks,
  volatility surface, and multi-leg strategy simulation. Plotly 2.32.0 is
  vendored with its license and listed in `NOTICE`.
- **AI research committee**: Macro, Events, Crypto, Fundamentals, Quant and
  Options desks, a red-team review, and a chief agent that writes the memo and
  the proposed decisions; agent chat per desk; live run view with tool activity
  and recorded costs; saved score history; memo archive with linked decisions;
  decision recording with feedback and vetoes. Model requests go through
  OpenRouter with one configurable model per function.
- **Mandate and journal**: declared investment constraints validated before a
  committee run, and a private versioned journal.
- **Settings panel**: database backups, scheduled-task status, backend status
  and model configuration.
- **Desktop application**: Electron/React interface with a command palette
  (Ctrl+K) covering all nineteen destinations, PIN login (four ASCII digits,
  `1234` rejected), loopback-only API, and an unsigned Windows NSIS installer
  that contains the interface but not Python or data.
- **Operations**: Windows scheduler scripts, CSV trade import, explicit data and
  schema migrations including the legacy cash migration with dry run and
  `--apply`, and an additive valuation-metadata migration.
- **Documentation**: English handbook with nineteen page chapters and synthetic
  DEMO illustrations, installation, configuration, first-session, reading-data,
  updates and troubleshooting guides, architecture notes, and
  `docs/guide/verify_docs.py` to check links and inventory.
- **Release and licensing**: allowlisted source export with privacy and secret
  checks (`tools/release/`), CI running the offline suite on Ubuntu and the
  desktop build on Windows, Apache-2.0 license with `NOTICE`.
