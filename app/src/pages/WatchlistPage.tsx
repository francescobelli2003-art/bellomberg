import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, type FavCompany, type FilingOverviewTitolo, type MktQuote, type MktSearchHit } from '@/lib/api';
import { useT } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import ModernPage from '@/components/ModernPage';
import { parole } from './preferiti/parole';
import { elenco, TUTTI, type Ordine } from './preferiti/calcoli';
import VistaPreferiti from './preferiti/VistaPreferiti';
import './preferiti-nuova.css';

/** Preferiti (palette Nuova): elenco dei titoli seguiti con interessi per settore, ordinamento e
 *  filtro, e dettaglio del titolo scelto (prezzo e intervallo a 52 settimane, dati chiave, nota del
 *  PM letta dal Consigliere, stato del filing). Gli stati restano tutti qui, chiamati sempre nello
 *  stesso ordine: le viste sono solo presentazione. */

const erroreDi = (e: any) => leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || String(e);
const ANNULLA_MS = 6000;
type Avviso = { tono: 'ok' | 'bad'; testo: string; annulla?: boolean };
const oraOra = () => new Date().toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' });

export default function WatchlistPage() {
  useT();
  const w = parole();
  const [favs, setFavs] = useState<FavCompany[] | null>(null);
  const [quotes, setQuotes] = useState<Record<string, MktQuote>>({});
  const [quoteErr, setQuoteErr] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [savedNote, setSavedNote] = useState<Record<string, boolean>>({});
  const [noteErrors, setNoteErrors] = useState<Record<string, string>>({});
  const [savingNotes, setSavingNotes] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [settore, setSettore] = useState<string>(TUTTI);
  const [ordine, setOrdine] = useState<Ordine>('var');
  const [testo, setTesto] = useState('');
  const [filing, setFiling] = useState<Record<string, FilingOverviewTitolo> | null>(null);
  const [filingErr, setFilingErr] = useState<string | null>(null);
  const [quotesAt, setQuotesAt] = useState<string | null>(null);
  const [profiloAperto, setProfiloAperto] = useState(false);
  const [avviso, setAvviso] = useState<Avviso | null>(null);
  const [aggiungo, setAggiungo] = useState<string | null>(null);
  const notesRef = useRef<Record<string, string>>({});
  const savingRef = useRef(new Set<string>());
  const serverRef = useRef<Record<string, string>>({});
  const addRef = useRef<HTMLInputElement | null>(null);
  // rimozione in attesa: il DB si tocca solo allo scadere di «Annulla» (review PR #11)
  const inAttesaRef = useRef<{ f: FavCompany; i: number; timer: ReturnType<typeof setTimeout> } | null>(null);
  const navigate = useNavigate();

  const leggiFiling = () => {
    Bellomberg.filingOverviewAmbito('preferiti')
      .then(r => { setFiling(Object.fromEntries((r.titoli || []).map(t => [t.ticker, t]))); setFilingErr(null); })
      .catch(e => { setFiling(null); setFilingErr(erroreDi(e)); });
  };

  const load = async () => {
    setLoading(true); setErr(null);
    leggiFiling();
    try {
      const r = await Bellomberg.favorites();
      // un titolo in attesa di rimozione resta fuori anche dopo «Aggiorna»
      const list = (r.favorites || []).filter(f => f.ticker !== inAttesaRef.current?.f.ticker);
      setFavs(list);
      // una bozza non salvata sopravvive ad «Aggiorna»; le altre note seguono il server
      const prima = notesRef.current, server = serverRef.current;
      notesRef.current = Object.fromEntries(list.map(f => [f.ticker,
        f.ticker in prima && prima[f.ticker] !== (server[f.ticker] ?? '') ? prima[f.ticker] : f.note || '']));
      serverRef.current = Object.fromEntries(list.map(f => [f.ticker, f.note || '']));
      setNotes(notesRef.current);
      const settled = await Promise.allSettled(list.map(f => Bellomberg.mktQuote(f.ticker)));
      const q: Record<string, MktQuote> = {}, qe: Record<string, string> = {};
      settled.forEach((s, i) => { if (s.status === 'fulfilled') q[list[i].ticker] = s.value; else qe[list[i].ticker] = erroreDi(s.reason); });
      setQuotes(q); setQuoteErr(qe); setQuotesAt(oraOra());
    } catch (e: any) {
      // Buco DICHIARATO (regola 14/07): errore backend ≠ "0 PREFERITI"
      setFavs(null); setErr(erroreDi(e));
    }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // l'avviso si chiude da solo dopo qualche secondo; quello con «Annulla» lo chiude la rimozione
  useEffect(() => {
    if (!avviso || avviso.annulla) return;
    const t = setTimeout(() => setAvviso(null), avviso.tono === 'bad' ? 8000 : 4000);
    return () => clearTimeout(t);
  }, [avviso]);
  // uscendo dalla pagina la rimozione in attesa si completa: il PM l'ha chiesta e non l'ha annullata
  useEffect(() => () => {
    const r = inAttesaRef.current;
    if (r) { clearTimeout(r.timer); inAttesaRef.current = null; Bellomberg.favDel(r.f.ticker).catch(() => {}); }
  }, []);

  const visibili = favs ? elenco(favs, quotes, { settore, testo, ordine }) : [];
  // il dettaglio segue la scelta; senza scelta (o se il titolo è uscito) il primo dell'elenco
  const scelto = favs?.find(f => f.ticker === sel) ?? visibili[0] ?? null;

  const scegli = (t: string) => { setSel(t); setProfiloAperto(false); };

  // Il cuore toglie il titolo dall'elenco ma cancella dal DB (nota compresa) solo dopo
  // ANNULLA_MS: fino ad allora «Annulla» lo rimette com'era. Chiudere l'avviso conferma subito.
  const conferma = async () => {
    const r = inAttesaRef.current;
    if (!r) return;
    clearTimeout(r.timer); inAttesaRef.current = null;
    setAvviso(a => a?.annulla ? null : a);
    try { await Bellomberg.favDel(r.f.ticker); }
    catch (e) { setAvviso({ tono: 'bad', testo: w.removeError(r.f.ticker, erroreDi(e)) }); load(); }
  };

  const annulla = () => {
    const r = inAttesaRef.current;
    if (!r) return;
    clearTimeout(r.timer); inAttesaRef.current = null;
    setFavs(f => { const l = (f || []).filter(x => x.ticker !== r.f.ticker); l.splice(Math.min(r.i, l.length), 0, r.f); return l; });
    setSel(r.f.ticker);
    setAvviso({ tono: 'ok', testo: w.restoredToast(r.f.ticker) });
  };

  const remove = async (t: string) => {
    const f = favs?.find(x => x.ticker === t);
    if (!f) return;
    await conferma(); // una sola rimozione in attesa alla volta: la precedente si completa
    if (scelto?.ticker === t) setSel(visibili.find(x => x.ticker !== t)?.ticker ?? null);
    setFavs(l => (l || []).filter(x => x.ticker !== t));
    inAttesaRef.current = { f, i: (favs || []).indexOf(f), timer: setTimeout(conferma, ANNULLA_MS) };
    setAvviso({ tono: 'ok', testo: w.removedToast(t), annulla: true });
  };

  const add = async (hit: MktSearchHit) => {
    const t = hit.symbol.toUpperCase();
    if (inAttesaRef.current?.f.ticker === t) { annulla(); return; }
    if (aggiungo || favs?.some(f => f.ticker === t)) return;
    setAggiungo(t);
    // la quotazione dà nome e settore; se manca si aggiunge lo stesso con il nome della ricerca
    // review PR #11: l'errore della quotazione si dichiara nel dettaglio (prima restava «Lettura…» per sempre)
    let q: MktQuote | null = null, qErr: string | null = null;
    try { q = await Bellomberg.mktQuote(t); } catch (e) { q = null; qErr = erroreDi(e); }
    const nuovo: FavCompany = { ticker: t, name: (q?.name && q.name.toUpperCase() !== t ? q.name : hit.name) || '', sector: q?.sector || '', industry: q?.industry || '' };
    try {
      await Bellomberg.favAdd(nuovo);
      setFavs(f => [{ ...nuovo, note: '', added_at: new Date().toISOString() }, ...(f || []).filter(x => x.ticker !== t)]);
      setQuotes(s => { const r = { ...s }; if (q) r[t] = q; else delete r[t]; return r; });
      setQuoteErr(s => { const r = { ...s }; if (qErr) r[t] = qErr; else delete r[t]; return r; });
      notesRef.current = { ...notesRef.current, [t]: '' }; setNotes(notesRef.current);
      serverRef.current = { ...serverRef.current, [t]: '' };
      setSettore(TUTTI); setTesto(''); scegli(t);
      setAvviso({ tono: 'ok', testo: w.addedToast(t) });
      leggiFiling();
    } catch (e) {
      setAvviso({ tono: 'bad', testo: w.addError(t, erroreDi(e)) });
    } finally { setAggiungo(null); }
  };

  const editNote = (t: string, v: string) => {
    notesRef.current = { ...notesRef.current, [t]: v };
    setNotes(notesRef.current);
    setSavedNote(s => ({ ...s, [t]: false }));
  };
  const saveNote = async (t: string) => {
    if (savingRef.current.has(t)) return;
    savingRef.current.add(t);
    setSavingNotes(s => ({ ...s, [t]: true }));
    setNoteErrors(s => ({ ...s, [t]: '' }));
    const submitted = notesRef.current[t] || '';
    try {
      await Bellomberg.favSetNote(t, submitted);
      serverRef.current = { ...serverRef.current, [t]: submitted };
      setFavs(f => (f || []).map(x => x.ticker === t ? { ...x, note: submitted } : x));
      setSavedNote(s => ({ ...s, [t]: notesRef.current[t] === submitted }));
    } catch (e) {
      setNoteErrors(s => ({ ...s, [t]: erroreDi(e) }));
    } finally {
      savingRef.current.delete(t);
      setSavingNotes(s => ({ ...s, [t]: false }));
    }
  };

  const openMkt = (t: string) => { sessionStorage.setItem('bb:mktTicker', t); navigate('/market'); };
  const openTradeIdea = (f: FavCompany) => {
    const draft = notesRef.current[f.ticker] ?? '';
    navigate(`/agents/trade-idea?ticker=${encodeURIComponent(f.ticker)}&source=favorites`, { state: { viewDraft: draft, noteSaved: draft === (f.note || '') } });
  };
  const openFiling = (t: string) => navigate(`/filing?t=${encodeURIComponent(t)}`);

  return (
    <ModernPage page="watchlist" render={() => (
      <VistaPreferiti w={w} favs={favs} visibili={visibili} scelto={scelto} quotes={quotes} quoteErr={quoteErr}
        loading={loading} err={err} quotesAt={quotesAt} filing={filing} filingErr={filingErr}
        settore={settore} setSettore={setSettore} ordine={ordine} setOrdine={setOrdine} testo={testo} setTesto={setTesto}
        notes={notes} savedNote={savedNote} noteErrors={noteErrors} savingNotes={savingNotes}
        profiloAperto={profiloAperto} setProfiloAperto={setProfiloAperto} avviso={avviso} chiudiAvviso={() => avviso?.annulla ? conferma() : setAvviso(null)} onAnnulla={annulla}
        aggiungo={aggiungo} addRef={addRef}
        onRefresh={load} onScegli={scegli} onRemove={remove} onAdd={add} onEditNote={editNote} onSaveNote={saveNote}
        onOpenMarket={openMkt} onTradeIdea={openTradeIdea} onOpenFiling={openFiling}
        onExploreMarket={() => navigate('/market')} />
    )} />
  );
}
