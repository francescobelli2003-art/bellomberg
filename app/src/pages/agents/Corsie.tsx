import type { CSSProperties } from 'react';

/* Le corsie della run (redesign 05/10/2026): una riga per desk, il tempo scorre a destra.
   Ogni barra e' una FINESTRA misurata (dalla prima all'ultima chiamata del desk in quel
   round, allungata fino al presente se il backend lo dichiara in corsa): niente durate
   stimate. Le tacche sono le chiamate, appena accennate. */

export interface BarraCorsia { r: number; t0: number; t1: number; aperta: boolean; tacche: number[] }
export interface RigaCorsia {
  id: string; nome: string; ruolo: string; tinta: string; esito: string; dettaglio: string;
  chiamate: string; costo: string; costoNd: boolean; barre: BarraCorsia[]; report: boolean;
}
export interface DatiCorsie {
  righe: RigaCorsia[];
  capo: { nome: string; sotto: string; barre: { t0: number; t1: number; aperta: boolean; etichetta: string }[]; costo: string; costoNd: boolean; memo: boolean };
  fasi: { k: string; t0: number; etichetta: string }[];
  T: number; ora: number | null; vuota: boolean; parziale: boolean;
}
export interface TestiCorsie { titolo: string; hint: string; vuoto: string; parziale: string; report: string; memo: string }

const pct = (v: number, T: number) => `${(Math.max(0, Math.min(1, v / T)) * 100).toFixed(3)}%`;

export default function Corsie({ dati, testi, onReport, onMemo }: {
  dati: DatiCorsie; testi: TestiCorsie; onReport: (id: string) => void; onMemo: () => void;
}) {
  const { T } = dati;
  const passo = T <= 3600 ? 300 : T <= 7200 ? 600 : 1800;
  const tacche: number[] = [];
  for (let s = 0; s <= T; s += passo) tacche.push(s);
  const bande = dati.fasi.map(f => <span key={f.k} className="ag-banda" style={{ left: pct(f.t0, T) }} />);
  const ora = dati.ora != null ? <span className="ag-ora" style={{ left: pct(dati.ora, T) }} /> : null;
  const asse = (sotto: boolean) => (
    <div className={'ag-asse' + (sotto ? ' is-sotto' : '')} aria-hidden="true">
      <span /><div className="pista">{sotto
        ? tacche.map(s => <span key={s} style={{ left: pct(s, T) }}>{Math.round(s / 60)}′</span>)
        : dati.fasi.map(f => <span key={f.k} className="f" style={{ left: pct(f.t0, T) }}>{f.etichetta}</span>)}</div>
      <span /><span /><span />
    </div>
  );

  return (
    <section className="bbn-card ag-corsie" aria-label={testi.titolo}>
      <header className="bbn-card-head"><h2>{testi.titolo}</h2><span className="bbn-grow" />
        {!dati.vuota && <span className="bbn-card-note">{testi.hint}</span>}</header>
      {dati.vuota ? <p className="bbn-empty">{testi.vuoto}</p> : <div className="ag-lanes">
        {asse(false)}
        {dati.righe.map(r => (
          <div key={r.id} className="ag-lane" data-agente={r.id} data-esito={r.esito} title={r.dettaglio}
            style={{ '--tinta': r.tinta } as CSSProperties}>
            <span className="lab"><i /><span className="nm"><b>{r.nome}</b><span>{r.ruolo}</span></span></span>
            <div className="pista">{bande}
              {r.barre.map(b => (
                <span key={b.r} className={'ag-barra r' + b.r + (b.aperta ? ' is-aperta' : '')}
                  style={{ left: pct(b.t0, T), width: `max(6px, ${pct(b.t1 - b.t0, T)})` }}>
                  <b>R{b.r}</b>
                  {b.tacche.map((t, k) => <i key={k} style={{ left: `${(b.t1 > b.t0 ? (t - b.t0) / (b.t1 - b.t0) * 100 : 0).toFixed(2)}%` }} />)}
                </span>
              ))}{ora}</div>
            <span className="cnt"><span>{r.chiamate}</span></span>
            <span className={'eur num' + (r.costoNd ? ' is-nd' : '')}>{r.costo}</span>
            <button type="button" className="ag-rep" data-report={r.id} disabled={!r.report} onClick={() => onReport(r.id)}>{testi.report}</button>
          </div>
        ))}
        <div className="ag-lane is-capo" data-agente="capo">
          <span className="lab"><i /><span className="nm"><b>{dati.capo.nome}</b><span>{dati.capo.sotto}</span></span></span>
          <div className="pista">{bande}
            {dati.capo.barre.map((b, k) => (
              <span key={k} className={'ag-barra' + (b.aperta ? ' is-aperta' : '')}
                style={{ left: pct(b.t0, T), width: `max(6px, ${pct(b.t1 - b.t0, T)})` }}><b>{b.etichetta}</b></span>
            ))}{ora}</div>
          <span className="cnt" />
          <span className={'eur num' + (dati.capo.costoNd ? ' is-nd' : '')}>{dati.capo.costo}</span>
          <button type="button" className="ag-rep" data-memo="1" disabled={!dati.capo.memo} onClick={onMemo}>{testi.memo}</button>
        </div>
        {asse(true)}
        {dati.parziale && <p className="ag-foot ag-lanes-nota">{testi.parziale}</p>}
      </div>}
    </section>
  );
}
