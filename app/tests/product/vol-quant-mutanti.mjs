// Banco di mutazioni di vol-quant (10/10/2026, Opus 5.5; v2: + prima e seconda revisione, + le 24 del revisore; v3: + ri-verifica). NON fa parte di test:product (lento):
//   node tests/product/vol-quant-mutanti.mjs
// Ogni mutante si scrive su una COPIA in una cartella temporanea (mai sul file vero: altre chat e
// agenti leggono lo stesso albero); il test la carica via VOL_QUANT_LIB. Ancora unica obbligatoria
// (una seconda occorrenza muterebbe il punto sbagliato); prima il controllo della copia intatta.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const app = resolve(here, '../..');
const source = readFileSync(join(app, 'src/lib/vol-quant.ts'), 'utf8');

const MUTANTS = [
  // --- banco dell'autore (v1, ancore aggiornate alla v2)
  ['bsDelta sconta con r invece di q', 'return Math.exp(-q * T) * (', 'return Math.exp(-r * T) * ('],
  ['d1 forward senza 1/2', '(Math.log(F / K) + sigma * sigma * T / 2)', '(Math.log(F / K) + sigma * sigma * T)'],
  ['delta put forward col segno sbagliato', "return type === 'call' ? n : n - 1;", "return type === 'call' ? n : 1 - n;"],
  ['put Black-76 con d1/d2 scambiati', 'K * normCdf(-d2) - F * normCdf(-d1)', 'K * normCdf(-d1) - F * normCdf(-d2)'],
  ['parità senza capitalizzazione e^{rT}', 'x.strike + growth * ((x.cm - ce) - (x.pm - pe))', 'x.strike + ((x.cm - ce) - (x.pm - pe))'],
  ['forward = media invece della mediana', 'const rawF = median(used.map(x => fwdOf(x, 0, 0)));', 'const rawF = used.map(x => fwdOf(x, 0, 0)).reduce((a, b) => a + b, 0) / used.length;'],
  ['coppie ordinate per strike, non per vicinanza ATM', 'Math.abs(a.cm - a.pm) - Math.abs(b.cm - b.pm) || a.strike - b.strike', 'a.strike - b.strike'],
  ['r mancante non dichiarato', "if (!isNum(input.r)) { out[expiry] = { ...base, reason: 'rate_missing' }; continue; }", ''],
  ['smile: interpolazione piatta fra nodi', 'return a.w + (b.w - a.w) * (k - a.k) / (b.k - a.k);', 'return a.w + (b.w - a.w) * 0.5;'],
  ['ATMF delta-neutral invece di K = F', 'const iv = smile.sigma(0);', 'const iv = smile.sigma((smile.sigma(0) ?? 0) ** 2 * smile.T / 2);'],
  ['RR25 col segno rovesciato', 'c25 - p25 : (reasons.rr25', 'p25 - c25 : (reasons.rr25'],
  ['BF25 senza la media delle ali', '(c25 + p25) / 2 - atm', '(c25 + p25) - atm'],
  ['skew normalizzato in valore assoluto', 'rr25 / atm : (reasons.skewNorm25', 'Math.abs(rr25) / atm : (reasons.skewNorm25'],
  ['vol forward divisa per T2 invece di T2 − T1', 'fv = (w2 - w1) / (b.T - a.T)', 'fv = (w2 - w1) / b.T'],
  ['calendario senza tolleranza di rumore', '!(hi2(k, v2) < lo1(k, v1))', '!(v2 * v2 * s2.T < v1 * v1 * s1.T)'],
  ['butterfly con pesi λ scambiati', 'const l1 = (kt[i + 1] - kt[i]) / (kt[i + 1] - kt[i - 1]), l3 = (kt[i] - kt[i - 1]) / (kt[i + 1] - kt[i - 1]);',
    'const l3 = (kt[i + 1] - kt[i]) / (kt[i + 1] - kt[i - 1]), l1 = (kt[i] - kt[i - 1]) / (kt[i + 1] - kt[i - 1]);'],
  ['Durrleman senza il termine 1/4', '(wp * wp / 4) * (1 / w + 1 / 4)', '(wp * wp / 4) * (1 / w)'],
  ['strike per delta: radici multiple non dichiarate', 'if (brackets.length > 1) return', 'if (brackets.length > 99) return'],
  ['evento: base su T2 invece di T2 − T1', 'const ve = a.w - w1 - base * (a.T - T1);', 'const ve = a.w - w1 - base * a.T;'],
  ['evento annualizzato su 365', 'Math.sqrt(TRADING_DAYS * ve)', 'Math.sqrt(365 * ve)'],
  ['base pre: varianza totale invece del tasso', ': b.w / b.T) : null;', ': b.w) : null;'],
  ['diff: interpolazione lineare in vol invece che in varianza', 'return { iv: Math.sqrt((wa + (wb - wa) * (T - a.T) / (b.T - a.T)) / T), reason: null };',
    'return { iv: a.cell.iv + ((b.cell.iv as number) - a.cell.iv) * (T - a.T) / (b.T - a.T), reason: null };'],
  ['diff: estrapola oltre l\'ultima scadenza', "if (j <= 0) return { iv: null, reason: 'outside_term' };", "if (j === 0) return { iv: null, reason: 'outside_term' }; if (j < 0) return sl[sl.length - 1].cell;"],
  ['cono con T invece di √T', 'const pct = p.iv * Math.sqrt(p.T),', 'const pct = p.iv * p.T,'],
  ['normCdf: costante di Hart troncata', '0.700383064443688', '0.7003830644'],
  // --- v2 (prima revisione)
  ['cono: banda lineare invece di lognormale', 'low: fi.F * Math.exp(-pct), high: fi.F * Math.exp(pct)', 'low: fi.F - fi.F * pct, high: fi.F + fi.F * pct'],
  ['evento: utili passati accettati', "if (ed < today) return { ...out, reason: 'event_past' };", ''],
  ['gomito: massa senza il fattore 1/2', '/ (2 * Math.sqrt(x.w) * Math.exp(x.k));', '/ (Math.sqrt(x.w) * Math.exp(x.k));'],
  ['gomito: tolleranza di rumore ignorata', 'if (!(dwFav < 0)) continue;', ''],
  ['diff: inversione di calendario interpolata', "if (wb < wa) return { iv: null, reason: 'calendar_arbitrage' };", ''],
  ['evento: calo di varianza attraverso l\'evento non dichiarato', "if (a.w < w1) return { ...res, reason: 'calendar_arbitrage'", "if (a.w < -1) return { ...res, reason: 'calendar_arbitrage'"],
  ['calendario solo fra consecutive', 'for (let i = 0; i < smiles.length; i++) for (let j = i + 1; j < smiles.length; j++) {', 'for (let i = 0; i < smiles.length; i++) for (let j = i + 1; j < Math.min(i + 2, smiles.length); j++) {'],
  ['calendario: tolleranza in punti vol invece che in varianza', 'const tolW = (v1 * v1 * s1.T - lo1(k, v1)) + (hi2(k, v2) - v2 * v2 * s2.T);', 'const tolW = eps;'],
  ['niente controllato = pulito', 'clean: checkedAny ? flags.every(f => f.indicative) : null', 'clean: flags.every(f => f.indicative)'],
  ['motivo del forward perso', "return { F: null, reason: why ?? 'forward_missing' };", "return { F: null, reason: 'forward_missing' };"],
  ['giunzione mai riconosciuta', "return strikes.some(put) && strikes.some(K => !put(K)) ? 'junction' : fallback;", 'return fallback;'],
  ['tasso DGS3MO come se fosse continuo', '2 * Math.log1p(y / 2)', 'Math.log1p(y)'],
  ['r in percento accettato', 'if (Math.abs(input.r) > MAX_ABS_RATE)', 'if (Math.abs(input.r) > 50)'],
  ['strikeForDelta senza raffinamento locale', 'Math.abs(b) < Math.abs(a) && Math.abs(b) <= Math.abs(c)', 'false'],
  ['smile osservato: giunzione rovesciata', "c.type === ((c.strike as number) < F ? 'put' : 'call')", "c.type === ((c.strike as number) > F ? 'put' : 'call')"],
  // --- v2 (seconda revisione)
  ['eseguibile: butterfly su bid/ask rovesciati', 'const cost = L1 * qt.ask[0] - qt.bid[1] + L3 * qt.ask[2];', 'const cost = L1 * qt.bid[0] - qt.ask[1] + L3 * qt.bid[2];'],
  ['eseguibile: put→call col segno sbagliato', "x.type === 'put' ? (sm.discount as number) * (sm.F - x.strike) : 0;", "x.type === 'put' ? (sm.discount as number) * (x.strike - sm.F) : 0;"],
  ['eseguibile: verticale comprato a credito ignorato', 'const buyCredit = -(qt.ask[lo] - qt.bid[hi])', 'const buyCredit = 0 * (qt.ask[lo] - qt.bid[hi])'],
  ['de-americanizzazione spenta (EEP = 0)', 'return am !== null && eu !== null ? Math.max(am - eu, 0) : NaN;', 'return am !== null && eu !== null ? 0 : NaN;'],
  ['CRR europeo che esercita', 'V[j] = american ? Math.max(cont, sign * (P[i - 2 * j + n] - K)) : cont;', 'V[j] = Math.max(cont, sign * (P[i - 2 * j + n] - K));'],
  ['rumore nelle ali costante', 'export const WING_NOISE_SLOPE = 1.5;', 'export const WING_NOISE_SLOPE = 0;'],
  ['scadenza duplicata: vince l\'ultima', 'if ((count.get(expiry) ?? 0) > 1)', 'if ((count.get(expiry) ?? 0) > 9)'],
  ['ivNoise non valido accettato', 'if (!isNum(eps) || eps < 0)\n    return', 'if (false)\n    return'],
  ['diff K/F: forward ignorato', 'const z = Math.log(x * F / S);', 'const z = Math.log(x);'],
  ['diff: scivolamento col segno opposto', 'prev[i].iv !== null ? s * dLnSpot : null;', 'prev[i].iv !== null ? -s * dLnSpot : null;'],
  ['IV fuori intervallo accettata nello smile', 'if (!isIv(p.iv)) { outOfRange++; continue; }', ''],
  // --- mutazioni del REVISORE (mut.mjs), ancore v2
  ['R parità: filtro liquidità OI/volume tolto', "if (!((c.oi ?? 0) > 0 || (c.volume ?? 0) > 0)) return 'no_activity';", ''],
  ['R parità: filtro spread relativo tolto', "/ mid <= maxRelSpread ? null : 'wide_spread';", "/ mid <= Infinity ? null : 'wide_spread';"],
  ['R duplicati: vince OI MINORE', 'if (!prev || (c.oi ?? 0) > (prev.oi ?? 0) || ((c.oi', 'if (!prev || (c.oi ?? 0) < (prev.oi ?? 0) || ((c.oi'],
  ['R mediana pari = elemento alto', 'return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;', 'return n % 2 ? s[(n - 1) / 2] : s[n / 2];'],
  ['R parità: sconto col segno sbagliato', 'growth = Math.exp(r * T);', 'growth = Math.exp(-r * T);'],
  ['R dividend yield = carry − r', 'impliedDividendYield: carry === null ? null : r - carry', 'impliedDividendYield: carry === null ? null : carry - r'],
  ['R deltaGrid: righe non ordinate per T', 'const rows = objs<SmileSlice>(surface?.slices).sort((a, b) => (isT(a.T) ? a.T : Infinity) - (isT(b.T) ? b.T : Infinity)).map(', 'const rows = objs<SmileSlice>(surface?.slices).map('],
  ['R surfaceDiff: tenor in giorni di borsa', 'const T = d / 365, nul', 'const T = d / 252, nul'],
  ['R evento: post diviso per T3', '(pts[j + 1].w - a.w) / (pts[j + 1].T - a.T)', '(pts[j + 1].w - a.w) / pts[j + 1].T'],
  ['R forward ≤ 0 accettato', 'if (isPos(f)) return { F: f, reason: null };', 'if (isNum(f)) return { F: f, reason: null };'],
  ['R strike duplicati: max invece della media', 'const iv = ps.reduce((a, b) => a + b.iv, 0) / ps.length', 'const iv = Math.max(...ps.map(p => p.iv))'],
  ['R calendario robusto con SOME', 'bracket(s2, k).every(x => hi2(x.k, x.iv) < lo1(k, v1))', 'bracket(s2, k).some(x => hi2(x.k, x.iv) < lo1(k, v1))'],
  ['R verticale: limite −2', 'slope < -1 ? -1 - slope : 0', 'slope < -2 ? -2 - slope : 0'],
  ['R arbitraggi: smile non ordinati per T', 'smiles.sort((a, b) => a.T - b.T);', ''],
  ['R strikeForDelta: 4 passi', 'const steps = 2000,', 'const steps = 4,'],
  ['R diff: vecchia e nuova scambiate', 'x.iv - (prev[i].iv as number)', '(prev[i].iv as number) - x.iv'],
  ['R cono: banda bassa doppia', 'low: fi.F * Math.exp(-pct)', 'low: fi.F * Math.exp(-2 * pct)'],
  ['R evento: guardia del giorno di scadenza tolta', "if (pts.some(p => p.expiry === ed)) return { ...out, reason: 'event_timing_ambiguous', detail: 'earnings on an expiry day' };", ''],
  ['R skew: BF25 con le ali 10', '(c25 + p25) / 2 - atm : (reasons.bf25', '(c10 ?? c25) / 2 + (p10 ?? p25) / 2 - atm : (reasons.bf25'],
  ['R strikeForDelta: nodi ignorati nella scansione', 'for (const n of range.nodes ?? []) if', 'for (const n of [] as number[]) if'],
  ['R forward: coppie massime 1', 'options.maxPairs ?? 4', 'options.maxPairs ?? 1'],
  ['R smileFromChain: duplicati per OI minore (stessa bestQuotes)', "(c.oi ?? 0) === (prev.oi ?? 0) && spread(c) < spread(prev)", "(c.oi ?? 0) === (prev.oi ?? 0) && spread(c) > spread(prev)"],
  // --- v3 (ri-verifica della v2)
  ['v3 EEP con l’IV della gamba sbagliata (N05)', "const c = ty === 'call' ? x.call : x.put;", "const c = ty === 'call' ? x.put : x.call;"],
  ['v3 densità: rumore ignorato (N26)', 'gF = g(k, wF, wpFav);', 'gF = gv;'],
  ['v3 segnali di modello mai indicativi', "indicative: test === 'model' && idx.every(i => n[i].bid !== null && n[i].ask !== null) };", 'indicative: false };'],
  ['v3 indicativi che sporcano la superficie', 'clean: checkedAny ? flags.every(f => f.indicative) : null,', 'clean: checkedAny ? flags.length === 0 : null,'],
  ['v3 T assurdo accettato', 'const isT = (v: unknown): v is number => isPos(v) && v <= MAX_T_YEARS;', 'const isT = (v: unknown): v is number => isPos(v);'],
];

const dir = mkdtempSync(join(tmpdir(), 'vq-mut-'));
const lib = join(dir, 'vol-quant.ts');
const run = () => spawnSync(process.execPath, ['--test', 'tests/product/vol-quant.mts'], {
  cwd: app, env: { ...process.env, VOL_QUANT_LIB: lib }, encoding: 'utf8' });
let failures = 0;
try {
  writeFileSync(lib, source);
  const intact = run();
  if (intact.status !== 0) { console.error('copia INTATTA rossa: il banco non vale\n' + intact.stdout.slice(-2000)); process.exit(2); }
  console.log('copia intatta: verde');
  for (const [name, from, to] of MUTANTS) {
    const count = source.split(from).length - 1;
    if (count !== 1) { console.log(`ANCORA NON UNICA (${count}) — ${name}`); failures++; continue; }
    writeFileSync(lib, source.replace(from, to));
    const r = run();
    // un mutante che non si carica (sintassi) farebbe dire «catturata» senza aver provato nulla
    const passed = Number((r.stdout.match(/^ℹ pass (\d+)/m) || [])[1] ?? 0);
    if (r.status !== 0 && passed === 0) { console.log(`MUTANTE NON CARICABILE — ${name}`); failures++; continue; }
    const caught = r.status !== 0;
    const fails = new Set(r.stdout.match(/^✖ (?!failing tests).*?(?= \(\d)/gm) || []).size;
    console.log(`${caught ? 'CATTURATA' : 'SOPRAVVISSUTA'} — ${name}${caught ? ` (${fails} test rossi)` : ''}`);
    if (!caught) failures++;
  }
} finally {
  rmSync(dir, { recursive: true, force: true });
}
console.log(`\n${MUTANTS.length - failures}/${MUTANTS.length} catturate`);
process.exit(failures ? 1 : 0);
