# Bellomberg architecture

Bellomberg has two local processes: an Electron/React desktop client in `app/` and a FastAPI service installed from `src/bellomberg/`. The client calls the API on the loopback interface. The service owns portfolio state, analytics, provider access, and model orchestration.

```text
Electron / React UI
        |
        | HTTP on loopback
        v
FastAPI service (`bellomberg-api`)
        |-- SQLite portfolio and research state
        |-- market-data and filing providers
        |-- valuation and portfolio analytics
        `-- OpenRouter / configured model provider
```

## Python package

The `src/bellomberg/` package is organized by responsibility:

- `api/` defines HTTP routes, validation, authentication, and response contracts.
- `agents/` contains the committee, specialist orchestration, chat, red-team, scoring, and agent tools.
- `core/` contains configuration, freshness rules, model access and pricing, current facts, and mandate handling.
- `storage/` owns SQLite access, schema migrations, classifications, and private-store loaders.
- `market_data/` integrates price, macro, news, filing, consensus, and taxonomy sources.
- `valuation/` contains DCF, bank, RAB, mNAV/NAV, CEF, and peer-comparison engines.
- `portfolio/` contains performance, attribution, factor, risk, GARCH, Monte Carlo, sizing, signal, and volatility analytics.
- `reporting/` creates charts, memos, PDFs, and email output.
- `cli/` provides the installed command entry points.
- `resources/examples/` contains tracked synthetic configuration examples and public reference snapshots such as Damodaran aggregates.

The primary command is `bellomberg-api`. Additional commands declared in `pyproject.toml` run the committee, price updater, briefing, and memo recovery workflows.

## State and paths

`bellomberg.core.paths` is the canonical source for runtime locations. `BELLOMBERG_PROJECT_ROOT` identifies the project deployment. `BELLOMBERG_DATA_DIR`, `BELLOMBERG_REPORT_DIR`, and `BELLOMBERG_RESEARCH_NOTES_DIR` can override the default data, report, and research-note directories.

The SQLite database is the only portfolio source. It contains positions, trade history, cash movements, cash state, NAV snapshots, research output, chat history, decisions, news, and model-usage records. A new database begins with no cash state; the user records the first deposit through the application. `portfolio.json` exists only as an input to the explicit legacy migration and is not part of a new installation.

SQLite write paths use transactions. Maintenance commands that change financial state default to a dry run where applicable, take a backup before applying changes, and declare partial or failed work.

## Research flow

The committee assembles the current portfolio, mandate, market context, and tool results. Specialist agents cover macro, events, crypto, fundamentals, quantitative analysis, and options. A red-team stage challenges the reports, and the chief agent produces a memo and proposed decisions. Numeric claims are expected to cite tool output with `[src: tool]`; missing or stale inputs must remain visible instead of being replaced silently.

Configured model calls are remote. Requests pass through OpenRouter to the selected model provider and can include portfolio positions, the user's mandate, and research context.

## Market data, valuation, and risk

Provider adapters isolate external HTTP/API behavior from calculation modules. Valuation engines cover conventional DCFs and specialized bank, regulated-asset, closed-end-fund, and mNAV cases. Portfolio modules calculate TWR, attribution, exposures, risk estimates, scenarios, and options analytics. Each result carries source/freshness or error state where the underlying input can be absent or stale.

IBKR is optional and requires a separately running Trader Workstation or Gateway; see `docs/setup/IBKR.md`. Other sources and their required keys are listed in `.env.example`.

## Desktop application

`app/src/` contains React pages, shared components, hooks, and pure data-formatting/validation helpers. `app/electron/` owns the desktop window and development-time backend lifecycle. The production desktop bundle contains the web UI and Electron code. It relies on an external Python environment for the backend.

## Operational tools

- `tools/ops/` contains operator-facing utilities, including CSV trade import.
- `tools/migrations/` contains explicit data and schema migrations.
- `tools/release/` contains packaging, export, and release gates.

The public export uses an allowlist and privacy checks. Runtime directories, secrets, personal mandates, and portfolio data are excluded from the published tree.

## Verification boundaries

Python tests run from `tests/` using synthetic temporary state. Frontend contract tests and the production build run under `app/`. These checks cover offline behavior and selected smoke paths. They do not certify live third-party availability, paid entitlements, brokerage sessions, email delivery, or every host-specific installer behavior.
