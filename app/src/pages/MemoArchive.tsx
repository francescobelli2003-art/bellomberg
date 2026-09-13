import { useT } from '@/i18n/provider';
import { t as tr } from '@/i18n/t';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Bellomberg, Memo, Decision, MemoSearchHit, API_BASE } from '@/lib/api';
import { fmtEUR, fmtNum } from '@/lib/format';
import { rendiMemo, sezioniDi, citazioni, MemoSezione } from '@/lib/memo-md';
import { useBox } from '@/lib/useBox';
import { AlertOctagon, RefreshCw, Search, X } from 'lucide-react';
import './dashboard-command.css';
import './memo-banco.css';

/* ============================================================================
   F9 v3 — "IL BANCO DI LETTURA" (impianto scelto dal PM 27/07 sui PNG di
   mockup_f9_memo: opzione B, contro A "la carta dell'archivio" e C
   "l'interrogatorio"; la ricerca di C e' innestata qui come overlay CTRL+K).

   Prima: 59 righe che rendevano titolo, data e due link. L'archivio dei memo
   NON faceva leggere un memo, su 536.846 caratteri di testo in DB.

   Cosa c'era e nessuno leggeva (misurato sul backend vivo, audit 23 + la
   ricognizione del 27/07):
     · GET /memos consegna 13 campi per memo, la pagina ne rendeva 3 —
       `portfolio_nav_eur`, `dcf_files`, `capo_tokens_*`, `notes` buttati
     · GET /memos/{id} ha il testo integrale: `memoById` era scritta in
       api.ts e non la chiamava NESSUNO
     · GET /memos/search interroga chunk gia' embeddati: zero consumatori
     · 193 decisioni sono agganciate ai 18 memo (0 orfane), e la pagina non
       le guardava

   Le tre cose che questa pagina dice e prima non diceva nessuno:
     1. quante run sono FALLITE (46 righe in DB, 18 leggibili: 28 soppresse
        da include_empty=false, e nessuno lo dichiarava)
     2. che fine ha fatto ogni riga della ACTION TABLE del memo
     3. su quali fonti si regge il memo (censimento dei tag [src: …])
   ========================================================================== */

/** Ordine di gravita' degli esiti: prima cio' che e' stato fatto, in fondo
 *  cio' che e' evaporato. Vale per il nastro, per la barra e per la legenda. */
const ORD = ['EXECUTED', 'PARTIAL', 'PENDING', 'SKIPPED', 'EXPIRED'] as const;
type Esito = typeof ORD[number];

const dd = (s: string) => {
  const d = new Date(s);
  return Number.isFinite(d.getTime()) ? d.toLocaleDateString(localeDi(linguaCorrente()), {
    day: '2-digit', month: '2-digit', year: '2-digit',
  }) : tr('memoarchive.f006');
};
const ts = (s: string) => new Date(s).getTime();
const nome = (p: string) => (p || '').split(/[\\/]/).pop()!.replace(/\.xlsx$/i, '');

/** I file DCF arrivano come stringa JSON. Se non lo e', non si tira a indovinare. */
function dcfDi(m: Memo): string[] | null {
  if (!m.dcf_files) return [];
  try {
    const v = JSON.parse(m.dcf_files);
    return Array.isArray(v) && v.every(x => typeof x === 'string') ? v : null;
  } catch { return null; }
}

function dettaglioErrore(e: any): string {
  return leggiDetail(e?.response?.data?.detail) || leggiDetail(e?.message) || leggiDetail(e);
}
function etichettaEsito(status: string): string {
  const keys = { EXECUTED: 'executed', PARTIAL: 'partial', PENDING: 'pending', SKIPPED: 'skipped', EXPIRED: 'expired' } as const;
  const key = keys[status as Esito];
  return key ? tr(`memoarchive.${key}`) : status;
}

interface Riga { act: string; tick: string; eur: string; timing: string; conf: string; dec: Decision | null }

/** La ACTION TABLE del memo, riga per riga, agganciata alle decisioni in DB.
 *  L'aggancio e' (ticker, azione) dentro lo stesso memo: misurato su tutto
 *  l'archivio tiene 187 righe su 189. Chi non aggancia porta `dec: null` e la
 *  pagina lo SCRIVE — non inventa un esito. */
function tabellaAzioni(md: string, dec: Decision[]): Riga[] {
  const idx = new Map<string, Decision>();
  dec.forEach(d => idx.set(`${(d.ticker || '').toUpperCase().trim()}|${(d.action || '').toUpperCase().trim()}`, d));
  const out: Riga[] = [];
  const righe = md.split('\n');
  let dentro = false;
  for (const riga of righe) {
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

export default function MemoArchive() {
  const tr = useT();
  const [memos, setMemos] = useState<Memo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Count only row-level evidence within the returned sample. Different
  // pagination limits cannot establish how many runs failed.
  const [campione, setCampione] = useState<{ rows: number; excluded: number | null } | null>(null);
  const [righeDbErr, setRigheDbErr] = useState<string | null>(null);

  const [dec, setDec] = useState<Decision[]>([]);
  const [decErr, setDecErr] = useState<string | null>(null);
  const [decLoading, setDecLoading] = useState(true);

  const [selId, setSelId] = useState<number | null>(null);
  const [testi, setTesti] = useState<Record<number, string>>({});
  const [testoErr, setTestoErr] = useState<string | null>(null);
  const [caricando, setCaricando] = useState(false);
  const [sezOn, setSezOn] = useState<string | null>(null);
  const [cerca, setCerca] = useState(false);

  const docRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    Bellomberg.memos(50)
      .then(r => {
        if (!Array.isArray(r?.memos)) { setErr(''); return; }
        const ms = r.memos.slice().sort((a, b) => ts(b.timestamp) - ts(a.timestamp));
        setMemos(ms);
        setSelId(prev => (prev !== null && ms.some(m => m.id === prev)) ? prev : (ms[0]?.id ?? null));
      })
      .catch(e => setErr(dettaglioErrore(e)))
      .finally(() => setLoading(false));

    setRigheDbErr(null); setCampione(null);
    Bellomberg.memosAll(200)
      .then(r => {
        const rows = r?.memos;
        if (!Array.isArray(rows)) { setRigheDbErr(''); return; }
        const declared = rows.every(m => typeof m.has_content === 'boolean' && typeof m.pdf_available === 'boolean');
        setCampione({ rows: rows.length, excluded: declared ? rows.filter(m => !m.has_content && !m.pdf_available).length : null });
      })
      .catch(e => { setCampione(null); setRigheDbErr(dettaglioErrore(e)); });

    setDecErr(null); setDecLoading(true);
    Bellomberg.decisions(undefined, 500)
      .then(r => {
        if (!Array.isArray(r?.decisions)) { setDec([]); setDecErr(''); return; }
        setDec(r.decisions);
      })
      .catch(e => { setDec([]); setDecErr(dettaglioErrore(e)); })
      .finally(() => setDecLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  // il testo integrale si carica al click e resta in cache: 40.987 char sul
  // memo piu' lungo, non si rifà il giro a ogni render
  useEffect(() => {
    setTestoErr(null);
    if (selId === null || testi[selId] !== undefined) { setCaricando(false); return; }
    let active = true;
    setCaricando(true);
    Bellomberg.memoById(selId)
      .then(m => {
        if (!active) return;
        if (!m || !(typeof m.full_markdown === 'string' || m.full_markdown === null)) { setTestoErr(''); return; }
        setTesti(t => ({ ...t, [selId]: m.full_markdown || '' }));
      })
      .catch(e => { if (active) setTestoErr(dettaglioErrore(e)); })
      .finally(() => { if (active) setCaricando(false); });
    return () => { active = false; };
  }, [selId, testi]);

  const decPerMemo = useMemo(() => {
    const m = new Map<number, Decision[]>();
    dec.forEach(d => {
      if (d.memo_id === null || d.memo_id === undefined) return;
      const a = m.get(d.memo_id) || []; a.push(d); m.set(d.memo_id, a);
    });
    m.forEach(a => a.sort((x, y) => ORD.indexOf(x.status as Esito) - ORD.indexOf(y.status as Esito)));
    return m;
  }, [dec]);

  const sel = memos.find(m => m.id === selId) || null;
  const md = selId !== null ? testi[selId] : undefined;
  const decSel = (selId !== null && decPerMemo.get(selId)) || [];

  const reso = useMemo(() => md ? rendiMemo(md, ['ACTION TABLE']) : null, [md, tr]);
  const sezioni: MemoSezione[] = reso ? reso.sezioni : (md ? sezioniDi(md) : []);
  const azioni = useMemo(() => md ? tabellaAzioni(md, decSel) : [], [md, decSel]);
  const fonti = useMemo(() => md ? citazioni(md) : [], [md]);

  const dcfInvalid = memos.some(m => dcfDi(m) === null);
  const totDcf = dcfInvalid ? null : memos.reduce((s, m) => s + dcfDi(m)!.length, 0);
  const totFlag = dcfInvalid ? null : memos.reduce((s, m) => s + dcfDi(m)!.filter(f => /_FLAGGED/i.test(f)).length, 0);
  const selDcf = sel ? dcfDi(sel) : [];
  const conEsito = dec.filter(d => d.outcome_pct !== null && d.outcome_pct !== undefined).length;

  const conta = (ds: Decision[]) => {
    const c: Partial<Record<Esito, number>> = {};
    ds.forEach(d => { const s = d.status as Esito; if (ORD.includes(s)) c[s] = (c[s] || 0) + 1; });
    return c;
  };
  const cSel = conta(decSel);
  const fatte = (cSel.EXECUTED || 0) + (cSel.PARTIAL || 0);

  const vaiA = (s: MemoSezione) => {
    setSezOn(s.id);
    docRef.current?.querySelector(`#${s.id}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  };

  // CTRL+SHIFT+F apre la ricerca. Il richiamo in barra fa la stessa cosa: se
  // la barra si vede, deve funzionare (una barra che non cerca e' una bugia).
  //
  // NON e' CTRL+K, e la ragione e' che CTRL+K e' GIA' la palette dei comandi
  // (CommandPalette.tsx:32, globale su tutta l'app): con quella scorciatoia lo
  // stesso tasto apriva due cose sovrapposte. Segnalato dal PM, verificato nel
  // codice. Libere restano solo le combinazioni con modificatori, perche'
  // Layout.tsx:159 si prende i tasti F1..F17 nudi per la navigazione.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault(); setCerca(true);
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  if (err !== null) {
    // Buco DICHIARATO (regola 14/07): un errore backend NON e' "nessun memo"
    // — il false-empty qui invitava a una run del consigliere da ~10 EUR.
    return (
      <div className="obsx f9b">
        <div className="p3">
          <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
          <div className="p3h am">{tr('memoarchive.f001')}</div>
          <div className="p-3 font-mono text-2xs text-crimson flex items-center gap-2 flex-wrap">
            <AlertOctagon size={13} />
            <span>{tr('memoarchive.f002')} {err || tr('memoarchive.errorUnknown')}{tr('memoarchive.f003')}</span>
            <button onClick={load} className="btn btn-cyan ml-auto"><RefreshCw size={11} /> {tr('memoarchive.f004')}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="obsx f9b">
      {/* ── barra d'assetto ──────────────────────────────────────────── */}
      <div className="bar">
        <span className="lab">{tr('memoarchive.f001')}</span>
        <span className="sep" />
        <span className="k">Memo</span><span className="v num"><b>{loading ? '…' : memos.length}</b></span>
        <span className="k">{tr('memoarchive.f005')}</span>
        <span className="v num">{decErr !== null ? <b className="ko">{tr('memoarchive.f006')}</b> : <b>{decLoading ? '…' : dec.length}</b>}</span>
        <span className="k">{tr('memoarchive.f007')}</span>
        <span className="v num"><b title={dcfInvalid ? tr('memoarchive.dcfInvalid') : undefined}>{loading ? '…' : totDcf ?? tr('memoarchive.f006')}</b>{totFlag !== null && totFlag > 0 && <> · <span className="ko">{totFlag} {tr('memoarchive.f008')}</span></>}</span>
        <span className="k">{tr('memoarchive.excludedLabel')}</span>
        <span className="v num">
          {righeDbErr !== null ? <b className="ko" title={righeDbErr || tr('memoarchive.errorUnknown')}>{tr('memoarchive.f006')}</b>
            : campione === null ? <b>…</b> : <b>{campione.excluded ?? tr('memoarchive.f006')} {tr('memoarchive.f043')} {campione.rows}</b>}
        </span>
        <span className="sep" />
        <button className="qcall" onClick={() => setCerca(true)} title={tr('memoarchive.f009')}>
          <Search size={12} color="#73829F" />
          <span className="ph">{tr('memoarchive.f010')}</span>
          <span className="kb">{tr('memoarchive.f011')}</span>
        </button>
      </div>

      <div className="body">
        {/* ── indice ─────────────────────────────────────────────────── */}
        <div className="cIdx">
          <div className="p3" style={{ flex: 1 }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h">{tr('memoarchive.f012')}<span className="side">{loading ? '…' : tr('memoarchive.f013', {a: memos.length})}</span></div>
            <div className="scroll" style={{ flex: 1 }}>
              {loading && <p className="px-3 py-2 text-2xs text-faint">{tr('memoarchive.f014')}</p>}
              {!loading && memos.length === 0 &&
                <p className="px-3 py-2 text-2xs text-faint">{tr('memoarchive.f015')}</p>}
              {memos.map(m => {
                const ds = decPerMemo.get(m.id) || [];
                return (
                  <div key={m.id} className={`mr${m.id === selId ? ' on' : ''}`} onClick={() => setSelId(m.id)}>
                    <span className="id">{m.id}</span>
                    <div className="mid">
                      <div className="dt">{dd(m.timestamp)}</div>
                      <div className="sub">
                        {decErr !== null ? tr('memoarchive.f016') : decLoading ? '…' : tr(ds.length === 1 ? 'memoarchive.oneDecision' : 'memoarchive.decisionsCount', { n: ds.length })}
                        {m.pdf_available ? ' · PDF' : ''}
                      </div>
                      {ds.length > 0 && (
                        <div className="nast" title={ORD.filter(s => conta(ds)[s]).map(s => `${conta(ds)[s]} ${etichettaEsito(s)}`).join(' · ')}>
                          {ds.map(d => <i key={d.id} className={`ex-${d.status}`} />)}
                        </div>
                      )}
                    </div>
                    <span className="nv num">
                      {typeof m.portfolio_nav_eur === 'number' && Number.isFinite(m.portfolio_nav_eur) ? fmtEUR(m.portfolio_nav_eur, false, 0) : <em className="text-faint">{tr('memoarchive.f017')}</em>}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="vuoti">
              {righeDbErr !== null ? tr('memoarchive.sampleError', { detail: righeDbErr || tr('memoarchive.errorUnknown') })
                : campione === null ? tr('memoarchive.sampleLoading')
                : campione.excluded === null ? tr('memoarchive.sampleUndeclared', { n: campione.rows })
                : tr('memoarchive.sampleCount', { excluded: campione.excluded, rows: campione.rows })}
            </div>
          </div>
        </div>

        {/* ── il memo ────────────────────────────────────────────────── */}
        <div className="cMid">
          <div className="p3" style={{ borderLeft: '2px solid rgba(255,165,30,.55)', flex: '0 0 auto' }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h am">
              {sel ? `Memo ${sel.id} · ${sel.title || tr('memoarchive.f018')}` : tr('memoarchive.f019')}
              {sel && <span className="side">{tr(sel.output_language === 'it' ? 'communications.originalOutputIt'
                : sel.output_language === 'en' ? 'communications.originalOutputEn' : 'communications.originalOutputUnknown')}</span>}
              <span className="side">
                {!sel ? '' : testoErr !== null ? tr('memoarchive.f030', { a: testoErr || tr('memoarchive.errorUnknown') }) : md === undefined ? tr('memoarchive.f020')
                  : azioni.length === 0 ? tr('memoarchive.f021')
                  : tr('memoarchive.f022', {a: azioni.length})}
              </span>
            </div>
            <div>
              {azioni.length > 0 ? (
                <table className="at">
                  {/* L'ultima colonna e' un RESPIRO senza nome. Senza, su un 49"
                      il timing si prende 4.000px e fra il suo testo e la
                      conviction resta una voragine: la tabella sembra rotta.
                      Cosi' le colonne restano impacchettate e leggibili a
                      qualunque larghezza. */}
                  <colgroup>
                    <col className="cAc" /><col className="cTk" /><col className="cEur" />
                    <col className="cTm" /><col className="cCf" /><col className="cEs" /><col />
                  </colgroup>
                  <thead><tr>
                    <th>{tr('memoarchive.f023')}</th><th>{tr('memoarchive.f024')}</th><th>EUR</th><th>{tr('memoarchive.f025')}</th><th>{tr('memoarchive.f026')}</th><th>{tr('memoarchive.f027')}</th><th />
                  </tr></thead>
                  <tbody>
                    {azioni.map((a, k) => (
                      <tr key={k} className={a.dec?.status === 'EXPIRED' ? 'evap' : undefined}>
                        <td className="ac">{a.act}</td>
                        <td className="tk">{a.tick}</td>
                        <td className="eur">{a.eur}</td>
                        <td>{a.timing}</td>
                        <td>{a.conf}</td>
                        <td>
                          <div className="esito" style={{ color: a.dec ? (a.dec.status === 'EXPIRED' ? '#FF3D60' : '#8D9FC4') : '#73829F' }}>
                            <i className={`ex-${a.dec ? a.dec.status : 'NONE'}`} />
                            {/* mai un esito dedotto: se il join non tiene, si scrive */}
                            <span>{a.dec ? etichettaEsito(a.dec.status) : tr('memoarchive.f028')}</span>
                          </div>
                        </td>
                        <td />
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-3 py-2 text-2xs text-faint">
                  {!sel ? tr('memoarchive.f029')
                    : testoErr !== null ? tr('memoarchive.f030', {a: testoErr || tr('memoarchive.errorUnknown')})
                    : caricando || md === undefined ? tr('memoarchive.f031')
                    : tr('memoarchive.f032')}
                </p>
              )}
            </div>
          </div>

          <div className="body" style={{ flex: 1 }}>
            <div className="p3 rail">
              <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
              <div className="p3h">{tr('memoarchive.f033')}<span className="side">{sezioni.length || ''}</span></div>
              <div className="scroll" style={{ flex: 1 }}>
                {sezioni.length === 0 && <p className="px-3 py-2 text-2xs text-faint">—</p>}
                {sezioni.map(s => (
                  <button key={s.id} title={s.titolo}
                    className={`sz${s.livello === 3 ? ' h3' : ''}${sezOn === s.id ? ' on' : ''}`}
                    onClick={() => vaiA(s)}>{s.titolo}</button>
                ))}
              </div>
            </div>

            <div className="p3" style={{ flex: 1 }}>
              <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
              <div className="p3h">
                {tr('memoarchive.f034')}
                <span className="side">{md ? tr('memoarchive.f035', {a: fmtNum(md.length, 0)}) : ''}</span>
              </div>
              <div className="scroll" style={{ flex: 1 }} ref={docRef}>
                {testoErr !== null ? (
                  <p className="px-4 py-3 text-2xs text-crimson">
                    {tr('memoarchive.f036')} {testoErr || tr('memoarchive.errorUnknown')}{tr('memoarchive.f037')}
                  </p>
                ) : md === undefined ? (
                  <p className="px-4 py-3 text-2xs text-faint">{tr('memoarchive.f031')}</p>
                ) : md === '' ? (
                  <p className="px-4 py-3 text-2xs text-faint">
                    {tr('memoarchive.f038')}<code>full_markdown</code> {tr('memoarchive.f039')}
                  </p>
                ) : (
                  <div className="doc">{reso!.nodi}</div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ── conseguenze e corredo ──────────────────────────────────── */}
        <div className="cDx">
          <div className="p3 cy" style={{ flex: '0 0 auto' }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h" style={{ color: '#29D3F2' }}>{tr('memoarchive.f040')}</div>
            <div className="verd">
              {decErr !== null ? (
                <p className="text-2xs text-crimson">{tr('memoarchive.f041')} {decErr || tr('memoarchive.errorUnknown')}</p>
              ) : decLoading ? <p className="text-2xs text-faint">{tr('memoarchive.f020')}</p> : decSel.length === 0 ? (
                <p className="text-2xs text-faint">{tr('memoarchive.f042')}</p>
              ) : (
                <>
                  <div className="big">{fatte} <em>{tr('memoarchive.f043')} {decSel.length}</em></div>
                  <div className="cap">{tr('memoarchive.f044')}</div>
                  <div className="stk">
                    {ORD.map(s => cSel[s] ? <i key={s} className={`ex-${s}`} style={{ flex: cSel[s] }} /> : null)}
                  </div>
                  <div className="stkleg">
                    {ORD.map(s => cSel[s] ? (
                      <span key={s}><i className={`ex-${s}`} />{cSel[s]} {etichettaEsito(s)}</span>
                    ) : null)}
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="p3" style={{ flex: '0 0 auto' }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h">{tr('memoarchive.f045')}<span className="side">{sel ? `MEMO ${sel.id}` : ''}</span></div>
            <div>
              <div className="kv"><span className="k">{tr('memoarchive.f046')}</span>
                <span className="v">{typeof sel?.portfolio_nav_eur === 'number' && Number.isFinite(sel.portfolio_nav_eur) ? fmtEUR(sel.portfolio_nav_eur, false, 0)
                  : <em>{tr('memoarchive.f047')}</em>}</span></div>
              <div className="kv"><span className="k">{tr('memoarchive.f048')}</span>
                {/* outcome_pct e' valorizzato su 0 righe su 193: si dichiara,
                    non si rende come zero (e mai colorato di verde) */}
                <span className="v"><em>{decErr !== null ? tr('memoarchive.f016') : decLoading ? '…' : conEsito === 0
                  ? tr('memoarchive.f049', {a: dec.length})
                  : tr('memoarchive.f050', {a: conEsito, b: dec.length})}</em></span></div>
              <div className="kv"><span className="k">{tr('memoarchive.f051')}</span>
                <span className="v">{sel && Number.isFinite(sel.capo_tokens_in) && Number.isFinite(sel.capo_tokens_out)
                  ? `${fmtNum(sel!.capo_tokens_in, 0)} in · ${fmtNum(sel!.capo_tokens_out, 0)} out`
                  : <em>{tr('memoarchive.f052')}</em>}</span></div>
              <div className="kv"><span className="k">{tr('memoarchive.f053')}</span>
                <span className="v">{md ? tr('memoarchive.f054', {a: fmtNum(md.length, 0), b: sezioni.length}) : <em>—</em>}</span></div>
              <div className="kv"><span className="k">{tr('memoarchive.f007')}</span>
                <span className="v">{selDcf === null ? <em className="text-crimson">{tr('memoarchive.dcfInvalid')}</em> : selDcf.length}
                  {selDcf?.some(f => /_FLAGGED/i.test(f)) &&
                    tr('memoarchive.f055', {a: selDcf.filter(f => /_FLAGGED/i.test(f)).length})}</span></div>
              {selDcf?.map(f => (
                <div className="file" key={f}>
                  <span className="nm">{nome(f)}</span>
                  {/_FLAGGED/i.test(f) && <span className="fg">{tr('memoarchive.f056')}</span>}
                </div>
              ))}
              <div className="apri">
                {/* l'allegato assente resta VISIBILE e spento, non sparisce:
                    un bottone che scompare non dice che il file non c'e' */}
                {sel?.pdf_available
                  ? <a href={`${API_BASE}/memos/${sel.id}/pdf`} target="_blank" rel="noreferrer">MEMO PDF</a>
                  : <span className="off">{tr('memoarchive.f057')}</span>}
                {sel?.appendix_available
                  ? <a href={`${API_BASE}/memos/${sel.id}/appendix`} target="_blank" rel="noreferrer">{tr('memoarchive.f058')}</a>
                  : <span className="off">{tr('memoarchive.f059')}</span>}
              </div>
              <div className="nota">
                <b>{tr('memoarchive.f060')}</b> {tr('memoarchive.f061')}
              </div>
            </div>
          </div>

          {/* su cosa si regge il memo: censimento dei tag [src: …] */}
          <div className="p3 vi" style={{ flex: 1 }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h vi">{tr('memoarchive.f062')}
              <span className="side">{fonti.length
                ? tr('memoarchive.f063', {a: fonti.reduce((s, f) => s + f.n, 0), b: fonti.length}) : ''}</span>
            </div>
            <div className="scroll" style={{ flex: 1 }}>
              {md === undefined ? <p className="px-3 py-2 text-2xs text-faint">—</p>
                : fonti.length === 0 ? (
                  <div className="nota" style={{ borderTop: 0 }}>
                    {tr('memoarchive.f064')} <code>[src: …]</code> {tr('memoarchive.f065')}
                  </div>
                ) : fonti.map(f => (
                  <div className="srcrow" key={f.fonte}>
                    <span className="nm" title={f.fonte}>{f.fonte}</span>
                    <span className="bb" style={{ width: Math.round(f.n / fonti[0].n * 82) }} />
                    <span className="ct num">{f.n}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>

      {cerca && (
        <RicercaMemo
          memos={memos}
          onChiudi={() => setCerca(false)}
          onApri={id => { setSelId(id); setCerca(false); }}
        />
      )}
    </div>
  );
}

/* ============================================================================
   LA RICERCA — innesto dell'opzione C dentro B (CTRL+K).
   Interroga i chunk gia' embeddati dell'archivio: l'endpoint esisteva da
   sempre e non lo chiamava nessuno.
   `distance` e' una distanza COSENO e si scrive com'e': tradurla in
   "rilevanza 87%" sarebbe un numero inventato.
   ========================================================================== */
function RicercaMemo({ memos, onChiudi, onApri }:
  { memos: Memo[]; onChiudi: () => void; onApri: (id: number) => void }) {
  const tr = useT();
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<MemoSearchHit[] | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const [errS, setErrS] = useState<string | null>(null);
  const [cercando, setCercando] = useState(false);
  const [sel, setSel] = useState(0);
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [asseRef, asse] = useBox<HTMLDivElement>();

  const byId = useMemo(() => new Map(memos.map(m => [m.id, m])), [memos]);
  const T0 = useMemo(() => Math.min(...memos.map(m => ts(m.timestamp))), [memos]);
  const T1 = useMemo(() => Math.max(...memos.map(m => ts(m.timestamp))), [memos]);

  const lancia = useCallback(() => {
    const testo = q.trim();
    if (!testo) return;
    setCercando(true); setErrS(null);
    const t0 = performance.now();
    Bellomberg.memosSearch(testo, 8)
      .then(r => {
        if (!Array.isArray(r?.results)) { setHits(null); setErrS(''); return; }
        setHits(r.results); setSel(0); setMs(Math.round(performance.now() - t0));
      })
      .catch(e => { setHits(null); setErrS(dettaglioErrore(e)); })
      .finally(() => setCercando(false));
  }, [q]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onChiudi(); return; }
      if (!hits || hits.length === 0) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(hits.length - 1, s + 1)); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(0, s - 1)); }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [hits, onChiudi]);

  const dMin = hits && hits.length ? Math.min(...hits.map(h => h.distance)) : 0;
  const dMax = hits && hits.length ? Math.max(...hits.map(h => h.distance)) : 1;
  const colpiti = new Set((hits || []).map(h => h.memo_id));

  // l'asse compatto: QUANDO l'ha detto e QUANTO e' vicino, in un segno solo.
  // Disegnato in pixel VERI sul box misurato (mai un viewBox stirato: il
  // testo dentro si stirerebbe con lui).
  const W = asse.w, H = asse.h || 104;
  const ML = 78, MR = 156, MARGIN_TOP = 15, yAx = H - 21;
  const px = (t: number) => T1 === T0 ? ML : ML + (t - T0) / (T1 - T0) * Math.max(1, W - ML - MR);
  const py = (d: number) => MARGIN_TOP + (dMax === dMin ? 0 : (d - dMin) / (dMax - dMin)) * (yAx - MARGIN_TOP - 14);

  return (
    <div className="f9b-scrim" onMouseDown={e => { if (e.target === box) onChiudi(); }} ref={setBox}>
      <div className="f9b-modal" onMouseDown={e => e.stopPropagation()}>
        <div className="mh">{tr('memoarchive.f066')}
          <span className="side">{memos.length} {tr('memoarchive.f067')}</span>
          <X size={13} style={{ cursor: 'pointer', color: '#73829F' }} onClick={onChiudi} />
        </div>
        <div className="mq">
          <Search size={17} color="#29D3F2" />
          <input autoFocus value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => {
              if (e.key !== 'Enter') return;
              e.preventDefault();
              if (hits && hits.length) onApri(hits[sel].memo_id); else lancia();
            }}
            placeholder={tr('memoarchive.f068')} />
          <span className="ms">
            {cercando ? tr('memoarchive.f069')
              : errS !== null ? <b style={{ color: '#FF3D60' }}>{tr('memoarchive.f070')}</b>
              : hits === null ? tr('memoarchive.f071')
              : <>{hits.length} {tr('memoarchive.f072')} <b>{ms} ms</b></>}
          </span>
        </div>

        {hits && hits.length > 0 && (
          <div ref={asseRef} className="masse" style={{ position: 'relative' }}>
            <svg width={W} height={H} style={{ position: 'absolute', inset: 0 }}>
              {[dMin, dMax].map((v, k) => (
                <g key={k}>
                  <line x1={ML} x2={Math.max(ML, W - MR)} y1={py(v)} y2={py(v)}
                    stroke={k === 0 ? '#25405A' : '#161F33'} strokeWidth={1} strokeDasharray={k ? '2 4' : undefined} />
                  <text x={ML - 9} y={py(v) + 4} textAnchor="end" fill="#73829F"
                    fontSize={10} fontWeight={600} fontFamily="JetBrains Mono, monospace">{fmtNum(v, 3)}</text>
                </g>
              ))}
              <text x={ML - 9} y={MARGIN_TOP - 5} textAnchor="end" fill="#29D3F2" fontSize={10} fontWeight={700}
                fontFamily="JetBrains Mono, monospace">{tr('memoarchive.f073')}</text>
              <line x1={ML} x2={Math.max(ML, W - MR)} y1={yAx} y2={yAx} stroke="#2A3760" strokeWidth={1} />
              {memos.map(m => {
                const on = colpiti.has(m.id), cx = px(ts(m.timestamp));
                return (
                  <g key={m.id}>
                    <line x1={cx} x2={cx} y1={yAx - 5} y2={yAx + 5} stroke={on ? '#29D3F2' : '#22304F'} strokeWidth={on ? 1.6 : 1} />
                    {/* 9px RESIDUO DICHIARATO (triage 03/08): gli id annotano le
                        tacche e stanno fitti lungo l'asse — a 10px due memo dello
                        stesso giorno collidono, e il modale non e' misurabile dal
                        cancello (si apre con CTRL+K). Se si alza, prima misurare. */}
                    <text x={cx} y={yAx + 16} textAnchor="middle" fill={on ? '#29D3F2' : '#4A5878'}
                      fontSize={9} fontWeight={600} fontFamily="JetBrains Mono, monospace">{m.id}</text>
                  </g>
                );
              })}
              {hits.map((h, i) => {
                const m = byId.get(h.memo_id); if (!m) return null;
                const cx = px(ts(m.timestamp)), cy = py(h.distance), on = i === sel;
                return (
                  <g key={h.chunk_id} style={{ cursor: 'pointer' }} onClick={() => setSel(i)}>
                    <line x1={cx} x2={cx} y1={cy} y2={yAx - 5} stroke={on ? '#29D3F2' : '#1D4C60'} strokeWidth={on ? 1.4 : 1} />
                    <circle cx={cx} cy={cy} r={on ? 5.5 : 3.6} fill={on ? '#29D3F2' : '#0A0F1C'} stroke="#29D3F2" strokeWidth={1.5} />
                    {on && <text x={cx + 10} y={cy + 4} fill="#74E6FF" fontSize={11} fontWeight={700}
                      fontFamily="JetBrains Mono, monospace">{fmtNum(h.distance, 4)}</text>}
                    <circle cx={cx} cy={cy} r={13} fill="transparent" />
                  </g>
                );
              })}
              {/* legenda corta: dentro un SVG una scritta lunga esce e si taglia zitta */}
              <rect x={W - MR + 14} y={MARGIN_TOP} width={9} height={9} fill="#29D3F2" />
              <text x={W - MR + 29} y={MARGIN_TOP + 8} fill="#8D9FC4" fontSize={10} fontWeight={600}
                fontFamily="JetBrains Mono, monospace">{colpiti.size} {tr('memoarchive.f074')}</text>
              <rect x={W - MR + 14} y={MARGIN_TOP + 19} width={9} height={9} fill="transparent" stroke="#22304F" />
              <text x={W - MR + 29} y={MARGIN_TOP + 27} fill="#8D9FC4" fontSize={10} fontWeight={600}
                fontFamily="JetBrains Mono, monospace">{memos.length - colpiti.size} {tr('memoarchive.f075')}</text>
            </svg>
          </div>
        )}

        <div className="mres">
          {errS !== null && (
            <div className="mvuoto">
              <b>{tr('memoarchive.f076')}</b> — {errS || tr('memoarchive.errorUnknown')}{tr('memoarchive.f077')}
            </div>
          )}
          {errS === null && hits === null && !cercando && (
            <div className="mvuoto">
              {tr('memoarchive.f078')} <b style={{ color: '#8D9FC4' }}>{tr('memoarchive.f079')}</b> {tr('memoarchive.f080')}
            </div>
          )}
          {errS === null && hits !== null && hits.length === 0 && (
            <div className="mvuoto">{tr('memoarchive.f081')}{q.trim()}”.</div>
          )}
          {errS === null && (hits || []).map((h, i) => {
            const m = byId.get(h.memo_id);
            return (
              <div key={h.chunk_id} className={`mhit${i === sel ? ' on' : ''}`}
                onClick={() => setSel(i)} onDoubleClick={() => onApri(h.memo_id)}>
                <div className="lft">
                  <div className="mm">MEMO {h.memo_id}</div>
                  <div className="dt">{m ? dd(m.timestamp) : tr('memoarchive.f082')}</div>
                  <div className="ds">{fmtNum(h.distance, 4)}</div>
                  <div className="rul">
                    <i style={{ width: `${Math.round(100 - (dMax === dMin ? 0 : (h.distance - dMin) / (dMax - dMin)) * 78)}%` }} />
                  </div>
                </div>
                <div className="sn">{(h.content || '').replace(/\s+/g, ' ').trim()}</div>
              </div>
            );
          })}
        </div>

        <div className="mf">
          <span><b>↑↓</b> {tr('memoarchive.f083')}</span>
          <span><b>{tr('memoarchive.f084')}</b> {tr('memoarchive.f085')}</span>
          <span><b>ESC</b> {tr('memoarchive.f086')}</span>
          <span><b>{tr('memoarchive.f011')}</b> {tr('memoarchive.f087')}</span>
          <span style={{ marginLeft: 'auto' }}>
            <b>{tr('memoarchive.f088')}</b> {tr('memoarchive.f089')}
          </span>
        </div>
      </div>
    </div>
  );
}
