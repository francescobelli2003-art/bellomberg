# Support

Bellomberg is a personal research tool published as source code and maintained
by one person. There is no support desk, no response-time commitment and no
private contact address. The channels below are the only ones.

## Where to ask

| Need | Channel |
| --- | --- |
| A question about installing, configuring or reading the data | GitHub **Discussions → Q&A** on this repository |
| A reproducible defect | A GitHub issue using the **Bug report** form |
| An idea or a change request | A GitHub issue using the **Feature request** form, or Discussions → Ideas |
| A security problem | GitHub's **private vulnerability reporting** (Security → Report a vulnerability), never a public issue; see [SECURITY.md](SECURITY.md) |

Blank issues are disabled: the forms ask for the details that make a report
usable.

## Before asking

1. Read the handbook: [troubleshooting](docs/guide/troubleshooting.md) maps
   symptoms to checks and actions; [reading the data](docs/guide/reading-data.md)
   explains `n.d.`, `STALE`, proxies and scores; [configuration](docs/guide/configuration.md)
   lists which key enables which feature.
2. Check that the problem is not a missing provider entitlement or an empty
   configuration: the application declares those instead of hiding them, and no
   code change can repair a subscription you do not have.
3. Note the version or commit (`pyproject.toml` / `app/package.json`, or
   `git rev-parse --short HEAD`), the operating system, the output of
   `python --version` and `node --version`, and the page involved (its F-number).
4. Reproduce the problem with **synthetic instruments and values**.

## What to include

- The page (F-number and name) and the exact steps.
- What you expected and what you saw, including any `n.d.`, `STALE` or error
  label shown on screen.
- The sanitized error text or log lines.
- Whether the backend was started by the desktop or separately, and the
  interpreter used.

## What never to paste

- The content of `.env`, any API key, token, the PIN or a session token.
- Your database, its backups, `portfolio.json`, exports or reports.
- Positions, quantities, prices paid, balances, NAV, cash movements.
- Your mandate text, journal entries, memos, decisions, chat transcripts.
- Absolute paths containing your user name, email addresses, account or broker
  identifiers.
- Screenshots that show any of the above.

If something slipped through, edit or delete the post and rotate any exposed key.

## What this project does not offer

Bellomberg is research software, not a broker, an advisory service or a data
vendor. Nothing here is financial advice. Provider availability, exchange
coverage and entitlements depend on your own accounts, and the maintainer cannot
diagnose them for you.
