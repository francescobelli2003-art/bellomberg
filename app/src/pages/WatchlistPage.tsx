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
  const [avviso, setAvviso] = useState<{ tono: 'ok' | 'bad'; testo: string } | null>(null);
  const [aggiungo, setAggiungo] = useState<string | null>(null);
  const notesRef = useRef<Record<string, string>>({});
  const savingRef = useRef(new Set<string>());
  const serverRef = useRef<Record<string, string>>({});
  const addRef = useRef<HTMLInputElement | null>(null);
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
      const list = r.favorites || [];
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

  // l'avviso si chiude da solo dopo qualche secondo
  useEffect(() => {
    if (!avviso) return;
    const t = setTimeout(() => setAvviso(null), avviso.tono === 'bad' ? 8000 : 4000);
    return () => clearTimeout(t);
  }, [avviso]);

  const visibili = favs ? elenco(favs, quotes, { settore, testo, ordine }) : [];
  // il dettaglio segue la scelta; senza scelta (o se il titolo è uscito) il primo dell'elenco
  const scelto = favs?.find(f => f.ticker === sel) ?? visibili[0] ?? null;

  const scegli = (t: string) => { setSel(t); setProfiloAperto(false); };

  const remove = async (t: string) => {
    const resto = (favs || []).filter(x => x.ticker !== t);
    if (scelto?.ticker === t) setSel(visibili.find(x => x.ticker !== t)?.ticker ?? null);
    setFavs(resto);
    try { await Bellomberg.favDel(t); setAvviso({ tono: 'ok', testo: w.removedToast(t) }); }
    catch (e) { setAvviso({ tono: 'bad', testo: w.removeError(t, erroreDi(e)) }); load(); }
  };

  const add = async (hit: MktSearchHit) => {
    const t = hit.symbol.toUpperCase();
    if (aggiungo || favs?.some(f => f.ticker === t)) return;
    setAggiungo(t);
    // la quotazione dà nome e settore; se manca si aggiunge lo stesso con il nome della ricerca
    let q: MktQuote | null = null;
    try { q = await Bellomberg.mktQuote(t); } catch { q = null; }
    const nuovo: FavCompany = { ticker: t, name: (q?.name && q.name.toUpperCase() !== t ? q.name : hit.name) || '', sector: q?.sector || '', industry: q?.industry || '' };
    try {
      await Bellomberg.favAdd(nuovo);
      setFavs(f => [{ ...nuovo, note: '', added_at: new Date().toISOString() }, ...(f || []).filter(x => x.ticker !== t)]);
      if (q) setQuotes(s => ({ ...s, [t]: q! }));
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
        profiloAperto={profiloAperto} setProfiloAperto={setProfiloAperto} avviso={avviso} chiudiAvviso={() => setAvviso(null)}
        aggiungo={aggiungo} addRef={addRef}
        onRefresh={load} onScegli={scegli} onRemove={remove} onAdd={add} onEditNote={editNote} onSaveNote={saveNote}
        onOpenMarket={openMkt} onTradeIdea={openTradeIdea} onOpenFiling={openFiling}
        onExploreMarket={() => navigate('/market')} />
    )} />
  );
}
