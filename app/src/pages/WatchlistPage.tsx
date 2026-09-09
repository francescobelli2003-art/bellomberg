import { useEffect, useRef, useState, Fragment } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, FavCompany, MktQuote } from '@/lib/api';
import { Star, Cpu, Heart, RefreshCw, Globe } from 'lucide-react';

/** T4-3: WATCHLIST - le favorite companies del PM con quote live. */

const fx2 = (v?: number | null) => v == null || !isFinite(v) ? '-' : v.toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function WatchlistPage() {
  const [favs, setFavs] = useState<FavCompany[] | null>(null);
  const [quotes, setQuotes] = useState<Record<string, MktQuote>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [savedNote, setSavedNote] = useState<Record<string, boolean>>({});
  const [noteErrors, setNoteErrors] = useState<Record<string, string>>({});
  const [savingNotes, setSavingNotes] = useState<Record<string, boolean>>({});
  const notesRef = useRef<Record<string, string>>({});
  const savingRef = useRef(new Set<string>());
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const r = await Bellomberg.favorites();
      const list = r.favorites || [];
      setFavs(list);
      notesRef.current = Object.fromEntries(list.map(f => [f.ticker, f.note || '']));
      setNotes(notesRef.current);
      const settled = await Promise.allSettled(list.map(f => Bellomberg.mktQuote(f.ticker)));
      const q: Record<string, MktQuote> = {};
      settled.forEach((s, i) => { if (s.status === 'fulfilled') q[list[i].ticker] = s.value; });
      setQuotes(q);
    } catch (e: any) {
      // Buco DICHIARATO (regola 14/07): errore backend ≠ "0 PREFERITI"
      setFavs(null); setErr(e?.response?.data?.detail || e?.message || String(e));
    }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const remove = async (t: string) => {
    setFavs(f => (f || []).filter(x => x.ticker !== t));
    try { await Bellomberg.favDel(t); } catch { load(); }
  };
  const openMkt = (t: string) => { sessionStorage.setItem('bb:mktTicker', t); navigate('/market'); };
  const saveNote = async (t: string) => {
    if (savingRef.current.has(t)) return;
    savingRef.current.add(t);
    setSavingNotes(s => ({ ...s, [t]: true }));
    setNoteErrors(s => ({ ...s, [t]: '' }));
    const submitted = notesRef.current[t] || '';
    try {
      await Bellomberg.favSetNote(t, submitted);
      setSavedNote(s => ({ ...s, [t]: notesRef.current[t] === submitted }));
    } catch (e: any) {
      setNoteErrors(s => ({ ...s, [t]: 'Nota non salvata: ' + (e?.response?.data?.detail || e?.message || String(e)) }));
    } finally {
      savingRef.current.delete(t);
      setSavingNotes(s => ({ ...s, [t]: false }));
    }
  };

  const sectors: Record<string, number> = {};
  (favs || []).forEach(f => { const k = (f.sector || f.industry || '').trim(); if (k) sectors[k] = (sectors[k] || 0) + 1; });

  return (
    <div className="space-y-3 font-sans animate-fadeIn">
      <div className="panel flex items-center justify-between px-3 py-2 border-amber-deep">
        <div className="flex items-center gap-3 font-mono text-2xs">
          <Star size={13} className="text-amber" />
          <span className="text-amber-bright text-glow-amber uppercase tracking-[0.2em]">// WATCHLIST</span>
          <span className="text-faint">|</span>
          <span className="text-muted">{err ? 'N.D.' : favs?.length ?? '...'} PREFERITI &middot; SEGUITI DAL CONSIGLIERE E DAL NEWS DESK</span>
        </div>
        <button onClick={load} disabled={loading} className="btn btn-cyan disabled:opacity-50">
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} /> REFRESH
        </button>
      </div>

      {Object.keys(sectors).length > 0 && (
        <div className="panel px-3 py-2 flex items-center gap-2 flex-wrap font-mono text-2xs">
          <span className="text-faint uppercase tracking-wider">Interessi dedotti:</span>
          {Object.entries(sectors).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <span key={k} className="px-2 py-0.5 border border-cyan-deep text-cyan">{k} &middot; {v}</span>
          ))}
        </div>
      )}

      <div className="panel">
        <div className="panel-header">
          <span className="panel-header-title">Favorite Companies // Quote live</span>
          <span className="text-3xs text-faint font-mono">CLICK = SECURITY TERMINAL</span>
        </div>
        {err ? (
          <div className="text-crimson text-2xs font-mono py-10 text-center">
            WATCHLIST NON DISPONIBILE — {err}. I preferiti non sono persi: backend in errore, usa REFRESH.
          </div>
        ) : favs === null ? (
          <div className="text-faint text-2xs font-mono py-10 text-center"><Cpu size={12} className="animate-pulse inline mr-2" />caricamento...</div>
        ) : favs.length === 0 ? (
          <div className="py-12 text-center font-mono">
            <Heart size={22} className="inline text-faint mb-2" />
            <div className="text-muted text-2xs uppercase tracking-[0.2em]">Nessun preferito</div>
            <button onClick={() => navigate('/market')} className="btn btn-amber mt-4"><Globe size={11} /> CERCA SUI MERCATI GLOBALI</button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-bbg">
              <thead><tr>
                <th>Ticker</th><th>Nome</th><th>Settore / Industry</th>
                <th className="text-right">Prezzo</th><th className="text-right">Var %</th>
                <th className="text-right">52W</th><th className="text-right">Dal</th><th className="text-right"></th>
              </tr></thead>
              <tbody>
                {favs.map(f => {
                  const q = quotes[f.ticker];
                  const chg = q?.price != null && q?.prev_close ? ((q.price / q.prev_close) - 1) * 100 : null;
                  const rng = q?.low_52w != null && q?.high_52w != null && q?.price != null && q.high_52w > q.low_52w
                    ? Math.min(100, Math.max(0, ((q.price - q.low_52w) / (q.high_52w - q.low_52w)) * 100)) : null;
                  return (
                    <Fragment key={f.ticker}>
                    <tr onClick={() => openMkt(f.ticker)} className="cursor-pointer">
                      <td className="text-gold font-semibold">{f.ticker}</td>
                      <td className="text-text-dim">{q?.name || f.name || '-'}</td>
                      <td className="text-muted text-3xs">{(f.sector || '-') + (f.industry ? ' / ' + f.industry : '')}</td>
                      <td className="text-right text-cyan tabular-nums">{q ? fx2(q.price) + ' ' + (q.currency || '') : '...'}</td>
                      <td className={'text-right tabular-nums font-semibold ' + (chg == null ? 'text-muted' : chg >= 0 ? 'pl-positive' : 'pl-negative')}>
                        {chg == null ? '-' : (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%'}
                      </td>
                      <td className="text-right">
                        {rng == null ? <span className="text-muted">-</span> : (
                          <span className="inline-block w-14 h-[5px] bg-bg-elev border border-border/40 relative align-middle">
                            <span className="absolute top-1/2 -translate-y-1/2 w-1 h-2.5 bg-amber" style={{ left: 'calc(' + rng.toFixed(0) + '% - 2px)' }} />
                          </span>
                        )}
                      </td>
                      <td className="text-right text-muted text-3xs">{(f.added_at || '').slice(0, 10)}</td>
                      <td className="text-right">
                        <button onClick={e => { e.stopPropagation(); remove(f.ticker); }}
                                title="Rimuovi dai preferiti"
                                className="text-crimson hover:text-crimson-deep transition-colors">
                          <Heart size={11} fill="currentColor" />
                        </button>
                      </td>
                    </tr>
                    <tr className="bg-bg-elev/30">
                      <td colSpan={8} className="py-1.5 px-3">
                        <div className="flex items-start gap-2" onClick={e => e.stopPropagation()}>
                          <span className="text-3xs text-faint font-mono uppercase tracking-wider mt-1 shrink-0">Nota PM</span>
                          <textarea value={notes[f.ticker] ?? ''}
                            onChange={e => {
                              notesRef.current = { ...notesRef.current, [f.ticker]: e.target.value };
                              setNotes(notesRef.current);
                              setSavedNote(s => ({ ...s, [f.ticker]: false }));
                            }}
                            onClick={e => e.stopPropagation()}
                            placeholder="Perche' lo segui: tesi, catalyst, livelli... (lo legge il consigliere)"
                            rows={2}
                            className="flex-1 bg-bg border border-border/50 text-2xs font-mono text-text-dim px-2 py-1 resize-y outline-none focus:border-cyan-deep" />
                          <button disabled={savingNotes[f.ticker]} onClick={e => { e.stopPropagation(); saveNote(f.ticker); }} className="btn btn-cyan shrink-0 mt-0.5">
                            {savingNotes[f.ticker] ? 'SALVATAGGIO…' : savedNote[f.ticker] ? 'SALVATO' : 'SALVA'}
                          </button>
                        </div>
                        {noteErrors[f.ticker] && <div role="alert" className="text-crimson text-2xs">{noteErrors[f.ticker]}</div>}
                      </td>
                    </tr>
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
