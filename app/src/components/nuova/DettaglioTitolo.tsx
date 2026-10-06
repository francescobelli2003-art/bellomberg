import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { MktQuote, Position } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { dayEur, dayPct } from '@/lib/dailypl';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import TvChartPanel from '@/components/TvChartPanel';
import IconaTitolo from './IconaTitolo';
import PastigliaVariazione from './PastigliaVariazione';

// Quanto di una tesi arriva al consigliere: specchio di current_facts.MAX_CHAR_TESI.
const LIMITE_TESI = 10000;
const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const compatto = (value: number | null | undefined, decimali = 2) => finito(value)
  ? Intl.NumberFormat(localeDi(linguaCorrente()), { notation: 'compact', maximumFractionDigits: decimali }).format(value) : fmtNum(null);

export interface TestiDettaglio {
  detailOf: string; close: string; openMarkets: string; today: string;
  value: string; totalPl: string; weight: string; quantity: string; stalePrice: string;
  keyData: string; marketCap: string; pe: string; fwdPe: string; eps: string; dividend: string; beta: string; evEbitda: string;
  range52: string; fromHigh: (pct: string) => string; analysts: string; target: string; upside: string;
  noAnalysts: string; prevClose: string; volume: string; avgVolume: string; avgPrice: string;
  quoteLoading: string; quoteError: (motivo: string) => string; source: (valuta: string) => string; reco: (chiave: string) => string;
  thesis: string; thesisHint: string; noThesis: string; thesisCut: (letti: string, scritti: string) => string;
}

/** Dati chiave da /market/quote: `quote` null finché la lettura è in corso, `errore` se è fallita. */
export interface QuoteDettaglio { quote: MktQuote | null; errore: string | null }

/** Pannello laterale del titolo: prezzo, variazione, candele (TvChartPanel), dati chiave
 *  di mercato e numeri della posizione. Esc o il velo lo chiudono; il focus torna a chi l'ha aperto. */
export default function DettaglioTitolo({ posizione, nome, quote, testi, onChiudi, onApriMercati }: {
  posizione: Position | null;
  nome: string;
  quote: QuoteDettaglio | null;
  testi: TestiDettaglio;
  onChiudi: () => void;
  onApriMercati: (ticker: string) => void;
}) {
  const chiudiRef = useRef<HTMLButtonElement>(null);
  const aperto = !!posizione;
  useEffect(() => {
    if (!aperto) return;
    const prima = document.activeElement as HTMLElement | null;
    chiudiRef.current?.focus();
    return () => { try { prima?.focus(); } catch { /* elemento smontato */ } };
  }, [aperto]);
  if (!posizione) return null;
  const p = posizione;
  const dp = dayPct(p), de = dayEur(p);
  const valuta = p.valuta === 'EUR' ? '€' : p.valuta;
  const nd = fmtNum(null);
  const tesi = (p.tesi || '').trim();

  // Dati di mercato: tutti nella valuta di quotazione di Yahoo (q.currency), mai convertiti.
  const q = quote?.quote || null;
  const qValuta = q?.currency === 'EUR' ? '€' : q?.currency || '';
  const conValuta = (v: number | null | undefined) => finito(v) ? `${fmtNum(v, 2)}${qValuta ? ' ' + qValuta : ''}` : nd;
  const px = finito(q?.price) ? q!.price! : null;
  const lo = q?.low_52w, hi = q?.high_52w;
  const range = finito(lo) && finito(hi) && finito(px) && hi > lo ? Math.min(100, Math.max(0, (px - lo) / (hi - lo) * 100)) : null;
  const dalMax = finito(hi) && finito(px) && hi > 0 ? (px / hi - 1) * 100 : null;
  const reco = q?.recommendation && q.recommendation.toLowerCase() !== 'none' ? q.recommendation : null;
  const upside = finito(q?.target_mean) && finito(px) && px > 0 ? (q!.target_mean! / px - 1) * 100 : null;
  // dividendYield di Yahoo (yfinance ≥ 0.2.5x) è già in punti percentuali, non una frazione: si stampa così com'è col simbolo %
  const chiave: Array<[string, string]> = [
    [testi.marketCap, finito(q?.market_cap) ? `${compatto(q!.market_cap)}${qValuta ? ' ' + qValuta : ''}` : nd],
    [testi.pe, finito(q?.pe) ? fmtNum(q!.pe, 1) : nd],
    [testi.fwdPe, finito(q?.fwd_pe) ? fmtNum(q!.fwd_pe, 1) : nd],
    [testi.eps, conValuta(q?.eps)],
    [testi.dividend, finito(q?.div_yield) ? fmtNum(q!.div_yield, 2) + '%' : nd],
    [testi.beta, finito(q?.beta) ? fmtNum(q!.beta, 2) : nd],
    [testi.evEbitda, finito(q?.ev_ebitda) ? fmtNum(q!.ev_ebitda, 1) : nd],
  ];
  const quoteStato = !quote ? null : quote.errore ? testi.quoteError(quote.errore) : !q ? testi.quoteLoading : null;

  return (
    <div className="bbn-drawer-root" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onChiudi(); } }}>
      <div className="bbn-scrim" onClick={onChiudi} aria-hidden="true" />
      <aside className="bbn-drawer bbn-drawer-wide" role="dialog" aria-modal="true" aria-label={`${testi.detailOf} ${nome}`} data-testid="stock-detail">
        <header className="bbn-drawer-head">
          <IconaTitolo ticker={p.ticker} nome={p.nome} dimensione="lg" />
          <div className="bbn-drawer-title"><b>{nome}</b><span>{[p.ticker, p.valuta, q?.exchange].filter(Boolean).join(' · ')}</span></div>
          <button ref={chiudiRef} type="button" className="bbn-icon-btn" onClick={onChiudi} aria-label={testi.close} title={testi.close}><X size={18} aria-hidden="true" /></button>
        </header>
        <div className="bbn-drawer-price">
          <span className="num">{finito(p.prezzo_live) ? `${fmtNum(p.prezzo_live, 2)} ${valuta}` : fmtNum(null)}</span>
          {p.price_stale && <span className="bbn-warn-pill">{testi.stalePrice}</span>}
          <PastigliaVariazione valore={finito(de) ? de : finito(dp) ? dp : null} grande lampo={finito(p.prezzo_live) ? p.prezzo_live : null}>
            <span className="num">{fmtEUR(finito(de) ? de : null, true)}{finito(dp) ? ` · ${fmtPct(dp)}` : ''} {testi.today}</span>
          </PastigliaVariazione>
          <span className="bbn-drawer-meta">
            {finito(p.prev_close) && <span className="num">{testi.prevClose} {fmtNum(p.prev_close, 2)}</span>}
            {finito(q?.volume) && <span className="num">{testi.volume} {compatto(q!.volume, 1)}{finito(q?.avg_volume) ? ` (${testi.avgVolume} ${compatto(q!.avg_volume, 1)})` : ''}</span>}
          </span>
        </div>

        <div className="bbn-drawer-main">
          <div className="bbn-drawer-chart">
            <TvChartPanel ticker={p.ticker} fill defaultRange={5} defaultInterval={4} timeSelects />
          </div>
          <div className="bbn-drawer-side" data-testid="stock-detail-key">
            <section className="bbn-drawer-card">
              <h3>{testi.keyData}</h3>
              {quoteStato
                ? <p className="bbn-drawer-note" role={quote?.errore ? 'alert' : 'status'}>{quoteStato}</p>
                : <dl className="bbn-kv num">{chiave.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
            </section>
            {q && <>
              <section className="bbn-drawer-card">
                <h3>{testi.range52}</h3>
                {range != null ? <>
                  <div className="bbn-range" aria-hidden="true"><i style={{ left: range.toFixed(1) + '%' }} /></div>
                  <div className="bbn-range-lab num">
                    <span>{fmtNum(lo, 2)}</span>
                    {finito(dalMax) && <span>{testi.fromHigh(fmtPct(dalMax))}</span>}
                    <span>{fmtNum(hi, 2)}</span>
                  </div>
                </> : <p className="bbn-drawer-note">{nd}</p>}
              </section>
              <section className="bbn-drawer-card">
                <h3>{testi.analysts}</h3>
                {reco || finito(q.target_mean) ? <>
                  <div className="bbn-reco-row">
                    {reco && <span className={'bbn-reco' + (/buy/i.test(reco) ? ' is-up' : /sell|under/i.test(reco) ? ' is-down' : '')}>{testi.reco(reco)}</span>}
                    {finito(q.target_mean) && <span className="num">{testi.target} <b>{conValuta(q.target_mean)}</b></span>}
                  </div>
                  {finito(upside) && <p className="bbn-drawer-note num">
                    {testi.upside} <span className={upside >= 0 ? 'is-up' : 'is-down'}>{fmtPct(upside)}</span>
                  </p>}
                </> : <p className="bbn-drawer-note">{testi.noAnalysts}</p>}
              </section>
            </>}
          </div>
        </div>

        <dl className="bbn-stats bbn-stats-4">
          <div><dt>{testi.value}</dt><dd className="num">{fmtEUR(finito(p.valore_mercato) ? p.valore_mercato : null)}</dd></div>
          <div><dt>{testi.totalPl}</dt><dd className={'num ' + (finito(p.pl_eur) ? (p.pl_eur > 0 ? 'is-up' : p.pl_eur < 0 ? 'is-down' : '') : '')}>
            {fmtEUR(finito(p.pl_eur) ? p.pl_eur : null, true)}{finito(p.pl_pct) ? ` · ${fmtPct(p.pl_pct)}` : ''}</dd></div>
          <div><dt>{testi.weight}</dt><dd className="num">{finito(p.peso_pct) ? fmtNum(p.peso_pct, 1) + '%' : fmtNum(null)}</dd></div>
          <div><dt>{testi.quantity} · {testi.avgPrice}</dt><dd className="num">
            {finito(p.quantita) ? fmtNum(p.quantita, p.quantita % 1 ? 4 : 0) : fmtNum(null)}
            {' · '}{finito(p.prezzo_medio) ? `${fmtNum(p.prezzo_medio, 2)} ${valuta}` : fmtNum(null)}</dd></div>
        </dl>
        {/* la view del PM che il consigliere riceve (positions.tesi): qui si legge, si scrive dal Diario */}
        <section className="bbn-drawer-card bbn-drawer-tesi" data-testid="stock-detail-thesis">
          <h3>{testi.thesis}</h3>
          {tesi ? <>
            <p className="bbn-tesi-testo">{tesi}</p>
            <p className="bbn-drawer-note">{tesi.length > LIMITE_TESI ? testi.thesisCut(fmtNum(LIMITE_TESI, 0), fmtNum(tesi.length, 0)) : testi.thesisHint}</p>
          </> : <p className="bbn-drawer-note">{testi.noThesis}</p>}
        </section>
        <footer className="bbn-drawer-foot">
          {q && <span>{testi.source(q.currency || nd)}</span>}
          <span className="bbn-grow" />
          <button type="button" className="bbn-link bbn-drawer-link" onClick={() => onApriMercati(p.ticker)}>{testi.openMarkets} →</button>
        </footer>
      </aside>
    </div>
  );
}
