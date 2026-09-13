# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability). It reaches the maintainer privately and
keeps the details out of the public timeline until a fix is available. There is
no separate contact address.

Include, if you can: what the problem is, how to reproduce it, and what an
attacker could obtain. A proof of concept is welcome but never required. Use
synthetic instruments and values in the report and remove keys, the PIN,
tokens and personal paths from any log you attach.

## Supported versions

Fixes are published on `main` through sync commits from the maintainer's private
repository. There are no maintained release branches or tags: only the latest
published state receives fixes.

## Scope

Bellomberg is a personal, single-user desktop application: a Python backend
listening on the loopback interface (`127.0.0.1:8765` by default) and an Electron
front end that talks to it. It is not a hosted service and it has no multi-tenant
model.

What is in scope:

* anything that lets a local process read the SQLite database, the `.env`, or
  the API keys without the PIN;
* command or SQL injection through user input, saved theses, CSV import, or
  model output;
* the login PIN flow and the session token in
  `src/bellomberg/api/bellomberg_api.py` (the root `bellomberg_api.py` is only a
  launcher);
* the Electron shell: preload, sandbox and content-security policy in
  `app/electron/`;
* the export gate under `tools/release/` (`export_pubblico.py`,
  `verifica_pubblico.py`, `verifica_deposito.py`): a way to make it publish
  something it should not is a security bug.

What is out of scope: denial of service against your own machine, third-party
services the app queries (Yahoo Finance, SEC EDGAR, FRED, OpenRouter, news
providers and the others), and anything that requires an attacker to already
have your Windows account.

## Keys and data

This repository ships **no** `.env`, no database and no `portfolio.json`: keys,
positions and cash live only on your machine. See `.env.example` and
`src/bellomberg/resources/examples/portfolio.example.json` for the shape of what
you provide. Every file published here goes through an export gate
(`tools/release/verifica_pubblico.py`) that looks for keys, private values and
the maintainer's own data before it leaves the private repository; the policy
lists the gate reads are deliberately not published. If you find a real secret
in the history, report it privately as above.
