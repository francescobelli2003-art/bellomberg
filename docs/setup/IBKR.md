# Setup IBKR API per dati opzioni real-time (FREE)

Questa guida ti permette di sostituire i dati opzioni yfinance (15 min delayed, no Greeks) con dati **real-time + Greeks reali** dal tuo IBKR direttamente. Funziona **gratis** con paper trading account.

## Cosa otterrai

Il tool `get_options_data` del consigliere proverà PRIMA IBKR TWS (real-time + delta/gamma/vega/theta), e farà fallback a yfinance solo se TWS non è raggiungibile.

Output esempio quando IBKR è attivo:

```json
{
  "data_source": "IBKR_TWS_realtime",
  "ticker": "MSTR",
  "spot": 287.45,
  "nearest_expiry": "20260606",
  "atm_iv_call_pct": 78.2,
  "atm_call_delta": 0.512,
  "atm_call_bid_ask_spread_pct": 0.4,
  "put_call_oi_ratio": 0.81,
  "max_pain_strike": 280.0,
  "gamma_exposure_usd_m": 124.5,
  "gex_interpretation": "Positive GEX = dealers long gamma..."
}
```

## STEP 1: Apri account paper trading IBKR (5 min, gratis)

Se hai gia' un account IBKR live, IBKR ti da **automaticamente** anche un paper trading account separato. Login a Client Portal -> Account Management -> Settings -> Paper Trading. Annota username e password (sono DIVERSI da quelli live).

Se non hai IBKR ancora: registrati su `https://www.interactivebrokers.com/it/index.php` (paper-only account si attiva subito, no funding richiesto).

## STEP 2: Installa Trader Workstation (TWS) o IB Gateway

Due opzioni:

| App | Quando usarla | Download |
| --- | --- | --- |
| **TWS** | Vuoi vederla anche tu, comoda per debug | `https://www.interactivebrokers.com/en/trading/tws.php` |
| **IB Gateway** | Solo API, headless, 1/4 della RAM | `https://www.interactivebrokers.com/en/trading/ib-gateway-stable.php` |

Per ora usa TWS (piu' semplice). IB Gateway lo useremo se passiamo a schedulazione automatica.

Login con le **credenziali paper trading** (non live).

## STEP 3: Abilita la API socket in TWS

Una volta dentro TWS:

1. **File -> Global Configuration** (in alto a sinistra)
2. Nella sidebar: **API -> Settings**
3. Spunta queste opzioni:
   - [x] **Enable ActiveX and Socket Clients**
   - [x] **Read-Only API** (importante: il consigliere non puo' fare ordini, sicurezza)
   - [ ] Allow connections from localhost only (UNCHECK se TWS gira su un'altra macchina; LEAVE CHECKED per sicurezza in locale)
   - **Socket port:** `7497` e' il default di TWS per il paper trading, `7496` per il live
4. Master API client ID: lascia 0
5. **OK** + restart TWS

> ⚠️ **Il codice di questo repo si connette a `7496` (LIVE), non a `7497`.** Il default sta in
> `price_updater.py` (`--ibkr-port`, default 7496) e in `agent_tools.py` (`_get_ibkr_options(ticker, port=7496)`).
> Se configuri TWS sul paper e lasci il default, la connessione viene rifiutata: passa
> `--ibkr-port 7497` (v. «Cambiare port» qui sotto). Nota che l'API e' in **Read-Only**, quindi
> anche connettendosi al conto live la libreria puo' solo LEGGERE dati, mai mandare ordini.

## STEP 4: Installa libreria Python

```bash
pip install ib_insync
```

Oppure dalla cartella del progetto:

```bash
pip install -r requirements.txt
```

## STEP 5: Test connessione

Con TWS aperto e loggato, da terminale nella cartella del progetto:

```bash
python -c "from agent_tools import _get_ibkr_options; import json; r = _get_ibkr_options('AAPL'); print(json.dumps(r, indent=2)[:1500])"
```

Output atteso (TWS connesso):
```
{
  "data_source": "IBKR_TWS_realtime",
  "ticker": "AAPL",
  "spot": 192.34,
  ...
}
```

Se TWS non e' aperto, ottieni `None` e il consigliere usera' yfinance automaticamente (fallback trasparente).

## STEP 6: Confermo che funziona col consigliere

Lancia il consigliere normalmente:

```bash
python consigliere.py
```

Quando l'agente chiama `get_options_data`, vedrai nel campo `data_source` se ha usato IBKR (`IBKR_TWS_realtime`) o yfinance (assenza del campo).

## Note importanti

- **TWS deve essere APERTO** quando lancia il consigliere. Se chiudi TWS, il fallback yfinance si attiva automaticamente.
- **Market data subscription**: per AAPL/MSTR/NVDA/etc il paper account ha gia' i dati delay-free di default. Per opzioni con dati real-time pieni (Level 1 OPRA), serve subscription a pagamento (~$2/mese OPRA Top of Book) MA i dati frozen/delayed (sufficienti per il consigliere settimanale) sono **gratis**.
- **Read-Only API attivo**: la libreria NON puo' mandare ordini. Solo lettura. Sicurezza garantita.
- **Conflitto port**: se hai gia' TWS aperto su live (7496) e paper (7497), puoi usare entrambi. La libreria si connette a **7496 (live)** di default: v. la sezione qui sotto per cambiarlo.

## Cambiare port (live vs paper)

Il default del repo e' **7496 (live)** in tutti e due i punti che parlano con TWS:

- `price_updater.py` espone il port come opzione di riga di comando — non serve toccare il codice:
  ```bash
  python price_updater.py --ibkr-port 7497     # paper
  ```
- `agent_tools.py`: la funzione `_get_ibkr_options(ticker, port=7496)` accetta il port come
  parametro. Per usare il paper, passaglielo al punto di chiamata:
  ```python
  ibkr_result = _get_ibkr_options(ticker, port=7497)
  ```

> Per il consigliere settimanale il paper trading e' identico in qualita' dei dati. L'API e' in
> Read-Only in entrambi i casi: nessun ordine puo' partire da qui.

## Troubleshooting

**"Connection refused"** -> TWS non aperto, o API non abilitata. Riapri TWS + verifica API settings.

**"Connection refused" con TWS aperto** -> quasi sempre il port: TWS sul paper (7497) e il codice sul default 7496. V. «Cambiare port».

**"Socket timeout"** -> Firewall Windows blocca il port di TWS (7496 o 7497). Aggiungi eccezione: `Pannello di controllo -> Windows Defender Firewall -> Consenti app -> aggiungi javaw.exe (cartella IBKR)`.

**"No security definition found"** -> Ticker non quotato su IBKR US (es. azioni europee). Fallback a yfinance avviene automaticamente.

**Dati IV/Greeks tutti null** -> TWS non ha completato lo snapshot in 3 secondi (dipende da carico). Aumenta `ib.sleep(3)` a `ib.sleep(5)` nella funzione `_get_ibkr_options` in `agent_tools.py`.

## Prossimo step (futuro)

Una volta che IBKR options funziona stabilmente, possiamo:
- Aggiungere `get_ibkr_positions` per leggere posizioni live IBKR (sostituirebbe la lettura Excel del portafoglio)
- Aggiungere `get_ibkr_options_chain` per chain completa con Greeks su tutte le scadenze
- Schedulare TWS auto-start con autologon (-> IB Gateway con `IBC` per headless)
