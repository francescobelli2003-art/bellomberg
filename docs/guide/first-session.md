# Your first research cycle

[Handbook](README.md) · [Installation](installation.md)

This walkthrough starts with a new, empty installation. Do not use it to recreate
an existing book over a live database; use the [update guide](updates.md) instead.
All examples in the mockups are fictional and should not be entered as real trades.

## 1. Establish the ledger

Open **F16 Trade Entry** and the cash section. Record an opening **DEPOSIT** using
your own amount and date. Confirm the preview and inspect the saved result. Then
record actual transactions with the exact listed ticker, quantity, price and
quote currency. A trade is an accounting entry; Bellomberg does not place it with
your broker.

Reconcile **F17 Movements** with your records. Return to **F1 Command Center** to
check positions and cash. Refresh prices if needed. Do not treat an unavailable
price, FX conversion or cash source as a zero.

Historical CSV import is an advanced alternative, not a shortcut around
reconciliation. The [Trade Entry chapter](pages/16-trade-entry.md) explains its
cash and partial-import limitations.

## 2. Declare the rules for research

Open **F18 → Mandato**. Work through profile, risk, sizing, cash, discipline,
options and notes. The form tells you which fields affect deterministic
calculations and which are instructions to the model.

Choose your own constraints, press **VALIDA + ANTEPRIMA**, inspect the generated
mandate, then **SALVA MANDATO**. The preview validates the supplied values; it does
not certify investment suitability. If fields conflict, resolve the named errors
instead of weakening your preferences just to make the form pass.

A missing/invalid mandate blocks a committee run. Personal preferences are not
inferred from your language, holdings or a model-generated note.

## 3. Explore before paying for a full run

Use **F4 Global Markets** to search an instrument, then add it to **F3 Watchlist**
if you want to follow it without holding it. Inspect sources in **F5 News Desk**.
Use **F7 Factor Lab** and **F8 Monte Carlo** only after checking their sample and
input coverage.

Open **F11 Agent Chat**, select a desk and ask one focused question. A ticker
button sends a desk-specific request immediately. Compare the answer's sources,
assumptions and counterarguments. A tool citation is a trail to inspect, not a
guarantee that the conclusion is correct.

## 4. Run and follow the committee

Start **RUN CONSIGLIERE** from F1, F12 or the command palette and review the
confirmation. A run can make paid model and data requests. If email delivery is
configured, the research workflow can send its output to that configured
recipient; leave email unconfigured if you do not want this.

Open **F12 Agents Live**. Watch phases, heartbeat, tool calls, output status and
recorded cost. A running screen is not proof that every provider succeeded.
Read errors and partial-usage labels. Avoid launching another run merely because
one stage is taking time.

## 5. Review, decide and record

Read **F14 Memo Archive**: main memo, action table, model attachments, warnings
and source tags. An attractive chart is not enough; read the assumptions and
countercase. In **F15 Decisions**, record your response, feedback and any veto.
Marking a proposal's status is not a broker trade or a substitute for updating
the ledger after an actual execution.

Visit **F13 Agent Progress** after a completed run. New score history starts
with captures made by this version; old runs are not assigned synthetic historic
scores. Outcomes need time and valid prices. A newly saved memo may therefore
still have no measurable outcome.

## 6. Keep your own reasoning

In **F18 → Diario**, write a thesis or macro note. Save it, update it as evidence
changes and revisit earlier versions. The journal is private research storage:
it does not silently alter your mandate, position thesis or the next agent
prompt.

At the end of a session, inspect **F19 Settings** and create a database backup
when appropriate. See [what a backup does and does not include](updates.md).
