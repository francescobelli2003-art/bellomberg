import type { Decision, Memo } from '@/lib/api';

/* Archivio memo (Nuova): la logica pura della pagina, senza React. Le regole sono quelle di
   F9 v3 e restano identiche: un esito non agganciato si dichiara, non si deduce; i file DCF
   malformati restano «non validi», non diventano zero. */

/** Ordine degli esiti: prima cio' che e' stato fatto, in fondo cio' che e' evaporato. */
export const ORD = ['EXECUTED', 'PARTIAL', 'PENDING', 'SKIPPED', 'EXPIRED'] as const;
export type Esito = typeof ORD[number];

export const ts = (s: string) => new Date(s).getTime();
export const nomeFile = (p: string) => (p || '').split(/[\\/]/).pop()!.replace(/\.xlsx$/i, '');
export const segnalato = (f: string) => /_FLAGGED/i.test(f);

/** I file DCF arrivano come stringa JSON. Se non lo e', non si tira a indovinare. */
export function dcfDi(m: Memo): string[] | null {
  if (!m.dcf_files) return [];
  try {
    const v = JSON.parse(m.dcf_files);
    return Array.isArray(v) && v.every(x => typeof x === 'string') ? v : null;
  } catch { return null; }
}

export function conta(ds: Decision[]): Partial<Record<Esito, number>> {
  const c: Partial<Record<Esito, number>> = {};
  ds.forEach(d => { const s = d.status as Esito; if ((ORD as readonly string[]).includes(s)) c[s] = (c[s] || 0) + 1; });
  return c;
}
export const eseguite = (c: Partial<Record<Esito, number>>) => (c.EXECUTED || 0) + (c.PARTIAL || 0);

export interface Riga { act: string; tick: string; eur: string; timing: string; conf: string; dec: Decision | null }

/** La ACTION TABLE del memo, riga per riga, agganciata alle decisioni in DB.
 *  L'aggancio e' (ticker, azione) dentro lo stesso memo. Chi non aggancia porta `dec: null`
 *  e la pagina lo SCRIVE — non inventa un esito. */
export function tabellaAzioni(md: string, dec: Decision[]): Riga[] {
  const idx = new Map<string, Decision>();
  dec.forEach(d => idx.set(`${(d.ticker || '').toUpperCase().trim()}|${(d.action || '').toUpperCase().trim()}`, d));
  const out: Riga[] = [];
  let dentro = false;
  for (const riga of md.split('\n')) {
    if (/^##\s+ACTION TABLE/i.test(riga)) { dentro = true; continue; }
    if (!dentro) continue;
    if (/^##\s/.test(riga)) break;
    if (!riga.trim().startsWith('|')) continue;
    const c = riga.trim().replace(/^\||\|$/g, '').split('|').map(s => s.trim());
    if (c.length < 4) continue;
    if (c.every(x => /^:?-+:?$/.test(x.replace(/\s/g, '')))) continue;
    if (/^(?:action|azione)$/i.test(c[0])) continue;
    out.push({
      act: c[0], tick: c[1], eur: c[2] || '', timing: c[3] || '', conf: c[4] || '',
      dec: idx.get(`${(c[1] || '').toUpperCase().trim()}|${(c[0] || '').toUpperCase().trim()}`) || null,
    });
  }
  return out;
}

/** Corpo grezzo di una sezione di livello 2 (titolo confrontato per prefisso, senza maiuscole). */
function corpoSezione(md: string, titolo: RegExp): string[] | null {
  const righe = md.split('\n');
  const da = righe.findIndex(r => /^##\s/.test(r) && !/^###/.test(r) && titolo.test(r.replace(/^##\s+/, '').trim()));
  if (da < 0) return null;
  const out: string[] = [];
  for (let i = da + 1; i < righe.length && !/^##\s/.test(righe[i]); i++) out.push(righe[i]);
  return out;
}

/** BLUF del memo: una voce per riga non vuota (il Capo lo scrive una frase per riga). */
export function bluf(md: string): string[] {
  const corpo = corpoSezione(md, /^BLUF\b/i);
  if (!corpo) return [];
  return corpo.map(r => r.replace(/^\s*[-*]\s+/, '').trim()).filter(Boolean);
}

export interface DecisionePrioritaria { titolo: string; obiezione: boolean }

/** Le «Decisione N: …» della sezione DECISIONI PRIORITARIE (o PRIORITY DECISIONS). */
export function decisioniPrioritarie(md: string): DecisionePrioritaria[] {
  const corpo = corpoSezione(md, /^(DECISIONI PRIORITARIE|PRIORITY DECISIONS)/i);
  if (!corpo) return [];
  const out: DecisionePrioritaria[] = [];
  corpo.forEach(r => {
    const h = /^###\s+(?:Decisione|Decision)\s*\d+\s*[:.·—-]\s*(.*)$/i.exec(r);
    if (h) { const t = h[1].trim(); out.push({ titolo: t.charAt(0).toUpperCase() + t.slice(1), obiezione: false }); }
    else if (out.length && RED_TEAM.test(r)) out[out.length - 1].obiezione = true;
  });
  return out;
}

/** Paragrafo «Obiezione del red team: … Rispondo: …» (IT) o «Red team objection: … Response: …» (EN). */
export const RED_TEAM = /^\*\*(?:Obiezione del red team|Red[- ]team objection)\s*:?\s*\*\*/i;
export function obiezione(riga: string): { q: string; a: string | null } | null {
  const t = riga.trim();
  if (!RED_TEAM.test(t)) return null;
  const resto = t.replace(RED_TEAM, '').trim();
  const r = /\*\*(?:Rispondo|Risposta|Response|I respond|Reply)\s*:?\s*\*\*/i.exec(resto);
  if (!r) return { q: resto, a: null };
  return { q: resto.slice(0, r.index).trim(), a: resto.slice(r.index + r[0].length).trim() };
}

export interface Mandato { impronta: string; origine: string; data: string }
/** Riga di testata «[MANDATO PM: impronta …; origine …; dichiarato_il AAAA-MM-GG]». */
export function mandato(md: string): Mandato | null {
  const m = /^\[(?:MANDATO PM|PM MANDATE):\s*(?:impronta|fingerprint)\s+([0-9a-f]+);\s*(?:origine|origin)\s+([^;]+);\s*(?:dichiarato_il|declared_on)\s+([\d-]+)\]\s*$/im.exec(md);
  return m ? { impronta: m[1], origine: m[2].trim(), data: m[3] } : null;
}
export const RIGA_MANDATO = /^\[(?:MANDATO PM|PM MANDATE):[^\]]*\]\s*$/i;

/** Sezioni appese dal codice e non scritte dal Capo: si leggono a richiesta, in fondo. */
export const SEZIONI_AUTOMATICHE = ['ACTION VALIDATOR', "QUALITA' DATI", 'QUALITÀ DATI', 'DATA QUALITY'];

/** Titoli scritti tutti in maiuscolo dal Capo («DECISIONI PRIORITARIE PER IL PM») resi in forma
 *  di frase; le sigle note restano sigle. Un titolo gia' in forma mista non si tocca. */
const SIGLE = new Set(['PM', 'BLUF', 'CPI', 'NAV', 'DCF', 'VAR', 'CVAR', 'ETF', 'USA', 'BCE', 'ECB', 'FED', 'FOMC', 'PIL', 'GDP', 'DEFI', 'IPO', 'EPS', 'AI', 'UE', 'EU', 'UK']);
export function titoloLeggibile(t: string): string {
  if (!/[A-ZÀ-Ý]{3}/.test(t) || t !== t.toUpperCase()) return t;
  const basso = t.toLowerCase().split(/(\s+)/).map(p => SIGLE.has(p.toUpperCase().replace(/[^A-Z]/g, '')) ? p.toUpperCase() : p).join('');
  const i = basso.search(/[a-zà-ý]/i);
  return i < 0 ? basso : basso.slice(0, i) + basso[i].toUpperCase() + basso.slice(i + 1);
}

/** «12. Nota di chiusura» → numero e testo, per l'indice. */
export function numeroSezione(t: string): { n: string | null; testo: string } {
  const m = /^(\d+(?:-bis)?)\.\s+(.*)$/i.exec(t);
  return m ? { n: m[1], testo: m[2] } : { n: null, testo: t };
}
