/* ════════════════════════════════════════════════════════════
   LA CURVA DI F1 — UN SOLO GIUDIZIO SU COSA SI DISEGNA
   ────────────────────────────────────────────────────────────
   Perché questo modulo esiste (MASTER §9-unquadragies-novodecies,
   changelog (F41)). Il (F40) di ieri ha fatto del valore quota il
   numero grande di F1 e ha tolto DUE frasi false dall'intestazione
   della curva sotto. Restava la domanda vera, che ieri era in coda
   come «decisione PM sui dati»: quella curva CHE COSA DISEGNA?

   ⚠️ LA MISURA HA CAMBIATO LA DOMANDA. Non era «come chiamare la
   linea», era «quale linea». Misurato il 21/08 sul payload vivo di
   `GET /portfolio/analytics/twr` e sul DB (`nav_snapshots`, 58
   righe, sola lettura):

     · La curva disegnava `nav_total_eur` di `/analytics/nav_history`,
       cioè il valore di mercato del book PIÙ LA CASSA DI OGGI,
       tenuta costante su OGNI giorno passato
       (`portfolio_analytics.py:585`, una sola list-comprehension
       senza indice temporale; il docstring :430-431 la chiama «cash
       costante» e marca la serie «info only»).
     · La cassa vera NON è costante: negli snapshot fra l'11/06 e il
       30/07 scende a meno di un quarto, e al 13/08 è quasi raddoppiata
       (cifre tolte il 02/09 per la pubblicazione, i rapporti restano).
       L'errore che ne esce CAMBIA SEGNO — misurato il 21/08 alle 21:52
       confrontando la serie DISEGNATA (`nav_history.nav_total_eur`) con
       `nav_snapshots.nav_total_eur`: l'11/06 la curva sta SOTTO il vero
       di più di un quinto, il 30/07 sta SOPRA di circa l'8%. Non è un
       livello sbagliato, è la FORMA.
       ⚠️ La prima stesura di questo docstring portava due scarti un po'
       diversi: erano calcolati su `invested_eur`, cioè sul solo
       capitale investito, NON sulla linea che la curva disegnava. Un
       numero prodotto da un mio strumento va verificato come un dato.
     · Il piedino ne ricavava un rendimento a TRE cifre (misurato il
       21/08: il valore del 02/02 quasi triplicato; cifre tolte il 02/09
       per la pubblicazione, i rapporti restano), perché la serie parte
       dal book in costruzione PIÙ la cassa di oggi incollata: quasi
       tutto capitale che ENTRA, non rendimento. Sulla stessa finestra
       il rendimento vero è circa +11% (quota misurata alle 21:56 del
       21/08 — e si muove, v. sotto).
     · Il caso lampante è il 12/08, giorno del bonifico: i soldi salgono
       di due cifre percentuali e la quota di pochi centesimi. La curva
       raccontava la storia sbagliata proprio sotto il titolo che il
       (F40) aveva appena raddrizzato.

   ⚠️ E IL PATRIMONIO VERO NON ESISTE SU TUTTA LA FINESTRA. Gli
   snapshot NAV partono dall'11/06: 57 giorni su 150. Prima c'è solo
   la ricostruzione da chiusure, che l'11/06 dice circa il 62% del
   vero — manca la cassa, che allora era il 38% del patrimonio.
   Quindi QUALUNQUE linea in euro su 150 giorni è
   falsa da qualche parte. La quota è l'unica serie vera dal primo
   all'ultimo punto, e lo è perché non pretende di essere soldi.

   COSA FA QUESTO MODULO: tiene DUE viste sulla stessa risposta —
   `quota` (l'indice TWR, tutta la finestra) e `patrimonio` (gli euro
   VERI degli snapshot, solo dove esistono) — e per ciascuna produce
   già scritte tutte le stringhe che vanno a schermo.

   ⚠️ UN SOLO POSTO CHE AFFETTA E UN SOLO POSTO CHE SCRIVE (lezione
   del 21/08, pagata). Ieri `cimaF1` ritornava una cifra formattata
   che la pagina NON usava: riformattava per conto suo, e tre casi
   della batteria misuravano un campo morto. Qui `leggiCurva` taglia
   la finestra UNA volta e consegna già allineati punti, euro, regimi
   e versamenti; `letturaCurva` non ha bisogno del payload e non
   cerca niente per stringa. La prima stesura lo faceva — cercava il
   giorno con `dates.indexOf(iso)` — ed era una seconda fonte di
   verità sullo stesso taglio.

   ⚠️ NIENTE RIPIEGHI MUTI (regola PM 14/07). Se la vista chiesta non
   è disponibile — `patrimonio` senza nemmeno un giorno ufficiale —
   NON si mostra l'altra facendo finta di niente: si dichiara.

   ⚠️ CONFINE — QUELLO CHE QUESTO MODULO NON PUÒ CURARE, e che va
   detto a schermo invece che nascosto: l'indice TWR incolla due
   grandezze. Nei primi 93 punti il denominatore del rendimento è il
   solo capitale investito (la cassa dell'epoca era fuori perimetro),
   negli ultimi 57 è il NAV totale. È documentato dal motore
   (`twr_engine.py:25-38`) e non è un errore di calcolo — ma vuol dire
   che i due tratti non sono omogenei, ed è per questo che il confine
   si DISEGNA invece di stare in una nota a piè di pagina.

   ⚠️ CONFINE — `official` NON vuol dire «chiusura di giornata».
   Lo snapshot del giorno corrente è riscritto in UPSERT a ogni giro
   del price_updater, ogni 15 minuti (misurato il 21/08: 81 scritture,
   escursione dell'1,37% del patrimonio, sopra la tolleranza di riconciliazione
   dell'1,0%). È la stessa trappola per cui il timbro della cima dice
   «IN CORSO» sull'ultimo giorno: qui l'ultimo punto della curva è
   vivo allo stesso modo.
   ════════════════════════════════════════════════════════════ */

import type { TwrPayload } from './api';
import { fmtEUR, fmtNum } from '@/lib/format';
import { dataIt } from './quota';

export type VistaCurva = 'quota' | 'patrimonio';
export type Regime = 'reconstructed' | 'official';

/** L'altra vista, per l'interruttore: spenta CON IL MOTIVO, mai spenta e basta.
 *  ⚠️ NIENTE `etichetta` qui: la pagina disegna SEMPRE tutte e due le parole, non
 *  solo l'altra, quindi un'etichetta valida per una sola delle due sarebbe un campo
 *  che nessuno legge — la trappola del (F40), che questo modulo esiste per non
 *  rifare. Le parole le da' `etichettaVista`. */
export interface AltraVista {
  vista: VistaCurva;
  motivoSpento: string | null;
}

/** La curva si può disegnare: tutte le stringhe di pagina sono già qui dentro. */
export interface CurvaViva {
  stato: 'viva';
  vista: VistaCurva;
  /** le date della vista (per `patrimonio` sono solo quelle ufficiali) */
  date: string[];
  /** la serie DISEGNATA, nell'unità della vista */
  valori: number[];
  /** gli euro dello stesso giorno, allineati punto a punto con `valori` */
  euro: number[];
  /** il regime punto per punto, allineato a `valori` */
  regimi: Regime[];
  /** i versamenti VERI (dal ledger) che cadono dentro la finestra: iso → importo firmato.
   *  ⚠️ SOLO i tipi riconosciuti: un tipo nuovo non entra qui di nascosto. */
  versamenti: Record<string, number>;
  /** i movimenti con un `type` che non sappiamo leggere: iso → il tipo, verbatim */
  ignoti: Record<string, string>;
  /** indice del primo punto ufficiale DENTRO la vista; null se non c'è confine da disegnare */
  confine: number | null;
  /** la data del PRIMO PUNTO disegnato come ufficiale: è quella che etichetta il tratteggio */
  confineData: string | null;
  /** `official_since` dichiarato dal backend. ⚠️ NON è sempre `confineData`: lo
   *  snapshot d0 esiste (11/06) ma quel giorno resta il punto della
   *  ricostruzione, e il primo punto a regime `official` è il successivo. */
  officialSince: string | null;
  /** i conteggi DELLA VISTA (in `patrimonio` sono i punti disegnati, non la finestra) */
  nRicostruiti: number;
  nUfficiali: number;
  /** quanti punti della finestra questa vista NON disegna (0 in `quota`) */
  esclusi: number;
  nota: string;
  /** i due estremi dell'asse, gia' scritti: la pagina non formatta date */
  estremoDa: string;
  estremoA: string;
  /** la frase centrale del piedino, già scritta */
  piede: string;
  /** true = verde, false = rosso, null = nessun colore (una cifra in euro non è un voto) */
  piedeVerde: boolean | null;
  altra: AltraVista;
}

export interface CurvaAttesa { stato: 'attesa'; frase: string }
export interface CurvaAssente { stato: 'assente'; frase: string; motivo: string }
export type EsitoCurva = CurvaViva | CurvaAttesa | CurvaAssente;

/** Il nome del riquadro. Sta qui e non in pagina perché serve ANCHE quando la
 *  serie non c'è: in attesa e in errore il riquadro ha comunque un'intestazione,
 *  e due posti che la scrivono sono due posti che possono divergere.
 *  ⚠️ È una FUNZIONE e non un campo dell'esito, apposta: un campo `titolo` dentro
 *  `CurvaViva` la pagina non potrebbe usarlo negli altri tre stati, quindi
 *  resterebbe morto mentre la batteria lo certifica. */
export function titoloVista(v: VistaCurva): string {
  return v === 'quota' ? 'VALORE QUOTA // DAILY' : 'PATRIMONIO // DAILY';
}

/** La parola dell'interruttore. Stessa ragione: le disegna la pagina in ogni stato. */
export function etichettaVista(v: VistaCurva): string {
  return v === 'quota' ? 'QUOTA' : 'PATRIMONIO';
}

/* ⚠️ QUESTI TRE NON FORMATTANO NIENTE DA SOLI: delegano a `lib/format.ts`, che
   è il posto del progetto che sa come si scrive un numero, e ricopiano la
   regola del segno della cima (`quota.ts:315` e `:328`) invece di inventarne
   una seconda. La prima stesura di questo modulo aveva tre formattatori suoi,
   con `toLocaleString` a mano: due modi di scrivere gli stessi euro a dieci
   centimetri di distanza nella stessa schermata. */

/** Gli euro come li scrive la cima di F1: senza decimali. */
export function eur0(v: number): string {
  return fmtEUR(v, false, 0);
}

/** La percentuale con la regola della cima: segno tipografico, virgola, due decimali. */
export function pct2(v: number): string {
  const segno = v > 0 ? '+' : v < 0 ? '−' : '';
  return `${segno}${fmtNum(Math.abs(v), 2)}%`;
}

/** Il valore quota come lo scrive la cima. */
export function quota2(v: number): string {
  return fmtNum(v, 2);
}

/**
 * L'UNICO giudizio su cosa si disegna.
 *
 * ⚠️ `inCorso` per primo: senza, F1 dichiarerebbe «assente» per tutti i secondi
 * in cui la chiamata è in volo — è lo stesso quarto stato che il (F40) ha
 * dovuto aggiungere alla cima dopo averlo scoperto sul campo.
 */
export function leggiCurva(
  twr: TwrPayload | null,
  vista: VistaCurva,
  inCorso: boolean,
  motivoErrore: string | null,
): EsitoCurva {
  if (inCorso) return { stato: 'attesa', frase: 'lettura della serie…' };
  if (motivoErrore) return { stato: 'assente', frase: 'serie non disponibile', motivo: motivoErrore };
  if (!twr || twr.error) {
    return {
      stato: 'assente',
      frase: 'serie non disponibile',
      motivo: twr?.error || 'il motore contabile non ha risposto',
    };
  }

  const date = Array.isArray(twr.dates) ? twr.dates : [];
  const idx = Array.isArray(twr.twr_index) ? twr.twr_index : [];
  const val = Array.isArray(twr.values_eur) ? twr.values_eur : [];
  const reg = (Array.isArray(twr.regimes) ? twr.regimes : []) as Regime[];

  // ⚠️ LISTE DISALLINEATE = SI DICHIARA, NON SI TAGLIA. La prima stesura prendeva
  // la più corta e disegnava: un punto spariva senza che nessuno lo dicesse
  // (ripiego muto, regola PM 14/07) — e per giunta `leggiQuota` sullo STESSO
  // payload rifiuta («serie incoerente»), quindi la cima avrebbe scritto «quota
  // non disponibile» mentre il riquadro sotto tracciava una quota.
  const lung = [date.length, idx.length, val.length, reg.length];
  const n = Math.min(...lung);
  if (n !== Math.max(...lung)) {
    return {
      stato: 'assente',
      frase: 'serie incoerente',
      motivo: `il motore ha mandato liste di lunghezza diversa: ${date.length} date, `
        + `${idx.length} valori quota, ${val.length} valori in euro, ${reg.length} regimi`,
    };
  }
  if (n < 2) {
    return {
      stato: 'assente',
      frase: 'serie troppo corta',
      motivo: `servono almeno 2 punti allineati, ne sono arrivati ${n}`,
    };
  }

  // ⚠️ `Number(null)` fa ZERO, non NaN: senza questa guardia un `null` dentro
  // le serie diventerebbe un punto a quota 0 o un patrimonio di 0 € disegnato
  // come se fosse una misura. Un buco nel dato deve restare un buco.
  const num = (x: unknown): number => (x === null || x === undefined || x === '' ? NaN : Number(x));
  const primoUff = reg.slice(0, n).indexOf('official');
  const nUfficiali = primoUff < 0 ? 0 : n - primoUff;
  const nRicostruiti = n - nUfficiali;
  const confineData = primoUff > 0 ? date[primoUff] : null;
  const officialSince = twr.regime_summary?.official_since || null;

  // la vista `patrimonio` esiste solo dove esistono gli snapshot, e serve più
  // di un punto per fare una linea
  const patrimonioSpento = nUfficiali >= 2 ? null
    : nUfficiali === 0
      ? 'nessuno snapshot NAV: il patrimonio vero non esiste per nessun giorno'
      : 'un solo snapshot NAV: serve più di un punto per disegnare una linea';

  const taglia = (da: number) => {
    const d = date.slice(da, n);
    const versamenti: Record<string, number> = {};
    const ignoti: Record<string, string> = {};
    for (const f of twr.external_flows || []) {
      // ⚠️ finestra SEMIAPERTA a sinistra (`<= d[0]` esclude), come fa il motore
      // (`twr_engine.py:243`): il valore del PRIMO punto contiene già l'effetto di
      // un versamento di quel giorno, quindi contarlo di nuovo lo direbbe due volte.
      if (!f || !f.date || f.date <= d[0] || f.date > d[d.length - 1]) continue;
      // ⚠️ NIENTE «tutto ciò che non è DEPOSIT è un prelievo»: un `type` nuovo del
      // backend (FEE, TRANSFER…) sarebbe finito nel netto col segno sbagliato, in
      // silenzio. I tipi che non sappiamo leggere si contano a parte e si dicono.
      if (f.type !== 'DEPOSIT' && f.type !== 'WITHDRAWAL') {
        ignoti[f.date] = String(f.type || 'senza tipo');
        continue;
      }
      const importo = (f.type === 'DEPOSIT' ? 1 : -1) * Number(f.amount_eur || 0);
      versamenti[f.date] = (versamenti[f.date] || 0) + importo;
    }
    return {
      date: d,
      euro: val.slice(da, n).map(num),
      regimi: reg.slice(da, n),
      versamenti,
      ignoti,
    };
  };

  if (vista === 'patrimonio') {
    if (patrimonioSpento) {
      return { stato: 'assente', frase: 'patrimonio non disponibile', motivo: patrimonioSpento };
    }
    const t = taglia(primoUff);
    // ⚠️ `nUfficiali` è POSIZIONALE (n − primoUff): presuppone che i regimi siano
    // in DUE blocchi contigui, che è come li costruisce il motore oggi (misurato:
    // 93 + 57). Se un giorno si interlacciassero, questa vista disegnerebbe un
    // punto ricostruito sotto un'intestazione che dice «snapshot NAV». Non lo
    // indovino: lo conto e lo dichiaro.
    const intrusi = t.regimi.filter(r => r !== 'official').length;
    if (intrusi) {
      return {
        stato: 'assente',
        frase: 'perimetro non contiguo',
        motivo: `${intrusi} ${intrusi === 1 ? 'punto' : 'punti'} del tratto non ${intrusi === 1 ? 'è' : 'sono'} `
          + 'a regime ufficiale: il patrimonio vero non è disegnabile su un perimetro che si alterna',
      };
    }
    const bucati = t.euro.filter(x => !Number.isFinite(x)).length;
    if (bucati) {
      return {
        stato: 'assente',
        frase: 'patrimonio non disegnabile',
        motivo: `${bucati} ${bucati === 1 ? 'punto' : 'punti'} su ${t.euro.length} non ${bucati === 1 ? 'e’ un numero' : 'sono numeri'}: la linea del patrimonio avrebbe dei buchi disegnati come zeri`,
      };
    }
    const delta = t.euro[t.euro.length - 1] - t.euro[0];
    const movimenti = Object.keys(t.versamenti).length;
    const nIgnoti = Object.keys(t.ignoti).length;
    const versati = Object.values(t.versamenti).reduce((s, x) => s + x, 0);
    const segno = delta > 0 ? '+' : delta < 0 ? '−' : '';
    return {
      stato: 'viva',
      vista,
      date: t.date,
      valori: t.euro,
      euro: t.euro,
      regimi: t.regimi,
      versamenti: t.versamenti,
      ignoti: t.ignoti,
      confine: null, // dentro questa vista è tutto ufficiale: non c'è confine da segnare
      confineData,
      officialSince,
      // i conteggi sono QUELLI DISEGNATI: dire «93 ricostruiti» in una vista che
      // non ne disegna nemmeno uno era la frase falsa del tooltip.
      nRicostruiti: t.regimi.filter(r => r !== 'official').length,
      nUfficiali: t.regimi.filter(r => r === 'official').length,
      esclusi: primoUff,
      estremoDa: dataIt(t.date[0]),
      estremoA: dataIt(t.date[t.date.length - 1]),
      // ⚠️ «SNAPSHOT UFFICIALI DAL 12/06» sarebbe FALSO: il primo snapshot è
      // dell'11/06 (misurato: 58 righe in `nav_snapshots`, min 2026-06-11), ma
      // quel giorno la serie del motore tiene il valore RICOSTRUITO, quindi il
      // primo punto disegnabile è il successivo. La nota dice cosa si vede.
      nota: `· SNAPSHOT NAV · DA ${dataIt(t.date[0], true)}`,
      // ⚠️ in EURO e non in percentuale, apposta: una percentuale su una linea
      // che i versamenti la muovono è esattamente la confusione che il (F40)
      // ha tolto dalla cima. Qui non si rimette.
      // ⚠️ si decide sul CONTEGGIO dei movimenti, non sul netto: due versamenti
      // che si elidono (+12.500 e −12.500) davano «NESSUN VERSAMENTO NEL TRATTO»
      // su un tratto che ne aveva due.
      piede: `${segno}${eur0(Math.abs(delta))} · ${t.euro.length} PUNTI · `
        + (movimenti === 0 && nIgnoti === 0
          ? 'NESSUN MOVIMENTO NEL TRATTO'
          : versati !== 0
            ? `INCL. ${eur0(Math.abs(versati))} ${versati > 0 ? 'VERSATI' : 'PRELEVATI'}`
            : movimenti > 0
              ? `${movimenti} MOVIMENTI, NETTO ZERO`
              : '')
        + (nIgnoti > 0 ? `${movimenti > 0 || versati !== 0 ? ' · ' : ''}${nIgnoti} DI TIPO IGNOTO` : ''),
      piedeVerde: null,
      altra: { vista: 'quota', motivoSpento: null },
    };
  }

  const t = taglia(0);
  const v = idx.slice(0, n).map(num);
  const bucati = v.filter(x => !Number.isFinite(x)).length;
  if (bucati) {
    return {
      stato: 'assente',
      frase: 'quota non disegnabile',
      motivo: `${bucati} ${bucati === 1 ? 'punto' : 'punti'} su ${v.length} non ${bucati === 1 ? 'e’ un numero' : 'sono numeri'}: la linea della quota avrebbe dei buchi disegnati come zeri`,
    };
  }
  // ⚠️ LA BASE SI MISURA. La prima stesura scriveva «· BASE 100» come letterale e
  // faceva cadere la variazione su 0 quando il primo punto era 0: un numero non
  // misurato, per giunta colorato di verde. `leggiQuota` sullo stesso payload
  // rifiuta (`quota.ts:166-168`), e qui si fa lo stesso.
  const base = v[0];
  if (!Number.isFinite(base) || Math.abs(base) < 1e-6) {
    return {
      stato: 'assente',
      frase: 'base non utilizzabile',
      motivo: `il primo punto della serie vale ${base}: senza una base non si può dire di quanto è variata`,
    };
  }
  const variazione = (v[v.length - 1] / base - 1) * 100;
  return {
    stato: 'viva',
    vista: 'quota',
    date: t.date,
    valori: v,
    euro: t.euro,
    regimi: t.regimi,
    versamenti: t.versamenti,
    ignoti: t.ignoti,
    confine: primoUff > 0 ? primoUff : null,
    confineData,
    officialSince,
    esclusi: 0,
    estremoDa: dataIt(t.date[0]),
    estremoA: dataIt(t.date[t.date.length - 1]),
    nRicostruiti,
    nUfficiali,
    // la base si scrive con i decimali SOLO se ne ha — stessa regola dell'occhiello
    // della cima (`quota.ts:319-320`), che sullo stesso numero diceva «BASE 101»
    // arrotondando una base da 100,5
    nota: `· BASE ${fmtNum(base, Number.isInteger(base) ? 0 : 2)}`
      + (confineData ? ` · UFFICIALE DAL ${dataIt(confineData, true)}` : ''),
    piede: `${pct2(variazione)} · ${v.length} PUNTI · AL NETTO DEI VERSAMENTI`,
    // ⚠️ lo zero non è un guadagno: niente verde su una variazione nulla (era
    // `>= 0`, e nessun caso lo guardava — mutazione cieca trovata il 22/08)
    piedeVerde: variazione === 0 ? null : variazione > 0,
    altra: { vista: 'patrimonio', motivoSpento: patrimonioSpento },
  };
}

export interface LetturaCurva {
  data: string;
  /** il numero della vista: la quota, oppure gli euro */
  primaria: string;
  /** l'altra faccia dello stesso giorno, col suo regime dichiarato */
  secondaria: string;
  /** il versamento vero di quel giorno, se c'è (dal ledger, non da `flows_eur`) */
  versamento: string | null;
}

/**
 * La lettura del crosshair: UN SOLO POSTO che sa come si scrive, e non ha
 * bisogno del payload perché `leggiCurva` ha già allineato tutto.
 *
 * ⚠️ La secondaria porta sempre il suo regime, perché il numero non vuol dire
 * la stessa cosa nei due tratti: nel ricostruito è il valore di mercato del
 * book SENZA la cassa (l'11/06: circa il 62% del vero), nell'ufficiale
 * è il patrimonio pieno. Scriverlo nudo sarebbe un proxy non etichettato.
 *
 * ⚠️ `flows_eur` NON è il ledger (misurato: 22 giorni non nulli contro 2
 * movimenti veri): i versamenti si leggono da `external_flows`, e solo quelli.
 */
export function letturaCurva(c: CurvaViva, i: number): LetturaCurva | null {
  if (!Number.isInteger(i) || i < 0 || i >= c.date.length) return null;
  const uff = c.regimi[i] === 'official';
  const e = c.euro[i];
  const v = c.versamenti[c.date[i]];
  return {
    data: dataIt(c.date[i], true),
    primaria: c.vista === 'quota' ? quota2(c.valori[i]) : eur0(c.valori[i]),
    secondaria: c.vista === 'quota'
      ? (e == null || !isFinite(e)
        ? 'euro n.d.'
        : `${eur0(e)} · ${uff ? 'patrimonio ufficiale' : 'ricostruito, senza cassa'}`)
      : (uff ? 'snapshot ufficiale' : 'ricostruito, senza cassa'),
    versamento: v != null && v !== 0
      ? `${v > 0 ? '+' : '−'}${eur0(Math.abs(v))} ${v > 0 ? 'versati' : 'prelevati'}`
      : c.ignoti[c.date[i]]
        // il movimento c'è ma non sappiamo di che segno: dirlo è meglio che tacerlo
        ? `movimento «${c.ignoti[c.date[i]]}»: non conteggiato`
        : null,
  };
}

/**
 * Il titolo lungo (tooltip) del riquadro: qui ci sta il dettaglio che a schermo
 * costerebbe una riga, ed è dove va detto quello che il confine disegnato non
 * può dire da solo.
 */
export function spiegaCurva(c: EsitoCurva): string {
  if (c.stato === 'attesa') return 'la serie è in arrivo dal motore contabile';
  if (c.stato === 'assente') return c.motivo;
  const righe = [
    c.vista === 'quota'
      ? 'Valore quota (indice TWR base 100): il rendimento al netto dei versamenti. '
        + 'È la stessa serie del numero grande in cima, e l’ultimo punto è quel numero.'
      : 'Patrimonio vero preso dagli snapshot NAV: posizioni più la cassa di QUEL giorno. '
        + 'Nessuna ricostruzione, nessun proxy.',
  ];
  // ⚠️ IL GATE È SUL TRATTEGGIO DISEGNATO (`confine`), NON SUL DATO (`confineData`).
  // Con il gate sul dato, la vista patrimonio — che il tratteggio non ce l'ha —
  // affermava «Il tratteggio è il 12/06», «57 giorni ufficiali», «93 ricostruiti»:
  // tre frasi false raggiungibili con un clic, trovate dai confutatori il 22/08.
  if (c.confine != null && c.confineData && c.nRicostruiti > 0) {
    righe.push(
      `Il tratteggio è il ${dataIt(c.confineData, true)}: da lì gli snapshot sono ufficiali `
      + `(${c.nUfficiali} giorni), prima la serie è ricostruita da chiusure `
      + `(${c.nRicostruiti} giorni) e la cassa dell’epoca restava fuori perimetro — `
      + 'quindi i due tratti non sono omogenei.',
    );
    // ⚠️ il backend dichiara `official_since` sul giorno del PRIMO SNAPSHOT, ma
    // quel giorno resta il punto della ricostruzione: il primo punto disegnato
    // come ufficiale è il successivo. Un giorno di differenza, e chi confronta
    // l'etichetta col payload lo vedrebbe senza spiegazione.
    if (c.officialSince && c.officialSince !== c.confineData) {
      righe.push(
        `Il primo snapshot è del ${dataIt(c.officialSince, true)}, ma quel giorno resta `
        + 'il punto della ricostruzione: il tratteggio sta sul primo punto disegnato '
        + 'come ufficiale.',
      );
    }
  }
  if (c.vista === 'patrimonio' && c.esclusi > 0) {
    righe.push(
      `I ${c.esclusi} giorni prima del ${dataIt(c.date[0], true)} non hanno uno snapshot NAV: `
      + 'qui non ci sono, e il vuoto è quello — non una linea piatta.',
    );
  }
  righe.push(
    'L’ultimo punto NON è una chiusura: lo snapshot di oggi è riscritto a ogni giro '
    + 'prezzi, ogni 15 minuti. Passando il mouse sul grafico si legge il giorno.',
  );
  return righe.join(' ');
}
