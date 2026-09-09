/* ════════════════════════════════════════════════════════════
   BANCO DI PROVA DI F5 — UN SOLO GIUDIZIO SULLE RIGHE
   ────────────────────────────────────────────────────────────
   Perché questo modulo esiste (MASTER §9-unquadragies-septdecies,
   changelog (F39)).

   Prima di oggi la stessa riga del banco veniva giudicata in TRE
   posti che non si parlavano:
     · `difettoRiga`        decideva se SIMULA era acceso;
     · `eurKo/pctKo`, nel render, decidevano se il campo era rosso;
     · `buildPayload`       decideva cosa partiva davvero, e
                            filtrava su `m.ticker` senza consultare
                            nessuno dei due.
   Da qui il difetto misurato il 21/08 sul DOM vero: una riga senza
   titolo con importo perfettamente leggibile non mostrava NIENTE
   (campi rossi 0 su 2), la testata la contava fra le attese
   («2 IN ATTESA»), SIMULA restava ACCESO — e la riga veniva scartata
   in silenzio. Il PM spendeva 10.000 traiettorie su un what-if a cui
   mancava una gamba, e il NAV post rispondeva a 2 modifiche su 3.

   La cura NON è un quarto giudizio. È un classificatore solo, e
   `costruisciPayload` si DERIVA da lui: **la classificazione e il payload**
   non possono più divergere per costruzione, invece che per disciplina.

   ⚠️ E si dice così, non «quello che la pagina mostra e quello che parte»,
   che è come l'avevo scritto e che è FALSO: una riga con ticker validato KO
   mostra un errore in rosso e parte lo stesso (vedi il confine qui sotto).
   La garanzia copre la coppia classificazione↔payload, non tutto lo schermo.

   ⚠️ CONFINE DELIBERATO — cosa questo modulo NON giudica.
   Un ticker validato KO resta `entra`. NON perché il frontend non sappia:
   la risposta ce l'ha in `m.validation` e la sta già rendendo a schermo —
   quella prima versione dell'argomento era una premessa falsa a sostegno di
   una conclusione giusta. La ragione vera è che quel verdetto è STANTIO
   (cache 5 minuti lato backend, `_TICKER_TTL_S`) e parziale, mentre il MOTORE
   lo rifà sul serio al momento della corsa, scarta la modifica e ne dichiara
   la ragione (`portfolio_montecarlo.py`, `skipped_modifications`), già resa
   in pagina nel riquadro «MODIFICHE SCARTATE DAL MOTORE». Duplicare qui quel
   verdetto vorrebbe dire rimettere in piedi due giudici che possono
   dissentire, cioè il bug che questo modulo esiste per chiudere.
   Decisione PM 21/08.

   ⚠️ CONSEGUENZA, e va detta perché è il prezzo di quel confine: il conteggio
   in testata NON può promettere quante modifiche verranno simulate, solo
   quante ne SPEDIAMO. Vedi `targhettaBanco`.

   ⚠️ IL CONFINE PASSA DOVE PASSA L'INFORMAZIONE, non dove è comodo. `trim`
   con lo slider a 0 è conoscibile QUI per intero — zero è zero — e infatti
   dal 21/08 è `inerte`. Resta al motore solo ciò che il motore sa e noi no:
   se un titolo si prezza, e se un `remove`/`trim` colpisce una posizione che
   in portafoglio non c'è.
   ════════════════════════════════════════════════════════════ */
import { leggiNumero } from '@/lib/cassa';
import type { PortfolioModification } from '@/lib/api';

/** Lo stato di una riga del banco. Tre valori, mutuamente esclusivi:
 *  · `entra`  — la riga fa parte del what-if e viaggia nel payload;
 *  · `inerte` — la riga NON entra e non è un errore: è un abbozzo.
 *               Oggi l'unico caso è «senza titolo», ed è l'unico
 *               scarto che il frontend può conoscere con certezza,
 *               perché è lo stesso predicato che filtra il payload;
 *  · `blocca` — la riga è illeggibile e SPEGNE SIMULA: farla partire
 *               vorrebbe dire simulare su un numero falso. */
export type StatoRiga = 'entra' | 'inerte' | 'blocca';

export interface GiudizioRiga {
  stato: StatoRiga;
  /** Perché, in italiano, da mostrare accanto alla riga. Assente su `entra`. */
  motivo?: string;
}

/** La forma minima che serve per giudicare una riga. `DraftMod` della
 *  pagina la soddisfa per struttura: il modulo non ha bisogno di sapere
 *  di `id`, `validation` o `validating`, e non deve poterli guardare. */
export interface RigaBanco {
  action: 'add' | 'remove' | 'trim';
  ticker: string;
  amount_eur: string;
  amount_pct: string;
}

/** Quello che il giudizio non può dedurre dalla riga da sola. */
export interface ContestoBanco {
  /** `GET /portfolio` non ha consegnato titoli: il picker di TOGLI/RIDUCI ha
   *  una sola voce, vuota. Senza questo, ogni riga TOGLI/RIDUCI risulterebbe
   *  «senza titolo» e la pagina addosserebbe all'utente una mancanza che è di
   *  un endpoint — reperto della review, e ha ragione. */
  bookVuoto?: boolean;
}

/** IL giudizio. Nessun altro posto deve deciderlo daccapo. */
export function classificaRiga(m: RigaBanco, ctx: ContestoBanco = {}): GiudizioRiga {
  // Stesso predicato che filtra il payload, e per questo è affidabile:
  // `costruisciPayload` non «ricorda» di escluderla, la esclude perché
  // è questa funzione a dirlo.
  if (!m.ticker.trim()) {
    if (ctx.bookVuoto && m.action !== 'add') {
      return { stato: 'inerte', motivo: 'book non caricato: nessun titolo da scegliere' };
    }
    // La riga non entra PERCHÉ manca il titolo — ma se anche l'importo è
    // illeggibile va detto subito, non quando il titolo arriverà: è la classe
    // ×10/×100 che questa pagina esiste per fermare, e tacerla è un fallback
    // silenzioso (regola 14/07). Reperto della review.
    const lE = leggiNumero(m.amount_eur);
    const lP = leggiNumero(m.amount_pct);
    const numeroKo = (lE && !lE.ok) || (lP && !lP.ok);
    return {
      stato: 'inerte',
      motivo: numeroKo
        ? 'senza titolo non entra nel calcolo · e l’importo scritto non si legge'
        : 'senza titolo non entra nel calcolo',
    };
  }
  // Lo slider ha dominio 0-100 per costruzione: non può essere illeggibile.
  // Ma a ZERO la riga non modifica niente — «una modifica che non modifica» —
  // e questo il frontend lo sa PER INTERO, a differenza di «questo titolo si
  // prezza?». Il motore la scarterebbe comunque (`TRIM requires positive
  // amount_pct`), quindi dichiararla inerte non sposta di una cifra la
  // simulazione: la dichiara solo PRIMA che il PM spenda 10.000 traiettorie
  // credendo di aver chiesto tre modifiche. Decisione PM 21/08.
  if (m.action === 'trim') {
    const pct = parseFloat(m.amount_pct);
    if (!isFinite(pct) || pct <= 0) {
      return { stato: 'inerte', motivo: 'la riduzione è a zero: non cambia niente' };
    }
    return { stato: 'entra' };
  }

  const lE = leggiNumero(m.amount_eur);
  if (lE && !lE.ok) return { stato: 'blocca', motivo: `EUR: ${lE.motivo}` };
  if (m.action === 'add') {
    return lE ? { stato: 'entra' } : { stato: 'blocca', motivo: 'importo mancante' };
  }
  const lP = leggiNumero(m.amount_pct);
  if (lP && !lP.ok) return { stato: 'blocca', motivo: `%: ${lP.motivo}` };
  if (lP && lP.ok && lP.valore > 100) return { stato: 'blocca', motivo: '%: oltre il 100' };
  return lE || lP
    ? { stato: 'entra' }
    : { stato: 'blocca', motivo: 'serve un importo in EUR oppure una %' };
}

export interface ContoBanco {
  totale: number;
  entrano: number;
  inerti: number;
  bloccanti: number;
  /** Le righe che spengono SIMULA, col loro motivo: serve al tooltip del
   *  tasto, che le elenca per nome. */
  difetti: { riga: RigaBanco; motivo: string }[];
  /** Le righe messe da parte, col loro motivo. */
  inerziali: { riga: RigaBanco; motivo: string }[];
}

/** Un solo passaggio sulle righe: la testata, il tasto e il colore leggono
 *  tutti da qui, quindi non possono raccontare tre storie diverse. */
export function contaBanco(mods: RigaBanco[], ctx: ContestoBanco = {}): ContoBanco {
  const c: ContoBanco = {
    totale: mods.length, entrano: 0, inerti: 0, bloccanti: 0,
    difetti: [], inerziali: [],
  };
  for (const m of mods) {
    const g = classificaRiga(m, ctx);
    if (g.stato === 'entra') c.entrano++;
    else if (g.stato === 'inerte') {
      c.inerti++;
      c.inerziali.push({ riga: m, motivo: g.motivo || '' });
    } else {
      c.bloccanti++;
      c.difetti.push({ riga: m, motivo: g.motivo || '' });
    }
  }
  return c;
}

/** Il payload, DERIVATO dal giudizio: entrano solo le righe `entra`.
 *
 *  ⚠️ Prima filtrava su `m.ticker` per conto suo. Il risultato è identico
 *  in ogni stato raggiungibile — `runSim` esce prima se c'è anche una sola
 *  riga bloccante, e il tasto è spento nello stesso caso, quindi questa
 *  funzione non viene MAI chiamata con righe `blocca` in lista — ma ora
 *  l'identità è una conseguenza del codice invece di una coincidenza da
 *  ricordare. */
export function costruisciPayload(
  mods: RigaBanco[], ctx: ContestoBanco = {},
): PortfolioModification[] {
  const out: PortfolioModification[] = [];
  for (const m of mods) {
    // ⚠ il contesto va passato QUI come lo passa `contaBanco`. Oggi nessun
    // flag del contesto cambia lo `stato` (solo il motivo), quindi ometterlo
    // non produrrebbe scarti — ma sarebbero di nuovo due chiamate allo stesso
    // giudice con informazioni diverse, cioè la forma esatta del bug che
    // questo modulo chiude. Un domani basta un flag che sposti uno stato.
    if (classificaRiga(m, ctx).stato !== 'entra') continue;
    const p: PortfolioModification = {
      action: m.action,
      ticker: m.ticker.trim().toUpperCase(),
    };
    if (m.action === 'add') {
      const l = leggiNumero(m.amount_eur);
      if (l && l.ok) p.amount_eur = l.valore;
    } else if (m.action === 'remove') {
      const lE = leggiNumero(m.amount_eur);
      const lP = leggiNumero(m.amount_pct);
      if (lE && lE.ok) p.amount_eur = lE.valore;
      else if (lP && lP.ok) p.amount_pct = lP.valore;
    } else {
      p.amount_pct = parseFloat(m.amount_pct) || 0;   // slider nativo, mai testo libero
    }
    out.push(p);
  }
  return out;
}

/** La targhetta della testata. Una frase sola, e ogni pezzo è un numero
 *  che qualcuno ha contato — mai un aggettivo.
 *
 *  ⚠️ «SPEDITE AL MOTORE» e non «NEL CALCOLO», e la differenza non è di stile.
 *  Tutte e tre le lenti della review hanno colpito lo stesso punto: «N NEL
 *  CALCOLO» afferma che N modifiche verranno simulate, e questo modulo non è
 *  in grado di prometterlo. Il motore ne scarta altre che da qui non si
 *  vedono — ticker in SKIP list, `remove`/`trim` su un titolo non in
 *  portafoglio, ticker che yfinance non prezza — e lo dichiara solo dopo la
 *  corsa, nel riquadro «MODIFICHE SCARTATE DAL MOTORE». Quante ne SPEDIAMO,
 *  invece, lo sappiamo per costruzione: è `costruisciPayload().length`.
 *  Il numero ora dice esattamente quello, e non un grammo di più.
 *
 *  ⚠️ E quando non se ne spedisce nessuna la frase lo DEVE dire per intero:
 *  con `entrano === 0` il payload è vuoto, `runSim` va sull'endpoint senza
 *  modifiche e si simula il book nudo — i riquadri NAV pre/post e MODIFICHE
 *  SCARTATE non compaiono nemmeno. Dire solo «0 SPEDITE» lascerebbe indovinare
 *  cosa si sta guardando: fallback silenzioso, regola 14/07. La frase giusta
 *  esisteva già dieci righe sopra, per il banco vuoto.
 *
 *  Singolare e plurale si accordano: «1 ILLEGGIBILI» diceva il falso sul
 *  numero, ed era in pagina da prima. */
export function targhettaBanco(c: ContoBanco): string {
  if (c.totale === 0) return 'NESSUNA — SI SIMULA IL BOOK COM’È';
  const p: string[] = [`${c.totale} IN ATTESA`];
  if (c.bloccanti > 0) {
    // NON «ILLEGGIBILI»: su nove motivi di blocco solo quattro sono
    // illeggibilità. «importo mancante» è un campo BIANCO, «deve essere
    // maggiore di zero» e «oltre il 100» sono numeri perfettamente leggibili
    // e fuori dominio. La parola vecchia mandava a cercare un refuso dove non
    // c'era niente da leggere (reperto della review; il termine era in pagina
    // da prima, ma questo lotto lo ereditava).
    p.push(`${c.bloccanti} DA CORREGGERE — SIMULA SPENTO`);
  }
  if (c.inerti > 0) {
    p.push(c.inerti === 1 ? '1 INERTE, NON ENTRA' : `${c.inerti} INERTI, NON ENTRANO`);
  }
  if (c.bloccanti > 0 || c.inerti > 0) {
    p.push(c.entrano === 0
      ? '0 SPEDITE — SI SIMULA IL BOOK COM’È'
      : `${c.entrano} SPEDIT${c.entrano === 1 ? 'A' : 'E'} AL MOTORE`);
  }
  return p.join(' · ');
}

/** L'etichetta del tasto. Dichiara quante righe manda DAVVERO, ma solo
 *  quando il numero dice qualcosa: a banco pieno e sano «SIMULA · 3 DI 3» è
 *  rumore, e a tasto spento il conteggio lo porta già la testata.
 *  A zero righe spedite il tasto dice cosa fa, perché quello che fa non è
 *  quello che il banco lascia credere: simula il book com'è. */
export function etichettaSimula(c: ContoBanco, spento: boolean): string {
  if (spento || c.totale === 0 || c.entrano === c.totale) return 'SIMULA';
  if (c.entrano === 0) return 'SIMULA IL BOOK COM’È';
  return `SIMULA · ${c.entrano} DI ${c.totale}`;
}
