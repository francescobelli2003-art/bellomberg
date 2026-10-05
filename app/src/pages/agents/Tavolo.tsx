import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import { AlertCircle, Check } from 'lucide-react';
import IconaDesk from '../chat/IconaDesk';
import type { StatoDesk, StatoRound } from './VistaAgenti';

/* Il tavolo del comitato (redesign 05/10/2026): il Capo al centro, i desk in cerchio.
   Solo vernice: stati, frasi e report arrivano gia' calcolati da AgentsLive.
   Il movimento racconta fatti del payload e nient'altro: un impulso per ogni chiamata
   NUOVA nel tool_log, un'onda per ogni report NUOVO; all'apertura della pagina il
   pregresso non si anima. Con «riduci movimento» del sistema non si anima nulla. */

export interface NodoDesk {
  id: string; nome: string; colore: string | null; tinta: string; stato: StatoDesk;
  pastiglia: string; titolo: string; esito: string; round: StatoRound[];
  fare: { testo: string; tk: string | null; codice: boolean; sotto: string };
}
export interface Conclusione { key: string; id: string; nome: string; tinta: string; round: number; ora: string; frase: string }
export interface Angoli { crono: string; sotto: string; avanz: number | null; legenda: { k: string; testo: string; n: number }[] }
export interface DatiTavolo {
  nodi: NodoDesk[];
  capo: { stato: StatoDesk; colore: string | null; segmenti: string[]; attesi: number | null; testo: ReactNode };
  vivo: boolean;
  /** chiavi stabili delle chiamate e dei report visti: le nuove generano un impulso */
  chiamate: { key: string; a: string }[];
  report: { key: string; a: string }[];
  angoli: Angoli | null;
  conclusioni: Conclusione[];
}
export interface TestiTavolo {
  etichetta: string; elapsed: string; legenda: string;
  conclTitolo: string; conclHint: string; conclVuoto: string; roundBreve: (r: number) => string;
}

const NS = 'http://www.w3.org/2000/svg';
const PASTIGLIA_PROBLEMA: Partial<Record<StatoDesk, string>> = { ko: 'is-giu', nd: 'is-warn', stale: 'is-warn' };

/** arco di cerchio da a0 a a1 gradi (0 = ore 3, senso orario) */
function arco(cx: number, cy: number, r: number, a0: number, a1: number) {
  const p = (a: number) => [cx + Math.cos(a * Math.PI / 180) * r, cy + Math.sin(a * Math.PI / 180) * r];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

type Posto = { x: number; y: number; lato: 'su' | 'giu' | 'dx' | 'sx' };

/** Geometria in pixel veri sul box misurato: il cerchio si adatta, le scritte no. */
function geometria(w: number, h: number, n: number) {
  const cx = w / 2, cy = h / 2;
  /* etichette ai lati da 900 px in su; sotto, tutte sotto i nodi (e il riquadro si alza).
     Decide solo la larghezza: l'altezza dipende da questa scelta, non il contrario. */
  const stretto = w < 900;
  const R = stretto ? Math.max(90, Math.min(h / 2 - 124, w / 2 - 94))
    : Math.max(110, Math.min(290, h / 2 - 122, (w / 2 - 272) / 0.866));
  const posti: Posto[] = Array.from({ length: n }, (_, i) => {
    const a = (-90 + i * 360 / Math.max(1, n)) * Math.PI / 180;
    const x = cx + Math.cos(a) * R, y = cy + Math.sin(a) * R;
    const s = Math.sin(a), c = Math.cos(a);
    const lato: Posto['lato'] = stretto ? (s < -0.6 ? 'su' : 'giu') : s < -0.6 ? 'su' : s > 0.6 ? 'giu' : c > 0 ? 'dx' : 'sx';
    return { x, y, lato };
  });
  return { cx, cy, R, posti, stretto };
}

function Archi({ round, tinta }: { round: StatoRound[]; tinta: string }) {
  return (
    <svg className="ag-archi" viewBox="0 0 84 84" aria-hidden="true">
      {round.map((r, k) => (
        <path key={k} d={arco(42, 42, 39, -150 + k * 120 + 9, -150 + (k + 1) * 120 - 9)}
          className={'seg' + (r ? ' is-' + r : '')} style={r === 'ok' || r === 'on' ? { stroke: tinta } : undefined} />
      ))}
    </svg>
  );
}

function Fare({ f }: { f: NodoDesk['fare'] }) {
  if (f.codice) return <code>{f.testo}</code>;
  if (f.tk && f.testo.includes(f.tk)) {
    const i = f.testo.indexOf(f.tk);
    return <span>{f.testo.slice(0, i)}<b className="tk">{f.tk}</b>{f.testo.slice(i + f.tk.length)}</span>;
  }
  return <span>{f.testo}</span>;
}

export default function Tavolo({ dati, testi, box, areaRef, onApri }: {
  dati: DatiTavolo; testi: TestiTavolo; box: { w: number; h: number };
  areaRef: React.Ref<HTMLDivElement>; onApri: (id: string, round?: number) => void;
}) {
  const { cx, cy, R, posti, stretto } = geometria(box.w, box.h, dati.nodi.length);
  const partRef = useRef<SVGGElement>(null);
  const capoRef = useRef<HTMLDivElement>(null);
  const viste = useRef<{ chiamate: Set<string>; report: Set<string> } | null>(null);
  const geo = useRef({ cx, cy, posti, nodi: dati.nodi });
  geo.current = { cx, cy, posti, nodi: dati.nodi };

  /* impulsi: solo per cio' che arriva DOPO la prima lettura, e solo a run viva */
  useEffect(() => {
    const prima = viste.current == null;
    const ora = { chiamate: new Set(dati.chiamate.map(c => c.key)), report: new Set(dati.report.map(r => r.key)) };
    const prec = viste.current;
    viste.current = ora;
    if (prima || !prec || !dati.vivo || !box.w) return;
    if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const nuoveC = dati.chiamate.filter(c => !prec.chiamate.has(c.key)).slice(-6);
    const nuoviR = dati.report.filter(r => !prec.report.has(r.key));
    nuoveC.forEach((c, i) => setTimeout(() => impulso(c.a, false), i * 140));
    nuoviR.forEach((r, i) => setTimeout(() => impulso(r.a, true), 200 + i * 260));
  }, [dati.chiamate, dati.report, dati.vivo, box.w]);

  function impulso(id: string, grande: boolean) {
    const g = partRef.current, { cx, cy, posti, nodi } = geo.current;
    const i = nodi.findIndex(n => n.id === id);
    if (!g || i < 0 || typeof g.animate !== 'function') return;
    const { x, y } = posti[i], tinta = nodi[i].tinta;
    const d = Math.hypot(cx - x, cy - y) || 1, ux = (cx - x) / d, uy = (cy - y) / d;
    const x0 = x + ux * 40, y0 = y + uy * 40, x1 = cx - ux * 60, y1 = cy - uy * 60;
    const punto = document.createElementNS(NS, 'circle');
    punto.setAttribute('r', grande ? '5' : '3');
    punto.setAttribute('class', grande ? 'ag-punto is-report' : 'ag-punto');
    punto.style.fill = grande ? 'var(--bbn-text)' : tinta;
    g.appendChild(punto);
    punto.animate([
      { transform: `translate(${x0}px, ${y0}px)`, opacity: 0 },
      { transform: `translate(${x0 + (x1 - x0) * .12}px, ${y0 + (y1 - y0) * .12}px)`, opacity: 1, offset: .12 },
      { transform: `translate(${x1}px, ${y1}px)`, opacity: grande ? 1 : 0 },
    ], { duration: grande ? 1300 : 1050, easing: 'cubic-bezier(.45,0,.25,1)' }).onfinish = () => {
      punto.remove();
      if (grande) capoRef.current?.animate([{ boxShadow: `0 0 0 0 ${tinta}` }, { boxShadow: `0 0 0 22px transparent` }], { duration: 1100, easing: 'ease-out' });
    };
    const filo = g.parentElement?.querySelector<SVGLineElement>(`[data-filo="${CSS.escape(id)}"]`);
    if (grande) filo?.animate([{ strokeOpacity: .95, strokeWidth: 2.6 }, { strokeOpacity: .2, strokeWidth: 1.2 }], { duration: 1600, easing: 'ease-out' });
    else {
      const onda = document.createElementNS(NS, 'circle');
      onda.setAttribute('cx', String(x)); onda.setAttribute('cy', String(y)); onda.setAttribute('r', '34');
      onda.setAttribute('class', 'ag-onda'); onda.style.stroke = tinta;
      g.appendChild(onda);
      onda.animate([{ transform: 'scale(1)', opacity: .55 }, { transform: 'scale(1.32)', opacity: 0 }], { duration: 950, easing: 'ease-out' }).onfinish = () => onda.remove();
    }
  }

  const attivo = (s: StatoDesk) => s === 'run' || s === 'think';
  const nSeg = Math.max(dati.capo.attesi ?? 0, dati.capo.segmenti.length, 1);
  const a = dati.angoli;

  return (
    <div className={'ag-tav' + (dati.vivo ? ' is-vivo' : '') + (stretto ? ' is-stretto' : '')}>
      <div className="ag-tav-area" ref={areaRef} role="group" aria-label={testi.etichetta}>
        {box.w > 0 && <>
          <svg className="ag-fili" width={box.w} height={box.h} aria-hidden="true">
            <circle className="ag-anello" cx={cx} cy={cy} r={R} />
            {dati.nodi.map((n, i) => (
              <line key={n.id} data-filo={n.id} x1={posti[i].x} y1={posti[i].y} x2={cx} y2={cy}
                className={'ag-filo' + (attivo(n.stato) ? ' is-attivo' : '')} style={{ stroke: n.tinta }} />
            ))}
            <g ref={partRef} />
          </svg>

          <div className={'ag-capo-c is-' + dati.capo.stato} ref={capoRef} style={{ left: cx, top: cy }} data-agente="capo" data-stato={dati.capo.stato}>
            <svg className="ag-capo-anello" viewBox="0 0 140 140" aria-hidden="true">
              {Array.from({ length: nSeg }, (_, k) => {
                const w = 360 / nSeg, gap = nSeg > 1 ? Math.min(5, w * .25) : 0;
                return <path key={k} d={nSeg === 1 ? arco(70, 70, 64, -90, 269.9) : arco(70, 70, 64, -90 + k * w + gap / 2, -90 + (k + 1) * w - gap / 2)}
                  className={'seg' + (dati.capo.segmenti[k] ? ' is-ok' : '')} style={dati.capo.segmenti[k] ? { stroke: dati.capo.segmenti[k] } : undefined} />;
              })}
            </svg>
            <IconaDesk id="capo" colore={dati.capo.colore} dimensione="md" />
            <span className="n">{dati.capo.testo}</span>
          </div>

          {dati.nodi.map((n, i) => {
            const p = posti[i];
            const problema = PASTIGLIA_PROBLEMA[n.stato];
            return (
              <button key={n.id} type="button" className={`ag-desk is-${n.stato} is-${p.lato}`}
                style={{ left: p.x, top: p.y, '--tinta': n.tinta } as CSSProperties}
                data-agente={n.id} data-esito={n.esito} data-stato={n.stato} title={n.titolo}
                onClick={() => onApri(n.id)}>
                <span className="ag-nodo">
                  <span className="ag-alone" />
                  <Archi round={n.round} tinta={n.tinta} />
                  <IconaDesk id={n.id} colore={n.colore} dimensione="md" />
                  {n.stato === 'ok' && <span className="ag-badge is-ok"><Check size={11} strokeWidth={3} /></span>}
                  {n.stato === 'ko' && <span className="ag-badge is-ko"><AlertCircle size={11} strokeWidth={3} /></span>}
                  {n.stato === 'nd' && <span className="ag-badge is-nd">!</span>}
                </span>
                <span className="ag-lbl">
                  <span className="nm"><b>{n.nome}</b></span>
                  <span className="ag-doing" title={n.fare.sotto || undefined}><Fare f={n.fare} /></span>
                  {problema && <span className={'bbn-pill ' + problema}>{n.pastiglia}</span>}
                </span>
              </button>
            );
          })}

          {a && <>
            <div className="ag-angolo is-sx">
              <span className="k">{testi.elapsed}</span>
              <b className="crono num">{a.crono}</b>
              <span className="s">{a.sotto}</span>
              {a.avanz != null && <span className="ag-avanz"><u style={{ width: `${Math.round(Math.max(0, Math.min(1, a.avanz)) * 100)}%` }} /></span>}
            </div>
            <div className="ag-angolo is-dx">
              <span className="k">{testi.legenda}</span>
              <ul className="ag-legenda">{a.legenda.map(l => (
                <li key={l.k} className={'is-' + l.k}>{l.testo}<b className="num">{l.n}</b><i /></li>
              ))}</ul>
            </div>
          </>}
        </>}
      </div>

      <div className="ag-concl">
        <div className="ag-concl-h"><b>{testi.conclTitolo}</b>{dati.conclusioni.length > 0 && <span>{testi.conclHint}</span>}</div>
        {dati.conclusioni.length === 0 ? <p className="bbn-empty">{testi.conclVuoto}</p> : (
          <div className="ag-concl-l">{dati.conclusioni.map(c => (
            <button key={c.key} type="button" className="ag-concl-i" data-concl={c.id} data-round={c.round}
              style={{ '--tinta': c.tinta } as CSSProperties} onClick={() => onApri(c.id, c.round)}>
              <span className="h"><b>{c.nome}</b>{testi.roundBreve(c.round)}<em className="num">{c.ora}</em></span>
              <span className="f">{c.frase}</span>
            </button>
          ))}</div>
        )}
      </div>
    </div>
  );
}
