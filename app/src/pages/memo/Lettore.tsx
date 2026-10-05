import type { ReactNode, RefObject } from 'react';
import { FileText, Paperclip, ScrollText, ShieldAlert, Sparkles } from 'lucide-react';
import type { Memo } from '@/lib/api';
import { API_BASE } from '@/lib/api';
import { fmtNum } from '@/lib/format';
import { inline, type MemoSezione } from '@/lib/memo-md';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { numeroSezione, titoloLeggibile, type DecisionePrioritaria, type Mandato } from './logica';
import { parole } from './parole';

export interface VoceIndice { id: string; titolo: string; bersaglio: string }

/** Colonna centrale: testata del memo, sintesi (BLUF + decisioni per il PM), indice e testo integrale. */
export default function Lettore({ sel, md, testoErr, caricando, nodi, sezioni, indice, sintesi, priorita, mandato,
  sezOn, onVai, docRef, vuoto }: {
  sel: Memo | null; md: string | undefined; testoErr: string | null; caricando: boolean;
  nodi: ReactNode[] | null; sezioni: MemoSezione[]; indice: VoceIndice[];
  sintesi: string[]; priorita: DecisionePrioritaria[]; mandato: Mandato | null;
  sezOn: string | null; onVai: (v: VoceIndice) => void; docRef: RefObject<HTMLDivElement>; vuoto?: string;
}) {
  const w = parole(), loc = localeDi(linguaCorrente());
  if (!sel) {
    return (
      <section className="bbn-card mm-reader" aria-label={w.noMemo}>
        <div className="mm-reader-empty"><p className="mm-empty"><b>{w.noMemo}</b><span>{vuoto || w.pick}</span></p></div>
      </section>
    );
  }
  const d = new Date(sel.timestamp), ok = Number.isFinite(d.getTime());
  const anno = ok && d.getFullYear() !== new Date().getFullYear();
  const titolo = w.memoOf(ok ? d.toLocaleDateString(loc, { day: 'numeric', month: 'long', ...(anno ? { year: 'numeric' } : {}) }) : w.nd);
  const quando = ok ? `${d.toLocaleDateString(loc, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })} · ${d.toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' })}` : w.nd;
  // le «Decisione N» del testo, nell'ordine: la card N porta alla sotto-sezione N
  const sottoDecisioni = sezioni.filter(s => s.livello === 3 && /^(?:Decisione|Decision)\s*\d/i.test(s.titolo));
  const dataMandato = mandato ? new Date(mandato.data + 'T12:00:00') : null;

  return (
    <section className="bbn-card mm-reader" aria-labelledby="mm-reader-t" data-qa="memo-reader">
      <header className="mm-det-head">
        <div className="mm-det-t">
          <span className="mm-kick">{w.kicker(sel.id, quando)}</span>
          <h2 id="mm-reader-t">{titolo}{sel.kind === 'trade_idea' && sel.label ? <span className="mm-tag">{sel.label}</span> : null}</h2>
          <span className="mm-meta">
            <span>{w.output(sel.output_language)}</span>
            {md ? <><span aria-hidden="true">·</span><span>{w.chars(fmtNum(md.length, 0))}</span></> : null}
            {sezioni.length > 0 && <><span aria-hidden="true">·</span><span>{w.sections(sezioni.length)}</span></>}
            {sel.title && <><span aria-hidden="true">·</span><span className="mm-orig" title={w.originalTitle(sel.title)}>{sel.title}</span></>}
          </span>
        </div>
        <div className="mm-det-actions" data-qa="memo-files">
          {/* l'allegato assente resta VISIBILE e spento: un bottone che sparisce non dice che il file non c'e' */}
          {sel.pdf_available
            ? <a className="bbn-btn is-sm" href={`${API_BASE}/memos/${sel.id}/pdf`} target="_blank" rel="noreferrer"><FileText size={14} aria-hidden="true" />{w.pdf}</a>
            : <span className="bbn-btn is-sm is-off" aria-disabled="true" title={w.pdfOff}><FileText size={14} aria-hidden="true" />{w.pdf}</span>}
          {sel.appendix_available
            ? <a className="bbn-btn is-sm" href={`${API_BASE}/memos/${sel.id}/appendix`} target="_blank" rel="noreferrer"><Paperclip size={14} aria-hidden="true" />{w.appendix}</a>
            : <span className="bbn-btn is-sm is-off" aria-disabled="true" title={w.appendixOff}><Paperclip size={14} aria-hidden="true" />{w.appendix}</span>}
        </div>
      </header>

      <div className="mm-reader-body" ref={docRef}>
        {mandato && dataMandato && (
          <span className="bbn-chip mm-mandate" title={w.mandateHint(mandato.impronta.slice(0, 8) + '…', mandato.origine)}>
            <ScrollText size={13} aria-hidden="true" />
            {w.mandate(Number.isFinite(dataMandato.getTime()) ? dataMandato.toLocaleDateString(loc, { day: 'numeric', month: 'short' }) : mandato.data)}
            {' · '}{mandato.origine}
          </span>
        )}

        {testoErr !== null ? <p className="mm-note is-bad" role="alert" data-qa="memo-text-error">{w.textErr(testoErr || w.unknown)}</p>
          : md === undefined || caricando ? <Caricamento testo={w.textLoading} />
          : md === '' ? <p className="mm-empty">{w.textEmpty}</p>
          : (
            <>
              {sintesi.length > 0 && (
                <div className="mm-sintesi" id="memo-sintesi" data-qa="memo-bluf">
                  <h3><Sparkles size={14} aria-hidden="true" />{w.summary}</h3>
                  <ul>{sintesi.map((r, i) => <li key={i}>{inline(r, `bluf-${i}`)}</li>)}</ul>
                </div>
              )}
              {priorita.length > 0 && (
                <div className="mm-dprio" data-qa="memo-priority">
                  {priorita.map((p, i) => {
                    const sotto = sottoDecisioni[i];
                    return (
                      <button key={i} type="button" className="mm-dp" disabled={!sotto}
                        onClick={() => sotto && onVai({ id: sotto.id, titolo: sotto.titolo, bersaglio: sotto.id })}>
                        <span className="mm-dp-n"><i>{i + 1}</i>{w.priority}</span>
                        <b>{inline(p.titolo, `dp-${i}`)}</b>
                        {p.obiezione && <span className="mm-dp-rt"><ShieldAlert size={13} aria-hidden="true" />{w.objection}</span>}
                      </button>
                    );
                  })}
                </div>
              )}
              {indice.length > 0 && (
                <nav className="mm-toc" aria-label={w.toc}>
                  {indice.map(v => {
                    const { n, testo } = numeroSezione(titoloLeggibile(v.titolo));
                    return (
                      <button key={v.id} type="button" aria-current={sezOn === v.id ? 'true' : undefined}
                        className={sezOn === v.id ? 'is-on' : undefined} title={v.titolo} onClick={() => onVai(v)}>
                        {n && <span className="mm-tn">{n}</span>}{testo}
                      </button>
                    );
                  })}
                </nav>
              )}
              <div className="mm-doc" data-qa="memo-doc">{nodi}</div>
            </>
          )}
      </div>
    </section>
  );
}

function Caricamento({ testo }: { testo: string }) {
  return (
    <div className="mm-loading" role="status">
      <span className="mm-sk" style={{ height: 120 }} />
      <span className="mm-sk" style={{ height: 12 }} /><span className="mm-sk" style={{ height: 12 }} />
      <span className="mm-sk" style={{ height: 12, width: '70%' }} />
      <p>{testo}</p>
    </div>
  );
}
