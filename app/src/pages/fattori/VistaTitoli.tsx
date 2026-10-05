import IconaTitolo from '@/components/nuova/IconaTitolo';
import { Segmenti } from '@/components/nuova/Card';
import { FATTORI, NOME_FATTORE } from '@/lib/fattori';
import type { Intervallo } from '@/lib/fattori';
import { CHIAVE_FATTORE, cifra, pct, tinta } from './calcoli';
import type { Ordine, RigaTitolo } from './calcoli';
import { Baffo, Info } from './pezzi';
import type { Parole } from './parole';

/* Vista «Titoli»: mappa titoli × fattori (colore solo se il coefficiente è significativo), alpha
   con intervallo al 95%, e a destra il dettaglio del titolo scelto. Sola presentazione. */

export interface PropsTitoli {
  w: Parole;
  righe: RigaTitolo[];
  ordine: Ordine;
  onOrdine: (o: Ordine) => void;
  scelto: string | null;
  onScelto: (ticker: string) => void;
  limAlfa: number;
  celleSig: number;
  celle: number;
  alfaSig: number;
  /** nome leggibile della regione (dataset K. French) per chiave del backend */
  regioni: Map<string, string>;
}

function Cella({ ic, w }: { ic: Intervallo | null; w: Parole }) {
  if (!ic) return <span className="fat-cell is-ns">—</span>;
  const t = tinta(ic);
  const titolo = w.cellTitle(cifra(ic.beta, 2), cifra(ic.t, 1)) + (ic.sig ? '' : ' · ' + w.cellNs);
  if (!t) return <span className="fat-cell is-ns num" title={titolo}>{cifra(ic.beta, 2)}</span>;
  return (
    <span className={'fat-cell num' + (t.alfa > 0.5 ? ' is-strong' : '')} title={titolo}
      style={{ background: `rgba(var(${t.segno > 0 ? '--fat-heat-up' : '--fat-heat-down'}), ${t.alfa.toFixed(3)})` }}>
      {cifra(ic.beta, 2)}
    </span>
  );
}

export default function VistaTitoli(p: PropsTitoli) {
  const { w, righe } = p;
  const sel = righe.find(r => r.ticker === p.scelto) || righe[0] || null;
  const sigAlfa = (r: RigaTitolo) => !!(r.ic && r.ic.sig);
  return (
    <div className="fat-view fat-titoli">
      <section className="bbn-card fat-card fat-hm-card" aria-label={w.hmTitle}>
        <header className="bbn-card-head">
          <h2>{w.hmTitle}</h2>
          <Info testo={w.hmInfo} />
          {righe.length > 0 && <span className="bbn-card-note">{w.hmNote(p.celleSig, p.celle, p.alfaSig, righe.length)}</span>}
          <span className="bbn-grow" />
          <Segmenti<Ordine> etichetta={w.sortLabel} valore={p.ordine} onChange={p.onOrdine}
            opzioni={[{ id: 'peso', testo: w.sortWeight }, { id: 'alpha', testo: w.sortAlpha }]} />
        </header>
        <div className="fat-body">
          <div className="fat-tabwrap">
            <table id="tab-alpha" className="fat-hm">
              <thead><tr>
                <th className="l">{w.colSecurity}</th>
                <th className="r">{w.colWeight}</th>
                {FATTORI.map(f => <th key={f} title={`${NOME_FATTORE[f].lungo}`}>{CHIAVE_FATTORE[f]}</th>)}
                <th>{w.colAlphaCi}</th>
                <th className="r">{w.colAlpha}</th>
                <th className="r" title={w.colR2Hint}>R²</th>
              </tr></thead>
              <tbody>
                {righe.map(r => (
                  <tr key={r.ticker} data-ticker={r.ticker} tabIndex={0} aria-selected={sel?.ticker === r.ticker}
                    className={sigAlfa(r) ? undefined : 'ns'}
                    onClick={() => p.onScelto(r.ticker)}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); p.onScelto(r.ticker); } }}>
                    <td>
                      <div className="fat-tt">
                        <IconaTitolo ticker={r.ticker} nome={r.nome} dimensione="sm" />
                        <div>
                          <b>{r.nome || r.ticker}</b>
                          <small>{r.ticker}{r.fxCaveat && <span className="fat-fx" title={r.fxCaveat}>FX</span>}</small>
                        </div>
                      </div>
                    </td>
                    <td className="r num">{pct(r.peso, 1)}</td>
                    {r.celle.map((ic, j) => <td key={FATTORI[j]} className="hc"><Cella ic={ic} w={w} /></td>)}
                    <td className="fat-whisk-cell"><Baffo ic={r.ic} limite={p.limAlfa} nome={w.colAlpha} unita="%" w={w} /></td>
                    <td className={'r num ' + (sigAlfa(r) ? (r.alpha !== null && r.alpha < 0 ? 'fat-bad' : 'fat-good') : 'fat-muted')}>{pct(r.alpha, 1, true)}</td>
                    <td className="r num fat-muted">{r.r2 === null ? '—' : pct(r.r2 * 100, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {righe.length === 0 && <p className="bbn-empty">{w.hmEmpty}</p>}
          </div>
          <div className="fat-hm-key">
            <span>{w.keyNeg}</span><span className="fat-grad" /><span>{w.keyPos}</span>
            <span><span className="fat-ns-key" />{w.keyNs}</span>
            <span>{w.keyAlpha}</span>
          </div>
        </div>
      </section>

      <section className="bbn-card fat-card fat-det" aria-label={w.detTitle}>
        <header className="bbn-card-head">
          <h2>{w.detTitle}</h2>
          <span className="bbn-grow" />
          {sel?.regione && <span className="fat-pill is-flat">{w.detDataset(p.regioni.get(sel.regione) || sel.regione)}</span>}
          {sel?.fxCaveat && <span className="fat-pill is-warn" title={sel.fxCaveat}>{w.detFx}</span>}
        </header>
        {!sel ? <p className="bbn-empty">{w.hmEmpty}</p> : (
          <div className="fat-body">
            <div className="fat-det-h">
              <IconaTitolo ticker={sel.ticker} nome={sel.nome} dimensione="md" />
              <div><b>{sel.nome || sel.ticker}</b><small>{sel.ticker} · {w.detWeight(pct(sel.peso, 1))}</small></div>
            </div>
            <div className="fat-tiles3">
              <div className="fat-tile">
                <span>{w.detAlpha} {sel.ic && sel.ic.lo !== null && sel.ic.hi !== null && <Info testo={w.detCi(pct(sel.ic.lo, 1, true), pct(sel.ic.hi, 1, true))} />}</span>
                <b className={'num ' + (sigAlfa(sel) ? (sel.alpha !== null && sel.alpha < 0 ? 'fat-bad' : 'fat-good') : '')}>{pct(sel.alpha, 1, true)}</b>
                <small>{sigAlfa(sel) ? w.detSig : w.detNs}</small>
              </div>
              <div className="fat-tile"><span>R²</span><b className="num">{sel.r2 === null ? '—' : pct(sel.r2 * 100, 0)}</b><small>{w.detR2Sub}</small></div>
              <div className="fat-tile"><span>{w.detObs}</span><b className="num">{cifra(sel.nObs, 0)}</b><small>{w.detObsSub}</small></div>
            </div>
            <div className="fat-fp">
              <div className="fat-fprow is-head"><span>{w.detProfile}</span><span className="fat-fp-scale"><span>−2</span><span>0</span><span>+2</span></span><span /></div>
              {FATTORI.map((f, j) => {
                const ic = sel.celle[j];
                return (
                  <div key={f} className="fat-fprow">
                    <span>{NOME_FATTORE[f].lungo}<small>{CHIAVE_FATTORE[f]}{ic && ic.t !== null ? ` · t ${cifra(ic.t, 1)}` : ''}</small></span>
                    <Baffo ic={ic} limite={2} nome={NOME_FATTORE[f].lungo} w={w} />
                    <b className={'num' + (ic && ic.sig ? '' : ' fat-muted')}>{ic ? cifra(ic.beta, 2, true) : '—'}</b>
                  </div>
                );
              })}
            </div>
            {sel.fxCaveat && <div className="fat-note is-warn"><span className="txt">{sel.fxCaveat}</span></div>}
          </div>
        )}
      </section>
    </div>
  );
}
