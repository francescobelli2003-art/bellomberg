import type { ReactNode } from 'react';
import { AlertCircle, Check } from 'lucide-react';
import IconaDesk, { coloreDesk } from '../chat/IconaDesk';

/* Presentazione della pagina Agenti in diretta (palette --bbn-*). Solo vernice:
   i dati arrivano già calcolati da AgentsLive, che tiene polling, avvio e stop. */

export type StatoDesk = 'run' | 'think' | 'ok' | 'ko' | 'nd' | 'wait' | 'stale';
export type StatoTappa = 'done' | 'now' | 'wait' | 'skip' | 'ko';
export type StatoRound = 'ok' | 'on' | 'ko' | 'off' | '';

/** Icona tonda del desk (come in Chat) con l'anello di stato. */
export function AnelloDesk({ id, colore, stato, grande = false }: {
  id: string; colore?: string | null; stato: StatoDesk; grande?: boolean;
}) {
  const c = grande ? 34 : 28, r = grande ? 32 : 26;
  const giro = stato === 'run' || stato === 'think' || stato === 'stale';
  const tratteggio = stato === 'wait' || stato === 'nd' ? '2 6' : giro ? undefined : `${2 * Math.PI * r} 0`;
  return (
    <span className={`ag-ring is-${stato}${grande ? ' is-lg' : ''}`} data-stato={stato}>
      <svg className="ag-ring-svg" viewBox={`0 0 ${c * 2} ${c * 2}`} aria-hidden="true">
        <circle className="trk" cx={c} cy={c} r={r} />
        <circle className="arc" cx={c} cy={c} r={r} strokeDasharray={tratteggio} />
      </svg>
      <IconaDesk id={id} colore={colore} dimensione="md" />
      {stato === 'ok' && <span className="ag-ring-badge"><Check size={11} strokeWidth={3} /></span>}
      {stato === 'ko' && <span className="ag-ring-badge"><AlertCircle size={11} strokeWidth={3} /></span>}
      {stato === 'nd' && <span className="ag-ring-badge">!</span>}
    </span>
  );
}

export function Kpi({ etichetta, valore, sotto, tono, nd = false }: {
  etichetta: string; valore: ReactNode; sotto?: ReactNode; tono?: 'warn' | 'bad'; nd?: boolean;
}) {
  return (
    <div className="ag-kpi">
      <span className="l">{etichetta}</span>
      <span className={'v num' + (nd ? ' is-nd' : '')}>{valore}</span>
      {sotto != null && <span className={'s' + (tono ? ' is-' + tono : '')}>{sotto}</span>}
    </div>
  );
}

export interface Tappa { id: string; nome: string; sotto: string; stato: StatoTappa }

export function Tappe({ tappe, etichetta }: { tappe: Tappa[]; etichetta: string }) {
  return (
    <ol className="ag-steps" aria-label={etichetta}>
      {tappe.map(t => (
        <li key={t.id} className={'ag-step is-' + t.stato} data-tappa={t.id} data-stato={t.stato}
          aria-current={t.stato === 'now' ? 'step' : undefined}>
          <span className="b">{t.stato === 'done' && <Check size={12} strokeWidth={3.5} />}</span>
          <span className="n">{t.nome}</span>
          <span className="d">{t.sotto}</span>
        </li>
      ))}
    </ol>
  );
}

export interface VistaDesk {
  id: string; nome: string; ruolo: string; colore: string; stato: StatoDesk;
  pastiglia: string; titolo: string;
  fare: { icona: 'tool' | 'think' | 'file' | 'ko' | 'wait' | 'pause'; testo: string; codice: boolean; sotto: string; tk?: string | null };
  round: StatoRound[]; dur: string; chiamate: string; costo: string; costoNd: boolean; dettaglio: string;
  esito: string;
}

export interface VistaCapo {
  stato: StatoDesk; titolo: string; sotto: string; pastiglia: string; colore: string | null;
  stadi: { id: string; nome: string; stato: 'ok' | 'on' | 'wait'; nota: string }[];
  azioni?: ReactNode;
}

export interface Riga { k: string; v: number | null; label: string; colore: string }

/** Barra orizzontale su fondo raised; v null = buco dichiarato, mai una barra a zero spacciata. */
export function Barra({ quota, colore }: { quota: number; colore: string }) {
  return <span className="ag-bar"><u style={{ width: `${Math.max(0, Math.min(1, quota)) * 100}%`, background: colore }} /></span>;
}

export function coloreBarra(colore: string | null | undefined) { return coloreDesk(colore); }
