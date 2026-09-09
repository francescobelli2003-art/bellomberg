import { useEffect, useMemo, useState } from 'react';
import { Bellomberg, ValuationModel, ValuationDetail, API_BASE } from '@/lib/api';
import { FileSpreadsheet, Download, AlertTriangle } from 'lucide-react';

// F17 Fundamentals — OPZIONE B scelta dal PM (17/07, mockup renderizzato, regola
// 15/07): master-detail stile terminal. Sinistra: tabella densa (un modello canonico
// per stock). Destra: pannello fisso col dettaglio del selezionato — FV grande,
// metodi, peer, IRR di holding, sanity, variant view, bottoni. Il dettaglio arriva
// dal sidecar VAL_X.payload.json (endpoint: campo 'detail'); assente = n.d.
// DICHIARATO finche' il modello non viene rigenerato.

const ORANGE = 'text-[#ff8c00]';
const HDR = `${ORANGE} uppercase text-[10px] tracking-[2px]`;

const pct = (v?: number | null, digits = 1) =>
  v == null ? 'n.d.' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;
const num = (v?: number | null) => (v == null ? 'n.d.' : v.toFixed(2));
const mln = (v?: number | null) =>
  v == null ? 'n.d.' : `${Math.round(v).toLocaleString('it-IT')} mln`;
// F17 v2 (opzione B, PM 22/07): etichette convenzione del motore RAB
const CONV_LABEL: Record<string, string> = {
  real_pretax: 'reale pre-tax (ARERA)',
  cpih_real_vanilla: 'CPIH-real vanilla (Ofgem)',
  analyst_pretax: 'pre-tax (analista)',
};
const CHIP = 'text-[9px] px-1 ml-1 border border-[#2c4a7a] text-[#8ab4f8] align-middle';

// V5 mNAV (opzione B del mockup, PM 23/07): la "misura viva" del veicolo in PUNTI
// percentuali di premio/sconto (per la barra) e come etichetta breve (per la tabella).
const mnavPct = (dd?: ValuationDetail | null): number | null => {
  if (!dd || dd.engine !== 'mnav') return null;
  if (dd.profile_key === 'cef_nav') return dd.discount_to_nav_pct ?? null;
  const v = dd.profile_key === 'dat_bitcoin' ? dd.mnav_ev : dd.mnav;
  return v != null ? (v - 1) * 100 : null;
};
const mnavMeasureLabel = (dd?: ValuationDetail | null): string | null => {
  if (!dd || dd.engine !== 'mnav') return null;
  if (dd.profile_key === 'cef_nav')
    return dd.discount_to_nav_pct != null ? `${dd.discount_to_nav_pct.toFixed(1)}%` : null;
  const v = dd.profile_key === 'dat_bitcoin' ? dd.mnav_ev : dd.mnav;
  return v != null ? `${v.toFixed(2)}x` : null;
};

// Barra premio/sconto (−50%…+50%): marker bianco = oggi, arancio = target analista.
// Fuori scala = marker clampato al bordo con l'etichetta che dice il valore vero.
function MnavBar({ now, target }: { now: number | null; target?: number | null }) {
  if (now == null) return null;
  const pos = (v: number) => Math.max(0, Math.min(100, 50 + v));
  const fuori = Math.abs(now) > 50 ? ' (fuori scala)' : '';
  return (
    <div className="relative h-12 mt-2 mb-1">
      <div className="absolute left-0 right-0 top-[18px] h-1.5"
           style={{ background: 'linear-gradient(90deg,#5c1f1f 0%,#222 50%,#1f5c3a 100%)' }} />
      <div className="absolute top-[14px] w-px h-3.5 bg-[#888]" style={{ left: '50%' }} />
      {([[0, '−50%'], [25, '−25%'], [50, 'parità'], [75, '+25%'], [100, '+50%']] as [number, string][]).map(([x, lb]) => (
        <span key={lb} className="absolute top-[38px] text-[9px] text-faint"
              style={{ left: `${x}%`, transform: 'translateX(-50%)' }}>{lb}</span>
      ))}
      <span className="absolute top-0 text-[10px] text-white whitespace-nowrap"
            style={{ left: `${pos(now)}%`, transform: 'translateX(-50%)' }}>
        ▼ oggi {now > 0 ? '+' : ''}{now.toFixed(1)}%{fuori}
      </span>
      {target != null && (
        <span className="absolute top-[25px] text-[10px] text-[#ff8c00] whitespace-nowrap"
              style={{ left: `${pos(target)}%`, transform: 'translateX(-50%)' }}>
          ▲ target {target > 0 ? '+' : ''}{target.toFixed(1)}%
        </span>
      )}
    </div>
  );
}

export default function FundamentalsPage() {
  const [models, setModels] = useState<ValuationModel[]>([]);
  const [nota, setNota] = useState<string | undefined>();
  const [err, setErr] = useState<string | null>(null);
  const [selTicker, setSelTicker] = useState<string | null>(null);
  const [showOld, setShowOld] = useState(false);
  // avvisi SOTP collassati col count (opzione B): si riapre da zero a ogni cambio titolo
  const [sotpWarnOpen, setSotpWarnOpen] = useState(false);

  useEffect(() => {
    Bellomberg.valuationModels()
      .then(r => { setModels(r.models); setNota(r.nota); })
      .catch(e => setErr(e?.message || String(e)));
  }, []);

  // un modello "migliore" per ticker: canonico se esiste, altrimenti il piu' recente
  const { best, vecchi } = useMemo(() => {
    const byTicker = new Map<string, ValuationModel[]>();
    for (const m of models) {
      if (!byTicker.has(m.ticker)) byTicker.set(m.ticker, []);
      byTicker.get(m.ticker)!.push(m);
    }
    const best: ValuationModel[] = [];
    const vecchi: ValuationModel[] = [];
    for (const [, files] of byTicker) {
      const sorted = [...files].sort((a, b) => {
        if (a.canonical !== b.canonical) return a.canonical ? -1 : 1;
        return (b.generated_at || '').localeCompare(a.generated_at || '');
      });
      best.push(sorted[0]);
      vecchi.push(...sorted.slice(1));
    }
    // con numero prima (per fair value presente), poi per revisione
    best.sort((a, b) => {
      const an = a.fair_value != null ? 0 : 1;
      const bn = b.fair_value != null ? 0 : 1;
      if (an !== bn) return an - bn;
      return (b.generated_at || '').localeCompare(a.generated_at || '');
    });
    return { best, vecchi };
  }, [models]);

  const sel = useMemo(
    () => best.find(m => m.ticker === selTicker) || best[0] || null,
    [best, selTicker]);

  useEffect(() => { setSotpWarnOpen(false); }, [sel?.ticker]);

  const upsideCls = (m: ValuationModel) =>
    m.flagged || m.sanity_severity === 'WARN' ? 'text-muted'
      : (m.upside_pct ?? 0) >= 0 ? 'pl-positive' : 'pl-negative';

  const badge = (m: ValuationModel) => {
    // review 17/07 F2: OK verde solo con un giudizio sanity VERO — senza dato, n.d.
    const sev = m.sanity_severity ?? m.detail?.sanity?.severity;
    if (m.flagged) return <span className="text-[10px] px-1.5 border border-crimson text-crimson">FLAG</span>;
    if (sev === 'WARN') return <span className="text-[10px] px-1.5 border border-amber text-amber">WARN</span>;
    if (sev == null) return <span className="text-[10px] px-1.5 border border-border text-muted">n.d.</span>;
    return <span className="text-[10px] px-1.5 border border-emerald/50 text-emerald">OK</span>;
  };

  const d = sel?.detail;
  const hi = d?.holding_irr;
  // review 17/07 (ALTA): bank e insurance condividono lo STESSO motore/payload
  const isBank = ['bank', 'insurance'].includes(d?.engine || sel?.engine || '');
  // V7: ramo rete regolata (F17 v2 opzione B, PM 22/07)
  const isRab = (d?.engine || sel?.engine) === 'rab';
  // V5: ramo veicoli mNAV (opzione B del mockup, PM 23/07) — quando il FV e' n.d.
  // (lo stato normale senza nav_target, D2) la headline e' la MISURA del veicolo
  const isMnav = (d?.engine || sel?.engine) === 'mnav';
  // FV del veicolo: tesi se c'e', altrimenti il sidecar (stessa generazione del file
  // per costruzione: l'endpoint scarta i sidecar piu' vecchi di 300s)
  const mnavFv = isMnav ? (sel?.fair_value ?? d?.fair_value_nav ?? null) : null;
  const mnavNow = isMnav ? mnavPct(d) : null;
  const mnavTargetPct = isMnav && d?.nav_target != null ? (d.nav_target - 1) * 100 : null;
  const mnavUpside = isMnav
    ? (sel?.upside_pct ?? (mnavFv != null && d?.price ? (mnavFv / d.price - 1) * 100 : null))
    : null;
  // il SOTP e' un layer sopra qualunque motore: si mostra se il sidecar ha gli aggregati
  const hasSotp = d != null && (d.fair_value_sotp != null || d.sotp_n_segments != null);

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between border-b-2 border-[#ff8c00] pb-2">
        <h1 className={`text-lg font-bold font-mono ${ORANGE}`}>BELLOMBERG &lt;VAL&gt; — MODELLI DI VALUTAZIONE</h1>
        <span className="text-muted text-xs font-mono">
          {best.filter(m => m.fair_value != null).length} con numero · {best.filter(m => m.fair_value == null).length} in attesa di view
        </span>
      </div>
      {err && <p className="text-crimson text-sm">Errore backend: {err}</p>}
      {nota && <p className="text-amber text-xs">{nota}</p>}

      <div className="flex gap-0 border border-border bg-black/40 min-h-[420px]">
        {/* -------- sinistra: tabella densa -------- */}
        <div className="flex-[3] overflow-x-auto border-r border-border">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="bg-[#141414]">
                <th className={`text-left py-1.5 px-2 ${HDR}`}>Ticker</th>
                <th className={`text-right px-2 ${HDR}`}>FV</th>
                <th className={`text-right px-2 ${HDR}`}>Px</th>
                <th className={`text-right px-2 ${HDR}`}>Upside</th>
                <th className={`text-center px-2 ${HDR}`}>Sanity</th>
                <th className={`text-left px-2 ${HDR}`}>Rev</th>
              </tr>
            </thead>
            <tbody>
              {best.map(m => (
                <tr key={m.file} onClick={() => setSelTicker(m.ticker)}
                    className={`border-b border-border/20 cursor-pointer hover:bg-bg/40 ${sel?.ticker === m.ticker ? 'bg-[#16202b]' : ''}`}>
                  <td className="py-1.5 px-2 font-semibold text-white whitespace-nowrap">
                    {m.ticker}{sel?.ticker === m.ticker ? ' ◄' : ''}
                    {m.detail?.engine === 'rab' && <span className={CHIP}>RAB</span>}
                    {m.detail?.engine === 'mnav' &&
                      <span className={CHIP}>{m.detail?.profile_key === 'cef_nav' ? 'NAV' : 'mNAV'}</span>}
                    {(m.detail?.fair_value_sotp != null || m.detail?.sotp_n_segments != null) &&
                      <span className={CHIP}>SOTP</span>}
                    {!m.matched && <span className="text-faint text-[10px] ml-1">(non nel book)</span>}
                  </td>
                  {/* V5 opzione B: per i veicoli senza FV la colonna mostra la MISURA (1.03x / −33.9%) */}
                  <td className="text-right px-2">{m.fair_value != null ? m.fair_value.toFixed(2)
                    : <span className="text-muted">{mnavMeasureLabel(m.detail) ?? 'n.d.'}</span>}</td>
                  <td className="text-right px-2 text-muted">{m.price_at_thesis != null ? m.price_at_thesis.toFixed(2)
                    : m.detail?.engine === 'mnav' && m.detail?.price != null ? m.detail.price.toFixed(2) : '—'}</td>
                  <td className={`text-right px-2 ${upsideCls(m)}`}>
                    {m.upside_pct != null ? `${m.upside_pct > 0 ? '+' : ''}${m.upside_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-center px-2">{badge(m)}</td>
                  <td className="text-left px-2 text-muted text-xs whitespace-nowrap">{(m.generated_at || 'n.d.').slice(5, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {best.length === 0 && !err && (
            <p className="text-muted text-sm p-4">Nessun modello. La run del consigliere li genera; oppure scripts/rigenera_modelli.py.</p>
          )}
        </div>

        {/* -------- destra: pannello dettaglio -------- */}
        <div className="flex-[2] p-4 bg-[#0a0a0a] font-mono text-sm">
          {!sel ? (
            <p className="text-muted text-xs">Seleziona un titolo a sinistra.</p>
          ) : (
            <div className="space-y-3">
              <div className={`${HDR} border-b border-border pb-1`}>
                {sel.ticker} · motore {isRab ? 'RAB (RETE REGOLATA)'
                  : isMnav ? `MNAV (${d?.profile_key === 'cef_nav' ? 'FONDO CHIUSO'
                              : d?.profile_key === 'dat_hype' ? 'DAT HYPE' : 'DAT BITCOIN'})`
                  : (d?.engine || (sel.engine === 'VAL' ? 'valuation' : sel.engine)).toUpperCase()}
              </div>
              {/* V5 opzione B (PM 23/07): headline del veicolo = FV se dichiarato,
                  altrimenti la MISURA viva (sconto / mNAV EV / mNAV) — mai un grande
                  n.d. quando il numero informativo esiste */}
              {isMnav && d ? (
                <div>
                  {mnavFv != null ? (
                    <>
                      <span className="text-3xl text-white font-bold">{mnavFv.toFixed(2)}</span>
                      <span className="text-muted text-xs ml-2">
                        {d.payload_currency || 'USD'} fair value = NAV {num(d.nav_per_share)} × target {d.nav_target != null ? d.nav_target.toFixed(2) : 'n.d.'}
                      </span>
                      <p className="text-[10px] text-amber mt-0.5">
                        upside alla CONVERGENZA del premio/sconto dichiarato — NON un target price (D2)
                      </p>
                    </>
                  ) : (
                    <>
                      <span className="text-3xl text-white font-bold">
                        {d.profile_key === 'cef_nav'
                          ? (d.discount_to_nav_pct != null ? `${d.discount_to_nav_pct.toFixed(1)}%` : 'n.d.')
                          : d.profile_key === 'dat_bitcoin'
                            ? (d.mnav_ev != null ? `${d.mnav_ev.toFixed(3)}x`
                               : d.mnav_equity != null ? `${d.mnav_equity.toFixed(3)}x` : 'n.d.')
                            : (d.mnav != null ? `${d.mnav.toFixed(3)}x` : 'n.d.')}
                      </span>
                      <span className="text-muted text-xs ml-2">
                        {d.profile_key === 'cef_nav'
                          ? `sconto sul NAV — NAV/azione ${num(d.nav_per_share)} USD`
                          : d.profile_key === 'dat_bitcoin'
                            ? (d.mnav_ev != null
                               ? `mNAV EV — misura di giudizio (equity basic ${d.mnav_equity != null ? d.mnav_equity.toFixed(3) : 'n.d.'}x)`
                               : 'mNAV equity basic — EV n.d. DICHIARATO (campi del record assenti)')
                            : `mNAV su Adjusted NAV/FD ${d.nav_per_share != null ? d.nav_per_share.toFixed(4) : 'n.d.'}${d.mnav_dtl_addback != null ? ` · DTL add-back ${d.mnav_dtl_addback.toFixed(3)}x` : ''}`}
                      </span>
                      <p className="text-[10px] text-muted mt-0.5">
                        FV n.d. DICHIARATO — {d.fv_note || 'nessun nav_target dell’analista (D2)'}
                      </p>
                    </>
                  )}
                  <MnavBar now={mnavNow} target={mnavTargetPct} />
                </div>
              ) : (
              <div>
                <span className="text-3xl text-white font-bold">
                  {sel.fair_value != null ? sel.fair_value.toFixed(2) : 'n.d.'}
                </span>
                <span className="text-muted text-xs ml-2">
                  {d?.payload_currency ? `${d.payload_currency} ` : ''}fair value{sel.fair_value == null ? ' — in attesa di variant view' : ''}
                </span>
              </div>
              )}

              {/* F17 v2 opzione B (PM 22/07): mini-tabella metodi RAB col mediano evidenziato */}
              {isRab && d && (<>
                <div className="grid grid-cols-3 gap-2 text-center">
                  {([
                    ['EV/RAB premium', d.fair_value_ev_rab],
                    ['DDM regolato', d.fair_value_ddm_reg],
                    [d.peer_method || 'Peer P/E reg.', d.fair_value_peer],
                  ] as [string, number | null | undefined][]).map(([lbl, v]) => (
                    <div key={lbl}
                         className={`border p-1.5 ${v != null && v === d.fair_value_blend ? 'border-[#ff8c00] bg-[#161006]' : 'border-border'}`}>
                      <div className="text-[9px] uppercase tracking-[1px] text-muted">
                        {lbl}{v != null && v === d.fair_value_blend ? ' ◂ blend' : ''}
                      </div>
                      <div className={`text-base ${v == null ? 'text-muted' : 'text-white'}`}>{num(v)}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[10px] text-faint">
                  blend mediano {num(d.fair_value_blend)}{d.blend_methods?.length ? ` su ${d.blend_methods.length} metodi` : ''}
                  {d.methods_divergence != null ? ` · divergenza ${pct(d.methods_divergence, 0)}` : ''}
                  {d.fair_value_peer == null ? ' · peer non passato (dichiarato)' : ''}
                </p>
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>Scheda regolatoria{d.service ? ` — ${d.service.replace(/_/g, ' ')}` : ''}</div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    <span className="text-muted uppercase text-[10px]">rab base</span>
                    <span>{mln(d.rab_base)}{d.rab_base != null && <span className="text-faint"> (input analista, [src] nella view)</span>}</span>
                    <span className="text-muted uppercase text-[10px]">rendimento ammesso</span>
                    <span>{d.allowed_return_calc != null
                      ? <>{(d.allowed_return_calc * 100).toFixed(2)}% {d.convention === 'cpih_real_vanilla' ? 'CPIH-real' : 'reale pre-tax'}
                          {d.allowed_return_nominal != null &&
                            <span className="text-muted"> ({(d.allowed_return_nominal * 100).toFixed(2)}% nominale rif.)</span>}</>
                      : 'n.d.'}</span>
                    <span className="text-muted uppercase text-[10px]">convenzione</span>
                    <span>{d.convention ? (CONV_LABEL[d.convention] || d.convention) : 'n.d.'}</span>
                    <span className="text-muted uppercase text-[10px]">premio EV/RAB</span>
                    <span>{d.rab_premium != null ? `${d.rab_premium.toFixed(2)}x`
                      : <span className="text-muted">n.d. in pagina — nel foglio EV-RAB &amp; Peers (chiave sidecar: P3 post-collaudo)</span>}</span>
                    <span className="text-muted uppercase text-[10px]">ancora regolatoria</span>
                    <span>{d.anchor_stale === true
                      ? <span className="text-amber">STALE — periodo scaduto, riverificare la delibera</span>
                      : d.anchor_stale === false ? <span className="text-emerald">valida (non STALE)</span>
                      : <span className="text-muted">n.d.</span>}</span>
                  </div>
                </div>
                <p className="text-[10px] text-faint">
                  warning del motore (premio vs banda, finanziabilità dividendi, de-leverage) = nel foglio Thesis dell'Excel; portarli in pagina = P3 post-collaudo (chiave sidecar).
                </p>
              </>)}

              {/* V5 opzione B (PM 23/07): card Scheda veicolo — fonti, vintage, target (D2) */}
              {isMnav && d && (<>
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>
                    Scheda veicolo — {d.profile_key === 'cef_nav' ? 'NAV/sconto'
                      : d.profile_key === 'dat_hype' ? 'DAT HYPE' : 'DAT Bitcoin'}
                  </div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    {d.profile_key === 'cef_nav' && (<>
                      <span className="text-muted uppercase text-[10px]">nav/azione</span>
                      <span>{num(d.nav_per_share)} USD
                        {d.nav_vintage?.nav_as_of != null && <span className="text-faint"> · as-of {String(d.nav_vintage.nav_as_of)}{d.nav_vintage?.nav_age_days != null ? ` (${d.nav_vintage.nav_age_days}g)` : ''} · settimanale ufficiale</span>}
                      </span>
                      <span className="text-muted uppercase text-[10px]">quotazione</span>
                      <span>{d.price_quote != null ? `${d.price_quote.toLocaleString('it-IT')} ${d.price_quote_currency || ''} → ` : ''}{num(d.price)} USD
                        <span className="text-faint"> (tasso del tool, stessa misura del live)</span></span>
                    </>)}
                    {d.profile_key === 'dat_bitcoin' && (<>
                      <span className="text-muted uppercase text-[10px]">btc-nav</span>
                      <span>{d.btc_nav_usd != null ? `${(d.btc_nav_usd / 1e9).toFixed(2).replace('.', ',')} mld USD` : 'n.d.'}</span>
                      <span className="text-muted uppercase text-[10px]">nav equity/az.</span>
                      <span>{num(d.nav_per_share)} USD · prezzo {num(d.price)} USD</span>
                      <span className="text-muted uppercase text-[10px]">record</span>
                      <span>strategy.com{d.nav_vintage?.record_as_of != null ? ` · as-of ${String(d.nav_vintage.record_as_of)}` : ''}
                        <span className="text-faint"> · prezzi live, cache {String(d.nav_vintage?.cache_ttl_min ?? 'n.d.')} min</span></span>
                    </>)}
                    {d.profile_key === 'dat_hype' && (<>
                      <span className="text-muted uppercase text-[10px]">adjusted nav</span>
                      <span>{d.adjusted_nav_musd != null ? `${d.adjusted_nav_musd.toLocaleString('it-IT')} $M` : 'n.d.'} · FD {d.fd_shares_m != null ? d.fd_shares_m.toLocaleString('it-IT') : 'n.d.'} M
                        <span className="text-faint"> (warrant ITM a treasury method)</span></span>
                      <span className="text-muted uppercase text-[10px]">valore hype</span>
                      <span>{d.hype_value_musd != null ? `${d.hype_value_musd.toLocaleString('it-IT')} $M` : 'n.d.'} · prezzo PURR {num(d.price)} USD</span>
                      <span className="text-muted uppercase text-[10px]">input bilancio</span>
                      <span>hypestrat.xyz (JSON IR){d.nav_vintage?.effective_date != null ? ` · effective ${String(d.nav_vintage.effective_date)}` : ''}{d.nav_vintage?.next_update != null ? ` · next ${String(d.nav_vintage.next_update)}` : ''}</span>
                      {d.nav_vintage?.lag_note != null && (<>
                        <span className="text-muted uppercase text-[10px]">lag fonte</span>
                        {/* la nota del sito arriva in markdown: via gli asterischi, testo intatto */}
                        <span className="text-amber">{String(d.nav_vintage.lag_note).replace(/\*\*/g, '').slice(0, 140)}</span>
                      </>)}
                    </>)}
                    <span className="text-muted uppercase text-[10px]">target analista</span>
                    <span>{d.nav_target != null
                      ? <>{d.nav_target.toFixed(2)} <span className="text-faint">(fonte nella variant view)</span></>
                      : <span className="text-muted">assente — dichiara nav_target ({d.profile_key === 'cef_nav' ? 'prezzo/NAV'
                          : d.profile_key === 'dat_bitcoin' ? 'su mNAV EV' : 'mNAV su ANAV/FD'}) per il fair value</span>}</span>
                  </div>
                </div>
                {d.profile_key === 'dat_bitcoin' && (
                  <p className="text-[10px] text-faint">
                    giudicare su mNAV EV: davanti alle ordinarie ci sono debt+preferred (leggimi del tool) · veto MSTR: il canonico è INFORMAZIONE, nessuna proposta operativa (D3)
                  </p>
                )}
                {(d.warnings?.length || 0) > 0 && (
                  <div className="text-[10px] text-[#c89a3f] space-y-0.5">
                    {d.warnings!.map((w, i) => (
                      <div key={i} style={{ paddingLeft: '10px', textIndent: '-10px' }}>⚠ {w}</div>
                    ))}
                  </div>
                )}
              </>)}

              <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                <span className="text-muted uppercase text-[10px]">prezzo alla tesi</span>
                <span>{sel.price_at_thesis != null ? sel.price_at_thesis.toFixed(2) : 'n.d.'}</span>
                <span className="text-muted uppercase text-[10px]">upside</span>
                <span className={upsideCls(sel)}>
                  {/* V5: per i veicoli l'upside viene dal payload quando la tesi manca
                      (stessa generazione del file) — semantica: convergenza al target */}
                  {(isMnav ? mnavUpside : sel.upside_pct) != null
                    ? `${(isMnav ? mnavUpside : sel.upside_pct)! > 0 ? '+' : ''}${(isMnav ? mnavUpside : sel.upside_pct)!.toFixed(1)}%`
                    : '—'}
                  {(sel.flagged || sel.sanity_severity === 'WARN') && sel.upside_pct != null && ' (non actionable)'}
                </span>
                {d ? (
                  <>
                    {/* per rab e mnav metodi/scheda sono già sopra (opzione B) */}
                    {(isRab || isMnav) ? null : isBank ? (
                      <>
                        <span className="text-muted uppercase text-[10px]">metodi</span>
                        <span>RI {num(d.fair_value_ri)} · P/TBV {num(d.fair_value_ptbv)} · DDM {num(d.fair_value_ddm)}</span>
                        {d.methods_divergence != null && (<>
                          <span className="text-muted uppercase text-[10px]">divergenza metodi</span>
                          <span>{pct(d.methods_divergence, 0)}</span>
                        </>)}
                        {/* decisione PM 23/07 (pacchetto): costo del rischio TTC in
                            pagina — ancora dichiarata V4, mai aggiustamento del ROE */}
                        {d.cost_of_risk_ttc && (<>
                          <span className="text-muted uppercase text-[10px]">costo rischio TTC</span>
                          <span>
                            media {d.cost_of_risk_ttc.avg_bps} bps su {d.cost_of_risk_ttc.years?.length ?? '—'} anni
                            {d.cost_of_risk_ttc.last_bps != null && <> · ultimo FY {d.cost_of_risk_ttc.last_year}: {d.cost_of_risk_ttc.last_bps} bps</>}
                            {d.cost_of_risk_ttc.mixed && <span className="text-amber"> · serie a segni misti: convenzione n.d.</span>}
                          </span>
                        </>)}
                      </>
                    ) : (
                      <>
                        <span className="text-muted uppercase text-[10px]">scenari DCF</span>
                        <span>bear {num(d.fair_value_bear)} · base {num(d.fair_value_base)} · bull {num(d.fair_value_bull)}</span>
                        {d.fair_value_comps_implied != null && (<>
                          <span className="text-muted uppercase text-[10px]">comps implicito</span>
                          <span>{num(d.fair_value_comps_implied)}{d.methods_delta != null ? ` (delta vs DCF ${pct(d.methods_delta, 0)})` : ''}</span>
                        </>)}
                      </>
                    )}
                    {(d.peers_used?.length || 0) > 0 && (<>
                      <span className="text-muted uppercase text-[10px]">peer</span>
                      <span className="break-words">{d.peers_used!.join(' · ')}</span>
                    </>)}
                    {hi && (<>
                      <span className="text-muted uppercase text-[10px]">IRR holding {hi.years || 3}a</span>
                      {/* review 17/07 F5/F7: il buco DICHIARATO dal motore (hi.note)
                          non si butta — by_scenario vuoto o irr null => si mostra il perche' */}
                      <span>
                        {isBank
                          ? (hi.irr != null
                              ? <>{pct(hi.irr)} exit giustif. {hi.exit_ptbv_just}x{hi.irr_exit_peer != null ? <> · {pct(hi.irr_exit_peer)} exit peer {hi.exit_ptbv_peer}x</> : null}</>
                              : <span className="text-muted">{hi.note || 'n.d.'}</span>)
                          : (Object.keys(hi.by_scenario || {}).length > 0
                              ? ['bear', 'base', 'bull'].filter(s => s in (hi.by_scenario || {}))
                                  .map(s => `${s} ${hi.by_scenario![s] == null ? 'n.d.' : pct(hi.by_scenario![s])}`).join(' · ')
                              : <span className="text-muted">{hi.note || 'n.d.'}</span>)}
                      </span>
                    </>)}
                    {hi?.gordon_check && (<>
                      <span className="text-muted uppercase text-[10px]">exit check</span>
                      <span className={hi.gordon_check.includes('ATTENZIONE') ? 'text-amber' : 'text-muted'}>{hi.gordon_check}</span>
                    </>)}
                  </>
                ) : (
                  <>
                    <span className="text-muted uppercase text-[10px]">dettaglio</span>
                    <span className="text-muted">n.d. — arriva alla prossima rigenerazione del modello (sidecar assente, dichiarato)</span>
                  </>
                )}
                <span className="text-muted uppercase text-[10px]">tesi registrata</span>
                <span className="text-muted">{sel.thesis_date ? sel.thesis_date.slice(0, 16).replace('T', ' ') : 'n.d.'}{sel.memo_id ? ` · memo #${sel.memo_id}` : ''}</span>
              </div>

              {/* F17 v2 opzione B (PM 22/07): card SOTP — aggregati dal sidecar, righe
                  per segmento SOLO nel foglio Excel (dichiarato). Headline resta il
                  consolidato (decisione PM 21/07 n.4). */}
              {hasSotp && d && (
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>
                    SOTP — {d.sotp_n_segments ?? 'n.d.'} segmenti · foglio "SOTP (segmenti)"
                  </div>
                  <div>
                    <span className="text-xl text-white font-bold">
                      {d.fair_value_sotp != null ? d.fair_value_sotp.toFixed(2) : 'n.d.'}
                    </span>
                    <span className="text-muted text-xs ml-1.5">{d.payload_currency || ''}/azione</span>
                    {d.sotp_delta_pct != null && (
                      <span className={`text-[11px] px-1.5 ml-2 border ${d.sotp_delta_pct >= 0 ? 'border-emerald/50 text-emerald' : 'border-crimson text-crimson'}`}>
                        {d.sotp_delta_pct > 0 ? '+' : ''}{d.sotp_delta_pct.toFixed(1)}% vs consolidato
                      </span>
                    )}
                    {(sel.flagged || sel.sanity_severity === 'WARN') && d.fair_value_sotp != null &&
                      <span className="text-[10px] text-muted ml-2">(non actionable)</span>}
                  </div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs mt-1.5">
                    <span className="text-muted uppercase text-[10px]">EV totale segmenti</span>
                    <span>{mln(d.sotp_ev_total)} <span className="text-faint">(valuta di bilancio)</span></span>
                    <span className="text-muted uppercase text-[10px]">righe per segmento</span>
                    <span className="text-muted">solo nel foglio Excel — il sidecar porta gli aggregati</span>
                  </div>
                  {d.sotp_incomplete && (
                    <p className="text-crimson text-xs mt-1.5">
                      SOTP INCOMPLETO — {d.sotp_note || 'segmenti non validi (dichiarato), mai somme parziali'}
                    </p>
                  )}
                  {!d.sotp_incomplete && d.fair_value_sotp == null && d.sotp_note && (
                    <p className="text-amber text-xs mt-1.5">{d.sotp_note}</p>
                  )}
                  {(d.sotp_warnings?.length || 0) > 0 && (<>
                    <button onClick={() => setSotpWarnOpen(!sotpWarnOpen)}
                            className="block text-left text-amber text-[10px] mt-1.5 hover:text-gold">
                      {sotpWarnOpen ? '▾' : '▸'} {d.sotp_warnings!.length} avvisi dichiarati dal motore
                      {!sotpWarnOpen && d.sotp_warnings!.length > 1 ? ' — click per aprire (sotto il 1°)' : ''}
                    </button>
                    <div className="text-[10px] text-[#c89a3f] space-y-0.5 mt-0.5">
                      {(sotpWarnOpen ? d.sotp_warnings! : d.sotp_warnings!.slice(0, 1)).map((w, i) => (
                        <div key={i} style={{ paddingLeft: '10px', textIndent: '-10px' }}>⚠ {w}</div>
                      ))}
                    </div>
                  </>)}
                </div>
              )}
              {/* nota SOTP senza aggregati (es. segments su motore rab/banca, o SOTP
                  poppato col FV rifiutato): buco dichiarato, mai silenzio */}
              {!hasSotp && d?.sotp_note && (
                <p className="text-amber text-[10px]">SOTP: {d.sotp_note}</p>
              )}

              {/* V5: per un mnav senza severity (FV n.d., mai badge OK né allarme) la
                  headline "nessun nav_target" è già detta sopra — niente doppione */}
              {(sel.sanity_headline || d?.sanity?.headline)
                && !(isMnav && !(sel.sanity_severity || d?.sanity?.severity)) && (
                <p className="text-amber text-xs">
                  <AlertTriangle size={11} className="inline mr-1" />
                  SANITY {sel.sanity_severity || d?.sanity?.severity}: {sel.sanity_headline || d?.sanity?.headline}
                </p>
              )}

              {/* decisione PM 23/07 (pacchetto): nota M7 in pagina — gm base che
                  devia dalla mediana storica/settore = possibile one-off, FV da
                  validare con variant view (la stringa arriva dal sidecar) */}
              {d?.margin_sanity && (
                <p className="text-amber text-xs">
                  <AlertTriangle size={11} className="inline mr-1" />
                  {d.margin_sanity}
                </p>
              )}

              {/* decisione PM 23/07: warnings del motore anche per i NON-mnav
                  (la card veicolo li rende già) — banca (banda costo del rischio),
                  rab, operating; pattern V5 M1, mai avvisi persi in pagina */}
              {!isMnav && (d?.warnings?.length || 0) > 0 && (
                <div className="text-[10px] text-[#c89a3f] space-y-0.5">
                  {d!.warnings!.map((w, i) => (
                    <div key={i} style={{ paddingLeft: '10px', textIndent: '-10px' }}>⚠ {w}</div>
                  ))}
                </div>
              )}

              <div>
                <div className={`${HDR} border-b border-border pb-1 mb-1.5`}>Motivo dell'ultima revisione (variant view)</div>
                <p className="text-white/85 text-xs whitespace-pre-wrap bg-[#101010] border-l-2 border-[#ff8c00] p-2">
                  {sel.variant_view || 'Nessuna variant view registrata per questo modello.'}
                </p>
              </div>
              {d?.peer_note && <p className="text-[10px] text-faint">peer: {d.peer_note}</p>}

              <div className="flex gap-2 pt-1">
                <a href={`${API_BASE}/fundamentals/models/${encodeURIComponent(sel.file)}/download`} target="_blank"
                   className="px-4 py-1.5 text-xs border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1.5">
                  <Download size={12} /> APRI EXCEL
                </a>
                {sel.memo_id && (
                  <span className="px-4 py-1.5 text-xs border border-[#2c4a7a] bg-[#0a1220] text-[#8ab4f8]">memo #{sel.memo_id} · Archivio memo</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {vecchi.length > 0 && (
        <div className="panel">
          <button onClick={() => setShowOld(!showOld)} className="text-xs text-muted hover:text-gold">
            {showOld ? '▾' : '▸'} file precedenti non piu' attivi ({vecchi.length}) — restano scaricabili, non contano
          </button>
          {showOld && (
            <div className="mt-2 space-y-1">
              {vecchi.map(f => (
                <div key={f.file} className="flex items-center justify-between text-xs py-1">
                  <span className="text-muted font-mono">{f.ticker} · {f.generated_at || 'n.d.'} · {f.dir}/{f.flagged ? ' FLAGGED' : ''}</span>
                  <a href={`${API_BASE}/fundamentals/models/${encodeURIComponent(f.file)}/download`}
                     target="_blank" className="text-cyan hover:underline inline-flex items-center gap-1">
                    <Download size={11} /> {f.file}
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="text-muted text-xs text-center flex items-center justify-center gap-2">
        <FileSpreadsheet size={12} />
        Un modello vivo per titolo: la run lo crea per i nomi nuovi e lo revisiona se cambiano guidance/tesi/condizioni.
        I fogli hanno formule vive coi valori gia' calcolati. FLAG = sanity fallita: il modello CHIEDE una variant view.
      </p>
    </div>
  );
}
