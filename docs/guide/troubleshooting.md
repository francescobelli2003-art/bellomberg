# Troubleshooting

[Handbook](README.md)

| What you see | Check first | What to do |
| --- | --- | --- |
| Login reports PIN configuration error / HTTP 503 | `BELLOMBERG_PIN` in the backend's actual `.env` | Use four ASCII digits other than the rejected default; restart that backend |
| Login returns 401 after restart | The old session token | Sign in again; preserve any unsaved journal text before reloading |
| Python import / module not found | Selected interpreter and installed package | Run the virtual environment's Python; install `-e .` and check `pip check` |
| Electron binary missing | Whether `desktop:install` completed | Run `npm run desktop:install` in `app/` |
| Address already in use | The process owning the API or Vite port | Reuse the intended backend or stop the identified stale process; do not kill unrelated processes |
| Browser development URL fails | The exact hostname Vite printed | Try that URL; do not assume `127.0.0.1` and `localhost` are interchangeable |
| Book unexpectedly empty after update | Runtime directory/interpreter | Stop and verify configuration; do not re-enter the portfolio |
| Committee refuses to start | F18 validation and server error | Complete and save the mandate; inspect model/provider configuration |
| Model unavailable / permissions error | Exact model variable and account | Verify availability and access with your provider; do not assume another model was silently substituted |
| Polymarket reports a TLS, HTTP or response error | The tool's source and error preview | The chat can continue with unavailable probabilities explicitly marked. Do not disable TLS verification; a connection interruption without a final response is reported separately |
| Options catalogue/chain is partial | Progress, entitlement, rate limit and coverage reasons | Resume the saved download; received contracts remain available. Complete download and usable surface curves are separate |
| An expiration is visible but has no surface | Eligibility and sufficient valid quotes | Read its excluded/partial/error reason; inspect the chain separately |
| IV percentile or agent score is unavailable | History length and measurable outcomes | Let real history accumulate; do not backfill invented observations |
| Fundamentals is empty | Whether a model was actually generated | Generate research through the relevant workflow, then inspect its output/errors |
| Journal says schema missing | Whether the updated backend ran its migrations | Verify installed code and startup logs; do not manually replace the database |
| Journal reports HTTP 409 | Another edit used a newer version | Keep your draft and compare the latest saved version before creating a new revision |
| News is empty | Filters, provider keys and account access | Reset filters; check provider setup, because some legacy empty responses do not explain the cause |
| Downloaded memo/attachment absent | Whether the run produced the file | Read run status and attachment availability; a DB row alone is not proof of a PDF |

## Useful checks

Run from the backend checkout, using its environment:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

The default API is local at `http://127.0.0.1:8765`. Its health result shows
service availability/version; a healthy response alone does not prove that every
provider or data path is correct. Interactive API documentation endpoints may
be intentionally disabled in the release.

For a frontend source problem:

```powershell
cd app
node --version
npm run test:release
npm run build:bundles
```

A missing provider entitlement cannot be repaired by rebuilding the frontend.
Do not install arbitrary dependencies, enable unrelated sources or upgrade a
subscription just to clear an unexplained warning.

## Reporting a problem

Describe the page, steps, expected result and actual result. Include app/backend
versions and a **sanitized** error message. Use synthetic instruments and values
when reproducing it. Before attaching logs, screenshots or reports, remove
credentials, positions, balances, personal mandates, notes and local identifiers.
See [SECURITY.md](../../SECURITY.md) for security reporting.
