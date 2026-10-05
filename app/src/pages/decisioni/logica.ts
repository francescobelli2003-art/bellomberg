/* Pagina Decisioni: regole pure (nessuna rete, nessun hook). Le regole d'archivio sono quelle di F10 v3:
   il backend dichiara `archived` (override incluso); il fallback client applica le STESSE regole per un
   backend non ancora riavviato. */
import type { Decision } from '@/lib/api';
import { statoDivergenza } from '@/lib/trade-entry';

export type Vista = 'todo' | 'res' | 'closed' | 'arch';
export type FiltroChiuse = 'all' | 'EXECUTED' | 'PARTIAL' | 'SKIPPED' | 'EXPIRED';
export type TipoArchivio = 'op' | 'res';
export type Esito = 'EXECUTED' | 'PARTIAL' | 'SKIPPED' | 'EXPIRED';

/** Giorni oltre i quali una ricerca aperta senza note nuove si dichiara «ferma». */
export const GIORNI_FERMA = 7;

export const isResearch = (d: Decision) => (d.action || '').toUpperCase() === 'RESEARCH';
export const isHold = (d: Decision) => (d.action || '').toUpperCase() === 'HOLD';

export const giorniDa = (ts?: string | null, ora = Date.now()) => {
  if (!ts) return null;
  const d = Math.floor((ora - new Date(ts).getTime()) / 86400_000);
  return Number.isFinite(d) ? Math.max(0, d) : null;
};

export const isArchived = (d: Decision) => {
  if (d.archive_override != null) return !!d.archive_override;
  if (typeof d.archived === 'boolean') return d.archived;
  if (isResearch(d)) return d.status !== 'PENDING';
  const g = giorniDa(d.timestamp);
  return g != null && g > 7;
};

/** Ultima attività di una ricerca: la proposta o l'ultima nota del filo. */
export const ultimaAttivita = (d: Decision) =>
  [d.timestamp, ...(d.notes || []).map(n => n.timestamp)].filter(Boolean).sort().at(-1) ?? null;
export const ricercaFerma = (d: Decision, ora = Date.now()) => {
  if (!isResearch(d) || d.status !== 'PENDING') return false;
  const g = giorniDa(ultimaAttivita(d), ora);
  return g != null && g > GIORNI_FERMA;
};

export interface Gruppi {
  esegui: Decision[]; bloccate: Decision[]; conferme: Decision[];
  ricerche: Decision[]; ricercheFerme: Decision[];
  chiuse: Decision[];
  archOp: Decision[]; archRes: Decision[];
}

const piuRecenti = (a: Decision, b: Decision) => (b.timestamp || '').localeCompare(a.timestamp || '') || b.id - a.id;

export function raggruppa(decisions: Decision[]): Gruppi {
  const g: Gruppi = { esegui: [], bloccate: [], conferme: [], ricerche: [], ricercheFerme: [], chiuse: [], archOp: [], archRes: [] };
  for (const d of [...decisions].sort(piuRecenti)) {
    const archiviata = isArchived(d);
    if (isResearch(d)) {
      if (archiviata) g.archRes.push(d);
      else if (d.status === 'PENDING') g.ricerche.push(d);
      else g.ricercheFerme.push(d);
    } else if (archiviata) g.archOp.push(d);
    else if (d.status !== 'PENDING') g.chiuse.push(d);
    else if (statoDivergenza(d) !== null) g.bloccate.push(d);
    else if (isHold(d)) g.conferme.push(d);
    else g.esegui.push(d);
  }
  const chiusura = (d: Decision) => d.closed_at || d.timestamp || '';
  g.chiuse.sort((a, b) => chiusura(b).localeCompare(chiusura(a)) || b.id - a.id);
  return g;
}

/** Vista in cui vive una decisione (per i link ?decision=ID e «Altre proposte sul titolo»). */
export function vistaDi(d: Decision): Vista {
  if (isArchived(d)) return 'arch';
  if (isResearch(d)) return 'res';
  return d.status === 'PENDING' ? 'todo' : 'closed';
}

/** Voci dell'elenco nell'ordine mostrato, per scegliere la prima quando la vista cambia. */
export function vociVista(g: Gruppi, vista: Vista, filtro: FiltroChiuse, tipo: TipoArchivio): Decision[] {
  if (vista === 'todo') return [...g.esegui, ...g.bloccate, ...g.conferme];
  if (vista === 'res') return [...g.ricerche, ...g.ricercheFerme];
  if (vista === 'closed') return g.chiuse.filter(d => filtro === 'all' || d.status === filtro);
  return tipo === 'op' ? g.archOp : g.archRes;
}

/** Esito proposto nel blocco «Chiudi la decisione»: lo stato chiuso attuale, altrimenti Eseguita se il
    controllo rischio lo consente, altrimenti Non eseguita. */
export function esitoPredefinito(d: Decision, eseguibile: boolean): Esito {
  if (d.status !== 'PENDING' && ['EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED'].includes(d.status)
    && (eseguibile || !['EXECUTED', 'PARTIAL'].includes(d.status))) return d.status as Esito;
  return eseguibile ? 'EXECUTED' : 'SKIPPED';
}

/** Chiusure raggruppate per mese (data di chiusura, altrimenti della proposta). */
export function perMese(lista: Decision[], locale: string): { mese: string; voci: Decision[] }[] {
  const out: { mese: string; voci: Decision[] }[] = [];
  for (const d of lista) {
    const iso = (d.closed_at || d.timestamp || '').slice(0, 7);
    const data = new Date(iso + '-01T00:00:00');
    const mese = Number.isFinite(data.getTime())
      ? data.toLocaleDateString(locale, { month: 'long', year: 'numeric' }).replace(/^./, c => c.toUpperCase()) : '—';
    const ultimo = out.at(-1);
    if (ultimo && ultimo.mese === mese) ultimo.voci.push(d); else out.push({ mese, voci: [d] });
  }
  return out;
}

/** Altre proposte sullo stesso titolo (più recenti prima), esclusa quella aperta. */
export const stessoTitolo = (decisions: Decision[], d: Decision, max = 5) =>
  decisions.filter(x => x.id !== d.id && (x.ticker || '').toUpperCase() === (d.ticker || '').toUpperCase())
    .sort(piuRecenti).slice(0, max);
