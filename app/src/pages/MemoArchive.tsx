import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';
import { useLingua } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import { Bellomberg, type Memo, type Decision, type MemoSearchHit } from '@/lib/api';
import { rendiMemo, sezioniDi, citazioni, type MemoSezione } from '@/lib/memo-md';
import ModernPage from '@/components/ModernPage';
import ElencoRun, { type Campione } from './memo/ElencoRun';
import Lettore, { type VoceIndice } from './memo/Lettore';
import { Esiti, Corredo } from './memo/Esiti';
import Ricerca from './memo/Ricerca';
import {
  ORD, ts, dcfDi, segnalato, tabellaAzioni, bluf, decisioniPrioritarie, obiezione, mandato, titoloLeggibile,
  RIGA_MANDATO, SEZIONI_AUTOMATICHE, type Esito,
} from './memo/logica';
import { parole } from './memo/parole';
import '@/components/nuova/nuova.css';
import './memo-nuova.css';

/* ============================================================================
   Archivio memo — Nuova (mockup approvato 05/10/2026, variante A:
   outputs/archivio-memo-nuova/mockup.html). Tre colonne: run del Comitato | memo (sintesi,
   decisioni per il PM, indice, testo integrale) | esiti delle decisioni e corredo.

   Dati e comportamento sono quelli di F9 v3, invariati:
     · GET /memos?limit=50 per l'elenco, GET /memos?include_empty=true (200) per il campione
       delle righe senza contenuto, GET /decisions (500) per gli esiti;
     · il testo integrale (GET /memos/{id}) si carica al click e resta in cache;
     · la ricerca semantica (GET /memos/search) si lancia SOLO con Invio;
     · un errore del backend NON e' «nessun memo»: si dichiara con «Riprova».
   Tutto lo stato sta qui (l'harness SSR dei test indicizza gli hook in ordine): le viste in
   ./memo sono presentazionali.
   ========================================================================== */

function dettaglioErrore(e: any): string {
  return leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || leggiDetail(e);
}
const SEZIONI_SALTATE = ['ACTION TABLE', 'BLUF'];

export default function MemoArchive() {
  useLingua();   // ridisegna al cambio lingua
  const w = parole();
  const [memos, setMemos] = useState<Memo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Count only row-level evidence within the returned sample. Different
  // pagination limits cannot establish how many runs failed.
  const [campione, setCampione] = useState<Campione | null>(null);
  const [righeDbErr, setRigheDbErr] = useState<string | null>(null);

  const [dec, setDec] = useState<Decision[]>([]);
  const [decErr, setDecErr] = useState<string | null>(null);
  const [decLoading, setDecLoading] = useState(true);

  const [selId, setSelId] = useState<number | null>(null);
  const [testi, setTesti] = useState<Record<number, string>>({});
  const [testoErr, setTestoErr] = useState<{ id: number; msg: string } | null>(null);
  const [caricando, setCaricando] = useState(false);
  const [sezOn, setSezOn] = useState<string | null>(null);
  const [cerca, setCerca] = useState(false);

  // ricerca semantica (campo in testata)
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MemoSearchHit[] | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const [errS, setErrS] = useState<string | null>(null);
  const [cercando, setCercando] = useState(false);
  const [hitSel, setHitSel] = useState(0);
  const [qCercata, setQCercata] = useState<string | null>(null);
  // colonna destra
  const [aperta, setAperta] = useState<number | null>(null);
  const [tutteFonti, setTutteFonti] = useState(false);

  const docRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    Bellomberg.memos(50)
      .then(r => {
        if (!Array.isArray(r?.memos)) { setErr(''); return; }
        const ms = r.memos.slice().sort((a, b) => ts(b.timestamp) - ts(a.timestamp));
        setMemos(ms);
        setSelId(prev => (prev !== null && ms.some(m => m.id === prev)) ? prev : (ms[0]?.id ?? null));
      })
      .catch(e => setErr(dettaglioErrore(e)))
      .finally(() => setLoading(false));

    setRigheDbErr(null); setCampione(null);
    Bellomberg.memosAll(200)
      .then(r => {
        const rows = r?.memos;
        if (!Array.isArray(rows)) { setRigheDbErr(''); return; }
        const declared = rows.every(m => typeof m.has_content === 'boolean' && typeof m.pdf_available === 'boolean');
        setCampione({ rows: rows.length, excluded: declared ? rows.filter(m => !m.has_content && !m.pdf_available).length : null });
      })
      .catch(e => { setCampione(null); setRigheDbErr(dettaglioErrore(e)); });

    setDecErr(null); setDecLoading(true);
    Bellomberg.decisions(undefined, 500)
      .then(r => {
        if (!Array.isArray(r?.decisions)) { setDec([]); setDecErr(''); return; }
        setDec(r.decisions);
      })
      .catch(e => { setDec([]); setDecErr(dettaglioErrore(e)); })
      .finally(() => setDecLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  // il testo integrale si carica al click e resta in cache; un errore resta legato al SUO memo
  useEffect(() => {
    if (selId === null || testi[selId] !== undefined) { setCaricando(false); return; }
    let active = true;
    setCaricando(true); setTestoErr(null);
    Bellomberg.memoById(selId)
      .then(m => {
        if (!active) return;
        if (!m || !(typeof m.full_markdown === 'string' || m.full_markdown === null)) { setTestoErr({ id: selId, msg: '' }); return; }
        setTesti(t => ({ ...t, [selId]: m.full_markdown || '' }));
      })
      .catch(e => { if (active) setTestoErr({ id: selId, msg: dettaglioErrore(e) }); })
      .finally(() => { if (active) setCaricando(false); });
    return () => { active = false; };
  }, [selId, testi]);

  const decPerMemo = useMemo(() => {
    const m = new Map<number, Decision[]>();
    dec.forEach(d => {
      if (d.memo_id === null || d.memo_id === undefined) return;
      const a = m.get(d.memo_id) || []; a.push(d); m.set(d.memo_id, a);
    });
    m.forEach(a => a.sort((x, y) => ORD.indexOf(x.status as Esito) - ORD.indexOf(y.status as Esito)));
    return m;
  }, [dec]);

  const sel = memos.find(m => m.id === selId) || null;
  const md = selId !== null ? testi[selId] : undefined;
  const erroreTesto = testoErr && testoErr.id === selId ? testoErr.msg : null;
  const decSel = (selId !== null && decPerMemo.get(selId)) || [];

  const lingua = linguaCorrente();
  const reso = useMemo(() => md ? rendiMemo(md, SEZIONI_SALTATE, {
    chiudi: SEZIONI_AUTOMATICHE, saltaRiga: r => RIGA_MANDATO.test(r.trim()), obiezione, formaTitolo: titoloLeggibile,
  }) : null, [md, lingua]);
  const sezioni: MemoSezione[] = reso ? reso.sezioni : (md ? sezioniDi(md) : []);
  const azioni = useMemo(() => md ? tabellaAzioni(md, decSel) : [], [md, decSel]);
  const fonti = useMemo(() => md ? citazioni(md) : [], [md]);
  const sintesi = useMemo(() => md ? bluf(md) : [], [md]);
  const priorita = useMemo(() => md ? decisioniPrioritarie(md) : [], [md]);
  const mand = useMemo(() => md ? mandato(md) : null, [md]);

  // indice: sezioni di livello 2; il BLUF porta al riquadro di sintesi, la action table
  // (gia' nella colonna degli esiti) e i controlli automatici restano fuori
  const indice: VoceIndice[] = sezioni.filter(s => s.livello === 2).flatMap(s => {
    const T = s.titolo.toUpperCase();
    if (T.startsWith('ACTION TABLE') || SEZIONI_AUTOMATICHE.some(a => T.startsWith(a))) return [];
    if (T.startsWith('BLUF')) return sintesi.length ? [{ id: s.id, titolo: s.titolo, bersaglio: 'memo-sintesi' }] : [];
    return [{ id: s.id, titolo: s.titolo, bersaglio: s.id }];
  });

  const dcfInvalid = memos.some(m => dcfDi(m) === null);
  const totDcf = dcfInvalid ? null : memos.reduce((s, m) => s + dcfDi(m)!.length, 0);
  const totFlag = dcfInvalid ? null : memos.reduce((s, m) => s + dcfDi(m)!.filter(segnalato).length, 0);
  const selDcf = sel ? dcfDi(sel) : [];
  const conEsito = dec.filter(d => d.outcome_pct !== null && d.outcome_pct !== undefined).length;

  const vaiA = (v: VoceIndice) => {
    setSezOn(v.id);
    const box = docRef.current, el = box?.querySelector<HTMLElement>(`#${v.bersaglio}`);
    if (el && el.tagName === 'DETAILS') (el as HTMLDetailsElement).open = true;
    el?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };
  const scegliMemo = useCallback((id: number) => {
    setSelId(id); setAperta(null); setSezOn(null); setTutteFonti(false);
    if (docRef.current) docRef.current.scrollTop = 0;
  }, []);

  const lancia = useCallback(() => {
    const testo = q.trim();
    if (!testo) return;
    setCercando(true); setErrS(null); setQCercata(testo);
    const t0 = performance.now();
    Bellomberg.memosSearch(testo, 8)
      .then(r => {
        if (!Array.isArray(r?.results)) { setHits(null); setErrS(''); return; }
        setHits(r.results); setHitSel(0); setMs(Math.round(performance.now() - t0));
      })
      .catch(e => { setHits(null); setErrS(dettaglioErrore(e)); })
      .finally(() => setCercando(false));
  }, [q]);
  const apriRisultato = useCallback((id: number) => { scegliMemo(id); setCerca(false); inputRef.current?.blur(); }, [scegliMemo]);
  const tasto = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { e.preventDefault(); setCerca(false); return; }
    if (!cerca) setCerca(true);
    const lista = hits || [];
    if (e.key === 'ArrowDown' && lista.length) { e.preventDefault(); setHitSel(s => Math.min(lista.length - 1, s + 1)); }
    if (e.key === 'ArrowUp' && lista.length) { e.preventDefault(); setHitSel(s => Math.max(0, s - 1)); }
    if (e.key === 'Enter') {
      e.preventDefault();
      // Invio apre il passaggio scelto; se la domanda e' cambiata, prima la rilancia
      if (lista.length && qCercata === q.trim()) apriRisultato(lista[Math.min(hitSel, lista.length - 1)].memo_id);
      else lancia();
    }
  };

  // CTRL+SHIFT+F apre la ricerca. NON e' CTRL+K: quella e' gia' la palette dei comandi
  // (CommandPalette.tsx, globale); Layout.tsx si prende i tasti F1..F17 nudi.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault(); setCerca(true); inputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);
  // un clic fuori dal campo chiude il pannello dei risultati: un solo ascoltatore per la vita della
  // pagina (aprire e chiudere il pannello non aggiunge ne' toglie ascoltatori globali)
  useEffect(() => {
    if (typeof document === 'undefined' || typeof document.addEventListener !== 'function') return;
    const h = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setCerca(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const errore = err !== null;
  const loc = localeDi(lingua);
  const ultima = memos[0] ? new Date(memos[0].timestamp) : null;

  return (
    <ModernPage page="memos" render={() => (
      <div className="bbn-memo bbn-font" data-qa="memo-archive">
        <header className="mm-top">
          <h1>{w.title}</h1>
          <span className="bbn-chip" data-qa="memo-count">{errore ? w.readableNd : loading ? '…' : w.readable(memos.length)}</span>
          {!errore && ultima && Number.isFinite(ultima.getTime()) && (
            <span className="bbn-chip"><i className="mm-dot is-ok" aria-hidden="true" />{w.lastRun}{' '}
              <b>{ultima.toLocaleDateString(loc, { day: 'numeric', month: 'short' })} · {ultima.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' })}</b></span>
          )}
          {!errore && (
            <span className="bbn-chip" data-qa="memo-decisions" title={dcfInvalid ? w.dcfInvalid : totDcf !== null ? w.dcfChipHint(totDcf, totFlag || 0) : undefined}>
              {decErr !== null ? w.decisionsNd : decLoading ? '…' : <b>{w.decisions(dec.length)}</b>}
              {dcfInvalid ? <> · {w.dcfInvalid}</> : totFlag ? <> · {w.dcfFlagged(totFlag)}</> : null}
            </span>
          )}
          <span className="bbn-grow" />
          <Ricerca aperta={cerca} q={q} hits={hits} ms={ms} errS={errS} cercando={cercando} sel={hitSel} memos={memos}
            boxRef={boxRef} inputRef={inputRef} onApri={() => setCerca(true)} onQ={setQ} onTasto={tasto}
            onScegli={setHitSel} onOpen={apriRisultato} />
        </header>

        {errore && (
          // Buco DICHIARATO: un errore backend NON e' «nessun memo» (il falso vuoto invitava a una run da ~10 EUR)
          <p className="mm-note is-bad" role="alert" data-qa="memo-archive-error">
            <TriangleAlert size={15} aria-hidden="true" />
            <span className="txt"><b>{w.archiveErr}</b> {w.archiveErrBody(err || w.unknown)}</span>
            <button type="button" className="bbn-btn is-sm" onClick={load}><RefreshCw size={13} aria-hidden="true" />{w.retry}</button>
          </p>
        )}

        <div className="mm-grid">
          <ElencoRun memos={memos} loading={loading} errore={errore} selId={selId} onSelect={scegliMemo}
            decPerMemo={decPerMemo} decErr={decErr} decLoading={decLoading} campione={campione} righeDbErr={righeDbErr} />
          <Lettore sel={errore ? null : sel} md={md} testoErr={erroreTesto} caricando={caricando}
            nodi={reso ? reso.nodi : null} sezioni={sezioni} indice={indice} sintesi={sintesi} priorita={priorita}
            mandato={mand} sezOn={sezOn} onVai={vaiA} docRef={docRef} vuoto={errore ? w.noMemoErr : undefined} />
          <div className="mm-col-r">
            <Esiti sel={errore ? null : sel} md={md} testoErr={erroreTesto} azioni={azioni} decSel={decSel}
              decErr={decErr} decLoading={decLoading} aperta={aperta} onApri={setAperta} />
            <Corredo sel={errore ? null : sel} md={md} dec={dec} decErr={decErr} decLoading={decLoading} conEsito={conEsito}
              selDcf={selDcf} fonti={fonti} tutteFonti={tutteFonti} onTutteFonti={() => setTutteFonti(v => !v)} />
          </div>
        </div>
      </div>
    )} />
  );
}
