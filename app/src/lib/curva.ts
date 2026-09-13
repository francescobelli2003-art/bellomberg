import { t as tr } from '@/i18n/t';
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
  return v === 'quota' ? tr('dashboard.curve_title_quota') : tr('dashboard.curve_title_wealth');
}

/** La parola dell'interruttore. Stessa ragione: le disegna la pagina in ogni stato. */
export function etichettaVista(v: VistaCurva): string {
  return v === 'quota' ? tr('dashboard.unit') : tr('dashboard.wealth');
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
  if (inCorso) return { stato: 'attesa', frase: tr('dashboard.curve_loading') };
  if (motivoErrore) return { stato: 'assente', frase: tr('dashboard.curve_missing'), motivo: motivoErrore };
  if (!twr || twr.error) {
    return {
      stato: 'assente',
      frase: tr('dashboard.curve_missing'),
      motivo: twr?.error || tr('dashboard.curve_no_response'),
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
      frase: tr('dashboard.curve_inconsistent'),
      motivo: tr('dashboard.curve_lengths_one', {a: date.length})
        + tr('dashboard.curve_lengths_two', {a: idx.length, b: val.length, c: reg.length}),
    };
  }
  if (n < 2) {
    return {
      stato: 'assente',
      frase: tr('dashboard.curve_short'),
      motivo: tr('dashboard.curve_minimum', {a: n}),
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
      ? tr('dashboard.curve_no_snapshot')
      : tr('dashboard.curve_one_snapshot');

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
        ignoti[f.date] = String(f.type || tr('dashboard.flow_untyped'));
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
      return { stato: 'assente', frase: tr('dashboard.curve_wealth_missing'), motivo: patrimonioSpento };
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
        frase: tr('dashboard.curve_noncontiguous'),
        motivo: tr('dashboard.curve_unofficial_points', {a: intrusi, b: intrusi === 1 ? tr('dashboard.point') : tr('dashboard.points'), c: intrusi === 1 ? tr('dashboard.is') : tr('dashboard.are')})
          + tr('dashboard.curve_unofficial_reason'),
      };
    }
    const bucati = t.euro.filter(x => !Number.isFinite(x)).length;
    if (bucati) {
      return {
        stato: 'assente',
        frase: tr('dashboard.curve_wealth_bad'),
        motivo: tr(bucati === 1 ? 'dashboard.wealth_bad_one' : 'dashboard.wealth_bad_many', { count: bucati, total: t.euro.length }),
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
      nota: tr('dashboard.curve_snapshot_since', {a: dataIt(t.date[0], true)}),
      // ⚠️ in EURO e non in percentuale, apposta: una percentuale su una linea
      // che i versamenti la muovono è esattamente la confusione che il (F40)
      // ha tolto dalla cima. Qui non si rimette.
      // ⚠️ si decide sul CONTEGGIO dei movimenti, non sul netto: due versamenti
      // che si elidono (+12.500 e −12.500) davano «NESSUN VERSAMENTO NEL TRATTO»
      // su un tratto che ne aveva due.
      piede: tr('dashboard.curve_wealth_change', {a: segno, b: eur0(Math.abs(delta)), c: t.euro.length})
        + (movimenti === 0 && nIgnoti === 0
          ? tr('dashboard.curve_no_flows')
          : versati !== 0
            ? tr('dashboard.curve_including_flow', { amount: eur0(Math.abs(versati)), direction: versati > 0 ? tr('dashboard.deposit') : tr('dashboard.withdrawal') })
            : movimenti > 0
              ? tr('dashboard.curve_flow_net', {a: movimenti})
              : '')
        + (nIgnoti > 0 ? tr('dashboard.curve_unknown_flows', {a: movimenti > 0 || versati !== 0 ? ' · ' : '', b: nIgnoti}) : ''),
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
      frase: tr('dashboard.curve_unit_bad'),
      motivo: tr(bucati === 1 ? 'dashboard.unit_bad_one' : 'dashboard.unit_bad_many', { count: bucati, total: v.length }),
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
      frase: tr('dashboard.curve_base_bad'),
      motivo: tr('dashboard.curve_base_reason', {a: base}),
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
      + (confineData ? tr('dashboard.curve_official_since', {a: dataIt(confineData, true)}) : ''),
    piede: tr('dashboard.curve_return', {a: pct2(variazione), b: v.length}),
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
        ? tr('dashboard.euro_na')
        : `${eur0(e)} · ${uff ? tr('dashboard.official_assets') : tr('dashboard.reconstructed_without_cash')}`)
      : (uff ? tr('dashboard.official_snapshot') : tr('dashboard.reconstructed_without_cash')),
    versamento: v != null && v !== 0
      ? `${v > 0 ? '+' : '−'}${eur0(Math.abs(v))} ${v > 0 ? tr('dashboard.deposit_lower') : tr('dashboard.withdrawal_lower')}`
      : c.ignoti[c.date[i]]
        // il movimento c'è ma non sappiamo di che segno: dirlo è meglio che tacerlo
        ? tr('dashboard.flow_unrecognized', {a: c.ignoti[c.date[i]]})
        : null,
  };
}

/**
 * Il titolo lungo (tooltip) del riquadro: qui ci sta il dettaglio che a schermo
 * costerebbe una riga, ed è dove va detto quello che il confine disegnato non
 * può dire da solo.
 */
export function spiegaCurva(c: EsitoCurva): string {
  if (c.stato === 'attesa') return tr('dashboard.curve_pending');
  if (c.stato === 'assente') return c.motivo;
  const righe = [
    c.vista === 'quota'
      ? tr('dashboard.curve_explain_unit')
        + tr('dashboard.curve_explain_same')
      : tr('dashboard.curve_explain_assets')
        + tr('dashboard.curve_no_proxy'),
  ];
  // ⚠️ IL GATE È SUL TRATTEGGIO DISEGNATO (`confine`), NON SUL DATO (`confineData`).
  // Con il gate sul dato, la vista patrimonio — che il tratteggio non ce l'ha —
  // affermava «Il tratteggio è il 12/06», «57 giorni ufficiali», «93 ricostruiti»:
  // tre frasi false raggiungibili con un clic, trovate dai confutatori il 22/08.
  if (c.confine != null && c.confineData && c.nRicostruiti > 0) {
    righe.push(
      tr('dashboard.curve_boundary_one', {a: dataIt(c.confineData, true)})
      + tr('dashboard.curve_boundary_two', {a: c.nUfficiali})
      + tr('dashboard.curve_boundary_three', {a: c.nRicostruiti})
      + tr('dashboard.curve_boundary_four'),
    );
    // ⚠️ il backend dichiara `official_since` sul giorno del PRIMO SNAPSHOT, ma
    // quel giorno resta il punto della ricostruzione: il primo punto disegnato
    // come ufficiale è il successivo. Un giorno di differenza, e chi confronta
    // l'etichetta col payload lo vedrebbe senza spiegazione.
    if (c.officialSince && c.officialSince !== c.confineData) {
      righe.push(
        tr('dashboard.curve_first_snapshot', {a: dataIt(c.officialSince, true)})
        + tr('dashboard.curve_first_reconstructed')
        + tr('dashboard.curve_as_official'),
      );
    }
  }
  if (c.vista === 'patrimonio' && c.esclusi > 0) {
    righe.push(
      tr('dashboard.curve_excluded_days', {a: c.esclusi, b: dataIt(c.date[0], true)})
      + tr('dashboard.curve_excluded_gap'),
    );
  }
  righe.push(
    tr('dashboard.curve_latest_live')
    + tr('dashboard.curve_latest_frequency'),
  );
  return righe.join(' ');
}
