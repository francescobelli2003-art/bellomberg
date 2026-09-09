import { useBox } from '@/lib/useBox';

/* ════════════════════════════════════════════════════════════
   NASTRO D'ESECUZIONE — strumento-firma di F3 (Opus 5, 26/07)

   Disegna come e' nata la risposta: l'asse e' il tempo MISURATO
   in pagina, ogni tacca e' una chiamata a uno strumento (larghezza
   = durata, altezza = byte tornati, colore = esito), la traccia
   ambra e' il ritmo del testo che arriva.

   Materia prima: gli eventi SSE tool_use_start / tool_result /
   delta / done, che prima di oggi finivano in console.log.

   Regole di casa rispettate:
   - SVG misurato in PIXEL REALI (useBox): mai preserveAspectRatio
     "none", nessuna scritta stirata;
   - interattivo: hover/click danno la lettura numerica esatta;
   - i tempi sono misurati DAL CLIENT e la pagina lo scrive: il
     backend non manda timestamp;
   - sulle risposte storiche la traccia NON esiste (il backend
     salva solo il testo) e il buco si DICHIARA.
   ════════════════════════════════════════════════════════════ */

export interface ToolCall {
  n: number;               // progressivo nella risposta
  id: string;              // tool_id: appaia tool_use_start e tool_result
  name: string;
  iteration: number;
  t0: number;              // ms dall'inizio della risposta
  t1?: number;             // arrivo del risultato
  ok?: boolean;
  bytes?: number;
  preview?: string;
  arg?: string;
}

export interface FlowPoint { t: number; len: number }

const CY = '#29D3F2', CR = '#FF3D60', AM = '#FFA51E';
const DIM = '#73829F', GRD = '#22304F', MONO = "'JetBrains Mono', monospace";
const BAY = 236;           // vano di lettura riservato: il riquadro non copre mai le tacche

/* ── v2, 28/07: CORSIE PER STRUMENTO (impianto B, scelto dal PM) ─────────
   Il PM: «quel box e' confusionale con testi che si sovrappongono, non ci si
   capisce niente quando parte». MISURATO con `mockup_f3_chat/prova_nastro.py`,
   che riproduce lo stream intercettandolo (nessuna chiamata vera, righe di
   chat_sessions e chat_messages identiche prima e dopo):
     · avvio, 8 strumenti   -> 28 sovrapposizioni, tutte e 8 NELLO STESSO PUNTO
     · raffica, 14 strumenti -> 29
     · FINITO, done arrivato -> 29   <- non era «solo quando parte»
   Due cause, entrambe di impianto e non di spaziatura:
   (1) un'etichetta `[n] nome` per OGNI tacca, alla sua posizione temporale,
       senza controllo di collisione. A 9px l'etichetta e' larga ~113px misurati
       e l'asse fa 26,7 px/s a tMax 30s: due nomi non si toccano solo oltre
       **4,2 s** di distanza fra le chiamate. Dentro un'iterazione gli strumenti
       partono a millisecondi: si toccavano sempre.
   (2) `tMax` era il massimo OSSERVATO, senza ampiezza minima: l'ultimo evento
       cade sempre sul bordo, quindi con eventi vicini nel tempo il nastro
       collassava in una scheggia di 20px contro la baia di lettura, lasciando
       vuoto quasi tutto il grafico. ⚠️ La prima stesura di questa riga diceva
       «1.050px di grafico vuoto»: il grafico non e' mai stato largo 1.050px —
       la review l'ha misurato a **802** (`plotW = w - BAY - GUT - 16` con
       w = 1.097 nella colonna centrale a un terzo di schermo). Il difetto era
       vero, la cifra no.
   La cura: il nome dello strumento esce dal grafico e diventa la CORSIA —
   scritto una volta sola nella colonna di sinistra, dove non puo' collidere per
   costruzione. L'asse prende un'ampiezza minima. L'iterazione non e' piu' l'asse
   Y: e' un marcatore in cima, diradato con una soglia DICHIARATA. */
const GUT = 132;           // colonna dei nomi: 22 caratteri a 9px (0,6em) = 119px + aria
const LANE = 15;           // altezza di una corsia
const MIN_SPAN = 10000;    // ampiezza MINIMA dell'asse: sotto i 10 s non si stringe
/* px minimi fra due marcatori d'iterazione, altrimenti si dirada.
   ⚠️ ERA 30, ed era TROPPO POCO: la review l'ha misurato. L'etichetta e'
   `ITER n` disegnata a `x+3`, e JetBrains Mono avanza 0,6em, cioe' 5,4px per
   carattere a 9px. «ITER 1» sono 6 caratteri = 32,4px, «ITER 10» sono 7 = 37,8px.
   Con la soglia a 30 due marcatori venivano TENUTI a 30px di distanza e le
   scritte si sovrapponevano di 2,4px (7,8 con due cifre) — cioe' esattamente il
   difetto per cui questo ri-disegno esiste, riprodotto in un caso che i miei
   scenari non esercitavano. 3 (offset) + 37,8 (etichetta piu' lunga) + 3 (aria)
   = 44. */
const GAP_ITER = 44;
const ASSE = 28;           // la striscia dell'asse: NON scorre, altrimenti sparisce
/* quanto il riquadro deve essere alto OLTRE l'svg delle corsie: 2px di bordo
   di `.p3` + 26px di `.p3h` + i 28 della striscia dell'asse. Sotto `.obsx` vale
   `box-sizing:border-box`, quindi vanno contati tutti. ⚠️ La prima stesura
   scriveva 68 invece di 80 e tagliava 12px SEMPRE da 4 corsie in su: la banda
   persa era proprio quella della traccia del ritmo e della scritta
   «testo · N car». Misurato dalla review con i css veri in Chromium. */
const CORNICE = 80;

export function fmtKB(b?: number) {
  if (b == null) return null;
  if (b < 1024) return b.toLocaleString('it-IT') + ' B';
  return (b / 1024).toLocaleString('it-IT', { maximumFractionDigits: 1 }) + ' KB';
}
export function fmtMs(ms?: number) {
  if (ms == null) return null;
  return ms < 1000 ? Math.round(ms) + ' ms'
    : (ms / 1000).toLocaleString('it-IT', { maximumFractionDigits: 1 }) + ' s';
}

export default function NastroEsecuzione({
  calls, flow, durata, streaming, disponibile, hot, onHot, pin, onPin, motivoAssenza,
}: {
  calls: ToolCall[];
  flow: FlowPoint[];
  durata: number | null;             // ms totali della risposta (null = ancora in corso)
  streaming: boolean;
  disponibile: boolean;              // false = risposta storica: traccia mai salvata
  hot: number | null;
  onHot: (n: number | null) => void;
  pin: number | null;
  onPin: (n: number | null) => void;
  motivoAssenza?: string;
}) {
  const [ref, box] = useBox<HTMLDivElement>();
  const w = box.w, h = box.h;

  const sel = pin ?? hot;
  const call = calls.find(c => c.n === sel) || null;

  const tOsservato = Math.max(
    durata ?? 0,
    ...calls.map(c => c.t1 ?? c.t0),
    ...flow.map(f => f.t),
    1,
  );
  /* ampiezza MINIMA: senza questa, con eventi vicini nel tempo l'asse si stringe
     su se stesso e tutte le tacche cadono sul bordo destro (misurato: 14 tacche
     in 20px, con 1.050px di grafico vuoto). */
  const tMax = Math.max(tOsservato, MIN_SPAN);
  const plotW = Math.max(60, w - BAY - GUT - 16);
  const x = (ms: number) => GUT + plotW * Math.min(1, ms / tMax);

  /* UNA CORSIA PER STRUMENTO DISTINTO, nell'ordine in cui e' comparso: il nome
     si scrive una volta sola, nella colonna, e non puo' piu' collidere. */
  const corsie: string[] = [];
  for (const c of calls) if (!corsie.includes(c.name)) corsie.push(c.name);
  /* L'ASSE STA IN UNA STRISCIA A PARTE, e non dentro il riquadro che scorre.
     Trovato GUARDANDO il PNG dopo la prima stesura: con 14 corsie il nastro e'
     piu' alto della scatola, scorre — e l'asse del tempo finiva SOTTO LA PIEGA.
     Le tacche restavano tutte a sinistra (giusto: erano partite tutte a t~0), ma
     senza l'asse in vista quel «a sinistra» non vuol dire niente. Il metro
     diceva zero sovrapposizioni e aveva ragione: la sovrapposizione non era il
     difetto. */
  const yTop = 6;
  const yFlow = yTop + corsie.length * LANE + 12;
  const corsieH = yFlow + 6;
  const iters = Array.from(new Set(calls.map(c => c.iteration))).sort((a, b) => a - b);
  const maxB = Math.max(1, ...calls.map(c => c.bytes ?? 0));

  /* tacche temporali "tonde" (1, 2, 5, 10, 20, 30 s) scelte sulla durata vera */
  const step = [1, 2, 5, 10, 20, 30, 60].find(s => tMax / 1000 / s <= 8) ?? 120;
  const ticks: number[] = [];
  for (let s = 0; s * 1000 <= tMax; s += step) ticks.push(s);

  /* marcatori d'iterazione: il t0 della PRIMA chiamata di ogni iterazione.
     Si diradano se cadrebbero a meno di GAP_ITER px l'uno dall'altro — e quante
     sono state saltate lo dice l'intestazione, perche' un diradamento zitto e'
     un dato che sparisce (regola 14/07). */
  const primeDiIter: { it: number; t: number }[] = [];
  for (const c of calls) {
    if (!primeDiIter.some(p => p.it === c.iteration)) primeDiIter.push({ it: c.iteration, t: c.t0 });
  }
  primeDiIter.sort((a, b) => a.t - b.t);
  const marcatori: { it: number; t: number }[] = [];
  for (const p of primeDiIter) {
    const ultimo = marcatori[marcatori.length - 1];
    if (!ultimo || x(p.t) - x(ultimo.t) >= GAP_ITER) marcatori.push(p);
  }
  const iterDiradate = primeDiIter.length - marcatori.length;

  return (
    /* Il riquadro cresce col numero di corsie, fra 116 e 190px. Con
       `CORNICE + n*LANE` il contenuto ci sta ESATTO fino a 7 corsie; da 8 in su
       il tetto di 190px morde e il nastro SCORRE dentro la sua scatola invece di
       stringere le corsie — comprimere una corsia sotto i 15px riporterebbe le
       tacche a toccarsi, cioe' il difetto da cui siamo partiti.
       ⚠️ Il «da 8 in su» e' vero SOLO con CORNICE=80. Con il 68 della prima
       stesura scorreva da 3, e questo commento affermava il falso. */
    <div className="p3 cy tape"
         style={{ height: Math.min(190, Math.max(116, CORNICE + corsie.length * LANE)) }}>
      <span className="tick tl" /><span className="tick tr" />
      <span className="tick bl" /><span className="tick br" />
      <div className="p3h c">
        Nastro d'esecuzione
        {disponibile && calls.length > 0 && (
          <span className="chip c" style={{ marginLeft: 6 }}>
            {calls.length} STRUMENT{calls.length === 1 ? 'O' : 'I'} · {corsie.length} DISTINT{corsie.length === 1 ? 'O' : 'I'} · {iters.length} ITERAZION{iters.length === 1 ? 'E' : 'I'}
            {calls.some(c => c.bytes != null) && ' · ' + fmtKB(calls.reduce((s, c) => s + (c.bytes ?? 0), 0))}
          </span>
        )}
        {/* un diradamento zitto e' un dato che sparisce: se un marcatore
            d'iterazione non e' stato disegnato, la pagina lo DICE. */}
        {iterDiradate > 0 && (
          <span className="chip a" style={{ marginLeft: 6 }}>
            {iterDiradate} ITER NON SEGNAT{iterDiradate === 1 ? 'A' : 'E'} — TROPPO VICINE
          </span>
        )}
        <span className="side">
          {!disponibile ? 'traccia non disponibile'
            : streaming ? 'in corso — tempi misurati in pagina'
            : durata != null ? fmtMs(durata) + ' dal comando alla firma'
            : '—'}
        </span>
      </div>

      <div className="tapebox" ref={ref}>
        {!disponibile ? (
          <div className="tapevuoto">
            <div>
              <b>TRACCIA NON DISPONIBILE</b> — {motivoAssenza
                ?? 'il backend salva il testo della risposta, non gli strumenti che l\'hanno prodotta: per le conversazioni gia\' in archivio il nastro non puo\' essere ricostruito.'}
            </div>
          </div>
        ) : calls.length === 0 && !streaming ? (
          <div className="tapevuoto">
            <div>
              <b>NESSUNO STRUMENTO CHIAMATO</b> — questa risposta e' uscita dal solo modello.
              In casa i numeri arrivano dai tool: leggila con quel metro.
            </div>
          </div>
        ) : w > 0 && h > 0 ? (
          <>
            <div className="tapescroll">
            <svg width={w} height={corsieH}>
              {/* i confini delle ITERAZIONI: qui solo le linee verticali. Le
                  etichette `ITER n` stanno nella striscia dell'asse, che non
                  scorre — se scorressero, sparirebbero come l'asse. */}
              {marcatori.map(p => (
                <line key={'it' + p.it} x1={x(p.t)} y1={yTop} x2={x(p.t)} y2={yFlow}
                      stroke="#2A3760" strokeDasharray="2 3" />
              ))}

              {/* UNA CORSIA PER STRUMENTO: il nome sta nella colonna, fuori dal
                  grafico, quindi non puo' collidere con niente. */}
              {corsie.map((nome, i) => {
                const yc = yTop + i * LANE + LANE / 2;
                const attivo = !!call && call.name === nome;
                /* 21 caratteri stanno in GUT-12 a 9px (0,6em = 5,4px/car). Oltre si
                   taglia con l'ellissi, che e' un taglio DICHIARATO.
                   ⚠️ La prima stesura aggiungeva «il nome intero sta nella baia di
                   lettura, che e' sempre visibile»: FALSO, e la review l'ha ripreso.
                   La baia mostra il nome della chiamata PUNTATA, non di tutte: se
                   nessuna e' puntata scrive «LETTURA» e l'invito a puntare. Quindi
                   un nome troncato si legge per intero solo puntando o mettendo il
                   fuoco su una sua tacca — che ora si puo' fare anche da tastiera,
                   e l'`aria-label` della tacca lo porta comunque per intero. */
                const breve = nome.length > 21 ? nome.slice(0, 20) + '…' : nome;
                return (
                  <g key={nome}>
                    <text x={GUT - 8} y={yc + 3} textAnchor="end" fontSize={9} fontWeight={600}
                          fill={attivo ? '#BFE9F5' : '#8794AE'} fontFamily={MONO}>{breve}</text>
                    <line x1={GUT} y1={yc} x2={GUT + plotW} y2={yc}
                          stroke={attivo ? '#22304F' : '#141E36'} strokeDasharray="2 4" />
                  </g>
                );
              })}

              {/* tacche: nella corsia del PROPRIO strumento. I byte restano
                  l'altezza, ma dentro la corsia (max 11px in 15) invece di
                  spingere l'etichetta nella corsia di sopra. */}
              {calls.map(c => {
                const lane = corsie.indexOf(c.name);
                const yc = yTop + lane * LANE + LANE / 2;
                const x0 = x(c.t0);
                const x1 = Math.max(x(c.t1 ?? c.t0), x0 + 3);
                const bh = c.bytes != null ? 4 + 7 * (c.bytes / maxB) : 6;
                const col = c.ok === false ? CR : c.ok == null ? AM : CY;
                const on = sel === c.n;
                const durata = c.t1 != null ? fmtMs(c.t1 - c.t0) : 'in corso';
                const esito = c.ok === false ? 'ERRORE dichiarato' : c.ok == null ? 'in corso' : 'ok';
                return (
                  /* ⚠️ RAGGIUNGIBILE DA TASTIERA. La prima stesura aveva solo
                     mouse: la review l'ha ripreso, e su F6 lo STESSO difetto era
                     un reperto bloccante (i baffi vivevano solo dentro un
                     attributo `title`). La regola di casa dice che la lettura
                     numerica esatta di un grafico deve essere raggiungibile
                     ANCHE da tastiera, quindi ogni tacca e' un `button`
                     focalizzabile: Tab la punta (e il fuoco accende la baia di
                     lettura come l'hover), Invio o Spazio la inchioda.
                     L'`aria-label` porta il dato per intero, perche' un lettore
                     di schermo non vede la baia. */
                  <g key={c.id} role="button" tabIndex={0}
                     aria-label={`chiamata ${c.n}: ${c.name}, iterazione ${c.iteration}, ${esito}` +
                                 (c.bytes != null ? `, ${fmtKB(c.bytes)} tornati` : '') +
                                 `, durata ${durata}`}
                     aria-pressed={pin === c.n}
                     onMouseEnter={() => onHot(c.n)} onMouseLeave={() => onHot(null)}
                     onFocus={() => onHot(c.n)} onBlur={() => onHot(null)}
                     onKeyDown={e => {
                       if (e.key === 'Enter' || e.key === ' ') {
                         e.preventDefault();
                         onPin(pin === c.n ? null : c.n);
                       }
                     }}
                     onClick={() => onPin(pin === c.n ? null : c.n)} style={{ cursor: 'pointer' }}>
                    <rect x={x0} y={yc - bh / 2} width={x1 - x0} height={bh}
                          fill={col + (on ? '99' : '44')} stroke={col} strokeWidth={on ? 1.4 : 1} />
                    {/* area di presa generosa: la tacca vera puo' essere di 3 px */}
                    <rect x={x0 - 4} y={yc - LANE / 2} width={Math.max(10, x1 - x0 + 8)}
                          height={LANE} fill="transparent" />
                  </g>
                );
              })}

              {/* ritmo del testo in arrivo: campionato dai delta, non decorativo.
                  Sta sotto l'ultima corsia, dove non incrocia nessuna tacca. */}
              {flow.length > 2 && (() => {
                const maxRate = Math.max(...flow.map((f, i) => i ? (f.len - flow[i - 1].len) / Math.max(1, f.t - flow[i - 1].t) : 0), 1e-6);
                const d = flow.map((f, i) => {
                  const rate = i ? (f.len - flow[i - 1].len) / Math.max(1, f.t - flow[i - 1].t) : 0;
                  return (i ? 'L' : 'M') + ' ' + x(f.t) + ' ' + (yFlow - 9 * (rate / maxRate));
                }).join(' ');
                return <path d={d} fill="none" stroke={AM} strokeWidth={1} opacity={0.75} />;
              })()}
              {flow.length > 2 && (
                <text x={GUT - 8} y={yFlow} textAnchor="end" fill="#9A7B44" fontSize={9}
                      fontWeight={600} fontFamily={MONO}>
                  testo · {flow[flow.length - 1].len.toLocaleString('it-IT')} car
                </text>
              )}

              {/* crosshair sulla chiamata puntata */}
              {call && (
                <line x1={x(call.t0)} y1={yTop} x2={x(call.t0)} y2={yFlow}
                      stroke={call.ok === false ? CR : CY} strokeWidth={1} opacity={0.7} strokeDasharray="3 3" />
              )}
              <line x1={w - BAY - 4} y1={0} x2={w - BAY - 4} y2={corsieH} stroke="#1A2440" />
            </svg>
            </div>

            {/* L'ASSE, IN UNA STRISCIA CHE NON SCORRE. Sopra le tacche del tempo
                stanno le etichette `ITER n`, su una riga propria: cosi' non si
                incontrano mai con i valori dell'asse, ed erano proprio loro a
                sovrapporsi a «TEMPO» al 90% nella versione di prima. */}
            <svg className="tapeasse" width={w} height={ASSE}>
              {marcatori.map(p => (
                <text key={'ita' + p.it} x={x(p.t) + 3} y={9} fill={DIM} fontSize={9}
                      fontWeight={600} fontFamily={MONO}>ITER {p.it}</text>
              ))}
              <line x1={GUT} y1={14} x2={GUT + plotW} y2={14} stroke={GRD} />
              {ticks.map(s => (
                <g key={s}>
                  <line x1={x(s * 1000)} y1={14} x2={x(s * 1000)} y2={18} stroke="#2A3760" />
                  {/* 9px e non 8: il pavimento che F6 dichiara nel proprio css */}
                  <text x={x(s * 1000)} y={ASSE - 1} fill={DIM} fontSize={9} fontWeight={600}
                        fontFamily={MONO} textAnchor="middle">{s}s</text>
                </g>
              ))}
            </svg>

            {/* vano di lettura: sempre nello stesso posto, non insegue il mouse */}
            <div className="rdo" style={{ left: w - BAY + 6, top: 4, width: BAY - 14 }}>
              {call ? (
                <>
                  <b>[{call.n}] {call.name}</b>
                  {pin === call.n && <span className="pin"> FISSATO</span>}
                  {call.arg && <div className="kv"><i>argomento</i><u>{call.arg}</u></div>}
                  <div className="kv"><i>esito</i><u style={{ color: call.ok === false ? CR : call.ok ? '#21E0A0' : AM }}>
                    {call.ok == null ? 'in corso…' : call.ok ? 'ok' : 'ERRORE dichiarato'}</u></div>
                  <div className="kv"><i>tornati</i><u>
                    {fmtKB(call.bytes) ?? 'n.d.'}{call.t1 != null && ' · ' + fmtMs(call.t1 - call.t0)}</u></div>
                  <div className="kv"><i>iterazione</i><u>{call.iteration}</u></div>
                </>
              ) : (
                <>
                  <b>LETTURA</b>
                  <div style={{ color: DIM, lineHeight: 1.55, marginTop: 2 }}>
                    Punta una tacca per il dato esatto: argomento, esito, byte tornati, durata.
                    Un clic la fissa.
                  </div>
                </>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
