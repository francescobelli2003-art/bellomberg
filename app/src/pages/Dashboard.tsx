import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bellomberg, PortfolioSnapshot, Decision, PortfolioRisk, NavHistory, Position, TwrPayload, AgentInfo, AgentsLiveState, MktOverview, MktOverviewRow } from '@/lib/api';
import { fmtEUR, fmtPct, fmtNum } from '@/lib/format';
import { leggiQuota, cimaF1, formattaCifra, notaFlusso, motivoChiamata } from '@/lib/quota';
import { leggiCurva, letturaCurva, spiegaCurva, titoloVista, etichettaVista } from '@/lib/curva';
import type { EsitoCurva, VistaCurva } from '@/lib/curva';
import { getPulsePicks, togglePulse } from '@/lib/pulse';
import { conservaDettaglioRun, dettaglioLeggibile, statusHttp } from '@/lib/mandato';
import { dayPct, dayEur, computeDailyPnl } from '@/lib/dailypl';
import { portfolioValues } from '@/lib/portfolio-values';
import { FlashPx, FlashVal } from '@/components/Flash';
import TvChartPanel from '@/components/TvChartPanel';
import RunConfirmDialog from '@/components/RunConfirmDialog';
import { RefreshCw, Play, Cpu } from 'lucide-react';
import './dashboard-command.css';
import './dashboard-f1.css';

/* ============================================================
   F1 v3 "OBSIDIAN COMMAND" (design approvato PM 23/07):
   3 colonne zero-scroll (B-UX5) — strumento NAV + AI DESK |
   equity curve + grafico TV-grade + heat | decisioni + blotter
   + telemetria rischio. Dati INVARIATI: stessi endpoint di prima.
   ============================================================ */

// tick-flash FlashPx/FlashVal e formula P&L daily: condivisi F1/F2 in
// components/Flash + lib/dailypl (estratti 23/07 per il vestito OBSIDIAN di F2)

// ===== count-up del NAV (cinematico ma sobrio; rispetta reduced-motion) =====
// ⚠️ `da` è nato col (F40): questo conteggio era tarato sugli euro e partiva
// sempre da ZERO. Da zero a un patrimonio a sei cifre è uno strumento che si accende; da
// zero al valore quota (un indice base 100) sono mezzo secondo di quote
// catastrofiche mai esistite (12,50 … 47,80 …). Sulla quota si parte dalla
// BASE, e ogni valore intermedio è dentro l'intervallo che la quota ha
// davvero attraversato. Con `da = 0` il comportamento è identico a prima.
function useCountUp(target: number, ms = 750, da = 0) {
  const [v, setV] = useState(da);
  const animated = useRef(false);
  const daPrec = useRef(da);
  useEffect(() => {
    if (!isFinite(target)) return;
    // ⚠️ la latch vale per UN numero: se cambia il punto di partenza stiamo
    // contando un'altra grandezza (euro -> quota) e va riarmata. Senza questo,
    // il conteggio dalla base non partiva MAI nel percorso normale, perche' lo
    // stato «attesa» aveva gia' consumato la latch sugli euro.
    if (daPrec.current !== da) { animated.current = false; daPrec.current = da; }
    if (animated.current || target === da ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setV(target);
      if (target !== da) animated.current = true;
      return;
    }
    animated.current = true;
    const t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      const f = Math.min(1, (t - t0) / ms);
      const e = 1 - Math.pow(1 - f, 3);
      setV(da + (target - da) * e);
      if (f < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms, da]);
  return v;
}

// fix 30c: fetch nav_history SOLLEVATO nel Dashboard (lifting) e condiviso via prop
function useNavHistory() {
  const [hist, setHist] = useState<NavHistory | null>(null);
  /* (F41) era un `failed: boolean` che, tolta la curva, non aveva piu' nessun
     lettore — e toglierlo del tutto avrebbe lasciato un `.catch` vuoto, cioe' un
     ingoio silenzioso (regola PM 14/07). Ora e' il MOTIVO, e il motivo lo legge
     il chip ITD: e' l'unica cosa che questa chiamata regge ancora. */
  const [motivo, setMotivo] = useState<string | null>(null);
  useEffect(() => {
    let m = true;
    Bellomberg.navHistory(false)
      .then(h => {
        if (!m) return;
        if (h && !h.error) setHist(h);
        else setMotivo(h?.error || 'il motore ha risposto senza la serie');
      })
      .catch(err => { if (m) setMotivo(motivoChiamata(err)); });
    return () => { m = false; };
  }, []);
  return { hist, motivo };
}

// ===== equity curve con crosshair (storico reale da nav_history, cache backend) =====
/* ══ (F41) LA CURVA: cosa disegna, e perché non è più quello di prima ══
   Fino a ieri qui si disegnava `nav_total_eur` di `/analytics/nav_history`:
   il valore di mercato del book PIÙ LA CASSA DI OGGI, tenuta costante su ogni
   giorno passato. Misurato il 21/08 (cifre tolte il 02/09 per la
   pubblicazione, i rapporti restano): la cassa vera fra l'11/06 e il 30/07
   è scesa a meno di un quarto, quindi l'errore CAMBIA SEGNO — è la forma
   della linea a essere falsa, non il livello — e il piedino ne ricavava un
   rendimento a TRE cifre su una finestra in cui il rendimento vero è circa
   +11%, perché la serie parte dal patrimonio di febbraio, meno di un quarto
   di quello di agosto, quando il book si stava ancora costruendo.

   Ora la linea è il VALORE QUOTA (`twr_index`), l'unica serie vera dal primo
   all'ultimo punto, con una seconda vista sugli euro VERI degli snapshot —
   che però esistono solo dal 12/06 (57 punti su 150). Il giudizio su cosa si
   può disegnare sta tutto in `lib/curva.ts`: qui non si decide e non si
   formatta niente.

   ⚠️ NESSUNA CHIAMATA NUOVA: `twr` è già in pagina dal (F40), e i campi
   `values_eur`/`regimes`/`external_flows` arrivavano già e finivano nel
   cestino. `navHistory` resta chiamata perché regge ancora il chip ITD. */
function NavSpark({ c, hClass = 'h-28' }: { c: EsitoCurva; hClass?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const viva = c.stato === 'viva' ? c : null;

  /* ⚠️ `hover` è un INDICE, e le due viste hanno lunghezze diverse (150 e 57):
     senza questo azzeramento, cambiando vista col mouse fermo il crosshair
     saltava a un altro giorno lontano dal puntatore — misurato dai confutatori
     il 22/08: hover=50 in patrimonio sta all'89% della larghezza, in quota al
     33%. Si azzera sulla SERIE, non sulla vista: vale anche quando il payload
     cambia sotto (F1 rinfresca i prezzi ogni 60 secondi). */
  useEffect(() => { setHover(null); }, [viva]);

  const s = useMemo(() => {
    if (!viva || viva.valori.length < 2) return null;
    const v = viva.valori;
    const min = Math.min(...v), max = Math.max(...v);
    const span = (max - min) || 1;
    const W = 600, H = 100, PAD = 6;
    const px = (i: number) => (i / (v.length - 1)) * W;
    const py = (val: number) => PAD + (1 - (val - min) / span) * (H - 2 * PAD);
    let line = '';
    for (let i = 0; i < v.length; i++) line += (i ? ' L' : 'M') + px(i).toFixed(1) + ',' + py(v[i]).toFixed(1);
    return { v, line, area: line + ' L' + W + ',' + H + ' L0,' + H + ' Z', W, H, px, py };
  }, [viva]);

  if (c.stato === 'attesa') {
    return (
      <div className={hClass + ' flex items-center justify-center text-faint text-3xs font-mono uppercase tracking-wider'}>
        <Cpu size={11} className="animate-pulse mr-2" /> {c.frase}
      </div>
    );
  }
  if (c.stato === 'assente') {
    /* il motivo VERBATIM, non «storico NAV non disponibile» e basta: il PM deve
       poter distinguere il backend spento dal motore contabile che tace. */
    return (
      <div className={hClass + ' flex flex-col items-center justify-center px-3 text-center gap-1'}>
        <span className="text-faint text-3xs font-mono uppercase tracking-wider">{c.frase}</span>
        <span className="text-3xs font-mono" style={{ color: '#FFA51E' }} title={c.motivo}>{c.motivo}</span>
      </div>
    );
  }
  if (!viva || !s) {
    return (
      <div className={hClass + ' flex items-center justify-center text-3xs font-mono uppercase tracking-wider'} style={{ color: '#FFA51E' }}>
        serie dichiarata viva ma non disegnabile
      </div>
    );
  }

  const lettura = hover != null ? letturaCurva(viva, hover) : null;
  const iHover = lettura != null && hover != null ? hover : null;
  const leftPct = iHover != null ? (iHover / (s.v.length - 1)) * 100 : 0;
  const topPct = iHover != null ? (s.py(s.v[iHover]) / s.H) * 100 : 0;
  const xConfine = viva.confine != null ? s.px(viva.confine) : null;

  const onMove = (ev: React.PointerEvent) => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return;
    const f = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    setHover(Math.round(f * (s.v.length - 1)));
  };

  return (
    <div className="relative select-none" ref={wrapRef}
         onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg viewBox={'0 0 ' + s.W + ' ' + s.H} preserveAspectRatio="none" className={'w-full block ' + hClass}>
        <defs>
          <linearGradient id="bbNavFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#29d3f2" stopOpacity="0.20" />
            <stop offset="100%" stopColor="#29d3f2" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map(f => (
          <line key={f} x1="0" x2={s.W} y1={s.H * f} y2={s.H * f} stroke="#1a2440" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
        <path d={s.area} fill="url(#bbNavFill)" />
        <path d={s.line} fill="none" stroke="#29d3f2" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        {/* IL CONFINE, disegnato invece che scritto in una nota: da lì gli
            snapshot sono ufficiali, da lì conosciamo il patrimonio, e da lì la
            quota è misurata sul NAV totale invece che sul solo investito.
            Tre cose vere in un tratteggio; il dettaglio sta nel titolo del
            riquadro (`spiegaCurva`). */}
        {xConfine != null && (
          <line x1={xConfine} x2={xConfine} y1="0" y2={s.H} stroke="#FFA51E" strokeOpacity="0.5"
                strokeDasharray="2 3" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
      </svg>

      {/* ultimo valore: pulse dot */}
      <div className="absolute w-1.5 h-1.5 rounded-full bg-cyan pulse-dot"
           style={{ right: -2, top: 'calc(' + ((s.py(s.v[s.v.length - 1]) / s.H) * 100) + '% - 3px)', boxShadow: '0 0 8px rgba(41,211,242,0.8)' }} />

      {/* crosshair: la lettura la scrive `letturaCurva`, qui si posiziona e basta */}
      {lettura && iHover != null && (
        <>
          <div className="absolute inset-y-0 w-px bg-cyan/40 pointer-events-none" style={{ left: leftPct + '%' }} />
          <div className="absolute w-2 h-2 rounded-full border border-cyan bg-bg pointer-events-none"
               style={{ left: 'calc(' + leftPct + '% - 4px)', top: 'calc(' + topPct + '% - 4px)' }} />
          <div className="absolute z-10 pointer-events-none font-mono text-3xs bg-bg-elev border border-cyan-deep px-2 py-1 whitespace-nowrap"
               style={{ left: leftPct > 60 ? 'auto' : 'calc(' + leftPct + '% + 8px)',
                        right: leftPct > 60 ? 'calc(' + (100 - leftPct) + '% + 8px)' : 'auto', top: 0 }}>
            <span className="text-muted">{lettura.data}</span>
            <span className="text-cyan ml-2 tabular-nums">{lettura.primaria}</span>
            <span className="text-faint ml-2">{lettura.secondaria}</span>
            {lettura.versamento && <span className="ml-2" style={{ color: '#FFA51E' }}>{lettura.versamento}</span>}
          </div>
        </>
      )}

      <div className="flex items-center justify-between mt-1 font-mono text-3xs text-faint uppercase tracking-wider">
        {/* ⚠️ le due date le scrive il MODULO (`estremoDa`/`estremoA`): prima qui
            c'era l'ISO grezza del backend, e la prima cura le formattava in
            pagina — cioè un secondo formattatore accanto a quello che scrive la
            lettura del crosshair. Forma lunga sugli estremi (l'anno su un asse
            serve) e breve nel crosshair: è una scelta, presa in un posto solo. */}
        <span>{viva.estremoDa}</span>
        <span className={viva.piedeVerde == null ? 'text-cyan' : viva.piedeVerde ? 'text-emerald' : 'text-crimson'}>
          {viva.piede}
        </span>
        <span>{viva.estremoA}</span>
      </div>
    </div>
  );
}

// ===== heat del book su UNA riga: top N per peso + resto aggregato DICHIARATO =====
function HeatStrip({ positions, limit = 10, onSelect }: {
  positions: Position[]; limit?: number; onSelect: (tk: string) => void;
}) {
  const ps = positions.filter(p => (p.peso_pct || 0) > 0).sort((a, b) => (b.peso_pct || 0) - (a.peso_pct || 0));
  if (ps.length === 0) return <div className="text-faint text-2xs font-mono py-4 text-center">no positions</div>;
  const top = ps.slice(0, limit);
  const rest = ps.slice(limit);
  const restW = rest.reduce((s, p) => s + (p.peso_pct || 0), 0);
  return (
    <div className="heat num">
      {top.map(p => {
        const pl = p.pl_pct ?? 0;
        const pos = pl >= 0;
        const a = Math.min(0.09 + Math.abs(pl) / 45, 0.4);
        return (
          <div key={p.ticker} className="hcell"
               title={p.ticker + '  peso ' + (p.peso_pct || 0).toFixed(1) + '%  P/L ' + pl.toFixed(1) + '%  ·  click → MKT'}
               onClick={() => onSelect(p.ticker)}
               style={{
                 flexGrow: Math.max(Math.sqrt(p.peso_pct || 1) * 2, 1),
                 background: pos ? 'rgba(33,224,160,' + a + ')' : 'rgba(255,61,96,' + a + ')',
                 borderColor: pos ? 'rgba(33,224,160,0.4)' : 'rgba(255,61,96,0.4)',
               }}>
            <span className="t">{p.ticker}</span>
            <span className="r2"><span className="w">{(p.peso_pct || 0).toFixed(1)}</span>
              <span className={'pl ' + (pos ? 'up' : 'dn')}>{pl >= 0 ? '+' : ''}{pl.toFixed(1)}%</span></span>
          </div>
        );
      })}
      {rest.length > 0 && (
        <div className="hcell" style={{ flexGrow: 1.4, background: 'rgba(154,166,192,0.05)', borderColor: '#2A3760' }}
             title={rest.map(p => p.ticker).join(' · ')}>
          <span className="t" style={{ color: '#8D9FC4' }}>+{rest.length} ALTRI</span>
          <span className="r2"><span className="w">{restW.toFixed(1)}</span><span className="pl">—</span></span>
        </div>
      )}
    </div>
  );
}

// ===== pannello MARKET: grafico TV-grade condiviso (TvChartPanel, altezza reattiva) =====
function MarketPanel({ tickers, onOpenMkt }: { tickers: string[]; onOpenMkt: (tk: string) => void }) {
  const [tk, setTk] = useState('');
  useEffect(() => { if (!tk && tickers.length) setTk(tickers[0]); }, [tickers, tk]);
  return (
    <div className="p3 cy mkchart">
      <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
      <div className="p3h">MARKET // {tk || '-'}
        <select value={tk} onChange={e => setTk(e.target.value)}
                className="bg-bg border border-border text-cyan font-mono text-2xs px-1.5 py-0.5 focus:outline-none focus:border-cyan cursor-pointer">
          {tickers.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <button className="tb" style={{ color: '#29D3F2' }} onClick={() => tk && onOpenMkt(tk)} title="apri la pagina completa su MKT">APRI IN MKT ⧉</button>
        <span className="side">MOTORE TRADINGVIEW · SCROLL = ZOOM · DRAG = PAN</span>
      </div>
      {tk && <TvChartPanel ticker={tk} fill defaultRange={5} defaultInterval={4} />}
    </div>
  );
}

// ===== AI DESK // COMITATO: agenti veri + stato run live =====
function AiDeskPanel() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [committeeEng, setCommitteeEng] = useState<string | null>(null);
  const [live, setLive] = useState<AgentsLiveState | null>(null);
  useEffect(() => {
    let m = true;
    Bellomberg.agentsList().then(r => {
      if (!m) return;
      setAgents(r.agents || []);
      // ENGINE = motore del COMITATO (engines.committee_r1_r2, ponte 26/07):
      // agents[].model è il modello CHAT e qui mentirebbe. Assente = dichiarato.
      const e = r.engines;
      setCommitteeEng(e?.committee_r1_r2 || (e?.committee_r1_r2_error ? 'ERR: ' + e.committee_r1_r2_error : null));
    }).catch(() => {});
    const poll = () => Bellomberg.agentsLive().then(r => { if (m) setLive(r); }).catch(() => { if (m) setLive(null); });
    poll();
    const i = setInterval(poll, 45 * 1000);
    return () => { m = false; clearInterval(i); };
  }, []);
  const eng = committeeEng ? committeeEng.replace('claude-', '').replace(/-\d{8}$/, '').replace(/-/g, ' ') : null;
  const stFor = (id: string): { cls: string; label: string } => {
    if (!live?.running) return { cls: 'g', label: 'READY' };
    const st = live.specialist_status?.[id];
    if (st === 'running') return { cls: 'a', label: 'R' + (live.current_round ?? '') };
    if (st === 'error') return { cls: 'r', label: 'ERR' };
    if (st === 'done') return { cls: 'g', label: 'DONE' };
    return { cls: 'off', label: 'IDLE' };
  };
  return (
    <div className="p3 vi" style={{ flexShrink: 0 }}>
      <div className="p3h vi">AI DESK // COMITATO
        <span className="side">{live?.running ? 'RUN IN CORSO · R' + (live.current_round ?? '-') : 'STANDBY'}</span>
      </div>
      <div className="num" style={{ paddingTop: 5 }}>
        {agents.length === 0 && <div className="dk"><span className="nm">agenti</span><span className="st">n.d. (backend)</span></div>}
        {agents.map(a => {
          const s = stFor(a.id);
          return (
            <div className="dk" key={a.id}>
              <span className="ld" style={{ width: 6, height: 6, borderRadius: '50%', background: a.color || '#9B7BFF', flexShrink: 0 }} />
              <span className="nm" title={a.role}>{a.name}</span>
              <span className="st">{s.label}</span>
              <span className={'ld ' + s.cls} />
            </div>
          );
        })}
      </div>
      <div style={{ borderTop: '1px solid #1A2440' }}>
        <div className="sysrow num"><span>ENGINE</span><span style={{ color: '#8D9FC4', letterSpacing: '.06em' }}>{agents.length || '-'} AGENTS · {eng || 'n.d.'} · <span style={{ color: '#9B7BFF' }}>LIVE ▸</span></span></div>
      </div>
    </div>
  );
}

// ===== MACRO PULSE: polso dei mercati dal feed overview (stessa fonte di F15), click → MKT =====
const PULSE_PICKS: ReadonlyArray<readonly [keyof MktOverview & string, string]> = [
  ['indici', 'S&P 500'], ['indici', 'Nasdaq'], ['indici', 'VIX'],
  ['obbligazioni', '10Y'], ['commodities', 'Oro'], ['commodities', 'WTI'],
  ['valute', 'EUR/USD'], ['valute', 'Bitcoin'],
];
function MacroPulse({ onSelect }: { onSelect: (tk: string) => void }) {
  const [rows, setRows] = useState<MktOverviewRow[] | null>(null);
  const [custom, setCustom] = useState<MktOverviewRow[]>([]);
  const [failed, setFailed] = useState(false);
  const [, bump] = useState(0); // re-render dopo una rimozione
  useEffect(() => {
    let m = true;
    const load = () => {
      Bellomberg.mktOverview('US')
        .then(d => {
          if (!m) return;
          const out: MktOverviewRow[] = [];
          for (const [sec, name] of PULSE_PICKS) {
            const list = (d as any)[sec] as MktOverviewRow[] | undefined;
            const hit = list?.find(r => r.name?.toLowerCase().includes(name.toLowerCase()));
            if (hit) out.push(hit);
          }
          setRows(out); setFailed(out.length === 0);
        })
        .catch(() => { if (m) { setRows([]); setFailed(true); } });
      // pick del PM (da F15 "+ MACRO PULSE"): quote per-ticker, n.d. dichiarato se il fetch fallisce
      const picks = getPulsePicks();
      Promise.all(picks.map(p =>
        Bellomberg.mktQuote(p.ticker)
          .then(q => ({ ticker: p.ticker, name: p.name || q.name || p.ticker, price: q.price ?? null,
                        change_pct: (q.price != null && q.prev_close) ? (q.price / q.prev_close - 1) * 100 : null }))
          .catch(() => ({ ticker: p.ticker, name: p.name, price: null, change_pct: null }))
      )).then(cs => { if (m) setCustom(cs); });
    };
    load();
    const i = setInterval(load, 5 * 60 * 1000);
    return () => { m = false; clearInterval(i); };
  }, []);
  const removePick = (tk: string) => {
    togglePulse(tk);
    setCustom(c => c.filter(r => r.ticker !== tk));
    bump(x => x + 1);
  };
  const renderRow = (r: MktOverviewRow, mine: boolean) => {
    const c = r.change_pct;
    return (
      <tr key={(mine ? '+' : '') + r.ticker} onClick={() => onSelect(r.ticker)} style={{ cursor: 'pointer' }} title={r.ticker + ' · click → MKT'}>
        <td style={{ color: '#8D9FC4' }}>{mine && <span style={{ color: '#D4AF37' }}>◆ </span>}{r.name}</td>
        <td style={{ color: '#29D3F2' }}>{r.price == null ? 'n.d.' : r.price.toLocaleString('it-IT', { maximumFractionDigits: 2 })}</td>
        <td className={c == null ? 'text-muted' : c >= 0 ? 'up' : 'dn'} style={{ fontWeight: 600 }}>
          {c == null ? 'n.d.' : (c >= 0 ? '+' : '') + c.toFixed(2) + '%'}
        </td>
        <td style={{ width: 14, padding: '3px 4px' }}>
          {mine && (
            <span className="pxdel" title={'togli ' + r.ticker + ' dal MACRO PULSE'}
                  onClick={e => { e.stopPropagation(); removePick(r.ticker); }}>×</span>
          )}
        </td>
      </tr>
    );
  };
  return (
    <div className="p3 cy" style={{ flex: 1, minHeight: 0 }}>
      <div className="p3h">MACRO PULSE <span className="n">· FEED MKT · 5MIN</span><span className="side">◆ = TUOI (DA MKT) · CLICK → MKT</span></div>
      <div style={{ overflowY: 'auto', minHeight: 0 }}>
        {rows === null ? (
          <div className="text-faint text-2xs font-mono py-4 text-center">caricamento feed…</div>
        ) : failed && custom.length === 0 ? (
          <div className="text-faint text-2xs font-mono py-4 text-center">feed overview n.d. (dichiarato)</div>
        ) : (
          <table className="num">
            <tbody>
              {rows.map(r => renderRow(r, false))}
              {custom.map(r => renderRow(r, true))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ===== telemetria rischio a barre (fondo scala dichiarato nel title) =====
function TelemetryRisk({ risk, loading, onRefresh }: { risk: PortfolioRisk | null; loading: boolean; onRefresh: () => void }) {
  const p = risk && !risk.error ? risk.portfolio : null;
  const bar = (label: string, value: string, frac: number, color: string, scale: string) => (
    <div className="ab num" title={'fondo scala barre: ' + scale}>
      <span className="k">{label}</span>
      <span className="bar"><i style={{ width: Math.min(100, Math.max(2, frac * 100)) + '%', background: color }} /></span>
      <span className="v">{value}</span>
    </div>
  );
  return (
    <div className="p3" style={{ flexShrink: 0, paddingBottom: 8 }}>
      <div className="p3h">TELEMETRY // RISK
        {risk && !risk.error && <span className="n">· {risk.n_assets_analyzed} ASSET · {risk.lookback_days}D</span>}
        <span className="side">
          <button className="tb" onClick={onRefresh} disabled={loading} style={{ color: '#29D3F2' }}>
            {loading ? 'CALCOLO…' : '↻ REFRESH'}
          </button>
        </span>
      </div>
      {!p ? (
        <div className="text-faint text-2xs font-mono py-4 text-center">
          {loading ? 'calcolo metriche (storico 1y)…' : (risk?.error || 'metriche non caricate')}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7, paddingTop: 8 }}>
          {bar('VaR 95 1D', p.var_95_1d_pct.toFixed(2) + '%', Math.abs(p.var_95_1d_pct) / 3, '#FF3D60', '3%')}
          {bar('VaR 99 1D', p.var_99_1d_pct.toFixed(2) + '%', Math.abs(p.var_99_1d_pct) / 4, '#FF3D60', '4%')}
          {bar('Sharpe', p.sharpe.toFixed(2), p.sharpe / 2, '#21E0A0', '2,0')}
          {bar('Vol ann', p.vol_annual_pct.toFixed(1) + '%', p.vol_annual_pct / 40, '#95A1BA', '40%')}
          {bar('Beta SPY', p.beta_vs_spy.toFixed(2), Math.abs(p.beta_vs_spy) / 2, '#95A1BA', '2,0')}
          {bar('Max DD 1Y', p.max_dd_1y_pct.toFixed(1) + '%', Math.abs(p.max_dd_1y_pct) / 30, '#FFA51E', '30%')}
          <div className="sysrow num" style={{ paddingTop: 2 }}>
            <span>RISK ALERTS</span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={'ld ' + ((risk?.alerts?.length || 0) > 0 ? 'a' : 'g')} />
              <span style={{ color: (risk?.alerts?.length || 0) > 0 ? '#FFA51E' : '#21E0A0', letterSpacing: '.08em' }}>
                {(risk?.alerts?.length || 0)} ACTIVE
              </span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [snap, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [risk, setRisk] = useState<PortfolioRisk | null>(null);
  const [riskLoading, setRiskLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [askRun, setAskRun] = useState(false);  // Lotto D: la run costa, si conferma prima

  // count-up sul NAV (hook chiamato sempre, prima dell'early return)
  const navTotalTarget = portfolioValues(snap).nav ?? Number.NaN;

  // fix 30c: nav_history condiviso (hero P&L TOTALE ITD + equity curve), una sola chiamata
  /* (F41) `nav_history` non regge piu' la curva: le resta il chip ITD, e basta.
     Il motivo di un suo fallimento finisce nel title di quel chip — un campo che
     nessuno legge sarebbe la trappola del (F40), e un catch muto sarebbe peggio. */
  const { hist: navHist, motivo: navHistMotivo } = useNavHistory();

  // riconciliazione NAV live vs chiusura ufficiale + metriche TWR GIPS + (F40)
  // IL VALORE QUOTA, che da oggi è il titolo della pagina. Nessuna chiamata
  // nuova: `dates`/`twr_index`/`regimes`/`external_flows` erano GIÀ in questa
  // risposta e finivano nel cestino insieme a tutto il resto del payload.
  const [twrRec, setTwrRec] = useState<TwrPayload['reconciliation']>(null);
  const [twr, setTwr] = useState<TwrPayload | null>(null);
  const [twrErr, setTwrErr] = useState<string | null>(null);
  const [twrInCorso, setTwrInCorso] = useState(true);
  /* (F41) quale delle due viste della curva. La quota e' la predefinita perche'
     e' l'unica serie VERA su tutta la finestra; il patrimonio vero esiste solo
     dal primo snapshot (57 punti su 150) e lo dichiara. */
  const [vistaCurva, setVistaCurva] = useState<VistaCurva>('quota');
  useEffect(() => {
    let m = true;
    Bellomberg.twr(false)
      .then(r => { if (m) { setTwrRec(r.reconciliation || null); setTwr(r); setTwrErr(null); } })
      // ⚠️ qui c'era `.catch(() => {})`. Finché la quota era un chip in fondo
      // il motivo si poteva buttare; ora senza motivo la pagina resterebbe
      // senza titolo e senza spiegazione — ripiego muto, regola PM 14/07.
      .catch(err => { if (m) { setTwr(null); setTwrErr(motivoChiamata(err)); } })
      .finally(() => { if (m) setTwrInCorso(false); });
    return () => { m = false; };
  }, []);
  // LA CIMA DELLA PAGINA — un giudizio solo, e la resa derivata da lui
  // (§9-unquadragies-octodecies): la cifra grande, il suo occhiello, il suo
  // timbro e la riga che la spiega non possono divergere per costruzione.
  const quota = useMemo(
    () => leggiQuota(twr, twrErr, twrInCorso), [twr, twrErr, twrInCorso]);

  /* (F41) lo stesso payload, letto per la curva: un giudizio solo, e tutte le
     stringhe del riquadro (titolo, nota, piedino, lettura del crosshair) escono
     da li'. La pagina non ricalcola e non riformatta niente. */
  const curva: EsitoCurva = useMemo(
    () => leggiCurva(twr, vistaCurva, twrInCorso, twrErr), [twr, vistaCurva, twrInCorso, twrErr]);
  /* ⚠️ il motivo per cui una parola dell'interruttore è spenta. Se la curva NON è
     viva, sono spente TUTTE E DUE: la prima stesura le lasciava premibili in
     attesa e in errore, e premendo PATRIMONIO il titolo cambiava sopra un
     riquadro vuoto, promettendo «gli euro veri degli snapshot». */
  const curvaSpenta = curva.stato === 'attesa'
    ? 'la serie non è ancora arrivata dal motore contabile'
    : curva.stato === 'assente' ? curva.motivo : null;
  const patrimonioSpento = curvaSpenta
    || (curva.stato === 'viva' && curva.altra.vista === 'patrimonio'
      ? curva.altra.motivoSpento : null);
  const oggiISO = new Date().toLocaleDateString('sv-SE');  // YYYY-MM-DD locale
  const cima = useMemo(
    () => cimaF1(quota, navTotalTarget, oggiISO), [quota, navTotalTarget, oggiISO]);
  const cifraDisp = useCountUp(cima.contaA, 750, cima.contaDa);
  const flussoOggi = notaFlusso(quota, oggiISO);

  // silent=true: refresh quasi-live senza smontare la pagina (il loader pieno resta solo al primo load)
  const loadAll = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [p, d] = await Promise.all([
        Bellomberg.portfolio(),
        Bellomberg.decisions('PENDING', 6).catch(() => ({ decisions: [] })),
      ]);
      setSnap(p);
      setDecisions(d?.decisions || []);
    } catch (e: any) {
      console.error('[Dashboard.loadAll]', e);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const loadRisk = async (force = false) => {
    setRiskLoading(true);
    try {
      const r = await Bellomberg.portfolioRisk(force);
      setRisk(r);
    } catch (e: any) {
      console.error('[Dashboard.loadRisk]', e);
    } finally {
      setRiskLoading(false);
    }
  };

  const refreshPrices = async () => {
    setRefreshing(true);
    try {
      await Bellomberg.updatePrices();
      await loadAll(true);
    } finally {
      setRefreshing(false);
    }
  };

  const triggerRun = async () => {
    setRunMsg('Spawning consigliere process...');
    try {
      const r = await Bellomberg.runConsigliere();
      setRunMsg('RUN ' + r.task_id + ' ACTIVE - est. 25-40min - email on complete');
    } catch (e: any) {
      const detail = dettaglioLeggibile(e);
      setRunMsg('Error: ' + detail);
      if (statusHttp(e) === 428) { conservaDettaglioRun(detail); navigate('/mandato'); }
    }
  };

  useEffect(() => { loadAll(); loadRisk(false); }, []);
  // LIVE FITTO (richiesta PM 23/07): rilettura /portfolio+decisioni ogni 30s e
  // FORZATURA /prices/update ogni 60s (il throttle server e' 30s: restiamo
  // larghi) — senza la forzatura i prezzi in DB si muovono solo col task
  // schedulato ~15min. Con finestra nascosta si salta il giro (niente quota
  // provider bruciata a vuoto). Risk resta a 5min. Delay feed ~15' dichiarato
  // in pagina (label RT 15').
  const forcing = useRef(false); // anti-accavallamento: il giro provider sui 27 ticker puo' durare piu' del tick
  useEffect(() => {
    const a = setInterval(() => { if (!document.hidden) loadAll(true); }, 30 * 1000);
    const f = setInterval(() => {
      if (document.hidden || forcing.current) return;
      forcing.current = true;
      Bellomberg.updatePrices().then(() => loadAll(true)).catch(() => {})
        .finally(() => { forcing.current = false; });
    }, 60 * 1000);
    const b = setInterval(() => loadRisk(false), 5 * 60 * 1000);
    return () => { clearInterval(a); clearInterval(f); clearInterval(b); };
  }, []);

  // click ticker → pagina MKT (stesso aggancio di Watchlist/CommandPalette, B-UX2)
  const goMkt = (tk: string) => {
    try { sessionStorage.setItem('bb:mktTicker', tk); } catch {}
    navigate('/market');
  };

  if (loading || !snap) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] gap-3 font-mono">
        <Cpu className="text-amber animate-pulse" size={32} />
        <div className="text-amber text-2xs uppercase tracking-[0.3em]">Loading terminal data...</div>
        <div className="text-faint text-2xs">Polling SQLite + FX feed</div>
      </div>
    );
  }

  const positions = Array.isArray(snap.positions) ? snap.positions : [];
  const values = portfolioValues(snap);
  const nav = values.invested;
  const cash = values.cash;
  const navTotal = values.nav;
  const totPl = Number(snap.totale_pl_eur) || 0;
  const isPositive = totPl >= 0;
  // fix 30c: P&L TOTALE ITD (unrealized + realized + dividendi) dalla contabilita' da fondo
  const totRet = (navHist && !navHist.error && navHist.final_total_return_eur != null)
    ? Number(navHist.final_total_return_eur) : null;
  const totRetPct = (totRet != null && navHist?.final_total_return_pct != null)
    ? Number(navHist.final_total_return_pct) : null;
  const investedPct = navTotal !== null && navTotal > 0 && nav !== null ? (nav / navTotal) * 100 : null;
  const cashPct = navTotal !== null && navTotal > 0 && cash !== null ? (cash / navTotal) * 100 : null;
  const maxDd = risk && !risk.error ? risk.portfolio.max_dd_1y_pct : null;

  const blotterRows = [...positions].sort((a, b) => (b.pl_eur || 0) - (a.pl_eur || 0));

  // P&L DAILY: formula condivisa F1/F2 in lib/dailypl (contratto (28), n.d. dichiarato)
  const { dayTot, dayPartial, dayTotPct } = computeDailyPnl(blotterRows);
  const nStale = snap.stale_positions?.length || 0;

  const actCls = (a: string) => {
    const u = (a || '').toUpperCase();
    if (u.startsWith('RESEARCH')) return 'res';
    if (u.startsWith('HOLD')) return 'hold';
    if (u.startsWith('BUY') || u.startsWith('ADD')) return 'buy';
    if (u.startsWith('SELL') || u.startsWith('TRIM')) return 'sell';
    return 'hold';
  };

  // gauge capitale investito: corona tacche + arco (dati VERI dallo snapshot; JSX puro, no innerHTML)
  const gaugeEls = (() => {
    if (investedPct === null) return [];
    const C = 58, R = 47;
    const A0 = -Math.PI * 0.78, A1 = Math.PI * 0.78;
    const frac = Math.min(1, investedPct / 100);
    const els: React.ReactNode[] = [];
    for (let i = 0; i < 60; i++) {
      const a = A0 + (i / 59) * (A1 - A0);
      const on = i / 59 <= frac, maj = i % 5 === 0;
      els.push(<line key={'t' + i}
        x1={C + Math.cos(a) * (R + 4)} y1={C + Math.sin(a) * (R + 4)}
        x2={C + Math.cos(a) * (R + (maj ? 9 : 6))} y2={C + Math.sin(a) * (R + (maj ? 9 : 6))}
        stroke={on ? 'rgba(255,165,30,.8)' : 'rgba(38,51,92,.85)'} strokeWidth={maj ? 1.3 : 0.8} />);
    }
    const arc = (key: string, r: number, a0: number, a1: number, col: string, w: number) => {
      const x0 = C + Math.cos(a0) * r, y0 = C + Math.sin(a0) * r, x1 = C + Math.cos(a1) * r, y1 = C + Math.sin(a1) * r;
      const large = (a1 - a0) > Math.PI ? 1 : 0;
      return <path key={key} d={'M ' + x0 + ' ' + y0 + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + x1 + ' ' + y1} fill="none" stroke={col} strokeWidth={w} />;
    };
    els.push(arc('bg', R, A0, A1, '#141B30', 5));
    els.push(arc('fg', R, A0, A0 + (A1 - A0) * frac, '#FFA51E', 5));
    return els;
  })();

  return (
    <div className="f1c animate-fadeIn">
      {runMsg && <div className="runline num"><span className="ld" style={{ background: '#29D3F2', boxShadow: '0 0 7px rgba(41,211,242,.8)' }} />{runMsg}</div>}

      {/* ══ COL 1: STRUMENTO NAV + AI DESK ══ */}
      <div className="col">
        <div className="p3 hero" style={{ flexShrink: 0 }}>
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          <div className="p3h am">NAV // INV + CASH
            <span className="side num">
              {snap.timestamp ? new Date(snap.timestamp).toLocaleTimeString('it-IT', { hour12: false, hour: '2-digit', minute: '2-digit' }) : '-'} · {((snap.source || '-').split(/[\s_]/)[0]).toUpperCase()}
            </span>
          </div>
          <div className="navv num">
            {/* (F40) L'OCCHIELLO: un valore quota da solo non vuol dire niente, e la
                base senza la sua data nemmeno. In coda, il TIMBRO: l'orologio
                DELLA CIFRA GRANDE. ⚠️ Sui giorni PASSATI dice «CHIUSURA», ma
                sull'ultimo dice «IN CORSO» — la prima versione affermava che
                fosse una chiusura ed era falso: lo snapshot di oggi è
                riscritto a ogni giro dell'updater (misurato il 21/08: la quota
                si è mossa di quasi tre decimi fra le 16:13 e le 16:23, stessa
                data e stesso regime).
                ⚠️ Il timbro sta QUI e non sulla riga della spiegazione, ed è
                una misura: a 1440×900 questa colonna dà 237px utili in
                JetBrains Mono 10px, l'occhiello ne chiede 341 e va a capo
                comunque — la seconda riga è mezza vuota e il timbro ci entra
                a costo zero. Appeso alla spiegazione la portava invece da due
                capi a TRE (548px → 695px). */}
            <div className={'occhiello' + (cima.allarme ? ' allarme' : '')}
                 title={cima.nota || undefined}>
              {cima.occhiello}
              {cima.timbro && <span className="timbro"> · {cima.timbro}</span>}
            </div>
            {/* ⚠️ la formattazione la decide il MODULO (`formattaCifra`), non
                questa riga: qui c'era `cima.tipo === 'quota' ? fmtNum : fmtEUR`,
                cioe' un SECONDO GIUDICE sulla stessa cosa — e la batteria
                misurava un campo `cifra` che la pagina non usava. */}
            <div className={'v' + (cima.allarme ? ' allarme' : '')}>
              {formattaCifra(cima, cifraDisp)}
            </div>
            <div className={'sub spiega' + (cima.allarme ? ' allarme' : '')}>{cima.sotto}</div>
            {/* gli euro qui sono ARROTONDATI: i centesimi esatti stanno sulla
                riga sotto (chiusura ufficiale), e nei 237px di questa colonna
                valevano una TERZA riga a capo (misurato: 45px → 30px). */}
            <div className="sub">
              {cima.tipo === 'quota' && <>PATRIMONIO {fmtEUR(navTotal, false, 0)} · </>}
              INV {fmtEUR(nav, false, 0)} · CASH {fmtEUR(cash, false, 0)} · {snap.n_positions || positions.length} POS
              {values.note && <span className="ko" role="status"> · {values.note}</span>}
              {/* il versamento sta sulla riga del PATRIMONIO perché è il
                  patrimonio che muove — ed è esattamente la confusione che
                  questo lotto toglie: il 12/08 il patrimonio è salito di due
                  cifre percentuali e la quota di pochi centesimi, con un
                  bonifico grande in mezzo. */}
              {flussoOggi && <> · <span style={{ color: '#FFA51E' }}>{flussoOggi}</span></>}
            </div>
            {twrRec && twrRec.last_snapshot_nav_eur != null && (
              <div className="sub">
                CHIUSURA UFF. {String(twrRec.last_snapshot_date || '').slice(5)} <span style={{ color: '#29D3F2' }}>{fmtEUR(twrRec.last_snapshot_nav_eur)}</span>
                {/* lo spazio sta FUORI dallo span: e' l'opportunita' di a-capo
                    che permette allo span nowrap di scendere INTERO (senza, a
                    NAV a 7 cifre + BREACH la riga sborderebbe muta) */}
                {twrRec.delta_pct != null && (<>
                  {' '}<span style={{ color: (twrRec.breach ?? Math.abs(twrRec.delta_pct) > 1) ? '#FFA51E' : '#8D9FC4' }}
                        title={twrRec.tolerance_pct != null ? 'allarme dal backend: tolleranza ' + twrRec.tolerance_pct + '%' : 'soglia locale 1% (backend pre-riavvio senza campo breach)'}>
                    · Δ LIVE {twrRec.delta_pct >= 0 ? '+' : ''}{twrRec.delta_pct.toFixed(2)}%{twrRec.breach ? ' ⚠ BREACH' : ''}
                  </span>
                </>)}
              </div>
            )}
            <div className="chips">
              {totRetPct != null
                ? <span className={'chip ' + (totRetPct >= 0 ? 'g' : 'r')}
                        title="ITD = rendimento sul capitale investito (utile+dividendi+realizzato diviso il costo). NON e' il TWR della cifra grande, che i versamenti non li conta: i due numeri sono vicini ma diversi, e possono divergere.">
                    ITD {fmtPct(totRetPct)}</span>
                : <span className="chip n"
                        title={navHistMotivo
                          ? 'ITD non disponibile: ' + navHistMotivo
                          : 'ITD non disponibile: il motore ha risposto, ma senza il rendimento sul capitale investito'}>
                    ITD n.d.</span>}
              {/* (F40) il chip «TWR …% GIPS» è SPARITO: è diventato il titolo
                  della pagina. Tenerlo direbbe due volte la stessa cosa, e a
                  due righe di distanza dalla cifra grande che lo dice meglio. */}
              <span className={'chip ' + (isPositive ? 'g' : 'r')}>UNRLZ {fmtEUR(totPl, true)}</span>
            </div>
          </div>
          <div className="gaugebox">
            <div className="gauge">
              <svg viewBox="0 0 116 116">{gaugeEls}</svg>
              <div className="in num">
                <span className="gk">INVESTITO</span>
                <span className="gv">{investedPct === null ? 'n.d.' : investedPct.toFixed(1) + '%'}</span>
                <span className="gs">cash {cashPct === null ? 'n.d.' : cashPct.toFixed(1) + '%'}</span>
              </div>
            </div>
            <div className="kside num">
              <div><div className="krow"><span className="kk">P&L GG</span>
                <span className="kv kvx"
                      style={{ fontWeight: 600, color: dayTot == null ? '#8D9FC4' : dayTot >= 0 ? '#21E0A0' : '#FF3D60' }}
                      title={dayTot == null
                        ? 'prev_close/FX non ancora nel payload: riavviare il backend per il P&L daily'
                        : 'P&L di giornata dell’INTERO portafoglio vs chiusura precedente (live, refresh 30/60s)'
                          + (dayPartial ? ' — PARZIALE: posizioni senza prev_close/FX escluse (n.d.)' : '')
                          + (dayTotPct != null ? ' · % sul valore investito di ieri · barra: fondo scala 5%' : '')}>
                  <FlashVal k="__plgg" value={dayTot}>{dayTot != null ? fmtEUR(dayTot, true) : 'n.d.'}</FlashVal>
                  {dayTotPct != null && <span className="kvs"> {(dayTotPct >= 0 ? '+' : '') + dayTotPct.toFixed(2)}%</span>}
                  {dayPartial && <span className="kvs" style={{ color: '#B97A00' }}> ±PARZ</span>}
                </span></div>
                <div className="meter"><i style={{ width: Math.min(100, Math.abs(dayTotPct ?? 0) * 20) + '%', background: dayTot == null ? '#414B68' : dayTot >= 0 ? '#21E0A0' : '#FF3D60' }} /></div></div>
              <div><div className="krow"><span className="kk">P&L ITD</span><span className="kv" style={{ fontWeight: 600, color: (totRet ?? 0) >= 0 ? '#21E0A0' : '#FF3D60' }}>{totRet != null ? fmtEUR(totRet, true) : 'n.d.'}</span></div>
                <div className="meter"><i style={{ width: Math.min(100, Math.abs(totRetPct ?? 0) * 8) + '%', background: (totRet ?? 0) >= 0 ? '#21E0A0' : '#FF3D60' }} /></div></div>
              <div><div className="krow"><span className="kk">Unrlz live</span><span className="kv" style={{ fontWeight: 600, color: isPositive ? '#21E0A0' : '#FF3D60' }}>{fmtEUR(totPl, true)}</span></div>
                <div className="meter"><i style={{ width: Math.min(100, Math.abs(totPl) / Math.max(1, Math.abs(totRet ?? totPl)) * 100) + '%', background: isPositive ? '#21E0A0' : '#FF3D60' }} /></div></div>
              <div><div className="krow"><span className="kk">Dry powder</span><span className="kv">{cashPct === null ? 'n.d.' : cashPct.toFixed(1) + '%'}</span></div>
                {cashPct !== null && <div className="meter"><i style={{ width: cashPct + '%', background: '#95A1BA' }} /></div>}</div>
              <div><div className="krow"><span className="kk">Max DD 1Y</span><span className="kv" style={{ color: '#FFA51E' }}>{maxDd != null ? maxDd.toFixed(1) + '%' : 'n.d.'}</span></div>
                <div className="meter"><i style={{ width: Math.min(100, Math.abs(maxDd ?? 0) / 30 * 100) + '%', background: '#FFA51E' }} /></div></div>
            </div>
          </div>
          <div className="acts">
            <button className="abtn cy" onClick={refreshPrices} disabled={refreshing}>
              <RefreshCw size={10} className={refreshing ? 'animate-spin' : ''} />{refreshing ? 'UPDATING' : 'REFRESH PRICES'}
            </button>
            <button className="abtn am" onClick={() => setAskRun(true)}><Play size={10} />RUN CONSIGLIERE</button>
            <RunConfirmDialog open={askRun}
              onConfirm={() => { setAskRun(false); triggerRun(); }}
              onCancel={() => setAskRun(false)} />
          </div>
        </div>

        <AiDeskPanel />
        <MacroPulse onSelect={goMkt} />
      </div>

      {/* ══ COL 2: EQUITY + MARKET TV + HEAT ══ */}
      <div className="col">
        <div className="p3 cy eqp">
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          {/* (F41) COSA DISEGNA QUESTA CURVA — la storia completa, con le
              misure, sta in `lib/curva.ts`. In due righe: fino al 21/08 qui
              c'era `nav_total_eur` (book + cassa di OGGI costante su ogni
              giorno passato), il (F40) ne ha tolto le frasi false
              dall'intestazione — «GIPS», «CHIUSURE UFFICIALI», «6M» — ma la
              LINEA era sbagliata lo stesso: errore che cambia segno e un
              rendimento a tre cifre in piedino dove quello vero e' circa +11%.
              Dal (F41) la linea e' il valore quota, e gli euro veri stanno
              nella seconda vista e nel crosshair.

              ⚠️ Il titolo NON e' scritto qui: viene da `titoloVista`, perche'
              serve anche in attesa e in errore, e due posti che scrivono lo
              stesso nome sono due posti che possono divergere. */}
          <div className="p3h" title={spiegaCurva(curva)}>
            {titoloVista(vistaCurva)}
            <span className="n">{curva.stato === 'viva' ? curva.nota : ''}</span>
            <span className="side curva-sw" role="group" aria-label="vista della curva">
              <button type="button" className={vistaCurva === 'quota' ? 'on' : ''}
                      aria-pressed={vistaCurva === 'quota'}
                      onClick={() => setVistaCurva('quota')}
                      disabled={!!curvaSpenta}
                      title={curvaSpenta
                        || 'Il valore quota: il rendimento al netto dei versamenti, su tutta la finestra.'}>
                {etichettaVista('quota')}
              </button>
              <span aria-hidden="true">·</span>
              {/* ⚠️ spento CON IL MOTIVO, mai spento e basta: senza snapshot NAV
                  il patrimonio vero non esiste, e dirlo e' il punto. */}
              <button type="button" className={vistaCurva === 'patrimonio' ? 'on' : ''}
                      aria-pressed={vistaCurva === 'patrimonio'}
                      onClick={() => setVistaCurva('patrimonio')}
                      disabled={!!patrimonioSpento}
                      title={patrimonioSpento
                        || 'Gli euro veri degli snapshot NAV: posizioni piu\' la cassa di quel giorno.'}>
                {etichettaVista('patrimonio')}
              </button>
            </span>
          </div>
          <div style={{ padding: '8px 12px 6px' }}>
            <NavSpark c={curva} hClass="h-36" />
          </div>
        </div>

        <MarketPanel tickers={positions.map(p => p.ticker)} onOpenMkt={goMkt} />

        <div className="p3 heatp">
          <div className="p3h">BOOK HEAT // PESO × P/L <span className="side">AREA = PESO · COLORE = P/L · CLICK → MKT</span></div>
          <HeatStrip positions={positions} limit={10} onSelect={goMkt} />
        </div>
      </div>

      {/* ══ COL 3: DECISIONI + BLOTTER + TELEMETRIA ══ */}
      <div className="col">
        <div className="p3" style={{ flexShrink: 0 }}>
          <span className="tick tl" /><span className="tick tr" /><span className="tick bl" /><span className="tick br" />
          <div className="p3h am">PENDING DECISIONS <span style={{ color: '#FFC555' }}>{decisions.length}</span>
            <span className="side" style={{ cursor: 'pointer' }} onClick={() => navigate('/decisions')}>DECN ▸</span>
          </div>
          <div className="num" style={{ maxHeight: 176, overflowY: 'auto' }}>
            {decisions.length === 0 ? (
              <div className="text-muted text-2xs font-mono py-4 text-center">NO PENDING ACTIONS</div>
            ) : decisions.map(d => (
              <div className="dec" key={d.id} onClick={() => navigate('/decisions')}>
                <span className="ld a" />
                <span className={'act ' + actCls(d.action)}>{d.action}</span>
                <span className="tkk">{d.ticker}</span>
                <span className="note">{[(d.eur_amount != null && d.eur_amount !== 0) ? fmtEUR(d.eur_amount, true) : null, d.timing].filter(Boolean).join(' · ') || '—'}</span>
                <span className="id">#{d.id}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="p3 blot" style={{ flex: 1, minHeight: 0 }}>
          <div className="p3h">BLOTTER <span className="n">· {positions.length} ACTIVE · TOP P/L</span>
            {dayTot != null && (
              <span className={'chip ' + (dayTot >= 0 ? 'g' : 'r')}
                    title={dayPartial ? 'somma PARZIALE: alcune posizioni senza prev_close/FX (n.d.)' : 'P&L di giornata vs chiusura precedente'}>
                GG {fmtEUR(dayTot, true)}{dayPartial ? ' ±' : ''}
              </span>
            )}
            {nStale > 0 && (
              <span className="chip a" title={'PREZZO STALE — P&L n.d. (al costo): ' + (snap.stale_positions || []).join(', ')}>
                {nStale} STALE
              </span>
            )}
            <span className="side">CLICK TICKER → MKT</span>
          </div>
          <div className="tscroll" style={{ flex: 1 }}>
            <table className="num">
              <thead><tr>
                <th>Ticker</th><th className="c-qty">Qty</th><th>Live</th><th>GG %</th>
                <th className="c-plg">P/L GG</th><th className="c-val">Val EUR</th>
                <th>P/L EUR</th><th className="c-plpct">P/L %</th><th className="c-wt">WT</th>
              </tr></thead>
              <tbody>
                {blotterRows.map(p => {
                  const dp = dayPct(p), de = dayEur(p);
                  return (
                    <tr key={p.ticker}>
                      <td className="tk" onClick={() => goMkt(p.ticker)} title={(p.nome || p.ticker) + ' · click → MKT'}>{p.ticker}</td>
                      <td className="text-muted c-qty">{fmtNum(p.quantita, 0)}</td>
                      <td style={{ color: '#29D3F2' }}>
                        {p.price_stale
                          ? <span className="chip a" title={'PREZZO STALE — riga al COSTO, P&L n.d. (' + (p.price_source || 'fonte n.d.') + ')'}>STALE</span>
                          : <FlashPx k={p.ticker} value={p.prezzo_live} />}
                      </td>
                      <td className={dp == null ? 'text-muted' : dp >= 0 ? 'up' : 'dn'}
                          title={p.prev_close_ts ? 'vs chiusura ' + String(p.prev_close_ts).slice(0, 10) : 'prev_close n.d. (storico prezzi o backend da riavviare)'}>
                        {dp != null ? fmtPct(dp) : 'n.d.'}
                      </td>
                      <td className={'c-plg ' + (de == null ? 'text-muted' : de >= 0 ? 'up' : 'dn')}>{de != null ? fmtEUR(de, true) : 'n.d.'}</td>
                      <td className="c-val">{fmtEUR(p.valore_mercato || 0)}</td>
                      <td className={(p.pl_eur || 0) >= 0 ? 'up' : 'dn'}>{p.pl_eur != null ? fmtEUR(p.pl_eur, true) : 'n.d.'}</td>
                      <td className={'c-plpct ' + ((p.pl_pct || 0) >= 0 ? 'up' : 'dn')}>{p.pl_pct != null ? fmtPct(p.pl_pct) : 'n.d.'}</td>
                      <td className="text-muted c-wt">{(p.peso_pct ?? 0).toFixed(1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <TelemetryRisk risk={risk} loading={riskLoading} onRefresh={() => loadRisk(true)} />
      </div>
    </div>
  );
}
