import { t as tr } from '@/i18n/t';
// F14 MOVIMENTI v4 "TRE VISTE" — impianto scelto dal PM 27/07 sui PNG di
// mockup_f14_movements (opzione B, contro A "le scie" e C "due pagine").
// Le SCIE non sono state scartate: sono una delle tre viste.
//
// DUE FETCH, DUE ARCHIVI. Commutare vista non chiama niente: le tre viste
// sono renderer puri delle derivazioni di lib/movimenti.ts.
//
// ── IL REGISTRO UNICO (impianto 2, scelto dal PM il 21/08 sulla rosa in
//    situ `mockup_f14_cassa/`) ────────────────────────────────────────────
// I flussi di cassa entrano FRA i trade, sulla stessa linea del tempo: la
// domanda a cui F14 risponde e' «cosa e' successo quel giorno», e la risposta
// non e' completa se un bonifico sta su un'altra pagina.
// Le derivazioni sui TITOLI (corsie, realizzato, cancello, arco) restano sui
// soli `Trade[]`: un flusso non ha titolo, prezzo, ne' un realizzato, e
// lasciarcelo entrare lo farebbe contare dove non c'entra.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { RefreshCw, Wallet } from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import type { MovimentoCassa } from '@/lib/api';
import { fmtNum } from '@/lib/format';
import { leggiDetail } from '@/lib/quota';
import { useT } from '@/i18n/provider';
import {
  Trade,
  raggruppaPerMese, arcoDi, costruisciCorsie,
  statoCancello, contaRealizzato, contaOreSegnaposto,
  fondiRegistro, testiDiRiga, tickerDiRiga, contaFlussi,
} from '@/lib/movimenti';
import Registro from '@/components/movimenti/Registro';
import Scie, { contaVerbi } from '@/components/movimenti/Scie';
import Diario from '@/components/movimenti/Diario';
import './movimenti.css';

type Vista = 'registro' | 'scie' | 'diario';
type Filtro = 'TUTTI' | 'BUY' | 'TRIM' | 'ADD' | 'DIVIDEND' | 'CASSA' | 'COMMENTO';

const viste = (): { id: Vista; nome: string }[] => [
  { id: 'registro', nome: tr('movements.register') },
  { id: 'scie', nome: tr('movements.trails') },
  { id: 'diario', nome: tr('movements.diary') },
];

// Il limite della richiesta non equivale al totale dello storico.
const LIMITE = 100;
// I limiti trade delle due pagine differiscono: dichiarare la finestra quando satura.
const LIMITE_CASSA = 200;

export default function MovementsPage() {
  const tr = useT();
  const VISTE = viste();
  const [trades, setTrades] = useState<Trade[]>([]);
  const [cassa, setCassa] = useState<MovimentoCassa[]>([]);
  const [loading, setLoading] = useState(true);
  type ErroreArchivio = { kind: 'shape' } | { kind: 'transport'; detail: string };
  const [erroreT, setErr] = useState<ErroreArchivio | null>(null);
  const [erroreC, setErrCassa] = useState<ErroreArchivio | null>(null);
  const mostraErrore = (e: ErroreArchivio | null, archive: 'trades' | 'movements') => e == null ? null
    : e.kind === 'shape' ? tr('movements.shapeError', { archive }) : e.detail || tr('movements.errorUnknown');
  const err = mostraErrore(erroreT, 'trades');
  const errCassa = mostraErrore(erroreC, 'movements');
  /* ── LA CURA §2.3: «LETTO» NON È «OSSERVABILE» ───────────────────────────
     Erano un interruttore solo (`noto`), e da quell'unico interruttore
     dipendevano DUE affermazioni di natura diversa:
       · i CONTEGGI (quante righe, quanti titoli) — che una lettura riuscita
         VUOTA sa dare benissimo: zero e' un fatto misurato;
       · le affermazioni sulla FORMA del payload («AL CANCELLO», «P&L NON
         CONSEGNATO DA /trades») — che su ZERO righe NON sono osservabili:
         `statoCancello([])` risponde 'chiuso' perche' `[].some()` e' false,
         non perche' abbia visto un payload privo di quella chiave.
     Un interruttore, due difetti OPPOSTI: dopo una lettura riuscita vuota piu'
     un refresh fallito la side diceva «ARCHIVIO NON LETTO» (falso: era stato
     letto, ed era vuoto); e curarlo mettendo `letto=true` avrebbe riaperto la
     MEDIA audit/24 §B.6, cioe' due affermazioni sulla forma di un payload che
     su zero righe nessuno ha osservato.
     Ora: `letto` abilita i CONTEGGI, `osservabile` (letto E almeno una riga)
     abilita le affermazioni sulla FORMA.
     ⚠️ `letto` non torna mai indietro, ed e' voluto: una lettura riuscita e'
     un fatto avvenuto e un fallimento successivo non lo cancella — dice solo
     che cio' che e' a schermo puo' essere vecchio, e a dirlo c'e' gia'
     l'avviso in fondo alla pagina.
     ⚠️ Due archivi, due interruttori: `GET /trades` e `GET /cash/movements`
     falliscono in modo indipendente, e «non letto» deve dire QUALE. */
  const [lettoT, setLettoT] = useState(false);
  const [lettoC, setLettoC] = useState(false);
  const [vista, setVista] = useState<Vista>('registro');
  const [filtro, setFiltro] = useState<Filtro>('TUTTI');
  const [selezione, setSelezione] = useState<string | null>(null);
  const vstRef = useRef<HTMLDivElement>(null);
  // contatore di generazione: due AGGIORNA in volo risolvono nell'ordine che
  // decide la rete, non in quello in cui sono partiti. Senza questo vinceva
  // l'ULTIMA risposta ad arrivare — quindi una lettura vecchia poteva
  // sovrascrivere una fresca, e il .finally spegneva lo spinner alla prima
  // che rientrava mentre l'altra era ancora aperta (review 27/07, MEDIA).
  const gen = useRef(0);
  const vivo = useRef(true);
  /* ⚠️ `vivo.current = true` all'ingresso, non solo `false` in uscita: senza,
     un doppio-invoke di React (StrictMode) spegnerebbe la bandierina al primo
     cleanup e nessuno la riaccenderebbe — la pagina resterebbe su «Caricamento»
     per sempre, in silenzio. Oggi non accade perche' StrictMode e' spento
     (`main.tsx`), ma chi lo riaccendesse romperebbe F14 senza un errore. */
  useEffect(() => { vivo.current = true; return () => { vivo.current = false; }; }, []);

  // il testo del backend si rende VERBATIM: e' l'unica cosa che sa davvero
  // cos'e' andato storto (il detail ora porta anche il tipo)
  const motivoDi = (e: any): ErroreArchivio => ({ kind: 'transport',
    detail: leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || (e instanceof Error ? '' : leggiDetail(e)) });

  /** Una lista di oggetti, non solo «un array».
   *  ⚠️ `Array.isArray` guarda il CONTENITORE. Misurato: `{trades:[1,2]}` passava
   *  e produceva righe fantasma contate fra i MOVIMENTI (il fallback zitto che
   *  la guardia doveva chiudere), e `{trades:[null]}` faceva esplodere
   *  `fondiRegistro`/`arcoDi`/`statoCancello` dentro l'ErrorBoundary — cioe' una
   *  schermata d'errore generica al posto di un buco dichiarato. */
  const listaDiRighe = (v: unknown): v is Record<string, unknown>[] =>
    Array.isArray(v) && v.every(x => !!x && typeof x === 'object');

  const carica = () => {
    const mio = ++gen.current;
    setLoading(true);
    /* ⚠️ NON si azzerano `err`/`errCassa` qui. Azzerandoli, l'avviso «a schermo
       restano righe vecchie» si spegneva ALL'ISTANTE mentre le righe vecchie
       restavano — e restavano per tutta la durata delle due richieste (timeout
       axios 30s, `api.ts`). Trenta secondi di dati stantii senza che nulla lo
       dicesse, e se la rilettura falliva di nuovo erano stati una bugia. Ogni
       errore si sostituisce quando la SUA risposta arriva, non prima. */
    /* Le due letture sono indipendenti: un errore di cassa non elimina i trade letti. */
    Promise.allSettled([
      Bellomberg.trades(LIMITE),
      Bellomberg.cashMovements(LIMITE_CASSA),
    ])
      .then(([rt, rc]) => {
        if (!vivo.current || mio !== gen.current) return;   // risposta sorpassata: si butta
        // ── i titoli ──────────────────────────────────────────────────
        if (rt.status === 'rejected') setErr(motivoDi(rt.reason));
        else {
          const r = rt.value as { trades?: unknown };
          // `r.trades || []` trasformava QUALUNQUE 200 malformato (chiave
          // rinominata, proxy che risponde HTML, `trades: null`) in "archivio
          // vuoto, non un errore" — un fallback silenzioso proprio nel punto
          // in cui i dati entrano. Ora la forma sbagliata e' un KO dichiarato.
          if (!listaDiRighe(r?.trades)) {
            setErr({ kind: 'shape' });
          } else {
            setTrades(r.trades as unknown as Trade[]);
            setLettoT(true);        // ⚠ solo su una lista VERA: un 200 malformato non e' una lettura
            setErr(null);           // la rilettura e' riuscita: solo ORA l'errore vecchio decade
          }
        }
        // ── la cassa: stessa disciplina, archivio separato ────────────
        if (rc.status === 'rejected') setErrCassa(motivoDi(rc.reason));
        else {
          const r = rc.value as { movements?: unknown };
          if (!listaDiRighe(r?.movements)) {
            setErrCassa({ kind: 'shape' });
          } else {
            setCassa(r.movements as unknown as MovimentoCassa[]);
            setLettoC(true);
            setErrCassa(null);
          }
        }
      })
      .finally(() => {
        if (vivo.current && mio === gen.current) setLoading(false);
      });
  };
  useEffect(carica, []);

  // Il registro unificato ordina entrambe le specie; evitare un secondo ordinamento.
  // Arco, corsie e realizzato si derivano dai soli trade: la cassa non ha ticker.
  const arco = useMemo(() => arcoDi(trades), [trades]);
  const corsie = useMemo(() => (arco ? costruisciCorsie(trades, arco) : []), [trades, arco]);
  const cancello = useMemo(() => statoCancello(trades), [trades]);
  const realizzato = useMemo(() => contaRealizzato(trades), [trades]);
  const verbi = useMemo(() => contaVerbi(corsie), [corsie]);
  const oreFinte = useMemo(() => contaOreSegnaposto(trades), [trades]);

  // ── il registro unico: titoli e cassa sulla stessa linea del tempo ──
  const registro = useMemo(() => fondiRegistro(trades, cassa), [trades, cassa]);
  const flussi = useMemo(() => contaFlussi(cassa), [cassa]);

  // ⚠️ I conteggi si fanno sulla popolazione GIA' ristretta dalla selezione di
  // corsia, non su tutto il portafoglio: altrimenti un chip prometteva
  // "DIVIDENDO 2", restava cliccabile con una posizione selezionata (che dividendi non
  // ne ha) e consegnava zero righe. Un numero che promette e non mantiene
  // (review 27/07, MEDIA).
  // ⚠️ Con una corsia scelta le righe di cassa ESCONO, e non e' una svista: una
  // corsia e' un titolo, e un flusso di cassa non ne ha uno.
  const inSelezione = useMemo(
    () => (selezione ? registro.filter(r => tickerDiRiga(r) === selezione) : registro),
    [registro, selezione]);

  const conteggi = useMemo(() => {
    const c: Record<string, number> = { TUTTI: inSelezione.length, COMMENTO: 0, CASSA: 0 };
    for (const r of inSelezione) {
      if (r.specie === 'cassa') c.CASSA++;
      else c[r.t.action] = (c[r.t.action] || 0) + 1;
      if (!testiDiRiga(r).vuoto) c.COMMENTO++;
    }
    return c;
  }, [inSelezione]);

  // Finche' la prima lettura non e' finita, o se e' fallita, NON si sa quanti
  // movimenti ci siano: zero non e' il valore, e' l'ignoto. Prima la fascia
  // scriveva MOVIMENTI 0 / TITOLI 0 a 17px nel colore del dato mentre il
  // corpo diceva "Storico non disponibile" (review 27/07, ALTA).
  /** almeno uno dei due archivi e' stato letto: i conteggi hanno un senso */
  const lettoQualcosa = lettoT || lettoC;
  /** il TOTALE e' un totale solo se sono stati letti tutti e due */
  const totaleIntero = lettoT && lettoC;
  /** ho visto almeno una riga dei trade: solo ora posso dire qualcosa sulla
   *  FORMA di quel payload (v. il blocco della cura §2.3 qui sopra) */
  const osservabileT = lettoT && trades.length > 0;
  /** quante righe ci sono in archivio: si sommano solo gli archivi LETTI —
   *  contare a zero quello non letto lo spaccerebbe per vuoto */
  const nMov = (lettoT ? trades.length : 0) + (lettoC ? cassa.length : 0);
  const nd = (v: number | string) => (lettoQualcosa ? v : tr('movements.nd'));
  /** i numeri che parlano SOLO dei titoli non li sblocca la lettura della cassa */
  const ndT = (v: number | string) => (lettoT ? v : tr('movements.nd'));
  // se il backend consegna esattamente il tetto chiesto, quella NON e' la
  // storia: e' la finestra piu' recente, e va dichiarato. Due archivi, due
  // finestre: dichiarate separate perche' si riempiono in momenti diversi.
  const finestra = trades.length >= LIMITE;
  const finestraCassa = cassa.length >= LIMITE_CASSA;
  /** Un filtro è attivo o no: è uno STATO, non si deduce dai conteggi. */
  const filtroAttivo = filtro !== 'TUTTI' || selezione !== null;

  // I filtri valgono su REGISTRO e DIARIO. Su SCIE no, e non e' una
  // dimenticanza: il peso di un segno e' relativo al piu' grosso della SUA
  // corsia, quindi filtrare cambierebbe l'altezza di tutti i segni rimasti
  // e la stessa mossa apparirebbe piu' grande solo perche' e' rimasta sola.
  // Sulle scie, al posto dei filtri, si mostra la legenda della scala.
  const filtrate = useMemo(() => {
    let out = registro;
    if (filtro === 'COMMENTO') out = out.filter(r => !testiDiRiga(r).vuoto);
    else if (filtro === 'CASSA') out = out.filter(r => r.specie === 'cassa');
    else if (filtro !== 'TUTTI')
      out = out.filter(r => r.specie === 'titolo' && r.t.action === filtro);
    if (selezione) out = out.filter(r => tickerDiRiga(r) === selezione);
    return out;
  }, [registro, filtro, selezione]);

  const soloConTesto = useMemo(() => filtrate.filter(r => !testiDiRiga(r).vuoto), [filtrate]);

  // I mesi si contano sulle righe RESE, non su tutte: un separatore che dice
  // "23 movimenti" sopra nove righe filtrate sarebbe un numero che mente.
  const mesiVisti = useMemo(() => raggruppaPerMese(filtrate), [filtrate, tr]);

  // frecce sul commutatore: e' un gruppo di bottoni, si scorre come tale
  const frecce = (e: ReactKeyboardEvent) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const i = VISTE.findIndex(v => v.id === vista);
    const n = (i + (e.key === 'ArrowRight' ? 1 : VISTE.length - 1)) % VISTE.length;
    setVista(VISTE[n].id);
    const b = vstRef.current?.querySelectorAll('button')[n] as HTMLButtonElement | undefined;
    b?.focus();
  };

  /* «Archivio vuoto» e' un'AFFERMAZIONE, e vale solo se tutti e due gli
     archivi sono stati letti e non ce n'e' uno in errore: con la cassa non
     letta, «nessun movimento» sarebbe una cosa che nessuno ha misurato.
     ⚠️ E vale solo a lettura FERMA. Senza `!loading` il cartello «il backend ha
     risposto correttamente con una lista vuota» si accendeva SOTTO quello di
     «Caricamento», cioe' affermava la forma di una risposta non ancora
     arrivata. Due cartelli impilati che si smentiscono. */
  const vuoto = !loading && totaleIntero && !err && !errCassa && registro.length === 0;

  return (
    <div className="f14m">
      {/* ── la plancia: viste, filtri o legenda, contatori ───────── */}
      <div className="cmd">
        <div className="vst" ref={vstRef} role="group" aria-label={tr('movements.viewLabel')} onKeyDown={frecce}>
          {VISTE.map(v => (
            <button
              key={v.id}
              type="button"
              aria-pressed={vista === v.id}
              onClick={() => setVista(v.id)}
            >
              {v.nome}
              {/* Il conteggio della linguetta descrive le righe rese; le corsie ignorano i filtri. */}
              <span className="k">
                {v.id === 'registro' ? nd(filtrate.length)
                  /* le CORSIE parlano solo di titoli: leggere la cassa non le
                     rende note, e il loro n.d. non deve spegnersi per sbaglio */
                  : v.id === 'scie' ? tr(lettoT && corsie.length === 1 ? 'movements.laneCountOne' : 'movements.laneCount', {a: ndT(corsie.length)})
                  : tr(lettoQualcosa && soloConTesto.length === 1 ? 'movements.noteCountOne' : 'movements.noteCount', {a: nd(soloConTesto.length)})}
              </span>
            </button>
          ))}
        </div>

        {vista === 'scie' ? (
          // ogni numero sta accanto al colore che lo disegna davvero: prima
          // "INGRESSO 58" stava sulla pastiglia ambra, ma i segni ambra sono
          // 56 — i 2 ADD sono ciano (review 27/07, BASSA)
          <div className="leg">
            <i><span className="sw in" />BUY {verbi.buy}</i>
            <i><span className="sw add" />ADD {verbi.add}</i>
            <i><span className="sw out" />{tr('movements.exit')} {verbi.uscite}</i>
            <i><span className="sw dv" />{tr('movements.dividend')} {verbi.dividendi}</i>
          </div>
        ) : (
          <div className="fil">
            {/* CASSA sta fra i verbi e CON COMMENTO: e' un filtro sulla SPECIE
                della riga, non su un verbo, e sta accanto agli altri perche'
                da qui in poi il registro contiene due specie. */}
            {(['TUTTI', 'BUY', 'TRIM', 'ADD', 'DIVIDEND', 'CASSA', 'COMMENTO'] as Filtro[]).map(f => (
              <button
                key={f}
                type="button"
                aria-pressed={filtro === f}
                /* ⚠️ un chip PREMUTO non si spegne mai. `aria-pressed="true"` su
                   un bottone `disabled` viene annunciato «premuto, non
                   disponibile», e il corpo diceva «togli il filtro» mentre il
                   comando che lo toglie non era ripremibile. */
                disabled={!conteggi[f] && filtro !== f}
                onClick={() => setFiltro(f)}
              >
                {f === 'COMMENTO' ? tr('movements.withComment') : f === 'TUTTI' ? tr('movements.all') : f === 'CASSA' ? tr('movements.cash') : f === 'DIVIDEND' ? tr('movements.dividend') : f} {conteggi[f] || 0}
              </button>
            ))}
            {/* Questo chip si SMONTA quando lo premi (la selezione sparisce e
                con lei il bottone): senza spostare il fuoco a mano finiva sul
                <body> e la tastiera ripartiva da capo. Il fuoco torna al primo
                filtro, che e' il vicino piu' prossimo (review 27/07, BASSA). */}
            {selezione && (
              <button
                type="button"
                className="tolg"
                aria-label={tr('movements.clearLane', {a: selezione})}
                onClick={e => {
                  const gruppo = (e.currentTarget.parentElement as HTMLElement | null);
                  setSelezione(null);
                  (gruppo?.querySelector('button') as HTMLButtonElement | null)?.focus();
                }}
              >
                {selezione} ✕
              </button>
            )}
          </div>
        )}

        <div className="kpi">
          <div>
            <span className="k">{tr('movements.movements')}</span>
            <span className="v num">{nd(nMov)}</span>
            {/* Questo contatore ha cambiato SIGNIFICATO: da «mosse sui titoli»
                a «cose successe». Il sub dice di cosa e' fatto — e se un
                archivio non e' stato letto lo dice, invece di lasciar passare
                un parziale per un totale. */}
            {/* Distinguere il numero delle operazioni dal numero dei ticker distinti. */}
            {lettoQualcosa && (
              <span className="sub">
                {totaleIntero
                  ? tr('movements.bothCounts', {a: tr(trades.length === 1 ? 'movements.tradeCountOne' : 'movements.tradeCount', {a: trades.length}), b: tr(cassa.length === 1 ? 'movements.cashCountOne' : 'movements.cashMovementCount', {a: cassa.length})})
                  : lettoT
                    ? tr('movements.tradesOnlyCount', {a: tr(trades.length === 1 ? 'movements.tradeCountOne' : 'movements.tradeCount', {a: trades.length})})
                    : tr('movements.cashOnlyCount', {a: tr(cassa.length === 1 ? 'movements.cashCountOne' : 'movements.cashMovementCount', {a: cassa.length})})}
              </span>
            )}
          </div>
          <div><span className="k">{tr('movements.securities')}</span><span className="v num">{ndT(corsie.length)}</span></div>
          <div>
            <span className="k">{tr('movements.span')}</span>
            <span className="v num">{arco ? tr(arco.giorni === 1 ? 'movements.dayOne' : 'movements.days', {a: arco.giorni}) : tr('movements.nd')}</span>
          </div>
          <div>
            <span className="k">{tr('movements.flows')}</span>
            {/* ⚠️ MAI verde/rosso su questi numeri: in questa fascia quei due
                colori sono il SEGNO DEL P&L (`.kpi .v.su/.giu`), e un prelievo
                non e' una perdita. Il verso lo dice il segno davanti al numero,
                che e' vero in ogni stato. */}
            {!lettoC
              ? <span className="v gate">{tr('movements.nd')}</span>
              : <>
                  {/* ⚠️ DUE decimali, non zero. Con `fmtNum(…, 0)` ogni netto
                      fra −0,50 e +0,50 usciva «−0 €» o «+0 €»: un deflusso vero
                      reso come uno zero col segno, e il valore esatto non
                      raggiungibile da nessuna parte — il difetto che F7 aveva
                      gia' curato mettendo l'esatto in `title` (F37). Qui gli
                      importi sono a 2 decimali a disco (`memory_db.py:1091`),
                      quindi due decimali SONO il valore esatto. */}
                  <span className="v num">
                    {(flussi.netto >= 0 ? '+' : '−') + fmtNum(Math.abs(flussi.netto), 2)} €
                  </span>
                  <span className="sub">
                    {tr('movements.notBalance')}
                    {/* la seconda cosa che questo numero non e', accanto al
                        numero e non in un altro blocco della plancia */}
                    {finestraCassa && tr('movements.windowOnly')}
                    {flussi.ignoti > 0 && tr('movements.uncounted', {a: flussi.ignoti})}
                  </span>
                </>}
          </div>
          <div>
            <span className="k">{tr('movements.realized')}</span>
            {/* Tre stati, non due. `null` = la colonna non arriva proprio.
                `vuoto` = arriva ma nessuna riga la valorizza (il caso normale
                di una migrazione in due tempi): prima usciva "+0,00 €" IN
                VERDE, cioe' "le nove uscite hanno chiuso in pari", che e'
                un'affermazione che nessuno ha fatto. */}
            {/* ⚠ e un QUARTO stato prima dei tre: l'ignoto. A lettura fallita
                statoCancello([]) direbbe 'chiuso' e qui uscirebbe AL CANCELLO —
                un'affermazione sulla forma di un payload mai ricevuto (la
                garanzia F21 «n.d. su tutti e quattro» valeva 3/4, audit/24 B.6). */}
            {!lettoT
              ? <span className="v gate">{tr('movements.nd')}</span>
              /* ⚠️ LA CURA §2.3, qui e' il punto dove mordeva: con `letto` da
                 solo, una lettura riuscita e VUOTA avrebbe fatto uscire «AL
                 CANCELLO» — perche' `contaRealizzato([])` torna null a causa
                 di `statoCancello([])` = 'chiuso', che su zero righe non ha
                 osservato nessun payload. E' la MEDIA audit/24 §B.6. */
              /* «SUI TITOLI» esplicito: la frase si legge mentre il registro
                 puo' avere righe di cassa a schermo, e senza il qualificatore
                 sembrerebbe negarle */
              : !osservabileT
              ? <span className="v gate">{tr('movements.noObservableTrade')}</span>
              : realizzato === null
              ? <span className="v gate">{tr('movements.atGate')}</span>
              : realizzato.stato === 'vuoto'
                ? <span className="v gate">{tr('movements.emptyColumn')}</span>
                : <>
                    <span className={'v num ' + (realizzato.somma >= 0 ? 'su' : 'giu')}>
                      {(realizzato.somma >= 0 ? '+' : '') + fmtNum(realizzato.somma, 2)} €
                    </span>
                    {/* Accompagnare la somma con il numero delle uscite e la concentrazione del risultato. */}
                    <span className="sub">
                      {realizzato.n} {tr('movements.exitsDot')} {realizzato.vinte}↑ {realizzato.perse}↓
                      {realizzato.pari > 0 && ` ${realizzato.pari}=`}
                      {realizzato.quotaMaggiore > 0
                        && ` · ${realizzato.tickerMaggiore} ${Math.round(realizzato.quotaMaggiore * 100)}%`}
                    </span>
                  </>}
          </div>
          <div className="azioni">
            <button
              type="button"
              className="agg"
              onClick={carica}
              disabled={loading}
              aria-label={tr('movements.refreshLabel')}
            >
              <RefreshCw size={12} className={loading ? 'spin' : ''} /> {tr('movements.refresh')}
            </button>
          </div>
        </div>
      </div>

      {/* ── la vista ─────────────────────────────────────────────── */}
      <div className="p">
        <span className="sq tl" /><span className="sq tr" />
        <span className="sq bl" /><span className="sq br" />

        <div className="ph">
          <Wallet size={12} />
          <h1>
            {/* l'intestazione dice cosa la vista È, non cosa ha caricato in
                questo istante: quello lo dichiara la riga di stato accanto */}
            {vista === 'registro' ? tr('movements.registerTitle')
              /* le corsie vengono dai soli trade: le sblocca `lettoT`, non la
                 lettura della cassa (era `noto`, che da oggi comprenderebbe
                 anche un archivio che con le corsie non c'entra) */
              : vista === 'scie' ? (lettoT
                  ? tr(corsie.length === 1 ? 'movements.trailsTitleOne' : 'movements.trailsTitle', {a: corsie.length, b: arco ? tr(arco.giorni === 1 ? 'movements.timesDayOne' : 'movements.timesDays', {a: arco.giorni}) : ''})
                  : tr('movements.trailsUnknown'))
              : tr('movements.diaryTitle')}
          </h1>
          {/* Numeratore e denominatore descrivono la stessa popolazione filtrata. */}
          <span className="side">
            {vista === 'scie'
              ? tr('movements.heightMeaning')
              : !lettoQualcosa
                /* «0 RIGHE RESE SU 0 IN ARCHIVIO» a lettura fallita era un
                   conteggio affermato sull'ignoto (audit/24 B.6). Ma la frase
                   che lo curava era falsa a sua volta: dopo una lettura
                   riuscita e VUOTA seguita da un refresh fallito diceva
                   «ARCHIVIO NON LETTO», e l'archivio era stato letto. Ora
                   dipende da `letto` (un fatto avvenuto), non dall'esito
                   dell'ULTIMA lettura. */
                ? tr('movements.bothUnread')
              : vista === 'registro'
                /* ⚠️ «ALMENO» quando una finestra è piena. Senza, la stessa riga
                   diceva «102 IN ARCHIVIO» e due frasi dopo «potrebbero
                   essercene altri»: due metà che si smentiscono. Il backend non
                   consegna un totale — serve la finestra e basta
                   (`bellomberg_api.py:1894`, `count = len(rows)`). F7 lo scrive
                   già così: «almeno N movimenti» (`TradeEntryPage.tsx:925`). */
                ? tr('movements.renderedCount', {a: filtrate.length, b: finestra || finestraCassa ? tr('movements.atLeast') : '', c: nMov})
                  /* La composizione completa e gia nel KPI; ripeterla solo se una fonte non e stata letta. */
                  + (totaleIntero
                      ? ''
                      : lettoT
                        ? tr('movements.onlyTradesAside')
                        : tr('movements.onlyCashAside'))
                  /* dallo STATO, non dal confronto dei conteggi: un filtro che
                     non scarta nulla è attivo lo stesso, e dirlo spento era
                     falso (es. CON COMMENTO con tutte le righe commentate) */
                  + (filtroAttivo ? tr('movements.filterActive') : '')
                : tr('movements.commentsCount', {a: soloConTesto.length, b: filtrate.length})}
            {/* Rendere visibile la concentrazione del risultato, oltre alla sua somma. */}
            {realizzato && realizzato.stato === 'ok' && vista !== 'scie'
              && tr('movements.realizedCounts', {a: realizzato.n, b: realizzato.vinte, c: realizzato.perse})
                 + (realizzato.pari > 0 ? tr('movements.flatCount', {a: realizzato.pari}) : '')
                 + (realizzato.quotaMaggiore > 0
                    ? tr('movements.concentration', {a: realizzato.tickerMaggiore, b: Math.round(realizzato.quotaMaggiore * 100)})
                    : '')}
            {/* due archivi, due tetti: si dichiarano SEPARATI perche' si
                riempiono in momenti diversi, e dire «finestra» senza dire di
                quale dei due lascerebbe il PM a indovinare quale meta' e' monca */}
            {finestra && tr('movements.tradeWindow', {a: LIMITE})}
            {finestraCassa && tr('movements.cashWindow', {a: LIMITE_CASSA})}
            {/* ⚠️ LA CURA §2.3: questa e' un'affermazione sulla FORMA del
                payload dei trade, quindi vuole `osservabile` e non `letto` —
                su zero righe `statoCancello([])` dice 'chiuso' senza aver
                osservato niente (audit/24 §B.6). */}
            {osservabileT && cancello === 'chiuso' && vista !== 'scie'
              && tr('movements.realizedMissing')}
            {/* l'ora non e' misurata dappertutto: su una parte delle righe e'
                il segnaposto dell'importatore, e spacciarla per un orario
                sarebbe una precisione inventata */}
            {oreFinte > 0
              && tr('movements.conventionalCount', {a: oreFinte, b: trades.length})}
          </span>
        </div>

        {/* aria-live: un buco dichiarato che nessuno annuncia e' dichiarato
            solo per chi guarda. Con un lettore di schermo, AGGIORNA che
            fallisce lasciava la tabella identica e il fuoco sul bottone,
            senza una parola (review 27/07, MEDIA). */}
        <div className="pb" role="status" aria-live="polite">
          {/* TRE stati e tre rese: un errore del backend NON si legge come
              "nessun movimento". Era il false-empty segnalato dall'audit. */}
          {loading && registro.length === 0 && (
            <div className="stato load">
              <span className="tt">{tr('movements.loading')}</span>
              <span className="tx">{tr('movements.loadingDetail')}</span>
            </div>
          )}

          {/* Due archivi che possono fallire da soli: l'avviso dice QUALE, e
              se ne cade uno solo la pagina resta viva con l'altro. */}
          {/* ⚠️ «è la lettura ad essere fallita» va detto SOLO degli archivi in
              errore. Con `/trades` che risponde 200 con una lista vuota e la
              cassa che cade, il registro è vuoto e questo blocco affermava che
              anche i titoli non erano stati letti: falso, erano stati letti e
              non c'era niente. Ora ogni archivio porta la sua frase. */}
          {(err || errCassa) && registro.length === 0 && (
            <div className="stato ko">
              <span className="tt">
                {err && errCassa ? tr('movements.unavailable') : tr('movements.incomplete')}
              </span>
              <span className="tx">
                {err && <>{tr('movements.tradeErrorPrefix')} <b>{err}</b>. </>}
                {!err && lettoT && (
                  <>{tr('movements.tradeRead')} <b>{tr('movements.readPlural')}</b>{tr('movements.noneRead')} </>
                )}
                {errCassa && <>{tr('movements.cashErrorPrefix')} <b>{errCassa}</b>. </>}
                {!errCassa && lettoC && (
                  <>{tr('movements.cashRead')} <b>{tr('movements.readCash')}</b>{tr('movements.noCash')} </>
                )}
                {tr('movements.readFailedHelp')}
                {/* ⚠️ Senza questa frase, la fascia scriveva MOVIMENTI 0 a 17px nel
                    colore del dato mentre qui si diceva «non disponibile» — cioè
                    la resa che la review del 27/07 aveva bollato come ALTA, che
                    torna raggiungibile proprio perché `letto` non torna indietro.
                    Lo zero È l'ultima misura, ma va detto di quando è. */}
                {lettoQualcosa && (
                  <> {tr('movements.headerOld')}<b>{tr('movements.lastSuccess')}</b>{tr('movements.possiblyOld')}</>
                )}
              </span>
            </div>
          )}

          {vuoto && (
            <div className="stato vuoto">
              <span className="tt">{tr('movements.empty')}</span>
              <span className="tx">
                {tr('movements.emptyDetail')}
              </span>
            </div>
          )}

          {/* Un filtro che non pesca nulla NON deve lasciare una tabella nuda:
              sarebbe lo stesso peccato del false-empty, in piccolo — il PM
              vedrebbe il vuoto senza sapere se e' il filtro o un guasto. */}
          {registro.length > 0 && vista === 'registro' && (
            filtrate.length > 0
              ? <Registro righe={filtrate} mesi={mesiVisti} cancello={cancello} />
              : <div className="stato vuoto">
                  <span className="tt">{tr('movements.emptyFilter')}</span>
                  {/* lo stesso qualificatore della riga di stato: senza, questa
                      frase dava per «l'archivio» quello che è solo la metà letta */}
                  <span className="tx">
                    {totaleIntero
                      ? <>{tr('movements.archiveHas')} {nMov} {tr('movements.movesLower')}</>
                      : lettoT
                        ? <>{tr('movements.readCount')} {nMov} {tr('movements.tradesOnlyLower')}</>
                        : <>{tr('movements.readCount')} {nMov} {tr('movements.cashOnlyLower')}</>}{tr('movements.noneMatch')}{selezione ? ` (${selezione})` : ''}{tr('movements.clearFilterHelp')}
                  </span>
                </div>
          )}
          {/* ⚠️ Le SCIE avevano DUE stati muti, e uno l'ha aperto questa stessa
              modifica. Il vecchio `trades.length > 0 && vista === 'scie' && arco`
              non rendeva NULLA — e senza una parola — quando `arco` è `null`,
              cioè quando nessuna data è leggibile (`arcoDi` torna `null`
              apposta invece di un arco `NaN`). E da oggi il registro può avere
              righe con ZERO trade — solo cassa — e lì una corsia non esiste
              proprio. Un vuoto che non si spiega è la stessa malattia del
              false-empty, in piccolo: adesso tutti e due dicono perché. */}
          {registro.length > 0 && vista === 'scie' && (
            trades.length === 0
              ? <div className="stato vuoto">
                  <span className="tt">{tr('movements.trailsSecurities')}</span>
                  <span className="tx">
                    {tr('movements.onScreen')} {cassa.length === 1
                      ? <>{tr('movements.thereIs')} <b>{tr('movements.oneCashMove')}</b></>
                      : <>{tr('movements.thereAre')} <b>{cassa.length} {tr('movements.cashMoves')}</b></>} {tr('movements.cashNoLane')} <b>{tr('movements.register')}</b> {tr('movements.registerLists')}
                  </span>
                </div>
              : arco
              ? <Scie
                  corsie={corsie}
                  arco={arco}
                  cancello={cancello}
                  selezione={selezione}
                  onSeleziona={tk => {
                    const scelta = tk === selezione ? null : tk;
                    setSelezione(scelta);
                    /* ⚠️ CASSA ∩ una corsia qualunque = ∅, SEMPRE: una corsia e'
                       un titolo, e un flusso di cassa non ne ha uno. Quindi
                       scegliere una corsia col filtro CASSA acceso porta a zero
                       righe per costruzione, non per caso — e il filtro si
                       toglie da se', invece di lasciare il PM in un vicolo. */
                    if (scelta && filtro === 'CASSA') setFiltro('TUTTI');
                    if (tk !== selezione) setVista('diario');
                  }}
                />
              : <div className="stato vuoto">
                  <span className="tt">{tr('movements.cannotDraw')}</span>
                  {/* ⚠️ Prima diceva «il REGISTRO le mostra tutte, nell'ordine in
                      cui sono arrivate»: due affermazioni, tutte e due false. Il
                      registro rende `filtrate`, che con un filtro attivo può non
                      mostrarne nessuna; e l'ordine non è quello d'arrivo — le
                      date illeggibili non ordinano affatto, `fondiRegistro` le
                      manda in fondo, in un gruppo che si dichiara. */}
                  <span className="tx">
                    {tr('movements.timelineHelp')} <b>{trades.length} {tr('movements.noReadableDates')}</b>{tr('movements.noTimeline')} <b>{tr('movements.register')}</b> {tr('movements.listsUnknown')} <b>{tr('movements.unknownDate')}</b>.
                  </span>
                </div>
          )}
          {registro.length > 0 && vista === 'diario' && (
            soloConTesto.length > 0
              ? <Diario righe={soloConTesto} cancello={cancello} />
              : <div className="stato vuoto">
                  <span className="tt">
                    {filtroAttivo ? tr('movements.noFilteredNotes') : tr('movements.noNotes')}
                  </span>
                  {/* ⚠️ «Con il filtro attuale» si diceva anche SENZA nessun
                      filtro: con TUTTI e nessuna corsia scelta, un archivio
                      senza commenti veniva incolpato di un filtro che non c'era */}
                  <span className="tx">
                    {tr('movements.ofThe')} {inSelezione.length} {tr('movements.rows')}{selezione ? tr('movements.ofTicker', {a: selezione}) : ''},
                    {' '}{conteggi.COMMENTO} {tr('movements.haveComment')}
                    {filtroAttivo
                      ? tr('movements.filterNoNotes')
                      : tr('movements.noWords')}
                  </span>
                </div>
          )}
        </div>
      </div>

      {/* Un AGGIORNA fallito con dati gia' a schermo era INVISIBILE: la
          tabella restava quella vecchia e nessuno lo diceva. Ora lo dice —
          e con role=alert lo dice anche a chi non guarda lo schermo. */}
      {/* Ogni archivio dichiara il proprio errore anche al primo caricamento.
          Solo una lettura precedente riuscita consente di parlare di righe
          potenzialmente vecchie; il successo dell'altro archivio non lo prova. */}
      {(err || errCassa) && registro.length > 0 && (
        <div className="stato ko avviso" role="alert">
          <span className="tx">
            {err && <><b>{tr(lettoT ? 'movements.staleSecurities' : 'movements.unreadSecurities')}</b> — {err}. </>}
            {errCassa && <><b>{tr(lettoC ? 'movements.staleCash' : 'movements.unreadCash')}</b> — {errCassa}. </>}
          </span>
        </div>
      )}
    </div>
  );
}
