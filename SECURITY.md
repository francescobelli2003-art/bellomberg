# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability). It reaches the maintainer privately and
keeps the details out of the public timeline until a fix is available.

Include, if you can: what the problem is, how to reproduce it, and what an
attacker could obtain. A proof of concept is welcome but never required.

## Scope

Bellomberg is a personal, single-user desktop application: a Python backend on
`localhost:8765` and an Electron front end that talks to it. It is not a hosted
service and it has no multi-tenant model.

What is in scope:

* anything that lets a local process read the SQLite database, the `.env`, or
  the API keys without the PIN;
* command or SQL injection through user input, saved theses, CSV import, or
  model output;
* the login PIN flow and the session token in `bellomberg_api.py`;
* the export gate under `scripts/` (`export_pubblico.py`, `verifica_pubblico.py`):
  a way to make it publish something it should not is a security bug.

What is out of scope: denial of service against your own machine, third-party
services the app queries (Yahoo Finance, SEC EDGAR, FRED, NewsAPI and the
others), and anything that requires an attacker to already have your Windows
account.

## Keys and data

This repository ships **no** `.env`, no database and no `portfolio.json`: keys,
positions and cash live only on your machine. See `.env.example` and
`portfolio.example.json` for the shape of what you provide. Every file published
here goes through an export gate (`scripts/verifica_pubblico.py`) that looks for
keys, private values and the maintainer's own data before it leaves the private
repository. If you find a real secret in the history, report it privately as above.
