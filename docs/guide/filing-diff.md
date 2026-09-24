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

La regex `verifica.periodo` riconosce le date esplicite con i gruppi `inizio`
e `fine`. Per relazioni che dichiarano una durata in mesi e la data finale,
puo' invece usare `mesi` e `fine`, per esempio
`(?P<mesi>six)-month period ended (?P<fine>[A-Za-z]+ \d{1,2}, \d{4})`.
Sono ammesse durate di 3, 6, 9 o 12 mesi, anche nelle corrispondenti parole
inglesi, coerenti con il tipo di relazione e con termine all'ultimo giorno
del mese. La data iniziale calcolata e la regola restano nella prova insieme
al testo originale e al suo hash. Non e' un riconoscimento automatico universale:
la regex del profilo deve isolare la dichiarazione del periodo pertinente;
date ambigue, cataloghi discordanti e calendari a settimane richiedono date
esplicite, senza ripiegare sul solo titolo o sulla data del catalogo.

"Verifica periodica" abilita il profilo per il worker. L'intervallo predefinito
e' settimanale; l'aggiornamento manuale e' possibile anche con periodicita'
disattivata. Il worker autonomo si installa come descritto sotto; il backend
con automazione delle valutazioni autorizzata puo' acquisire le fonti anche
tramite il controllo limitato descritto qui.

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

Con il trigger `filing_diff` autorizzato per le valutazioni, il backend esamina
anche un profilo abilitato e scaduto per ciclo, fra portafoglio e watchlist.
Riusa intervalli, blocchi contro run concorrenti e verifiche di Filing Diff.
Questa acquisizione salta esplicitamente giudizio AI e indicizzazione; il worker
autonomo e i comandi manuali conservano le proprie opzioni. Gli esiti verificati
passano al normale controllo degli eventi prima di accodare una valutazione.
Profili assenti/disabilitati e lavori gia' attivi restano visibili nello stato
dell'automazione. Un `6-K` generico non basta: il documento deve soddisfare il
profilo contabile configurato. Comunicati guidance fuori da quelle regole restano
fuori copertura. A backend spento questa scansione non avviene.

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

Il preparatore dei modelli riusa i documenti verificati e riconcilia i riferimenti
SEC sullo stesso URL e sulla stessa data di deposito. Conserva emittente, form e
accession, con provenienza e hash dei byte; i metadati discordanti sono esclusi
dal riuso. Un `6-K` entra nel catalogo finanziario solo se Filing Diff ne ha gia'
verificato il bilancio, l'emittente e il periodo: il solo tipo SEC non basta.

Se i tag SEC non coprono un deposito, il preparatore prova anche le tabelle HTML
dei prospetti consolidati. Il lettore supporta intestazioni inglesi esplicite,
periodi in mesi di calendario e importi in migliaia/milioni di USD, EUR o GBP.
Conserva celle, colonne, periodo, valuta e hash; il compilatore ricostruisce le
osservazioni dal pacchetto acquisito. Non crea tag XBRL. Tabelle duplicate, colonne
ambigue, percentuali e formati non supportati hanno un esito dichiarato. Celle
vuote e trattini non diventano zero; i saldi finali del rendiconto non diventano
flussi del periodo. Restano visibili anche le lacune del catalogo SEC originario.

Quando l'ultima voce di una sezione di stato patrimoniale affianca il subtotale,
il lettore distingue gli importi solo con colonne coerenti e somma esatta di
tutte le voci esplicite della sezione. Conserva anche celle e termini della
riconciliazione. Un dato mancante, una colonna discordante o una somma diversa
lasciano la riga ambigua; il subtotale non sostituisce il valore della voce.

Queste osservazioni possono documentare il ricavo annuale o il ponte annuale +
semestre corrente - semestre precedente, conservando i controlli su emittente,
valuta e intervalli. Le componenti operative riconosciute non attestano da sole
la completezza del capitale circolante. Classe quotata, ipotesi economiche e
modello completo richiedono ancora le rispettive verifiche.

Per una classe ordinaria estera, il preparatore puo' collegare il simbolo locale
alla quotazione del provider quando il bilancio iniziale dichiara una classe
unica e la sede di negoziazione diretta, e l'ultimo annuale dello stesso emittente
definisce le azioni ordinarie e il simbolo. Il lettore supporta le dichiarazioni
inglesi esplicite per il mercato italiano e il suffisso `.MI` documentato da
[Yahoo](https://help.yahoo.com/kb/SLN2310.html). Richiede anche emittente, simbolo,
mercato, tipo e valuta concordanti nella risposta del provider. Conserva frasi,
hash, date e identita' dei documenti, ricontrollati dal compilatore. I metadati
del provider riportano la data di osservazione e non certificano il numero di
azioni storico. ADR, piu' classi, altri mercati e prove discordanti restano
incompleti: nessuna conversione viene dedotta dal solo suffisso del ticker.

Quando valuta finanziaria e valuta di quotazione sono esplicite e diverse, il
preparatore richiede anche il cambio alla data del prezzo iniziale. Per le
coppie con EUR acquisisce il riferimento giornaliero BCE e ne conserva il CSV
originale, la data di acquisizione e la direzione. L'eventuale reciproco e'
calcolato e ricontrollato dal compilatore. Il riferimento BCE non e' un prezzo
di transazione o il cambio all'ora di chiusura dell'azione. Giorni mancanti,
valute non dichiarate, sottounita' e coppie non supportate restano espliciti;
nessun cambio precedente viene riportato in avanti. Anche la coda automatica
trasmette la valuta finanziaria osservata nel profilo al servizio comune.

Per gli emittenti esteri il dossier include anche i recenti rapporti SEC `6-K`
e i loro eventuali allegati `EX-99`, con CIK, accession, data di deposito e byte
verificati. Il rapporto puo' contenere risultati, outlook o altre comunicazioni:
la raccolta non lo classifica automaticamente come guidance numerica. Restano
visibili i limiti di selezione e le fonti escluse. Se un rapporto e' gia' nel
dossier finanziario, viene riusato solo con testo e provenienza concordanti;
le prove originali non vengono sostituite dai metadati della raccolta supplementare.

Per le dichiarazioni inglesi supportate nei rapporti esteri, il preparatore
conserva anche la definizione societaria di circolante operativo come scorte
piu' crediti commerciali meno debiti commerciali e anticipi dei clienti.
Richiede una definizione esplicita non-IFRS, una tabella univoca con date e
valuta, somme esatte e la corrispondenza di ogni componente corrente con il
bilancio stampato. Il compilatore ricontrolla la prova sui documenti originali;
formati diversi o discrepanze restano lacune visibili. La definizione verificata
rimane nel contesto dell'AI anche quando il testo narrativo viene ridotto.
Questa misura societaria non approva il perimetro del DCF: l'analista deve ancora
motivare il trattamento delle altre voci e le proiezioni. I ricavi trimestrali
annualizzati presenti nella tabella non diventano ricavi TTM o previsioni.

Il riferimento statistico beta in USD supporta anche quotazioni in EUR:
conserva i prezzi rettificati del titolo, l'indice S&P 500 e il cambio giornaliero
`EURUSD=X` del provider. Converte il prezzo con il cambio della stessa data e
calcola rendimenti su intervalli comuni, senza riportare valori mancanti in
avanti. L'archivio conserva le osservazioni originali normalizzate e dichiara
lacune, date escluse e ultima data utilizzata. Un'eventuale barra FX vuota alla
data finale esclusa resta archiviata e non entra nel calcolo. Indice dei prezzi,
orari di chiusura diversi e convenzioni delle rettifiche limitano il confronto:
questa regressione non sceglie il beta prospettico, la struttura finanziaria o
il WACC. Altre valute non vengono convertite implicitamente.

Quando il dossier FCFF contiene bilanci stampati normalizzati, il contratto AI
indica anche i loro percorsi JSON per citare i ricavi. La riconciliazione TTM
usa annuale piu' progressivo corrente meno progressivo comparabile precedente;
i periodi e le unita' restano verificati dal compilatore. I fatti stampati non
si mescolano ai concept XBRL nella stessa somma. Queste istruzioni si applicano
solo al dossier interessato, senza modificare le richieste bancarie.

Gli estratti possono dichiarare `layout_projection: collapse_blank_lines_v1`:
la proiezione compatta soltanto sequenze di righe vuote, lasciando intatte le
righe con contenuto, gli originali e i documenti JSON. Offset e hash degli
estratti si riferiscono sempre alla fonte originale; il resoconto distingue
testo escluso, spaziatura rimossa e hash del testo inviato. Le citazioni devono
comparire sia nel singolo frammento originale sia nella sua versione visibile:
una frase ricomposta attraverso spaziatura eliminata non diventa una citazione
letterale valida. L'opzione non certifica la completezza delle note selezionate
e non modifica i limiti del modello o i controlli del budget.

Il preparatore configurato prova `ifrs_note_sections_v1` quando un nuovo contesto
FCFF supera il limite conservativo. La regola riconosce una gerarchia inglese di
note IFRS: non usa ticker, date o offset prefissati. Riduce l'annuale solo quando
restano disponibili le note infrannuali complete dello stesso emittente e un
rapporto gestionale con periodo contabile successivo verificato nell'intestazione;
la data dell'evento non sostituisce quel periodo. Layout ambigui o sconosciuti
restano interi con un motivo dichiarato, e possono ancora superare il limite.
Questa regola non certifica copertura economica e non si applica alle banche.

Il riuso esatto di una risposta gia' registrata, integrale o selezionata, precede
la nuova selezione e la lettura dei prezzi. Manifest espliciti e snapshot di
ripresa conservano la loro selezione. Ogni controllo considera il piano realmente
accumulato: valori e motivazioni delle risposte precedenti non vengono tagliati
per far entrare la richiesta. Se la dimensione resta eccessiva, la preparazione
si ferma prima di prenotare denaro; resta necessario acquisire o selezionare altre
prove. Limiti del modello, prenotazione massima e autorizzazione restano invariati.

La coda applica la stessa preparazione del contesto, controllando autorizzazione
e possesso del lavoro sia prima della selezione sia prima della richiesta AI.
Usa subito la copia delle fonti salvata nel checkpoint: cosi' una ripresa non
cambia l'ordine degli elenchi nel prompt e puo' riusare le fasi gia' pagate.
Un'interruzione durante la selezione conserva quel checkpoint; la ripresa resta
soggetta alla riconciliazione dei costi e alla validita' dell'autorizzazione.
