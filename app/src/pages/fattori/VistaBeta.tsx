import type { KeyboardEvent } from 'react';
import { AlertTriangle } from 'lucide-react';
import { perche } from '@/lib/fattori';
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
/** I tre esiti di advanced_metrics.reconcile_betas. Solo UNRELIABLE vuol dire «fonti discordanti»:
 *  con INSUFFICIENT_SOURCES nessun confronto è avvenuto, e un esito sconosciuto si scrive com'è. */
export const DIVERGENTE = 'UNRELIABLE';
export const INSUFFICIENTE = 'INSUFFICIENT_SOURCES';
/** Un esito fuori da questi tre non è un giudizio: niente banda del consenso, e si dichiara
 *  «esito n.d.» (il codice compare solo fra parentesi, come per il guardrail del punteggio). */
export const esitoNoto = (v: string | null): v is string => v === RICONCILIATO || v === DIVERGENTE || v === INSUFFICIENTE;
/** Via libera all'uso decisionale: dal 06/10 il backend la dichiara con `beta_per_decisioni`, e
 *  RECONCILED senza quel `true` è un payload incoerente che non autorizza niente. */
export const viaLibera = (cal: Calibro) => cal.verdetto === RICONCILIATO && cal.perDecisioni === true;
export function testoEsito(v: string, w: Parole, perDecisioni: boolean | null): { pill: string; frase: string } {
  if (v === RICONCILIATO) return perDecisioni === true
    ? { pill: w.verdictOk, frase: w.okText } : { pill: w.verdictNoClearance, frase: w.noClearanceText };
  if (v === DIVERGENTE) return { pill: w.verdictKo, frase: w.koText };
  if (v === INSUFFICIENTE) return { pill: w.verdictInsufficient, frase: w.insufficientText };
  return { pill: w.verdictUnknown, frase: w.unknownText(v) };
}
/** Il suggerimento sull'esito: mai il codice grezzo, sempre il nome tradotto. */
export const titoloEsito = (v: string, w: Parole, perDecisioni: boolean | null) =>
  w.verdictTitle(esitoNoto(v) ? testoEsito(v, w, perDecisioni).pill : w.verdictUnknownCode(v));

/** `metrics.beta_guardrail` del punteggio quant in una frase: il codice non si mostra mai grezzo,
 *  e uno sconosciuto (o assente) si dichiara «guardrail n.d.» col codice fra parentesi. */
export function fraseGuardrailBeta(codice: string | null | undefined, w: Parole): string {
  switch (codice) {
    case 'RECONCILED': return w.gqReconciled;
    case 'UNRELIABLE': return w.gqUnreliable;
    case 'INSUFFICIENT_SOURCES': return w.gqInsufficient;
    case 'NON_CALCOLATO': return w.gqNotComputed;
    case 'NON_DISPONIBILE': return w.gqUnavailable;
    case 'RECONCILED_SENZA_VIA_LIBERA': return w.gqNoClearance;
    case 'RECONCILED_SENZA_FONTE_RISCHIO': return w.gqNoRiskSource;
    default: return codice ? w.gqUnknown(codice) : w.gqMissing;
  }
}

export function nomeFonte(l: Lancetta, w: Parole, periodo: string): string {
  return nomeChiave(l.chiave, w, periodo, l.etichetta);
}
/** Il nome di una fonte dalla sua chiave: serve anche dove non c'è una lancetta (fonti cadute o
 *  escluse senza beta). Una chiave sconosciuta senza etichetta propria si dichiara «non
 *  riconosciuta», col codice solo fra parentesi. */
export function nomeChiave(chiave: string, w: Parole, periodo: string, etichetta?: string): string {
  if (chiave === 'factor_model_mkt') return w.srcFactor3;
  if (chiave === 'factor_model_mkt_1y') return w.srcFactorWindow(periodo);
  if (chiave === 'portfolio_risk_spy') return w.srcSpy;
  if (chiave === 'advanced_metrics_twr') return w.srcTwr;
  return etichetta && etichetta !== chiave ? etichetta : w.srcUnknown(chiave);
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
    const ok = viaLibera(cal);
    return (
      <span className={'fat-pill ' + (ok ? 'is-good' : cal.verdetto === DIVERGENTE ? 'is-bad' : 'is-warn')} title={titoloEsito(cal.verdetto, w, cal.perDecisioni)} data-verdetto={cal.verdetto}>
        <i className="fat-dot" />{testoEsito(cal.verdetto, w, cal.perDecisioni).pill}
      </span>
    );
  }
  if (recAtt) return <span className="fat-pill is-flat">{w.verdictPending}</span>;
  return null;
}

export default function VistaBeta(p: PropsBeta) {
  const { w, cal } = p;
  const ok = viaLibera(cal);
  const giudicato = cal.verdetto !== null;
  const esito = cal.verdetto !== null ? testoEsito(cal.verdetto, w, cal.perDecisioni) : null;
  const divergente = cal.verdetto === DIVERGENTE;
  // fonti escluse per osservazioni insufficienti: anche con RECONCILED la nota va letta
  const conEsclusioni = cal.lancette.some(l => l.esclusa !== null) || cal.esclusaSenzaBeta.length > 0;
  // ⚠ Con fonti discordanti NON esiste una banda del consenso: gli estremi del backend
  //   (`indicative.range`) si disegnano come intervallo indicativo, con l'etichetta fissa
  //   «non per decisioni». Senza `indicative` non si disegna niente (mai ricostruito qui).
  //   Con un esito sconosciuto (o assente) nessuno ha giudicato le stime: niente banda.
  const banda = divergente
    ? (cal.indicativo ? { min: cal.indicativo.min, max: cal.indicativo.max, indicativa: true } : null)
    : (esitoNoto(cal.verdetto) && cal.consensoMin !== null && cal.consensoMax !== null ? { min: cal.consensoMin, max: cal.consensoMax, indicativa: false } : null);
  const nRic = cal.lancette.filter(l => l.riconciliato).length;
  // La finestra della soglia si centra sul PUNTO MEDIO delle riconciliate, non sulla mediana:
  // il controllo giudica max − min ≤ soglia, e «tutte dentro la finestra» equivale al criterio
  // solo con questo centro (audit/24 B.8).
  const centro = cal.consensoMin !== null && cal.consensoMax !== null ? (cal.consensoMin + cal.consensoMax) / 2 : cal.consenso;
  const soglia = cal.soglia !== null && centro !== null ? [centro - cal.soglia / 2, centro + cal.soglia / 2] : null;
  const r = righello(cal.lancette, (soglia || []).concat(banda ? [banda.min, banda.max] : []));
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
                  <span className="fat-big num">{cal.consenso === null ? <span className="fat-muted">{w.na}</span> : cifra(cal.consenso, 2)}</span>
                  <div className="fat-row">
                    {cal.spreadMax !== null && <span className={'fat-pill ' + (giudicato ? (ok ? 'is-good' : divergente ? 'is-bad' : 'is-warn') : 'is-flat')}>{w.spreadPill(cifra(cal.spreadMax, 2))}</span>}
                    {cal.soglia !== null && <span className="fat-pill is-flat">{w.thresholdPill(cifra(cal.soglia, 2))}</span>}
                  </div>
                  {divergente && cal.consenso === null && <small>{w.consensusNone}</small>}
                  {divergente && cal.indicativo && cal.indicativo.mediana !== null && (
                    <small className="fat-indicativa" data-uso="non_per_decisioni">{w.indicativeMedian(cifra(cal.indicativo.mediana, 2))} · <b>{w.notForDecisions}</b></small>
                  )}
                  {esito && <small>{esito.frase}</small>}
                  {!giudicato && !p.recAtt && <small className="fat-muted">{p.recErr ? w.verdictMissing(p.recErr) : w.verdictMissing(w.errorMissing)}</small>}
                </div>

                <div className="fat-ruler-wrap">
                  {/* L'etichetta «non per decisioni» sta in una riga SUA sopra il righello, centrata
                      sull'intervallo: dentro il righello finiva sugli steli e sui valori delle lancette.
                      Il centro si tiene fra 12% e 88% perché il testo non esca dal riquadro. */}
                  {banda && banda.indicativa && (
                    <div className="fat-band-row" aria-hidden="true">
                      <span className="fat-band-lbl" style={{ left: p2(Math.min(88, Math.max(12, (r.pc(banda.min) + r.pc(banda.max)) / 2))) }}>{w.notForDecisions}</span>
                    </div>
                  )}
                  <div className="calibro fat-ruler" role="radiogroup" aria-label={w.rulerLabel}
                    data-strato="calibro" onKeyDown={p.onTasti}>
                    <span className="fat-axis" />
                    {r.tacche.map(t => (
                      <span key={t.toFixed(2)}>
                        <span className="fat-tick" style={{ left: p2(r.pc(t)) }} />
                        <span className="fat-tlab num" style={{ left: p2(r.pc(t)) }}>{cifra(t, 1)}</span>
                      </span>
                    ))}
                    {banda && (banda.indicativa ? (
                      <span className="fat-band is-indicativa" data-uso="non_per_decisioni" title={`${w.indicativeRange} · ${w.notForDecisions}`}
                        style={{ left: p2(r.pc(banda.min)), width: p2(r.pc(banda.max) - r.pc(banda.min)) }} />
                    ) : (
                      <span className={'fat-band' + (ok ? ' is-ok' : '')}
                        style={{ left: p2(r.pc(banda.min)), width: p2(r.pc(banda.max) - r.pc(banda.min)) }} />
                    ))}
                    {soglia && <span className="fat-soglia" style={{ left: p2(r.pc(soglia[0])), width: p2(r.pc(soglia[1]) - r.pc(soglia[0])) }} />}
                    {/* Sul righello resta solo il numero: con due stime a pochi millesimi i nomi si
                        sovrapponevano. Il nome sta nella scheda qui sotto, nello stesso ordine. */}
                    {cal.lancette.map((l, i) => {
                      const nome = nomeFonte(l, w, p.periodo);
                      return (
                        <button key={l.chiave} ref={el => p.bottone(i, el)} type="button" role="radio"
                          aria-checked={i === p.scelta} tabIndex={i === p.scelta ? 0 : -1}
                          aria-label={`${nome}: ${cifra(l.valore, 3)}. ${l.riconciliato ? w.radioReconciled : l.esclusa ? (l.esclusa.nObs === null ? w.radioExcludedUnverifiable : w.radioExcluded) : w.radioOutside}. ${l.definizione || perche(l.definizioneMuta)}`}
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
                    {banda && (banda.indicativa
                      ? <span><i className="k-b is-indicativa" />{w.indicativeRange} · <b>{w.notForDecisions}</b></span>
                      : <span><i className={'k-b' + (ok ? ' is-ok' : '')} />{w.keyBand}</span>)}
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
                      {l.esclusa
                        ? <span className="fat-pill is-warn">{w.excludedPill(l.esclusa.nObs === null ? w.na : cifra(l.esclusa.nObs, 0), l.esclusa.minObs === null ? w.na : cifra(l.esclusa.minObs, 0))}</span>
                        : !l.riconciliato && <span className="fat-pill is-outline">{w.radioOutside}</span>}
                      <Info testo={l.definizione || perche(l.definizioneMuta)} />
                    </span>
                    <b className="num">{cifra(l.valore, 2)}</b>
                    {/* l'esclusa dice PERCHÉ (testo del backend); le altre la glossa e le osservazioni */}
                    {l.esclusa
                      ? <small className="fat-bad">{l.esclusa.motivo}</small>
                      : <small>{glossaFonte(l, w)}{l.nObs !== null ? ' · ' + w.obsCount(cifra(l.nObs, 0)) : ''}</small>}
                  </button>
                ))}
              </div>

              {(cal.nota || (esito && !ok)) && (
                <div className={'fat-note ' + (giudicato && !ok ? (divergente ? 'is-bad' : 'is-warn') : conEsclusioni ? 'is-warn' : 'is-plain')}>
                  {((giudicato && !ok) || conEsclusioni) && <AlertTriangle size={16} aria-hidden="true" />}
                  <span className="txt">{cal.nota || (esito ? esito.frase : '')}</span>
                </div>
              )}
              {cal.fontiCadute.length > 0 && (
                <div className="fat-note is-warn"><span className="txt">{w.sourcesFailed(cal.fontiCadute.map(([k, v]) => `${nomeChiave(k, w, p.periodo)} (${v})`).join(' · '))}</span></div>
              )}
              {cal.esclusaSenzaBeta.length > 0 && (
                <div className="fat-note is-warn"><span className="txt">{w.excludedNoValue(cal.esclusaSenzaBeta.map(([k, v]) => `${nomeChiave(k, w, p.periodo)} (${v})`).join(' · '))}</span></div>
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
