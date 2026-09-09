import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import {
  LayoutDashboard, FileText, MessageSquare, CheckSquare, ClipboardList,
  Settings as SettingsIcon, TrendingUp, Activity, Zap, Cpu, Wallet, Newspaper, PieChart, LineChart, Waves, Radar, ArrowLeftRight, Globe, Star, FileSpreadsheet
} from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import SettingsPanel from './SettingsPanel';

import { PAGE_DESTINATIONS, SETTINGS_DESTINATION } from '../lib/navigation';

const icons: Record<string, typeof LayoutDashboard> = {
  dashboard: LayoutDashboard, performance: LineChart, watchlist: Star,
  market: Globe, news: Newspaper, fundamentals: FileSpreadsheet,
  factors: PieChart, montecarlo: Cpu, vol: Waves, edge: Radar,
  chat: MessageSquare, agents: Activity, progress: TrendingUp, memos: FileText,
  decisions: CheckSquare, trades: Wallet, movements: ArrowLeftRight, mandato: ClipboardList,
};
const nav = PAGE_DESTINATIONS.map(entry => ({ ...entry, icon: icons[entry.id] }));
const KEY_SETTINGS = SETTINGS_DESTINATION.key;

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const i = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(i);
  }, [intervalMs]);
  return now;
}

// 206-UI: telemetria VERA (engine dal model, conteggio agenti, stato run) - ora inline nel footer
const fmtModel = (m: string) =>
  m.replace('claude-', '').replace(/-\d{8}$/, '').toUpperCase().replace(/-/g, ' ');

function useTelemetry() {
  const [tel, setTel] = useState<{ engine: string; agents: string; running: boolean; ok: boolean }>(
    { engine: '...', agents: '...', running: false, ok: true });
  useEffect(() => {
    let mounted = true;
    // Mai un 8 inventato: senza payload il conteggio è n.d. (regola 14/07)
    let total: number | null = null;
    Bellomberg.agentsList().then(r => {
      total = r.agents?.length || null;
      // ENGINE = motore del COMITATO (engines.committee_r1_r2, voce ponte 26/07);
      // agents[].model è il modello CHAT e qui mentirebbe. engines assente
      // (backend pre-riavvio) o import fallito = buco dichiarato, mai proxy.
      const eng = r.engines;
      const m = eng?.committee_r1_r2 ? fmtModel(eng.committee_r1_r2)
        : eng?.committee_r1_r2_error ? 'ERR: ' + eng.committee_r1_r2_error
        : 'N.D. (RIAVVIO BACKEND)';
      if (mounted) setTel(t => ({ ...t, engine: m, agents: total != null ? total + ' READY' : 'N.D.' }));
    }).catch(() => { if (mounted) setTel(t => ({ ...t, engine: 'OFFLINE', agents: 'N.D.', ok: false })); });
    const poll = async () => {
      try {
        const live = await Bellomberg.agentsLive();
        if (!mounted) return;
        if (live.running) {
          const st = live.specialist_status || {};
          const done = Object.values(st).filter(v => v === 'done').length;
          const tot = Object.keys(st).length || total;
          setTel(t => ({ ...t, agents: done + '/' + (tot != null ? tot : 'n.d.') + ' RUN', running: true, ok: true }));
        } else {
          setTel(t => ({ ...t, agents: total != null ? total + ' READY' : 'N.D.', running: false, ok: true }));
        }
      } catch { if (mounted) setTel(t => ({ ...t, ok: false })); }
    };
    poll();
    const i = setInterval(poll, 15000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return tel;
}

function useBackendHealth() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const ping = async () => {
      if (inFlight) return;
      inFlight = true;
      try { await Bellomberg.health(); if (mounted) setOk(true); }
      catch { if (mounted) setOk(false); }
      finally { inFlight = false; }
    };
    ping();
    const i = setInterval(ping, 15000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return ok;
}

function useFx() {
  const [fx, setFx] = useState<Record<string, number>>({});
  // Staleness DICHIARATA (regola 14/07): dopo il primo successo i fallimenti
  // erano muti e il nastro mostrava quote stantie sotto l'etichetta "FX 60s"
  const [fxAt, setFxAt] = useState<number | null>(null);
  const [fxErr, setFxErr] = useState(false);
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const r = await Bellomberg.fx();
        if (mounted && r && r.rates && typeof r.rates === 'object') {
          const clean: Record<string, number> = {};
          for (const [k, v] of Object.entries(r.rates)) {
            if (typeof v === 'number' && isFinite(v)) clean[k] = v;
          }
          setFx(clean); setFxAt(Date.now()); setFxErr(false);
        }
      } catch { if (mounted) setFxErr(true); }
      finally { inFlight = false; }
    };
    tick();
    const i = setInterval(tick, 60000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return { fx, fxAt, fxErr };
}

/* Il pallino d'allarme sulla rotellina: e' la ragione per cui togliere
   le impostazioni dalle pagine e' un GUADAGNO e non una perdita. Prima
   il PM doveva aprire F11 per scoprire che il salvataggio notturno del
   database era in errore — e la pagina non glielo diceva comunque,
   perche' `LastTaskResult` non era reso da nessuna parte. Ora l'allarme
   lo raggiunge da qualunque pagina.

   Un lavoro SPENTO DI PROPOSITO non e' un guasto: il Consigliere e'
   disattivato dal 03/07 e porta ancora il suo vecchio esito. Contarlo
   qui accenderebbe il pallino per sempre, e un allarme sempre acceso
   e' un allarme spento. */
function useTaskAlarm() {
  // null = NON MISURATO (regola 14/07). Spegnere il pallino su un fetch
  // fallito direbbe "tutto a posto" senza saperlo: e' il fallback muto
  // proprio sull'oggetto che deve avvisare. Tre stati, non due.
  const [guasti, setGuasti] = useState<number | null>(null);
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const r = await Bellomberg.scheduledTasks();
        if (!mounted) return;
        setGuasti((r.tasks || []).filter((t: any) =>
          String(t.State).toLowerCase() !== 'disabled' &&
          t.LastTaskResult !== 0 && t.LastTaskResult != null).length);
      } catch {
        if (mounted) setGuasti(null);   // buco DICHIARATO, non "zero guasti"
      }
    };
    tick();
    const i = setInterval(tick, 60000);
    return () => { mounted = false; clearInterval(i); };
  }, []);
  return guasti;
}

function marketStatus(now: Date) {
  const dayUTC = now.getUTCDay();
  const minUTC = now.getUTCHours() * 60 + now.getUTCMinutes();
  const weekday = dayUTC >= 1 && dayUTC <= 5;
  const nyse = weekday && minUTC >= 14*60+30 && minUTC < 21*60;
  const lse  = weekday && minUTC >= 8*60 && minUTC < 16*60+30;
  const eu   = weekday && minUTC >= 8*60 && minUTC < 16*60+30;
  const hk   = weekday && minUTC >= 1*60+30 && minUTC < 8*60;
  return [
    { label: 'NY', live: nyse, color: nyse ? '#21e0a0' : '#3d4763' },
    { label: 'LN', live: lse,  color: lse  ? '#21e0a0' : '#3d4763' },
    { label: 'MI', live: eu,   color: eu   ? '#21e0a0' : '#3d4763' },
    { label: 'HK', live: hk,   color: hk   ? '#21e0a0' : '#3d4763' },
  ];
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const now = useNow();
  const health = useBackendHealth();
  const { fx, fxAt, fxErr } = useFx();
  const tel = useTelemetry();
  const guasti = useTaskAlarm();
  const [cfgOpen, setCfgOpen] = useState(false);
  const markets = marketStatus(now);
  const navigate = useNavigate();
  const location = useLocation();
  const current = nav.find(n => location.pathname.startsWith(n.to));

  // Function keys use the same registry as the menu and command palette.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (!/^F\d+$/.test(e.key)) return;
      if (e.key === KEY_SETTINGS) {
        e.preventDefault();
        setCfgOpen(o => !o);
        return;
      }
      const target = nav.find(n => n.key === e.key);
      if (target) {
        e.preventDefault();
        navigate(target.to);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate]);

  // la palette (CTRL+K) apre lo stesso pannello: un solo posto, due strade
  useEffect(() => {
    const onOpen = () => setCfgOpen(true);
    window.addEventListener('bb:settings', onOpen as EventListener);
    return () => window.removeEventListener('bb:settings', onOpen as EventListener);
  }, []);

  /* Col pannello aperto tutto cio' che sta DIETRO e' inerte: non si legge
     (c'e' il velo sopra) e non si deve poter navigare col tab. La fascia in
     alto NO: ci vive la rotellina, che e' anche il bottone per richiudere.
     Il marcatore serve anche al cancello (qa_app.py), che altrimenti
     misurerebbe il contrasto della pagina dietro il velo e riferirebbe
     difetti che non esistono. */
  const dietro = cfgOpen ? { 'data-inerte': '', 'aria-hidden': true } : {};

  const timeStr = now.toLocaleTimeString('it-IT', { hour12: false });
  const dateStr = now.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
  const tzOffset = now.getTimezoneOffset();

  return (
    <div className="flex flex-col h-screen bg-bg overflow-hidden">

      {/* ===== TIER 1: plancia di comando ===== */}
      {/* z-45: la fascia sta SOPRA il velo del pannello impostazioni (z-40).
          La rotellina resta accesa e cliccabile mentre il pannello e' aperto,
          perche' e' anche il bottone con cui lo richiudi: spegnerla insieme
          al resto sarebbe un vicolo cieco. */}
      <header className="relative z-[45] h-10 flex items-stretch border-b border-border bg-bg-elev shrink-0 text-2xs font-mono">
        <div className="flex items-center gap-2.5 px-3 border-r border-border bg-bg">
          <span className="inline-block w-2 h-2 bg-amber" style={{ boxShadow: '0 0 8px rgba(255,165,30,0.55)' }} />
          <span className="text-amber font-bold tracking-[0.28em] text-glow-amber">BELLOMBERG</span>
          <span className="text-faint text-3xs tracking-[0.2em]">v0.9 OBSIDIAN</span>
        </div>

        <div className="flex items-center gap-3 px-4 border-r border-border">
          {markets.map(m => (
            <div key={m.label} className="flex items-center gap-1.5">
              <span className="led" style={{ background: m.color, boxShadow: m.live ? '0 0 6px ' + m.color : 'none' }} />
              <span className={m.live ? 'text-text' : 'text-muted'}>{m.label}</span>
            </div>
          ))}
        </div>

        {/* OMNIBOX: il cuore del terminale (apre la palette) */}
        <button
          onClick={() => window.dispatchEvent(new Event('bb:palette'))}
          className="flex-1 flex items-center gap-2.5 px-4 mx-3 my-1.5 bg-bg border border-border hover:border-amber-deep hover:bg-panel transition-colors cursor-pointer text-left group"
          title="Command palette (Ctrl+K)">
          <span className="text-amber font-bold">&#10095;</span>
          <span className="text-faint group-hover:text-muted transition-colors tracking-wider">
            CERCA TICKER &middot; PAGINE &middot; AZIONI
          </span>
          <span className="ml-auto text-faint text-3xs border border-border px-1.5 py-0.5 tracking-wider">CTRL+K</span>
        </button>

        <div className="flex items-center gap-2 px-3 border-r border-l border-border">
          <span className={'led ' + (health ? 'led-green' : health === false ? 'led-red' : 'led-amber')} />
          <span className="text-muted">API</span>
          <span className={health ? 'text-emerald' : health === false ? 'text-crimson' : 'text-amber'}>
            {health === null ? 'PING' : health ? 'LIVE' : 'DOWN'}
          </span>
        </div>

        <div className="flex items-center gap-3 px-3">
          <span className="text-muted">{dateStr}</span>
          <span className="text-amber tabular-nums text-glow-amber">{timeStr}</span>
          <span className="text-faint">UTC{tzOffset <= 0 ? '+' : '-'}{Math.abs(Math.round(tzOffset/60))}</span>
        </div>

        {/* ── LA ROTELLINA — ultimo segmento, dopo l'orologio ──────────────
            Sta nella famiglia di DESTRA insieme ad API e ora: a sinistra
            l'identita', al centro l'azione, a destra lo stato della macchina.
            Il pallino rosso compare quando un lavoro automatico e' in errore. */}
        <button
          onClick={() => setCfgOpen(o => !o)}
          title={guasti == null
            ? 'IMPOSTAZIONI (F11) — stato dei lavori automatici NON LEGGIBILE (backend in errore)'
            : guasti > 0
              ? `IMPOSTAZIONI (F11) — ${guasti} lavoro automatico NON riuscito`
              : `IMPOSTAZIONI (${KEY_SETTINGS})`}
          aria-label="Impostazioni"
          aria-expanded={cfgOpen}
          className={'relative flex items-center gap-2 px-3.5 border-l border-border transition-colors ' +
            (cfgOpen ? 'text-amber bg-amber/10' : 'text-text-dim hover:text-amber hover:bg-bg-elev')}>
          <SettingsIcon size={14} />
          <span className="text-3xs text-muted tracking-wider">{KEY_SETTINGS}</span>
          {/* rosso = guasto misurato · ambra cava = stato NON leggibile · niente = tutto a posto */}
          {guasti == null ? (
            <span
              className="absolute top-1.5 right-1.5 w-[7px] h-[7px] rounded-full border border-amber"
              style={{ boxShadow: '0 0 0 2px #0b0e17' }}
            />
          ) : guasti > 0 ? (
            <span
              className="absolute top-1.5 right-1.5 w-[7px] h-[7px] rounded-full bg-crimson motion-safe:animate-pulse"
              style={{ boxShadow: '0 0 0 2px #0b0e17, 0 0 9px rgba(255,61,96,.85)' }}
            />
          ) : null}
        </button>
      </header>

      {/* ===== TIER 2: module bar orizzontale (niente sidebar: questo e' un terminale) ===== */}
      <nav {...dietro} aria-label="Moduli Bellomberg" className="h-10 flex items-stretch border-b border-border bg-bg shrink-0 font-mono overflow-x-auto overflow-y-hidden">
        {nav.map(({ to, short, label, group, icon: Icon, key }, index) => (
          <NavLink key={to} to={to} title={`${key} ? ${label} ? ${group}`}
            className={({ isActive }) =>
              'relative flex items-center gap-1.5 px-3 text-2xs tracking-wider whitespace-nowrap transition-colors border-r border-border/50 ' +
              (index > 0 && nav[index - 1].group !== group ? 'border-l-2 border-l-amber/25 ' : '') +
              (isActive ? 'text-amber bg-panel shadow-[inset_0_-2px_0_0_#ffa51e]' : 'text-text-dim hover:text-amber hover:bg-bg-elev')}
          >
            <span className="text-3xs text-muted tabular-nums">{key.slice(1).padStart(2, '0')}</span>
            <Icon size={12} />
            <span>{short}</span>
          </NavLink>
        ))}
        <button onClick={() => setCfgOpen(true)} title={`${KEY_SETTINGS} ? Impostazioni`}
          className="ml-auto flex items-center gap-1.5 px-3 text-2xs tracking-wider whitespace-nowrap border-l-2 border-amber/25 text-text-dim hover:text-amber hover:bg-bg-elev">
          <span className="text-3xs text-muted tabular-nums">{KEY_SETTINGS.slice(1)}</span>
          <SettingsIcon size={12} /><span>CONFIG</span>
        </button>
      </nav>

      {/* ===== TIER 3: contesto modulo + nastro FX ===== */}
      <div {...dietro} className="h-7 flex items-center border-b border-border bg-bg-elev/60 shrink-0 font-mono text-2xs px-3 gap-4">
        <span className="text-amber-deep uppercase tracking-[0.2em]">
          {current ? current.key + ' // ' + current.label : '//'}
        </span>
        <span className="text-faint">|</span>
        <div className="flex-1 flex items-center overflow-hidden gap-5">
          {Object.entries(fx).length === 0 ? (
            <span className={fxErr ? 'text-crimson' : 'text-faint'}>
              {fxErr ? 'FX NON DISPONIBILE — backend in errore' : 'awaiting fx feed...'}
            </span>
          ) : (
            Object.entries(fx).map(([c, r]) => {
              const safe = typeof r === 'number' && isFinite(r);
              return (
                <span key={c} className="flex items-center gap-1.5">
                  <span className="text-muted">{c}/EUR</span>
                  <span className="text-cyan tabular-nums">
                    {safe ? r.toFixed(c === 'GBX' ? 5 : 4) : '-'}
                  </span>
                </span>
              );
            })
          )}
        </div>
        {fxErr && fxAt != null ? (
          <span className="text-amber text-3xs uppercase tracking-wider" title="ultimo aggiornamento FX riuscito: le quote nel nastro sono STANTIE">
            FX STALE {Math.max(1, Math.round((now.getTime() - fxAt) / 60000))}M
          </span>
        ) : (
          <span className="text-faint text-3xs uppercase tracking-wider">FX 60s</span>
        )}
      </div>

      {/* ===== CONTENUTO: tutta la larghezza ===== */}
      <main {...dietro} className="flex-1 overflow-hidden bg-bg relative">
        <div className="absolute inset-0 pointer-events-none opacity-40 grid-hud" />
        <div className="relative p-4 h-full overflow-y-auto">
          {children}
        </div>
      </main>

      {/* ===== FOOTER: telemetria viva ===== */}
      <footer {...dietro} className="h-7 border-t border-border bg-bg-elev flex items-center text-3xs font-mono text-faint px-3 gap-5 shrink-0">
        <span className="text-amber-deep uppercase tracking-widest">[ Bellomberg &middot; Obsidian ]</span>
        <span className="flex items-center gap-1.5">
          <span className="text-faint uppercase">Engine</span>
          <span className="text-cyan">{tel.engine}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="text-faint uppercase">Agents</span>
          <span className={tel.running ? 'text-emerald' : 'text-amber'}>{tel.agents}</span>
        </span>
        <span className="flex items-center gap-1">
          {tel.running
            ? <span className="text-emerald flex items-center gap-1"><Zap size={9} /> RUN LIVE</span>
            : tel.ok
              ? <span className="text-cyan flex items-center gap-1"><Zap size={9} /> ONLINE</span>
              : <span className="text-crimson">OFFLINE</span>}
        </span>
        <span>SESSION {now.toISOString().slice(0,10)}</span>
        <span className="flex-1 text-right text-muted uppercase tracking-[0.15em]">
          Workspace locale &middot; Dati live &middot; F1-F19 &middot; CTRL+K
        </span>
      </footer>

      {/* F11 IMPOSTAZIONI: sta SOPRA la pagina, non al posto suo */}
      <SettingsPanel open={cfgOpen} onClose={() => setCfgOpen(false)} />
    </div>
  );
}
