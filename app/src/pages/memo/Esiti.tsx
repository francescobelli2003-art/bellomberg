import { ChevronDown, Package, Target } from 'lucide-react';
import type { Decision, Memo } from '@/lib/api';
import { fmtEUR, fmtNum } from '@/lib/format';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { BarraEsiti } from './ElencoRun';
import { ORD, conta, eseguite, nomeFile, segnalato, type Esito, type Riga } from './logica';
import { parole } from './parole';

const TONO: Record<Esito, string> = { EXECUTED: 'su', PARTIAL: 'su', PENDING: 'warn', SKIPPED: 'piatto', EXPIRED: 'giu' };

/** Esiti delle decisioni: riepilogo «X su Y eseguite» e una riga per ogni voce della action table. */
export function Esiti({ sel, md, testoErr, azioni, decSel, decErr, decLoading, aperta, onApri }: {
  sel: Memo | null; md: string | undefined; testoErr: string | null; azioni: Riga[];
  decSel: Decision[]; decErr: string | null; decLoading: boolean;
  aperta: number | null; onApri: (i: number | null) => void;
}) {
  const w = parole(), c = conta(decSel);
  const nonAgganciate = azioni.filter(a => !a.dec).length;
  let riepilogo;
  if (!sel) riepilogo = <p className="mm-empty">—</p>;
  else if (decErr !== null) riepilogo = <p className="mm-note is-bad" role="alert">{w.decErr(decErr || w.unknown)}</p>;
  else if (decLoading) riepilogo = <p className="mm-empty" role="status">…</p>;
  else if (decSel.length === 0) riepilogo = <p className="mm-empty" data-qa="memo-no-decisions">{w.noDecisions}</p>;
  else riepilogo = (
    <div className="mm-sum" data-qa="memo-outcome">
      <div className="mm-big"><b className="num">{eseguite(c)}</b><span>{w.executedOf(decSel.length)}</span></div>
      <BarraEsiti ds={decSel} grande />
      <div className="mm-leg">
        {ORD.map(s => c[s] ? <span key={s} className={`bbn-pill mm-pill is-${TONO[s]}`}><i className={`mm-dot mm-e-${s}`} />{w.legenda[s]} <b className="num">{c[s]}</b></span> : null)}
        {nonAgganciate > 0 && <span className="bbn-pill mm-pill is-vuoto">{w.unlinkedLeg} <b className="num">{nonAgganciate}</b></span>}
      </div>
    </div>
  );

  return (
    <section className="bbn-card mm-esiti" aria-labelledby="mm-esiti-t">
      <header className="mm-card-head">
        <span className="mm-ci" aria-hidden="true"><Target size={16} /></span>
        <h2 id="mm-esiti-t">{w.outcomes}</h2>
        <span className="mm-card-note" title={w.link}>{azioni.length ? w.outcomesRows(azioni.length) : w.outcomesNote}</span>
      </header>
      <div className="mm-card-body is-fixed">{riepilogo}</div>
      <div className="mm-rows" data-qa="memo-actions">
        {sel && md !== undefined && testoErr === null && azioni.length === 0 && md !== '' && <p className="mm-empty">{w.noActionTable}</p>}
        {azioni.map((a, i) => {
          const on = aperta === i, s = a.dec?.status as Esito | undefined;
          const noto = s && (ORD as readonly string[]).includes(s);
          return (
            <div key={i} className={'mm-arow' + (on ? ' is-open' : '') + (s === 'EXPIRED' ? ' is-evap' : '')} data-action-row={i}>
              <button type="button" className="mm-arow-btn" aria-expanded={on} onClick={() => onApri(on ? null : i)}>
                <IconaTitolo ticker={a.tick} dimensione="sm" />
                <span className="mm-arow-tx">
                  <b><span className="mm-tick">{a.tick}</span><span className={`mm-act is-${a.act.toUpperCase()}`}>{a.act}</span></b>
                  <span className="mm-why">{a.timing}</span>
                </span>
                {/* mai un esito dedotto: se il join non tiene, si scrive */}
                {a.dec
                  ? <span className={`bbn-pill mm-pill is-${noto ? TONO[s!] : 'piatto'}`} data-esito={a.dec.status}>{noto ? w.esito[s!] : a.dec.status}</span>
                  : <span className="bbn-pill mm-pill is-vuoto" data-esito="NONE">{w.unlinked}</span>}
                <ChevronDown size={14} className="mm-chev" aria-hidden="true" />
              </button>
              {on && (
                <div className="mm-aexp">
                  <p>{a.timing}</p>
                  <dl>
                    <div><dt>{w.amount}</dt><dd className="num">{a.eur || w.nd}</dd></div>
                    <div><dt>{w.conviction}</dt><dd>{a.conf || w.nd}</dd></div>
                  </dl>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** Corredo del memo: importi e metadati salvati con la run, fonti citate. */
export function Corredo({ sel, md, dec, decErr, decLoading, conEsito, selDcf, fonti, tutteFonti, onTutteFonti }: {
  sel: Memo | null; md: string | undefined; dec: Decision[]; decErr: string | null; decLoading: boolean; conEsito: number;
  selDcf: string[] | null; fonti: { fonte: string; n: number }[]; tutteFonti: boolean; onTutteFonti: () => void;
}) {
  const w = parole();
  if (!sel) return null;
  const nav = typeof sel.portfolio_nav_eur === 'number' && Number.isFinite(sel.portfolio_nav_eur) ? sel.portfolio_nav_eur : null;
  const tok = Number.isFinite(sel.capo_tokens_in) && Number.isFinite(sel.capo_tokens_out);
  const flag = selDcf ? selDcf.filter(segnalato).length : 0;
  const visibili = tutteFonti ? fonti : fonti.slice(0, 5);
  const max = fonti[0]?.n || 1;
  return (
    <section className="bbn-card mm-kit" aria-labelledby="mm-kit-t">
      <header className="mm-card-head">
        <span className="mm-ci" aria-hidden="true"><Package size={16} /></span>
        <h2 id="mm-kit-t">{w.kit}</h2><span className="mm-card-note">{w.kitNote(sel.id)}</span>
      </header>
      <div className="mm-card-body">
        <div className="mm-tiles" data-qa="memo-kit">
          <div className="mm-tile" title={w.securitiesBasis}>
            <span className="k">{w.investedAt}</span>
            <span className="v num">{nav !== null ? fmtEUR(nav, false, 0) : w.nd}</span>
            <span className="s">{nav !== null ? w.investedSub : w.investedNd}</span>
          </div>
          {/* un esito non registrato resta dichiarato come mancante, mai reso come zero */}
          <div className="mm-tile" title={w.marketHint}>
            <span className="k">{w.market}</span>
            <span className={'v num' + (conEsito === 0 ? ' is-nd' : '')}>{decErr !== null || decLoading || conEsito === 0 ? w.nd : fmtNum(conEsito, 0)}</span>
            <span className="s">{decErr !== null ? w.decisionsNd : decLoading ? '…' : w.marketSub(conEsito, dec.length)}</span>
          </div>
          <div className="mm-tile">
            <span className="k">{w.tokens}</span>
            <span className={'v num' + (tok ? '' : ' is-nd')}>{tok ? fmtNum(sel.capo_tokens_out, 0) : w.nd}</span>
            <span className="s">{tok ? w.tokensSub(fmtNum(sel.capo_tokens_in, 0), fmtNum(sel.capo_tokens_out, 0)) : w.tokensNd}</span>
          </div>
          <div className="mm-tile">
            <span className="k">{w.dcf}</span>
            <span className={'v num' + (selDcf === null ? ' is-nd' : '')}>{selDcf === null ? w.nd : selDcf.length}</span>
            <span className={'s' + (selDcf === null ? ' is-bad' : '')}>{selDcf === null ? w.dcfInvalid
              : selDcf.length === 0 ? w.dcfNone : flag ? w.dcfFlags(flag) : w.dcfNoFlags}</span>
          </div>
        </div>
        {selDcf && selDcf.length > 0 && (
          <div className="mm-files">
            {selDcf.map(f => <span key={f} className={'bbn-chip' + (segnalato(f) ? ' is-flag' : '')} title={f}>{nomeFile(f)}{segnalato(f) ? ` · ${w.dcfFlag}` : ''}</span>)}
          </div>
        )}

        <div className="mm-sec-h">
          <h3>{w.sources}</h3>
          {fonti.length > 0 && <span className="mm-card-note">{w.sourcesNote(fonti.reduce((s, f) => s + f.n, 0), fonti.length)}</span>}
          <span className="bbn-grow" />
          {fonti.length > 5 && <button type="button" className="bbn-link" aria-expanded={tutteFonti} onClick={onTutteFonti}>{tutteFonti ? w.sourcesTop : w.sourcesAll}</button>}
        </div>
        {md === undefined ? <p className="mm-mini">—</p>
          : fonti.length === 0 ? <p className="mm-mini">{w.sourcesNone}</p>
          : (
            <div className={'mm-fonti' + (tutteFonti ? ' is-all' : '')} data-qa="memo-sources">
              {visibili.map(f => (
                <div className="mm-frow" key={f.fonte}>
                  <span className="nm" title={f.fonte}>{f.fonte}</span>
                  <span className="bb"><i style={{ width: `${Math.max(6, Math.round(f.n / max * 100))}%` }} /></span>
                  <span className="ct num">{f.n}</span>
                </div>
              ))}
            </div>
          )}
      </div>
    </section>
  );
}
