import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi, type Lingua } from '@/i18n/lingua';
import { localizePayload } from '@/lib/api-presentation';
import { leggiDetail } from '@/lib/quota';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Cpu, Play, RefreshCw, AlertCircle, Plus, X, Check } from 'lucide-react';
import {
  Bellomberg, MonteCarloResult, TickerValidation, Position
} from '@/lib/api';
import { fmtEUR as fmtEURlib, fmtPct as fmtPctlib, fmtNum as fmtNumlib } from '@/lib/format';
import { leggiNumero } from '@/lib/cassa';
import {
  classificaRiga, contaBanco, costruisciPayload, targhettaBanco, etichettaSimula
} from '@/lib/montecarlo';
import { useBox } from '@/lib/useBox';
import './dashboard-command.css';
import './montecarlo-plancia.css';

type Method = 'fhs' | 'block_bootstrap' | 'parametric_t';
type Drift  = 'zero' | 'shrinkage' | 'historical';
type Stress = 'none' | 'gfc_2008' | 'covid_2020' | 'shock_3sigma';
type ModAction = 'add' | 'remove' | 'trim';

interface DraftMod {
  id: number;
  action: ModAction;
  ticker: string;
  amount_eur: string;
  amount_pct: string;
  validation?: TickerValidation;
  validating?: boolean;
  inputLanguage?: Lingua;
}

// B-UI15: logica di formato UNICA in lib/format; qui solo la convenzione locale
// della pagina — EUR a 0 decimali, numeri nella lingua corrente. Il buco lo dichiara
// la pagina col segnaposto della lingua della UI: lib/format stampa un 'n/a' fisso.
const naOr = (v: number | null | undefined, f: (x: number) => string) =>
  v == null || !isFinite(v) ? tr('montecarlo.na') : f(v);
const fmtEUR = (v: number | null | undefined) => naOr(v, x => fmtEURlib(x, false, 0));
const fmtEUR2 = (v: number | null | undefined) => naOr(v, x => fmtEURlib(x, false, 2));
const fmtPct = (v: number | null | undefined) => naOr(v, x => fmtPctlib(x, false));
const fmtPctS = (v: number | null | undefined) => naOr(v, x => fmtPctlib(x, true));
const fmtNum = (v: number | null | undefined, dec = 2) => naOr(v, x => fmtNumlib(x, dec));
const fmtInt = (v: number | null | undefined) =>
  naOr(v, x => Math.round(x).toLocaleString(localeDi(linguaCorrente()), { useGrouping: true }));

/** Timbro d'esecuzione della simulazione: "26 LUG 2026 · 10:41". Mai inventato:
 *  se il payload non porta il timestamp, si dichiara. */
const stamp = (iso?: string) => {
  if (!iso) return tr('montecarlo.f001');
  const d = new Date(iso);
  if (isNaN(d.getTime())) return tr('montecarlo.f001');
  const p2 = (n: number) => String(n).padStart(2, '0');
  const month = d.toLocaleDateString(localeDi(linguaCorrente()), { month: 'short' }).replace('.', '').toUpperCase();
  return `${p2(d.getDate())} ${month} ${d.getFullYear()} · ${p2(d.getHours())}:${p2(d.getMinutes())}`;
};

/** Id del motore (metodo, drift, stress) -> la stessa etichetta dei select della
 *  pagina. Un id senza etichetta si mostra com'e' e si dichiara: mai un nome inventato. */
const engineIdLabel = (kind: 'method' | 'drift' | 'stress', id?: string | null): string => {
  if (!id) return tr('montecarlo.na');
  const labels: Record<string, string> = kind === 'method'
    ? { fhs: tr('montecarlo.f006'), block_bootstrap: 'Block bootstrap', parametric_t: tr('montecarlo.f007') }
    : kind === 'drift'
      ? { zero: tr('montecarlo.f008'), shrinkage: tr('montecarlo.f009'), historical: tr('montecarlo.f010') }
      : { none: tr('montecarlo.f015'), gfc_2008: 'GFC 2008', covid_2020: 'COVID 2020', shock_3sigma: 'shock −3σ' };
  return Object.prototype.hasOwnProperty.call(labels, id) ? labels[id] : tr('montecarlo.unlabelledId', { id });
};

let modIdCounter = 1;

export default function MonteCarloPage() {
  const tr = useT();
  const [horizonDays, setHorizonDays] = useState(252);
  const [nSims, setNSims] = useState(10000);
  const [lookbackYears, setLookbackYears] = useState(5);
  const [method, setMethod] = useState<Method>('fhs');
  const [drift, setDrift] = useState<Drift>('zero');
  const [stress, setStress] = useState<Stress>('none');

  const [mods, setMods] = useState<DraftMod[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [posErr, setPosErr] = useState<string | null>(null);

  const [resultRaw, setResult] = useState<MonteCarloResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ detail: string; draft?: DraftMod[] } | null>(null);
  const result = useMemo(() => localizePayload(resultRaw), [resultRaw, tr]);

  useEffect(() => {
    // Buco DICHIARATO (regola 14/07): picker vuoto muto = falso "book vuoto"
    Bellomberg.portfolio()
      .then(p => { setPosErr(null); setPositions(p.positions || []); })
      .catch((e: any) => setPosErr(leggiDetail(e?.response?.data?.detail ?? e?.message ?? e)));
  }, []);

  const ownedTickers = useMemo(
    () => Array.from(new Set(positions.map(p => p.ticker.toUpperCase()))).sort(),
    [positions]
  );

  const addMod = (action: ModAction) => {
    setMods(prev => [...prev, {
      id: modIdCounter++,
      action,
      ticker: '',
      amount_eur: '',
      amount_pct: action === 'trim' ? '20' : '',
      inputLanguage: linguaCorrente(),
    }]);
  };

  const updateMod = (id: number, patch: Partial<DraftMod>) => {
    setMods(prev => prev.map(m => m.id === id ? { ...m, ...patch } : m));
  };

  const removeMod = (id: number) => setMods(prev => prev.filter(m => m.id !== id));

  // ⚠ en-GB su <input type="number">: la virgola veniva CANCELLATA senza badInput
  // e la simulazione girava su un importo ×100 (review 01/08, audit/24 B.2).
  // Stessa cura di F7 (leggiNumero, l'ambiguo si rifiuta) — e una riga
  // illeggibile SPEGNE il tasto invece di far girare 10.000 path su un numero
  // falso.
  //
  // Il giudizio sulle righe NON vive più qui: sta in lib/montecarlo, in una
  // funzione sola da cui si deriva anche il payload (§9-unquadragies-septdecies).
  // Prima erano tre giudizi paralleli e una riga senza titolo riusciva a essere
  // «in attesa» per la testata, sana per il colore e scartata dal payload — tutto
  // insieme e in silenzio.
  // `bookVuoto`: senza di lui una riga TOGLI/RIDUCI a book non caricato
  // direbbe «senza titolo», addossando all'utente una mancanza che è
  // dell'endpoint — e la causa vera è dichiarata nel picker due centimetri
  // più in là (reperto della review).
  const ctxBanco = useMemo(() => ({ bookVuoto: ownedTickers.length === 0 }), [ownedTickers]);
  const banco = useMemo(() => contaBanco(mods, ctxBanco), [mods, ctxBanco, tr]);

  const validateMod = async (id: number, tickerRaw: string) => {
    const t = tickerRaw.trim().toUpperCase();
    if (!t) return;
    updateMod(id, { validating: true, validation: undefined });
    try {
      const v = await Bellomberg.validateTicker(t);
      updateMod(id, { validating: false, validation: v });
    } catch (e: any) {
      updateMod(id, {
        validating: false,
        validation: { ok: false, symbol: t, error: leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) }
      });
    }
  };

  const runSim = async (force: boolean = true) => {  // 202-C: il mount usa la cache backend
    if (banco.bloccanti > 0) {
      // difesa in profondità: oggi irraggiungibile (il tasto è `disabled` sulla
      // STESSA condizione letta dallo stesso render), e la review lo conferma.
      // Resta perché `runSim` non deve dipendere da chi la chiama.
      setError({ detail: '', draft: mods });
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const payload = costruisciPayload(mods, ctxBanco);
      const r = payload.length > 0
        ? await Bellomberg.portfolioMonteCarloV3({
            horizon_days: horizonDays, n_sims: nSims, lookback_years: lookbackYears,
            method, drift_mode: drift, stress,
            modifications: payload, force,
          })
        : await Bellomberg.portfolioMonteCarlo({
            horizon_days: horizonDays, n_sims: nSims, lookback_years: lookbackYears,
            method, drift_mode: drift, stress, force,
          });
      if (r.error) throw new Error(r.error);
      setResult(r);
    } catch (e: any) {
      setError({ detail: leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) });
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => { runSim(false); }, []); // 202-C: auto-run dal mount = cache, niente 10k sim gratuite // eslint-disable-line react-hooks/exhaustive-deps

  // FINESTRA VERTICALE della plancia, ricavata dai DATI (mai da costanti): deve
  // contenere il cono vero E il 99% della massa d'arrivo, cosi' le code tagliate
  // restano una briciola — e comunque contate e DICHIARATE (regola 14/07).
  const view = useMemo(() => {
    const fb = result?.fan_bands;
    if (!fb || !fb.days?.length) return null;
    let lo = Math.min(...fb.p5), hi = Math.max(...fb.p95);
    const th = result?.terminal_hist;
    if (th && th.counts.length && th.edges_eur.length === th.counts.length + 1) {
      const tot = th.counts.reduce((a, b) => a + b, 0);
      if (tot > 0) {
        let cum = 0, qlo = th.edges_eur[0], qhi = th.edges_eur[th.edges_eur.length - 1];
        for (let i = 0; i < th.counts.length; i++) {
          const before = cum; cum += th.counts[i];
          if (before < tot * 0.005 && cum >= tot * 0.005) qlo = th.edges_eur[i];
          if (before < tot * 0.995 && cum >= tot * 0.995) qhi = th.edges_eur[i + 1];
        }
        lo = Math.min(lo, qlo); hi = Math.max(hi, qhi);
      }
    }
    const pad = (hi - lo) * 0.04;
    return { lo: lo - pad, hi: hi + pad };
  }, [result?.fan_bands, result?.terminal_hist]);

  const outsideWindow = useMemo(() => {
    const th = result?.terminal_hist;
    if (!th || !view) return null;
    let out = 0, tot = 0;
    th.counts.forEach((c, i) => {
      const mid = (th.edges_eur[i] + th.edges_eur[i + 1]) / 2;
      tot += c;
      if (mid < view.lo || mid > view.hi) out += c;
    });
    return { out, tot };
  }, [result?.terminal_hist, view]);

  return (
    <div className="obsx f5p animate-fadeIn">
      <div className="asst">
        <span className="lab">// Monte Carlo</span>
        <span className="fld"><span className="k">{tr('montecarlo.f005')}</span>
          <select value={method} onChange={e => setMethod(e.target.value as Method)}>
            <option value="fhs">{tr('montecarlo.f006')}</option>
            <option value="block_bootstrap">Block bootstrap</option>
            <option value="parametric_t">{tr('montecarlo.f007')}</option>
          </select></span>
        <span className="fld"><span className="k">drift</span>
          <select value={drift} onChange={e => setDrift(e.target.value as Drift)}>
            <option value="zero">{tr('montecarlo.f008')}</option>
            <option value="shrinkage">{tr('montecarlo.f009')}</option>
            <option value="historical">{tr('montecarlo.f010')}</option>
          </select></span>
        <span className="fld"><span className="k">lookback</span>
          <select value={lookbackYears} onChange={e => setLookbackYears(parseInt(e.target.value))}>
            <option value={1}>{tr('montecarlo.f011')}</option><option value={2}>{tr('montecarlo.f012')}</option>
            <option value={5}>{tr('montecarlo.f013')}</option><option value={10}>{tr('montecarlo.f014')}</option>
          </select></span>
        <span className="fld"><span className="k">stress</span>
          <select value={stress} onChange={e => setStress(e.target.value as Stress)}>
            <option value="none">{tr('montecarlo.f015')}</option>
            <option value="gfc_2008">GFC 2008</option>
            <option value="covid_2020">COVID 2020</option>
            <option value="shock_3sigma">shock −3σ</option>
          </select></span>
        <span className="fld"><span className="k">{tr('montecarlo.f016')}</span>
          <select value={horizonDays} onChange={e => setHorizonDays(parseInt(e.target.value))}>
            <option value={21}>{tr('montecarlo.f017')}</option><option value={63}>{tr('montecarlo.f018')}</option>
            <option value={126}>{tr('montecarlo.f019')}</option><option value={252}>{tr('montecarlo.f011')}</option>
            <option value={504}>{tr('montecarlo.f012')}</option>
          </select></span>
        <span className="fld"><span className="k">{tr('montecarlo.f020')}</span>
          <select value={nSims} onChange={e => setNSims(parseInt(e.target.value))}>
            <option value={3000}>{tr('montecarlo.f021')}</option><option value={10000}>{fmtInt(10000)}</option>
            <option value={25000}>{fmtInt(25000)}</option><option value={50000}>{tr('montecarlo.f022')}</option>
          </select></span>
        <button className="go" onClick={() => runSim(true)} disabled={running || banco.bloccanti > 0}
                title={banco.bloccanti > 0
                  ? tr('montecarlo.f023') + banco.difetti.map(x => `${x.riga.ticker.trim()} — ${x.motivo}`).join(' · ')
                  : banco.inerti > 0
                    // niente accordo sbagliato («1 modifiche su 2»: era la
                    // stessa classe di «1 ILLEGGIBILI» che questo lotto chiude,
                    // reintrodotta nella stringa gemella), e il motivo si dice
                    // UNA volta invece di ripeterlo per ogni riga inerte —
                    // il ticker è vuoto per definizione, non identificherebbe
                    // nulla. Entrambi rilievi della review.
                    ? tr(banco.entrano === 1 ? 'montecarlo.dispatchOne' : 'montecarlo.dispatchMany', { sent: banco.entrano, total: banco.totale })
                      + tr(banco.inerti === 1 ? 'montecarlo.asideOne' : 'montecarlo.asideMany', { count: banco.inerti })
                      // i motivi DISTINTI: ripetere «senza titolo» tre volte è
                      // rumore, ma se una riga è inerte per il book non caricato
                      // e un'altra perché il titolo manca davvero, sono due cose
                      + Array.from(new Set(banco.inerziali.map(x => x.motivo))).join(' · ')
                    : undefined}>
          {running ? <RefreshCw size={11} className="animate-spin" /> : <Play size={11} />}
          {running ? tr('montecarlo.f024') : etichettaSimula(banco, banco.bloccanti > 0)}
        </button>
      </div>

      <div className="p3 hero">
        <span className="tick tl" /><span className="tick br" />
        <div className="p3h am">{tr('montecarlo.f025')}
          <span className="side">{targhettaBanco(banco)}</span>
          <span style={{ display: 'flex', gap: 4, marginLeft: 10 }}>
            <button onClick={() => addMod('add')} className="addb"
                    style={{ color: '#21E0A0', borderColor: 'rgba(33,224,160,.4)' }}>
              <Plus size={8} /> {tr('montecarlo.f026')}
            </button>
            <button onClick={() => addMod('remove')} className="addb"
                    style={{ fontWeight: 600, color: '#FF3D60', borderColor: 'rgba(255,61,96,.4)' }}>
              <Plus size={8} /> {tr('montecarlo.f027')}
            </button>
            <button onClick={() => addMod('trim')} className="addb"
                    style={{ color: '#29D3F2', borderColor: 'rgba(41,211,242,.4)' }}>
              <Plus size={8} /> {tr('montecarlo.f028')}
            </button>
          </span>
        </div>

        {posErr !== null && <div className="dec ko" role="alert" style={{ margin: '8px 11px' }}>
          {tr('montecarlo.portfolioUnavailable')} — {posErr || tr('montecarlo.errorMissing')}
        </div>}
        {mods.length === 0 ? (
          <div className="note" style={{ padding: '8px 11px' }}>
            {tr('montecarlo.f029')}
          </div>
        ) : (
          <div>
            {mods.map(m => {
              const g = classificaRiga(m, ctxBanco);
              const lE = leggiNumero(m.amount_eur, m.inputLanguage, linguaCorrente());
              const lP = leggiNumero(m.amount_pct, m.inputLanguage, linguaCorrente());
              const validation = localizePayload(m.validation);
              const inputHint = tr((m.inputLanguage ?? linguaCorrente()) === 'en' ? 'montecarlo.inputEnglish' : 'montecarlo.inputItalian');
              // ⚠ i rossi di campo si DERIVANO dal giudizio, non lo affiancano:
              // il rosso significa «è questo campo a spegnere SIMULA». Su una riga
              // inerte non si accende — la riga è messa da parte, il suo importo
              // non è in gioco, e un campo rosso sotto la scritta INERTE direbbe
              // due cose diverse sulla stessa riga (era il difetto di partenza).
              const inerte = g.stato === 'inerte';
              const eurKo = g.stato === 'blocca' && !!(lE && !lE.ok);
              const pctKo = g.stato === 'blocca' && !!(lP && (!lP.ok || (lP.ok && lP.valore > 100)));
              const pctMotivo = lP && !lP.ok ? lP.motivo : tr('montecarlo.f030');
              return (
              <div key={m.id} className={inerte ? 'wif inerte' : 'wif'}>
                <div className="col-span-1">
                  <span className={
                    'tagact ' +
                    (m.action === 'add'    ? 'border-emerald text-emerald bg-emerald/5'
                    : m.action === 'remove' ? 'border-crimson text-crimson bg-crimson/5'
                                            : 'border-cyan text-cyan bg-cyan/5')
                  }>{m.action === 'add' ? tr('montecarlo.f031') : m.action === 'remove' ? tr('montecarlo.f032') : tr('montecarlo.f033')}</span>
                </div>

                <div className="col-span-3">
                  {m.action === 'add' ? (
                    <input
                      type="text"
                      placeholder={tr('montecarlo.f034', { examples: 'NVDA, MC.PA' })}
                      value={m.ticker}
                      onChange={e => updateMod(m.id, { ticker: e.target.value.toUpperCase() })}
                      onBlur={e => validateMod(m.id, e.target.value)}
                      className="w-full bg-bg border border-border px-2 py-1 text-text focus:outline-none focus:border-amber"
                    />
                  ) : (
                    <select
                      value={m.ticker}
                      onChange={e => updateMod(m.id, { ticker: e.target.value })}
                      className="w-full bg-bg border border-border px-2 py-1 text-text focus:outline-none focus:border-amber"
                    >
                      <option value="">{posErr !== null ? tr('montecarlo.f035', {a: posErr || tr('montecarlo.errorMissing')}) : tr('montecarlo.f036')}</option>
                      {ownedTickers.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  )}
                </div>

                {m.action === 'add' && (
                  <div className="col-span-3">
                    <div className="flex items-center gap-1">
                      <input
                        type="text" inputMode="decimal"
                        placeholder={tr('montecarlo.f037')}
                        value={m.amount_eur}
                        aria-invalid={eurKo}
                        title={eurKo && lE && !lE.ok ? lE.motivo : inputHint}
                        onChange={e => updateMod(m.id, { amount_eur: e.target.value })}
                        className={`w-full bg-bg border px-2 py-1 focus:outline-none ${eurKo ? 'border-crimson text-crimson' : 'border-border text-text focus:border-amber'}`}
                      />
                      <span className="text-faint text-3xs">EUR</span>
                    </div>
                  </div>
                )}
                {m.action === 'remove' && (
                  <>
                    <div className="col-span-2">
                      <div className="flex items-center gap-1">
                        <input
                          type="text" inputMode="decimal"
                          placeholder="EUR"
                          value={m.amount_eur}
                          aria-invalid={eurKo}
                          title={eurKo && lE && !lE.ok ? lE.motivo : inputHint}
                          onChange={e => updateMod(m.id, { amount_eur: e.target.value, amount_pct: '' })}
                          className={`w-full bg-bg border px-2 py-1 focus:outline-none ${eurKo ? 'border-crimson text-crimson' : 'border-border text-text focus:border-amber'}`}
                        />
                        <span className="text-faint text-3xs">EUR</span>
                      </div>
                    </div>
                    <div className="col-span-1 text-center text-faint">{tr('montecarlo.f038')}</div>
                    <div className="col-span-1">
                      <div className="flex items-center gap-1">
                        <input
                          type="text" inputMode="decimal"
                          placeholder="%"
                          value={m.amount_pct}
                          aria-invalid={pctKo}
                          title={pctKo ? pctMotivo : inputHint}
                          onChange={e => updateMod(m.id, { amount_pct: e.target.value, amount_eur: '' })}
                          className={`w-full bg-bg border px-2 py-1 focus:outline-none ${pctKo ? 'border-crimson text-crimson' : 'border-border text-text focus:border-amber'}`}
                        />
                        <span className="text-faint text-3xs">%</span>
                      </div>
                    </div>
                  </>
                )}
                {m.action === 'trim' && (
                  <div className="col-span-3">
                    <div className="flex items-center gap-2">
                      <input
                        type="range" min="0" max="100" step="5"
                        value={m.amount_pct}
                        onChange={e => updateMod(m.id, { amount_pct: e.target.value })}
                        className="flex-1 accent-cyan"
                      />
                      <span className="text-cyan tabular-nums w-12 text-right">{m.amount_pct || 0}%</span>
                    </div>
                  </div>
                )}

                {/* lo slot dei messaggi: `msg` lo porta a 12px (era 8, la utility
                    text-3xs) — registro del 27/07, scelta PM 21/08. */}
                <div className="col-span-4 msg">
                  {inerte ? (
                    // il verdetto della riga viene prima di tutto: se non entra nel
                    // calcolo, un esito di validazione rimasto lì da un titolo poi
                    // cancellato parlerebbe di una riga che non c'è più.
                    <span className="inerte flex items-center gap-1">{tr('montecarlo.f039')} {g.motivo}</span>
                  ) : g.stato === 'blocca' ? (
                    // ⚠ Il motivo del BLOCCO va scritto QUI, non solo nel `title` di
                    // un tasto grigio. Su «importo mancante» e «serve un importo in
                    // EUR oppure una %» non si accende nessun campo rosso — il campo
                    // è VUOTO, non illeggibile — quindi prima la riga colpevole era
                    // indistinguibile da una sana, con SIMULA spento e nessuna
                    // spiegazione a schermo: il difetto del 21/08 rovesciato.
                    // Reperto della review, e lo slot a 12px serve anche a questo.
                    <span className="text-crimson flex items-center gap-1">
                      <AlertCircle size={9} /> {g.motivo}
                    </span>
                  ) : (<>
                    {m.action === 'add' && m.validating && (
                      <span className="text-amber flex items-center gap-1">
                        <RefreshCw size={9} className="animate-spin" /> {tr('montecarlo.f040')}
                      </span>
                    )}
                    {m.action === 'add' && validation && (
                      validation.ok ? (
                        <span className="text-emerald flex items-center gap-1">
                          <Check size={9} /> {validation.name} ({validation.currency}{' '}
                          {fmtNum(validation.last_price, 2)})
                        </span>
                      ) : (
                        <span className="text-crimson flex items-center gap-1">
                          <AlertCircle size={9} /> {tr('montecarlo.f002')}: {validation.error || tr('montecarlo.errorMissing')}
                        </span>
                      )
                    )}
                  </>)}
                </div>

                <div className="col-span-1 flex justify-end">
                  <button onClick={() => removeMod(m.id)} className="xbtn" title={tr('montecarlo.f041')}>
                    <X size={12} />
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        )}
      </div>

      {error && (
        <div className="p3 cr">
          <span className="tick tl" /><span className="tick br" />
          <div className="p3h cr">{tr('montecarlo.f042')}</div>
          <div className="dec ko" style={{ margin: '8px 11px' }}>{error.draft
            ? tr('montecarlo.f003') + contaBanco(error.draft, ctxBanco).difetti.map(x => `${x.riga.ticker.trim()}: ${x.motivo}`).join(' · ')
            : `${tr('montecarlo.f004')}: ${error.detail || tr('montecarlo.errorMissing')}`}</div>
        </div>
      )}

      {result && !error && (
        <>
          {result.nav_pre_eur != null && result.nav_post_eur != null && (
            <div className="p3">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h am">{tr('montecarlo.f043')}</div>
              <div className="rcells">
                <RCell k={tr('montecarlo.navPre')} v={fmtEUR(result.nav_pre_eur)} c="#ECF1FA" />
                <RCell k={tr('montecarlo.navPost')} v={fmtEUR(result.nav_post_eur)} c="#FFA51E" />
                <RCell k={tr('montecarlo.f044')}
                       v={(result.nav_post_eur - result.nav_pre_eur >= 0 ? '+' : '') + fmtEUR(result.nav_post_eur - result.nav_pre_eur)}
                       c={result.nav_post_eur - result.nav_pre_eur >= 0 ? '#21E0A0' : '#FF3D60'} />
              </div>
            </div>
          )}

          <div className="planc">
            {/* ═══════════ colonna strumenti ═══════════ */}
            <div className="col">
              <div className="p3 hero">
                <span className="tick tl" /><span className="tick br" />
                <div className="p3h am">{tr('montecarlo.f045')}
                  <span className="side">{stamp(result.timestamp)}</span></div>
                <div style={{ padding: '9px 12px 10px' }}>
                  <div className="big num">{fmtEUR2(result.base_nav_eur)}</div>
                  <div className="sub">
                    {tr('montecarlo.f046')} {result.n_assets} {tr('montecarlo.f047')}
                  </div>
                  <div className="statline">
                    <span><i>{tr('montecarlo.f048')}</i><b className="num" style={{ fontWeight: 600, color: result.expected_return_pct >= 0 ? '#21E0A0' : '#FF3D60' }}>{fmtPctS(result.expected_return_pct)}</b></span>
                    <span><i>{tr('montecarlo.f049')}</i><b className="num" style={{ fontWeight: 600, color: result.median_return_pct >= 0 ? '#21E0A0' : '#FF3D60' }}>{fmtPctS(result.median_return_pct)}</b></span>
                    <span><i>{tr('montecarlo.f050')}</i><b className="num" style={{ color: '#29D3F2' }}>{fmtPct(result.stdev_pct)}</b></span>
                    <span><i>sharpe</i><b className="num" style={{ color: '#FFA51E' }}>{fmtNum(result.sharpe_simulated, 2)}</b></span>
                  </div>
                </div>
              </div>

              <div className="p3 cr">
                <span className="tick tl" /><span className="tick br" />
                <div className="p3h cr">{tr('montecarlo.f051')} <span className="side">{tr('montecarlo.f052')} {result.horizon_days}{tr('montecarlo.f053')}</span></div>
                <div style={{ padding: '9px 12px 10px' }}>
                  <div className="tk">{tr('montecarlo.f054')}</div>
                  <div className="tesi num" style={{ fontWeight: 600, color: result.median_return_pct >= 0 ? '#21E0A0' : '#FF3D60' }}>
                    {fmtPctS(result.median_return_pct)}
                  </div>
                  <div className="sub num">{fmtEUR(result.percentiles_eur?.p50)} {tr('montecarlo.f055')}</div>
                  <div className="hr" />
                  <div className="tk">{tr('montecarlo.f056')}</div>
                  <div className="tesi num" style={{ fontWeight: 600, color: '#FF3D60' }}>{fmtPct(result.max_drawdown_median_pct)}</div>
                  <div className="sub">
                    {tr('montecarlo.f057')} <b>{tr('montecarlo.f058')}</b> {tr('montecarlo.f059')}
                  </div>
                </div>
              </div>

              <div className="p3 cr" style={{ flex: 1, minHeight: 120 }}>
                <span className="tick tl" /><span className="tick br" />
                <div className="p3h cr">{tr('montecarlo.f060')} <span className="side">{tr('montecarlo.f061')} {fmtInt(result.n_sims)} {tr('montecarlo.f062')}</span></div>
                <OddsChart r={result} />
              </div>
            </div>

            {/* ═══════════ scope ═══════════ */}
            <ScopePanel r={result} view={view} />

            {/* ═══════════ arrivo ═══════════ */}
            <div className="col">
              <MarginalPanel r={result} view={view} outside={outsideWindow} />
              <div className="p3">
                <span className="tick tl" /><span className="tick br" />
                <div className="p3h">{tr('montecarlo.f063')} <span className="side">{result.horizon_days}{tr('montecarlo.f064')} {result.horizon_years}Y</span></div>
                <table>
                  <thead><tr><th>Percentile</th><th>{tr('montecarlo.f065')}</th><th>{tr('montecarlo.f066')}</th></tr></thead>
                  <tbody>
                    {(['p95', 'p90', 'p75', 'p50', 'p25', 'p10', 'p5'] as const).map(k => {
                      const ratio = result.percentiles_ratio?.[k];
                      const d = ratio != null ? (ratio - 1) * 100 : null;
                      return (
                        <tr key={k} className={k === 'p50' ? 'med' : undefined}>
                          <td>{k.toUpperCase()}</td>
                          <td className="num" style={{ color: '#ECF1FA' }}>{fmtEUR(result.percentiles_eur?.[k])}</td>
                          <td className="num" style={{ fontWeight: 600, color: d == null ? '#8D9FC4' : d < 0 ? '#FF3D60' : '#21E0A0' }}>
                            {d == null ? tr('montecarlo.na') : fmtPctS(d)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ═══════════ fascia bassa ═══════════ */}
          <div className="bottom">
            <div className="p3">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h">{tr('montecarlo.f067')}
                <span className="side">{tr('montecarlo.f052')} {result.horizon_years}{tr('montecarlo.f068')}</span></div>
              <div className="rcells">
                <RCell k="VaR 95%" v={fmtPct(result.var_95_pct)} s={tr('montecarlo.f069')} />
                <RCell k="VaR 99%" v={fmtPct(result.var_99_pct)} s={tr('montecarlo.f070')} />
                <RCell k="CF-VaR 99%" v={fmtPct(result.var_99_cornish_fisher_pct)} s={tr('montecarlo.f071')} />
                <RCell k="ES 95%" v={fmtPct(result.es_95_pct)} s={tr('montecarlo.f072', {a: result.es_95_eur != null ? ` · ${fmtEUR(result.es_95_eur)}` : ''})} />
                <RCell k="ES 99%" v={fmtPct(result.es_99_pct)} s={tr('montecarlo.f073', {a: result.es_99_eur != null ? ` · ${fmtEUR(result.es_99_eur)}` : ''})} />
                <RCell k="Max DD p5" v={fmtPct(result.max_drawdown_p5_pct)} s={tr('montecarlo.f074')} />
              </div>
            </div>

            <div className="p3 cr">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h cr">{tr('montecarlo.f075')} <span className="side">MAX DRAWDOWN</span></div>
              <DepthChart r={result} />
            </div>

            <div className="p3">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h">{tr('montecarlo.f076')}</div>
              <div className="notes">
                <div className="note">
                  <b>{tr('montecarlo.f077')}</b> {result.method_description || engineIdLabel('method', result.method)} · drift{' '}
                  {engineIdLabel('drift', result.drift_mode || drift)} · stress {engineIdLabel('stress', result.stress_scenario || stress)} ·{' '}
                  {fmtInt(result.n_sims)} {tr('montecarlo.f078')} {result.lookback_years}Y ·{' '}
                  {tr('montecarlo.f079')} {result.lookback_days_calibration} {tr('montecarlo.f080')}
                </div>
                {result.calibration_note && (
                  <div className="dec"><b>{tr('montecarlo.f081')}</b> {result.calibration_note}</div>
                )}
                {result.returns_basis && (
                  <div className="note"><b>{tr('montecarlo.f082')}</b> {result.returns_basis}</div>
                )}
                {result.stress_fallback && (
                  <div className="dec ko">
                    <b>{tr('montecarlo.f083')}</b> {tr('montecarlo.f084')} {engineIdLabel('stress', result.stress_requested)}{tr('montecarlo.f085')}
                    {result.stress_meta?.fallback_reason ? ` — ${result.stress_meta.fallback_reason}` : ''}
                  </div>
                )}
                {result.stress_meta?.window_loss_pct != null && (
                  <div className="note">
                    <b>{tr('montecarlo.f086')}</b> {fmtPct(result.stress_meta.window_loss_pct)} {tr('montecarlo.f087')}{' '}
                    {result.stress_meta.replaced_days} {tr('montecarlo.f088')}{fmtEUR(result.stress_meta.window_loss_eur)})
                    {result.stress_meta.proxied && Object.keys(result.stress_meta.proxied).length > 0 && (
                      <> {tr('montecarlo.f089')} {Object.entries(result.stress_meta.proxied).map(([t, p]) => `${t} (${p})`).join('; ')}</>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          {result.weights_pre && result.weights_post && (
            <div className="p3">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h">{tr('montecarlo.f090')}</div>
              <table>
                <thead><tr><th>Ticker</th><th>{tr('montecarlo.f091')}</th><th>{tr('montecarlo.f092')}</th><th>{tr('montecarlo.f093')}</th></tr></thead>
                <tbody>
                  {Array.from(new Set([
                    ...Object.keys(result.weights_pre),
                    ...Object.keys(result.weights_post),
                  ])).sort().map(t => {
                    const pre = result.weights_pre![t] ?? 0;
                    const post = result.weights_post![t] ?? 0;
                    const delta = post - pre;
                    return (
                      <tr key={t}>
                        <td>{t}</td>
                        <td className="num" style={{ color: '#8D9FC4' }}>{fmtNum(pre * 100, 2)}%</td>
                        <td className="num" style={{ color: '#ECF1FA' }}>{fmtNum(post * 100, 2)}%</td>
                        <td className="num" style={{ fontWeight: 600, color: delta > 0 ? '#21E0A0' : delta < 0 ? '#FF3D60' : '#8D9FC4' }}>
                          {delta > 0 ? '+' : ''}{fmtNum(delta * 100, 2)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {result.skipped_modifications && result.skipped_modifications.length > 0 && (
            <div className="p3 cr">
              <span className="tick tl" /><span className="tick br" />
              <div className="p3h cr">{tr('montecarlo.f094')}</div>
              <div className="notes">
                {result.skipped_modifications.map((s, i) => (
                  <div key={i} className="note">
                    <b style={{ fontWeight: 600, color: '#FF3D60' }}>{s.ticker || tr('montecarlo.f095')}</b>: {s.reason}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════
   STRUMENTI DELLA PLANCIA (F5 v3) — tutti su dati VERI del
   payload e tutti INTERATTIVI (regola PM 26/07): niente valori
   interpolati, niente riempimenti inventati, e dove il campo
   manca il buco si DICHIARA invece di sparire.
   Gli SVG sono misurati in PIXEL REALI (ResizeObserver): mai
   preserveAspectRatio="none", che stirerebbe tutte le scritte.
   ════════════════════════════════════════════════════════════ */

const AM = '#FFA51E', CY = '#29D3F2', CR = '#FF3D60', EM = '#21E0A0';
const TXT = '#ECF1FA', MUT = '#8D9FC4', FNT = '#8D9FC4', DIM = '#73829F';
// GRD faceva DUE mestieri: il tratto della griglia E le etichette d'asse.
// Un colore da griglia (1,62:1) su un'etichetta e' illeggibile, ma schiarire
// la griglia le darebbe piu' peso del dato. Quindi due costanti.
const GRD = '#2C3752';          // TRATTO della griglia — invariato
/* #8D9FC4 e non #73829F: le tacche d'asse sono ETICHETTE e stanno sul gradino
   etichetta, non su quello della nota. A 10px 400 sul fondo del grafico il
   grigio-nota misurava 3,89-4,10:1 con nominale che passava (la firma di F3:
   il tratto sottile non arriva mai al colore pieno). Cura di gradino, non di
   gusto — stessa lezione del lotto (F24). */
const AXT = '#8D9FC4';          // TESTO delle etichette d'asse
const MONO = "'JetBrains Mono', monospace";
const PKEYS = ['p95', 'p90', 'p75', 'p50', 'p25', 'p10', 'p5'] as const;
type PKey = typeof PKEYS[number];

/* useBox (misura in pixel reali per gli SVG) e' passato in lib/useBox.ts il 26/07:
   stessa identica implementazione, ora condivisa con F3 (Opus 5). */

/** campo stellare deterministico: seed fisso, stesse stelle a ogni render */
function starfield(n: number) {
  let s = 1806;
  const rnd = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  return Array.from({ length: n }, () => ({ x: rnd(), y: rnd(), r: 0.4 + rnd() * 0.7, o: 0.08 + rnd() * 0.30 }));
}
const STARS = starfield(190);

function niceTicks(lo: number, hi: number, target = 5): number[] {
  if (!(hi > lo)) return [];
  const raw = (hi - lo) / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}
const eurK = (v: number) => fmtInt(Math.round(v / 1000)) + 'k';

/* ─────────────── SCOPE DI TRAIETTORIA ─────────────── */
function ScopePanel({ r, view }: { r: MonteCarloResult; view: { lo: number; hi: number } | null }) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [pinIdx, setPinIdx] = useState<number | null>(null);
  const fb = r.fan_bands;

  // il riquadro di lettura ESISTE solo mentre punti (o se l'hai fissato con un
  // click): fuori da li' il grafico resta libero, non coperto (richiesta PM 26/07)
  const idx = hoverIdx ?? pinIdx;
  const day = fb && idx != null ? fb.days[idx] : null;

  return (
    <div className="p3 hero scope">
      <span className="tick tl" /><span className="tick tr" />
      <span className="tick bl" /><span className="tick br" />
      <div className="p3h am">{tr('montecarlo.f096')}
        <span className="side">
          {fb ? tr('montecarlo.f097', {a: fb.days.length, b: r.sample_paths?.length ?? 0, c: fmtInt(r.n_sims)}) : tr('montecarlo.f098')}
          {' · '}{stamp(r.timestamp)}
        </span>
      </div>

      {!fb || !fb.days?.length || !view ? (
        <div className="dec ko" style={{ margin: '10px 11px' }}>
          {tr('montecarlo.f099')} <b>{tr('montecarlo.fanBandsName')}</b> {tr('montecarlo.f100', { field: 'fan_bands' })}
        </div>
      ) : (
        <div className="scopewrap" ref={ref}>
          <ScopeSvg r={r} view={view} w={box.w} h={box.h}
                    idx={idx} onHover={setHoverIdx}
                    onPick={i => setPinIdx(p => (p != null ? null : i))} />
          {idx != null && day != null && (
            // il riquadro salta dall'altro lato quando punti a destra, cosi' non
            // copre mai le etichette dei percentili a scadenza
            <div className={'readout' + (idx / Math.max(1, fb.days.length - 1) > 0.55 ? ' sx' : '')}>
              <div className="rhead">
                {tr('montecarlo.f101')} {day} / {fb.days[fb.days.length - 1]}
                {pinIdx != null && <span className="pin">{tr('montecarlo.f102')}</span>}
              </div>
              {PKEYS.map(k => {
                const v = fb[k][idx];
                const d = (v / r.base_nav_eur - 1) * 100;
                return (
                  <div className={'rrow' + (k === 'p50' ? ' med' : '')} key={k}>
                    <span className="rk">{k.toUpperCase()}</span>
                    <span className="rv num">{fmtEUR(v)}</span>
                    <span className="rd num" style={{ color: d < 0 ? CR : EM }}>{fmtPctS(d)}</span>
                  </div>
                );
              })}
              <div className="rfoot">{pinIdx != null ? tr('montecarlo.f103') : tr('montecarlo.f104')}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ScopeSvg({ r, view, w, h, idx, onHover, onPick }: {
  r: MonteCarloResult; view: { lo: number; hi: number }; w: number; h: number;
  idx: number | null; onHover: (i: number | null) => void; onPick: (i: number | null) => void;
}) {
  const tr = useT();
  const fb = r.fan_bands!;
  if (w < 80 || h < 80) return <svg />;
  const L = 66, R = w - 104, T = 46, B = h - 42;
  const d0 = fb.days[0], d1 = fb.days[fb.days.length - 1];
  const y = (v: number) => B - ((v - view.lo) / (view.hi - view.lo)) * (B - T);
  const x = (d: number) => L + ((d - d0) / Math.max(1, d1 - d0)) * (R - L);
  const line = (a: number[]) => fb.days.map((d, i) => `${i ? 'L' : 'M'}${x(d).toFixed(1)} ${y(a[i]).toFixed(1)}`).join(' ');
  const band = (lo: number[], hi: number[]) =>
    fb.days.map((d, i) => `${i ? 'L' : 'M'}${x(d).toFixed(1)} ${y(hi[i]).toFixed(1)}`).join(' ') +
    fb.days.map((_, i) => {
      const j = fb.days.length - 1 - i;
      return `L${x(fb.days[j]).toFixed(1)} ${y(lo[j]).toFixed(1)}`;
    }).join(' ') + 'Z';

  const nav = r.base_nav_eur;
  const paths = r.sample_paths || [];
  const pdays = r.sample_paths_days;
  const yTicks = niceTicks(view.lo, view.hi, Math.max(3, Math.round((B - T) / 64)));
  const nxt = 6;
  const xTicks = Array.from({ length: nxt }, (_, i) => Math.round(d0 + ((d1 - d0) * (i + 1)) / nxt));
  const tails: [number | undefined, string][] = [
    [r.var_95_pct, 'VaR 95'], [r.es_95_pct, 'ES 95'], [r.es_99_pct, 'ES 99'],
  ];
  const legend: [string, string, number][] = [
    ['p5–p95', CY, 0.09], ['p10–p90', CY, 0.13], ['p25–p75', AM, 0.16],
  ];

  // l'indice lo calcola l'EVENTO, non una variabile di stato: cosi' il click non
  // dipende da una chiusura stantia (il "fissa la lettura" non teneva per questo)
  const idxFromEvent = (e: React.MouseEvent<SVGSVGElement>): number | null => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < L || px > R) return null;
    const dd = d0 + ((px - L) / Math.max(1, R - L)) * (d1 - d0);
    let best = 0, bd = Infinity;
    fb.days.forEach((v, i) => { const t = Math.abs(v - dd); if (t < bd) { bd = t; best = i; } });
    return best;
  };

  return (
    <svg width={w} height={h}
         onMouseMove={e => onHover(idxFromEvent(e))}
         onMouseLeave={() => onHover(null)}
         onClick={e => onPick(idxFromEvent(e))}
         style={{ cursor: 'crosshair' }}>
      <defs>
        <clipPath id="f5plot"><rect x={L} y={T} width={Math.max(0, R - L)} height={Math.max(0, B - T)} /></clipPath>
      </defs>

      <g clipPath="url(#f5plot)">
        {STARS.map((s, i) => (
          <circle key={i} cx={(L + s.x * (R - L)).toFixed(1)} cy={(T + s.y * (B - T)).toFixed(1)}
                  r={s.r} fill="#BFD4FF" opacity={s.o} />
        ))}
        {[0.22, 0.46, 0.72, 1.0].map((f, i) => (
          <ellipse key={i} cx={L} cy={y(nav)} rx={((R - L) * f).toFixed(1)} ry={((B - T) * f * 0.62).toFixed(1)}
                   fill="none" stroke="#1B4463" strokeWidth={0.7} opacity={0.7} strokeDasharray="1,6" />
        ))}
      </g>

      {yTicks.map(v => (
        <g key={v}>
          <line x1={L} x2={R} y1={y(v)} y2={y(v)} stroke="#131B2E" strokeWidth={0.7} />
          <text x={L - 9} y={y(v) + 3.5} fontSize={10} fill={AXT} textAnchor="end" fontFamily={MONO}>{eurK(v)}</text>
        </g>
      ))}
      {xTicks.map(d => (
        <g key={d}>
          <line x1={x(d)} x2={x(d)} y1={T} y2={B} stroke="#101728" strokeWidth={0.6} />
          <line x1={x(d)} x2={x(d)} y1={B} y2={B + 4} stroke={GRD} strokeWidth={0.8} />
          <text x={x(d)} y={B + 15} fontSize={10} fill={AXT} textAnchor="middle" fontFamily={MONO}>{d}</text>
        </g>
      ))}
      {/* AXT e non DIM: a 10px 400 il grigio-nota misurava 4,45-4,49 (un pelo
          sotto soglia) — le didascalie d'asse sono etichette, gradino etichetta */}
      <text x={(L + R) / 2} y={B + 30} fontSize={10} fill={AXT} textAnchor="middle"
            letterSpacing=".22em" fontFamily={MONO}>{tr('montecarlo.f105')}</text>
      <text transform={`translate(15 ${(T + B) / 2}) rotate(-90)`} fontSize={10} fill={AXT}
            textAnchor="middle" letterSpacing=".22em" fontFamily={MONO}>{tr('montecarlo.f106')}</text>

      <g clipPath="url(#f5plot)">
        <path d={band(fb.p5, fb.p95)} fill="rgba(41,211,242,.09)" />
        <path d={band(fb.p10, fb.p90)} fill="rgba(41,211,242,.13)" />
        <path d={band(fb.p25, fb.p75)} fill="rgba(255,165,30,.16)" />
        <path d={line(fb.p5)} fill="none" stroke="#1E4C63" strokeWidth={0.9} />
        <path d={line(fb.p95)} fill="none" stroke="#1E4C63" strokeWidth={0.9} />
        <path d={line(fb.p25)} fill="none" stroke="rgba(255,165,30,.45)" strokeWidth={0.9} />
        <path d={line(fb.p75)} fill="none" stroke="rgba(255,165,30,.45)" strokeWidth={0.9} />
        <path d={line(fb.p50)} fill="none" stroke={AM} strokeWidth={1.7} />

        {/* col campione fitto (200 dalla voce ponte (51)) le tracce vanno diradate,
            o coprono le bande: la gerarchia resta bande = tutte, tracce = campione */}
        {paths.map((p, i) => {
          const dd = (j: number) => (pdays && pdays[j] != null ? pdays[j] : d0 + ((d1 - d0) * j) / Math.max(1, p.length - 1));
          const dpath = p.map((v, j) => `${j ? 'L' : 'M'}${x(dd(j)).toFixed(1)} ${y(v * nav).toFixed(1)}`).join(' ');
          return <path key={i} d={dpath} fill="none" stroke="#7FC4DC"
                       strokeWidth={paths.length > 40 ? 0.45 : 0.65}
                       opacity={paths.length > 40 ? 0.11 : 0.26} />;
        })}

        <line x1={L} x2={R} y1={y(nav)} y2={y(nav)} stroke={AM} strokeWidth={1} strokeDasharray="4,4" opacity={0.85} />

        {tails.map(([v, lab]) => {
          if (v == null) return null;
          const e = nav * (1 + v / 100);
          if (e < view.lo || e > view.hi) return null;
          return (
            <g key={lab}>
              <line x1={L} x2={R} y1={y(e)} y2={y(e)} stroke={CR} strokeWidth={0.8} strokeDasharray="2,6" opacity={0.5} />
              <text x={L + 6} y={y(e) - 5} fontSize={10} fill={CR} letterSpacing=".08em" fontFamily={MONO}>
                {lab} {fmtPct(v)} · {fmtEUR(e)}
              </text>
            </g>
          );
        })}

        {/* crosshair: compare solo quando punti (o quando hai fissato la lettura) */}
        {idx != null && (
          <>
            <line x1={x(fb.days[idx])} x2={x(fb.days[idx])} y1={T} y2={B} stroke={AM} strokeWidth={0.9} opacity={0.55} />
            {PKEYS.map(k => (
              <circle key={k} cx={x(fb.days[idx])} cy={y(fb[k][idx])} r={k === 'p50' ? 2.8 : 2}
                      fill={k === 'p50' ? AM : '#8FB6D8'} stroke="#0A0F1C" strokeWidth={0.8} />
            ))}
          </>
        )}
      </g>

      {/* piastra dietro la scritta (pattern F4): ambra su banda ambra fallisce
          in NOMINALE (3,96:1 misurato) — non è questione di corpo, è il fondo.
          Larghezza dai caratteri: JetBrains Mono 12px = 7,2px + .12em. */}
      {(() => {
        const qlab = tr('montecarlo.f107', {a: fmtEUR2(nav)});
        return (
          <>
            {/* 20px di piastra per 12px di corpo: l'anello con cui il cancello
                campiona il fondo esce di ~3px dalla scatola del testo — con la
                piastra da 15 pescava le bande ambra e misurava 2,2:1. */}
            <rect x={L + 1} y={y(nav) - 21} width={qlab.length * 8.7 + 14} height={20}
                  fill="#0A0F1C" opacity={0.95} />
            <text x={L + 6} y={y(nav) - 7} fontSize={12} fill={AM} letterSpacing=".12em" fontFamily={MONO}>
              {qlab}
            </text>
          </>
        );
      })()}

      {(['p95', 'p75', 'p50', 'p25', 'p5'] as const).map(k => {
        const v = fb[k][fb[k].length - 1];
        const strong = k === 'p50';
        return (
          <g key={k}>
            <line x1={R} x2={R + 7} y1={y(v)} y2={y(v)} stroke={strong ? AM : '#3D4F8A'} strokeWidth={strong ? 1.6 : 1} />
            {/* etichetta a 9px = minuteria LEGITTIMA (annota il valore a 12px
                sotto di sé, modello .eta di F6). Gli offset -3/+12 separano i
                due corpi: a -1,5/+9,5 le scatole si toccavano — le «9
                sovrapposizioni» del cancello (audit 03/08), una per coppia. */}
            <text x={R + 11} y={y(v) - 3} fontSize={9} fill={strong ? AM : FNT} letterSpacing=".14em" fontFamily={MONO}>{k.toUpperCase()}</text>
            <text x={R + 11} y={y(v) + 12} fontSize={12} fill={strong ? TXT : MUT} fontFamily={MONO}>{fmtEUR(v)}</text>
          </g>
        );
      })}

      {/* legenda delle bande. T-22 e non T-26: con la didascalia a T-36 e due
          corpi a 10px servono >=14px fra le due baseline — a T-26 le scatole
          si toccavano del 15% (misurato, giro 2 del cancello 03/08). */}
      {legend.map(([lab, c, op], i) => (
        <g key={lab} transform={`translate(${L + i * 96} ${T - 22})`}>
          <rect x={0} y={-6} width={16} height={8} fill={c} opacity={op * 3.2} />
          <text x={21} y={1} fontSize={10} fill={FNT} letterSpacing=".1em" fontFamily={MONO}>{lab}</text>
        </g>
      ))}
      <g transform={`translate(${L + 3 * 96} ${T - 22})`}>
        <line x1={0} x2={16} y1={-2} y2={-2} stroke={AM} strokeWidth={1.7} />
        <text x={21} y={1} fontSize={10} fill={FNT} letterSpacing=".1em" fontFamily={MONO}>{tr('montecarlo.f049')}</text>
      </g>
      <g transform={`translate(${L + 4 * 96} ${T - 22})`}>
        <line x1={0} x2={16} y1={-2} y2={-2} stroke="#7FC4DC" strokeWidth={1} opacity={0.7} />
        <text x={21} y={1} fontSize={10} fill={FNT} letterSpacing=".1em" fontFamily={MONO}>
          {fmtInt(paths.length)} {tr('montecarlo.f108')} {fmtInt(r.n_sims)} {tr('montecarlo.f109')}
        </text>
      </g>
      {/* T-36 e non T-38: a 10px la riga usciva di 2px dal bordo alto (misurato) */}
      <text x={L} y={T - 36} fontSize={10} fill={AXT} letterSpacing=".14em" fontFamily={MONO}>
        {tr('montecarlo.f110')} {fmtInt(r.n_sims)} {tr('montecarlo.f111')} {fmtInt(paths.length)} {tr('montecarlo.f112')}
        {!pdays && paths.length > 0 ? tr('montecarlo.f113') : ''}
      </text>
    </svg>
  );
}

/* ─────────────── PROFILO D'ARRIVO ─────────────── */
function MarginalPanel({ r, view, outside }: {
  r: MonteCarloResult; view: { lo: number; hi: number } | null;
  outside: { out: number; tot: number } | null;
}) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const th = r.terminal_hist;
  const pctOut = outside && outside.tot > 0 ? (outside.out / outside.tot) * 100 : null;

  return (
    <div className="p3 cy marg">
      <span className="tick tl" /><span className="tick br" />
      <div className="p3h cy">{tr('montecarlo.f114')}
        <span className="side">{th ? tr('montecarlo.f115', {a: th.counts.length}) : tr('montecarlo.f116')}</span></div>
      {!th || !th.counts?.length || !view ? (
        <div className="dec ko" style={{ margin: '10px 11px' }}>
          {tr('montecarlo.f117')} <b>{tr('montecarlo.terminalHistName')}</b>{tr('montecarlo.f118', { field: 'terminal_hist' })}
        </div>
      ) : (
        <>
          <div className="margwrap" ref={ref}>
            <MarginalSvg r={r} view={view} w={box.w} h={box.h} hover={hover} onHover={setHover} />
          </div>
          <div className="margfoot">
            {hover != null && th.edges_eur[hover + 1] != null ? (
              <span className="num">
                {fmtEUR(th.edges_eur[hover])} → {fmtEUR(th.edges_eur[hover + 1])} ·{' '}
                <b style={{ color: TXT }}>{fmtInt(th.counts[hover])} {tr('montecarlo.f119')}</b> ·{' '}
                {fmtNum((th.counts[hover] / th.counts.reduce((a, b) => a + b, 0)) * 100, 2)}%
              </span>
            ) : (
              <span>{tr('montecarlo.f120')}</span>
            )}
            {pctOut != null && pctOut > 0 && (
              <span style={{ color: CR }}>
                {tr('montecarlo.f121')} {fmtInt(outside!.out)} {tr('montecarlo.f122')} {fmtInt(outside!.tot)} ({fmtNum(pctOut, 2)}%)
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function MarginalSvg({ r, view, w, h, hover, onHover }: {
  r: MonteCarloResult; view: { lo: number; hi: number }; w: number; h: number;
  hover: number | null; onHover: (i: number | null) => void;
}) {
  const th = r.terminal_hist!;
  if (w < 60 || h < 60) return <svg />;
  const L = 8, R = w - 96, T = 8, B = h - 8;
  const y = (v: number) => B - ((v - view.lo) / (view.hi - view.lo)) * (B - T);
  const mx = Math.max(...th.counts);
  const nav = r.base_nav_eur;

  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const py = e.clientY - rect.top;
    let best: number | null = null;
    th.counts.forEach((_, i) => {
      const yt = y(th.edges_eur[i + 1]), yb = y(th.edges_eur[i]);
      if (py >= yt && py <= yb) best = i;
    });
    onHover(best);
  };

  return (
    <svg width={w} height={h} onMouseMove={move} onMouseLeave={() => onHover(null)} style={{ cursor: 'crosshair' }}>
      {th.counts.map((c, i) => {
        const lo = th.edges_eur[i], hi = th.edges_eur[i + 1], mid = (lo + hi) / 2;
        if (mid < view.lo || mid > view.hi) return null;
        const yt = y(hi), yb = y(lo), bw = (c / mx) * (R - L);
        const neg = mid < nav;
        const on = hover === i;
        return (
          <rect key={i} x={L} y={yt} width={Math.max(bw, 0.6).toFixed(1)}
                height={Math.max(yb - yt - 1, 1).toFixed(1)}
                fill={neg ? CR : EM} opacity={on ? 0.95 : neg ? 0.55 : 0.5} />
        );
      })}
      <line x1={L} x2={w - 4} y1={y(nav)} y2={y(nav)} stroke={AM} strokeWidth={1} strokeDasharray="4,4" opacity={0.85} />
      {/* solo 3 etichette: il pannello e' corto e p25/p75 stanno gia' nella tabella sotto */}
      {(['p95', 'p50', 'p5'] as const).map(k => {
        const v = r.percentiles_eur?.[k];
        if (v == null) return null;
        const strong = k === 'p50';
        return (
          <g key={k}>
            <line x1={R} x2={R + 6} y1={y(v)} y2={y(v)} stroke={strong ? AM : '#3D4F8A'} strokeWidth={strong ? 1.6 : 1} />
            {/* stessa coppia del ventaglio: 9px legittimo sopra, dato a 12px
                sotto, offset separati (audit 03/08: 3 coppie sovrapposte qui) */}
            <text x={R + 10} y={y(v) - 3} fontSize={9} fill={strong ? AM : FNT} letterSpacing=".14em" fontFamily={MONO}>{k.toUpperCase()}</text>
            <text x={R + 10} y={y(v) + 12} fontSize={12} fill={strong ? TXT : MUT} fontFamily={MONO}>{fmtEUR(v)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ─────────────── QUOTE ─────────────── */
function OddsChart({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const rows: [string, number | undefined, string][] = [
    [tr('montecarlo.f123'), r.prob_negative_pct, CR],
    [tr('montecarlo.f124'), r.prob_loss_10pct, CR],
    [tr('montecarlo.f125'), r.prob_loss_20pct, CR],
    [tr('montecarlo.f126'), r.prob_gain_10pct, EM],
    [tr('montecarlo.f127'), r.prob_gain_20pct, EM],
  ];
  const w = box.w, h = box.h;
  const PADL = 11, PADR = 12;
  const bx = Math.max(120, w * 0.44), bw = Math.max(24, w - bx - PADR - 54);
  return (
    <div className="oddswrap" ref={ref}>
      {w > 40 && h > 40 && (
        <svg width={w} height={h} onMouseLeave={() => setHover(null)}>
          {rows.map(([lab, v, c], i) => {
            const rowh = (h - 10) / rows.length;
            const yy = 10 + i * rowh + rowh / 2;
            const on = hover === i;
            return (
              <g key={lab} onMouseEnter={() => setHover(i)}>
                <rect x={0} y={yy - rowh / 2} width={w} height={rowh} fill={on ? 'rgba(255,255,255,.04)' : 'transparent'} />
                <text x={PADL} y={yy + 3} fontSize={10} fill={on ? TXT : MUT} letterSpacing=".06em" fontFamily={MONO}>{lab}</text>
                <rect x={bx} y={yy - 3} width={bw} height={6} fill="rgba(26,36,64,.9)" />
                {v != null && isFinite(v) && (
                  <rect x={bx} y={yy - 3} width={(bw * v) / 100} height={6} fill={c} opacity={on ? 0.95 : 0.75} />
                )}
                <text x={w - PADR} y={yy + 4} fontSize={12} fill={v == null ? FNT : TXT} textAnchor="end" fontFamily={MONO}>
                  {v == null || !isFinite(v) ? tr('montecarlo.na') : fmtNum(v, 2) + '%'}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

/* ─────────────── PROFONDITÀ ─────────────── */
function DepthChart({ r }: { r: MonteCarloResult }) {
  const tr = useT();
  const [ref, box] = useBox<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const rows: [number | undefined, string, string, string][] = [
    [r.max_drawdown_p95_pct, 'p95', MUT, tr('montecarlo.f128')],
    [r.max_drawdown_median_pct, tr('montecarlo.f129'), AM, tr('montecarlo.f130')],
    [r.max_drawdown_p5_pct, 'p5', CR, tr('montecarlo.f131')],
  ];
  const w = box.w, h = box.h;
  const worst = Math.max(40, ...rows.map(([v]) => (v == null ? 0 : Math.abs(v))));
  const LX = 68, RX = Math.max(LX + 20, w - 84);
  const x = (v: number) => RX - (Math.abs(v) / worst) * (RX - LX);
  const rowh = (h - 40) / rows.length;
  return (
    <div className="depthwrap" ref={ref}>
      {w > 60 && h > 50 && (
        <svg width={w} height={h} onMouseLeave={() => setHover(null)}>
          <line x1={RX} x2={RX} y1={10} y2={h - 30} stroke="#1F2A46" strokeWidth={1} />
          {/* 9 e non 8: il pavimento della scala (ordine PM 27/07) vale anche
              per la minuteria d'asse. In TESTA all'asse e non al piede: al
              piede toccava l'etichetta della riga p5 (misurato: 51% di
              sovrapposizione a tutte e 5 le inquadrature). */}
          <text x={RX + 6} y={8} fontSize={9} fill={AXT} fontFamily={MONO}>0%</text>
          {rows.map(([v, lab, c, tip], i) => {
            const yy = 12 + i * rowh + rowh / 2;
            const on = hover === i;
            if (v == null) {
              return <text key={lab} x={LX} y={yy + 3} fontSize={12} fill={FNT} fontFamily={MONO}>{lab} {tr('montecarlo.na')}</text>;
            }
            return (
              <g key={lab} onMouseEnter={() => setHover(i)}>
                <rect x={0} y={yy - rowh / 2} width={w} height={rowh} fill={on ? 'rgba(255,255,255,.04)' : 'transparent'} />
                <line x1={x(v)} x2={RX} y1={yy} y2={yy} stroke={c} strokeWidth={i === 1 ? 2 : 1} opacity={on ? 1 : 0.85} />
                <line x1={x(v)} x2={x(v)} y1={yy - 5} y2={yy + 5} stroke={c} strokeWidth={1.4} />
                <text x={x(v) - 7} y={yy + 3.5} fontSize={12} fill={c} textAnchor="end" fontFamily={MONO}>{fmtPct(v)}</text>
                <text x={RX + 6} y={yy + 3.5} fontSize={10} fill={on ? TXT : MUT} letterSpacing=".14em" fontFamily={MONO}>{lab}</text>
              </g>
            );
          })}
          {/* 10 e non 12: la riga statica e' lunga e il pannello e' quello da
              310px (misurato: a 12px sbordava di 15px). AXT e non DIM: a 10px
              400 il grigio-nota mancava la soglia (4,32-4,36). */}
          <text x={11} y={h - 10} fontSize={10} fill={hover != null ? MUT : AXT} fontFamily={MONO}>
            {hover != null && rows[hover][0] != null
              ? rows[hover][3]
              : tr('montecarlo.f132')}
          </text>
        </svg>
      )}
    </div>
  );
}

function RCell({ k, v, s, c }: { k: string; v: string; s?: string; c?: string }) {
  return (
    <div className="rcell">
      <div className="k">{k}</div>
      <div className="v num" style={c ? { color: c } : undefined}>{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}
