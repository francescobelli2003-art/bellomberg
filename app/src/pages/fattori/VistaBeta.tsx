import type { KeyboardEvent } from 'react';
import { AlertTriangle } from 'lucide-react';
import type { Calibro, Lancetta } from '@/lib/fattori';
import { QUOTE, cifra, p2, righello, suAsse } from './calcoli';
import { Info } from './pezzi';
import type { Parole } from './parole';

/* Vista «Beta e misure»: il beta di portafoglio è il protagonista (righello con tutte le stime,
   banda delle riconciliate, finestra della soglia), sotto alpha e Sharpe a confronto fra le
   due fonti. Componente di sola presentazione: lo stato vive in FactorsPage. */

export interface LatoMisura { nome: string; v: number | null; come: string; motivo?: string | null }
export interface Misura { a: LatoMisura; b: LatoMisura; dec: number; unita: string; /** lo scarto nella sua unità: «50 pp» per l'alpha, puro per lo Sharpe */ scarto: (v: string) => string }

export interface PropsBeta {
  w: Parole;
  cal: Calibro;
  periodo: string;
  scelta: number;
  onScelta: (i: number) => void;
  onTasti: (e: KeyboardEvent) => void;
  bottone: (i: number, el: HTMLButtonElement | null) => void;
  inAttesa: boolean;
  recAtt: boolean;
  recErr: string | null;
  facErr: string | null;
  costoRiconciliazione: number;
  alpha: Misura;
  sharpe: Misura;
  avvisoMisure: string[];
}

export const RICONCILIATO = 'RECONCILED';

export function nomeFonte(l: Lancetta, w: Parole, periodo: string): string {
  if (l.chiave === 'factor_model_mkt') return w.srcFactor3;
  if (l.chiave === 'factor_model_mkt_1y') return w.srcFactorWindow(periodo);
  if (l.chiave === 'portfolio_risk_spy') return w.srcSpy;
  if (l.chiave === 'advanced_metrics_twr') return w.srcTwr;
  return l.etichetta;
}
function glossaFonte(l: Lancetta, w: Parole): string {
  if (l.chiave === 'factor_model_mkt') return w.glossFactor3;
  if (l.chiave === 'factor_model_mkt_1y') return w.glossFactorWindow;
  if (l.chiave === 'portfolio_risk_spy') return w.glossSpy;
  if (l.chiave === 'advanced_metrics_twr') return w.glossTwr;
  return w.glossOther;
}

export function Esito({ cal, recAtt, w }: { cal: Calibro; recAtt: boolean; w: Parole }) {
  if (cal.verdetto) {
    const ok = cal.verdetto === RICONCILIATO;
    return (
      <span className={'fat-pill ' + (ok ? 'is-good' : 'is-bad')} title={w.verdictTitle(cal.verdetto)} data-verdetto={cal.verdetto}>
        <i className="fat-dot" />{ok ? w.verdictOk : w.verdictKo}
      </span>
    );
  }
  if (recAtt) return <span className="fat-pill is-flat">{w.verdictPending}</span>;
  return null;
}

export default function VistaBeta(p: PropsBeta) {
  const { w, cal } = p;
  const ok = cal.verdetto === RICONCILIATO;
  const giudicato = cal.verdetto !== null;
  const nRic = cal.lancette.filter(l => l.riconciliato).length;
  // La finestra della soglia si centra sul PUNTO MEDIO delle riconciliate, non sulla mediana:
  // il controllo giudica max − min ≤ soglia, e «tutte dentro la finestra» equivale al criterio
  // solo con questo centro (audit/24 B.8).
  const centro = cal.consensoMin !== null && cal.consensoMax !== null ? (cal.consensoMin + cal.consensoMax) / 2 : cal.consenso;
  const soglia = cal.soglia !== null && centro !== null ? [centro - cal.soglia / 2, centro + cal.soglia / 2] : null;
  const r = righello(cal.lancette, soglia || []);
  // Al primo caricamento c'è solo la stima della pagina: finché il controllo non risponde si
  // mostra l'attesa, non un righello con una lancetta e un consenso vuoto.
  const attesaControllo = p.recAtt && !cal.lancette.some(l => l.riconciliato);

  return (
    <div className="fat-view fat-beta">
      <section className="bbn-card fat-card fat-beta-card" aria-label={w.betaTitle}>
        <header className="bbn-card-head">
          <h2>{w.betaTitle}</h2>
          <Info testo={w.betaInfo} />
          {cal.lancette.length > 0 && !attesaControllo && (
            <span className="bbn-card-note">
              {cal.lancette.length === 1 ? w.betaCountOne : w.betaCount(cal.lancette.length)} · {w.betaReconciled(nRic)}
            </span>
          )}
          <span className="bbn-grow" />
          <Esito cal={cal} recAtt={p.recAtt} w={w} />
        </header>

        <div className="fat-body">
          {cal.lancette.length === 0 || attesaControllo ? (
            <div className="attesa fat-wait">
              <b>{p.inAttesa || attesaControllo ? w.waiting : w.betaEmpty}</b>
              <span>{p.inAttesa || attesaControllo ? w.waitingSub(cifra(p.costoRiconciliazione, 1)) : (p.facErr || p.recErr || w.verdictMissing(w.errorMissing))}</span>
              {(p.inAttesa || attesaControllo) && <div className="fat-fonti">{[0, 1, 2, 3].map(i => <div key={i} className="fat-skel" />)}</div>}
            </div>
          ) : (
            <>
              <div className="fat-beta-top">
                <div className="fat-cons">
                  <span className="fat-lbl">{w.consensus} <Info testo={w.consensusInfo} /></span>
                  <span className="fat-big num">{cifra(cal.consenso, 2)}</span>
                  <div className="fat-row">
                    {cal.spreadMax !== null && <span className={'fat-pill ' + (giudicato ? (ok ? 'is-good' : 'is-bad') : 'is-flat')}>{w.spreadPill(cifra(cal.spreadMax, 2))}</span>}
                    {cal.soglia !== null && <span className="fat-pill is-flat">{w.thresholdPill(cifra(cal.soglia, 2))}</span>}
                  </div>
                  {giudicato && <small>{ok ? w.okText : w.koText}</small>}
                  {!giudicato && !p.recAtt && <small className="fat-muted">{p.recErr ? w.verdictMissing(p.recErr) : w.verdictMissing(w.errorMissing)}</small>}
                </div>

                <div className="fat-ruler-wrap">
                  <div className="calibro fat-ruler" role="radiogroup" aria-label={w.rulerLabel}
                    data-strato="calibro" onKeyDown={p.onTasti}>
                    <span className="fat-axis" />
                    {r.tacche.map(t => (
                      <span key={t.toFixed(2)}>
                        <span className="fat-tick" style={{ left: p2(r.pc(t)) }} />
                        <span className="fat-tlab num" style={{ left: p2(r.pc(t)) }}>{cifra(t, 1)}</span>
                      </span>
                    ))}
                    {cal.consensoMin !== null && cal.consensoMax !== null && (
                      <span className={'fat-band' + (ok ? ' is-ok' : '')}
                        style={{ left: p2(r.pc(cal.consensoMin)), width: p2(r.pc(cal.consensoMax) - r.pc(cal.consensoMin)) }} />
                    )}
                    {soglia && <span className="fat-soglia" style={{ left: p2(r.pc(soglia[0])), width: p2(r.pc(soglia[1]) - r.pc(soglia[0])) }} />}
                    {/* Sul righello resta solo il numero: con due stime a pochi millesimi i nomi si
                        sovrapponevano. Il nome sta nella scheda qui sotto, nello stesso ordine. */}
                    {cal.lancette.map((l, i) => {
                      const nome = nomeFonte(l, w, p.periodo);
                      return (
                        <button key={l.chiave} ref={el => p.bottone(i, el)} type="button" role="radio"
                          aria-checked={i === p.scelta} tabIndex={i === p.scelta ? 0 : -1}
                          aria-label={`${nome}: ${cifra(l.valore, 3)}. ${l.riconciliato ? w.radioReconciled : w.radioOutside}. ${l.definizione || ''}`}
                          className={'fat-mk' + (l.riconciliato ? '' : ' is-out')}
                          style={{ left: p2(r.pc(l.valore)), top: QUOTE[i % QUOTE.length] + '%' }}
                          onFocus={() => p.onScelta(i)} onMouseEnter={() => p.onScelta(i)} onClick={() => p.onScelta(i)}>
                          <span className="v num">{cifra(l.valore, 2)}</span><span className="stem" /><span className="head" />
                        </button>
                      );
                    })}
                  </div>
                  <div className="fat-rkey">
                    <span><i className="k-r" />{w.keyReconciled}</span>
                    {cal.lancette.some(l => !l.riconciliato) && <span><i className="k-o" />{w.keyOutside}</span>}
                    {cal.consensoMin !== null && <span><i className={'k-b' + (ok ? ' is-ok' : '')} />{w.keyBand}</span>}
                    {cal.soglia !== null && <span><i className="k-s" />{w.keyThreshold(cifra(cal.soglia, 2))}</span>}
                  </div>
                </div>
              </div>

              <div className="fat-fonti">
                {cal.lancette.map((l, i) => (
                  <button key={l.chiave} type="button" className="fat-fonte" aria-current={i === p.scelta}
                    onClick={() => p.onScelta(i)} onMouseEnter={() => p.onScelta(i)}>
                    <span className="k">
                      {nomeFonte(l, w, p.periodo)}
                      <span className="bbn-grow" />
                      {!l.riconciliato && <span className="fat-pill is-outline">{w.radioOutside}</span>}
                      <Info testo={l.definizione || w.glossOther} />
                    </span>
                    <b className="num">{cifra(l.valore, 2)}</b>
                    <small>{glossaFonte(l, w)}</small>
                  </button>
                ))}
              </div>

              {(cal.nota || (giudicato && !ok)) && (
                <div className={'fat-note ' + (giudicato && !ok ? 'is-bad' : 'is-plain')}>
                  {giudicato && !ok && <AlertTriangle size={16} aria-hidden="true" />}
                  <span className="txt">{cal.nota || w.koText}</span>
                </div>
              )}
              {cal.fontiCadute.length > 0 && (
                <div className="fat-note is-warn"><span className="txt">{w.sourcesFailed(cal.fontiCadute.map(([k, v]) => `${k} (${v})`).join(' · '))}</span></div>
              )}
            </>
          )}
        </div>
      </section>

      <Duello titolo={w.alphaTitle} info={w.alphaInfo} m={p.alpha} w={w} />
      <Duello titolo={w.sharpeTitle} info={w.sharpeInfo} m={p.sharpe} w={w} />
      {p.avvisoMisure.length > 0 && (
        <div className="fat-misure-err" role="status">
          {p.avvisoMisure.map(a => <div key={a} className="fat-note is-bad"><span className="txt">{a}</span></div>)}
        </div>
      )}
    </div>
  );
}

/** Due misure della stessa grandezza e la loro distanza. Nessuna delle due ha un test di
 *  significatività: il segno non è un giudizio, quindi i numeri restano nel colore della fonte. */
function Duello({ titolo, info, m, w }: { titolo: string; info: string; m: Misura; w: Parole }) {
  const { a, b } = m;
  const scarto = a.v !== null && b.v !== null ? Math.abs(a.v - b.v) : null;
  const massimo = Math.max(Math.abs(a.v ?? 0), Math.abs(b.v ?? 0), 0.01);
  const lim = scala(massimo * 1.15);
  const fmt = (v: number | null) => cifra(v, m.dec, true) + (v === null ? '' : m.unita);
  const lato = (x: LatoMisura, cls: string) => (
    <div className={'fat-side ' + cls}>
      <span className="fat-lbl">{x.nome}</span>
      <b className="num">{x.v === null ? <span className="fat-muted fat-missing">{x.motivo || w.notMeasured}</span> : fmt(x.v)}</b>
      <small>{x.come}</small>
    </div>
  );
  return (
    <section className="bbn-card fat-card fat-duel" aria-label={titolo}>
      <header className="bbn-card-head">
        <h2>{titolo}</h2>
        <Info testo={info} />
        <span className="bbn-card-note">{w.twoSources}</span>
        <span className="bbn-grow" />
        {scarto === null
          ? <span className="fat-pill is-flat fat-muted">{w.gapMissing}</span>
          : <span className="fat-pill is-warn">{w.gapPill(m.scarto(cifra(scarto, m.dec)))}</span>}
      </header>
      <div className="fat-body">
        <div className="fat-duel-row">
          {lato(a, 'is-a')}
          <div className="fat-gap"><span>{w.gapLabel}</span>
            <b className="num">{scarto === null ? '—' : m.scarto(cifra(scarto, m.dec))}</b>
          </div>
          {lato(b, 'is-b')}
        </div>
        <div className="fat-dumb" aria-hidden="true">
          <span className="ax" />
          {[-1, -0.5, 0.5, 1].map(k => (
            <span key={k}>
              <span className="tk" style={{ left: p2(suAsse(k * lim, lim)) }} />
              <span className="tl num" style={{ left: p2(suAsse(k * lim, lim)) }}>{cifra(k * lim, decimali(k * lim), true)}{m.unita}</span>
            </span>
          ))}
          {a.v !== null && b.v !== null && (
            <span className="seg" style={{ left: p2(Math.min(suAsse(a.v, lim), suAsse(b.v, lim))), width: p2(Math.abs(suAsse(a.v, lim) - suAsse(b.v, lim))) }} />
          )}
          <span className="z" style={{ left: '50%' }} /><span className="tl" style={{ left: '50%' }}>0</span>
          {a.v !== null && <span className="pt is-a" style={{ left: p2(suAsse(a.v, lim)) }} title={`${a.nome}: ${fmt(a.v)}`} />}
          {b.v !== null && <span className="pt is-b" style={{ left: p2(suAsse(b.v, lim)) }} title={`${b.nome}: ${fmt(b.v)}`} />}
        </div>
        <div className="fat-note is-plain"><span className="txt">{w.noTest}</span></div>
      </div>
    </section>
  );
}

/** Decimali minimi per scrivere una tacca senza arrotondarla (2,5 → 1; 1,25 → 2). */
const decimali = (v: number) => Number.isInteger(v) ? 0 : Number.isInteger(v * 10) ? 1 : 2;

/** Un fondo scala «tondo» (1, 2, 2,5, 5 × 10^k) per le tacche del confronto. */
function scala(v: number): number {
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * e >= v) return m * e;
  return 10 * e;
}
