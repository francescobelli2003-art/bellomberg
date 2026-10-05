import { analizzaNumero } from '@/i18n/numeri';
import { localeDi, type Lingua } from '@/i18n/lingua';
import { traduci, type Chiave, type Parametri } from '@/i18n/t';
import type { CampoMandato } from '@/lib/mandato';

/* Lettura della bozza per grafici, sintesi e avvisi. Il modulo resta fatto di stringhe (come le
   scrive chi compila): qui diventano numeri solo per disegnare. La validazione vera resta quella
   di MandatoPage (leggiNumeroMandato) e, sopra tutto, del server. */

export type Valore = number | null;
export type Intervallo = [Valore, Valore];
export interface Lettura {
  n: (nome: string) => Valore;
  r: (nome: string) => Intervallo;
  b: (nome: string) => boolean | null;
  s: (nome: string) => string;
  lista: (nome: string) => string[];
  mappa: (nome: string) => Record<string, boolean | null>;
}

export function leggi(form: Record<string, any>, lingua: Lingua): Lettura {
  const num = (raw: unknown): Valore => {
    if (raw == null || raw === '') return null;
    const letto = analizzaNumero(String(raw), lingua);
    return letto && letto.ok ? letto.valore : null;
  };
  return {
    n: nome => num(form[nome]),
    r: nome => { const v = form[nome]; return Array.isArray(v) ? [num(v[0]), num(v[1])] : [null, null]; },
    b: nome => typeof form[nome] === 'boolean' ? form[nome] : null,
    s: nome => form[nome] == null ? '' : String(form[nome]),
    lista: nome => Array.isArray(form[nome]) ? form[nome] : typeof form[nome] === 'string' ? form[nome].split('\n').map((x: string) => x.trim()).filter(Boolean) : [],
    mappa: nome => form[nome] && typeof form[nome] === 'object' && !Array.isArray(form[nome]) ? form[nome] : {},
  };
}

export const tr = (l: Lingua, k: string, p?: Parametri) => traduci(l, ('mandate.' + k) as Chiave, p);

export function numero(v: Valore | undefined, l: Lingua, decimali = 2): string {
  return v == null || !Number.isFinite(v) ? '—' : v.toLocaleString(localeDi(l), { maximumFractionDigits: decimali });
}
export function intervallo(r: Intervallo, l: Lingua): string {
  const [a, b] = r;
  if (a == null && b == null) return '—';
  if (a === b) return numero(a, l);
  return `${numero(a, l)}–${numero(b, l)}`;
}

// Le stesse ipotesi di mandato_pm.vol_annua_implicita: z a una coda del 99% e 252 sedute.
const Z99 = 2.3263478740408408, SEDUTE = 252, BANDA_VOL = 1.5;
export const volImplicita = (var99: Valore) => var99 == null ? null : var99 / Z99 * Math.sqrt(SEDUTE);
export const bandaVol = (target: Valore) => target == null ? null : target * BANDA_VOL;

/* Avvisi di coerenza: le stesse regole di mandato_pm.valida, calcolate sulla bozza per
   disegnarle mentre si scrive. Non bloccano niente: decide il server alla verifica. */
export function coerenze(v: Lettura, l: Lingua): Record<string, string> {
  const out: Record<string, string> = {};
  const t = (k: string, p?: Parametri) => tr(l, k, p);
  const f = (x: Valore) => numero(x, l);
  const gt = (a: Valore, b: Valore) => a != null && b != null && a > b;
  const bs = v.n('base_single_pct'), cs = v.n('cap_single_pct'), bv = v.n('base_veicolo_pct'), cv = v.n('cap_veicolo_pct');
  if (gt(bs, cs)) out.base_single_pct = t('h_base_single', { a: f(bs), b: f(cs) });
  if (gt(bv, cv)) out.base_veicolo_pct = t('h_base_veicolo', { a: f(bv), b: f(cv) });
  const floor = v.n('limite_minimo_pct'), minPos = v.n('posizione_minima_pct'), top3 = v.n('top3_max_pct');
  if (gt(floor, bs)) out.limite_minimo_pct = t('h_floor', { a: f(floor), b: f(bs) });
  if (gt(minPos, cs)) out.posizione_minima_pct = t('h_min_pos', { a: f(minPos), b: f(cs) });
  if (gt(cs, top3)) out.top3_max_pct = t('h_top3', { a: f(top3), b: f(cs) });
  const var99 = v.n('var99_1g_pct'), dd = v.n('drawdown_max_pct'), stress = v.n('stress_gfc_pct'), vol = v.n('volatilita_target_pct');
  if (gt(var99, dd)) out.var99_1g_pct = t('h_var_dd');
  else if (gt(var99, stress)) out.var99_1g_pct = t('h_var_stress');
  const vi = volImplicita(var99), banda = bandaVol(vol);
  if (gt(vi, banda)) out.volatilita_target_pct = t('h_vol', { a: numero(vi, l, 1) });
  if (gt(stress, dd)) out.drawdown_max_pct = t('h_dd_stress');
  const minima = v.n('cassa_minima_pct'), tipica = v.r('cassa_tipica_pct');
  if (gt(minima, tipica[0])) out.cassa_minima_pct = t('h_cash_min');
  const libera = v.r('taglio_max_senza_condizioni_pct'), soglia = v.n('taglio_con_condizioni_oltre_pct');
  if (gt(libera[1], soglia)) out.taglio_max_senza_condizioni_pct = t('h_cut');
  const abilitate = v.b('opzioni_abilitate'), strumenti = v.lista('strumenti_ammessi'), budget = v.n('budget_premio_pct');
  if (abilitate === false && (strumenti.length || budget != null)) out.strumenti_ammessi = t('h_opt_off');
  if (abilitate === true && !strumenti.length) out.strumenti_ammessi = t('h_opt_empty');
  return out;
}

/* Una riga per sezione nell'indice: quello che serve per riconoscerla senza aprirla. */
export function sintesi(blocco: string, v: Lettura, l: Lingua, etichetta: (valore: string) => string): string {
  const t = (k: string, p?: Parametri) => tr(l, k, p), f = (x: Valore) => numero(x, l);
  switch (blocco) {
    case 'profilo': return t('sum_profilo', { a: v.s('tipo_investimento') ? etichetta(v.s('tipo_investimento')) : '—',
      b: v.s('stile') ? etichetta(v.s('stile')) : '—', c: v.s('valuta_base').toUpperCase() || '—' });
    case 'rischio': return t('sum_rischio', { a: f(v.n('var99_1g_pct')), b: f(v.n('drawdown_max_pct')) });
    case 'sizing': return t('sum_sizing', { a: f(v.n('base_single_pct')), b: f(v.n('cap_single_pct')), c: f(v.n('base_veicolo_pct')), d: f(v.n('cap_veicolo_pct')) });
    case 'cassa': return t('sum_cassa', { a: intervallo(v.r('cassa_tipica_pct'), l), b: v.s('politica_impiego') ? etichetta(v.s('politica_impiego')).toLowerCase() : '—' });
    case 'disciplina': return t('sum_disciplina', { a: f(v.r('taglio_max_senza_condizioni_pct')[1]), b: f(v.n('taglio_con_condizioni_oltre_pct')) });
    case 'opzioni': return v.b('opzioni_abilitate') ? t('sum_opzioni_on', { a: v.lista('strumenti_ammessi').length }) : t('sum_opzioni_off');
    case 'note': return t('sum_note', { a: v.lista('aree_gradite').length, b: v.lista('esclusioni').length });
    default: return '';
  }
}

/* I campi che la sezione mostra nel riquadro principale invece che nella griglia. */
export const CAMPI_PRINCIPALI: Record<string, string[]> = {
  profilo: ['tipo_investimento', 'stile', 'mercati_accessibili'],
  disciplina: ['condizioni_taglio_oltre'],
  opzioni: ['opzioni_abilitate', 'strumenti_ammessi'],
  note: ['note_per_il_comitato', 'aree_gradite', 'esclusioni'],
};

/* Il testo per il comitato diviso in paragrafi, ognuno attribuito alla sezione che lo genera
   (titoli IT ed EN di mandato_pm). Un paragrafo che non si riconosce resta senza sezione. */
const PARAGRAFI: [string, RegExp][] = [
  ['profilo', /^(PROFILO DEL PM|PM PROFILE):/],
  ['rischio', /^(RISCHIO ACCETTATO|RISK ACCEPTED):/],
  ['sizing', /^SIZING:/],
  ['note', /^(NOTE DEL PM PER IL COMITATO|PM NOTES FOR THE COMMITTEE):/],
  ['cassa', /^## (GESTIONE DEL CASH|CASH MANAGEMENT)/],
  ['opzioni', /^## (DISCIPLINA SULLE OPZIONI|OPTIONS DISCIPLINE)/],
  ['disciplina', /^## (DISCIPLINA|REDUCTION|PAIR TRADE|ROTAZIONE|SECTOR ROTATION|DECISIONI SALTATE|SKIPPED|LE VIEW|THE PM)/],
];
export function paragrafi(testo: string): { blocco: string | null; testo: string }[] {
  const out: { blocco: string | null; testo: string }[] = [];
  for (const riga of testo.split('\n')) {
    const inizio = /^## /.test(riga) || /^[A-Z][A-Z' ]{3,}:/.test(riga) || (!out.length);
    if (inizio) out.push({ blocco: PARAGRAFI.find(([, re]) => re.test(riga))?.[0] ?? null, testo: riga });
    else out[out.length - 1].testo += '\n' + riga;
  }
  return out.map(p => ({ ...p, testo: p.testo.replace(/\n+$/, '') })).filter(p => p.testo.trim());
}

export type Schema = Record<string, CampoMandato>;
