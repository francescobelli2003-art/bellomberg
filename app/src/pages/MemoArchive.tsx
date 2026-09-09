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
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getFullYear()).slice(2)}`;
};
const ts = (s: string) => new Date(s).getTime();
const nome = (p: string) => (p || '').split(/[\\/]/).pop()!.replace(/\.xlsx$/i, '');

/** I file DCF arrivano come stringa JSON. Se non lo e', non si tira a indovinare. */
function dcfDi(m: Memo): string[] {
  if (!m.dcf_files) return [];
  try {
    const v = JSON.parse(m.dcf_files);
    return Array.isArray(v) ? v.filter(x => typeof x === 'string') : [];
  } catch { return []; }
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
    if (/^action$/i.test(c[0])) continue;
    out.push({
      act: c[0], tick: c[1], eur: c[2] || '', timing: c[3] || '', conf: c[4] || '',
      dec: idx.get(`${(c[1] || '').toUpperCase().trim()}|${(c[0] || '').toUpperCase().trim()}`) || null,
    });
  }
  return out;
}

export default function MemoArchive() {
  const [memos, setMemos] = useState<Memo[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // conteggio delle run fallite: chiamata a parte, con buco DICHIARATO se
  // fallisce (non si spaccia "0 fallite" per una misura riuscita)
  const [righeDb, setRigheDb] = useState<number | null>(null);
  const [righeDbErr, setRigheDbErr] = useState<string | null>(null);

  const [dec, setDec] = useState<Decision[]>([]);
  const [decErr, setDecErr] = useState<string | null>(null);

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
        const ms = (r.memos || []).slice().sort((a, b) => ts(b.timestamp) - ts(a.timestamp));
        setMemos(ms);
        setSelId(prev => (prev !== null && ms.some(m => m.id === prev)) ? prev : (ms[0]?.id ?? null));
      })
      .catch(e => setErr(e?.response?.data?.detail || e?.message || String(e)))
      .finally(() => setLoading(false));

    setRigheDbErr(null);
    Bellomberg.memosAll(200)
      .then(r => setRigheDb((r.memos || []).length))
      .catch(e => { setRigheDb(null); setRigheDbErr(e?.response?.data?.detail || e?.message || String(e)); });

    setDecErr(null);
    Bellomberg.decisions(undefined, 500)
      .then(r => setDec(r.decisions || []))
      .catch(e => { setDec([]); setDecErr(e?.response?.data?.detail || e?.message || String(e)); });
  }, []);
  useEffect(() => { load(); }, [load]);

  // il testo integrale si carica al click e resta in cache: 40.987 char sul
  // memo piu' lungo, non si rifà il giro a ogni render
  useEffect(() => {
    if (selId === null || testi[selId] !== undefined) return;
    setCaricando(true); setTestoErr(null);
    Bellomberg.memoById(selId)
      .then(m => setTesti(t => ({ ...t, [selId]: m.full_markdown || '' })))
      .catch(e => setTestoErr(e?.response?.data?.detail || e?.message || String(e)))
      .finally(() => setCaricando(false));
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

  const reso = useMemo(() => md ? rendiMemo(md, ['ACTION TABLE']) : null, [md]);
  const sezioni: MemoSezione[] = reso ? reso.sezioni : (md ? sezioniDi(md) : []);
  const azioni = useMemo(() => md ? tabellaAzioni(md, decSel) : [], [md, decSel]);
  const fonti = useMemo(() => md ? citazioni(md) : [], [md]);

  const totDcf = memos.reduce((s, m) => s + dcfDi(m).length, 0);
  const totFlag = memos.reduce((s, m) => s + dcfDi(m).filter(f => /_FLAGGED/i.test(f)).length, 0);
  const conEsito = dec.filter(d => d.outcome_pct !== null && d.outcome_pct !== undefined).length;
  const fallite = righeDb === null ? null : Math.max(0, righeDb - memos.length);

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

  if (err) {
    // Buco DICHIARATO (regola 14/07): un errore backend NON e' "nessun memo"
    // — il false-empty qui invitava a una run del consigliere da ~10 EUR.
    return (
      <div className="obsx f9b">
        <div className="p3">
          <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
          <div className="p3h am">Archivio del comitato</div>
          <div className="p-3 font-mono text-2xs text-crimson flex items-center gap-2 flex-wrap">
            <AlertOctagon size={13} />
            <span>ARCHIVIO MEMO NON DISPONIBILE — {err}. I memo salvati NON sono persi: backend non raggiungibile o in errore.</span>
            <button onClick={load} className="btn btn-cyan ml-auto"><RefreshCw size={11} /> RETRY</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="obsx f9b">
      {/* ── barra d'assetto ──────────────────────────────────────────── */}
      <div className="bar">
        <span className="lab">Archivio del comitato</span>
        <span className="sep" />
        <span className="k">Memo</span><span className="v num"><b>{loading ? '…' : memos.length}</b></span>
        <span className="k">Decisioni</span>
        <span className="v num">{decErr ? <b className="ko">n.d.</b> : <b>{dec.length}</b>}</span>
        <span className="k">Modelli DCF</span>
        <span className="v num"><b>{totDcf}</b>{totFlag > 0 && <> · <span className="ko">{totFlag} flagged</span></>}</span>
        <span className="k">Run fallite</span>
        <span className="v num">
          {righeDbErr ? <b className="ko" title={righeDbErr}>n.d.</b>
            : fallite === null ? <b>…</b> : <b>{fallite} su {righeDb}</b>}
        </span>
        <span className="sep" />
        <button className="qcall" onClick={() => setCerca(true)} title="ricerca semantica nell'archivio">
          <Search size={12} color="#73829F" />
          <span className="ph">cerca dentro l’archivio — il testo di tutti i memo</span>
          <span className="kb">CTRL+MAIUSC+F</span>
        </button>
      </div>

      <div className="body">
        {/* ── indice ─────────────────────────────────────────────────── */}
        <div className="cIdx">
          <div className="p3" style={{ flex: 1 }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h">Archivio<span className="side">{loading ? '…' : `${memos.length} LEGGIBILI`}</span></div>
            <div className="scroll" style={{ flex: 1 }}>
              {loading && <p className="px-3 py-2 text-2xs text-faint">caricamento archivio…</p>}
              {!loading && memos.length === 0 &&
                <p className="px-3 py-2 text-2xs text-faint">Nessun memo ancora. Lancia il consigliere.</p>}
              {memos.map(m => {
                const ds = decPerMemo.get(m.id) || [];
                return (
                  <div key={m.id} className={`mr${m.id === selId ? ' on' : ''}`} onClick={() => setSelId(m.id)}>
                    <span className="id">{m.id}</span>
                    <div className="mid">
                      <div className="dt">{dd(m.timestamp)}</div>
                      <div className="sub">
                        {decErr ? 'decisioni n.d.' : `${ds.length} decision${ds.length === 1 ? 'e' : 'i'}`}
                        {m.pdf_available ? ' · PDF' : ''}
                      </div>
                      {ds.length > 0 && (
                        <div className="nast" title={ORD.filter(s => conta(ds)[s]).map(s => `${conta(ds)[s]} ${s}`).join(' · ')}>
                          {ds.map(d => <i key={d.id} className={`ex-${d.status}`} />)}
                        </div>
                      )}
                    </div>
                    <span className="nv num">
                      {m.portfolio_nav_eur ? fmtEUR(m.portfolio_nav_eur, false, 0) : <em className="text-faint">NAV n.d.</em>}
                    </span>
                  </div>
                );
              })}
            </div>
            {/* le run fallite non stanno in elenco, ma si dichiarano */}
            <div className="vuoti">
              {righeDbErr
                ? <>Quante run siano <b>FALLITE</b> non è misurabile adesso: la lettura con <code>include_empty</code> è in errore — {righeDbErr}</>
                : fallite === null ? <>conteggio delle run fallite in corso…</>
                : <><b>{fallite} run fallite</b> non sono in questa lista: il backend le filtra con <code>include_empty=false</code> perché hanno meno di 100 caratteri di testo. In DB le righe sono {righeDb}, qui se ne leggono {memos.length}.</>}
            </div>
          </div>
        </div>

        {/* ── il memo ────────────────────────────────────────────────── */}
        <div className="cMid">
          <div className="p3" style={{ borderLeft: '2px solid rgba(255,165,30,.55)', flex: '0 0 auto' }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h am">
              {sel ? `Memo ${sel.id} · ${sel.title || 's.t.'}` : 'Nessun memo selezionato'}
              <span className="side">
                {!sel ? '' : md === undefined ? 'CARICAMENTO…'
                  : azioni.length === 0 ? 'NESSUNA ACTION TABLE IN QUESTO MEMO'
                  : `ACTION TABLE — ${azioni.length} RIGHE, AGGANCIATE ALLE DECISIONI`}
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
                    <th>Azione</th><th>Titolo</th><th>EUR</th><th>Timing</th><th>Conviction</th><th>Esito reale</th><th />
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
                            <span>{a.dec ? a.dec.status : 'NON AGGANCIATA'}</span>
                          </div>
                        </td>
                        <td />
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="px-3 py-2 text-2xs text-faint">
                  {!sel ? 'Scegli un memo dall’archivio.'
                    : testoErr ? `TESTO DEL MEMO NON DISPONIBILE — ${testoErr}`
                    : caricando || md === undefined ? 'caricamento del testo…'
                    : 'Questo memo non ha una ACTION TABLE: il testo qui sotto è comunque integrale.'}
                </p>
              )}
            </div>
          </div>

          <div className="body" style={{ flex: 1 }}>
            <div className="p3 rail">
              <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
              <div className="p3h">Sezioni<span className="side">{sezioni.length || ''}</span></div>
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
                Il memo
                <span className="side">{md ? `${fmtNum(md.length, 0)} CHAR` : ''}</span>
              </div>
              <div className="scroll" style={{ flex: 1 }} ref={docRef}>
                {testoErr ? (
                  <p className="px-4 py-3 text-2xs text-crimson">
                    TESTO NON DISPONIBILE — {testoErr}. Il memo non è perso: il PDF resta scaricabile qui accanto.
                  </p>
                ) : md === undefined ? (
                  <p className="px-4 py-3 text-2xs text-faint">caricamento del testo…</p>
                ) : md === '' ? (
                  <p className="px-4 py-3 text-2xs text-faint">
                    Questo memo non ha testo in archivio (<code>full_markdown</code> vuoto): è una run che non ha prodotto il documento.
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
            <div className="p3h" style={{ color: '#29D3F2' }}>Che fine ha fatto questo memo</div>
            <div className="verd">
              {decErr ? (
                <p className="text-2xs text-crimson">DECISIONI NON DISPONIBILI — {decErr}</p>
              ) : decSel.length === 0 ? (
                <p className="text-2xs text-faint">Nessuna decisione agganciata a questo memo.</p>
              ) : (
                <>
                  <div className="big">{fatte} <em>su {decSel.length}</em></div>
                  <div className="cap">decisioni eseguite, anche in parte</div>
                  <div className="stk">
                    {ORD.map(s => cSel[s] ? <i key={s} className={`ex-${s}`} style={{ flex: cSel[s] }} /> : null)}
                  </div>
                  <div className="stkleg">
                    {ORD.map(s => cSel[s] ? (
                      <span key={s}><i className={`ex-${s}`} />{cSel[s]} {s}</span>
                    ) : null)}
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="p3" style={{ flex: '0 0 auto' }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h">Corredo<span className="side">{sel ? `MEMO ${sel.id}` : ''}</span></div>
            <div>
              <div className="kv"><span className="k">NAV alla data</span>
                <span className="v">{sel?.portfolio_nav_eur ? fmtEUR(sel.portfolio_nav_eur, false, 0)
                  : <em>n.d. — non registrato per questa run</em>}</span></div>
              <div className="kv"><span className="k">Esito di mercato</span>
                {/* outcome_pct e' valorizzato su 0 righe su 193: si dichiara,
                    non si rende come zero (e mai colorato di verde) */}
                <span className="v"><em>{conEsito === 0
                  ? `n.d. — outcome_pct valorizzato su 0 righe su ${dec.length}`
                  : `${conEsito} su ${dec.length} decisioni con esito`}</em></span></div>
              <div className="kv"><span className="k">Token Capo</span>
                <span className="v">{(sel?.capo_tokens_in || sel?.capo_tokens_out)
                  ? `${fmtNum(sel!.capo_tokens_in, 0)} in · ${fmtNum(sel!.capo_tokens_out, 0)} out`
                  : <em>n.d. — non registrati per questa run</em>}</span></div>
              <div className="kv"><span className="k">Testo</span>
                <span className="v">{md ? `${fmtNum(md.length, 0)} char · ${sezioni.length} sezioni` : <em>—</em>}</span></div>
              <div className="kv"><span className="k">Modelli DCF</span>
                <span className="v">{sel ? dcfDi(sel).length : 0}
                  {sel && dcfDi(sel).some(f => /_FLAGGED/i.test(f)) &&
                    ` · ${dcfDi(sel).filter(f => /_FLAGGED/i.test(f)).length} FLAGGED`}</span></div>
              {sel && dcfDi(sel).map(f => (
                <div className="file" key={f}>
                  <span className="nm">{nome(f)}</span>
                  {/_FLAGGED/i.test(f) && <span className="fg">FLAGGED</span>}
                </div>
              ))}
              <div className="apri">
                {/* l'allegato assente resta VISIBILE e spento, non sparisce:
                    un bottone che scompare non dice che il file non c'e' */}
                {sel?.pdf_available
                  ? <a href={`${API_BASE}/memos/${sel.id}/pdf`} target="_blank" rel="noreferrer">MEMO PDF</a>
                  : <span className="off">PDF NON DISPONIBILE</span>}
                {sel?.appendix_available
                  ? <a href={`${API_BASE}/memos/${sel.id}/appendix`} target="_blank" rel="noreferrer">APPENDICE</a>
                  : <span className="off">NESSUNA APPENDICE</span>}
              </div>
              <div className="nota">
                <b>Aggancio dichiarato:</b> la colonna ESITO nasce dall’incrocio (memo, titolo, azione)
                fra la ACTION TABLE del memo e le decisioni in archivio. Dove non aggancia scrive
                NON AGGANCIATA, invece di inventare un esito.
              </div>
            </div>
          </div>

          {/* su cosa si regge il memo: censimento dei tag [src: …] */}
          <div className="p3 vi" style={{ flex: 1 }}>
            <i className="tick tl" /><i className="tick tr" /><i className="tick bl" /><i className="tick br" />
            <div className="p3h vi">Su cosa si regge
              <span className="side">{fonti.length
                ? `${fonti.reduce((s, f) => s + f.n, 0)} CITAZIONI · ${fonti.length} STRINGHE DISTINTE` : ''}</span>
            </div>
            <div className="scroll" style={{ flex: 1 }}>
              {md === undefined ? <p className="px-3 py-2 text-2xs text-faint">—</p>
                : fonti.length === 0 ? (
                  <div className="nota" style={{ borderTop: 0 }}>
                    Nessun tag <code>[src: …]</code> in questo memo — e la regola di casa dice che ogni cifra ne vuole uno.
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
      .then(r => { setHits(r.results || []); setSel(0); setMs(Math.round(performance.now() - t0)); })
      .catch(e => { setHits(null); setErrS(e?.response?.data?.detail || e?.message || String(e)); })
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
        <div className="mh">Cerca nell’archivio
          <span className="side">{memos.length} MEMO LEGGIBILI</span>
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
            placeholder="che cosa aveva detto il comitato su…" />
          <span className="ms">
            {cercando ? 'interrogo l’archivio…'
              : errS ? <b style={{ color: '#FF3D60' }}>ricerca in errore</b>
              : hits === null ? 'INVIO per cercare'
              : <>{hits.length} passi in <b>{ms} ms</b></>}
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
                    fontSize={10} fontWeight={600} fontFamily="JetBrains Mono, monospace">{v.toFixed(3)}</text>
                </g>
              ))}
              <text x={ML - 9} y={MARGIN_TOP - 5} textAnchor="end" fill="#29D3F2" fontSize={10} fontWeight={700}
                fontFamily="JetBrains Mono, monospace">PIÙ VICINA</text>
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
                      fontFamily="JetBrains Mono, monospace">{h.distance.toFixed(4)}</text>}
                    <circle cx={cx} cy={cy} r={13} fill="transparent" />
                  </g>
                );
              })}
              {/* legenda corta: dentro un SVG una scritta lunga esce e si taglia zitta */}
              <rect x={W - MR + 14} y={MARGIN_TOP} width={9} height={9} fill="#29D3F2" />
              <text x={W - MR + 29} y={MARGIN_TOP + 8} fill="#8D9FC4" fontSize={10} fontWeight={600}
                fontFamily="JetBrains Mono, monospace">{colpiti.size} memo rispondono</text>
              <rect x={W - MR + 14} y={MARGIN_TOP + 19} width={9} height={9} fill="transparent" stroke="#22304F" />
              <text x={W - MR + 29} y={MARGIN_TOP + 27} fill="#8D9FC4" fontSize={10} fontWeight={600}
                fontFamily="JetBrains Mono, monospace">{memos.length - colpiti.size} memo muti</text>
            </svg>
          </div>
        )}

        <div className="mres">
          {errS && (
            <div className="mvuoto">
              <b>RICERCA IN ERRORE</b> — {errS}. L’archivio non è perso: i memo restano leggibili
              dall’indice, è l’indicizzazione semantica a non rispondere.
            </div>
          )}
          {!errS && hits === null && !cercando && (
            <div className="mvuoto">
              Scrivi una domanda e premi INVIO. La ricerca guarda il <b style={{ color: '#8D9FC4' }}>testo</b> dei memo,
              non i titoli: “copertura FOMC”, “sconto NAV Pershing”, “funding Hyperliquid”.
            </div>
          )}
          {!errS && hits !== null && hits.length === 0 && (
            <div className="mvuoto">Nessun passo dell’archivio risponde a “{q.trim()}”.</div>
          )}
          {!errS && (hits || []).map((h, i) => {
            const m = byId.get(h.memo_id);
            return (
              <div key={h.chunk_id} className={`mhit${i === sel ? ' on' : ''}`}
                onClick={() => setSel(i)} onDoubleClick={() => onApri(h.memo_id)}>
                <div className="lft">
                  <div className="mm">MEMO {h.memo_id}</div>
                  <div className="dt">{m ? dd(m.timestamp) : 'run senza testo'}</div>
                  <div className="ds">{h.distance.toFixed(4)}</div>
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
          <span><b>↑↓</b> scegli</span>
          <span><b>INVIO</b> apre il memo</span>
          <span><b>ESC</b> chiude</span>
          <span><b>CTRL+MAIUSC+F</b> riapre</span>
          <span style={{ marginLeft: 'auto' }}>
            <b>DISTANZA COSENO</b> — più bassa, più vicina. Non è una percentuale di rilevanza.
          </span>
        </div>
      </div>
    </div>
  );
}
