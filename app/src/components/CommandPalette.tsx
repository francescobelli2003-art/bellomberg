import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, API_BASE } from '../lib/api';
import RunConfirmDialog from './RunConfirmDialog';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '../lib/mandato';

type Item = { k: string; label: string; hint?: string; run: () => void | Promise<void> };

import { NAVIGATION } from '../lib/navigation';
import { portfolioTickers } from '../lib/chat-prompts';

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const [tickers, setTickers] = useState<string[]>([]);
  const [tickersErr, setTickersErr] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // la run costa: da qui partiva con UN INVIO (Lotto D) -> passa dalla conferma
  const [askRun, setAskRun] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // apertura: Ctrl+K ovunque + evento custom dalla barra header
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen(o => !o); }
      if (e.key === 'Escape') setOpen(false);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener('bb:palette', onOpen as EventListener);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('bb:palette', onOpen as EventListener); };
  }, []);

  useEffect(() => {
    if (open) {
      setQ(''); setIdx(0);
      setTimeout(() => inputRef.current?.focus(), 10);
      let active = true;
      setTickers([]); setTickersErr(false);
      Bellomberg.portfolio()
        .then(s => { if (active) setTickers(portfolioTickers(s.positions)); })
        .catch(() => { if (active) setTickersErr(true); });
      return () => { active = false; };
    }
  }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  const close = useCallback(() => setOpen(false), []);
  const act = useCallback(async (label: string, fn: () => Promise<any>, done: string) => {
    setBusy(label);
    try { await fn(); setBusy(done); setTimeout(() => { setBusy(null); close(); }, 900); }
    catch (e: any) { setBusy('ERRORE: ' + (e?.message || 'fallito')); setTimeout(() => setBusy(null), 2200); }
  }, [close]);
  const runConsigliere = useCallback(async () => {
    setBusy('LANCIO RUN...');
    try { await Bellomberg.runConsigliere(); setBusy('RUN AVVIATA ? apri Agents Live'); setTimeout(() => { setBusy(null); close(); }, 900); }
    catch (e) {
      const detail = dettaglioLeggibile(e);
      setBusy('ERRORE: ' + detail);
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); close(); navigate('/mandato'); }
    }
  }, [close, navigate]);

  const items: Item[] = useMemo(() => {
    const base: Item[] = [];
    const Q = q.trim().toUpperCase();
    // ticker-first: se la query matcha un ticker del book, le sue azioni salgono in testa
    for (const t of tickers) {
      if (Q && t.toUpperCase().includes(Q)) {
        base.push({ k: t, label: `MKT ${t}`, hint: 'security terminal globale', run: () => {
          sessionStorage.setItem('bb:mktTicker', t); navigate('/market'); close(); } });
        base.push({ k: t, label: `NEWS ${t}`, hint: 'feed filtrato sul nome', run: () => {
          sessionStorage.setItem('bb:newsTicker', t); navigate('/news'); close(); } });
        base.push({ k: t, label: `POSIZIONE ${t}`, hint: 'blotter + trade entry', run: () => { navigate('/trades'); close(); } });
      }
    }
    // ricerca GLOBALE: qualsiasi query apre il security terminal (T4)
    if (Q && Q.length >= 2) {
      base.push({ k: 'MKT', label: `CERCA "${Q}" SUI MERCATI GLOBALI`, hint: 'azioni/ETF/indici/FX mondiali', run: () => {
        sessionStorage.setItem('bb:mktQuery', Q); navigate('/market'); close(); } });
    }
    for (const entry of NAVIGATION) {
      base.push({ k: entry.key, label: entry.label.toUpperCase(), hint: entry.group, run: () => {
        if (entry.kind === 'settings') window.dispatchEvent(new Event('bb:settings'));
        else navigate(entry.to);
        close();
      }});
    }
    base.push(
      { k: 'RUN', label: 'LANCIA CONSIGLIERE', hint: 'analisi multi-agente ? conferma prima del lancio', run: () =>
          setAskRun(true) },
      { k: 'PX', label: 'REFRESH PREZZI', hint: 'polygon -> yfinance, tutte le posizioni', run: () =>
          act('AGGIORNO PREZZI...', () => Bellomberg.updatePrices(), 'PREZZI AGGIORNATI') },
      { k: 'NEWS', label: 'REFRESH NEWS FEED', hint: 'pull + classify (~60s)', run: () =>
          act('REFRESH FEED...', () => Bellomberg.newsFeedRefresh(1, true), 'FEED AGGIORNATO') },
      { k: 'NAV', label: 'RICALCOLA NAV HISTORY', hint: 'force refresh performance', run: () =>
          act('RICALCOLO NAV...', () => Bellomberg.navHistory(true), 'NAV RICALCOLATO') },
      { k: 'MEMO', label: 'APRI ULTIMO MEMO (PDF)', hint: 'weekly research note', run: async () => {
          const m = await Bellomberg.memos(1); const id = m.memos?.[0]?.id;
          if (id) window.open(`${API_BASE}/memos/${id}/pdf`, '_blank'); close(); } },
      { k: 'BAK', label: 'BACKUP DATABASE', hint: 'snapshot data/consigliere.db', run: () =>
          act('BACKUP...', () => Bellomberg.dbBackupCreate(), 'BACKUP CREATO') },
    );
    if (!Q) return base;
    return base.filter(i => (i.k + ' ' + i.label + ' ' + (i.hint || '')).toUpperCase().includes(Q));
  }, [q, tickers, navigate, close, act]);

  useEffect(() => { setIdx(0); }, [q]);
  useEffect(() => { setIdx(i => Math.max(0, Math.min(i, items.length - 1))); }, [items.length]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [idx, open]);

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIdx(i => Math.max(0, Math.min(i + 1, items.length - 1))); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter' && items[idx]) { e.preventDefault(); items[idx].run(); }
  };

  if (!open) return null;
  return (
    <>
      <div className="cmdk-overlay" onClick={close} />
      <div className="cmdk-modal">
        <input ref={inputRef} className="cmdk-input" value={q} onChange={e => setQ(e.target.value)}
               role="combobox" aria-label="Cerca pagine, ticker e azioni" aria-expanded={open} aria-controls="bb-command-list" aria-activedescendant={items[idx] ? `bb-command-${idx}` : undefined}
               onKeyDown={onInputKey} placeholder="BELLOMBERG>  ticker, pagina o comando..." spellCheck={false} />
        <div className="cmdk-list" ref={listRef} id="bb-command-list" role="listbox" aria-label="Risultati">
          {busy && <div className="cmdk-item active"><span className="k">::</span>{busy}</div>}
          {items.map((it, i) => (
            <div key={it.k + it.label} id={`bb-command-${i}`} role="option" aria-selected={i === idx} className={'cmdk-item' + (i === idx ? ' active' : '')}
                 onMouseEnter={() => setIdx(i)} onClick={() => it.run()}>
              <span className="k">{it.k}</span>
              <span>{it.label}</span>
              {it.hint && <span style={{ marginLeft: 'auto', fontSize: 9, color: '#8D9FC4' }}>{it.hint}</span>}
            </div>
          ))}
          {items.length === 0 && <div className="cmdk-item"><span className="k">--</span>nessun risultato</div>}
          {tickersErr && (
            <div className="cmdk-item"><span className="k" style={{ color: '#ff3355' }}>!!</span>
              <span style={{ color: '#ff3355' }}>book NON caricato (backend in errore): azioni ticker del portafoglio non disponibili</span>
            </div>
          )}
        </div>
        <div className="cmdk-hint">CTRL+K APRI/CHIUDI &nbsp;·&nbsp; ↑↓ NAVIGA &nbsp;·&nbsp; INVIO ESEGUI &nbsp;·&nbsp; ESC CHIUDI</div>
      </div>
      <RunConfirmDialog
        open={askRun}
        onConfirm={() => { setAskRun(false); runConsigliere(); }}
        onCancel={() => setAskRun(false)}
      />
    </>
  );
}
