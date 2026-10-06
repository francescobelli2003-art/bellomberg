// F14 MOVIMENTI · stile Nuova (05/10/2026): mockup approvato `outputs/movimenti-nuova/mockup.html`,
// variante A «dettaglio fisso». Le tre viste di prima (Registro | Scie | Diario) diventano una pagina sola:
// Attività per mese | Realizzato in alto, Registro | Dettaglio sotto. Le Scie restano come «Storia del
// titolo» nel dettaglio, il Diario come commento completo del movimento scelto. Dal 06/10/2026 il Diario torna
// anche come vista: interruttore Elenco | Diario nell'intestazione, i soli movimenti commentati in fila.
//
// DUE FETCH, DUE ARCHIVI, invariati: `GET /trades` e `GET /cash/movements` falliscono in modo
// indipendente e ogni buco si dichiara (regola 14/07). Filtri, mese, titolo e ricerca non chiamano
// niente: sono derivazioni pure di lib/movimenti.ts e pages/movimenti/calcoli.ts.
//
// IL REGISTRO UNICO (impianto 2, 21/08): i flussi di cassa entrano FRA i trade, sulla stessa linea del
// tempo. Le derivazioni sui TITOLI (corsie, realizzato, cancello, arco) restano sui soli `Trade[]`.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import type { MovimentoCassa } from '@/lib/api';
import { leggiDetail } from '@/lib/quota';
import { caricaLoghi } from '@/lib/loghi-remoti';
import { useLingua, useT } from '@/i18n/provider';
import { localeDi } from '@/i18n/lingua';
import ModernPage from '@/components/ModernPage';
import NewInterfaceBoundary from '@/components/NewInterfaceBoundary';
import {
  Trade,
  raggruppaPerMese, arcoDi, costruisciCorsie,
  statoCancello, contaRealizzato, contaOreSegnaposto,
  fondiRegistro, tickerDiRiga, contaFlussi,
} from '@/lib/movimenti';
import { attivitaMensile, chiaveRiga, contributi as contaContributi, FILTRI, passaFiltro, passaRicerca, righeDiario, type FiltroMov, type VistaMov } from './movimenti/calcoli';
import VistaMovimenti, { type AzioniMovimenti, type DatiMovimenti, type RigaVista } from './movimenti/VistaMovimenti';
import '@/components/nuova/nuova.css';
import './movimenti-nuova.css';

// Il limite della richiesta non equivale al totale dello storico.
const LIMITE = 100;
// I limiti trade delle due pagine differiscono: dichiarare la finestra quando satura.
const LIMITE_CASSA = 200;

export default function MovementsPage() {
  const tr = useT();
  const lingua = useLingua();
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
  const [filtro, setFiltro] = useState<FiltroMov>('TUTTI');
  /** il titolo scelto (dai contributi al realizzato): restringe il registro a quel ticker */
  const [titolo, setTitolo] = useState<string | null>(null);
  const [mese, setMese] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [selK, setSelK] = useState<string | null>(null);
  /** ora locale dell'ultima lettura riuscita di almeno un archivio */
  const [lettoAlle, setLettoAlle] = useState<string | null>(null);
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
            segnaLettura();
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
            segnaLettura();
          }
        }
      })
      .finally(() => {
        if (vivo.current && mio === gen.current) setLoading(false);
      });
  };
  const segnaLettura = () => setLettoAlle(new Date().toLocaleTimeString(localeDi(lingua), { hour: '2-digit', minute: '2-digit' }));
  useEffect(carica, []);

  // Arco, corsie e realizzato si derivano dai soli trade: la cassa non ha ticker.
  const arco = useMemo(() => arcoDi(trades), [trades]);
  const corsie = useMemo(() => (arco ? costruisciCorsie(trades, arco) : []), [trades, arco]);
  const cancello = useMemo(() => statoCancello(trades), [trades]);
  const realizzato = useMemo(() => contaRealizzato(trades), [trades]);
  const contributi = useMemo(() => contaContributi(trades), [trades]);
  const oreFinte = useMemo(() => contaOreSegnaposto(trades), [trades]);
  const nTitoli = useMemo(() => new Set(trades.map(t => t.ticker)).size, [trades]);
  const tickerLoghi = useMemo(() => [...new Set(trades.map(t => t.ticker))].sort().join(','), [trades]);
  useEffect(() => { if (tickerLoghi) caricaLoghi(tickerLoghi.split(',')); }, [tickerLoghi]);

  // ── il registro unico: titoli e cassa sulla stessa linea del tempo ──
  const registro = useMemo(() => fondiRegistro(trades, cassa), [trades, cassa]);
  const flussi = useMemo(() => contaFlussi(cassa), [cassa]);
  const tutte = useMemo<RigaVista[]>(() => registro.map((r, i) => ({ r, k: chiaveRiga(r, i) })), [registro]);
  const chiaveDiTrade = useMemo(() => {
    const m = new Map<Trade, string>();
    for (const v of tutte) if (v.r.specie === 'titolo') m.set(v.r.t, v.k);
    return m;
  }, [tutte]);
  const attivita = useMemo(() => attivitaMensile(registro), [registro]);

  // ⚠️ I conteggi dei filtri si fanno sulla popolazione GIA' ristretta da titolo, mese e ricerca, non
  // su tutto il portafoglio: un chip che promette «Dividendi 2» e consegna zero righe e' un numero che
  // mente (review 27/07, MEDIA). Con un titolo scelto le righe di cassa ESCONO: un flusso non ha titolo.
  const ristrette = useMemo(() => tutte.filter(v =>
    (!titolo || tickerDiRiga(v.r) === titolo)
    && (!mese || v.r.quando.slice(0, 7) === mese)
    && passaRicerca(v.r, q)), [tutte, titolo, mese, q]);
  const conteggi = useMemo(() => {
    const c = {} as Record<FiltroMov, number>;
    for (const f of FILTRI) c[f] = ristrette.filter(v => passaFiltro(v.r, f)).length;
    return c;
  }, [ristrette]);
  const righe = useMemo(() => ristrette.filter(v => passaFiltro(v.r, filtro)), [ristrette, filtro]);
  // I mesi si contano sulle righe RESE: un separatore che dice «23 movimenti» sopra nove righe mente.
  const mesi = useMemo(() => raggruppaPerMese(righe.map(v => v.r)), [righe, tr]);
  /* IL DIARIO (06/10/2026): torna la vista di prima del restyling, «le parole del PM» in fila. Stessi dati, nessuna
     lettura in più: sono le righe di `tutte` che hanno un testo, nell'ordine del registro (dalla più recente).
     ⚠️ Hook in CODA al componente: i test SSR ricostruiscono stato e memo PER INDICE. */
  const [vista, setVista] = useState<VistaMov>('elenco');
  const diario = useMemo(() => righeDiario(tutte), [tutte]);
  // il dettaglio segue la lista che si vede: nel Diario una riga nascosta dai filtri resta sceglibile
  const lista = vista === 'diario' ? diario : righe;
  const sel = lista.find(v => v.k === selK) ?? lista[0] ?? null;

  /** quante righe ci sono in archivio: si sommano solo gli archivi LETTI */
  const nMov = (lettoT ? trades.length : 0) + (lettoC ? cassa.length : 0);
  // se il backend consegna esattamente il tetto chiesto, quella NON e' la storia: e' la finestra piu'
  // recente, e va dichiarato. Due archivi, due finestre.
  const finestra = trades.length >= LIMITE;
  const finestraCassa = cassa.length >= LIMITE_CASSA;

  const dati: DatiMovimenti = {
    loading, err, errCassa, lettoT, lettoC, lettoAlle, trades, cassa, tutte, righe, mesi, conteggi,
    filtro, mese, titolo, q, sel, vista, diario, nMov, nTitoli, finestra, finestraCassa, limite: LIMITE, limiteCassa: LIMITE_CASSA,
    flussi, realizzato, cancello,
    /* LA CURA §2.3: le affermazioni sulla FORMA del payload dei trade vogliono almeno una riga osservata */
    osservabileT: lettoT && trades.length > 0,
    arco, corsie, oreFinte, attivita, contributi,
    chiaveDi: t => chiaveDiTrade.get(t),
  };
  const azioni: AzioniMovimenti = {
    aggiorna: carica,
    // ⚠️ CASSA ∩ un titolo = ∅ per costruzione: scegliere Cassa toglie il titolo, e viceversa
    filtro: f => { setFiltro(f); if (f === 'CASSA') setTitolo(null); },
    // mese e titolo restringono il REGISTRO: sceglierli dal Diario riporta all'elenco, dove il filtro si vede
    mese: k => { setMese(k); if (k) setVista('elenco'); },
    titolo: t => { setTitolo(t); if (t && filtro === 'CASSA') setFiltro('TUTTI'); if (t) setVista('elenco'); },
    vista: setVista,
    cerca: setQ,
    scegli: k => {
      // dalla storia del titolo e dal Diario si puo' scegliere un movimento che i filtri nascondono: li si toglie,
      // cosi' tornando all'elenco la riga scelta c'e' ancora (e i filtri tolti si vedono, tutti su «Tutti»)
      if (!righe.some(v => v.k === k)) {
        const v = tutte.find(x => x.k === k);
        setFiltro('TUTTI'); setMese(null); setQ('');
        if (titolo && v && tickerDiRiga(v.r) !== titolo) setTitolo(null);
      }
      setSelK(k);
    },
    azzera: () => { setFiltro('TUTTI'); setMese(null); setQ(''); setTitolo(null); },
  };

  return <ModernPage page="movements" render={() => (
    <NewInterfaceBoundary language={lingua}>
      <VistaMovimenti d={dati} a={azioni} />
    </NewInterfaceBoundary>
  )} />;
}
