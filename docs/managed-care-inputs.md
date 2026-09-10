# Managed care: input documentati e risultato comune

Il metodo `managed_care_distributable_equity`, versione `2`, usa i motori
esistenti `managed_care` e `distributable_equity`. Il primo calcola il conto
economico GAAP/adjusted; il secondo riconcilia utili, capitale per entita e
cassa del parent fino ai flussi degli azionisti. Non ci sono formule per
ticker, membership stimata implicitamente, minimi regolamentari universali
o conversioni da utile a dividendo.

## Ingresso applicativo

`get_valuation` e `build_dcf_model` accettano `method_records` e
`analysis_context`. Il normale percorso acquisisce le fonti con i provider
esistenti. L'analista riusa i dati effettivamente letti e presenta record
strutturati, separando osservazioni, guidance e stime. Non viene eseguita
una nuova estrazione automatica dai filing grezzi: un dato non mappato resta
un'acquisizione da completare, senza default numerici.

Il provider Python `method_inputs` puo restituire gli stessi record nel suo
envelope S2. I test sostituiscono soltanto le fonti, non i calcolatori.
In RESEARCH il bundle gia acquisito si riusa senza nuove chiamate provider.

Un nuovo `method_records` e il **set completo aggiornato dell'analista**:
sostituisce il suo precedente set esplicito in una nuova revisione. Il set
precedente resta in `previous_acquisition` e nello snapshot immutabile.
I record di provider indipendenti sono conservati: duplicazioni o conflitti
non si risolvono scegliendo silenziosamente la fonte dell'analista.

Per managed care, `analysis_context` contiene soltanto `scenario_rationale`
con bear/base/bull e `revisions` nel contratto esistente. Le prove numeriche
sono derivate dai record realmente consumati, legate ai rispettivi valori.
Gli argomenti operating come `growth_path` o `terminal_growth` sono rifiutati.

## Record

Ogni record richiede:

```text
field, driver, scenario, value, entity, period, unit, accounting_basis,
source_id, as_of, valid_until, kind, rationale
```

`source_id` e l'URL del documento realmente acquisito; `as_of` e la data
della fonte, distinta dal cutoff informativo del bundle. `valid_until`
deve coprire il cutoff richiesto. `kind` distingue `historical`,
`company_guidance`, `analyst_estimate` e `proxy`: un proxy rimane bloccante.
`source_locator` e opzionale. Il contratto controlla la documentazione
fornita; non autentica una fonte e non certifica approvazioni regolamentari.

I consuntivi richiedono `historical` con fonte non anteriore alla chiusura
osservata. I percorsi futuri non accettano `historical`: i trasferimenti
passati non sono una previsione. Le serie numeriche hanno tanti elementi
quanti gli esercizi espliciti, senza un orizzonte imposto dal prodotto.

### Strutture, tutte con scenario `model`

| driver | field | value richiesto | accounting_basis |
|---|---|---|---|
| `perimeter` | `valuation_perimeter` | `currency`, `consolidated_entity`, `parent_entity`, `subsidiaries=[{id,regime}]`, `share_class`, `share_basis` | `scope` |
| `calendar` | `forecast_periods` | `years`, `actuals_year`, `valuation_date`, `actuals_start`, `actuals_kind`, `day_count`, `fiscal_periods=[{year,start,end,payment_date}]` | `calendar` |
| `quotation` | `quotation_units` | `financial_currency`, `quote_currency`, `quote_unit`, `quote_units_per_currency`, `financial_to_quote_rate`, `shares_per_quote`, `share_class`, `price`, `price_as_of` | `quotation` |

Le strutture hanno `unit=contract`, entita consolidata e periodo `annual`,
tranne `quotation` che usa `opening`. ID entita distinti, senza whitespace
ai bordi; ogni controllata ha un regime dichiarato. Le classi azionarie e
le valute devono riconciliarsi. FX sulla stessa valuta vale uno; conversione
GBX/GBP esplicita; rapporto azioni per titolo quotato distinto dal cambio.
Unita di quotazione diverse non supportate sono un errore dichiarato.

### Driver dei motori

La mappa completa, generata dai campi dei motori, e anche nello schema
runtime dei due tool: l'agente non deve avere accesso a questo file.

| driver | field | unita / base |
|---|---|---|
| `actuals.premium`, `segments.<nome>.premium`, `other_premium` | `premium_revenue` | milioni nella valuta del caso, GAAP |
| `actuals.medical_costs`, `other_medical_costs` | `medical_costs` | milioni, GAAP |
| `segments.<nome>.mcr` | `medical_costs` | `ratio`, GAAP; denominatore premi |
| `actuals.gna` e altri `ANNUAL_INPUTS`, esclusi quelli elencati separatamente | `operating_expenses` | milioni, GAAP; `gna_ratio`/`investment_yield` in `ratio` |
| `actuals.net_income`, `adjustments_after_tax.<nome>` | `accounting_bridge` | milioni, GAAP; rettifiche signed |
| `diluted_shares_m`, `capital.shares_m` | `diluted_shares` | `million shares`, rispettivamente GAAP / valuation |
| `capital.ke` | `discount_rate_inputs` | `ratio`, valuation |
| `capital.discount_periods` | `forecast_periods` | `years`, valuation |
| `capital.subsidiaries.<indice>.<campo>` | `legal_entity_capital` | milioni, statutory; opening_gaap_equity/gaap_net_income in GAAP |
| `capital.subsidiaries.<indice>.liquidity_before_transfers`, `.minimum_liquidity` | `legal_entity_liquidity` | milioni, statutory |
| altri `capital.<campo>` e `capital.parent_cash_flows.<campo>` | `parent_ledger` | milioni, cash; parent_gaap_net_income/consolidation_adjustments in GAAP; policy/basi `text` |

Per le grandezze monetarie la stringa unita e `<currency> million`, per
esempio `USD million` in una fixture denominata in USD. Non sono ammessi
importi in unita semplici interpretati implicitamente come milioni.

Gli `actuals.*` sono `model`; tutti gli altri driver sono distinti per
bear/base/bull. L'entita e il consolidato per economia, tasso, azioni e
sconto; il parent per `parent_ledger`; la controllata indicata in perimeter
per `capital.subsidiaries.*`. Gli indici seguono quell'elenco, non una lista
di societa incorporata nel sorgente.

Sono obbligatori tutti i campi letti dai motori. Una riconciliazione adjusted
nulla va dichiarata con una rettifica esplicita nulla; il mapper non inventa
zeri. La sanity di ogni scenario deve essere disponibile. Un input
estraneo, duplicato o non consumato mantiene il risultato incompleto.

## Calendario e basi temporali

- `years` indica esercizi consecutivi espliciti, identificati dall'anno
  della chiusura. `fiscal_periods` contiene date inclusive senza sovrapposizioni
  o buchi, anche per esercizi non solari.
- `actuals_kind=interim`: il consuntivo inizia col primo FY e termina prima
  della chiusura; il motore sottrae quanto gia realizzato solo dal primo FY.
- `actuals_kind=FY`: il consuntivo concluso precede immediatamente il primo
  esercizio previsto; il primo forecast non subisce una seconda sottrazione.
- `valuation_date` coincide col cutoff del consuntivo e dei saldi iniziali.
  Non esiste rollforward implicito. Il prezzo di confronto ha quella data.
- Convenzione supportata: `ACT/365F`, esplicitamente richiesta. Gli intervalli
  di sconto forniti devono coincidere con giorni effettivi dalla valuation_date
  alla data dei flussi divisi per la base della convenzione.
- `payment_date=end`: questo adapter supporta distribuzioni alla chiusura
  dell'esercizio. Date successive richiedono un diverso ledger e trattamento
  del terminale; vengono rifiutate, senza spostare il valore terminale.

`period` del record deve coincidere con la sua base:

| timing nello schema | stringa period |
|---|---|
| annual | `start/end` dei FY uniti da `|` |
| future | come annual, ma primo start = giorno dopo valuation_date |
| opening | valuation_date |
| terminal | ultimo end |
| actuals | actuals_start/valuation_date |

Opening cash/debt, saldi iniziali, ke e shares usano opening; terminal_equity,
terminal_debt e terminal_basis usano terminal; gli altri percorsi capital
usano future. Nessun saldo di cash/net debt si aggiunge una seconda volta
al fair value equity prodotto dal ledger.

## Output e limiti

Il risultato passa attraverso bundle/gate S2, cache con snapshot e hash,
persistenza immutabile, RESEARCH, comitato, score e F17. Il supporto
`integrated` riguarda l'adapter: dati mancanti, fonti scadute, riconciliazioni
fallite o sanity bloccante mantengono FV n.d., anche nei dettagli.

Il FV e un ricalcolo al cutoff del ledger con le informazioni del bundle:
non e una valutazione storica osservata e non e upside al prezzo corrente.
Cutoff e limite sono riportati in Excel, sidecar, F17, riepilogo comitato e
score. Gli importi del conto economico e del ledger rimangono nella valuta
di bilancio; i fair value principali sono nell'unita della quotazione.

Excel e uno **snapshot numerico** dei motori, con evidenze e scenari: non
duplica le formule di valutazione. Per modificare le ipotesi si rigenera
un nuovo bundle. Ogni generazione ha un nuovo file e sidecar; le precedenti
restano al loro posto. Un caso privo anche delle strutture iniziali conserva
lo snapshot di ricerca senza produrre un workbook fittizio.

Il vecchio contratto puro infrannuale resta leggibile. L'adapter applicativo
richiede sempre il calendario esplicito. L'attivazione del backend e la
migrazione metadata del DB reale sono operazioni separate dal codice locale.
