import type { ReactNode } from 'react';
import { AlertCircle, Check } from 'lucide-react';
import type { Lingua } from '@/i18n/lingua';
import { bandaVol, intervallo, numero, tr, volImplicita, type Lettura, type Valore } from './valori';

/* I grafici delle sezioni numeriche. Tutti su una scala sola per grafico, con i valori della
   bozza: si ridisegnano mentre si scrive. Rosso = un avviso di coerenza su quel campo. */

const pct = (x: Valore, max: number) => x == null ? 0 : Math.max(0, Math.min(100, x / max * 100));
const scala = (m: number) => [10, 20, 30, 40, 50, 60, 80, 100].find(s => s >= m) ?? 100;

function Riga({ etichetta, valore, children }: { etichetta: ReactNode; valore: ReactNode; children: ReactNode }) {
  return <div className="mnd-row"><span className="mnd-row-label">{etichetta}</span><div className="mnd-track">{children}</div><span className="mnd-row-value">{valore}</span></div>;
}
function Asse({ max, l }: { max: number; l: Lingua }) {
  const tacche = [0, max / 4, max / 2, max * 3 / 4, max];
  return <div className="mnd-axis" aria-hidden="true"><span /><div>{tacche.map((t, i) => <span key={i} style={{ left: pct(t, max) + '%' }}>{numero(t, l, 1)}{i === 4 ? '%' : ''}</span>)}</div><span /></div>;
}
export function Verifica({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <div className={'mnd-check' + (ok ? '' : ' is-bad')} role={ok ? undefined : 'status'}>{ok ? <Check aria-hidden="true" /> : <AlertCircle aria-hidden="true" />}<span>{children}</span></div>;
}
const Punto = ({ x, max, cls = '' }: { x: Valore; max: number; cls?: string }) => x == null ? null : <i className={'mnd-dot ' + cls} style={{ left: pct(x, max) + '%' }} />;
const Tacca = ({ x, max, cls = '' }: { x: Valore; max: number; cls?: string }) => x == null ? null : <i className={'mnd-tick ' + cls} style={{ left: pct(x, max) + '%' }} />;
const Tratto = ({ da, a, max, cls = '' }: { da: Valore; a: Valore; max: number; cls?: string }) => da == null || a == null ? null
  : <i className={'mnd-fill ' + cls} style={{ left: pct(Math.min(da, a), max) + '%', width: Math.max(1.2, pct(Math.abs(a - da), max)) + '%' }} />;

export function GraficoRischio({ v, avvisi, l }: { v: Lettura; avvisi: Record<string, string>; l: Lingua }) {
  const t = (k: string, p?: Record<string, string>) => tr(l, k, p), f = (x: Valore) => numero(x, l);
  const var99 = v.n('var99_1g_pct'), stress = v.n('stress_gfc_pct'), dd = v.n('drawdown_max_pct'), sig = v.n('drawdown_significativo_pct');
  const max = scala(Math.max(dd ?? 0, stress ?? 0, sig ?? 0, 10) * 1.15);
  const perdita = (k: string, x: Valore, cls = '') => <Riga etichetta={t(k)} valore={f(x) + '%'}><i className="mnd-fill is-soft" style={{ left: 0, width: pct(x, max) + '%' }} /><Punto x={x} max={max} cls={cls} /></Riga>;
  const vol = v.n('volatilita_target_pct'), vi = volImplicita(var99), banda = bandaVol(vol);
  const vmax = scala(Math.max(banda ?? 0, vi ?? 0, 10) * 1.1), volBad = !!avvisi.volatilita_target_pct;
  const ordine = !(var99 != null && stress != null && var99 > stress) && !(stress != null && dd != null && stress > dd) && !(var99 != null && dd != null && var99 > dd);
  return <div className="mnd-hero-cols">
    <div className="mnd-col">
      <div className="mnd-sub">{t('ch_losses')}</div>
      <div className="mnd-plot">
        {perdita('ch_var', var99, avvisi.var99_1g_pct ? 'is-bad' : '')}
        {perdita('ch_stress', stress)}
        {perdita('ch_dd', dd, avvisi.drawdown_max_pct ? 'is-bad' : '')}
        {perdita('ch_dd_title', sig, 'is-muted')}
      </div>
      <Asse max={max} l={l} />
      <Verifica ok={ordine}>{t(ordine ? 'ck_order_ok' : 'ck_order_bad')}</Verifica>
    </div>
    <div className="mnd-col">
      <div className="mnd-sub">{t('ch_vol')}</div>
      <div className="mnd-plot">
        <Riga etichetta={t('ch_vol_target')} valore={f(vol) + '%'}><i className="mnd-fill is-good-soft" style={{ left: 0, width: pct(banda, vmax) + '%' }} /><Punto x={vol} max={vmax} /></Riga>
        <Riga etichetta={t('ch_vol_implied')} valore={numero(vi, l, 1) + '%'}><i className="mnd-fill is-good-soft" style={{ left: 0, width: pct(banda, vmax) + '%' }} /><Punto x={vi} max={vmax} cls={'is-hollow' + (volBad ? ' is-bad' : '')} /></Riga>
      </div>
      <Asse max={vmax} l={l} />
      {vi != null && banda != null && <Verifica ok={!volBad}>{t(volBad ? 'ck_vol_bad' : 'ck_vol_ok', { a: numero(vi, l, 1), b: numero(banda, l, 1) })}</Verifica>}
    </div>
  </div>;
}

export function GraficoSizing({ v, avvisi, l }: { v: Lettura; avvisi: Record<string, string>; l: Lingua }) {
  const t = (k: string) => tr(l, k), f = (x: Valore) => numero(x, l);
  const bs = v.n('base_single_pct'), cs = v.n('cap_single_pct'), bv = v.n('base_veicolo_pct'), cv = v.n('cap_veicolo_pct'), sec = v.n('cap_settore_pct');
  const nuova = v.r('size_nuova_posizione_pct'), floor = v.n('limite_minimo_pct'), minPos = v.n('posizione_minima_pct');
  const max = scala(Math.max(cv ?? 0, sec ?? 0, bv ?? 0, 10) * 1.1);
  const rng = (k: string, a: Valore, b: Valore, bad: boolean) => <Riga etichetta={t(k)} valore={<>{f(a)}<small>→</small>{f(b)}%</>}>
    <Tratto da={a} a={b} max={max} cls={bad ? 'is-bad' : ''} /><Punto x={a} max={max} cls={bad ? 'is-bad' : ''} /><Tacca x={b} max={max} cls={bad ? 'is-bad' : ''} /></Riga>;
  const ok = !avvisi.base_single_pct && !avvisi.base_veicolo_pct && !avvisi.limite_minimo_pct && !avvisi.posizione_minima_pct;
  return <>
    <div className="mnd-sub">{t('ch_sizing')}</div>
    <div className="mnd-plot">
      {rng('ch_single', bs, cs, !!avvisi.base_single_pct)}
      {rng('ch_vehicle', bv, cv, !!avvisi.base_veicolo_pct)}
      <Riga etichetta={t('ch_sector')} valore={'≤ ' + f(sec) + '%'}><i className="mnd-fill is-soft" style={{ left: 0, width: pct(sec, max) + '%' }} /><Tacca x={sec} max={max} /></Riga>
      <Riga etichetta={t('ch_new')} valore={intervallo(nuova, l) + '%'}><Tratto da={nuova[0]} a={nuova[1]} max={max} cls="is-good" /></Riga>
      <Riga etichetta={t('ch_floor')} valore={<>{f(floor)}<small> · </small>{f(minPos)}%</>}><Punto x={floor} max={max} cls={'is-muted' + (avvisi.limite_minimo_pct ? ' is-bad' : '')} /><Punto x={minPos} max={max} cls={'is-hollow' + (avvisi.posizione_minima_pct ? ' is-bad' : '')} /></Riga>
    </div>
    <Asse max={max} l={l} />
    <Verifica ok={ok}>{t(ok ? 'ck_sizing_ok' : 'ck_sizing_bad')}</Verifica>
  </>;
}

function Zona({ colore, valore, titolo, testo }: { colore: string; valore: string; titolo: string; testo: string }) {
  return <div className={'mnd-zone is-' + colore}><b>{valore}</b><span className="mnd-zone-title">{titolo}</span><span>{testo}</span></div>;
}

export function GraficoCassa({ v, avvisi, l, politica }: { v: Lettura; avvisi: Record<string, string>; l: Lingua; politica: string }) {
  const t = (k: string, p?: Record<string, string>) => tr(l, k, p), f = (x: Valore) => numero(x, l);
  const tip = v.r('cassa_tipica_pct'), oltre = v.r('cassa_max_senza_giustificazione_pct'), min = v.n('cassa_minima_pct');
  const quota = v.r('impiego_default_pct'), fin = v.r('impiego_finestra_settimane');
  const max = scala(Math.max(oltre[1] ?? 0, tip[1] ?? 0, 10) * 1.45);
  const mid = (r: [Valore, Valore]) => r[0] == null || r[1] == null ? null : (r[0] + r[1]) / 2;
  const impiego = tip[1] != null && quota[0] != null && quota[1] != null && fin[0] != null;
  return <div className="mnd-hero-cols">
    <div className="mnd-col">
      <div className="mnd-sub">{t('ch_cash')}</div>
      <div className="mnd-band">
        {min != null && <i className="mnd-z is-under" style={{ left: 0, width: pct(min, max) + '%' }} />}
        {tip[0] != null && tip[1] != null && <i className="mnd-z is-accent" style={{ left: pct(tip[0], max) + '%', width: pct(tip[1] - tip[0], max) + '%' }} />}
        {oltre[0] != null && oltre[1] != null && <i className="mnd-z is-warn" style={{ left: pct(oltre[0], max) + '%', width: pct(oltre[1] - oltre[0], max) + '%' }} />}
        {oltre[1] != null && <i className="mnd-z is-warn-strong" style={{ left: pct(oltre[1], max) + '%', right: 0 }} />}
        {min != null && <i className={'mnd-mark' + (avvisi.cassa_minima_pct ? ' is-bad' : '')} style={{ left: pct(min, max) + '%' }} />}
      </div>
      <div className="mnd-band-labels" aria-hidden="true">
        <span className="is-start" style={{ left: 0 }}>0%</span>
        {min != null && <span style={{ left: pct(min, max) + '%' }}><b>{f(min)}%</b></span>}
        {mid(tip) != null && <span style={{ left: pct(mid(tip), max) + '%' }}><b>{intervallo(tip, l)}%</b></span>}
        {mid(oltre) != null && <span style={{ left: pct(mid(oltre), max) + '%' }}><b>{intervallo(oltre, l)}%</b></span>}
        <span className="is-end" style={{ left: '100%' }}>{max}%</span>
      </div>
      <div className="mnd-zones">
        <Zona colore="bad" valore={min != null ? t('below', { a: f(min) }) : '—'} titolo={t('z_min')} testo={t('z_min_d')} />
        <Zona colore="accent" valore={intervallo(tip, l) + '%'} titolo={t('z_typ')} testo={t('z_typ_d')} />
        <Zona colore="warn" valore={t('above', { a: intervallo(oltre, l) })} titolo={t('z_over')} testo={t('z_over_d')} />
      </div>
    </div>
    <div className="mnd-col">
      <div className="mnd-sub">{t('ch_deploy')}</div>
      <div className="mnd-plot">
        <Riga etichetta={t('ch_deploy_share')} valore={intervallo(quota, l) + '%'}><Tratto da={quota[0]} a={quota[1]} max={100} /></Riga>
        <Riga etichetta={t('ch_deploy_window')} valore={<>{intervallo(fin, l)}<small> {t('weeks_short')}</small></>}><Tratto da={fin[0] == null ? null : fin[0] - 1} a={fin[1] == null ? null : fin[1] - 1} max={11} cls="is-good" /></Riga>
      </div>
      {impiego && <Verifica ok>{t('ck_deploy', { a: f(tip[1]), b: politica || '—', c: numero(tip[1]! * quota[0]! / 100, l, 1), d: numero(tip[1]! * quota[1]! / 100, l, 1), e: intervallo(fin, l) })}</Verifica>}
    </div>
  </div>;
}

export function BandaDisciplina({ v, avvisi, l }: { v: Lettura; avvisi: Record<string, string>; l: Lingua }) {
  const t = (k: string, p?: Record<string, string>) => tr(l, k, p), f = (x: Valore) => numero(x, l);
  const libera = v.r('taglio_max_senza_condizioni_pct'), soglia = v.n('taglio_con_condizioni_oltre_pct'), bad = !!avvisi.taglio_max_senza_condizioni_pct;
  return <>
    <div className="mnd-sub">{t('ch_cut')}</div>
    <div className="mnd-band">
      {libera[0] != null && <i className="mnd-z is-good" style={{ left: 0, width: pct(libera[0], 100) + '%' }} />}
      {libera[0] != null && libera[1] != null && <i className="mnd-z is-good-soft" style={{ left: pct(libera[0], 100) + '%', width: pct(libera[1] - libera[0], 100) + '%' }} />}
      {libera[1] != null && soglia != null && <i className="mnd-z is-hatch" style={{ left: pct(libera[1], 100) + '%', width: pct(Math.max(0, soglia - libera[1]), 100) + '%' }} />}
      {soglia != null && <i className="mnd-z is-warn-strong" style={{ left: pct(soglia, 100) + '%', right: 0 }} />}
      {soglia != null && <i className={'mnd-mark' + (bad ? ' is-bad' : '')} style={{ left: pct(soglia, 100) + '%' }} />}
    </div>
    <div className="mnd-band-labels" aria-hidden="true">
      <span className="is-start" style={{ left: 0 }}>0%</span>
      {libera[1] != null && <span style={{ left: pct(((libera[0] ?? 0) + libera[1]) / 2, 100) + '%' }}><b>{intervallo(libera, l)}%</b></span>}
      {soglia != null && <span style={{ left: pct((soglia + 100) / 2, 100) + '%' }}><b>{t('above', { a: f(soglia) })}</b></span>}
      <span className="is-end" style={{ left: '100%' }}>100%</span>
    </div>
    <div className="mnd-zones">
      <Zona colore="good" valore={t('up_to', { a: intervallo(libera, l) })} titolo={t('z_free')} testo={t('z_free_d')} />
      <Zona colore="warn" valore={soglia != null ? t('above', { a: f(soglia) }) : '—'} titolo={t('z_big')} testo={t('z_big_d', { a: intervallo(libera, l) })} />
    </div>
    {libera[1] != null && soglia != null && <Verifica ok={!bad}>{t(bad ? 'ck_cut_bad' : 'ck_cut_ok', { a: intervallo(libera, l), b: f(soglia) })}</Verifica>}
  </>;
}

/* Profilo di guadagno e perdita a scadenza, solo forma: x = prezzo del sottostante,
   la linea tratteggiata è lo zero. */
const PAYOFF: Record<string, string> = {
  long_call_catalyst: 'M0 32 L50 32 L100 4', put_hedge: 'M0 4 L50 32 L100 32', put_spread: 'M0 14 L28 14 L58 32 L100 32',
  covered_call: 'M0 46 L58 14 L100 14', cash_secured_put: 'M0 46 L48 18 L100 18', short_premium_nudo: 'M0 48 L50 16 L100 48',
  straddle_strangle: 'M0 4 L50 34 L100 4',
};
export const CATEGORIA: Record<string, string> = {
  long_call_catalyst: 'direzionale', put_hedge: 'copertura', put_spread: 'copertura', covered_call: 'reddito',
  cash_secured_put: 'reddito', short_premium_nudo: 'rischio', straddle_strangle: 'volatilita',
};
export function Payoff({ strumento, etichetta }: { strumento: string; etichetta: string }) {
  const d = PAYOFF[strumento];
  if (!d) return null;
  return <svg className="mnd-payoff" viewBox="0 0 100 50" preserveAspectRatio="none" role="img" aria-label={etichetta}>
    <line x1="0" y1="25" x2="100" y2="25" className="mnd-payoff-zero" vectorEffect="non-scaling-stroke" />
    <path d={d} className={'mnd-payoff-line' + (strumento === 'short_premium_nudo' ? ' is-bad' : '')} vectorEffect="non-scaling-stroke" />
  </svg>;
}
