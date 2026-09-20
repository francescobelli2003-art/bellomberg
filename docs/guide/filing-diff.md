# Filing Diff

Filing Diff confronta sezioni di due relazioni comparabili e conserva le prove.
In Fundamentals e Market, aprire il pannello del titolo per leggere storico,
copertura delle fonti, freschezza, estratti e citazioni. Il confronto corrente
e quello storico sono distinti: un documento recente non verificato impedisce
di presentare una coppia precedente come aggiornata.

## Configurazione

Ogni titolo richiede un profilo documentale curato: identita' dell'emittente,
lingua, periodo, perimetro, fonti e regole per riconoscere le sezioni. Il pannello
avanzato permette di salvare il JSON del profilo; ogni salvataggio crea una
revisione. Il riconoscimento non e' universale. SEC, ESEF e fonti IR hanno
coperture diverse; ZIP, scansioni senza testo e rettifiche non gestite restano
visibilmente esclusi.

"Verifica periodica" abilita il profilo per il worker. L'intervallo predefinito
e' settimanale; l'aggiornamento manuale e' possibile anche con periodicita'
disattivata. Il worker va installato separatamente come descritto sotto.

Il giudizio AI e' un'opzione separata che utilizza l'API e puo' generare costi.
Riusa il modello configurato per `ACTION_EXTRACTOR_MODEL`. Le citazioni sono
controllate e l'input limitato e' dichiarato; un confronto identico gia' valutato
viene riutilizzato. Il giudizio non aggiorna automaticamente fair value,
ipotesi approvate o ordini. Gli estratti delle fonti restano nella lingua originale.

## Archivio e automazione

Un database nuovo include gia' le tabelle. Per un database preesistente, dalla
radice del progetto:

```powershell
python tools/migrations/migra_filing.py --dry-run
# Chiudere il backend prima del comando successivo.
python tools/migrations/migra_filing.py --apply
```

La migrazione crea un backup e verifica che dati e schema preesistenti siano
rimasti invariati. SQLite e' l'archivio autorevole; Chroma e' un indice derivato.
Un errore di indicizzazione rimane visibile senza eliminare il confronto.

Il worker portabile legge solo i profili abilitati e scaduti:

```powershell
python -m bellomberg.market_data.filing_worker --status
python -m bellomberg.market_data.filing_worker --due
```

Su Windows, il programma seguente mostra prima la configurazione; `-Apply`
registra solo `Bellomberg-FilingDiff`, senza modificare gli altri task:

```powershell
powershell -NoProfile -File tools/ops/windows/install_filing_scheduler.ps1
powershell -NoProfile -File tools/ops/windows/install_filing_scheduler.ps1 -Apply
```

Il controllo avviene ogni giorno alle 08:10, quando l'utente e' connesso, e
all'occasione successiva se il computer era spento. Le scadenze dei singoli
profili restano nel database. Su altri sistemi, pianificare lo stesso comando
`--due` con il gestore di sistema. Gli esiti sono in `filing_worker.jsonl`
nella directory dati; lo stato diverso da zero richiede attenzione.

Se un processo termina dopo aver accodato un lavoro, quel lavoro resta visibile
come attivo. Dopo aver verificato che non esista piu' un worker che lo esegue,
concluderlo esplicitamente prima di riprovare:

```powershell
python -m bellomberg.market_data.filing_worker --recover-run ID --reason "processo interrotto verificato"
```

Gli agenti consultano `get_filing_changes` senza avviare acquisizioni o chiamate
AI. Anche il comitato riceve il contesto disponibile, con limiti e dati mancanti
dichiarati. Nessuna assenza di risultato viene sostituita da un'analisi inventata.
