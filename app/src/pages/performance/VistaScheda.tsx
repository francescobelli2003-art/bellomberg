import { Fragment, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Bitcoin, Cpu, HelpCircle, Landmark, MessageSquare, Mountain, ShoppingBag, Zap } from 'lucide-react';
import type { AdvancedMetrics, AttributionPayload, TwrPayload } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { Segmenti } from '@/components/nuova/Card';
import IconaTitolo from '@/components/nuova/IconaTitolo';
import PastigliaVariazione from '@/components/nuova/PastigliaVariazione';
import { PERIODI, indiceBase, pnlPeriodo, pnlTotale, rendimentiMensili, rendimentoAllaData, statistichePeriodo } from './calcoli';
import type { AnnoMensile, Periodo } from './calcoli';
import GraficoTwr from './GraficoTwr';
import { VUOTO, dataBreve, euro, num, pct, punti } from './formato';
import { parole } from './parole';

/** Stato di ogni sorgente: in attesa, errore dichiarato (testo della fonte), dati. */
export type Stato<T> = { stato: 'attesa' } | { stato: 'errore'; testo: string } | { stato: 'ok'; dati: T };

export function StatoCard<T>({ s, children }: { s: Stato<T>; children: (dati: T) => ReactNode }) {
  const w = parole();
  if (s.stato === 'attesa') return <p className="perf-state" role="status">{w.calculating}</p>;
  if (s.stato === 'errore') return <p className="perf-state is-error" role="status"><b>{w.unavailable}</b> · {s.testo}</p>;
  return <>{children(s.dati)}</>;
}

export function Info({ testo }: { testo: string }) {
  return <i className="perf-info" title={testo} aria-label={testo} role="img">i</i>;
}

const finito = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const classeSegno = (v: number | null | undefined) => (!finito(v) ? undefined : v > 0 ? 'up-t' : v < 0 ? 'down-t' : undefined);

export interface Contabilita {
  valoreMercato: number | null; costo: number | null; nonRealizzato: number | null;
  realizzato: number | null; dividendi: number | null; cassa: number | null;
  /** serie contabile assente: la riga lo dichiara invece di mostrare zeri */
  nota?: string;
}
export interface PropsScheda {
  periodo: Periodo;
  onPeriodo: (p: Periodo) => void;
  twr: Stato<TwrPayload>;
  /** benchmark già allineato (ufficiale o fallback dichiarato), base 100 */
  spy: Stato<{ date: string[]; indice: number[] }>;
  avanzate: Stato<AdvancedMetrics>;
  contabilita: Stato<Contabilita>;
  nav: { valore: number | null; posizioni: number | null };
  attribuzione: Stato<AttributionPayload>;
  /** ticker -> nome, dalle posizioni del portafoglio */
  nomi: Record<string, string>;
}

/** Cifra grande del riepilogo: rendimento % (TWR) o P&L del periodo in euro; l'altra resta sotto, in piccolo. */
export type Unita = 'pct' | 'eur';
const CHIAVE_UNITA = 'bb.performance.unit';
/** Preferenza di vista ricordata solo in questo browser, mai dati. */
function leggiUnita(): Unita {
  try { return localStorage.getItem(CHIAVE_UNITA) === 'eur' ? 'eur' : 'pct'; } catch { return 'pct'; }
}

const ICONE: Record<string, typeof Cpu> = {
  Technology: Cpu, Crypto: Bitcoin, 'Consumer Cyclical': ShoppingBag, Energy: Zap,
  'Basic Materials': Mountain, 'Financial Services': Landmark, 'Communication Services': MessageSquare,
};

function Riepilogo({ periodo, onPeriodo, twr, spy, nav }: Pick<PropsScheda, 'periodo' | 'onPeriodo' | 'spy' | 'nav'> & { twr: TwrPayload }) {
  const w = parole();
  const [mostraSpy, setMostraSpy] = useState(true);
  const [mostraMedie, setMostraMedie] = useState(false);
  const [unita, setUnitaStato] = useState<Unita>(leggiUnita);
  const setUnita = (u: Unita) => {
    setUnitaStato(u);
    try { localStorage.setItem(CHIAVE_UNITA, u); } catch { /* preferenza non salvabile: vale per questa sessione */ }
  };
  const date = twr.dates || [], indice = twr.twr_index || [];
  const base = indiceBase(date, periodo);
  // tasso privo di rischio non dichiarato dal motore: Sharpe n.d. col motivo, mai rf = 0 muto (08b S10)
  const rfDato = twr.metrics?.risk_free_used;
  const rf = typeof rfDato === 'number' && Number.isFinite(rfDato) ? rfDato : null;
  const st0 = statistichePeriodo(indice, base, rf ?? 0, date);
  const st = rf == null ? { ...st0, sharpe: null } : st0;
  const spyOk = spy.stato === 'ok' ? spy.dati : null;
  const spyPct = spyOk && date[base] ? rendimentoAllaData(spyOk.date, spyOk.indice, date[base]) : null;
  const scarto = finito(st.rendimentoPct) && finito(spyPct) ? st.rendimentoPct - spyPct : null;
  const spyItd = spyOk && date[0] ? rendimentoAllaData(spyOk.date, spyOk.indice, date[0]) : null;
  const itd = twr.metrics?.twr_total_pct ?? null;
  const spyAllineato = useMemo(() => {
    if (!spyOk) return null;
    const mappa = new Map(spyOk.date.map((d, i) => [d, spyOk.indice[i]]));
    return date.map(d => (mappa.has(d) ? mappa.get(d)! : null));
  }, [spyOk, date]);
  const pnl = pnlPeriodo(date, twr.values_eur || [], twr.flows_eur, indice, base);
  const cifraPct = pct(st.rendimentoPct), cifraEur = euro(pnl.eur, 2, true);
  const parziale = pnl.eur != null && pnl.esclusi > 0 ? w.pnlPeriodPartial(pnl.esclusi) : null;
  const storiaCorta = base === 0 && periodo !== 'Tutto' && date.length > 0;
  return (
    <section className="bbn-card perf-hero" data-qa="perf-chart">
      <div className="perf-hero-top">
        <div>
          <div className="perf-hero-label">{w.returnLabel(w.periodLong[periodo])} <Info testo={unita === 'eur' ? w.pnlPeriodInfo : w.returnInfo} />
            <Segmenti<Unita> etichetta={w.unit} valore={unita} onChange={setUnita} className="perf-unit"
              opzioni={[{ id: 'pct', testo: w.unitPct, title: w.unitPctHint }, { id: 'eur', testo: w.unitEur, title: w.unitEurHint }]} /></div>
          <div className="perf-hero-value num" data-qa="perf-return">
            <span className={unita === 'eur' ? classeSegno(pnl.eur) : undefined}>{unita === 'eur' ? cifraEur : cifraPct}</span>
            <span className="perf-hero-alt" title={unita === 'eur' ? w.returnInfo : parziale ?? w.pnlPeriodInfo}>
              {unita === 'eur' ? cifraPct : cifraEur}{parziale && <small> · {parziale}</small>}</span>
          </div>
          <div className="perf-hero-row">
            <PastigliaVariazione valore={scarto}>{scarto == null ? w.vsSpy(VUOTO) : w.vsSpy(punti(scarto))}</PastigliaVariazione>
            <span className="bbn-pill is-piatto">{w.spyPill(pct(spyPct))}</span>
          </div>
        </div>
        <div className="perf-tiles">
          <div className="perf-tile"><span>{w.maxDd} <Info testo={w.maxDdInfo} /></span>
            <b className="down-t">{pct(st.maxDdPct, 2, false)}</b><small>{w.maxDdSub(pct(st.ddOggiPct, 2, false))}</small></div>
          <div className="perf-tile"><span>{w.sharpe} <Info testo={w.sharpeInfo} /></span>
            <b title={st.sharpe == null ? (rf == null ? w.sharpeNoRf : w.sharpeShort) : undefined}>{num(st.sharpe, 2)}</b>
              <small>{rf == null ? w.sharpeNoRf : w.sharpeSub(pct(rf * 100, 2, false))}</small></div>
          <div className="perf-tile"><span>{w.sinceStart} <Info testo={w.sinceStartInfo(dataBreve(date[0]))} /></span>
            <b className={classeSegno(itd)}>{pct(itd)}</b>
            <small>{w.sinceStartSub(pct(spyItd), finito(itd) && finito(spyItd) ? punti(itd - spyItd) : VUOTO)}</small></div>
          <div className="perf-tile"><span>{w.nav}</span><b>{euro(nav.valore)}</b><small>{w.navSub(nav.posizioni)}</small></div>
        </div>
      </div>
      <div className="perf-tools">
        <button type="button" className="bbn-toggle-chip" aria-pressed={mostraSpy && !!spyOk} disabled={!spyOk}
          title={spy.stato === 'errore' ? w.spyMissing(spy.testo) : w.spyHint} onClick={() => setMostraSpy(v => !v)}>
          <i className="is-dash" />{w.spy}</button>
        <button type="button" className="bbn-toggle-chip" aria-pressed={mostraMedie} onClick={() => setMostraMedie(v => !v)}>
          <i className="is-sma" />{w.sma}</button>
        <span className="bbn-grow" />
        {storiaCorta && <span className="bbn-card-note">{w.shortHistory(dataBreve(date[0]))}</span>}
        <span data-qa="perf-range">
          <Segmenti<Periodo> etichetta={w.period} valore={periodo} onChange={onPeriodo}
            opzioni={PERIODI.map(id => ({ id, testo: w.periods[id] }))} />
        </span>
      </div>
      <GraficoTwr date={date} indice={indice} spy={spyAllineato} base={base}
        mostraSpy={mostraSpy && !!spyOk} mostraMedie={mostraMedie} ricostruitaFino={twr.regime_summary?.official_since ?? null} />
    </section>
  );
}

function Riga({ etichetta, info, children, forte = false }: { etichetta: string; info: string; children: ReactNode; forte?: boolean }) {
  return <div className={'perf-mrow' + (forte ? ' is-strong' : '')}><span title={info}>{etichetta}</span><b>{children}</b></div>;
}

function AltreMetriche({ twr, avanzate, contabilita }: Pick<PropsScheda, 'twr' | 'avanzate' | 'contabilita'>) {
  const w = parole();
  const m = twr.stato === 'ok' ? twr.dati.metrics : undefined;
  const a = avanzate.stato === 'ok' ? avanzate.dati : null;
  const inizio = twr.stato === 'ok' ? twr.dati.dates?.[0] : undefined;
  return (
    <section className="bbn-card perf-side">
      <header className="bbn-card-head"><h2>{w.otherMetrics}</h2><span className="bbn-card-note">{w.sinceDate(dataBreve(inizio))}</span></header>
      <div className="perf-mgroup">
        <h3>{w.groupRisk}</h3>
        <Riga etichetta={w.mVol} info={w.mVolInfo}>{pct(m?.vol_annual_pct, 2, false)}</Riga>
        <Riga etichetta={w.mSortino} info={w.mSortinoInfo}>{num(a?.sortino, 2)}</Riga>
        <Riga etichetta={w.mCalmar} info={w.mCalmarInfo}>{num(a?.calmar, 2)}</Riga>
        <Riga etichetta={w.mBeta} info={w.mBetaInfo}>{num(a?.benchmark?.beta, 2)}</Riga>
        <Riga etichetta={w.mAlpha} info={w.mAlphaInfo}><span className={classeSegno(a?.benchmark?.alpha_annual_pct)}>{pct(a?.benchmark?.alpha_annual_pct)}</span></Riga>
        <Riga etichetta={w.mIrr} info={w.mIrrInfo}><span className={classeSegno(m?.irr_annual_pct)}>{pct(m?.irr_annual_pct)}</span></Riga>
        {avanzate.stato === 'errore' && <p className="perf-state is-error"><b>{w.unavailable}</b> · {avanzate.testo}</p>}
      </div>
      <div className="perf-mgroup">
        <h3>{w.groupBooks}</h3>
        <StatoCard s={contabilita}>{c => (
          <>
            {c.nota && <p className="perf-state" role="status">{c.nota}</p>}
            <Riga etichetta={w.mTotal} info={w.mTotalInfo} forte>
              <span className={classeSegno(pnlTotale(c.nonRealizzato, c.realizzato, c.dividendi))}>{euro(pnlTotale(c.nonRealizzato, c.realizzato, c.dividendi), 2, true)}</span></Riga>
            <Riga etichetta={w.mMv} info={w.mMvInfo}>{euro(c.valoreMercato)}{c.costo != null && <small>{w.mCost(euro(c.costo))}</small>}</Riga>
            <Riga etichetta={w.mUpl} info={w.mUplInfo}><span className={classeSegno(c.nonRealizzato)}>{euro(c.nonRealizzato, 2, true)}</span></Riga>
            <Riga etichetta={w.mRpl} info={w.mRplInfo}><span className={classeSegno(c.realizzato)}>{euro(c.realizzato, 2, true)}</span></Riga>
            <Riga etichetta={w.mDiv} info={w.mDivInfo}>{euro(c.dividendi)}</Riga>
            <Riga etichetta={w.mCash} info={w.mCashInfo}>{euro(c.cassa)}</Riga>
          </>
        )}</StatoCard>
      </div>
    </section>
  );
}

function Mensili({ twr, spy }: { twr: TwrPayload; spy: Stato<{ date: string[]; indice: number[] }> }) {
  const w = parole();
  const port = rendimentiMensili(twr.dates || [], twr.twr_index || []);
  const bench = spy.stato === 'ok' ? rendimentiMensili(spy.dati.date, spy.dati.indice, twr.dates?.[0]) : null;
  if (!port) return <p className="perf-state">{w.unavailable}</p>;
  const mesi = Array.from({ length: 12 }, (_, m) => {
    const t = new Intl.DateTimeFormat(localeDi(linguaCorrente()), { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2000, m, 1)));
    return t.charAt(0).toUpperCase() + t.slice(1, 3);
  });
  // --bbn-heat-up/down sono terne RGB (come nella heatmap della Dashboard): rgba(var(...), alfa)
  const sfondo = (v: number) => `rgba(var(${v >= 0 ? '--bbn-heat-up' : '--bbn-heat-down'}), ${(0.1 + Math.min(1, Math.abs(v) / 18) * 0.55).toFixed(2)})`;
  const cella = (v: number | null, chiave: string, inCorso: boolean, diff = false) => {
    if (!finito(v)) return <td key={chiave} className="is-empty">–</td>;
    return (
      <td key={chiave} className={diff ? classeSegno(v) : undefined} style={diff ? undefined : { background: sfondo(v) }}>
        {diff ? num(v, 1, true) : pct(v, 1)}{inCorso && <span className="perf-mtd">{w.inProgress}</span>}
      </td>
    );
  };
  const anni = [...port.anni].reverse();
  return (
    <div className="perf-heat">
      {anni.map((a: AnnoMensile) => {
        const b = bench?.anni.find(x => x.anno === a.anno) ?? null;
        const inCorso = (i: number) => `${a.anno}-${String(i + 1).padStart(2, '0')}` === port.ultimoMese;
        const diff = a.celle.map((v, i) => (finito(v) && b && finito(b.celle[i]) ? v - (b.celle[i] as number) : null));
        const ytdDiff = finito(a.ytd) && b && finito(b.ytd) ? a.ytd - b.ytd : null;
        return (
          <table key={a.anno}>
            <thead><tr><th className="is-label">{anni.length > 1 ? a.anno : ''}</th>{mesi.map(m => <th key={m}>{m}</th>)}<th>{w.year}</th></tr></thead>
            <tbody>
              <tr><td className="is-label">{w.rowPortfolio}<small>{w.rowPortfolioSub}</small></td>
                {a.celle.map((v, i) => cella(v, 'p' + i, inCorso(i)))}<td className={'is-year ' + (classeSegno(a.ytd) || '')}>{pct(a.ytd)}</td></tr>
              {b && <tr><td className="is-label">{w.rowSpy}<small>{w.rowSpySub}</small></td>
                {b.celle.map((v, i) => cella(v, 's' + i, inCorso(i)))}<td className={'is-year ' + (classeSegno(b.ytd) || '')}>{pct(b.ytd)}</td></tr>}
              {b && <tr><td className="is-label">{w.rowDiff}<small>{w.rowDiffSub}</small></td>
                {diff.map((v, i) => cella(v, 'd' + i, inCorso(i), true))}<td className={'is-year ' + (classeSegno(ytdDiff) || '')}>{punti(ytdDiff)}</td></tr>}
            </tbody>
          </table>
        );
      })}
    </div>
  );
}

function Barra({ v, max }: { v: number; max: number }) {
  const largo = Math.min(50, Math.abs(v) / (max || 1) * 50);
  return <span className="perf-bibar" aria-hidden="true"><i className={v >= 0 ? 'is-pos' : 'is-neg'} style={{ width: largo + '%' }} /></span>;
}

function Attribuzione({ periodo, attribuzione, nomi }: Pick<PropsScheda, 'periodo' | 'attribuzione' | 'nomi'>) {
  const w = parole();
  const [aperte, setAperte] = useState<Record<string, boolean>>({});
  const dati = attribuzione.stato === 'ok' ? attribuzione.dati : null;
  const rec = dati?.reconciliation;
  return (
    <>
      <section className="bbn-card perf-pos" data-qa="perf-attribution">
        <header className="bbn-card-head">
          <h2>{w.attribution}</h2><Info testo={w.attributionInfo} /><span className="bbn-grow" />
          <span className="bbn-card-note">{w.periodLong[periodo]}</span>
          {dati && !dati.error && finito(dati.portfolio_return_pct) && (
            <PastigliaVariazione valore={dati.portfolio_return_pct}
              title={rec && finito(rec.official_twr_pct) && finito(rec.delta_pp) ? w.attributionRecon(pct(rec.official_twr_pct), num(rec.delta_pp, 2, true)) : undefined}>
              {punti(dati.portfolio_return_pct)}
            </PastigliaVariazione>
          )}
        </header>
        <StatoCard s={attribuzione}>{d => {
          if (d.error) return <p className="perf-state is-error"><b>{w.unavailable}</b> · {d.error}</p>;
          const righe = [...(d.by_position || [])].sort((a, b) => b.contribution_pct - a.contribution_pct);
          const max = Math.max(0, ...righe.map(r => Math.abs(r.contribution_pct)));
          const settoreDi = new Map<string, string>();
          for (const b of d.by_bucket || []) for (const t of b.tickers) settoreDi.set(t, b.bucket);
          return (
            <div className="perf-rows">
              <div className="perf-arow perf-arow-head"><span /><span>{w.colTitle}</span><span /><span className="is-r">{w.colContribution}</span><span className="is-r">{w.colWeight}</span></div>
              {righe.map(r => {
                const escl = d.excluded?.find(x => x.ticker === r.ticker && x.partial);
                const nome = nomi[r.ticker] || r.ticker;
                const aperta = !!aperte[r.ticker];
                return (
                  <Fragment key={r.ticker}>
                    <button type="button" className="perf-arow" aria-expanded={aperta} aria-label={w.openRow(nome)}
                      onClick={() => setAperte(s => ({ ...s, [r.ticker]: !s[r.ticker] }))}>
                      <IconaTitolo ticker={r.ticker} nome={nome} dimensione="sm" />
                      <span className="perf-nm"><b>{nome}{escl && <span className="bbn-warn-pill">{w.estimated}</span>}</b>
                        <span>{r.ticker}{settoreDi.get(r.ticker) ? ' · ' + settoreDi.get(r.ticker) : ''}</span></span>
                      <Barra v={r.contribution_pct} max={max} />
                      <span className={'perf-val ' + (classeSegno(r.contribution_pct) || '')}>{num(r.contribution_pct, 2, true)}</span>
                      <span className="perf-wt">{pct(r.avg_weight_pct, 1, false)}</span>
                    </button>
                    {aperta && (
                      <div className="perf-adetail">
                        <span>{w.local}<b>{num(r.local_pct, 2, true)}</b></span>
                        <span>{w.fx}<b>{num(r.fx_pct, 2, true)}</b></span>
                        <span>{w.avgWeight}<b>{pct(r.avg_weight_pct, 1, false)}</b></span>
                        {escl ? <span className="is-note">{w.estimatedNote(escl.days_excluded, escl.days_total)}</span> : <span />}
                      </div>
                    )}
                  </Fragment>
                );
              })}
            </div>
          );
        }}</StatoCard>
      </section>
      <section className="bbn-card perf-sect">
        <header className="bbn-card-head"><h2>{w.bySector}</h2>
          {dati?.by_bucket && <span className="bbn-card-note">{w.sectors(dati.by_bucket.length)}</span>}</header>
        <StatoCard s={attribuzione}>{d => {
          if (d.error) return <p className="perf-state">{w.unavailable}</p>;
          const righe = [...(d.by_bucket || [])].sort((a, b) => b.contribution_pct - a.contribution_pct);
          const max = Math.max(0, ...righe.map(r => Math.abs(r.contribution_pct)));
          return (
            <>
              <div className="perf-rows">
                <div className="perf-arow perf-arow-head"><span /><span>{w.colSector}</span><span /><span className="is-r">{w.colContribution}</span></div>
                {righe.map(b => {
                  const Icona = ICONE[b.bucket] || HelpCircle;
                  const nome = b.bucket === 'n.d.' || !b.bucket ? w.unclassified : b.bucket;
                  return (
                    <div key={b.bucket} className="perf-arow" title={b.tickers.join(' · ')}>
                      <span className="perf-sicon"><Icona size={18} aria-hidden="true" /></span>
                      <span className="perf-nm"><b>{nome}</b><span>{w.titles(b.tickers.length)}</span></span>
                      <Barra v={b.contribution_pct} max={max} />
                      <span className={'perf-val ' + (classeSegno(b.contribution_pct) || '')}>{num(b.contribution_pct, 2, true)}</span>
                    </div>
                  );
                })}
              </div>
              {(d.by_currency || []).map(c => (
                <div key={c.currency} className="perf-fxline"><span className="bbn-chip"><b>{c.currency}</b></span>
                  {(d.by_currency || []).length === 1
                    ? <span>{w.fxAll(c.currency)} <b>{punti(c.fx_contribution_pct)}</b></span>
                    : w.fxLine(c.currency, punti(c.fx_contribution_pct))}</div>
              ))}
            </>
          );
        }}</StatoCard>
      </section>
    </>
  );
}

export default function VistaScheda(p: PropsScheda) {
  const w = parole();
  return (
    <div className="perf-grid-scheda">
      {p.twr.stato === 'ok'
        ? <Riepilogo periodo={p.periodo} onPeriodo={p.onPeriodo} twr={p.twr.dati} spy={p.spy} nav={p.nav} />
        : <section className="bbn-card perf-hero" data-qa="perf-chart">
            <header className="bbn-card-head"><h2>{w.returnLabel(w.periodLong[p.periodo])}</h2>
              <span className="bbn-grow" />
              <span data-qa="perf-range"><Segmenti<Periodo> etichetta={w.period} valore={p.periodo} onChange={p.onPeriodo}
                opzioni={PERIODI.map(id => ({ id, testo: w.periods[id] }))} /></span></header>
            <StatoCard s={p.twr}>{() => null}</StatoCard>
          </section>}
      <AltreMetriche twr={p.twr} avanzate={p.avanzate} contabilita={p.contabilita} />
      <section className="bbn-card perf-month">
        <header className="bbn-card-head"><h2>{w.monthly}</h2><Info testo={w.monthlyInfo} /><span className="bbn-grow" />
          {p.twr.stato === 'ok' && p.twr.dati.dates?.length ? <span className="bbn-card-note">{[...new Set([p.twr.dati.dates[0].slice(0, 4), p.twr.dati.dates[p.twr.dati.dates.length - 1].slice(0, 4)])].join('–')}</span> : null}</header>
        <StatoCard s={p.twr}>{t => <Mensili twr={t} spy={p.spy} />}</StatoCard>
      </section>
      <Attribuzione periodo={p.periodo} attribuzione={p.attribuzione} nomi={p.nomi} />
    </div>
  );
}
