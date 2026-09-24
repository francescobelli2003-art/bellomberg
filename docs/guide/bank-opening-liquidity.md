# Cassa iniziale e flussi bancari

Il contratto `liquidity_bridge` contiene proiezioni dell'analista, ma il suo saldo
iniziale resta un fatto storico. Il preparatore richiede `facts[legal_bank_id]`
per ogni banca: fonte citata, cifra, unità e data coincidente con l'apertura del
modello. La verifica collega questa prova al valore `opening_cash` del contratto.

Una stima ottenuta sottraendo tutta la cassa della capogruppo dal consolidato non
prova la cassa della banca: i depositi infragruppo sono eliminati nel consolidamento.
Per le fonti FDIC supportate, `CHBAL` identifica cash and due from depository
institutions; `CHBALI` è solo la porzione fruttifera. Il normalizzatore conserva
queste osservazioni quando presenti, senza ricavare saldi mancanti da altre voci.

La prova non certifica la disponibilità a distribuire il saldo. Flussi futuri,
buffer, vincoli patrimoniali e trasferimenti richiedono verifiche distinte.
I dividendi e i contributi espliciti non vanno conteggiati nuovamente nei flussi
di finanziamento prima dei trasferimenti.

L'utile GAAP della capogruppo segue il metodo contabile dichiarato: gli utili
non distribuiti riconosciuti con l'equity method non possono essere esclusi e poi
chiamati GAAP. La riconciliazione degli utili e i dividendi effettivamente incassati
appartengono a prospetti diversi.

Nel ponte cassa parent FR Y-9LP/Y-9SP, un puntatore `value` copiato esattamente
dal puntatore `period` dello stesso fatto normalizzato può essere ricondotto a
`/facts/<indice>/value`. Servono la cifra citata coincidente, stessa unità/data,
entità parent e tutti i componenti previsti con i segni corretti. Nessun altro
percorso o valore viene riparato. Proposta e risposta originali restano immutate;
il rationale del record conserva `SOURCE POINTER NORMALIZED`, fonte, puntatore
ricevuto e verificato. Le fasi successive ricevono la stessa traccia. Senza un
collettore di questa traccia la prova diretta resta rigorosa e rifiuta il refuso.

Le fasi successive ricevono anche i requisiti patrimoniali calcolati dai vincoli
già proposti. Il calcolatore usa la stessa funzione del validatore finale: massimo
fra `exposure * ratio + buffer` e `absolute_floor`, poi massimo fra vincoli.
Percorsi incompleti, duplicati o non finiti fermano la preparazione; non diventano
zeri. Questi risultati sono aritmetica sulle ipotesi, non nuovi fatti o approvazioni.

Completate le previsioni annuali, lo stesso motore del capitale può calcolare i
saldi finali senza inserire un valore terminale fittizio. Questa modalità restituisce
`FORECAST_CALCOLABILE`, nessun fair value e nessun valore dell'equity; rifiuta
input terminali che non consumerebbe. I controlli su capitale, liquidità, utili,
calendario, capacità di finanziamento e perimetro restano attivi.

Il preparatore trasmette alla fase terminale i saldi calcolati, il book finale e i
risultati aritmetici impliciti in ROE, crescita e Ke già proposti. Sono obiettivi
di riconciliazione: non certificano che il continuing sia economicamente sostenibile.
La fase deve ancora documentare utili, flussi, vincoli e trasferimenti sostenibili.
Il preparatore non crea rettifiche regolamentari o nuova cassa per farli coincidere.
Comunica anche i saldi continuing richiesti dalla crescita gia proposta: cassa e
debito parent, capitale statutario e cassa delle banche. Superare un buffer minimo
non basta a dimostrare quella crescita; flussi e fonti di finanziamento devono
riconciliare tutti i saldi, senza rettifiche implicite.

La richiesta terminale esplicita anche il formato annidato: lista delle entita con
`id`, tutti i flussi come percorsi di un solo anno, vincoli e liquidita per entita.
Il requisito del primo anno continuing deve coincidere con il massimo degli importi
`terminal_requirement` gia proposti; ricalcolare esposizioni arrotondate non autorizza
a sostituirlo. Il valore terminale nell'anno di prova resta zero, distinto dal valore
finale della valutazione. I controlli economici mantengono le tolleranze esistenti.

Per riprendere una preparazione, ogni `stage_dossiers` puo conservare il proprio
`excerpt_manifest`: una lista verificata di estratti, oppure `null` per la vista
integrale originale. Se assente, usa il manifest corrente come prima. Questo
permette di abbreviare soltanto le richieste future senza ripagare fasi identiche.
Identita, contenuto e metadati delle fonti storiche devono ancora coincidere;
manifest invalidi fermano il flusso prima del provider. I documenti completi e
le risposte originali non vengono riscritti.

Il contratto bancario puo dichiarare nel `terminal_ledger` la scelta esplicita
`statutory_projection: "retained_flows_at_g"`. In questo caso i flussi che
modificano il capitale statutario proseguono al g dichiarato, insieme ai requisiti;
non si impone che il saldo statutario iniziale cresca esso stesso esattamente al g.
Componenti contabili e regolamentari fisse possono infatti produrre un saldo
diverso senza creare nuovi flussi fiscali o finanziari.

Posti S0 il capitale iniziale, d la variazione del primo anno e R1 il requisito
del primo anno, per g diverso da zero il saldo futuro e
`S(t) = S0 + d * ((1+g)^t - 1) / g`, mentre il requisito e
`R(t) = R1 * (1+g)^(t-1)`. Oltre alla copertura del primo anno, si verifica:

- g positivo: `d * (1+g) >= g * R1`;
- g zero: `d >= 0`;
- g negativo, maggiore di -1: `d >= g * S0`.

Sono condizioni sulla traiettoria perpetua, non una simulazione troncata dopo
pochi anni. Un eccesso iniziale non compensa una futura insufficienza. Restano
vincolanti cassa, trasferimenti, limiti di distribuzione, crescita del common
equity e riconciliazione del valore terminale. Le formule Excel applicano la
stessa verifica. Nessuna rettifica, beneficio fiscale o finanziamento diventa
fondato soltanto perche supera questi controlli aritmetici: ogni flusso perpetuo
richiede una motivazione documentata e non puo perpetuare risorse esauribili.

Se il campo manca, il contratto precedente conserva il controllo sulla crescita
proporzionale del saldo; non viene selezionata implicitamente la nuova ipotesi.
Valori sconosciuti sono rifiutati. L'estensione riguarda il metodo bancario;
i contratti assicurativi conservano le proprie regole.
