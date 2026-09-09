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
import {
  Trade,
  raggruppaPerMese, arcoDi, costruisciCorsie,
  statoCancello, contaRealizzato, contaOreSegnaposto, ORA_SEGNAPOSTO,
  fondiRegistro, testiDiRiga, tickerDiRiga, contaFlussi,
} from '@/lib/movimenti';
import Registro from '@/components/movimenti/Registro';
import Scie, { contaVerbi } from '@/components/movimenti/Scie';
import Diario from '@/components/movimenti/Diario';
import './movimenti.css';

type Vista = 'registro' | 'scie' | 'diario';
type Filtro = 'TUTTI' | 'BUY' | 'TRIM' | 'ADD' | 'DIVIDEND' | 'CASSA' | 'COMMENTO';

const VISTE: { id: Vista; nome: string }[] = [
  { id: 'registro', nome: 'REGISTRO' },
  { id: 'scie', nome: 'SCIE' },
  { id: 'diario', nome: 'DIARIO' },
];

// Il tetto chiesto al backend. Serve come COSTANTE e non come letterale
// dentro la chiamata perche' va confrontato con le righe rese: se ne
// tornano esattamente LIMITE, quello che vedi e' una finestra e non lo
// storico — e va detto invece di far passare 100 per il totale
// (review 27/07, ALTA latente: oggi le righe sono 69).
const LIMITE = 100;
// Il tetto per i movimenti di cassa: 200, lo STESSO che chiede gia' il libretto
// di F7 (`TradeEntryPage.tsx:55`), tenuto identico di proposito — due tetti
// diversi sulla stessa tabella darebbero due verita' su due pagine. Il backend
// lo clampa a 1..1000 (`memory_db.py:1119`).
// ⚠️ SUI TRADE quel principio NON vale ed e' un debito, non una scelta: qui
// `LIMITE = 100`, in F7 `LIMITE_TRADES = 200` (`TradeEntryPage.tsx:49`).
// Oggi non si vede — il libro ha 75 righe — ma a 100 F14 dichiarera' una
// finestra e F7 no. Allineare i due cambia cosa il PM vede in questa pagina,
// quindi la decisione e' sua: voce aperta, non toccata qui.
const LIMITE_CASSA = 200;

export default function MovementsPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [cassa, setCassa] = useState<MovimentoCassa[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [errCassa, setErrCassa] = useState<string | null>(null);
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
  const motivoDi = (e: any) => e?.response?.data?.detail || e?.message || String(e);

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
    /* `allSettled` e non `all`: i due archivi sono INDIPENDENTI, e con `all`
       un rifiuto della cassa avrebbe buttato anche una lettura dei titoli
       andata a buon fine — cioe' avrebbe fatto sparire 75 righe vere per un
       guasto che non le riguarda. */
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
            setErr('lista `trades` assente o con elementi non-oggetto: il backend ha risposto 200 con una forma inattesa');
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
            setErrCassa('lista `movements` assente o con elementi non-oggetto: il backend ha risposto 200 con una forma inattesa');
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

  // ── derivazioni sui TITOLI: la cassa non entra qui, e non deve ──────
  // corsie, realizzato, cancello e arco parlano di titoli. Un flusso di cassa
  // non ha ticker, prezzo ne' realizzato: lasciarlo entrare qui lo farebbe
  // contare dove non c'entra, in silenzio.
  // ⚠️ `perDataDesc` non si chiama piu' da qui: l'ordinamento e' passato dentro
  // `fondiRegistro`, che deve ordinare DUE specie insieme. Il `useMemo` era
  // rimasto e riordinava 75 righe a ogni cambio di `trades` per nessuno.
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
  const nd = (v: number | string) => (lettoQualcosa ? v : 'n.d.');
  /** i numeri che parlano SOLO dei titoli non li sblocca la lettura della cassa */
  const ndT = (v: number | string) => (lettoT ? v : 'n.d.');
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
  const mesiVisti = useMemo(() => raggruppaPerMese(filtrate), [filtrate]);

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
        <div className="vst" ref={vstRef} role="group" aria-label="Vista dei movimenti" onKeyDown={frecce}>
          {VISTE.map(v => (
            <button
              key={v.id}
              type="button"
              aria-pressed={vista === v.id}
              onClick={() => setVista(v.id)}
            >
              {v.nome}
              {/* il numero sulla linguetta e' quello che QUELLA vista rende
                  davvero: prima diceva 69 mentre il registro, filtrato, ne
                  mostrava 9 (review 27/07, MEDIA). Le SCIE ignorano i filtri
                  per progetto, quindi il loro numero non cambia. */}
              <span className="k">
                {v.id === 'registro' ? nd(filtrate.length)
                  /* le CORSIE parlano solo di titoli: leggere la cassa non le
                     rende note, e il loro n.d. non deve spegnersi per sbaglio */
                  : v.id === 'scie' ? `${ndT(corsie.length)} CORSIE`
                  : `${nd(soloConTesto.length)} NOTE`}
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
            <i><span className="sw out" />USCITA {verbi.uscite}</i>
            <i><span className="sw dv" />DIVIDENDO {verbi.dividendi}</i>
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
                {f === 'COMMENTO' ? 'CON COMMENTO' : f} {conteggi[f] || 0}
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
                aria-label={`Togli la scelta della corsia ${selezione}`}
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
            <span className="k">Movimenti</span>
            <span className="v num">{nd(nMov)}</span>
            {/* Questo contatore ha cambiato SIGNIFICATO: da «mosse sui titoli»
                a «cose successe». Il sub dice di cosa e' fatto — e se un
                archivio non e' stato letto lo dice, invece di lasciar passare
                un parziale per un totale. */}
            {/* ⚠️ «MOSSE SUI TITOLI» e non «TITOLI»: due tessere piu' in la'
                «Titoli 29» conta i ticker DISTINTI. Con la stessa parola per due
                cose diverse nella stessa fascia, 75 e 29 sembravano lo stesso
                genere di numero e non tornavano. */}
            {lettoQualcosa && (
              <span className="sub">
                {totaleIntero
                  ? `${trades.length} MOSSE SUI TITOLI + ${cassa.length} DI CASSA`
                  : lettoT
                    ? `${trades.length} MOSSE SUI TITOLI · CASSA NON LETTA`
                    : `${cassa.length} DI CASSA · TITOLI NON LETTI`}
              </span>
            )}
          </div>
          <div><span className="k">Titoli</span><span className="v num">{ndT(corsie.length)}</span></div>
          <div>
            <span className="k">Arco</span>
            <span className="v num">{arco ? `${arco.giorni} gg` : 'n.d.'}</span>
          </div>
          <div>
            <span className="k">Flussi</span>
            {/* ⚠️ MAI verde/rosso su questi numeri: in questa fascia quei due
                colori sono il SEGNO DEL P&L (`.kpi .v.su/.giu`), e un prelievo
                non e' una perdita. Il verso lo dice il segno davanti al numero,
                che e' vero in ogni stato. */}
            {!lettoC
              ? <span className="v gate">n.d.</span>
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
                    NON È IL SALDO CASSA
                    {/* la seconda cosa che questo numero non e', accanto al
                        numero e non in un altro blocco della plancia */}
                    {finestraCassa && ' · SOLO LA FINESTRA'}
                    {flussi.ignoti > 0 && ` · ${flussi.ignoti} RIGHE NON CONTEGGIATE`}
                  </span>
                </>}
          </div>
          <div>
            <span className="k">Realizzato</span>
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
              ? <span className="v gate">n.d.</span>
              /* ⚠️ LA CURA §2.3, qui e' il punto dove mordeva: con `letto` da
                 solo, una lettura riuscita e VUOTA avrebbe fatto uscire «AL
                 CANCELLO» — perche' `contaRealizzato([])` torna null a causa
                 di `statoCancello([])` = 'chiuso', che su zero righe non ha
                 osservato nessun payload. E' la MEDIA audit/24 §B.6. */
              /* «SUI TITOLI» esplicito: la frase si legge mentre il registro
                 puo' avere righe di cassa a schermo, e senza il qualificatore
                 sembrerebbe negarle */
              : !osservabileT
              ? <span className="v gate">n.d. — NESSUNA RIGA SUI TITOLI DA OSSERVARE</span>
              : realizzato === null
              ? <span className="v gate">AL CANCELLO</span>
              : realizzato.stato === 'vuoto'
                ? <span className="v gate">n.d. — COLONNA VUOTA</span>
                : <>
                    <span className={'v num ' + (realizzato.somma >= 0 ? 'su' : 'giu')}>
                      {(realizzato.somma >= 0 ? '+' : '') + fmtNum(realizzato.somma, 2)} €
                    </span>
                    {/* la somma non compare MAI da sola: porta con se' quante
                        uscite la fanno e quanto pesa la piu' grossa. Sui dati
                        veri una sola uscita vale il 62% dei guadagni, e una
                        cifra nuda racconterebbe un risultato diffuso dove i
                        dati dicono una posizione sola. */}
                    <span className="sub">
                      {realizzato.n} USCITE · {realizzato.vinte}↑ {realizzato.perse}↓
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
              aria-label="Aggiorna i movimenti"
            >
              <RefreshCw size={12} className={loading ? 'spin' : ''} /> AGGIORNA
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
            {vista === 'registro' ? 'REGISTRO — TITOLI E CASSA, PER DATA VERA'
              /* le corsie vengono dai soli trade: le sblocca `lettoT`, non la
                 lettura della cassa (era `noto`, che da oggi comprenderebbe
                 anche un archivio che con le corsie non c'entra) */
              : vista === 'scie' ? (lettoT
                  ? `LE SCIE — ${corsie.length} CORSIE${arco ? ` × ${arco.giorni} GIORNI` : ''}`
                  : 'LE SCIE — CORSIE n.d.')
              : 'DIARIO — LE PAROLE DEL PM'}
          </h1>
          {/* Ogni numero di questa riga descrive la STESSA popolazione: prima
              "54 VOCI SU 69 MOVIMENTI" mescolava il filtrato col globale, e
              col filtro BUY diceva 44 su 69 dove le 44 erano solo dei BUY
              (review 27/07, MEDIA). Ora il denominatore e' dichiarato. */}
          <span className="side">
            {vista === 'scie'
              ? 'ALTEZZA = PESO DENTRO LA SUA CORSIA · FRA CORSIE NON SI CONFRONTA (VALUTE DIVERSE)'
              : !lettoQualcosa
                /* «0 RIGHE RESE SU 0 IN ARCHIVIO» a lettura fallita era un
                   conteggio affermato sull'ignoto (audit/24 B.6). Ma la frase
                   che lo curava era falsa a sua volta: dopo una lettura
                   riuscita e VUOTA seguita da un refresh fallito diceva
                   «ARCHIVIO NON LETTO», e l'archivio era stato letto. Ora
                   dipende da `letto` (un fatto avvenuto), non dall'esito
                   dell'ULTIMA lettura. */
                ? 'NESSUNO DEI DUE ARCHIVI È STATO LETTO — CONTEGGI n.d.'
              : vista === 'registro'
                /* ⚠️ «ALMENO» quando una finestra è piena. Senza, la stessa riga
                   diceva «102 IN ARCHIVIO» e due frasi dopo «potrebbero
                   essercene altri»: due metà che si smentiscono. Il backend non
                   consegna un totale — serve la finestra e basta
                   (`bellomberg_api.py:1894`, `count = len(rows)`). F7 lo scrive
                   già così: «almeno N movimenti» (`TradeEntryPage.tsx:925`). */
                ? `${filtrate.length} RIGHE RESE SU ${finestra || finestraCassa ? 'ALMENO ' : ''}${nMov} IN ARCHIVIO`
                  /* ⚠️ La composizione «(75 SUI TITOLI + 2 DI CASSA)» NON sta
                     qui quando entrambi sono letti: la dice gia' il `sub` del
                     KPI Movimenti, sullo stesso schermo. Questa riga ha
                     `text-overflow:ellipsis` e a `terzo` (1706px) ha 1302px
                     utili: misurata, la parentesi costava **203px**, e i
                     puntini passavano da 7 a 35 caratteri — mangiandosi
                     l'avviso ⚠ sulle ore segnaposto, cioe' un buco DICHIARATO
                     che diventava illeggibile. Nello stato PARZIALE resta,
                     perche' li' e' l'informazione piu' importante della riga. */
                  + (totaleIntero
                      ? ''
                      : lettoT
                        ? ' (SOLI TITOLI: LA CASSA NON È STATA LETTA)'
                        : ' (SOLA CASSA: I TITOLI NON SONO STATI LETTI)')
                  /* dallo STATO, non dal confronto dei conteggi: un filtro che
                     non scarta nulla è attivo lo stesso, e dirlo spento era
                     falso (es. CON COMMENTO con tutte le righe commentate) */
                  + (filtroAttivo ? ' · FILTRO ATTIVO' : '')
                : `${soloConTesto.length} VOCI CON COMMENTO SU ${filtrate.length} RIGHE RESE`}
            {/* la concentrazione sta ACCANTO alla somma, mai la somma da sola:
                sui dati veri una sola uscita fa il 62% dei guadagni */}
            {realizzato && realizzato.stato === 'ok' && vista !== 'scie'
              && ` · ${realizzato.n} USCITE: ${realizzato.vinte} IN GUADAGNO, ${realizzato.perse} IN PERDITA`
                 + (realizzato.pari > 0 ? `, ${realizzato.pari} IN PARI` : '')
                 + (realizzato.quotaMaggiore > 0
                    ? ` · ${realizzato.tickerMaggiore} DA SOLA VALE IL ${Math.round(realizzato.quotaMaggiore * 100)}% DEI GUADAGNI`
                    : '')}
            {/* due archivi, due tetti: si dichiarano SEPARATI perche' si
                riempiono in momenti diversi, e dire «finestra» senza dire di
                quale dei due lascerebbe il PM a indovinare quale meta' e' monca */}
            {finestra && ` · ⚠ FINESTRA TITOLI: IL BACKEND NE CONSEGNA AL MASSIMO ${LIMITE}, POTREBBERO ESSERCENE ALTRI`}
            {finestraCassa && ` · ⚠ FINESTRA CASSA: AL MASSIMO ${LIMITE_CASSA} MOVIMENTI, POTREBBERO ESSERCENE ALTRI`}
            {/* ⚠️ LA CURA §2.3: questa e' un'affermazione sulla FORMA del
                payload dei trade, quindi vuole `osservabile` e non `letto` —
                su zero righe `statoCancello([])` dice 'chiuso' senza aver
                osservato niente (audit/24 §B.6). */}
            {osservabileT && cancello === 'chiuso' && vista !== 'scie'
              && ' · P&L REALIZZATO NON CONSEGNATO DA GET /trades (9 COLONNE SU 13)'}
            {/* l'ora non e' misurata dappertutto: su una parte delle righe e'
                il segnaposto dell'importatore, e spacciarla per un orario
                sarebbe una precisione inventata */}
            {oreFinte > 0
              && ` · ⚠ ${oreFinte} RIGHE SU ${trades.length} PORTANO ${ORA_SEGNAPOSTO} ESATTE: È IL SEGNAPOSTO DELL'IMPORT, NON UN ORARIO MISURATO`}
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
              <span className="tt">Caricamento</span>
              <span className="tx">Lettura di GET /trades e GET /cash/movements sul backend.</span>
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
                {err && errCassa ? 'Storico non disponibile' : 'Storico incompleto'}
              </span>
              <span className="tx">
                {err && <>I movimenti sui titoli — il backend ha risposto: <b>{err}</b>. </>}
                {!err && lettoT && (
                  <>I movimenti sui titoli sono stati <b>letti</b>, e non ce n'è nessuno. </>
                )}
                {errCassa && <>La cassa — il backend ha risposto: <b>{errCassa}</b>. </>}
                {!errCassa && lettoC && (
                  <>La cassa è stata <b>letta</b>, e non ha movimenti. </>
                )}
                Le righe degli archivi in errore <b>non sono perse</b>: è la lettura
                ad essere fallita. Riprova con AGGIORNA.
                {/* ⚠️ Senza questa frase, la fascia scriveva MOVIMENTI 0 a 17px nel
                    colore del dato mentre qui si diceva «non disponibile» — cioè
                    la resa che la review del 27/07 aveva bollato come ALTA, che
                    torna raggiungibile proprio perché `letto` non torna indietro.
                    Lo zero È l'ultima misura, ma va detto di quando è. */}
                {lettoQualcosa && (
                  <> I conteggi in testata sono quelli dell'<b>ultima lettura
                  riuscita</b>: possono essere vecchi.</>
                )}
              </span>
            </div>
          )}

          {vuoto && (
            <div className="stato vuoto">
              <span className="tt">Nessun movimento registrato</span>
              <span className="tx">
                Il backend ha risposto correttamente con una lista vuota.
                Questo è un archivio vuoto, non un errore.
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
                  <span className="tt">Nessun movimento con questo filtro</span>
                  {/* lo stesso qualificatore della riga di stato: senza, questa
                      frase dava per «l'archivio» quello che è solo la metà letta */}
                  <span className="tx">
                    {totaleIntero
                      ? <>L'archivio ha {nMov} movimenti</>
                      : lettoT
                        ? <>Dei movimenti letti ce ne sono {nMov} (soli titoli: la cassa
                            non è stata letta)</>
                        : <>Dei movimenti letti ce ne sono {nMov} (sola cassa: i titoli
                            non sono stati letti)</>}: nessuno passa il filtro
                    attivo{selezione ? ` (${selezione})` : ''}. È il filtro, non un guasto —
                    togli il filtro per rivederli tutti.
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
                  <span className="tt">Le scie disegnano i titoli</span>
                  <span className="tx">
                    A schermo {cassa.length === 1
                      ? <>c'è <b>1 movimento di cassa</b></>
                      : <>ci sono <b>{cassa.length} movimenti di cassa</b></>} e nessuna
                    riga sui titoli: un flusso di cassa non ha un titolo, quindi non ha
                    una corsia. Il <b>REGISTRO</b> li elenca comunque.
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
                  <span className="tt">Le scie non si possono disegnare</span>
                  {/* ⚠️ Prima diceva «il REGISTRO le mostra tutte, nell'ordine in
                      cui sono arrivate»: due affermazioni, tutte e due false. Il
                      registro rende `filtrate`, che con un filtro attivo può non
                      mostrarne nessuna; e l'ordine non è quello d'arrivo — le
                      date illeggibili non ordinano affatto, `fondiRegistro` le
                      manda in fondo, in un gruppo che si dichiara. */}
                  <span className="tx">
                    Le scie posano ogni mossa lungo un arco di tempo, e l'arco si
                    costruisce sulle date: delle <b>{trades.length} righe sui titoli
                    nessuna porta una data leggibile</b>, quindi non c'è un arco su cui
                    posarle. Non è un archivio vuoto — il <b>REGISTRO</b> le elenca
                    comunque, in fondo, sotto <b>DATA NON LEGGIBILE</b>.
                  </span>
                </div>
          )}
          {registro.length > 0 && vista === 'diario' && (
            soloConTesto.length > 0
              ? <Diario righe={soloConTesto} cancello={cancello} />
              : <div className="stato vuoto">
                  <span className="tt">
                    {filtroAttivo ? 'Nessuna nota con questo filtro' : 'Nessuna nota'}
                  </span>
                  {/* ⚠️ «Con il filtro attuale» si diceva anche SENZA nessun
                      filtro: con TUTTI e nessuna corsia scelta, un archivio
                      senza commenti veniva incolpato di un filtro che non c'era */}
                  <span className="tx">
                    Delle {inSelezione.length} righe{selezione ? ` di ${selezione}` : ''},
                    {' '}{conteggi.COMMENTO} hanno un commento.
                    {filtroAttivo
                      ? ' Con il filtro attuale non ne resta nessuna.'
                      : ' Nessuna di loro porta parole.'}
                  </span>
                </div>
          )}
        </div>
      </div>

      {/* Un AGGIORNA fallito con dati gia' a schermo era INVISIBILE: la
          tabella restava quella vecchia e nessuno lo diceva. Ora lo dice —
          e con role=alert lo dice anche a chi non guarda lo schermo. */}
      {/* ⚠️ QUESTA GUARDIA È IL REPERTO PIÙ GRAVE DELLA REVIEW, e la sbagliavo io.
          Era `(err || errCassa) && registro.length > 0`, e `registro.length > 0`
          può essere soddisfatto INTERAMENTE dall'altro archivio. Stato reale, al
          PRIMO caricamento: `/trades` risponde con 75 righe, `/cash/movements`
          cade. Uscivano tre affermazioni false in una frase — «Aggiornamento»
          (non c'era stato nessun aggiornamento), «restano le righe dell'ultima
          lettura riuscita» (della cassa non ce n'era MAI stata una), e il
          puntatore «(la cassa)» su righe che a schermo non esistevano — mentre
          la riga di stato, pochi pixel sopra, diceva correttamente «LA CASSA NON
          È STATA LETTA».
          La domanda giusta non è «ci sono righe a schermo?»: è «questo archivio
          era già stato letto una volta?». Se sì, quello che si vede è vecchio;
          se no, non c'è niente di vecchio da dichiarare — c'è un archivio mai
          letto, e a dirlo è già la riga di stato. */}
      {((err && lettoT) || (errCassa && lettoC)) && registro.length > 0 && (
        <div className="stato ko avviso" role="alert">
          <span className="tx">
            <b>Rilettura fallita</b> — {err && lettoT && <>titoli: {err}. </>}
            {errCassa && lettoC && <>cassa: {errCassa}. </>}
            A schermo restano{' '}
            {err && lettoT && errCassa && lettoC
              ? <>tutte le righe dell'ultima lettura riuscita: <b>possono essere vecchie</b></>
              : err && lettoT
                ? <>i movimenti sui titoli dell'ultima lettura riuscita: <b>possono essere
                    vecchi</b> (la cassa è fresca)</>
                : <>i movimenti di cassa dell'ultima lettura riuscita: <b>possono essere
                    vecchi</b> (i titoli sono freschi)</>}.
          </span>
        </div>
      )}
    </div>
  );
}
