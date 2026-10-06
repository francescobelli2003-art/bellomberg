/* Calcoli puri del pannello Impostazioni: nessun hook, nessuna chiamata.
   Le regole sono quelle della «veglia» originale: un lavoro spento per scelta
   non e' un guasto, la copertura si disegna solo sui backup CONSEGNATI, la
   contraddizione compare solo se tiene davvero. */
import type { Chiave } from '../../i18n/t';

export type Backup = { filename: string; path: string; size_mb: number; created: string };
export type Lavoro = { TaskName?: unknown; State?: unknown; LastTaskResult?: unknown; LastRunTime?: unknown; NextRunTime?: unknown };
export type Giorno = { g: string; n: number; mb: number; mano: number };
export type SezioneId = 'generale' | 'backup' | 'lavori' | 'motori';
export const SEZIONI: SezioneId[] = ['generale', 'backup', 'lavori', 'motori'];

/* Il payload del Task Scheduler arriva col formato US (07/26/2026 23:00:01).
   Un `new Date(stringa)` qui sarebbe un azzardo: si parsa a mano. */
export function parseTask(s: unknown): Date | null {
  const m = String(s ?? '').match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2}):(\d{2})/);
  if (!m) return null;
  const d = new Date(+m[3], +m[1] - 1, +m[2], +m[4], +m[5], +m[6]);
  return isNaN(d.getTime()) ? null : d;
}
export function parseIso(s: unknown): Date | null {
  const d = new Date(String(s ?? ''));
  return isNaN(d.getTime()) ? null : d;
}
const due = (n: number) => String(n).padStart(2, '0');
export const ggmm = (d: Date | null, nd: string) => (d ? `${due(d.getDate())}/${due(d.getMonth() + 1)}` : nd);
export const hhmm = (d: Date | null) => (d ? `${due(d.getHours())}:${due(d.getMinutes())}` : '');
export const chiave = (d: Date) => `${d.getFullYear()}-${due(d.getMonth() + 1)}-${due(d.getDate())}`;
export const isAuto = (f: string) => /^bellomberg_backup_/i.test(f);
export const nomeLavoro = (t: Lavoro) => String(t.TaskName || '?').replace('Bellomberg-', '');
export const spento = (t: Lavoro) => String(t.State).toLowerCase() === 'disabled';

/* Su macOS il backend lancia `powershell`, che non esiste: la lettura fallisce
   con ENOENT. Non e' un guasto della macchina ma un sistema non supportato, e
   si dice cosi'. Ogni altro errore resta un errore dichiarato. */
export const powershellAssente = (diagnostica: string | null) =>
  !!diagnostica && /powershell/i.test(diagnostica) && /no such file|not found|cannot find|ENOENT|errno 2/i.test(diagnostica);

export function lavori(tasks: Lavoro[] | null) {
  const t = tasks || [];
  const spenti = t.filter(spento);
  const attivi = t.filter(x => !spento(x));
  const guasti = attivi.filter(x => x.LastTaskResult !== 0 && x.LastTaskResult != null);
  const prossimi = attivi.map(x => parseTask(x.NextRunTime)).filter((d): d is Date => !!d).sort((a, b) => a.getTime() - b.getTime());
  return { attivi, spenti, guasti, prossimo: prossimi[0] || null };
}

export function perGiornoDi(backups: Backup[] | null) {
  const per: Record<string, Backup[]> = {};
  (backups || []).forEach(b => {
    const d = parseIso(b.created);
    if (!d) return;
    (per[chiave(d)] = per[chiave(d)] || []).push(b);
  });
  return per;
}

/* Un giorno per data, dal primo backup consegnato all'ultimo: i giorni vuoti
   restano nel calendario come notti scoperte. */
export function calendarioDi(per: Record<string, Backup[]>): Giorno[] {
  const gg = Object.keys(per).sort();
  if (!gg.length) return [];
  const cal: Giorno[] = [];
  const d0 = new Date(gg[0] + 'T00:00:00');
  const d1 = new Date(gg[gg.length - 1] + 'T00:00:00');
  for (let d = new Date(d0); d <= d1; d.setDate(d.getDate() + 1)) {
    const items = per[chiave(d)] || [];
    cal.push({
      g: chiave(d), n: items.length,
      mb: items.reduce((s, x) => s + (x.size_mb || 0), 0),
      mano: items.filter(x => !isAuto(x.filename)).length,
    });
  }
  return cal;
}

/* Il primo lavoro guasto che ha comunque lasciato un backup nella notte in cui
   dichiara di aver fallito. Nessuno = nessun riquadro: non si inventa un allarme. */
export function contraddizioneDi(guasti: Lavoro[], per: Record<string, Backup[]>) {
  for (const t of guasti) {
    const last = parseTask(t.LastRunTime);
    if (!last) continue;
    const lasciati = per[chiave(last)] || [];
    if (lasciati.length) return { t, last, lasciati, mb: lasciati.reduce((s, x) => s + (x.size_mb || 0), 0) };
  }
  return null;
}

export const engineLabels: Record<string, Chiave> = {
  chat: 'settingsPage.engine_chat', committee_r0: 'settingsPage.engine_r0', committee_r1_r2: 'settingsPage.engine_r1_r2',
  committee_macro: 'settingsPage.engine_macro', committee_quant: 'settingsPage.engine_quant', committee_options: 'settingsPage.engine_options',
  committee_fundamentals: 'settingsPage.engine_fundamentals', committee_crypto: 'settingsPage.engine_crypto', committee_eventdesk: 'settingsPage.engine_eventdesk',
  capo: 'settingsPage.engine_capo', red_team: 'settingsPage.engine_redteam', reflection: 'settingsPage.engine_reflection',
  action_extractor: 'settingsPage.engine_extractor', synthesizer: 'settingsPage.engine_synthesizer', briefing: 'settingsPage.engine_briefing', news_classifier: 'settingsPage.engine_news',
};
const REVISIONE = new Set(['capo', 'red_team', 'reflection', 'action_extractor', 'synthesizer']);
const ALTRI = new Set(['chat', 'briefing', 'news_classifier']);
export type GruppoMotori = 'committee' | 'review' | 'other' | 'unknown';
export function gruppoMotore(k: string): GruppoMotori {
  if (k.startsWith('committee_')) return 'committee';
  if (REVISIONE.has(k)) return 'review';
  if (ALTRI.has(k)) return 'other';
  return 'unknown';
}

/* Etichette dell'asse: prima e ultima data, poi i primi del mese, poi un passo
   regolare; ognuna entra solo se lascia spazio alle altre (mai due date
   attaccate, come «30/0901/10» su uno schermo stretto). */
export function etichetteAsse(cal: Giorno[]): string[] {
  const n = cal.length;
  if (!n) return [];
  const passo = Math.max(1, Math.ceil(n / 8));
  const distanza = Math.max(2, Math.ceil(passo * 0.6));
  const candidati = [0, n - 1,
    ...cal.map((c, i) => (c.g.endsWith('-01') ? i : -1)).filter(i => i > 0),
    ...cal.map((_, i) => (i % passo === 0 ? i : -1)).filter(i => i > 0)];
  const scelti: number[] = [];
  for (const i of candidati) {
    if (scelti.includes(i)) continue;
    if (scelti.every(j => Math.abs(i - j) >= distanza)) scelti.push(i);
  }
  return cal.map((c, i) => {
    if (!scelti.includes(i)) return '';
    const [, mm, dd] = c.g.split('-');
    return `${dd}/${mm}`;
  });
}

/* Etichette dell'asse dei MB (cima e meta' del grafico). Sotto i 10 MB
   l'intero mente: 2,5 diventava «3» e, col fondo scala a 1, cima e meta'
   uscivano entrambe «1». Quindi una cifra decimale sotto 10, due sotto 1;
   se l'arrotondamento le rende comunque uguali si aggiunge una cifra a
   entrambe, mai due etichette identiche su righe diverse. */
export const cifreMb = (v: number) => (v >= 10 ? 0 : v >= 1 ? 1 : 2);
export function etichetteMb(max: number, num: (v: number, cifre: number) => string): [string, string] {
  let ca = cifreMb(max), cm = cifreMb(max / 2);
  while (num(max, ca) === num(max / 2, cm) && ca < 6) { ca++; cm++; }
  return [num(max, ca), num(max / 2, cm)];
}
