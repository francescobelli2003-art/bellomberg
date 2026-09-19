import { useT } from '@/i18n/provider';
import { localizePayload } from '@/lib/api-presentation';
import { leggiDetail, dataIt } from '@/lib/quota';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bellomberg } from '@/lib/api';
import type { PortfolioSnapshot, AdvancedMetrics, PortfolioRisk } from '@/lib/api';
import {
  ORDINE_CHIAMATE, COSTO_CACHE, FATTORI, NOME_FATTORE,
  calibro, copertura, confrontoFinestre, scalaComune, alphaPerTitolo,
  contaRumore, regioni, scartati, ritardoGiorni, scartoInPunti, intervallo,
  n as fnum, num, perche,
} from '@/lib/fattori';
import type {
  PayloadFattori, PayloadRiconciliazione, Lancetta, RigaAlpha, Intervallo,
} from '@/lib/fattori';
import './factor-riconciliazione.css';

/* ============================================================================
   F6 FACTOR LAB — "RICONCILIAZIONE" (Opus 5, 27/07)
   Impianto A, scelto dal PM sui PNG di mockup_f6_factors, contro B (atlante
   fattoriale) e C (scomposizione settoriale).
   Spec: docs/superpowers/specs/2026-07-27-f6-factor-lab-riconciliazione-design.md

   PERIMETRO: il terminale produce QUATTRO stime del beta di portafoglio e ne
   mostra una per pagina senza dire quale. Il guardrail ne riconcilia TRE, e
   quella che questa pagina mostrava (finestra 1y) non e' fra quelle. Stessa
   cosa sull'alpha (+8,66% dal modello fattoriale contro -12,56% dal benchmark
   ufficiale) e sullo Sharpe (1,59 contro 0,38). Questa pagina le mette una
   accanto all'altra, ognuna con la sua definizione VERBATIM dal backend.

   REGISTRO — ordine PM 27/07: ISTITUZIONALE. Intestazioni nominali, mai
   interrogative; etichetta, numero, unita', fonte. Nessun paragrafo di
   commento: le note metodologiche in riga singola, prefissate.

   TUTTE LE DERIVAZIONI STANNO IN lib/fattori.ts. Qui c'e' solo il disegno.
   ========================================================================== */

// ── numeri ──────────────────────────────────────────────────────────────────
// Il formattatore di lib/fattori.ts mantiene il raggruppamento esplicito e
// segue la lingua corrente, come il formattatore condiviso.

/** un numero che non c'e' non e' un trattino: e' una frase che dice perche' */
function Cifra({ v, dec = 2, suffisso = '', motivo, segno }: {
  v: unknown; dec?: number; suffisso?: string; motivo?: string; segno?: boolean;
}) {
  const tr = useT();
  const x = num(v);
  if (x === null) return <span className="muto">{motivo || tr('factors.f001')}</span>;
  return <>{segno && x > 0 ? '+' : ''}{fnum(x, dec)}{suffisso}</>;
}

// ── il baffo di confidenza ──────────────────────────────────────────────────
// Il punto e' piccolo, l'intervallo e' l'oggetto. Un coefficiente il cui IC
// comprende lo zero e' disegnato MENTRE lo comprende: e' il modo per cui la
// resa cromatica di prima (129 celle colorate su 162, 58 con |t| < 1,96) non
// puo' essere ripetuta da questa pagina.
// ⚠ IL BAFFO E' L'OGGETTO DI QUESTA PAGINA, e la prima stesura lo chiudeva
//   dentro un attributo `title`: nessuno dei suoi numeri esisteva altrove, e
//   da tastiera erano irraggiungibili. Ora e' focalizzabile e porta la stessa
//   lettura nell'aria-label. E il tooltip chiamava «β» un alpha annualizzato
//   in percento, senza scrivere l'unita': due errori in una stringa sola.
//   Trovati dalla review avversariale.
function Baffo({ ic, limite, nome, unita }: {
  ic: Intervallo | null; limite: number; nome: string; unita: string;
}) {
  const tr = useT();
  if (!ic) return <span className="muto">{perche('coefficiente-assente')}</span>;
  if (ic.lo === null || ic.hi === null) return <span className="muto">{perche(ic.muto)}</span>;
  const p = (v: number) => Math.max(0, Math.min(100, 50 + (v / (limite * 2)) * 100));
  const a = p(ic.lo), b = p(ic.hi), c = p(ic.beta);
  const lettura = tr('factors.f002', {a: nome, b: fnum(ic.beta, 2) ?? tr('factors.f013'), c: unita})
    + tr('factors.f003', {a: fnum(ic.lo, 2) ?? tr('factors.f013'), b: unita, c: fnum(ic.hi, 2) ?? tr('factors.f013'), d: unita, e: fnum(ic.t, 2) ?? tr('factors.f013')})
    + (ic.sig ? tr('factors.f004')
      : ic.attraversaZero ? tr('factors.f005') : tr('factors.f006'));
  return (
    <div className={'baffo ' + (ic.sig ? 'sig' : 'ns')} data-strato="baffo"
         tabIndex={0} role="img" aria-label={lettura} title={lettura}>
      <span className="asse" />
      <span className="zero" style={{ left: '50%' }} />
      <span className="ic" style={{ left: a.toFixed(2) + '%', width: (b - a).toFixed(2) + '%' }} />
      <span className="pt" style={{ left: c.toFixed(2) + '%', marginLeft: -1.5 }} />
    </div>
  );
}

export default function FactorsPage() {
  const tr = useT();
  // una sorgente per stato, e ogni errore si DICHIARA: mai un buco muto
  const [facRaw, setFac] = useState<PayloadFattori | null>(null);
  const [facErr, setFacErr] = useState<string | null>(null);
  const [recRaw, setRec] = useState<PayloadRiconciliazione | null>(null);
  const [recErr, setRecErr] = useState<string | null>(null);
  const [recAtt, setRecAtt] = useState(false);
  const [fac3Raw, setFac3] = useState<PayloadFattori | null>(null);
  const [fac3Err, setFac3Err] = useState<string | null>(null);
  const [snapRaw, setSnap] = useState<PortfolioSnapshot | null>(null);
  const [snapErr, setSnapErr] = useState<string | null>(null);
  const [advRaw, setAdv] = useState<AdvancedMetrics | null>(null);
  // ⚠ Queste due mancavano: `.catch(() => setAdv(null))` faceva sparire due
  //   misure su quattro senza che nessuno dicesse che una rotta non aveva
  //   risposto — il fallback silenzioso vietato il 14/07, commesso proprio
  //   nella pagina che esiste per denunciarlo. Trovato dalla review.
  const [advErr, setAdvErr] = useState<string | null>(null);
  const [riskRaw, setRisk] = useState<PortfolioRisk | null>(null);
  const [riskErr, setRiskErr] = useState<string | null>(null);
  const [caricando, setCaricando] = useState(false);
  const [riscalda, setRiscalda] = useState(false);
  const [scelta, setScelta] = useState(0);
  const [riscaldaErr, setRiscaldaErr] = useState<string | null>(null);
  // Only explicitly authored variants are projected; the original response is retained.
  const fac = useMemo(() => localizePayload(facRaw), [facRaw, tr]);
  const rec = useMemo(() => localizePayload(recRaw), [recRaw, tr]);
  const fac3 = useMemo(() => localizePayload(fac3Raw), [fac3Raw, tr]);
  const snap = useMemo(() => localizePayload(snapRaw), [snapRaw, tr]);
  const adv = useMemo(() => localizePayload(advRaw), [advRaw, tr]);
  const risk = useMemo(() => localizePayload(riskRaw), [riskRaw, tr]);
  const vivo = useRef(true);

  useEffect(() => () => { vivo.current = false; }, []);

  const err = (e: unknown): string => {
    const x = e as { response?: { data?: { detail?: unknown } }; message?: unknown };
    return leggiDetail(x?.response?.data?.detail ?? x?.message ?? e);
  };

  /* ⚠ L'ORDINE E' MISURATO, NON SCELTO A GUSTO. Vedi ORDINE_CHIAMATE in
     lib/fattori.ts: la cache del motore fattoriale tiene UNA finestra alla
     volta e la pagina ne usa due. Qui si disegna appena arriva la 1y, si
     riempie il calibro quando arriva la riconciliazione, si prende la 3y
     gratis, e alla fine si rimette la 1y in cache per chi viene dopo. */
  // ⚠ RIENTRANZA. Il bottone diceva "RICALCOLA" per tutti gli 8 s della
  //   riconciliazione, e un secondo click apriva una SECONDA pipeline sullo
  //   stesso motore a cache singola: due giri che si rubano lo slot a vicenda,
  //   ~30 s di regressioni per niente. Il guardiano sta qui, non solo
  //   sull'attributo `disabled`. Trovato dalla review.
  const inCorsa = useRef(false);

  const carica = useCallback(async (force = false) => {
    if (inCorsa.current) return;
    inCorsa.current = true;
    setCaricando(true);
    setFacErr(null); setRecErr(null); setFac3Err(null); setSnapErr(null);
    setAdvErr(null); setRiskErr(null); setRiscaldaErr(null);

    // ⚠ Una risposta 200 puo' portare un campo `error` dentro: succede su
    //   /metrics/advanced (numpy assente, storico NAV insufficiente) e sui
    //   fattori. La prima stesura controllava `r.error` SOLO sulla 1Y.
    const buono = <T extends { error?: string }>(r: T): T => {
      if (r && r.error) throw new Error(r.error);
      return r;
    };

    // queste tre NON toccano la cache del motore fattoriale: partono subito
    Bellomberg.portfolio()
      .then(r => { if (vivo.current) setSnap(r); })
      .catch(e => { if (vivo.current) { setSnap(null); setSnapErr(err(e)); } });
    Bellomberg.metricsAdvanced()
      .then(r => { if (vivo.current) setAdv(buono(r)); })
      .catch(e => { if (vivo.current) { setAdv(null); setAdvErr(err(e)); } });
    Bellomberg.portfolioRisk()
      .then(r => { if (vivo.current) setRisk(buono(r as { error?: string } & PortfolioRisk)); })
      .catch(e => { if (vivo.current) { setRisk(null); setRiskErr(err(e)); } });

    // 1. la finestra che la pagina mostra: appena arriva, si disegna
    try {
      const r: PayloadFattori = await Bellomberg.portfolioFactors(force);
      if (r && r.error) throw new Error(r.error);
      if (vivo.current) setFac(r);
    } catch (e) {
      if (vivo.current) { setFac(null); setFacErr(err(e)); }
    }
    if (vivo.current) setCaricando(false);

    // 2. il guardrail. Costa ~8 s perche' costringe il motore alla 3y.
    if (vivo.current) setRecAtt(true);
    try {
      const r = await Bellomberg.betaReconcile();
      if (vivo.current) setRec(buono(r as { error?: string }) as typeof r);
    } catch (e) {
      if (vivo.current) { setRec(null); setRecErr(err(e)); }
    }
    if (vivo.current) setRecAtt(false);

    // 3. la 3y ora e' in cache: gratis (misurato 0,00-0,03 s)
    try {
      const r: PayloadFattori = await Bellomberg.portfolioFactorsPeriodo('3y');
      if (vivo.current) setFac3(buono(r));
    } catch (e) {
      if (vivo.current) { setFac3(null); setFac3Err(err(e)); }
    }

    // 4. la cortesia: si rimette la 1y in cache come l'avevamo trovata.
    //    ⚠ E' lavoro che il PM non ha chiesto: sta scritto nella nota CACHE.
    if (vivo.current) setRiscalda(true);
    try { buono(await Bellomberg.portfolioFactors(false)); }
    catch (e) { if (vivo.current) setRiscaldaErr(err(e)); }
    if (vivo.current) setRiscalda(false);
    inCorsa.current = false;
  }, []);

  useEffect(() => { carica(false); }, [carica]);

  // ── le derivazioni, tutte da lib/fattori.ts ───────────────────────────────
  const cal = calibro(rec, fac);
  const cop = copertura(fac, snap);
  const conf = confrontoFinestre(
    fac ? fac.portfolio_aggregate || null : null,
    fac3 ? fac3.portfolio_aggregate || null : null,
  );
  const scala = scalaComune(conf);
  const alfa = alphaPerTitolo(fac);
  const rumore = contaRumore(fac);
  const reg = regioni(fac);
  const fuori = scartati(fac);
  const ritardo = ritardoGiorni(fac ? fac.ff_data_last_date : null);

  const alphaFattoriale = num(fac && fac.portfolio_aggregate
    ? fac.portfolio_aggregate.alpha_annualized_pct : null);
  const alphaBenchmark = num(adv && (adv as { benchmark?: { alpha_annual_pct?: number } }).benchmark
    ? (adv as { benchmark?: { alpha_annual_pct?: number } }).benchmark!.alpha_annual_pct : null);
  const sharpeRisk = num(risk && risk.portfolio ? risk.portfolio.sharpe : null);
  const sharpeAdv = num(adv ? (adv as { sharpe?: number }).sharpe : null);
  // ⚠ la frase che dice DAVVERO come sono allineate le due serie: la manda il
  //   backend, e la prima stesura scriveva al suo posto "stessa data di
  //   calcolo", che e' falsa di 59 giorni.
  const allineamento = adv && typeof (adv as { benchmark_alignment?: string }).benchmark_alignment === 'string'
    ? (adv as { benchmark_alignment?: string }).benchmark_alignment! : null;
  // la nota del backend sullo Sharpe: prima viveva solo dentro un `title`,
  // cioe' era irraggiungibile da tastiera
  const notaSharpe = risk && typeof (risk as { sharpe_note?: string }).sharpe_note === 'string'
    ? (risk as { sharpe_note?: string }).sharpe_note! : null;

  const nSigAlfa = alfa.filter(r => r.ic && r.ic.sig).length;
  const limAlfa = alfa.reduce((m, r) => {
    if (!r.ic) return m;
    const lo = r.ic.lo === null ? r.ic.beta : r.ic.lo;
    const hi = r.ic.hi === null ? r.ic.beta : r.ic.hi;
    return Math.max(m, Math.abs(lo), Math.abs(hi));
  }, 1) * 1.05;

  // ── il calibro: geometria ─────────────────────────────────────────────────
  const vals = cal.lancette.map(l => l.valore);
  const lo = vals.length ? Math.min.apply(null, vals) : 0;
  const hi = vals.length ? Math.max.apply(null, vals) : 1;
  // ⚠ Con pad 1,25x le quattro lancette stavano in un terzo del righello e i
  //   due terzi restanti erano vuoti. Il righello serve a far vedere QUANTO
  //   distano fra loro: si stringe.
  const pad = Math.max((hi - lo) * 0.42, 0.02);
  const a0 = lo - pad, a1 = hi + pad;
  const pc = (v: number) => (a1 - a0 > 0 ? ((v - a0) / (a1 - a0)) * 100 : 50);
  const tacche: number[] = [];
  if (a1 > a0) {
    for (let t = Math.ceil(a0 / 0.05) * 0.05; t <= a1 + 1e-9; t += 0.05) tacche.push(t);
  }
  const QUOTE = [8, 44, 26, 62];

  // ⚠ SU UN RIGHELLO LE FRECCE PROMETTONO UNO SPOSTAMENTO SPAZIALE. Le
  //   lancette sono gia' ordinate per valore da `calibro()`, quindi l'indice
  //   E' la posizione: ←/→ seguono il righello, non l'ordine di arrivo dal
  //   backend. (Su F7 la navigazione seguiva la data e il fuoco rimbalzava.)
  // ⚠ IL ROVING TABINDEX NON BASTA: VA SPOSTATO ANCHE IL FUOCO VERO.
  //   La prima stesura cambiava solo lo stato `scelta`, quindi il tabindex si
  //   spostava ma `document.activeElement` restava sul bottone di partenza:
  //   la navigazione da tastiera era finta e la lettura numerica non seguiva.
  //   L'ha trovato prova_riconciliazione.py, non il cancello — il cancello
  //   non preme tasti.
  const bottoni = useRef<(HTMLButtonElement | null)[]>([]);
  const vai = (i: number) => {
    const j = Math.max(0, Math.min(cal.lancette.length - 1, i));
    setScelta(j);
    const b = bottoni.current[j];
    if (b) b.focus();
  };
  const tasti = (e: React.KeyboardEvent) => {
    if (!cal.lancette.length) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); vai(scelta + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); vai(scelta - 1); }
    else if (e.key === 'Home') { e.preventDefault(); vai(0); }
    else if (e.key === 'End') { e.preventDefault(); vai(cal.lancette.length - 1); }
  };
  useEffect(() => {
    if (scelta > cal.lancette.length - 1) setScelta(Math.max(0, cal.lancette.length - 1));
  }, [cal.lancette.length, scelta]);
  const attiva: Lancetta | null = cal.lancette[scelta] || null;

  return (
    <div className="f6r">

      {/* ══ TESTATA ══════════════════════════════════════════════════════ */}
      <div className="testa">
        <span className="marchio">FACTOR LAB</span>
        <span className="sotto">
          {tr('factors.f007')}
          {fac ? tr('factors.f008', {a: fac.n_holdings_analyzed ?? '?', b: (fac.period || '').toUpperCase()}) : ''}
          {fac && fac.version ? ` · ${fac.version.toUpperCase()}` : ''}
        </span>
        {/* ⚠ il bottone dice in quale delle tre fasi si trova: prima diceva
            "RICALCOLA" per tutti gli 8 s della riconciliazione, cioe' si
            dichiarava a riposo mentre lavorava */}
        <button className="bt" onClick={() => carica(true)}
                disabled={caricando || recAtt || riscalda}>
          {caricando ? tr('factors.f009')
            : recAtt ? tr('factors.f010')
              : riscalda ? tr('factors.f011')
                : tr('factors.f012')}
        </button>
        {/* ⚠ DECISIONE PM 27/07: badge in testata e si disegna. I fattori
            Kenneth French finiscono due mesi fa; non dichiararlo sarebbe il
            fallback silenzioso vietato il 14/07. */}
        <div className={'eta' + (ritardo !== null && ritardo > 90 ? ' ko' : '')}>
          <span className="n num">{ritardo === null ? tr('factors.f013') : ritardo + tr('factors.f014')}</span>
          <span>
            <span className="d">{tr('factors.f015')}</span><br />
            <span className="p">
              {fac && fac.ff_data_last_date
                ? tr('factors.f016') + dataIt(fac.ff_data_last_date)
                : perche('coefficiente-assente')}
            </span>
          </span>
        </div>
      </div>

      {facErr !== null && (
        <div className="avviso">
          {tr('factors.f017')} {facErr || tr('factors.errorMissing')}
        </div>
      )}

      {/* ══ ① IL CALIBRO ═════════════════════════════════════════════════ */}
      <div className="rq">
        <span className="sq tl" /><span className="sq br" />
        <div className="ph a">
          <h1>{tr('factors.f018')} {cal.lancette.length || tr('factors.f019')} {tr('factors.f020')}</h1>
          <span className="side">
            {cal.verdetto
              ? <>{tr('factors.f021')} <span className={cal.verdetto === 'RECONCILED' ? 'up' : 'dn'}>{cal.verdetto}</span>
                {' · SPREAD MAX '}<Cifra v={cal.spreadMax} />
                {tr('factors.f022')}<Cifra v={cal.soglia} />
                {tr('factors.f023')}<Cifra v={cal.consenso} />
                {/* ⚠ prima si stampava solo il CONTEGGIO delle fonti cadute:
                    il motivo, che il backend manda verbatim, spariva */}
                {cal.fontiCadute.length === 0
                  ? tr('factors.f024')
                  : tr('factors.f025') + cal.fontiCadute.map(([k, v]) => `${k} (${v})`).join(' · ')}</>
              : recAtt
                ? tr('factors.f026')
                : <span className="muto">{recErr || perche('riconciliazione-assente')}</span>}
          </span>
        </div>

        <div className="pb nopad">
          {cal.lancette.length === 0 ? (
            <div className="attesa">
              <div className="t">{recAtt || caricando ? tr('factors.f027') : tr('factors.f028')}</div>
              <div className="s">
                {recAtt || caricando
                  ? tr('factors.f029', {a: fnum(COSTO_CACHE.reconcile, 1) ?? tr('factors.f013')})
                  : (facErr || recErr || perche('riconciliazione-assente'))}
              </div>
            </div>
          ) : (
            <>
              {/* data-strato: soglia/banda/riga sono strati DELLO STESSO
                  strumento, sovrapposti per progetto (la banda DEVE stare
                  dentro la finestra: semantica del fix B.8) — il cancello li
                  scusa solo se condividono QUESTO contenitore, mai fra
                  strumenti diversi (deciso PM 03/08: non e' data-inerte,
                  gli strati restano visibili e misurati) */}
              <div className="calibro" role="radiogroup" aria-label={tr('factors.f030')}
                   data-strato="calibro" onKeyDown={tasti}>
                {/* ⚠ La finestra della soglia si CENTRA SUL PUNTO MEDIO di
                    [min, max] delle riconciliate, non sul consenso (mediana):
                    il guardrail giudica su max−min ≤ soglia, e «tutte le
                    lancette dentro la banda» equivale al criterio SOLO con
                    questo centro. Centrata sulla mediana, con spread in
                    (soglia/2, soglia] asimmetrico, una lancetta usciva dalla
                    banda con l'ESITO RECONCILED stampato accanto (audit/24 B.8:
                    {0,50·0,50·0,84}, soglia 0,35 — 0,84 fuori, verdetto verde). */}
                {cal.soglia !== null && (cal.consenso !== null || (cal.consensoMin !== null && cal.consensoMax !== null)) && (() => {
                  const centro = cal.consensoMin !== null && cal.consensoMax !== null
                    ? (cal.consensoMin + cal.consensoMax) / 2
                    : (cal.consenso as number);
                  return (
                    <span className="soglia" style={{
                      left: Math.max(0, pc(centro - cal.soglia / 2)).toFixed(2) + '%',
                      width: Math.max(0, Math.min(100, pc(centro + cal.soglia / 2))
                        - Math.max(0, pc(centro - cal.soglia / 2))).toFixed(2) + '%',
                    }} />
                  );
                })()}
                {cal.consensoMin !== null && cal.consensoMax !== null && (
                  <span className="banda" style={{
                    left: pc(cal.consensoMin).toFixed(2) + '%',
                    width: (pc(cal.consensoMax) - pc(cal.consensoMin)).toFixed(2) + '%',
                  }} />
                )}
                <span className="riga" />
                {tacche.map((t, i) => {
                  const p = pc(t);
                  if (p < -0.1 || p > 100.1) return null;
                  return (
                    <span key={i}>
                      <span className="tacca" style={{ left: p.toFixed(2) + '%' }} />
                      <span className="tval num" style={{ left: p.toFixed(2) + '%' }}>{fnum(t, 2)}</span>
                    </span>
                  );
                })}
                {cal.lancette.map((l, i) => {
                  const p = pc(l.valore), y = QUOTE[i % QUOTE.length];
                  // ⚠ SUL RIGHELLO RESTA SOLO IL NUMERO. La prima stesura
                  //   scriveva anche il nome della fonte sotto ogni lancetta:
                  //   con 0,770 e 0,775 a cinque millesimi di distanza,
                  //   "BOOK VS SPY" finiva addosso a "0,775" — il PM l'ha
                  //   visto per primo. I nomi stanno nelle quattro schede qui
                  //   sotto, NELLO STESSO ORDINE, e passare su una lancetta
                  //   accende la sua. Togliere e' l'unica cura che non crea
                  //   un secondo problema di spazio.
                  return (
                    <button
                      key={l.chiave}
                      ref={el => { bottoni.current[i] = el; }}
                      type="button"
                      role="radio"
                      aria-checked={i === scelta}
                      tabIndex={i === scelta ? 0 : -1}
                      onFocus={() => setScelta(i)}
                      onMouseEnter={() => setScelta(i)}
                      onClick={() => setScelta(i)}
                      aria-label={`${l.etichetta}: ${fnum(l.valore, 3)}. ${l.riconciliato ? tr('factors.f031') : tr('factors.f032')}. ${l.definizione || perche(l.definizioneMuta)}`}
                      className={'lanc' + (l.riconciliato ? '' : ' fuori') + (i === scelta ? ' on' : '')}
                      style={{ left: p.toFixed(2) + '%' }}
                    >
                      <span className="gambo" style={{ top: y + 22, height: Math.max(0, 118 - y - 22) }} />
                      <span className="testina" style={{ top: 113 }} />
                      <span className="val num" style={{ top: y }}>{fnum(l.valore, 3)}</span>
                    </button>
                  );
                })}
              </div>

              {/* ⚠ NIENTE TOOLTIP FLOTTANTE. La lettura esatta e' QUI SOTTO, per
                  tutte e quattro, sempre visibile: passare su una lancetta
                  ACCENDE la sua scheda invece di coprire quella accanto. */}
              {/* la nota del guardrail, VERBATIM: prima non era nemmeno nel
                  tipo del payload, quindi veniva buttata */}
              {cal.nota && <div className="nota" style={{ padding: '0 11px 7px' }}>{cal.nota}</div>}

              <div className="defs">
                {cal.lancette.map((l, i) => (
                  <div key={l.chiave}
                       className={(i === scelta ? 'on' : '') + (l.riconciliato ? '' : ' fuori')}>
                    <div className={'k' + (l.riconciliato ? '' : ' fuori')}>
                      {l.etichetta}{l.riconciliato ? '' : tr('factors.f033')}
                    </div>
                    <div className="v num">{fnum(l.valore, 3)}</div>
                    <div className="d">{l.definizione || perche(l.definizioneMuta)}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="fila">

        {/* ══ ② + ③ MISURE DISCORDANTI · ALPHA PER TITOLO ════════════════ */}
        <div className="rq col-1">
          <span className="sq tl" /><span className="sq br" />
          {/* ⚠ «STESSA DATA DI CALCOLO» ERA UNA BUGIA, e scritta a mano.
              I due alpha NON sono allineati: i fattori finiscono al 29/05 e la
              serie TWR arriva a ieri — 59 giorni di distanza fra gli estremi
              destri. Il backend manda la frase giusta in `benchmark_alignment`
              e la prima stesura la buttava per scriverne una falsa. */}
          <div className="ph r"><h2>{tr('factors.f034')}</h2>
            <span className="side">
              {allineamento ? tr('factors.f035') : <span className="muto">{tr('factors.f036')}</span>}
            </span></div>
          <div className="pb fisso">
            {(advErr !== null || riskErr !== null) && (
              <div className="avviso" style={{ marginBottom: 8 }}>
                {advErr !== null ? tr('factors.f037', {a: advErr || tr('factors.errorMissing')}) : ''}
                {advErr !== null && riskErr !== null ? ' · ' : ''}
                {riskErr !== null ? tr('factors.f038', {a: riskErr || tr('factors.errorMissing')}) : ''}
              </div>
            )}
            <Duello
              nome={tr('factors.f039')}
              a={{ v: alphaFattoriale, u: '%', fonte: tr('factors.f040'),
                   come: tr('factors.f041', {a: fac?.n_holdings_analyzed ?? '?', b: (fac?.period || '?').toUpperCase(), c: fac?.ff_data_last_date ? dataIt(fac.ff_data_last_date) : tr('factors.f013')}),
                   // ⚠ `portfolio_aggregate` NON porta `alpha_tstat`: verificato,
                   //   la chiave non esiste. Colorare questo numero di verde per
                   //   il solo segno e' esattamente la bugia n.6 che questa
                   //   pagina esiste per correggere — e la prima stesura la
                   //   ricommetteva a 26px, mentre 52 righe sotto applicava la
                   //   regola giusta sui 27 alpha per titolo. Trovato dalla review.
                   giudicabile: false,
                   nonGiudicabile: tr('factors.f042') }}
              b={{ v: alphaBenchmark, u: '%', fonte: tr('factors.f043'),
                   come: tr('factors.f044'),
                   // ⚠ Stessa regola del lato A, che la prima stesura applicava
                   //   a UNA sola delle due rese dello stesso alpha: il blocco
                   //   benchmark di /metrics/advanced porta beta/alpha/correl/IR/
                   //   Treynor e NESSUNA t-stat (advanced_metrics.py:152-158).
                   //   Il -12,56% rosso a 26px per il solo segno era la bugia
                   //   n.6 sul lato B (audit/24 B.7).
                   giudicabile: false,
                   nonGiudicabile: tr('factors.f045'),
                   motivoAssente: allineamento || undefined }}
              dec={3} unitaScarto=" PP"
            />
            <Duello
              nome="SHARPE RATIO"
              a={{ v: sharpeRisk, u: '', fonte: '/PORTFOLIO/RISK',
                   come: tr('factors.f046'),
                   giudicabile: false,
                   nonGiudicabile: tr('factors.f047') }}
              b={{ v: sharpeAdv, u: '', fonte: '/PORTFOLIO/METRICS/ADVANCED',
                   come: tr('factors.f048', {a: adv && (adv as {risk_free_used?: number}).risk_free_used !== undefined ? fnum((adv as {risk_free_used?: number}).risk_free_used! * 100, 2) + '%' : tr('factors.f013')}),
                   giudicabile: false,
                   nonGiudicabile: tr('factors.f047') }}
              dec={2} unitaScarto=""
            />
            {/* le due frasi che il backend manda gia' scritte, VERBATIM e per
                intero: nell'intestazione venivano troncate coi puntini */}
            {allineamento && <div className="nota" style={{ paddingTop: 6 }}>{allineamento}</div>}
            {notaSharpe && <div className="nota" style={{ paddingTop: 4 }}>{notaSharpe}</div>}
          </div>

          <div className="ph divide"><h2>{tr('factors.f049')}</h2>
            <span className="side">
              {alfa.length ? tr('factors.f050', {a: nSigAlfa, b: alfa.length}) : '—'}
            </span></div>
          <div className="pb scorre nopad" style={{ flex: '1 1 0' }}>
                {/* L'id limita il collaudo degli stati alla tabella alpha,
                    senza includere anche le tabelle della colonna accanto. */}
            <table id="tab-alpha">
              <thead><tr>
                {/* ⚠ `text-transform:uppercase` distrugge α e β: senza il
                    contenitore .sym l'intestazione dell'alpha diventava
                    «A ANN.». Trovato dalla review. */}
                <th>{tr('factors.f051')}</th><th className="n">{tr('factors.f052')}</th><th className="n">{tr('factors.f053')}</th>
                <th style={{ width: '38%' }}><span className="sym">α</span> {tr('factors.f054')}</th>
                <th className="n"><span className="sym">α</span> ann.</th><th className="n">R²</th>
              </tr></thead>
              <tbody>
                {alfa.map(r => <RigaAlfa key={r.ticker} r={r} lim={limAlfa} />)}
              </tbody>
            </table>
          </div>
        </div>

        {/* ══ ④ + REGIONI ════════════════════════════════════════════════ */}
        <div className="rq col-1">
          <span className="sq tl" /><span className="sq br" />
          <div className="ph c"><h2>{tr('factors.f055')}</h2>
            <span className="side">
              {fac3
                ? tr('factors.f056', {a: fnum(scala, 2) ?? tr('factors.f013')})
                : fac3Err !== null
                  ? <span className="muto">{tr('factors.windowUnavailable')} — {fac3Err || tr('factors.errorMissing')}</span>
                  : tr('factors.f057')}
            </span></div>
          <div className="pb scorre nopad fisso">
            <table>
              <thead><tr>
                <th>{tr('factors.f058')}</th><th className="n">1Y</th><th className="n">3Y</th>
                <th style={{ width: '34%' }}>{tr('factors.f059')}</th><th className="n">Δ</th>
              </tr></thead>
              <tbody>
                {conf.map(r => (
                  <tr key={r.chiave} className={r.fuoriScala ? 'ns' : undefined}>
                    <td className="t-et">{r.etichetta}</td>
                    <td className="n cy"><Cifra v={r.unAnno} dec={r.fuoriScala ? 2 : 3} motivo={perche('finestra-assente')} /></td>
                    <td className="n am"><Cifra v={r.treAnni} dec={r.fuoriScala ? 2 : 3} motivo={perche('finestra-assente')} /></td>
                    <td>
                      {r.fuoriScala
                        ? <span className="muto">{perche('altra-unita')}</span>
                        : (r.unAnno !== null && r.treAnni !== null)
                          ? <DueFinestre a={r.unAnno} b={r.treAnni} lim={scala} />
                          : <span className="muto">{perche('finestra-assente')}</span>}
                    </td>
                    <td className="n"><Cifra v={r.delta} dec={r.fuoriScala ? 2 : 3} motivo="—" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="ph divide"><h2>{tr('factors.f060')}</h2>
            <span className="side">
              {reg.length
                ? tr(reg.length === 1
                    ? (fuori.n === 1 ? 'factors.datasetOneSkippedOne' : 'factors.datasetOne')
                    : (fuori.n === 1 ? 'factors.datasetSkippedOne' : 'factors.f061'), {a: reg.length, b: fuori.n})
                : '—'}
            </span></div>
          <div className="pb scorre nopad" style={{ flex: '1 1 0' }}>
            <table>
              <thead><tr>
                <th>{tr('factors.f062')}</th><th className="n">{tr('factors.f063')}</th><th className="n">{tr('factors.f064')}</th>
                <th className="n">{tr('factors.f053')}</th><th className="data">{tr('factors.f065')}</th>
              </tr></thead>
              <tbody>
                {reg.map(r => (
                  <tr key={r.chiave}>
                    <td className="t-et">{r.etichetta}
                      {r.errore && <> <span className="dn">· {r.errore}</span></>}</td>
                    <td className="n"><Cifra v={r.nHolding} dec={0} /></td>
                    <td className="n"><Cifra v={r.peso} dec={1} suffisso="%" /></td>
                    <td className="n"><Cifra v={r.nObs} dec={0} /></td>
                    <td className="data t-et">{r.ultimaData ? dataIt(r.ultimaData) : <span className="muto">{tr('factors.f013')}</span>}</td>
                  </tr>
                ))}
                {/* ⚠ REGRESSIONE RIPARATA. La pagina sostituita mostrava
                    «N skipped» e un pannello con i motivi; la prima stesura di
                    questa buttava sia `n_holdings_skipped` sia
                    `skipped_detail`, cioe' un motivo che il backend consegna
                    gia' scritto. Trovato dalla review. */}
                {fuori.n > 0 && (
                  <tr><td colSpan={5} style={{ paddingTop: 7 }}>
                    <span className="dn" style={{ fontWeight: 600 }}>
                      {fuori.n} {fuori.n === 1 ? tr('factors.f066') : tr('factors.f067')} {tr('factors.f068')}
                    </span>
                    {fuori.righe.length === 0 && (
                      <> — <span className="muto">{tr('factors.f069')}</span></>
                    )}
                  </td></tr>
                )}
                {fuori.righe.map(s => (
                  <tr key={s.ticker} className="ns">
                    <td>{s.ticker}</td>
                    <td className="n" />
                    <td className="n"><Cifra v={s.peso} dec={1} suffisso="%" /></td>
                    <td colSpan={2}>{s.motivo}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* ══ ⑤ BASE DI CALCOLO ══════════════════════════════════════════ */}
        <div className="rq col-base">
          <span className="sq tl" /><span className="sq br" />
          <div className="ph v"><h2>{tr('factors.f070')}</h2>
            <span className="side">{tr('factors.f071')}</span></div>
          <div className="pb scorre">
            <div id="cop-dichiarata" className="grande num t-et">
              <Cifra v={cop.dichiarata} dec={2} suffisso="%" />
            </div>
            <div className="sotto-grande">{tr('factors.f072')}</div>
            <div style={{ height: 14 }} />
            {/* ⚠ la coverage vera sul NAV e' il PRODOTTO delle due frazioni,
                non il solo investito/NAV: v. lib/fattori.ts.
                Gli id servono al collaudo degli stati, che deve poter
                interrogare QUESTO numero e non il testo di tutto il pannello. */}
            <div id="cop-effettiva" className="grande num cy">
              <Cifra v={cop.effettiva} dec={2} suffisso="%" motivo={perche(cop.effettivaMuta || cop.muto)} />
            </div>
            <div className="sotto-grande">{tr('factors.f073')}</div>
            <div style={{ height: 12 }} />
            <table>
              <tbody>
                <tr><td className="t-et">{tr('factors.f074')}</td>
                  <td className="n"><Cifra v={cop.investito} suffisso=" €" motivo={perche(cop.muto)} /></td></tr>
                <tr><td className="t-et">{tr('factors.f075')}</td>
                  <td className="n"><Cifra v={cop.cassa} suffisso=" €" motivo={perche(cop.muto)} /></td></tr>
                <tr><td className="t-et">NAV</td>
                  <td className="n"><Cifra v={cop.nav} suffisso=" €" motivo={perche(cop.muto)} /></td></tr>
                <tr><td className="t-et">{tr('factors.f076')}</td>
                  <td className="n"><Cifra v={cop.cassaPct} suffisso="%" motivo={perche(cop.muto)} /></td></tr>
                <tr><td className="t-et">{tr('factors.f077')}</td>
                  <td className="n"><Cifra v={cop.investitoSuNav} suffisso="%" motivo={perche(cop.muto)} /></td></tr>
              </tbody>
            </table>
            {snapErr !== null && <div className="nota" style={{ marginTop: 8 }}><span className="dn">{tr('factors.portfolioUnavailable')} — {snapErr || tr('factors.errorMissing')}</span></div>}
            {/* ⚠ il riquadro si intitola «denominatore dichiarato» e ignorava
                fx_incomplete e stale_positions: un NAV con cambi mancanti
                scritto in euro senza una parola. Trovato dalla review. */}
            {cop.avvisi.map((a, i) => (
              <div key={i} className="nota" style={{ marginTop: 6 }}><span className="dn">NAV — {a}</span></div>
            ))}

            {/* ⚠ La prima stesura metteva qui tre paragrafi. Il PM: «tantissimi
                commenti che confondono le cose importanti da guardare». Le
                stesse informazioni sono cifre: stanno in tabella. */}
            <div className="ph divide" style={{ margin: '12px -11px 0' }}>
              <h2>{tr('factors.f078')}</h2>
              <span className="side">{fac ? <>{rumore.celle} {tr('factors.f079')} <span className="sym">β</span></> : '—'}</span>
            </div>
            {/* ⚠ Senza il modello fattoriale questa tabella scriveva «0 / 0» e
                «In saturazione 0»: lo zero al posto del dato assente, vietato
                il 14/07 — nella pagina che esiste per denunciarlo. */}
            {!fac ? (
              <div className="nota" style={{ marginTop: 6 }}>
                <span className="muto">{facErr || perche('coefficiente-assente')}</span>
              </div>
            ) : (
              <table style={{ marginTop: 6 }}>
                <tbody>
                  <tr><td className="t-et">{tr('factors.f080')}</td>
                    <td className="n">{rumore.significative} / {rumore.celle}</td></tr>
                  <tr><td className="t-et"><span className="sym">α</span> {tr('factors.f081')}</td>
                    <td className="n">
                      <Cifra v={fac.n_alpha_significant_5pct} dec={0} /> / <Cifra v={fac.n_holdings_analyzed} dec={0} />
                    </td></tr>
                  <tr><td className="t-et">{tr('factors.f082')}</td>
                    <td className="n"><Cifra v={num(fac.portfolio_avg_r_squared) !== null ? fac.portfolio_avg_r_squared! * 100 : null} dec={1} suffisso="%" /></td></tr>
                  <tr><td className="t-et">{tr('factors.f083')}</td>
                    <td className="n">{rumore.colorateRegolaVecchia}</td></tr>
                  <tr><td className="t-et">{tr('factors.f084')}</td>
                    <td className="n">{rumore.colorateNonSignificative}</td></tr>
                  <tr><td className="t-et">{tr('factors.f085')}<span className="sym">β</span>{tr('factors.f086')}</td>
                    <td className="n">{rumore.inSaturazione}</td></tr>
                </tbody>
              </table>
            )}

            <div className="nota sep">
              {tr('factors.f087')}{' '}
              <b className="am">~{fnum(COSTO_CACHE.totale, 1)} s</b>{tr('factors.f088')}{riscalda && <> <b className="am">{tr('factors.f089')}</b></>}
            </div>
            {riscaldaErr !== null && <div className="avviso" role="alert">
              {tr('factors.cacheRestoreFailed')} — {riscaldaErr || tr('factors.errorMissing')}
            </div>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── due misure della stessa grandezza, e la loro distanza ───────────────────
// ⚠ L'UNITA' DELLO SCARTO NON E' L'UNITA' DELLE DUE CIFRE. La distanza fra
//   +8,662% e -12,56% e' 21,22 PUNTI PERCENTUALI. Scrivere "21,22%" sarebbe
//   lo stesso errore di categoria che questa pagina esiste per denunciare.
interface LatoDuello {
  v: number | null; u: string; fonte: string; come: string;
  /** il testo integrale del backend, se piu' lungo di quel che sta in riga */
  esteso?: string;
  /** false quando la misura NON ha un test di significativita': allora il
   *  segno non e' un giudizio e il numero non si colora. Default true. */
  giudicabile?: boolean;
  /** la frase da mettere sotto una cifra non giudicabile */
  nonGiudicabile?: string;
  /** perche' la cifra non c'e', quando non c'e' */
  motivoAssente?: string;
}

function Duello({ nome, a, b, dec, unitaScarto }: {
  nome: string; a: LatoDuello; b: LatoDuello; dec: number; unitaScarto: string;
}) {
  const tr = useT();
  const sc = scartoInPunti(a.v, b.v);
  const tono = (x: LatoDuello) =>
    x.v === null ? 'muto' : x.giudicabile === false ? 't-et' : x.v >= 0 ? 'up' : 'dn';
  const lato = (x: LatoDuello, dx: boolean) => (
    <div className={'lato' + (dx ? ' dx' : '')}>
      <div className={'cifra num ' + tono(x)}>
        <Cifra v={x.v} dec={dec} suffisso={x.u} segno motivo={x.motivoAssente} />
      </div>
      <div className="fonte">{x.fonte}</div>
      <div className="come" title={x.esteso || undefined}>
        {x.come}{x.giudicabile === false && x.nonGiudicabile ? ` · ${x.nonGiudicabile}` : ''}
      </div>
    </div>
  );
  return (
    <div className="duello">
      {lato(a, false)}
      <div className="mezzo">
        <div className="et">{nome}</div>
        <div className="sc num">
          {sc === null
            ? <span className="muto">{tr('factors.f090')}</span>
            : <>SPREAD {fnum(sc, 2)}{unitaScarto}</>}
        </div>
      </div>
      {lato(b, true)}
    </div>
  );
}

function DueFinestre({ a, b, lim }: { a: number; b: number; lim: number }) {
  const p = (v: number) => Math.max(0, Math.min(100, 50 + (v / (lim * 2)) * 100));
  const p1 = p(a), p3 = p(b);
  return (
    <div className="duefin" title={`1Y ${fnum(a, 3)} · 3Y ${fnum(b, 3)}`}>
      <span className="asse" /><span className="zero" />
      <span className="arco" style={{ left: Math.min(p1, p3).toFixed(2) + '%', width: Math.abs(p3 - p1).toFixed(2) + '%' }} />
      <span className="p1" style={{ left: p1.toFixed(2) + '%' }} />
      <span className="p3" style={{ left: p3.toFixed(2) + '%' }} />
    </div>
  );
}

function RigaAlfa({ r, lim }: { r: RigaAlpha; lim: number }) {
  const tr = useT();
  const sig = !!(r.ic && r.ic.sig);
  return (
    <tr className={sig ? undefined : 'ns'}>
      <td>
        {r.ticker}
        {/* il caveat sul cambio riguarda 21 holding su 27 (83,76% del
            capitale): era raggiungibile solo col mouse */}
        {r.fxCaveat && <> <span className="fx" tabIndex={0} role="note"
                              aria-label={r.fxCaveat} title={r.fxCaveat}>FX</span></>}
      </td>
      <td className="n t-et"><Cifra v={r.peso} dec={1} suffisso="%" /></td>
      {/* ⚠ n_obs va reso: PURR e' stimato su 121 osservazioni contro una
          mediana di 210 e mostra il piu' grande alpha della tabella. */}
      <td className="n t-et"><Cifra v={r.nObs} dec={0} /></td>
      <td><Baffo ic={r.ic} limite={lim} nome={tr('factors.f091')} unita="%" /></td>
      <td className={'n ' + (sig ? (r.alpha !== null && r.alpha >= 0 ? 'up' : 'dn') : 'muto')}>
        <Cifra v={r.alpha} dec={1} suffisso="%" segno />
      </td>
      <td className="n t-et">
        <Cifra v={r.r2 === null ? null : r.r2 * 100} dec={0} suffisso="%" />
      </td>
    </tr>
  );
}

// riferimento usato solo per far fallire il compilatore se ORDINE_CHIAMATE
// cambia senza che questo file venga riletto: l'ordine e' una decisione
// misurata, non un dettaglio implementativo.
void ORDINE_CHIAMATE;
void intervallo;
void NOME_FATTORE;
