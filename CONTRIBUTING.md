# Contributing

Bellomberg is developed in a private repository and published through an allowlisted export. Accepted changes are applied upstream and appear in a later sync commit; a pull request may therefore be closed with a reference to the applied commit instead of being merged directly. Published history is not force-pushed.

## Useful contributions

- A reproducible bug report with synthetic inputs and the exact observed result.
- A focused failing test that demonstrates the issue offline.
- A small change that preserves existing interfaces unless the interface itself is the defect.
- Documentation corrections tied to current package paths and commands.

Never submit API keys, tokens, personal financial records, real mandates, absolute personal paths, or production database extracts.

## Development setup

Use Python 3.10 or newer and Node.js 22.12 or newer. CI runs Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e . pytest
Copy-Item .env.example .env
cd app
npm ci
npm run desktop:install
cd ..
$env:BELLOMBERG_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
```

Use synthetic provider credentials and temporary databases in tests. Do not call paid APIs or a real broker from the offline suite.

## Project rules

Financial and research code follows three core rules:

1. Missing, invalid, or stale data is declared. Do not replace it with a silent default or proxy.
2. Numeric claims produced by agents must trace back to tool output and carry a `[src: tool]` citation.
3. SQLite is the only portfolio source. Do not introduce a parallel spreadsheet or JSON portfolio.

Keep changes surgical and retain the language used by the surrounding source. Windows is the supported desktop platform; preserve CRLF where `.gitattributes` requires it and use portable path APIs in Python.

## Tests

Run the smallest relevant tests first, then the offline suite:

```powershell
python -m pytest tests/ -q
```

For frontend changes:

```powershell
cd app
npm run test:release
npm run build:bundles
npm run test:desktop
```

State which checks you ran and which live integrations remain untested. A passing offline suite does not validate current provider availability, account entitlements, brokerage connectivity, or email delivery.

## Pull requests

Describe the concrete trigger, the previous behavior, the resulting behavior, and validation. Keep unrelated cleanup separate. New dependencies need a clear reason and should avoid unnecessary major-version changes.

By contributing, you agree that your contribution may be included under the repository's Apache-2.0 license. See `NOTICE` for project notices.
