import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { fmtNum } from '@/lib/format';
import { dataIt, leggiDetail } from '@/lib/quota';
import { useEffect, useMemo, useState } from 'react';
import { Bellomberg, ValuationModel, ValuationDetail, API_BASE } from '@/lib/api';
import { FileSpreadsheet, Download, AlertTriangle } from 'lucide-react';
import { prepareValuationModel, valuationBadge } from '@/lib/sector-valuation';

// F17 Fundamentals — OPZIONE B scelta dal PM (17/07, mockup renderizzato, regola
// 15/07): master-detail stile terminal. Sinistra: tabella densa (un modello canonico
// per stock). Destra: pannello fisso col dettaglio del selezionato — FV grande,
// metodi, peer, IRR di holding, sanity, variant view, bottoni. Il dettaglio arriva
// dal sidecar VAL_X.payload.json (endpoint: campo 'detail'); assente = n.d.
// DICHIARATO finche' il modello non viene rigenerato.

const ORANGE = 'text-[#ff8c00]';
const HDR = `${ORANGE} uppercase text-[10px] tracking-[2px]`;

const pct = (v?: number | null, digits = 1) =>
  v == null ? tr('fundamentals.f001') : `${v > 0 ? '+' : ''}${fmtNum((v * 100), digits)}%`;
const num = (v?: number | null) => (v == null ? tr('fundamentals.f001') : fmtNum(v, 2));
const mln = (v?: number | null) =>
  v == null ? tr('fundamentals.f001') : tr('fundamentals.f002', {a: fmtNum(Math.round(v), 0)});

const displayedDate = (value?: string | null, short = false) => {
  if (!value) return tr('fundamentals.f001');
  const date = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(value);
  return date ? dataIt(date[1], short) + (date[2] ? ` ${date[2]}` : '') : value;
};
const qualityLabel = (status?: string | null): string => {
  if (!status) return tr('fundamentals.f027');
  const labels: Record<string, string> = {
    DOCUMENTATA: tr('fundamentals.qualityDocumented'),
    INCOMPLETA: tr('fundamentals.qualityIncomplete'),
    BOZZA_AUTOMATICA: tr('fundamentals.qualityAutomaticDraft'),
  };
  return Object.prototype.hasOwnProperty.call(labels, status) ? labels[status] : status;
};
const scenarioLabel = (key: string) => key === 'bear' ? tr('fundamentals.scenarioBear')
  : key === 'base' ? tr('fundamentals.scenarioBase') : key === 'bull' ? tr('fundamentals.scenarioBull') : key;
// Id del motore di valutazione -> nome leggibile nella lingua della UI. Un id senza
// nome resta a schermo e lo si dichiara (mai un nome inventato); 'n.d.' e '?' sono
// buchi dell'API (riga snapshot senza motore; file legacy fuori pattern), non id.
// 'VAL' e 'DCF' = prefissi del file; 'operating_v3' = id del motore operativo vivo
// (valuation/dcf_buyside_v3.py); 'sconosciuto'/'unknown' = parola del backend per il
// motore non determinato, tradotta.
const engineLabel = (id?: string | null): string => {
  if (!id || id === 'n.d.' || id === '?') return tr('fundamentals.f001');
  const labels: Record<string, string> = {
    operating: tr('fundamentals.engineOperating'), operating_v3: tr('fundamentals.engineOperating'),
    bank: tr('fundamentals.engineBank'), insurance: tr('fundamentals.engineInsurance'),
    VAL: tr('fundamentals.engineValuation'), DCF: 'DCF',
    sconosciuto: tr('fundamentals.engineUnknown'), unknown: tr('fundamentals.engineUnknown'),
  };
  return Object.prototype.hasOwnProperty.call(labels, id) ? labels[id] : tr('fundamentals.engineUnlabelled', { id });
};
// F17 v2 (opzione B, PM 22/07): etichette convenzione del motore RAB
const CONV_LABEL = (): Record<string, string> => ({
  real_pretax: tr('fundamentals.f003'),
  cpih_real_vanilla: tr('fundamentals.f004'),
  analyst_pretax: tr('fundamentals.f005'),
});
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
    return dd.discount_to_nav_pct != null ? `${fmtNum(dd.discount_to_nav_pct, 1)}%` : null;
  const v = dd.profile_key === 'dat_bitcoin' ? dd.mnav_ev : dd.mnav;
  return v != null ? `${fmtNum(v, 2)}x` : null;
};

// Barra premio/sconto (−50%…+50%): marker bianco = oggi, arancio = target analista.
// Fuori scala = marker clampato al bordo con l'etichetta che dice il valore vero.
function MnavBar({ now, target }: { now: number | null; target?: number | null }) {
  const tr = useT();
  if (now == null) return null;
  const pos = (v: number) => Math.max(0, Math.min(100, 50 + v));
  const fuori = Math.abs(now) > 50 ? tr('fundamentals.f006') : '';
  return (
    <div className="relative h-12 mt-2 mb-1">
      <div className="absolute left-0 right-0 top-[18px] h-1.5"
           style={{ background: 'linear-gradient(90deg,#5c1f1f 0%,#222 50%,#1f5c3a 100%)' }} />
      <div className="absolute top-[14px] w-px h-3.5 bg-[#888]" style={{ left: '50%' }} />
      {([[0, '−50%'], [25, '−25%'], [50, tr('fundamentals.f007')], [75, '+25%'], [100, '+50%']] as [number, string][]).map(([x, lb]) => (
        <span key={lb} className="absolute top-[38px] text-[9px] text-faint"
              style={{ left: `${x}%`, transform: 'translateX(-50%)' }}>{lb}</span>
      ))}
      <span className="absolute top-0 text-[10px] text-white whitespace-nowrap"
            style={{ left: `${pos(now)}%`, transform: 'translateX(-50%)' }}>
        {tr('fundamentals.f008')} {now > 0 ? '+' : ''}{fmtNum(now, 1)}%{fuori}
      </span>
      {target != null && (
        <span className="absolute top-[25px] text-[10px] text-[#ff8c00] whitespace-nowrap"
              style={{ left: `${pos(target)}%`, transform: 'translateX(-50%)' }}>
          {tr('fundamentals.f009')} {target > 0 ? '+' : ''}{fmtNum(target, 1)}%
        </span>
      )}
    </div>
  );
}

export default function FundamentalsPage() {
  const tr = useT();
  const [models, setModels] = useState<ValuationModel[]>([]);
  const [nota, setNota] = useState<string | undefined>();
  const [err, setErr] = useState<{ detail: string | null } | null>(null);
  const [selTicker, setSelTicker] = useState<string | null>(null);
  const [showOld, setShowOld] = useState(false);
  // avvisi SOTP collassati col count (opzione B): si riapre da zero a ogni cambio titolo
  const [sotpWarnOpen, setSotpWarnOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [responseLanguage, setResponseLanguage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const requestedLanguage = linguaCorrente();
    setLoading(true);
    Bellomberg.valuationModels()
      .then(r => {
        if (!active || requestedLanguage !== linguaCorrente()) return;
        setModels(r.models); setNota(r.nota); setErr(null); setResponseLanguage(requestedLanguage);
      })
      .catch(e => { if (active && requestedLanguage === linguaCorrente()) setErr({ detail: leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) || null }); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // Read-only registry descriptors may change language; stored artifacts and
    // valuation calculations are neither translated nor regenerated by this GET.
  }, [tr]);

  // un modello "migliore" per ticker: canonico se esiste, altrimenti il piu' recente
  const { best, vecchi } = useMemo(() => {
    const byTicker = new Map<string, ValuationModel[]>();
    for (const raw of models) {
      const m = prepareValuationModel(raw);
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
  }, [models, tr]);

  const sel = useMemo(
    () => best.find(m => m.ticker === selTicker) || best[0] || null,
    [best, selTicker]);

  useEffect(() => { setSotpWarnOpen(false); }, [sel?.ticker]);

  const upsideCls = (m: ValuationModel) =>
    m.flagged || m.sanity_severity === 'WARN' ? 'text-muted'
      : (m.upside_pct ?? 0) >= 0 ? 'pl-positive' : 'pl-negative';

  const badge = (m: ValuationModel) => {
    // review 17/07 F2: OK verde solo con un giudizio sanity VERO — senza dato, n.d.
    const sev = valuationBadge(m);
    if (sev === 'BLOCK') return <span className="text-[10px] px-1.5 border border-crimson text-crimson">BLOCK</span>;
    if (sev === 'WARN') return <span className="text-[10px] px-1.5 border border-amber text-amber">WARN</span>;
    if (sev !== 'OK') return <span className="text-[10px] px-1.5 border border-border text-muted">{tr('fundamentals.f001')}</span>;
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
  // Entrambi i valori sono gia' passati dal controllo comune e dalla guardia cache UI.
  const mnavFv = isMnav ? (sel?.fair_value ?? d?.fair_value_nav ?? null) : null;
  const mnavNow = isMnav ? mnavPct(d) : null;
  const mnavTargetPct = isMnav && d?.nav_target != null ? (d.nav_target - 1) * 100 : null;
  const mnavUpside = isMnav
    ? (sel?.upside_pct ?? (mnavFv != null && d?.price ? (mnavFv / d.price - 1) * 100 : null))
    : null;
  // il SOTP e' un layer sopra qualunque motore: si mostra se il sidecar ha gli aggregati
  const hasSotp = d != null && (d.fair_value_sotp != null || d.sotp_n_segments != null);
  const marketQuote = sel?.market_quote;
  const quoteStatus = marketQuote?.status_at_read ?? 'data_missing';
  const quoteStatusLabel = ({
    ok: tr('fundamentals.quoteReady'), stale: tr('fundamentals.quoteStale'), data_missing: tr('fundamentals.quoteMissing'),
    source_unavailable: tr('fundamentals.quoteSourceUnavailable'), identity_mismatch: tr('fundamentals.quoteIdentityMismatch'),
    currency_mismatch: tr('fundamentals.quoteCurrencyMismatch'), fx_not_rolled: tr('fundamentals.quoteFxNotRolled'),
  } as Record<string, string>)[quoteStatus] ?? tr('fundamentals.quoteUnknown');

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between border-b-2 border-[#ff8c00] pb-2">
        <h1 className={`text-lg font-bold font-mono ${ORANGE}`}>{tr('fundamentals.f010')}</h1>
        <span className="text-muted text-xs font-mono">
          {models.length === 0 && (loading || err) ? tr(err ? 'fundamentals.archiveUnknown' : 'fundamentals.loadingModels') : <>
            {best.filter(m => m.fair_value != null).length} {tr('fundamentals.f011')} {best.filter(m => m.fair_value == null).length} {tr('fundamentals.f012')}
          </>}
        </span>
      </div>
      {err && <p className="text-crimson text-sm">{tr('fundamentals.f013')} {err.detail || tr('fundamentals.errorUnknown')}</p>}
      {nota && <p className="text-amber text-xs">{nota}</p>}
      {loading && <p className="text-muted text-xs">{tr('fundamentals.loadingModels')}</p>}

      <div className="flex gap-0 border border-border bg-black/40 min-h-[420px]">
        {/* -------- sinistra: tabella densa -------- */}
        <div className="flex-[3] overflow-x-auto border-r border-border">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="bg-[#141414]">
                <th className={`text-left py-1.5 px-2 ${HDR}`}>Ticker</th>
                <th className={`text-right px-2 ${HDR}`}>FV</th>
                <th className={`text-right px-2 ${HDR}`} title={tr('fundamentals.modelBasis')}>Px</th>
                <th className={`text-right px-2 ${HDR}`}>{tr('fundamentals.f014')}</th>
                <th className={`text-center px-2 ${HDR}`}>{tr('fundamentals.f015')}</th>
                <th className={`text-left px-2 ${HDR}`}>{tr('fundamentals.f016')}</th>
              </tr>
            </thead>
            <tbody>
              {best.map(m => (
                <tr key={`${m.dir}/${m.file}/${m.generation_id || m.ticker}`} onClick={() => setSelTicker(m.ticker)}
                    className={`border-b border-border/20 cursor-pointer hover:bg-bg/40 ${sel?.ticker === m.ticker ? 'bg-[#16202b]' : ''}`}>
                  <td className="py-1.5 px-2 font-semibold text-white whitespace-nowrap">
                    {m.ticker}{sel?.ticker === m.ticker ? ' ◄' : ''}
                    {m.detail?.engine === 'rab' && <span className={CHIP}>RAB</span>}
                    {m.detail?.engine === 'mnav' &&
                      <span className={CHIP}>{m.detail?.profile_key === 'cef_nav' ? 'NAV' : 'mNAV'}</span>}
                    {(m.detail?.fair_value_sotp != null || m.detail?.sotp_n_segments != null) &&
                      <span className={CHIP}>SOTP</span>}
                    {!m.matched && <span className="text-faint text-[10px] ml-1">{tr('fundamentals.f017')}</span>}
                  </td>
                  {/* V5 opzione B: per i veicoli senza FV la colonna mostra la MISURA (1.03x / −33.9%) */}
                  <td className="text-right px-2">{m.fair_value != null ? fmtNum(m.fair_value, 2)
                    : <span className="text-muted">{mnavMeasureLabel(m.detail) ?? tr('fundamentals.f001')}</span>}</td>
                  <td className="text-right px-2 text-muted">{m.price_at_thesis != null ? fmtNum(m.price_at_thesis, 2)
                    : m.detail?.engine === 'mnav' && m.detail?.price != null ? fmtNum(m.detail.price, 2) : '—'}</td>
                  <td className={`text-right px-2 ${upsideCls(m)}`}>
                    {m.upside_pct != null ? `${m.upside_pct > 0 ? '+' : ''}${fmtNum(m.upside_pct, 1)}%` : '—'}
                  </td>
                  <td className="text-center px-2">{badge(m)}</td>
                  <td className="text-left px-2 text-muted text-xs whitespace-nowrap">{displayedDate(m.generated_at, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {best.length === 0 && !err && !loading && (
            <p className="text-muted text-sm p-4">{tr('fundamentals.f018')}</p>
          )}
        </div>

        {/* -------- destra: pannello dettaglio -------- */}
        <div className="flex-[2] p-4 bg-[#0a0a0a] font-mono text-sm">
          {!sel ? (
            <p className="text-muted text-xs">{tr('fundamentals.f019')}</p>
          ) : (
            <div className="space-y-3">
              <div className={`${HDR} border-b border-border pb-1`}>
                {sel.ticker} {tr('fundamentals.f020')} {isRab ? tr('fundamentals.f021')
                  : isMnav ? `MNAV (${d?.profile_key === 'cef_nav' ? tr('fundamentals.f022')
                              : d?.profile_key === 'dat_hype' ? 'DAT HYPE' : 'DAT BITCOIN'})`
                  : engineLabel(d?.engine || sel.engine)}
              </div>
              <div className="text-xs space-y-1" data-testid="sector-valuation-status">
                <p className="text-faint">{tr('fundamentals.originalModel')}</p>
                <p className="text-white">{tr('fundamentals.f023')} {sel.valuation_decision?.method_id || tr('fundamentals.f001')}
                  {' · '}{sel.valuation_decision?.support_status || tr('fundamentals.f024')}</p>
                <p className="text-muted">{sel.valuation_decision?.rationale || tr('fundamentals.f025')}</p>
                {sel.presentation?.decision_display && <div className="text-muted">
                  <span className="text-faint">{tr('fundamentals.registryDisplay', { language: responseLanguage === 'it' ? tr('fundamentals.languageIt') : responseLanguage === 'en' ? tr('fundamentals.languageEn') : tr('fundamentals.languageUnknown') })}</span>
                  {sel.presentation.decision_display.method_rationale && <p>{sel.presentation.decision_display.method_rationale}</p>}
                  {sel.presentation.decision_display.support_note && <p>{sel.presentation.decision_display.support_note}</p>}
                </div>}
                <p className="text-muted">{tr('fundamentals.f026')} {qualityLabel(sel.analytical_quality?.status)}
                  {' · '}{tr('fundamentals.f028')} {sel.valuation_decision?.requirements_status || tr('fundamentals.f029')}</p>
                {d?.valuation_date && <p className="text-muted">{tr('fundamentals.f030')} {d.valuation_date}</p>}
                {d?.valuation_basis && <p className="text-amber">{d.valuation_basis}</p>}
                {!sel.valuation_usability?.usable && <p className="text-amber">
                  {tr('fundamentals.f031')} {(sel.valuation_usability?.reasons || []).join('; ') || tr('fundamentals.f032')}
                </p>}
                {(sel.valuation_usability?.missing_fields?.length || sel.valuation_decision?.missing_fields?.length || 0) > 0 &&
                  <p className="text-amber">{tr('fundamentals.f033')} {(sel.valuation_usability?.missing_fields?.length
                    ? sel.valuation_usability.missing_fields : sel.valuation_decision?.missing_fields)?.join(', ')}</p>}
                {(sel.acquisition_tasks?.length || 0) > 0 && <ul className="text-amber list-disc pl-4">
                  {sel.acquisition_tasks!.map((task, index) => <li key={index}>
                    {task.field || tr('fundamentals.f034')}: {task.status || tr('fundamentals.f035')}{task.reason ? ` — ${task.reason}` : ''}
                    {sel.presentation?.requirements_display?.fields?.find(field => field.field === task.field)?.description && <span> · {sel.presentation.requirements_display.fields.find(field => field.field === task.field)!.description}</span>}
                  </li>)}
                </ul>}
              </div>
              {/* V5 opzione B (PM 23/07): headline del veicolo = FV se dichiarato,
                  altrimenti la MISURA viva (sconto / mNAV EV / mNAV) — mai un grande
                  n.d. quando il numero informativo esiste */}
              {isMnav && d ? (
                <div>
                  {mnavFv != null ? (
                    <>
                      <span className="text-3xl text-white font-bold">{fmtNum(mnavFv, 2)}</span>
                      <span className="text-muted text-xs ml-2">
                        {d.payload_currency || 'USD'} fair value = NAV {num(d.nav_per_share)} × target {d.nav_target != null ? fmtNum(d.nav_target, 2) : tr('fundamentals.f001')}
                      </span>
                      <p className="text-[10px] text-amber mt-0.5">
                        {tr('fundamentals.f036')}
                      </p>
                    </>
                  ) : (
                    <>
                      <span className="text-3xl text-white font-bold">
                        {d.profile_key === 'cef_nav'
                          ? (d.discount_to_nav_pct != null ? `${fmtNum(d.discount_to_nav_pct, 1)}%` : tr('fundamentals.f001'))
                          : d.profile_key === 'dat_bitcoin'
                            ? (d.mnav_ev != null ? `${fmtNum(d.mnav_ev, 3)}x`
                               : d.mnav_equity != null ? `${fmtNum(d.mnav_equity, 3)}x` : tr('fundamentals.f001'))
                            : (d.mnav != null ? `${fmtNum(d.mnav, 3)}x` : tr('fundamentals.f001'))}
                      </span>
                      <span className="text-muted text-xs ml-2">
                        {d.profile_key === 'cef_nav'
                          ? tr('fundamentals.f037', {a: num(d.nav_per_share)})
                          : d.profile_key === 'dat_bitcoin'
                            ? (d.mnav_ev != null
                               ? tr('fundamentals.f038', {a: d.mnav_equity != null ? fmtNum(d.mnav_equity, 3) : tr('fundamentals.f001')})
                               : tr('fundamentals.f039'))
                            : tr('fundamentals.f040', {a: d.nav_per_share != null ? fmtNum(d.nav_per_share, 4) : tr('fundamentals.f001'), b: d.mnav_dtl_addback != null ? ` · DTL add-back ${fmtNum(d.mnav_dtl_addback, 3)}x` : ''})}
                      </span>
                      <p className="text-[10px] text-muted mt-0.5">
                        {tr('fundamentals.f041')} {d.fv_note || (!sel.valuation_usability?.usable
                          ? tr('fundamentals.f042') : tr('fundamentals.f043'))}
                      </p>
                    </>
                  )}
                  <MnavBar now={mnavNow} target={mnavTargetPct} />
                </div>
              ) : (
              <div>
                <span className="text-3xl text-white font-bold">
                  {sel.fair_value != null ? fmtNum(sel.fair_value, 2) : tr('fundamentals.f001')}
                </span>
                <span className="text-muted text-xs ml-2">
                  {d?.payload_currency ? `${d.payload_currency} ` : ''}fair value{sel.fair_value == null ? tr('fundamentals.f044') : ''}
                </span>
              </div>
              )}

              {/* F17 v2 opzione B (PM 22/07): mini-tabella metodi RAB col mediano evidenziato */}
              {isRab && d && (<>
                <div className="grid grid-cols-3 gap-2 text-center">
                  {([
                    [tr('fundamentals.f045'), d.fair_value_ev_rab],
                    [tr('fundamentals.f046'), d.fair_value_ddm_reg],
                    [d.peer_method || tr('fundamentals.f047'), d.fair_value_peer],
                  ] as [string, number | null | undefined][]).map(([lbl, v]) => (
                    <div key={lbl}
                         className={`border p-1.5 ${v != null && v === d.fair_value_blend ? 'border-[#ff8c00] bg-[#161006]' : 'border-border'}`}>
                      <div className="text-[9px] uppercase tracking-[1px] text-muted">
                        {lbl}{v != null && v === d.fair_value_blend ? tr('fundamentals.f048') : ''}
                      </div>
                      <div className={`text-base ${v == null ? 'text-muted' : 'text-white'}`}>{num(v)}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[10px] text-faint">
                  {tr('fundamentals.f049')} {num(d.fair_value_blend)}{d.blend_methods?.length ? tr('fundamentals.f050', {a: d.blend_methods.length}) : ''}
                  {d.methods_divergence != null ? tr('fundamentals.f051', {a: pct(d.methods_divergence, 0)}) : ''}
                  {d.fair_value_peer == null ? tr('fundamentals.f052') : ''}
                </p>
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>{tr('fundamentals.f053')}{d.service ? ` — ${d.service.replace(/_/g, ' ')}` : ''}</div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f054')}</span>
                    <span>{mln(d.rab_base)}{d.rab_base != null && <span className="text-faint"> {tr('fundamentals.f055')}</span>}</span>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f056')}</span>
                    <span>{d.allowed_return_calc != null
                      ? <>{fmtNum((d.allowed_return_calc * 100), 2)}% {d.convention === 'cpih_real_vanilla' ? tr('fundamentals.f057') : tr('fundamentals.f058')}
                          {d.allowed_return_nominal != null &&
                            <span className="text-muted"> ({fmtNum((d.allowed_return_nominal * 100), 2)}{tr('fundamentals.f059')}</span>}</>
                      : tr('fundamentals.f001')}</span>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f060')}</span>
                    <span>{d.convention ? (CONV_LABEL()[d.convention] || d.convention) : tr('fundamentals.f001')}</span>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f061')}</span>
                    <span>{d.rab_premium != null ? `${fmtNum(d.rab_premium, 2)}x`
                      : <span className="text-muted">{tr('fundamentals.f062')}</span>}</span>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f063')}</span>
                    <span>{d.anchor_stale === true
                      ? <span className="text-amber">{tr('fundamentals.f064')}</span>
                      : d.anchor_stale === false ? <span className="text-emerald">{tr('fundamentals.f065')}</span>
                      : <span className="text-muted">{tr('fundamentals.f001')}</span>}</span>
                  </div>
                </div>
                <p className="text-[10px] text-faint">
                  {tr('fundamentals.f066')}
                </p>
              </>)}

              {/* V5 opzione B (PM 23/07): card Scheda veicolo — fonti, vintage, target (D2) */}
              {isMnav && d && (<>
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>
                    {tr('fundamentals.f067')} {d.profile_key === 'cef_nav' ? tr('fundamentals.f068')
                      : d.profile_key === 'dat_hype' ? 'DAT HYPE' : 'DAT Bitcoin'}
                  </div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                    {d.profile_key === 'cef_nav' && (<>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f069')}</span>
                      <span>{num(d.nav_per_share)} USD
                        {d.nav_vintage?.nav_as_of != null && <span className="text-faint"> {tr('fundamentals.f070')} {String(d.nav_vintage.nav_as_of)}{d.nav_vintage?.nav_age_days != null ? tr('fundamentals.f071', {a: d.nav_vintage.nav_age_days}) : ''} {tr('fundamentals.f072')}</span>}
                      </span>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f073')}</span>
                      <span>{d.price_quote != null ? `${d.price_quote.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true })} ${d.price_quote_currency || ''} → ` : ''}{num(d.price)} USD
                        <span className="text-faint"> {tr('fundamentals.f074')}</span></span>
                    </>)}
                    {d.profile_key === 'dat_bitcoin' && (<>
                      <span className="text-muted uppercase text-[10px]">btc-nav</span>
                      <span>{d.btc_nav_usd != null ? tr('fundamentals.f075', {a: fmtNum((d.btc_nav_usd / 1e9), 2)}) : tr('fundamentals.f001')}</span>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f076')}</span>
                      <span>{num(d.nav_per_share)} {tr('fundamentals.f077')} {num(d.price)} USD</span>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f078')}</span>
                      <span>strategy.com{d.nav_vintage?.record_as_of != null ? tr('fundamentals.f079', {a: String(d.nav_vintage.record_as_of)}) : ''}
                        <span className="text-faint"> {tr('fundamentals.f080')} {String(d.nav_vintage?.cache_ttl_min ?? tr('fundamentals.f001'))} min</span></span>
                    </>)}
                    {d.profile_key === 'dat_hype' && (<>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f081')}</span>
                      <span>{d.adjusted_nav_musd != null ? `${d.adjusted_nav_musd.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true })} $M` : tr('fundamentals.f001')} · FD {d.fd_shares_m != null ? d.fd_shares_m.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }) : tr('fundamentals.f001')} M
                        <span className="text-faint"> {tr('fundamentals.f082')}</span></span>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f083', { asset: 'HYPE' })}</span>
                      <span>{d.hype_value_musd != null ? `${d.hype_value_musd.toLocaleString(localeDi(linguaCorrente()), { useGrouping: true })} $M` : tr('fundamentals.f001')} {tr('fundamentals.f084', { ticker: sel.ticker })} {num(d.price)} USD</span>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f085')}</span>
                      <span>hypestrat.xyz (JSON IR){d.nav_vintage?.effective_date != null ? tr('fundamentals.f086', {a: String(d.nav_vintage.effective_date)}) : ''}{d.nav_vintage?.next_update != null ? tr('fundamentals.f087', {a: String(d.nav_vintage.next_update)}) : ''}</span>
                      {d.nav_vintage?.lag_note != null && (<>
                        <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f088')}</span>
                        {/* la nota del sito arriva in markdown: via gli asterischi, testo intatto */}
                        <span className="text-amber">{String(d.nav_vintage.lag_note).replace(/\*\*/g, '').slice(0, 140)}</span>
                      </>)}
                    </>)}
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f089')}</span>
                    <span>{d.nav_target != null
                      ? <>{fmtNum(d.nav_target, 2)} <span className="text-faint">{tr('fundamentals.f090')}</span></>
                      : <span className="text-muted">{tr('fundamentals.f091')}{d.profile_key === 'cef_nav' ? tr('fundamentals.f092')
                          : d.profile_key === 'dat_bitcoin' ? tr('fundamentals.f093') : tr('fundamentals.f094')}{tr('fundamentals.f095')}</span>}</span>
                  </div>
                </div>
                {d.profile_key === 'dat_bitcoin' && (
                  <p className="text-[10px] text-faint">
                    {tr('fundamentals.f096')}
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
                <span className="text-muted uppercase text-[10px]">{tr(sel.price_model_as_of ? 'fundamentals.modelPrice' : 'fundamentals.f097')}</span>
                <span>{sel.price_at_thesis != null ? fmtNum(sel.price_at_thesis, 2) : tr('fundamentals.f001')}
                  {sel.price_model_as_of && <span className="text-faint"> · {displayedDate(sel.price_model_as_of)}</span>}</span>
                <span className="text-muted uppercase text-[10px]">{tr('fundamentals.modelUpside')}</span>
                <span className={upsideCls(sel)}>
                  {/* V5: per i veicoli l'upside viene dal payload quando la tesi manca
                      (stessa generazione del file) — semantica: convergenza al target */}
                  {(isMnav ? mnavUpside : sel.upside_pct) != null
                    ? `${(isMnav ? mnavUpside : sel.upside_pct)! > 0 ? '+' : ''}${fmtNum((isMnav ? mnavUpside : sel.upside_pct)!, 1)}%`
                    : '—'}
                  {(sel.flagged || sel.sanity_severity === 'WARN') && sel.upside_pct != null && tr('fundamentals.f099')}
                </span>
                <span className="text-muted uppercase text-[10px]">{tr('fundamentals.observedPrice')}</span>
                <span data-testid="valuation-market-quote">
                  {marketQuote?.price != null ? `${fmtNum(marketQuote.price, 2)} ${marketQuote.currency ?? ''}` : tr('fundamentals.f001')}
                  <span className="text-faint"> · {quoteStatusLabel}</span>
                  {marketQuote?.observed_at && <span className="block text-[10px] text-muted">{marketQuote.observed_at}</span>}
                  {marketQuote?.source_id && <span className="block text-[10px] text-muted">
                    {tr('fundamentals.quoteSource')} {marketQuote.source_id}
                    {marketQuote.exchange ? ` · ${marketQuote.exchange}` : ''}
                    {marketQuote.delayed_minutes != null ? ` · ${tr('fundamentals.quoteDelay', { minutes: marketQuote.delayed_minutes })}` : ''}
                  </span>}
                </span>
                <span className="text-muted uppercase text-[10px]">{tr('fundamentals.observedUpside')}</span>
                <span>
                  {quoteStatus === 'ok' && sel.upside_today_pct != null ? `${sel.upside_today_pct > 0 ? '+' : ''}${fmtNum(sel.upside_today_pct, 1)}%` : tr('fundamentals.f001')}
                  <span className="block text-[10px] text-faint">{tr('fundamentals.quoteNoRollforward')}</span>
                </span>
                {d ? (
                  <>
                    {/* per rab e mnav metodi/scheda sono già sopra (opzione B) */}
                    {(isRab || isMnav) ? null : isBank ? (
                      <>
                        <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f100')}</span>
                        <span>RI {num(d.fair_value_ri)} · P/TBV {num(d.fair_value_ptbv)} · DDM {num(d.fair_value_ddm)}</span>
                        {d.methods_divergence != null && (<>
                          <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f101')}</span>
                          <span>{pct(d.methods_divergence, 0)}</span>
                        </>)}
                        {/* decisione PM 23/07 (pacchetto): costo del rischio TTC in
                            pagina — ancora dichiarata V4, mai aggiustamento del ROE */}
                        {d.cost_of_risk_ttc && (<>
                          <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f102')}</span>
                          <span>
                            {tr('fundamentals.f103')} {d.cost_of_risk_ttc.avg_bps} {tr('fundamentals.f104')} {d.cost_of_risk_ttc.years?.length ?? '—'} {tr('fundamentals.f105')}
                            {d.cost_of_risk_ttc.last_bps != null && <> {tr('fundamentals.f106')} {d.cost_of_risk_ttc.last_year}: {d.cost_of_risk_ttc.last_bps} bps</>}
                            {d.cost_of_risk_ttc.mixed && <span className="text-amber"> {tr('fundamentals.f107')}</span>}
                          </span>
                        </>)}
                      </>
                    ) : (
                      <>
                        <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f108')}</span>
                        <span>{scenarioLabel('bear')} {num(d.fair_value_bear)} · {scenarioLabel('base')} {num(d.fair_value_base)} · {scenarioLabel('bull')} {num(d.fair_value_bull)}</span>
                        {d.fair_value_comps_implied != null && (<>
                          <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f109')}</span>
                          <span>{num(d.fair_value_comps_implied)}{d.methods_delta != null ? ` (delta vs DCF ${pct(d.methods_delta, 0)})` : ''}</span>
                        </>)}
                      </>
                    )}
                    {(d.peers_used?.length || 0) > 0 && (<>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f110')}</span>
                      <span className="break-words">{d.peers_used!.join(' · ')}</span>
                    </>)}
                    {hi && (<>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f111')} {hi.years || 3}{tr('fundamentals.yearsUnit')}</span>
                      {/* review 17/07 F5/F7: il buco DICHIARATO dal motore (hi.note)
                          non si butta — by_scenario vuoto o irr null => si mostra il perche' */}
                      <span>
                        {isBank
                          ? (hi.irr != null
                              ? <>{pct(hi.irr)} {tr('fundamentals.f112')} {hi.exit_ptbv_just}x{hi.irr_exit_peer != null ? <> · {pct(hi.irr_exit_peer)} {tr('fundamentals.f113')} {hi.exit_ptbv_peer}x</> : null}</>
                              : <span className="text-muted">{hi.note || tr('fundamentals.f001')}</span>)
                          : (Object.keys(hi.by_scenario || {}).length > 0
                              ? ['bear', 'base', 'bull'].filter(s => s in (hi.by_scenario || {}))
                                  .map(s => `${scenarioLabel(s)} ${hi.by_scenario![s] == null ? tr('fundamentals.f001') : pct(hi.by_scenario![s])}`).join(' · ')
                              : <span className="text-muted">{hi.note || tr('fundamentals.f001')}</span>)}
                      </span>
                    </>)}
                    {hi?.gordon_check && (<>
                      <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f114')}</span>
                      <span className={/ATTENZIONE|WARNING/.test(hi.gordon_check) ? 'text-amber' : 'text-muted'}>{hi.gordon_check}</span>
                    </>)}
                  </>
                ) : (
                  <>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f115')}</span>
                    <span className="text-muted">{tr('fundamentals.f116')}</span>
                  </>
                )}
                <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f117')}</span>
                <span className="text-muted">{displayedDate(sel.thesis_date)}{sel.memo_id ? ` · memo #${sel.memo_id}` : ''}</span>
              </div>

              {/* F17 v2 opzione B (PM 22/07): card SOTP — aggregati dal sidecar, righe
                  per segmento SOLO nel foglio Excel (dichiarato). Headline resta il
                  consolidato (decisione PM 21/07 n.4). */}
              {hasSotp && d && (
                <div className="border border-border bg-[#0d0d0d] p-2.5">
                  <div className={`${HDR} mb-1.5`}>
                    SOTP — {d.sotp_n_segments ?? tr('fundamentals.f001')} {tr('fundamentals.f118')}
                  </div>
                  <div>
                    <span className="text-xl text-white font-bold">
                      {d.fair_value_sotp != null ? fmtNum(d.fair_value_sotp, 2) : tr('fundamentals.f001')}
                    </span>
                    <span className="text-muted text-xs ml-1.5">{d.payload_currency || ''}{tr('fundamentals.f119')}</span>
                    {d.sotp_delta_pct != null && (
                      <span className={`text-[11px] px-1.5 ml-2 border ${d.sotp_delta_pct >= 0 ? 'border-emerald/50 text-emerald' : 'border-crimson text-crimson'}`}>
                        {d.sotp_delta_pct > 0 ? '+' : ''}{fmtNum(d.sotp_delta_pct, 1)}{tr('fundamentals.f120')}
                      </span>
                    )}
                    {(sel.flagged || sel.sanity_severity === 'WARN') && d.fair_value_sotp != null &&
                      <span className="text-[10px] text-muted ml-2">{tr('fundamentals.f121')}</span>}
                  </div>
                  <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs mt-1.5">
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f122')}</span>
                    <span>{mln(d.sotp_ev_total)} <span className="text-faint">{tr('fundamentals.f123')}</span></span>
                    <span className="text-muted uppercase text-[10px]">{tr('fundamentals.f124')}</span>
                    <span className="text-muted">{tr('fundamentals.f125')}</span>
                  </div>
                  {d.sotp_incomplete && (
                    <p className="text-crimson text-xs mt-1.5">
                      {tr('fundamentals.f126')} {d.sotp_note || tr('fundamentals.f127')}
                    </p>
                  )}
                  {!d.sotp_incomplete && d.fair_value_sotp == null && d.sotp_note && (
                    <p className="text-amber text-xs mt-1.5">{d.sotp_note}</p>
                  )}
                  {(d.sotp_warnings?.length || 0) > 0 && (<>
                    <button onClick={() => setSotpWarnOpen(!sotpWarnOpen)}
                            className="block text-left text-amber text-[10px] mt-1.5 hover:text-gold">
                      {sotpWarnOpen ? '▾' : '▸'} {d.sotp_warnings!.length} {tr('fundamentals.f128')}
                      {!sotpWarnOpen && d.sotp_warnings!.length > 1 ? tr('fundamentals.f129') : ''}
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
                  <span className="uppercase">{tr('fundamentals.f015')}</span> {sel.sanity_severity || d?.sanity?.severity}: {sel.sanity_headline || d?.sanity?.headline}
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
                <div className={`${HDR} border-b border-border pb-1 mb-1.5`}>{tr('fundamentals.f130')}</div>
                <p className="text-white/85 text-xs whitespace-pre-wrap bg-[#101010] border-l-2 border-[#ff8c00] p-2">
                  {sel.variant_view || tr('fundamentals.f131')}
                </p>
              </div>
              {d?.peer_note && <p className="text-[10px] text-faint">{tr('fundamentals.f132')} {d.peer_note}</p>}

              <div className="flex gap-2 pt-1">
                {sel.file && <a href={`${API_BASE}/fundamentals/models/${encodeURIComponent(sel.file)}/download`} target="_blank"
                   className="px-4 py-1.5 text-xs border border-[#7a5b1e] bg-[#1c1305] text-[#ffb000] hover:bg-[#33240a] inline-flex items-center gap-1.5">
                  <Download size={12} /> {tr('fundamentals.f133')}
                </a>}
                {sel.memo_id && (
                  <span className="px-4 py-1.5 text-xs border border-[#2c4a7a] bg-[#0a1220] text-[#8ab4f8]">memo #{sel.memo_id} {tr('fundamentals.f134')}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {vecchi.length > 0 && (
        <div className="panel">
          <button onClick={() => setShowOld(!showOld)} className="text-xs text-muted hover:text-gold">
            {showOld ? '▾' : '▸'} {tr('fundamentals.f135')}{vecchi.length}{tr('fundamentals.f136')}
          </button>
          {showOld && (
            <div className="mt-2 space-y-1">
              {vecchi.map(f => (
                <div key={`${f.dir}/${f.file}/${f.generation_id || f.ticker}`} className="flex items-center justify-between text-xs py-1">
                  <span className="text-muted font-mono">{f.ticker} · {displayedDate(f.generated_at)} · {f.dir}/{f.flagged ? tr('fundamentals.f137') : ''}</span>
                  {f.file && <a href={`${API_BASE}/fundamentals/models/${encodeURIComponent(f.file)}/download`}
                     target="_blank" className="text-cyan hover:underline inline-flex items-center gap-1">
                    <Download size={11} /> {f.file}
                  </a>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="text-muted text-xs text-center flex items-center justify-center gap-2">
        <FileSpreadsheet size={12} />
        {tr('fundamentals.f138')}
      </p>
    </div>
  );
}
