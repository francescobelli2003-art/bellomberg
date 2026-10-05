// Ricerca opportunità (F13 Edge Scanner) · vista Nuova, dal mockup approvato il 05/10/2026
// (`outputs/ricerca-opportunita-nuova/mockup.html`). Solo presentazione: stato, chiamate e
// orologio stanno in EdgeScannerPage.tsx; i giudizi sul payload in lib/edge.ts.
import type { ReactNode } from 'react';
import {
  AlertTriangle, ExternalLink, List, Minus, Radar, RefreshCw, ShieldCheck, Stethoscope, TrendingDown, TrendingUp, X,
} from 'lucide-react';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { localeDi } from '@/i18n/lingua';
import {
  copyAttesa, durata, oraIt, LATENZE_MISURATE, SOGLIA_MINIMA_PAGINA,
  type CoperturaDichiarata, type EsitoChiamata, type EsitoScan, type EtaResa, type ScanViva, type Segnale, type Vuoto,
} from '@/lib/edge';
import {
  CATEGORIE, SPIEGAZIONE, etichettaMappa, fileMarcatori, livelloForza, nomeCategoria, rilevatoreDi, scalaDiagnosi, type Grado,
} from './calcoli';
import { parole, type ChiavePagina, type Parole } from './parole';

export type Diagnosi =
  | { ticker: string; stato: 'attesa' }
  | { ticker: string; stato: 'errore'; motivo: string }
  | { ticker: string; stato: 'ok'; score: number; verdetto: string; nota: string; nSegnali: number | null; alle: string | null };

export interface RigaSegnale { s: Segnale; k: string }

export interface DatiRicerca {
  esito: EsitoScan;
  viva: ScanViva | null;
  errore: EsitoChiamata | null;
  loading: boolean;
  forzata: boolean;
  attesaS: number;
  timeoutMs: number;
  minStrength: number;
  /** la soglia con cui è stata chiesta la risposta a schermo */
  sogliaResa: number;
  cat: string;
  tutti: RigaSegnale[];
  righe: RigaSegnale[];
  sel: RigaSegnale | null;
  vuoto: Vuoto | null;
  eta: EtaResa | null;
  oraScan: string | null;
  copertura: boolean;
  diagnosi: Diagnosi | null;
}

export interface AzioniRicerca {
  soglia: (v: number) => void;
  categoria: (c: string) => void;
  scegli: (k: string) => void;
  rifai: () => void;
  riprova: () => void;
  copertura: (aperta: boolean) => void;
  diagnosi: (ticker: string) => void;
  apriMercati: (ticker: string) => void;
}

const SOGLIE = [30, 45, 60, 75];
const TACCHE = [0, 30, 45, 60, 75, 100];
const ICONA_DIR: Record<string, typeof TrendingUp> = { bullish: TrendingUp, bearish: TrendingDown, caution: AlertTriangle, neutral: Minus };
const DIREZIONI = ['bullish', 'bearish', 'caution', 'neutral'];

type P = Parole;

function valore(v: Segnale['value'], p: P): string {
  return typeof v === 'number'
    ? v.toLocaleString(localeDi(p.lingua), { maximumSignificantDigits: 21, useGrouping: true })
    : String(v ?? '');
}
function dirDi(s: Segnale): string {
  return DIREZIONI.includes(s.direction) ? s.direction : 'neutral';
}
function nomeDir(d: string, p: P): string {
  return d === 'bullish' || d === 'bearish' || d === 'caution' || d === 'neutral' ? p.t(`edge.${d}`) : d;
}

function PillDir({ s, p }: { s: Segnale; p: P }) {
  const d = dirDi(s);
  const Icona = ICONA_DIR[d];
  return <span className={`ro-dir is-${d}`}><Icona size={13} aria-hidden="true" />{nomeDir(s.direction, p)}</span>;
}
function PillCat({ c, p }: { c: string; p: P }) {
  return <span className={`ro-cat is-${c}`}>{nomeCategoria(c, p.t)}</span>;
}
function Testa({ icona, titolo, nota, children }: { icona: ReactNode; titolo: string; nota?: string; children?: ReactNode }) {
  return (
    <header className="bbn-card-head ro-card-head">
      <span className="ro-ci" aria-hidden="true">{icona}</span>
      <h2>{titolo}</h2>
      {nota && <span className="bbn-card-note">{nota}</span>}
      {children}
    </header>
  );
}

/* ── testata ─────────────────────────────────────────────────────── */

function Testata({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const { viva, eta } = d;
  let registro: ReactNode;
  if (d.esito.stato === 'attesa') {
    registro = <span className="bbn-chip ro-chip" data-zona="registro"><RefreshCw size={13} className="ro-spin" aria-hidden="true" />
      {p.t('edge.scanRunning')} · <b>{durata(d.attesaS)}</b></span>;
  } else if (viva && eta) {
    const e = viva.eta;
    const fonte = e.dichiarata ? (e.fuoriCache ? p.t('edge.f008') : e.servitaDaCache ? p.t('edge.f009') : p.t('edge.f010')) : '';
    registro = eta.secondi != null
      ? <span className={`bbn-chip ro-chip${eta.scaduta ? ' is-warn' : ''}`} data-zona="registro" title={eta.testo}>
          <i className={`ro-dot${eta.scaduta ? ' is-warn' : ''}`} aria-hidden="true" />
          <span className="ro-chip-t">
            {d.oraScan ? <>{p.t('edge.scanAt', { a: '' })}<b>{d.oraScan}</b> · </> : null}
            {durata(eta.secondi)} {p.t('edge.f027')} · {fonte}{eta.scaduta ? ` · ${p.t('edge.scanExpired')}` : ''}
            {e.dichiarata && e.ttlMotivo ? ` — ${e.ttlMotivo}` : ''}
          </span>
        </span>
      : <span className="bbn-chip ro-chip is-warn" data-zona="registro" title={eta.testo}>
          <AlertTriangle size={13} aria-hidden="true" />{p.t('edge.scanAgeNd')}
        </span>;
  } else {
    registro = <span className="bbn-chip ro-chip is-warn" data-zona="registro"><AlertTriangle size={13} aria-hidden="true" />{p.t('edge.noValidScan')}</span>;
  }
  const costo = `${Math.round(LATENZE_MISURATE.caldoS)}-${Math.round(LATENZE_MISURATE.freddoS)} s `
    + p.t('edge.f013', { a: LATENZE_MISURATE.posizioni, b: LATENZE_MISURATE.misurateIl });
  return (
    <div className="ro-top">
      <h1>{p.t('edge.pageTitle')}</h1>
      <div className="bbn-seg ro-seg" role="group" aria-label={p.t('edge.minStrength')} data-zona="comandi">
        <span className="ro-seg-l">{p.t('edge.minStrength')}</span>
        {SOGLIE.map(v => (
          <button key={v} type="button" className={d.minStrength === v ? 'is-on' : ''} aria-pressed={d.minStrength === v}
            data-soglia={v} title={p.t('edge.f019')} onClick={() => a.soglia(v)}>≥ {v}</button>
        ))}
      </div>
      <span className="bbn-grow" />
      {registro}
      <button type="button" className="bbn-btn is-primary ro-rifai" data-azione="rifai" disabled={d.loading}
        title={p.t('edge.f022', { a: costo })} onClick={a.rifai}>
        <RefreshCw size={14} className={d.loading ? 'ro-spin' : ''} aria-hidden="true" />
        {p.t('edge.refresh')}
      </button>
    </div>
  );
}

/* ── mappa della forza ───────────────────────────────────────────── */

function categorieDi(tutti: RigaSegnale[]): string[] {
  const viste = new Set(tutti.map(x => x.s.category));
  return [...CATEGORIE.filter(c => viste.has(c)), ...[...viste].filter(c => !(CATEGORIE as readonly string[]).includes(c))];
}

function Mappa({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const viva = d.viva!;
  const presenti = categorieDi(d.tutti);
  const assenti = CATEGORIE.filter(c => !presenti.includes(c));
  const soglia = d.sogliaResa;
  const c = viva.copertura;
  const sfondo = <><span className="ro-below" style={{ width: `${soglia}%` }} /><span className="ro-thr" style={{ left: `${soglia}%` }} /></>;
  return (
    <section className="bbn-card ro-card ro-mappa" data-zona="mappa">
      <Testa icona={<Radar size={16} />} titolo={p.t('edge.mapTitle')} nota={p.t('edge.mapNote')} />
      <div className="ro-card-body">
        <div className="ro-map">
          {presenti.map(cat => {
            const ss = d.tutti.filter(x => x.s.category === cat);
            const file = fileMarcatori(ss.map(x => ({ ...x, strength: x.s.strength, ticker: x.s.ticker })));
            const nFile = Math.max(1, ...file.map(f => f.fila + 1));
            return [
              <span key={cat + 'l'} className="ro-lane-l">{nomeCategoria(cat, p.t)}<small>{p.n('edge.signalOne', 'edge.signalMany', ss.length)}</small></span>,
              <div key={cat} className="ro-lane" style={{ height: nFile * 30 + 14 }}>
                {sfondo}
                {file.map(({ s: x, fila }) => (
                  <button key={x.k} type="button" className={`ro-mk is-${x.s.category}${x.s.strength >= 96 ? ' is-end' : x.s.strength <= 4 ? ' is-start' : ''}`} data-sel={x.k}
                    style={{ left: `${Math.max(0, Math.min(100, x.s.strength))}%`, top: 7 + fila * 30 }}
                    aria-current={d.sel?.k === x.k ? 'true' : undefined}
                    title={p.t('edge.markerTitle', { a: x.s.ticker, b: x.s.name, c: x.s.strength })}
                    onClick={() => a.scegli(x.k)}>{etichettaMappa(x.s.ticker)}</button>
                ))}
              </div>,
            ];
          })}
          {!presenti.length && <>
            <span className="ro-lane-l">—</span>
            <div className="ro-lane is-empty">{sfondo}<span>{p.t('edge.mapEmpty', { a: soglia })}</span></div>
          </>}
          <div className="ro-axis">
            {TACCHE.map(v => <span key={v} className={v === soglia ? 'is-on' : ''} style={{ left: `${v}%` }}>{v}</span>)}
          </div>
        </div>
        <div className="ro-map-foot">
          <span>{viva.nTotali != null
            ? p.t('edge.mapTotal', { a: viva.segnali.length, b: soglia, c: viva.nTotali })
            : p.t('edge.mapTotalNd', { a: viva.segnali.length, b: soglia })}</span>
          {assenti.length > 0 && <span>{p.t('edge.mapNone', { a: assenti.map(x => nomeCategoria(x, p.t)).join(', ') })}</span>}
          {c.dichiarata && c.soloPrezzo.length > 0 && c.contate &&
            <span>{p.t('edge.mapOnlyPrice', { a: c.soloPrezzo.length, b: c.totali })}</span>}
        </div>
      </div>
    </section>
  );
}

/* ── copertura ───────────────────────────────────────────────────── */

interface TitoloCoperto { ticker: string; grado: Grado; motivo: string }

function titoliCoperti(c: CoperturaDichiarata, p: P): TitoloCoperto[] {
  return [
    ...c.piena.map(t => ({ ticker: t, grado: 'piena' as Grado, motivo: p.t('edge.covReasonFull') })),
    ...c.soloPrezzo.map(t => ({ ticker: t, grado: 'solo' as Grado, motivo: p.t('edge.covReasonPrice') })),
    ...Object.entries(c.degradata).map(([t, m]) => ({ ticker: t, grado: 'muto' as Grado, motivo: m })),
    ...Object.entries(c.nessunaMisura).map(([t, m]) => ({ ticker: t, grado: 'nessuna' as Grado, motivo: m })),
    ...c.nonScansionate.map(t => ({ ticker: t, grado: 'non' as Grado, motivo: p.t('edge.covReasonNotScanned') })),
  ];
}
const GRADI: [Grado, ChiavePagina][] = [
  ['piena', 'edge.covFull'], ['solo', 'edge.covPrice'], ['muto', 'edge.covMuted'], ['nessuna', 'edge.covNone'], ['non', 'edge.covNotScanned'],
];

function Copertura({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const c = d.viva!.copertura;
  if (!c.dichiarata) {
    return (
      <section className="bbn-card ro-card ro-cop" data-zona="copertura">
        <Testa icona={<ShieldCheck size={16} />} titolo={p.t('edge.covTitle')} nota={p.t('edge.covNote')} />
        <div className="ro-card-body">
          <p className="ro-note is-warn"><AlertTriangle size={15} aria-hidden="true" /><span><b>{p.t('edge.covUndeclared')}.</b> {c.motivo}</span></p>
        </div>
      </section>
    );
  }
  const titoli = titoliCoperti(c, p);
  const conta = (g: Grado) => titoli.filter(x => x.grado === g).length;
  const fattoriali = c.fattorialiSu != null ? p.t('edge.covFactorsOn', { a: c.fattorialiSu })
    : c.fattorialiKo ? p.t('edge.covFactorsKo') : p.t('edge.covFactorsNd');
  return (
    <section className="bbn-card ro-card ro-cop" data-zona="copertura">
      <Testa icona={<ShieldCheck size={16} />} titolo={p.t('edge.covTitle')} nota={p.t('edge.covNote')}>
        <span className="bbn-grow" />
        <button type="button" className="bbn-link ro-link" data-azione="copertura" title={c.nota || undefined} onClick={() => a.copertura(true)}>
          {p.t('edge.covDetails')} <ExternalLink size={13} aria-hidden="true" />
        </button>
      </Testa>
      <div className="ro-card-body">
        <div className="ro-cov-v">
          <b>{c.contate ? `${c.scansionate}/${c.totali}` : '—'}</b>
          <span>{p.t('edge.covPositions')} · {fattoriali}</span>
          <span className={`ro-dir ${c.grado === 'piena' ? 'is-bullish' : 'is-caution'} ro-cov-grado`}>
            {c.grado === 'piena' ? p.t('edge.covComplete') : p.t('edge.covPartial')}</span>
        </div>
        <div className="ro-split" aria-hidden="true">
          {GRADI.map(([g]) => conta(g) > 0 && <i key={g} className={`is-${g}`} style={{ flex: conta(g) }} />)}
        </div>
        <div className="ro-cov-leg">
          {GRADI.filter(([g]) => g !== 'non' || conta(g) > 0).map(([g, k]) => (
            <span key={g}><i className={`is-${g}`} />{p.t(k)}<b>{conta(g)}</b></span>
          ))}
        </div>
        {c.grado === 'degradata' && c.motivoDegrado &&
          <p className="ro-note is-warn"><AlertTriangle size={15} aria-hidden="true" /><span>{c.motivoDegrado}</span></p>}
        <div className="ro-tks">
          {titoli.map(x => <span key={x.grado + x.ticker} className="ro-tk" title={x.motivo}><i className={`is-${x.grado}`} />{x.ticker}</span>)}
        </div>
      </div>
    </section>
  );
}

function DialogoCopertura({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const c = d.viva?.copertura;
  if (!c || !c.dichiarata) return null;
  const titoli = titoliCoperti(c, p);
  const nome = (g: Grado) => p.t(GRADI.find(x => x[0] === g)![1]);
  return (
    <div className="ro-scrim" onClick={e => { if (e.target === e.currentTarget) a.copertura(false); }}>
      <div className="ro-dialog" role="dialog" aria-modal="true" aria-label={p.t('edge.covDialogTitle')} data-zona="dialogo-copertura">
        <Testa icona={<ShieldCheck size={16} />} titolo={p.t('edge.covDialogTitle')}
          nota={[d.oraScan ? p.t('edge.scanAt', { a: d.oraScan }) : '', c.contate ? p.t('edge.covDialogMeta', { a: c.scansionate, b: c.totali }) : ''].filter(Boolean).join(' · ')}>
          <span className="bbn-grow" />
          <button type="button" className="bbn-icon-btn" aria-label={p.t('edge.close')} onClick={() => a.copertura(false)}><X size={16} /></button>
        </Testa>
        <div className="ro-dialog-body">
          <div className="ro-ctab">
            <span className="ro-h">{p.t('edge.covColName')}</span><span className="ro-h">{p.t('edge.covTitle')}</span><span className="ro-h">{p.t('edge.covColReason')}</span>
            {titoli.map(x => [
              <b key={x.ticker + 't'}>{x.ticker}</b>,
              <span key={x.ticker + 'g'} className="ro-g"><i className={`is-${x.grado}`} />{nome(x.grado)}</span>,
              <span key={x.ticker + 'm'} className="ro-muted">{x.motivo}</span>,
            ])}
          </div>
          <div>
            <div className="ro-sec-h"><h3>{p.t('edge.covFactorsH')}</h3></div>
            <p className="ro-prose ro-muted">{c.fattorialiSu != null
              ? p.t('edge.covFactorsText', { a: c.fattorialiSu, b: c.totali })
              : c.fattorialiKo ? p.t('edge.covFactorsKoText', { a: c.fattorialiKo }) : p.t('edge.covFactorsNd')}</p>
          </div>
          {c.nota && <div>
            <div className="ro-sec-h"><h3>{p.t('edge.covNoteH')}</h3><span className="bbn-card-note">{p.t('edge.covNoteSub')}</span></div>
            <p className="ro-prose ro-quote">{c.nota}</p>
          </div>}
        </div>
      </div>
    </div>
  );
}

/* ── elenco ──────────────────────────────────────────────────────── */

function Elenco({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const viva = d.viva!;
  const cats = categorieDi(d.tutti);
  return (
    <section className="bbn-card ro-card ro-elenco" data-zona="lista" data-stato="viva">
      <Testa icona={<List size={16} />} titolo={p.t('edge.listTitle')} nota={p.t('edge.listNote', { a: d.righe.length, b: d.sogliaResa })} />
      {d.tutti.length > 0 && <div className="ro-fbar" role="group" aria-label={p.t('edge.f020')}>
        <button type="button" className="ro-fpill" aria-pressed={!d.cat} data-cat="" onClick={() => a.categoria('')}>
          {p.t('edge.catAll')} <b>{d.tutti.length}</b></button>
        {cats.map(c => (
          <button key={c} type="button" className="ro-fpill" aria-pressed={d.cat === c} data-cat={c} onClick={() => a.categoria(c)}>
            {nomeCategoria(c, p.t)} <b>{viva.perCategoria[c] ?? d.tutti.filter(x => x.s.category === c).length}</b></button>
        ))}
      </div>}
      <div className="ro-list">
        {d.vuoto && (
          <div className="ro-empty" data-vuoto={d.vuoto.tono}>
            <span className={`ro-dir ${d.vuoto.tono === 'misurato' || d.vuoto.tono === 'filtro' ? 'is-neutral' : 'is-caution'}`}>
              {p.t(d.vuoto.tono === 'misurato' ? 'edge.zeroMeasured' : d.vuoto.tono === 'parziale' ? 'edge.zeroPartial' : d.vuoto.tono === 'nd' ? 'edge.zeroNd' : 'edge.zeroFilter')}</span>
            <p>{d.vuoto.testo}</p>
            {d.vuoto.tono === 'filtro'
              ? <button type="button" className="bbn-btn" onClick={() => a.categoria('')}>{p.t('edge.showAll')}</button>
              : d.sogliaResa > SOGLIA_MINIMA_PAGINA && (viva.nTotali ?? 0) > 0
                && <button type="button" className="bbn-btn" onClick={() => a.soglia(SOGLIA_MINIMA_PAGINA)}>{p.t('edge.lowerThreshold', { a: SOGLIA_MINIMA_PAGINA })}</button>}
          </div>
        )}
        {d.righe.map(x => (
          <button key={x.k} type="button" className="ro-row" data-sel={x.k} aria-current={d.sel?.k === x.k ? 'true' : undefined} onClick={() => a.scegli(x.k)}>
            <span className="ro-str"><b>{x.s.strength}</b><span className="ro-bar"><i style={{ width: `${Math.max(0, Math.min(100, x.s.strength))}%` }} /></span></span>
            <IconaTitolo ticker={x.s.ticker} dimensione="sm" />
            <span className="ro-tx">
              <span className="ro-l1"><b>{x.s.ticker}</b><PillCat c={x.s.category} p={p} /><span className="ro-nm">{x.s.name}</span></span>
              <span className="ro-cm">{x.s.reading}</span>
            </span>
            <span className="ro-r"><span className="ro-val">{valore(x.s.value, p)}</span><PillDir s={x.s} p={p} /></span>
          </button>
        ))}
        {viva.illeggibili > 0 && (
          <p className="ro-note is-warn" data-avviso="illeggibili"><AlertTriangle size={15} aria-hidden="true" />
            <span>{viva.illeggibili} {viva.illeggibili === 1 ? p.t('edge.f047') : p.t('edge.f048')} {p.t('edge.f049')}</span></p>
        )}
      </div>
      <p className="ro-foot">{p.t('edge.f050')}</p>
    </section>
  );
}

/* ── dettaglio ───────────────────────────────────────────────────── */

function BoxDiagnosi({ s, d, a, p }: { s: Segnale; d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const g = d.diagnosi && d.diagnosi.ticker === s.ticker ? d.diagnosi : null;
  const testa = (extra?: ReactNode) => (
    <div className="ro-sec-h ro-diag-h"><Stethoscope size={16} aria-hidden="true" /><h3>{p.t('edge.diagH')}</h3>{extra}</div>
  );
  if (!g) {
    return (
      <div className="ro-diag" data-zona="diagnosi">
        {testa(<span className="bbn-card-note">{p.t('edge.diagNote', { a: s.ticker })}</span>)}
        <div className="ro-diag-run">
          <button type="button" className="bbn-btn" data-azione="diagnosi" onClick={() => a.diagnosi(s.ticker)}>{p.t('edge.diagRun')}</button>
          <span className="bbn-card-note">{p.t('edge.diagHint')}</span>
        </div>
      </div>
    );
  }
  if (g.stato === 'attesa') {
    return <div className="ro-diag" data-zona="diagnosi" data-stato="attesa">
      <div className="ro-sec-h ro-diag-h"><RefreshCw size={16} className="ro-spin" aria-hidden="true" /><h3>{p.t('edge.diagBusy', { a: s.ticker })}</h3></div>
    </div>;
  }
  if (g.stato === 'errore') {
    return (
      <div className="ro-diag" data-zona="diagnosi" data-stato="errore">
        {testa()}
        <p className="ro-note is-bad"><AlertTriangle size={15} aria-hidden="true" /><span><b>{p.t('edge.diagFail')}:</b> {g.motivo}</span></p>
        <div><button type="button" className="bbn-btn" data-azione="diagnosi" onClick={() => a.diagnosi(s.ticker)}>{p.t('edge.diagRepeat')}</button></div>
      </div>
    );
  }
  const [sigla, ...resto] = g.verdetto.split(' — ');
  const tono = g.score > 0.6 ? 'is-bullish' : g.score < -0.6 ? 'is-bearish' : 'is-neutral';
  const { pos, ampiezza } = scalaDiagnosi(g.score);
  const num = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toLocaleString(localeDi(p.lingua), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <div className="ro-diag" data-zona="diagnosi" data-stato="ok">
      {testa(<>
        <span className="bbn-card-note">{p.t('edge.diagAt', { a: g.alle ?? '—', b: g.nSegnali ?? '—' })}</span>
        <span className="bbn-grow" />
        <button type="button" className="bbn-link ro-link" data-azione="diagnosi" onClick={() => a.diagnosi(s.ticker)}>{p.t('edge.diagRepeat')}</button>
      </>)}
      <div className="ro-verdetto"><span className={`ro-dir is-large ${tono}`}>{sigla}</span>{resto.length > 0 && <b>{resto.join(' — ')}</b>}</div>
      <div>
        <div className="ro-gauge" role="img" aria-label={`${p.t('edge.diagScore')} ${num(g.score)}`}>
          <span className="ro-gz" style={{ left: `${50 - (0.6 / ampiezza) * 50}%`, right: `${50 - (0.6 / ampiezza) * 50}%` }} />
          <span className="ro-gp" style={{ left: `${pos}%` }} />
          <span className="ro-gl" style={{ left: `${pos}%` }}>{num(g.score)}</span>
        </div>
        <div className="ro-gauge-l"><span>−{ampiezza}</span><span>{p.t('edge.diagBand')}</span><span>+{ampiezza}</span></div>
      </div>
      {g.nota && <span className="bbn-card-note">{g.nota}</span>}
    </div>
  );
}

function Dettaglio({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  const x = d.sel;
  if (!x) return <section className="bbn-card ro-card ro-det" data-zona="dettaglio"><p className="ro-empty">{p.t('edge.detSelect')}</p></section>;
  const s = x.s;
  const ril = rilevatoreDi(s);
  const altri = d.tutti.filter(y => y.s.ticker === s.ticker && y.k !== x.k);
  const dir = dirDi(s);
  const sottoDir = s.category === 'momentum' ? p.t('edge.dirSubMomentum') : dir === 'caution' ? p.t('edge.dirSubCaution') : p.t('edge.dirSubOther');
  return (
    <section className="bbn-card ro-card ro-det" data-zona="dettaglio">
      <div className="ro-det-head">
        <IconaTitolo ticker={s.ticker} dimensione="lg" />
        <div className="ro-det-t">
          <h2>{s.ticker} <PillCat c={s.category} p={p} /> <PillDir s={s} p={p} /></h2>
          <span className="ro-meta">{p.t('edge.detMeta', { a: s.name, b: s.source })}{d.oraScan ? ` · ${p.t('edge.scanAt', { a: d.oraScan })}` : ''}</span>
        </div>
        <button type="button" className="bbn-link ro-link" data-azione="mercati" onClick={() => a.apriMercati(s.ticker)}>
          {p.t('edge.openMarkets')} <ExternalLink size={13} aria-hidden="true" /></button>
      </div>
      <div className="ro-det-body">
        <div className="ro-tiles">
          <div className="ro-tile"><span className="ro-k">{p.t('edge.tStrength')}</span><span className="ro-v">{s.strength}<small> / 100</small></span><span className="ro-s">{p.t(livelloForza(s.strength))}</span></div>
          <div className="ro-tile"><span className="ro-k">{p.t('edge.tValue')}</span><span className="ro-v">{valore(s.value, p)}</span><span className="ro-s">{s.name}</span></div>
          <div className="ro-tile"><span className="ro-k">{p.t('edge.tContext')}</span><span className="ro-v is-sm" title={s.context}>{s.context || '—'}</span><span className="ro-s">{p.t('edge.tContextSub')}</span></div>
          <div className="ro-tile"><span className="ro-k">{p.t('edge.tDirection')}</span><span className={`ro-v is-${dir}`}>{nomeDir(s.direction, p)}</span><span className="ro-s">{sottoDir}</span></div>
        </div>
        <div className="ro-det-cols">
          <div className="ro-col">
            <div>
              <div className="ro-sec-h"><h3>{p.t('edge.readingH')}</h3><span className="bbn-card-note">{p.t('edge.readingNote')}</span></div>
              <p className="ro-prose ro-quote">{s.reading}</p>
            </div>
            {ril && (
              <div data-rilevatore={ril}>
                <div className="ro-sec-h"><h3>{p.t('edge.detectorH')}</h3></div>
                <p className="ro-prose ro-muted ro-sm">{p.t(SPIEGAZIONE[ril][0])}</p>
                <div className="ro-kv">
                  <span>{p.t('edge.tStrength')}</span><b>{p.t(SPIEGAZIONE[ril][1])}</b>
                  <span>{p.t('edge.kvSource')}</span><b>{s.source}</b>
                </div>
              </div>
            )}
          </div>
          <div className="ro-col">
            <div>
              <div className="ro-sec-h"><h3>{p.t('edge.sameH', { a: s.ticker })}</h3><span className="bbn-card-note">{p.t('edge.sameNote')}</span></div>
              {altri.length
                ? <div className="ro-same">{altri.map(y => (
                    <button key={y.k} type="button" className="ro-srow" data-sel={y.k} onClick={() => a.scegli(y.k)}>
                      <span className="ro-s1">{y.s.strength}</span>
                      <span className="ro-stx"><PillCat c={y.s.category} p={p} /><small>{y.s.name} · {valore(y.s.value, p)}</small></span>
                      <PillDir s={y.s} p={p} />
                    </button>))}</div>
                : <p className="ro-prose ro-muted ro-sm">{p.t('edge.sameNone', { a: s.ticker })}</p>}
            </div>
            <BoxDiagnosi s={s} d={d} a={a} p={p} />
          </div>
        </div>
      </div>
    </section>
  );
}

/* ── stati senza scansione ───────────────────────────────────────── */

function Attesa({ d, p }: { d: DatiRicerca; p: P }) {
  const L = LATENZE_MISURATE;
  const limiteS = d.timeoutMs / 1000;
  const pc = (v: number) => `${Math.min(100, (v / limiteS) * 100)}%`;
  return <>
    <div className="ro-overview" aria-hidden="true"><div className="ro-skel" /><div className="ro-skel" /></div>
    <section className="bbn-card ro-card ro-stato" data-stato="attesa" title={copyAttesa(d.attesaS, d.timeoutMs, d.forzata)}>
      <div className="ro-stato-in">
        <h2><RefreshCw size={18} className="ro-spin" aria-hidden="true" />{p.t('edge.waitTitle')}</h2>
        <span className="bbn-card-note ro-sm">
          <b>{p.t('edge.f115', { a: durata(d.attesaS) })}</b> · {p.t('edge.waitLimit', { a: durata(limiteS) })}
          {d.forzata ? ` · ${p.t('edge.forcedNote')}` : ''}
        </span>
        <div className="ro-track">
          <span className="ro-fill" style={{ width: pc(d.attesaS) }} />
          <span className="ro-m" style={{ left: pc(L.caldoS) }} /><span className="ro-ml is-t" style={{ left: pc(L.caldoS) }}>{p.t('edge.waitWarm', { a: Math.round(L.caldoS) })}</span>
          <span className="ro-m" style={{ left: pc(L.freddoS) }} /><span className="ro-ml is-t" style={{ left: pc(L.freddoS) }}>{p.t('edge.waitCold', { a: Math.round(L.freddoS) })}</span>
          <span className="ro-ml is-b is-start">0</span><span className="ro-ml is-b is-end">{p.t('edge.waitPageLimit', { a: durata(limiteS) })}</span>
        </div>
        <div className="ro-cases">
          <div className="ro-tile"><span className="ro-k">{p.t('edge.caseCache')}</span><span className="ro-v is-sm">~{L.cacheS.toLocaleString(localeDi(p.lingua))} s</span><span className="ro-s">{p.t('edge.caseCacheSub')}</span></div>
          <div className="ro-tile"><span className="ro-k">{p.t('edge.caseFresh')}</span><span className="ro-v is-sm">{Math.round(L.caldoS)}–{Math.round(L.freddoS)} s</span><span className="ro-s">{p.t('edge.f013', { a: L.posizioni, b: L.misurateIl })}</span></div>
          <div className="ro-tile"><span className="ro-k">{p.t('edge.caseQueue')}</span><span className="ro-v is-sm">{p.t('edge.caseQueueV', { a: `~${(L.lockS / 60).toLocaleString(localeDi(p.lingua), { maximumFractionDigits: 1 })} min` })}</span><span className="ro-s">{p.t('edge.caseQueueSub')}</span></div>
        </div>
      </div>
    </section>
  </>;
}

function Guasto({ d, a, p }: { d: DatiRicerca; a: AzioniRicerca; p: P }) {
  if (d.esito.stato !== 'guasto') return null;
  const e = d.esito;
  const titoli: Record<string, ChiavePagina> = { payload: 'edge.f004', timeout: 'edge.f005', chiamata: 'edge.f006', forma: 'edge.f007' };
  const spiega = e.origine === 'payload'
    ? p.t('edge.f036') + (e.quando ? p.t('edge.f037', { a: oraIt(e.quando) || e.quando }) : '') + ': ' + p.t('edge.f038')
    : e.origine === 'timeout' ? p.t('edge.f039') + p.t('edge.f040')
    : e.origine === 'chiamata' ? p.t('edge.f041') : p.t('edge.f042');
  return (
    <section className="bbn-card ro-card ro-stato" data-stato="guasto" data-origine={e.origine}>
      <div className="ro-stato-in">
        <span className="ro-dir is-bearish ro-self-start">{e.origine === 'timeout' ? p.t('edge.f034') : p.t('edge.f035')} · {p.t(titoli[e.origine])}</span>
        <p className="ro-prose ro-quote">{e.motivo}</p>
        <p className="ro-prose ro-muted ro-sm">{spiega}</p>
        <div><button type="button" className={`bbn-btn${e.origine === 'timeout' ? ' is-primary' : ''}`} data-azione="riprova" disabled={d.loading}
          onClick={a.riprova}><RefreshCw size={14} aria-hidden="true" />{p.t('edge.retry')}</button></div>
      </div>
    </section>
  );
}

/* ── pagina ──────────────────────────────────────────────────────── */

export default function VistaRicerca({ d, a }: { d: DatiRicerca; a: AzioniRicerca }) {
  const p = parole();
  const viva = d.viva;
  return (
    <div className="bbn-font bbn-ricerca">
      <Testata d={d} a={a} p={p} />
      {viva?.ultimaChiamataFallita && (
        <p className="ro-note is-warn" data-avviso="chiamata-fallita"><AlertTriangle size={15} aria-hidden="true" />
          <span><b>{p.t('edge.f044')}</b> {viva.ultimaChiamataFallita.motivo} {p.t('edge.f045')}{d.sogliaResa}).</span>
          <button type="button" className="bbn-btn ro-btn-sm" data-azione="riprova" disabled={d.loading} onClick={a.riprova}>{p.t('edge.retry')}</button>
        </p>
      )}
      {d.esito.stato === 'attesa' && <Attesa d={d} p={p} />}
      {d.esito.stato === 'guasto' && <Guasto d={d} a={a} p={p} />}
      {viva && <>
        <div className="ro-overview"><Mappa d={d} a={a} p={p} /><Copertura d={d} a={a} p={p} /></div>
        <div className={`ro-flow${d.tutti.length ? '' : ' is-zero'}`}>
          <Elenco d={d} a={a} p={p} />
          {d.tutti.length > 0 && <Dettaglio d={d} a={a} p={p} />}
        </div>
        {d.copertura && <DialogoCopertura d={d} a={a} p={p} />}
      </>}
    </div>
  );
}
