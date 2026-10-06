import { NOME_FATTORE, perche } from '@/lib/fattori';
import type { Copertura, Fattore, RigaFinestra, RigaRegione, Scartato } from '@/lib/fattori';
import { dataIt } from '@/lib/quota';
import { CHIAVE_FATTORE, cifra, euro, p2, pct, suAsse } from './calcoli';
import { Info } from './pezzi';
import type { Parole } from './parole';

/* Vista «Fattori»: profilo del portafoglio sui sei fattori (finestra della pagina contro 3 anni),
   copertura del modello e dataset regionali. Sola presentazione. */

export interface PropsFattori {
  w: Parole;
  conf: RigaFinestra[];
  scala: number;
  etichetta1: string;
  etichetta3: string;
  stato3: 'ok' | 'attesa' | 'errore';
  err3: string | null;
  nAnalizzati: number | null;
  cop: Copertura;
  snapErr: string | null;
  reg: RigaRegione[];
  fuori: { n: number; righe: Scartato[] };
  ultimoDato: string | null;
  ritardo: number | null;
  storico: [number, number] | null;
}

const glossa = (f: Fattore, w: Parole) => ({
  beta_market: w.glossMKT, beta_smb: w.glossSMB, beta_hml: w.glossHML,
  beta_rmw: w.glossRMW, beta_cma: w.glossCMA, beta_mom: w.glossMOM,
}[f]);

export default function VistaFattori(p: PropsFattori) {
  const { w, conf } = p;
  // fondo scala tondo (mezze unità): le tacche cadono su valori leggibili, mai ±1,9
  const scala = Math.max(0.5, Math.ceil(p.scala * 2) / 2);
  const righe = conf.filter(r => !r.fuoriScala);
  const alfa = conf.find(r => r.fuoriScala) || null;
  const barra = (v: number | null, cls: string) => v === null ? null
    : <i className={cls} style={{ left: p2(Math.min(50, suAsse(v, scala))), width: p2(Math.abs(suAsse(v, scala) - 50)) }} />;
  const quota = p.cop.investitoSuNav;

  return (
    <div className="fat-view fat-fattori">
      <section className="bbn-card fat-card fat-prof-card" aria-label={w.profTitle}>
        <header className="bbn-card-head">
          <h2>{w.profTitle}</h2>
          <Info testo={w.profInfo} />
          <span className="bbn-card-note">{w.profNote}</span>
          <span className="bbn-grow" />
          {p.stato3 === 'attesa' && <span className="fat-pill is-flat">{w.window3Loading}</span>}
          {p.stato3 === 'errore' && <span className="fat-pill is-warn">{w.window3Unavailable(p.err3 || w.errorMissing)}</span>}
          <span className="fat-legend"><span><i className="is-a" />{p.etichetta1}</span><span><i className="is-b" />{p.etichetta3}</span></span>
        </header>
        <div className="fat-body">
          <div className="fat-prof" role="table" aria-label={w.profTitle}>
            <div className="fat-prow is-head" role="row">
              <span role="columnheader">{w.colFactor}</span>
              <span className="fat-prow-dir" role="columnheader"><span>{w.colNeg}</span><span>{w.colPos}</span></span>
              <span className="r" role="columnheader">{p.etichetta1}</span>
              <span className="r" role="columnheader">{p.etichetta3}</span>
              <span className="r" role="columnheader" title={w.deltaHint}>{w.colDelta}</span>
            </div>
            {righe.map(r => {
              const f = r.chiave as Fattore;
              const forte = r.delta !== null && Math.abs(r.delta) > 0.4;
              return (
                <div key={r.chiave} className="fat-prow" role="row" data-fattore={r.chiave}>
                  <div className="nm" role="cell"><b>{NOME_FATTORE[f]?.lungo ?? r.etichetta} <em>{CHIAVE_FATTORE[f] ?? ''}</em></b><small>{glossa(f, w)}</small></div>
                  <div className="fat-bars" role="cell" title={`${p.etichetta1} ${cifra(r.unAnno)} · ${p.etichetta3} ${cifra(r.treAnni)}`}>
                    {barra(r.unAnno, 'b1')}{barra(r.treAnni, 'b3')}
                  </div>
                  <span className="r v1 num" role="cell">{r.unAnno === null ? <span className="fat-muted" title={perche('finestra-assente')}>—</span> : cifra(r.unAnno, 2, true)}</span>
                  <span className="r v3 num" role="cell">{r.treAnni === null ? <span className="fat-muted" title={perche('finestra-assente')}>—</span> : cifra(r.treAnni, 2, true)}</span>
                  <span className={'r dl num' + (forte ? ' is-big' : '')} role="cell" title={forte ? w.deltaBig : w.deltaHint}>{cifra(r.delta, 2, true)}</span>
                </div>
              );
            })}
            <div className="fat-prow is-scale" aria-hidden="true">
              <span />
              <div className="fat-prow-dir">{[-1, -0.5, 0, 0.5, 1].map(k => <span key={k} className="num">{cifra(k * scala, Number.isInteger(k * scala) ? 0 : Number.isInteger(k * scala * 10) ? 1 : 2, true)}</span>)}</div>
              <span /><span /><span />
            </div>
          </div>
          {alfa && (
            <div className="fat-note is-plain">
              <span className="txt"><b>{w.alphaRowTitle}</b> · {p.etichetta1} <b className="num">{pct(alfa.unAnno, 1, true)}</b> · {p.etichetta3} <b className="num">{pct(alfa.treAnni, 1, true)}</b> — {w.alphaRowRest}</span>
            </div>
          )}
        </div>
      </section>

      <section className="bbn-card fat-card fat-cov-card" aria-label={w.covTitle}>
        <header className="bbn-card-head">
          <h2>{w.covTitle}</h2>
          <Info testo={w.covInfo} />
          <span className="bbn-card-note">{w.covNote}</span>
        </header>
        <div className="fat-body">
          <div className="fat-tiles2">
            <div className="fat-tile">
              <span>{w.covInvested}</span>
              <b id="cop-dichiarata" className="num">{pct(p.cop.dichiarata, 2)}</b>
              <small>{p.nAnalizzati === null ? w.na
                : p.fuori.n > 0 ? w.covInvestedSubSkipped(p.nAnalizzati, p.fuori.n) : w.covInvestedSub(p.nAnalizzati)}</small>
            </div>
            <div className="fat-tile">
              <span>{w.covNav}</span>
              <b id="cop-effettiva" className="num fat-acc">{p.cop.effettiva === null
                ? <span className="fat-muted fat-missing">{perche(p.cop.effettivaMuta || p.cop.muto)}</span> : pct(p.cop.effettiva, 2)}</b>
              <small>{w.covNavSub}</small>
            </div>
          </div>
          {quota !== null && (
            <div className="fat-stack" title={`NAV ${euro(p.cop.nav)}`}>
              <i className="s1" style={{ width: p2(Math.max(0, Math.min(100, quota))) }} />
              <i className="s3" style={{ width: p2(Math.max(0, 100 - quota)) }} />
            </div>
          )}
          <dl className="fat-kv">
            <div><dt><i className="s1" />{w.covAnalyzed}</dt><dd className="num">{p.cop.investito === null ? perche(p.cop.muto) : euro(p.cop.investito)}</dd></div>
            <div><dt><i className="s3" />{w.covCash}</dt><dd className="num">{p.cop.cassa === null ? perche(p.cop.muto) : `${euro(p.cop.cassa)} · ${pct(p.cop.cassaPct, 1)}`}</dd></div>
            <div><dt>NAV</dt><dd className="num">{p.cop.nav === null ? perche(p.cop.muto) : euro(p.cop.nav)}</dd></div>
          </dl>
          {p.snapErr !== null && <div className="fat-note is-bad"><span className="txt">{w.portfolioUnavailable(p.snapErr || w.errorMissing)}</span></div>}
          {p.cop.avvisi.map(a => <div key={a} className="fat-note is-warn"><span className="txt">NAV — {a}</span></div>)}
        </div>
      </section>

      <section className="bbn-card fat-card fat-reg-card" aria-label={w.regTitle}>
        <header className="bbn-card-head">
          <h2>{w.regTitle}</h2>
          <Info testo={w.regInfo} />
          {p.reg.length > 0 && <span className="bbn-card-note">{`${p.reg.length === 1 ? w.regDatasetOne : w.regDatasets(p.reg.length)} · ${p.fuori.n === 1 ? w.regSkippedOne : w.regSkipped(p.fuori.n)}`}</span>}
        </header>
        <div className="fat-body">
          <div className="fat-reg">
            {p.reg.map(r => (
              <div key={r.chiave} className="fat-rrow">
                <div>
                  <b>{r.etichetta}</b>
                  <small>{w.regRowSub(r.nHolding === null ? w.na : cifra(r.nHolding, 0), r.nObs === null ? w.na : cifra(r.nObs, 0), r.ultimaData ? dataIt(r.ultimaData) : w.na)}</small>
                  {r.errore && <small className="fat-bad">{r.errore}</small>}
                </div>
                {/* peso assente = n.d. dichiarato, non una barra larga zero che si legge come misura */}
                {r.peso === null ? (
                  <>
                    <div className="fat-wbar is-empty" role="img" aria-label={w.regWeightMissing} title={w.regWeightMissing} />
                    <span className="r num fat-muted" title={w.regWeightMissing}>{w.na}</span>
                  </>
                ) : (
                  <>
                    <div className="fat-wbar"><i style={{ width: p2(Math.max(0, Math.min(100, r.peso))) }} /></div>
                    <span className="r num">{pct(r.peso, 1)}</span>
                  </>
                )}
              </div>
            ))}
          </div>
          <div className="fat-tiles2">
            <div className="fat-tile">
              <span>{w.lastData}</span>
              <b className="num">{p.ultimoDato ? dataIt(p.ultimoDato) : w.na}</b>
              <small>{p.ritardo === null ? w.delayMissing : w.lastDataSub(p.ritardo)}</small>
            </div>
            <div className="fat-tile">
              <span>{w.obsRange}</span>
              <b className="num">{p.storico ? (p.storico[0] === p.storico[1] ? cifra(p.storico[0], 0) : `${cifra(p.storico[0], 0)}–${cifra(p.storico[1], 0)}`) : w.na}</b>
              <small>{w.obsRangeSub}</small>
            </div>
          </div>
          {p.fuori.n === 0 ? (
            <div className="fat-note is-plain"><span className="txt"><b>{w.noSkipped}</b> {w.noSkippedSub}</span></div>
          ) : (
            <div className="fat-note is-warn fat-skipped">
              <span className="txt">
                <b>{p.fuori.n === 1 ? w.skippedTitleOne : w.skippedTitle(p.fuori.n)}</b>
                {p.fuori.righe.length === 0 && <> — {w.skippedNoDetail}</>}
                {p.fuori.righe.map(s => <span key={s.ticker} className="fat-skip-row"><b>{s.ticker}</b> · {pct(s.peso, 1)} · {s.motivo}</span>)}
              </span>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
