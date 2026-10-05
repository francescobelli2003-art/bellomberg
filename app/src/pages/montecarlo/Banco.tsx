import { useEffect, useRef } from 'react';
import { AlertCircle, Check, Play, RefreshCw, X } from 'lucide-react';
import { useT } from '@/i18n/provider';
import { linguaCorrente, type Lingua } from '@/i18n/lingua';
import { localizePayload } from '@/lib/api-presentation';
import type { MonteCarloResult, TickerValidation } from '@/lib/api';
import { leggiNumero } from '@/lib/cassa';
import { classificaRiga, type ContestoBanco, type ContoBanco } from '@/lib/montecarlo';
import { fmtNum } from './formato';

export type ModAction = 'add' | 'remove' | 'trim';
export interface DraftMod {
  id: number;
  action: ModAction;
  ticker: string;
  amount_eur: string;
  amount_pct: string;
  validation?: TickerValidation;
  validating?: boolean;
  inputLanguage?: Lingua;
}

/* Banco di prova: pannello laterale aperto su richiesta. Lo stato (righe, validazioni,
   giudizio) resta in MonteCarloPage; qui solo la presentazione. Il giudizio di ogni riga
   viene da lib/montecarlo (un solo classificatore, da cui si deriva anche il payload):
   il rosso di un campo si DERIVA da lì, non lo affianca. */
export function Banco({ tab, onTab, onClose, mods, ctx, ownedTickers, posErr, banco, running, simulaLabel, simulaTitle,
  onAdd, onUpdate, onRemove, onClear, onValidate, onRun, result }: {
  tab: 'mod' | 'pesi'; onTab: (t: 'mod' | 'pesi') => void; onClose: () => void;
  mods: DraftMod[]; ctx: ContestoBanco; ownedTickers: string[]; posErr: string | null; banco: ContoBanco;
  running: boolean; simulaLabel: string; simulaTitle?: string;
  onAdd: (a: ModAction) => void; onUpdate: (id: number, patch: Partial<DraftMod>) => void; onRemove: (id: number) => void;
  onClear: () => void; onValidate: (id: number, ticker: string) => void; onRun: () => void;
  result: MonteCarloResult | null;
}) {
  const tr = useT();
  const chiudi = useRef<HTMLButtonElement>(null);
  useEffect(() => { chiudi.current?.focus(); }, []);
  const pesi = !!(result?.weights_pre && result?.weights_post);
  const scartate = result?.skipped_modifications || [];
  const conta = (n: number, uno: Parameters<typeof tr>[0], molti: Parameters<typeof tr>[0]) => n === 1 ? tr(uno) : tr(molti, { count: n });

  return <div className="mc-drawer-root">
    <div className="mc-scrim" onClick={onClose} />
    <aside className="mc-drawer" role="dialog" aria-modal="true" aria-labelledby="mc-drawer-title"
           onKeyDown={e => { if (e.key === 'Escape') onClose(); }}>
      <div className="mc-drawer-head">
        <h2 id="mc-drawer-title">{tr('montecarlo.bancoTitle')}</h2><span className="bbn-grow" />
        <div className="bbn-seg mc-seg" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'mod'} className={tab === 'mod' ? 'is-on' : ''} onClick={() => onTab('mod')}>{tr('montecarlo.tabMods')}</button>
          <button type="button" role="tab" aria-selected={tab === 'pesi'} className={tab === 'pesi' ? 'is-on' : ''} disabled={!pesi}
                  title={pesi ? undefined : tr('montecarlo.weightsUnavailable')} onClick={() => onTab('pesi')}>{tr('montecarlo.tabWeights')}</button>
        </div>
        <button type="button" ref={chiudi} className="bbn-icon-btn" aria-label={tr('montecarlo.close')} onClick={onClose}><X aria-hidden="true" /></button>
      </div>
      <p className="mc-drawer-lead">{tr('montecarlo.bancoLead')}</p>

      {tab === 'pesi' && pesi ? <div className="mc-drawer-body"><Pesi r={result!} /></div> : <>
        <div className="mc-drawer-body">
          <div className="mc-addrow">
            <button type="button" className="mc-addb is-add" data-add="add" onClick={() => onAdd('add')}><i />{tr('montecarlo.addAdd')}</button>
            <button type="button" className="mc-addb is-remove" data-add="remove" onClick={() => onAdd('remove')}><i />{tr('montecarlo.addRemove')}</button>
            <button type="button" className="mc-addb is-trim" data-add="trim" onClick={() => onAdd('trim')}><i />{tr('montecarlo.addTrim')}</button>
          </div>
          {posErr !== null && <div className="mc-note is-bad" role="alert"><AlertCircle aria-hidden="true" />
            <span>{tr('montecarlo.portfolioUnavailable')} — {posErr || tr('montecarlo.errorMissing')}</span></div>}
          {mods.length === 0
            ? <div className="mc-dempty"><b>{tr('montecarlo.emptyMods')}</b>{tr('montecarlo.emptyModsBody')}</div>
            : mods.map(m => <Riga key={m.id} m={m} ctx={ctx} ownedTickers={ownedTickers} posErr={posErr}
                                  onUpdate={onUpdate} onRemove={onRemove} onValidate={onValidate} />)}
          {scartate.length > 0 && <div className="mc-note is-warn"><AlertCircle aria-hidden="true" />
            <span><b>{tr('montecarlo.skippedTitle')}</b> {scartate.map(s => `${s.ticker || tr('montecarlo.f095')}: ${s.reason}`).join(' · ')}</span></div>}
        </div>
        <div className="mc-drawer-foot">
          <span className="mc-foot-sum">{banco.totale === 0 ? tr('montecarlo.f140') : [
            conta(banco.entrano, 'montecarlo.readyOne', 'montecarlo.readyMany'),
            banco.bloccanti ? conta(banco.bloccanti, 'montecarlo.blockOne', 'montecarlo.blockMany') : '',
            banco.inerti ? conta(banco.inerti, 'montecarlo.inertOne', 'montecarlo.inertMany') : '',
          ].filter(Boolean).join(' · ')}</span>
          <button type="button" className="bbn-btn" disabled={mods.length === 0} onClick={onClear}>{tr('montecarlo.clear')}</button>
          <button type="button" className="bbn-btn is-primary" data-action="simulate-banco" disabled={running || banco.bloccanti > 0}
                  title={simulaTitle} onClick={onRun}>
            {running ? <RefreshCw aria-hidden="true" className="animate-spin" /> : <Play aria-hidden="true" />}{simulaLabel}</button>
        </div>
      </>}
    </aside>
  </div>;
}

function Riga({ m, ctx, ownedTickers, posErr, onUpdate, onRemove, onValidate }: {
  m: DraftMod; ctx: ContestoBanco; ownedTickers: string[]; posErr: string | null;
  onUpdate: (id: number, patch: Partial<DraftMod>) => void; onRemove: (id: number) => void; onValidate: (id: number, ticker: string) => void;
}) {
  const tr = useT();
  const g = classificaRiga(m, ctx);
  // ⚠ en-GB su <input type="number"> cancellava la virgola: campi di testo + leggiNumero,
  // e una riga illeggibile SPEGNE Simula invece di far girare le traiettorie su un numero falso.
  const lE = leggiNumero(m.amount_eur, m.inputLanguage, linguaCorrente());
  const lP = leggiNumero(m.amount_pct, m.inputLanguage, linguaCorrente());
  const validation = localizePayload(m.validation);
  const hint = tr((m.inputLanguage ?? linguaCorrente()) === 'en' ? 'montecarlo.inputEnglish' : 'montecarlo.inputItalian');
  const inerte = g.stato === 'inerte';
  const eurKo = g.stato === 'blocca' && !!(lE && !lE.ok);
  const pctKo = g.stato === 'blocca' && !!(lP && (!lP.ok || (lP.ok && lP.valore > 100)));
  const pctMotivo = lP && !lP.ok ? lP.motivo : tr('montecarlo.f138');
  const azione = { add: tr('montecarlo.actAdd'), remove: tr('montecarlo.actRemove'), trim: tr('montecarlo.actTrim') }[m.action];
  const libro = <span className="mc-sel"><select value={m.ticker} aria-label={tr('montecarlo.pickBook')} onChange={e => onUpdate(m.id, { ticker: e.target.value })}>
    <option value="">{posErr !== null ? tr('montecarlo.bookNotLoaded', { a: posErr || tr('montecarlo.errorMissing') }) : tr('montecarlo.pickBook')}</option>
    {ownedTickers.map(t => <option key={t} value={t}>{t}</option>)}
  </select></span>;
  const importo = (patch: (v: string) => Partial<DraftMod>) =>
    <input type="text" inputMode="decimal" className={'mc-inp is-eur' + (eurKo ? ' is-bad' : '')} placeholder={tr('montecarlo.amountPh')}
           value={m.amount_eur} aria-invalid={eurKo} title={eurKo && lE && !lE.ok ? lE.motivo : hint}
           onChange={e => onUpdate(m.id, patch(e.target.value))} />;

  let msg = null;
  if (inerte) msg = <span className="mc-msg is-muted">{tr('montecarlo.setAside', { why: g.motivo || '' })}</span>;
  // il motivo del BLOCCO va scritto qui, non solo nel title di un tasto grigio: un campo
  // vuoto non si accende di rosso, e senza questa riga la colpevole sembrerebbe sana
  else if (g.stato === 'blocca') msg = <span className="mc-msg is-bad"><AlertCircle aria-hidden="true" />{g.motivo}</span>;
  else if (m.action === 'add' && m.validating) msg = <span className="mc-msg is-muted"><RefreshCw aria-hidden="true" className="animate-spin" />{tr('montecarlo.validating')}</span>;
  else if (m.action === 'add' && validation) msg = validation.ok
    ? <span className="mc-msg is-good"><Check aria-hidden="true" />{validation.name} · {validation.currency} {fmtNum(validation.last_price, 2)}</span>
    : <span className="mc-msg is-bad"><AlertCircle aria-hidden="true" />{tr('montecarlo.validationFailed')}: {validation.error || tr('montecarlo.errorMissing')}</span>;
  else if (m.action === 'trim') msg = <span className="mc-msg is-muted">{tr('montecarlo.trimNote', { p: (m.amount_pct || 0) + '%' })}</span>;

  return <div className={'mc-mod' + (inerte ? ' is-inert' : '')} data-mod={m.action}>
    <span className={'mc-act is-' + m.action}>{azione}</span>
    <div className="mc-flds">
      {m.action === 'add' && <>
        <input type="text" className="mc-inp is-tk" placeholder={tr('montecarlo.tickerPh', { examples: 'NVDA, MC.PA' })} value={m.ticker}
               onChange={e => onUpdate(m.id, { ticker: e.target.value.toUpperCase() })} onBlur={e => onValidate(m.id, e.target.value)} />
        {importo(v => ({ amount_eur: v }))}<span className="mc-unit">€</span>
      </>}
      {m.action === 'remove' && <>
        {libro}{importo(v => ({ amount_eur: v, amount_pct: '' }))}<span className="mc-unit">€</span>
        <span className="mc-or">{tr('montecarlo.or')}</span>
        <input type="text" inputMode="decimal" className={'mc-inp is-pct' + (pctKo ? ' is-bad' : '')} placeholder="%" value={m.amount_pct}
               aria-invalid={pctKo} title={pctKo ? pctMotivo : hint} onChange={e => onUpdate(m.id, { amount_pct: e.target.value, amount_eur: '' })} />
        <span className="mc-unit">%</span>
      </>}
      {m.action === 'trim' && <>
        {libro}
        <span className="mc-range"><input type="range" min="0" max="100" step="5" value={m.amount_pct} aria-label={tr('montecarlo.actTrim')}
               onChange={e => onUpdate(m.id, { amount_pct: e.target.value })} /><b className="num">{m.amount_pct || 0}%</b></span>
      </>}
    </div>
    <button type="button" className="bbn-icon-btn" aria-label={tr('montecarlo.removeRow')} title={tr('montecarlo.removeRow')} onClick={() => onRemove(m.id)}><X aria-hidden="true" /></button>
    {msg && <div className="mc-msg-row">{msg}</div>}
  </div>;
}

function Pesi({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const pre = r.weights_pre!, post = r.weights_post!;
  const titoli = Array.from(new Set([...Object.keys(pre), ...Object.keys(post)])).sort();
  const max = Math.max(0.0001, ...titoli.map(t => post[t] ?? 0));
  return <table className="mc-wtab">
    <thead><tr><th>{tr('montecarlo.colTicker')}</th><th>{tr('montecarlo.colBefore')}</th><th>{tr('montecarlo.colAfter')}</th><th>{tr('montecarlo.colDiff')}</th></tr></thead>
    <tbody>{titoli.map(t => {
      const a = pre[t] ?? 0, b = post[t] ?? 0, d = b - a;
      return <tr key={t}>
        <td>{t}</td>
        <td className="num mc-muted">{fmtNum(a * 100, 2)}%</td>
        <td className="num"><span className="mc-wbar"><i style={{ width: (b / max) * 100 + '%' }} /></span>{fmtNum(b * 100, 2)}%</td>
        <td><span className={'bbn-pill ' + (d > 0 ? 'is-su' : d < 0 ? 'is-giu' : 'is-piatto')}>{d > 0 ? '+' : ''}{fmtNum(d * 100, 2)}%</span></td>
      </tr>;
    })}</tbody>
  </table>;
}
