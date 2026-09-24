# Configure your own installation

[Handbook](README.md) · [First research session](first-session.md)

## Configuration files are private

Copy [.env.example](../../.env.example) to `.env` only on a new installation.
The tracked file documents variable names; the private copy holds your secrets.
Use a text editor, keep one assignment per line and restart the backend after
changing startup configuration. Do not paste a complete `.env` into an issue,
chat, screenshot or public commit.

Set `BELLOMBERG_PIN` to exactly four ASCII digits. Missing/malformed PINs and the
default `1234` are rejected. The PIN protects local application access; it is not
an authentication design for exposing the API to the public internet.

## Language is a profile preference

Choose English or Italian after the first login, or in **F19 Settings** later.
The choice is stored in `preferences.json` inside `BELLOMBERG_DATA_DIR`, separately
from the portfolio and mandate. Saving includes a readback. Browser storage is a
local display cache; it does not replace the saved profile preference.

The interface updates without discarding drafts. Newly generated committee
research, chat replies, briefings, news summaries and report labels use the
selected language. A running generation keeps the language captured when it
started. Existing documents, source quotations, notes and numerical records stay
unchanged; changing language does not itself launch a model or email delivery.
Historical records without language metadata remain explicitly unknown.

When no preference file exists, background workflows retain the declared Italian
compatibility default; the first-run interface still asks for an explicit choice.
An unreadable preference is an error. Use the displayed readback/repair controls;
explicit repair preserves a verified copy of the unreadable file.

## Enable the capabilities you need

| Capability | Configuration | What to expect without it |
| --- | --- | --- |
| AI chat, committee, briefing and classification | `OPENROUTER_API_KEY` and the relevant model variables | That model workflow cannot complete |
| Automatic valuation models | [Valuation schema and private budget/trigger configuration](sector-valuations.md#shared-automatic-preparation), plus provider access | Preparation remains disabled; missing inputs or setup are declared |
| Macro series | `FRED_API_KEY` | Macro inputs can be unavailable |
| Options chains, IV and Greeks | `POLYGON_API_KEY` plus suitable account access | Missing or rejected option data; the manual strategy calculator remains separate |
| News, earnings and company intelligence | `FINNHUB_API_KEY` | Coverage depends on endpoint and account |
| Web research | `TAVILY_API_KEY` | Web-search tools cannot provide their results |
| News feeds | `NEWS_API_KEY`, `MARKETAUX_API_KEY`, `THENEWSAPI_API_KEY`, `GNEWS_API_KEY`, `TIINGO_API_KEY` as needed | Individual sources may be absent; some legacy feeds appear empty |
| US filings / European reports | `SEC_CONTACT_EMAIL` | Requests needing a contact User-Agent fail |
| Congress, lobbying and government-contract data | `QUIVER_API_KEY` | Those tools cannot provide data |
| Optional email output | `EMAIL_FROM`, `EMAIL_PASSWORD`, `EMAIL_TO` | No configured delivery |
| Optional IBKR source | Separate broker application and setup | Other available price sources remain separate |

A configured key does not guarantee a particular exchange, endpoint, real-time
entitlement or rate limit. Check your own provider account. This guide does not
quote subscription prices or promise coverage for every instrument.
[IBKR setup](../setup/IBKR.md) is optional.

Model choices are resolved from `CHAT_MODEL`, `CONSIGLIERE_MODEL`, the individual
desk overrides and variables for synthesis, red team, reflection and extraction.
Review the template; leaving a required model variable blank produces an explicit
configuration error. A model identifier in a sample is not a guarantee that a
provider still serves it or supports every requested feature. Verify availability
in your provider's catalogue. Do not replace all model variables blindly.

`CHAT_MAX_TOKENS` limits each chat model call, including reasoning where applicable.
It is a token budget, not a character count or a promised answer length. Larger
values may increase cost and latency; model context and provider limits still
apply. The template allows longer responses, while your existing private setting
is preserved by a normal update.

## Where your data lives

| Setting | Default under the project root | Content |
| --- | --- | --- |
| `BELLOMBERG_DATA_DIR` | `data/` | SQLite, semantic index, private stores, logs, caches and backups |
| `BELLOMBERG_REPORT_DIR` | `report/` | Generated report/valuation files |
| `BELLOMBERG_RESEARCH_NOTES_DIR` | `research_notes/` | Generated research notes |
| `BELLOMBERG_PROJECT_ROOT` | Detected from a source checkout | Root used for configuration and relative paths |

The SQLite book is `consigliere.db` inside the data directory. A new installation
starts empty and gets its cash through Trade Entry. Do not import or maintain an
Excel portfolio as a competing source. The legacy `portfolio.json` is only an
explicit old-cash migration input.

For a non-editable package installed away from its source, set
`BELLOMBERG_PROJECT_ROOT` **in the process environment before starting Python**,
so the package can find the deployment's `.env`. Relative runtime overrides are
resolved against that root. Use the same directories across desktop, backend
and any scheduled tasks. A different empty directory is a different book.

## Your mandate and instrument metadata

Configure the mandate through **F18 Mandate and Journal** (*Mandato e Diario*) →
**Mandate** (*Mandato*), validate its preview and save.
Personal risk limits are not supplied by someone else's portfolio. An example
profile is an editable starting point that you must review; it is not a
recommendation or your automatically approved mandate.

Some advanced tools also require instrument metadata. Their public format
examples live in [the examples directory](../../src/bellomberg/resources/examples).
These are schemas and synthetic samples—not a list of positions to adopt.

| Private store | What it controls |
| --- | --- |
| `veicoli.json` | Instrument classification, source/provenance, currency and valuation routing |
| `alias_fonti.json` | Explicit provider aliases for instruments/listings |
| `iv_tickers.json` | Configured universe for historical IV collection |
| `fonti_guidance.json` | Issuer guidance/report source registry |
| `lei_emittenti.json` | Verified issuer LEI mappings |
| `istituzioni.json` | Institutional/closed-end-fund mappings |
| `fattori_portafoglio.json` | Declared factor/region mappings |
| `news_search_terms.json`, `news_topics_tickers.json` | News search and topic-to-instrument configuration |

When a tool reports a missing store, use its corresponding `.example.json` to
understand the required format, create your own file in the configured data
directory and fill only verified information. Do not bulk-copy synthetic example
instruments into an operational profile. These files are not all editable in a
dedicated GUI yet. Missing or unknown classification can deliberately block
valuation instead of choosing a plausible engine silently.

Most portfolio reporting uses EUR. A currency preference in the mandate does not
automatically rebuild every accounting/risk engine in a different base currency.
Always inspect the currency and basis printed by the specific tool.
