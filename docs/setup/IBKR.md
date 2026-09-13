# Interactive Brokers (IBKR) connection: experimental, not validated

Bellomberg can read prices and option data from a Trader Workstation (TWS) or
IB Gateway running on the same machine. The connection is **optional and
experimental**: the release workflow does not validate it against a live TWS
session, the offline test suite exercises it only with synthetic doubles, and the
scheduled Windows price task runs with IBKR disabled (`--no-ibkr`). This page
records what the code does today; it does not promise data quality or coverage.

## Library

- `ib_async` (1.0.1 or newer) is listed in `requirements.txt`, so
  `python -m pip install -e .` installs it with the package.
- `src/bellomberg/agents/agent_tools.py` (`_get_ibkr_options`) imports
  `ib_async` and falls back to the older `ib_insync` package when only that one
  is installed. `src/bellomberg/cli/price_updater.py` (`_fetch_ibkr`) requires
  `ib_async`.
- The sessions are opened read-only. No orders, no account or position reads:
  market data only. The portfolio stays in SQLite.

## Where it is used

### Price updater

- `bellomberg-prices` (or the root launcher `python price_updater.py`) refreshes
  the prices of active positions. Default source order: IBKR, then yfinance, then
  CoinGecko for the symbols declared in the private special-prices store.
- IBKR is tried only for symbols without a dot and of at most five characters
  (US listings). When the library is missing or the connection fails, the symbol
  moves to the next source and the recorded source tells which one answered.
- `--no-ibkr` skips IBKR; `--ibkr-port` sets the socket port (default `7496`).
- The price refresh triggered from the API uses Polygon, then IBKR, then yfinance,
  then CoinGecko.
- `tools/ops/windows/run_price_updater.bat`, the scheduled task, runs with
  `--no-ibkr`.

### Options data tool (`get_options_data`)

The tool used by the agents and by the chat tries Polygon first when
`POLYGON_API_KEY` is configured, then IBKR, then yfinance.

- The IBKR branch connects to `127.0.0.1` on the port passed to
  `_get_ibkr_options` (default `7496`, client id 42, four-second timeout,
  read-only session), requests **delayed-frozen** market data (type 4, no
  subscription required), takes the nearest expiration or the one requested,
  and reads bid/ask, implied volatility and Greeks for the strikes around the
  spot. From these it reports ATM implied volatility, ATM delta, the ATM
  bid/ask spread, the put/call open-interest ratio, max pain and a gamma
  exposure estimate.
- `data_source` declares what answered: `IBKR_TWS_requested_delayed_frozen`, or
  `IBKR_TWS_market_data_type_unknown` when the data-type request failed;
  `IBKR_TWS_error` with an `error_ibkr` message for a failure after connecting;
  the suffix ` + yfinance_OI` when open interest came from yfinance because IBKR
  returned none.
- Limits, as implemented: a missing library or a failed connection returns
  nothing to the caller and the next source is used, so only `data_source`
  tells you which provider answered. The port of this tool is a function
  parameter with no command-line or `.env` switch. Contracts are requested as
  `SMART`/`USD`, so only US-listed underlyings are supported. Greeks that do not
  arrive within the fixed five-second wait are reported as `null`, not estimated.

## TWS or IB Gateway settings

1. Log in to TWS or IB Gateway with the account you intend to use (paper or
   live; the code only reads data either way).
2. Open **File → Global Configuration → API → Settings**: enable **ActiveX and
   Socket Clients**, keep **Allow connections from localhost only** enabled, and
   enable **Read-Only API**.
3. Note the **socket port**. TWS defaults to `7496` for a live login and `7497`
   for a paper login. Bellomberg defaults to `7496`: for a paper session run the
   price updater with `--ibkr-port 7497`; the options tool keeps its default
   unless the caller passes another port.
4. Market data entitlements are your own. Delayed-frozen data is requested;
   instruments without an entitlement come back empty, and the empty fields are
   reported as such.

## Checking the connection

With TWS open and logged in, from the checkout root (replace `TICKER` with a
US-listed symbol and the port with the one TWS shows):

```powershell
.\.venv\Scripts\python.exe -c "from bellomberg.agents.agent_tools import _get_ibkr_options; import json; print(json.dumps(_get_ibkr_options('TICKER', port=7497), indent=2, default=str)[:1500])"
```

`None` means no connection or no library: TWS or IB Gateway is not running, the
API is not enabled, or the port is wrong. A dictionary whose `data_source` starts
with `IBKR_TWS_` means the session worked; read `error_ibkr` if it is present.

## Troubleshooting

- `None` from the check above: application not running, API socket disabled, or
  paper session (`7497`) with the default port (`7496`).
- `IBKR_TWS_error` with "Scadenza richiesta non disponibile": the requested
  expiration is not listed by IBKR for that symbol.
- All Greeks `null`: the snapshot did not arrive within the five-second wait or
  the entitlement is missing.
- Connection timeout with TWS open: the Windows firewall may block the port;
  allow TWS's `javaw.exe` for the port you use.
- European or non-US symbols are never sent to IBKR by the price updater and are
  not resolvable by the options tool; the other sources apply.
