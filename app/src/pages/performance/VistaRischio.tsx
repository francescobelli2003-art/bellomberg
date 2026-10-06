import { useState } from 'react';
import type { ConcentrationResult, LiquidityResult, MonteCarloResult, PortfolioRisk, TearsheetPayload, VarContributionResult } from '@/lib/api';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import { giudicaRischio } from '@/lib/rischio-livello';
import { istogramma, sintesiDistribuzione } from './calcoli';
import type { PnlGiornaliero } from './calcoli';
import { VUOTO, dataBreve, euro, num, pct } from './formato';
import { parole } from './parole';
import type { IdScenario } from './parole';
import { Info, StatoCard } from './VistaScheda';
import type { Stato } from './VistaScheda';

const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
export const SCENARI: IdScenario[] = ['none', 'shock_3sigma', 'gfc_2008', 'covid_2020'];

export interface PropsRischio {
  rischio: Stato<PortfolioRisk>;
  /** P&L giornaliero in euro al netto dei flussi (pnlGiornalieri sul payload TWR) */
  pnl: Stato<PnlGiornaliero>;
  contributi: Stato<VarContributionResult>;
  concentrazione: Stato<ConcentrationResult>;
  drawdown: Stato<NonNullable<TearsheetPayload['drawdowns']>>;
  /** /portfolio/analytics/liquidity: giorni per liquidare e semaforo per posizione */
  liquidita: Stato<LiquidityResult>;
  scenari: Record<IdScenario, Stato<MonteCarloResult>>;
  onRiprovaScenario: (id: IdScenario) => void;
  nomi: Record<string, string>;
}

function PerditaPotenziale({ rischio }: { rischio: Stato<PortfolioRisk> }) {
  const w = parole();
  return (
    <section className="bbn-card perf-rhero">
      <StatoCard s={rischio}>{r => {
        const p = r.portfolio, b = r.benchmark || null;
        const g = giudicaRischio(p.vol_annual_pct, b?.vol_annual_pct);
        return (
          <>
            <div className="perf-hero-label">{w.potentialLoss} <Info testo={w.potentialLossInfo(r.lookback_days)} /></div>
            <div className="perf-hero-value num">{euro(finito(p.var_95_1d_eur) ? Math.abs(p.var_95_1d_eur) : null, 0)}</div>
            <div className="perf-hero-row">
              <span className="bbn-pill is-giu">{w.ofNav(pct(finito(p.var_95_1d_pct) ? Math.abs(p.var_95_1d_pct) : null, 2, false))}</span>
              <span className="bbn-pill is-piatto">{w.varHist}</span>
            </div>
            <div className="perf-tiles is-two">
              <div className="perf-tile"><span>{w.var99} <Info testo={w.var99Info} /></span>
                <b className="down-t">{euro(finito(p.var_99_1d_eur) ? Math.abs(p.var_99_1d_eur) : null, 0)}</b>
                <small>{w.ofNav(pct(finito(p.var_99_1d_pct) ? Math.abs(p.var_99_1d_pct) : null, 2, false))}</small></div>
              <div className="perf-tile"><span>{w.relVol}</span>
                <b>{g.rapporto != null ? num(g.rapporto, 2) + '× SPY' : VUOTO}</b>
                <small>{w.relVolSub(pct(p.vol_annual_pct, 2, false))}</small></div>
            </div>
          </>
        );
      }}</StatoCard>
    </section>
  );
}

function Distribuzione({ pnl, rischio }: { pnl: Stato<PnlGiornaliero>; rischio: Stato<PortfolioRisk> }) {
  const w = parole();
  const r = rischio.stato === 'ok' ? rischio.dati.portfolio : null;
  const v95 = r && finito(r.var_95_1d_eur) ? Math.abs(r.var_95_1d_eur) : null;
  const v99 = r && finito(r.var_99_1d_eur) ? Math.abs(r.var_99_1d_eur) : null;
  return (
    <section className="bbn-card perf-hist">
      <header className="bbn-card-head"><h2>{w.distribution}</h2><Info testo={w.distributionInfo} /><span className="bbn-grow" />
        <span className="perf-legend"><span><i style={{ background: 'var(--bbn-bad)' }} />{w.negSessions}</span>
          <span><i style={{ background: 'var(--bbn-good)' }} />{w.posSessions}</span></span></header>
      <StatoCard s={pnl}>{({ sedute, senzaFlusso }) => {
        const esclusi = senzaFlusso.length > 0
          ? <p className="perf-why" role="status">{w.distExcludedNoFlow(senzaFlusso.length, senzaFlusso.slice(0, 10).map(d => dataBreve(d)).join(', ') + (senzaFlusso.length > 10 ? ', …' : ''))}</p> : null;
        if (sedute.length < 2) return <><p className="perf-state">{w.unavailable}</p>{esclusi}</>;
        const { classi, passo } = istogramma(sedute.map(s => s.eur));
        const s = sintesiDistribuzione(sedute, v95);
        const lo = classi[0].da, hi = classi[classi.length - 1].a;
        const W = 1000, H = 300, top = 34;
        const max = Math.max(...classi.map(c => c.n));
        const x = (v: number) => (v - lo) / (hi - lo) * W;
        const y = (n: number) => H - n / max * (H - top);
        const bw = W / classi.length;
        // linee VaR nell'SVG (si allunga in larghezza), etichette in HTML: il testo non si deforma
        const soglie = ([[v95, 'VaR 95%'], [v99, 'VaR 99%']] as Array<[number | null, string]>)
          .filter((t): t is [number, string] => t[0] != null && -t[0] > lo && -t[0] < hi)
          .map(([v, nome], k) => ({ v: -v, testo: `${nome} ${euro(-v, 0)}`, riga: k }));
        const tacche = [0, 1, 2, 3, 4].map(t => Math.round((lo + (hi - lo) * t / 4) / passo) * passo);
        return (
          <>
            <p className="perf-why">{s.oltreVar == null
              ? w.distNoVar + ' ' + w.distSummaryNoVar(s.sedute, dataBreve(s.peggiore?.data), euro(s.peggiore?.eur, 0), euro(s.migliore?.eur, 0, true))
              : w.distSummary(s.sedute, s.oltreVar, s.attese, dataBreve(s.peggiore?.data), euro(s.peggiore?.eur, 0), euro(s.migliore?.eur, 0, true))}</p>
            {esclusi}
            <div className="perf-hist-chart">
              {soglie.map(t => (
                <span key={t.testo} className="perf-var-tag" style={{ left: `${x(t.v) / W * 100}%`, top: `${t.riga * 18}px` }}>{t.testo}</span>
              ))}
              <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={w.distribution}>
                {classi.map((c, i) => {
                  const negativa = c.a <= 0;
                  const oltre = v95 != null && c.a <= -v95;
                  return (
                    <rect key={i} x={i * bw + 1.5} y={y(c.n)} width={Math.max(1, bw - 3)} height={H - y(c.n)} rx="4"
                      fill={negativa ? 'var(--bbn-bad)' : 'var(--bbn-good)'} opacity={negativa && !oltre ? 0.55 : oltre ? 1 : 0.6}>
                      <title>{w.binTitle(c.n, euro(c.da, 0), euro(c.a, 0))}</title>
                    </rect>
                  );
                })}
                {soglie.map(t => (
                  <line key={t.testo} x1={x(t.v)} x2={x(t.v)} y1={0} y2={H} stroke="var(--bbn-text)" strokeWidth="1.5" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
                ))}
                <line x1="0" x2={W} y1={H} y2={H} stroke="var(--bbn-line)" vectorEffect="non-scaling-stroke" />
              </svg>
            </div>
            <div className="perf-axis">{tacche.map((t, i) => <span key={i}>{euro(t, 0, true)}</span>)}</div>
          </>
        );
      }}</StatoCard>
    </section>
  );
}

function ContributoRischio({ contributi, concentrazione, nomi }: Pick<PropsRischio, 'contributi' | 'concentrazione' | 'nomi'>) {
  const w = parole();
  const [tutti, setTutti] = useState(false);
  return (
    <section className="bbn-card perf-contrib">
      <header className="bbn-card-head"><h2>{w.contribution}</h2><Info testo={w.contributionInfo} /></header>
      <StatoCard s={contributi}>{c => {
        const righe = [...c.items].filter(i => finito(i.contribution_pct_of_total_var))
          .sort((a, b) => b.contribution_pct_of_total_var - a.contribution_pct_of_total_var);
        const mostrate = tutti ? righe : righe.slice(0, 5);
        const resto = righe.slice(5).reduce((s, i) => s + i.contribution_pct_of_total_var, 0);
        const scala = Math.max(45, ...righe.map(i => Math.abs(i.contribution_pct_of_total_var)));
        return (
          <div className="perf-rows">
            <div className="perf-vrow is-head"><span /><span>{w.colTitle}</span><span>{w.colVarShare}</span><span className="is-r">{w.colRisk}</span><span className="is-r">{w.colWeight}</span></div>
            {mostrate.map(i => {
              const quota = i.contribution_pct_of_total_var, nome = nomi[i.ticker] || i.ticker;
              const nota = quota < 0 ? <span className="up-t"> · {w.diversifying}</span>
                : quota > i.weight_pct * 1.5 ? <span className="down-t"> · {w.aboveWeight}</span> : null;
              return (
                <div key={i.ticker} className="perf-vrow" title={`VaR ${euro(i.component_var_eur)}`}>
                  <IconaTitolo ticker={i.ticker} nome={nome} dimensione="sm" />
                  <span className="perf-nm"><b>{nome}</b><span>{i.ticker}{nota}</span></span>
                  <span className="perf-rbar" aria-hidden="true"><i className={quota < 0 ? 'is-neg' : undefined} style={{ width: Math.max(1, Math.abs(quota)) / scala * 100 + '%' }} /></span>
                  <span className={'perf-val ' + (quota < 0 ? 'up-t' : 'down-t')}>{pct(quota, 1, false)}</span>
                  <span className="perf-wt">{pct(i.weight_pct, 1, false)}</span>
                </div>
              );
            })}
            {righe.length > 5 && (
              <button type="button" className="perf-more" onClick={() => setTutti(v => !v)}>
                {tutti ? w.onlyTop : w.others(righe.length - 5, pct(resto, 1, false))}</button>
            )}
          </div>
        );
      }}</StatoCard>
      {concentrazione.stato === 'ok' && !concentrazione.dati.error && (() => {
        const k = concentrazione.dati;
        const reg = Object.entries(k.by_region?.weights_pct || {}).sort((a, b) => b[1] - a[1])[0];
        const val = Object.entries(k.by_currency?.weights_pct || {}).sort((a, b) => b[1] - a[1])[0];
        const cl = (c: string) => w.classification[c] || c;
        return (
          <div className="perf-conc">
            <span><b>{w.effN(num(k.by_ticker?.effective_n, 1))}</b><small>{w.hhi(cl(k.by_ticker?.classification), num(k.by_ticker?.hhi, 0))}</small></span>
            {reg && <span><b>{(w.regionNames[reg[0]] || reg[0]) + ' ' + pct(reg[1], 1, false)}</b><small>{w.regionSub(cl(k.by_region.classification))}</small></span>}
            {val && <span><b>{val[0] + ' ' + pct(val[1], 0, false)}</b><small>{val[1] >= 99.95 ? w.currencySub : w.currencyMixed}</small></span>}
          </div>
        );
      })()}
      {concentrazione.stato === 'errore' && <p className="perf-state is-error"><b>{w.unavailable}</b> · {concentrazione.testo}</p>}
    </section>
  );
}

function ConfrontoMercato({ rischio }: { rischio: Stato<PortfolioRisk> }) {
  const w = parole();
  const r = rischio.stato === 'ok' ? rischio.dati : null;
  const g = r ? giudicaRischio(r.portfolio.vol_annual_pct, r.benchmark?.vol_annual_pct) : null;
  const tono = g?.livello === 'alto' ? 'is-giu' : g?.livello === 'medio' ? 'is-piatto' : 'is-su';
  return (
    <section className="bbn-card perf-spyc">
      <header className="bbn-card-head"><h2>{w.market}</h2>{r && <Info testo={w.marketInfo(r.lookback_days)} />}<span className="bbn-grow" />
        {g?.livello && <span className={'bbn-pill ' + tono}>{w.riskLevel[g.livello]}</span>}</header>
      <StatoCard s={rischio}>{d => {
        const p = d.portfolio, b = d.benchmark || null;
        const rapporto = (a: number | null | undefined, c: number | null | undefined) => finito(a) && finito(c) && c !== 0 ? Math.abs(a) / Math.abs(c) : null;
        const specifico = finito(p.beta_vs_spy) && g?.rapporto != null && p.beta_vs_spy < g.rapporto && p.beta_vs_spy < 1;
        const righe: Array<[string, string, number | null, string]> = [
          [w.cVol, w.cVolSub(pct(p.vol_annual_pct, 2, false), pct(b?.vol_annual_pct, 2, false)), rapporto(p.vol_annual_pct, b?.vol_annual_pct), w.mVolInfo],
          [w.cVar, w.cVarSub(pct(p.var_95_1d_pct), pct(b?.var_95_1d_pct)), rapporto(p.var_95_1d_pct, b?.var_95_1d_pct), w.potentialLossInfo(d.lookback_days)],
          [w.cDd, w.cDdSub(pct(p.max_dd_1y_pct), pct(b?.max_dd_1y_pct)), rapporto(p.max_dd_1y_pct, b?.max_dd_1y_pct), w.maxDdInfo],
          [w.cBeta, specifico ? w.betaSpecific(num(p.beta_vs_spy, 2)) : w.betaMarket(num(p.beta_vs_spy, 2)), finito(p.beta_vs_spy) ? p.beta_vs_spy : null, w.mBetaInfo],
          [w.cSharpe, w.cSharpeSub(num(p.sharpe, 2), num(b?.sharpe, 2)), rapporto(p.sharpe, b?.sharpe), w.sharpeInfo],
        ];
        return (
          <div className="perf-cmp">
            {righe.map(([t, sub, rr, info]) => (
              <div key={t} className="perf-crow" title={info}>
                <div><b>{t}</b><span>{sub}</span></div>
                {b && rr != null ? <span className="perf-rel" aria-hidden="true"><i style={{ width: Math.min(100, rr / 3 * 100) + '%' }} /><em /></span> : <span />}
                <small>{b && rr != null ? num(rr, 1) + '×' : ''}</small>
              </div>
            ))}
            {b && <div className="perf-cmp-key"><em />{w.levelSpy}</div>}
          </div>
        );
      }}</StatoCard>
    </section>
  );
}

function Drawdown({ drawdown }: { drawdown: Stato<NonNullable<TearsheetPayload['drawdowns']>> }) {
  const w = parole();
  return (
    <section className="bbn-card perf-ddc">
      <header className="bbn-card-head"><h2>{w.drawdown}</h2><Info testo={w.drawdownInfo} /></header>
      <StatoCard s={drawdown}>{d => {
        const peggiore = d.top.length ? Math.min(...d.top.map(e => e.depth_pct)) : null;
        return (
          <>
            <div className="perf-dd-now">
              {d.current ? <><b>{pct(d.current.current_dd_pct, 2, false)}</b><small>{w.ddNow(dataBreve(d.current.start_date), pct(peggiore, 2, false))}</small></>
                : <small>{w.ddNone}</small>}
            </div>
            {d.top.length > 0 && (
              <table className="perf-ep" aria-label={w.ddTop}>
                <thead><tr><th>{w.ddPeak}</th><th>{w.ddTrough}</th><th>{w.ddDepth}</th><th>{w.ddRecovery}</th></tr></thead>
                <tbody>{d.top.slice(0, 5).map(e => {
                  const aperto = !e.recovery_date;
                  const oggi = aperto && d.current && d.current.start_date === e.start_date ? d.current.current_dd_pct : null;
                  return (
                    <tr key={e.start_date}>
                      <td>{dataBreve(e.start_date)}</td>
                      <td>{dataBreve(e.trough_date)}</td>
                      <td className="down-t">{pct(e.depth_pct, 2, false)}</td>
                      <td className={aperto ? 'down-t' : 'up-t'}>{aperto
                        ? (oggi != null ? w.ddOpenToday(pct(oggi, 2, false)) : w.ddOpen)
                        : finito(e.days_total) ? w.ddRecovered(w.ddDays(e.days_total - e.days_to_trough), dataBreve(e.recovery_date)) : dataBreve(e.recovery_date)}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            )}
          </>
        );
      }}</StatoCard>
    </section>
  );
}

/** Liquidabilità per posizione (capacità della pagina Performance fino a e44e955, ripristinata):
 *  giorni per liquidare a una quota del volume medio e semaforo. Le righe non misurate restano
 *  visibili col motivo del servizio, mai un giorno inventato. */
function Liquidita({ liquidita }: { liquidita: Stato<LiquidityResult> }) {
  const w = parole();
  // contatori assenti = n.d. (mai «undefined verde»); volume: assente, o 0 su una riga che il servizio
  // stesso dichiara non misurata (score unknown/error/skip), è n.d. — uno 0 su una riga misurata resta 0
  const conta = (n: unknown) => (typeof n === 'number' && Number.isFinite(n) ? String(n) : w.liqNd);
  const volumeMisurato = (it: LiquidityResult['items'][number]) => finito(it.avg_daily_volume_eur)
    && (it.avg_daily_volume_eur > 0 || it.score === 'green' || it.score === 'yellow' || it.score === 'red');
  return (
    <section className="bbn-card perf-liq" data-qa="perf-liquidity">
      <header className="bbn-card-head"><h2>{w.liquidity}</h2>
        {liquidita.stato === 'ok' && <Info testo={w.liquidityInfo(pct(liquidita.dati.assumption_pct_of_volume * 100, 0, false),
          num(liquidita.dati.threshold_green_days, 0), num(liquidita.dati.threshold_yellow_days, 0))} />}
        <span className="bbn-grow" />
        {liquidita.stato === 'ok' && <span className="perf-legend num">
          <span><i style={{ background: 'var(--bbn-good)' }} />{conta(liquidita.dati.n_green)} {w.liqScore.green}</span>
          <span><i style={{ background: 'var(--bbn-warn)' }} />{conta(liquidita.dati.n_yellow)} {w.liqScore.yellow}</span>
          <span><i style={{ background: 'var(--bbn-bad)' }} />{conta(liquidita.dati.n_red)} {w.liqScore.red}</span>
        </span>}
      </header>
      <StatoCard s={liquidita}>{l => (
        <>
          {l.items.length === 0 ? <p className="perf-state" role="status">{w.liqNoRows}</p> : (
            <table className="perf-ep" aria-label={w.liquidity}>
              <thead><tr><th>{w.colTitle}</th><th>{w.liqPosition}</th><th>{w.liqAdv}</th><th>{w.liqDays}</th><th>{w.liqScoreCol}</th></tr></thead>
              <tbody>{l.items.map(it => {
                const tono = it.score === 'green' ? 'up-t' : it.score === 'red' ? 'down-t' : it.score === 'yellow' ? 'perf-liq-warn' : 'perf-liq-nd';
                return (
                  <tr key={it.ticker}>
                    <td>{it.ticker}</td>
                    <td>{euro(it.position_eur, 0)}</td>
                    <td>{volumeMisurato(it) ? euro(it.avg_daily_volume_eur, 0) : w.liqNd}</td>
                    <td>{finito(it.days_to_liquidate) ? w.liqDaysValue(num(it.days_to_liquidate, 2)) : w.liqNd}</td>
                    <td className={tono}>● {w.liqScore[it.score] || it.score}{it.reason ? <small className="perf-liq-why"> · {it.reason}</small>
                      : !volumeMisurato(it) && it.score !== 'green' && it.score !== 'yellow' && it.score !== 'red'
                        ? <small className="perf-liq-why"> · {w.liqNoVolume}</small> : null}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
          {l.note && <p className="perf-why">{w.liqNote(l.note)}</p>}
        </>
      )}</StatoCard>
    </section>
  );
}

function Scenario({ id, s, onRiprova }: { id: IdScenario; s: Stato<MonteCarloResult>; onRiprova: () => void }) {
  const w = parole();
  const [titolo, sotto] = w.scenarios[id];
  const replay = id === 'gfc_2008' || id === 'covid_2020';
  return (
    <div className="perf-sc" data-qa="perf-scenario" data-scenario={id}>
      <div className="perf-sc-h"><b>{titolo}</b><span>{sotto}</span></div>
      {s.stato === 'errore'
        ? <div><p className="perf-state is-error"><b>{w.unavailable}</b> · {s.testo}</p>
            <button type="button" className="bbn-link" onClick={onRiprova}>{w.retry}</button></div>
        : <StatoCard s={s}>{m => {
          const meta = m.stress_meta;
          if (replay) {
            if (meta?.fallback) return <span className="bbn-warn-pill" title={meta.fallback_reason}>{w.replayFallback}</span>;
            const proxied = Object.keys(meta?.proxied || {});
            // window_loss_* è un rendimento CON SEGNO (negativo = perdita): un replay può anche
            // chiudere in guadagno. Rosso solo per la perdita, verde per il guadagno, nessun
            // colore se manca; l'etichetta segue il segno dell'euro (o della % se l'euro manca).
            const esito = finito(meta?.window_loss_eur) ? meta!.window_loss_eur : finito(meta?.window_loss_pct) ? meta!.window_loss_pct : null;
            const tono = esito == null || esito === 0 ? '' : esito < 0 ? 'down-t' : 'up-t';
            return (
              <>
                <div className="perf-sc-v is-big" data-replay-sign={esito == null ? 'na' : esito < 0 ? 'loss' : esito > 0 ? 'gain' : 'flat'}>
                  <span>{esito != null && esito > 0 ? w.replayGain : esito != null && esito < 0 ? w.replayLoss : w.replayOutcome}</span>
                  <b className={tono || undefined}>{euro(meta?.window_loss_eur, 0, true)}</b><small>{pct(meta?.window_loss_pct)}</small></div>
                <div className="perf-sc-note">{w.replayNote}</div>
                {proxied.length > 0 && <div className="perf-sc-f">{w.proxied(proxied.join(', '))}</div>}
              </>
            );
          }
          const base = m.base_nav_eur, p50 = m.percentiles_eur?.p50, p5 = m.percentiles_eur?.p5;
          const perdita = (v: number | undefined) => (finito(v) && finito(base) ? v - base : null);
          const quota = (v: number | undefined) => (finito(v) && finito(base) && base > 0 ? (v / base - 1) * 100 : null);
          return (
            <>
              <div className="perf-sc-v"><span>{w.medianLoss}</span><b className="down-t">{euro(perdita(p50), 0)}</b><small>{pct(m.median_return_pct)}</small></div>
              <div className="perf-sc-v"><span>{w.worst5}</span><b className="down-t">{euro(perdita(p5), 0)}</b><small>{pct(quota(p5))}</small></div>
              <div className="perf-sc-p"><span>{w.probLoss10}</span>
                <div className="perf-pbar" aria-hidden="true"><i style={{ width: (finito(m.prob_loss_10pct) ? Math.min(100, m.prob_loss_10pct) : 0) + '%' }} /></div>
                <b>{pct(m.prob_loss_10pct, 1, false)}</b></div>
              <div className="perf-sc-f">{w.es95(euro(m.es_95_eur, 0))}</div>
            </>
          );
        }}</StatoCard>}
    </div>
  );
}

export default function VistaRischio(p: PropsRischio) {
  const w = parole();
  return (
    <div className="perf-grid-rischio">
      <PerditaPotenziale rischio={p.rischio} />
      <Distribuzione pnl={p.pnl} rischio={p.rischio} />
      <ContributoRischio contributi={p.contributi} concentrazione={p.concentrazione} nomi={p.nomi} />
      <ConfrontoMercato rischio={p.rischio} />
      <Drawdown drawdown={p.drawdown} />
      <Liquidita liquidita={p.liquidita} />
      <section className="bbn-card perf-scen">
        <header className="bbn-card-head"><h2>{w.stress}</h2><Info testo={w.stressInfo} /><span className="bbn-grow" /><span className="bbn-card-note">{w.stressSub}</span></header>
        <div className="perf-scen-grid">
          {SCENARI.map(id => <Scenario key={id} id={id} s={p.scenari[id]} onRiprova={() => p.onRiprovaScenario(id)} />)}
        </div>
      </section>
    </div>
  );
}
