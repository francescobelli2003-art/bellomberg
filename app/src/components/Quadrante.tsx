import { useT } from '@/i18n/provider';
/* ============================================================
   F4 — IL QUADRANTE (Opus 5, 26/07)

   Lo strumento-firma della plancia orbitale, scelto dal PM sui mockup
   (mockup_f4_agents/opzione_2.html). Un giro = la durata della run;
   mezzogiorno = lo start; senso orario. Un'orbita per desk, in ordine
   di accensione: la piu' esterna al primo che si e' acceso.

   DISCIPLINA (lezioni gia' pagate, vedi montecarlo-plancia.css):
   - l'SVG disegna 1:1 sul box misurato in pixel VERI: niente
     preserveAspectRatio="none", quindi nessuna scritta stirata;
   - il box si misura FUORI dal flusso (il padre e' position:relative,
     l'svg absolute inset 0), o il ResizeObserver va in retroazione;
   - ogni etichetta e' una PIASTRA la cui scatola e' calcolata sul testo
     che contiene (letter-spacing incluso: senza, il testo esce di 8px),
     e le piastre vicine vengono separate da `layoutLabels` PRIMA di
     essere disegnate. E' cosi' che si ottiene zero testo sovrapposto:
     non a occhio, per costruzione.

   26/07, apertura dell'audit frontend: le primitive che stavano QUI
   (geometria polare, misura del testo, piastre, sigilli) sono passate in
   `lib/svg-kit.ts` e `lib/Sigil.tsx`. Non erano di F4: erano solo nate
   qui. In questo file resta il QUADRANTE — cioe' la sola cosa che parla
   della run del comitato.
   ============================================================ */
import { useMemo } from 'react';
import type { Plancia, Desk } from '@/lib/plancia-data';
import { fmtClock, fmtDurShort } from '@/lib/plancia-data';
import { TAU, MONO, angleAt, pointAt, arcPath, plateBox, layoutLabels } from '@/lib/svg-kit';
import type { LabLine } from '@/lib/svg-kit';
import { Sigil } from '@/lib/Sigil';

/* staffa di tracciamento: quattro angoli, mai un rettangolo pieno */
function Bracket({ cx, cy, w, h, color, op = 0.75 }: {
  cx: number; cy: number; w: number; h: number; color: string; op?: number;
}) {
  const k = Math.min(w, h) * 0.3;
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2;
  return <path fill="none" stroke={color} strokeOpacity={op} strokeWidth={1}
    d={`M${x0 + k} ${y0}H${x0}V${y0 + k} M${x1 - k} ${y0}H${x1}V${y0 + k}
        M${x0 + k} ${y1}H${x0}V${y1 - k} M${x1 - k} ${y1}H${x1}V${y1 - k}`} />;
}

/** blocco d'etichetta su piastra opaca: il testo vince sempre sul fondo */
function Plate({ x, y, lines, align, lh = 11.5, stroke, num }: {
  x: number; y: number; lines: LabLine[]; align: 'start' | 'end';
  lh?: number; stroke?: string; num?: number;
}) {
  const { w, h } = plateBox(lines, lh);
  const px = align === 'end' ? x - w - 6 : x - 6;
  return (
    <g>
      <rect x={px.toFixed(1)} y={(y - h / 2 - 4).toFixed(1)} width={(w + 12).toFixed(1)}
        height={(h + 8).toFixed(1)} fill="#060A14" fillOpacity={0.9}
        stroke={stroke || 'rgba(36,50,90,.9)'} strokeOpacity={0.85} />
      {/* il numero d'orbita sta SEMPRE dal lato che guarda il quadrante: messo
          all'esterno usciva dal bordo dell'SVG e veniva tagliato di 9px
          (le piastre di destra arrivano gia' a filo del bordo) */}
      {num != null && (
        <text x={(align === 'end' ? px + w + 17 : px - 5).toFixed(1)} y={y.toFixed(1)}
          fontWeight={600} fill="#73829F" fontSize={9} textAnchor={align === 'end' ? 'start' : 'end'}
          dominantBaseline="middle" fontFamily={MONO}>{num}</text>
      )}
      {lines.map((l, k) => (
        <text key={k} x={x.toFixed(1)} y={(y - h / 2 + lh * (k + 0.5)).toFixed(1)}
          fill={l.col} fillOpacity={l.op ?? 1} fontSize={l.size ?? 9}
          letterSpacing={l.ls ?? 0.4} textAnchor={align} dominantBaseline="middle"
          fontFamily={MONO}>{l.t}</text>
      ))}
    </g>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   IL QUADRANTE
   ══════════════════════════════════════════════════════════════════════ */
export interface QuadranteProps {
  p: Plancia;
  w: number; h: number;
  cursor: number;              // secondi puntati dalla lancetta
  pinned: boolean;
  onCursor: (t: number) => void;
  onPin: () => void;
  koIds: string[];
  fmtEur: (v: number | null | undefined) => string;
  costoRun: number | null | undefined;   // il verdetto che va nel nucleo
  koCost: number | null;                 // quanto di quel costo viene dai KO
  memoLabel: string;                     // "memo #47" oppure il buco dichiarato
  /* Il FONDO MISURATO della piastra di lettura, in px dall'alto dello stesso
     box su cui disegna l'SVG (`.inst` e' `position:absolute;inset:0` dentro
     `.p3b`, quindi l'origine e' la stessa). 0 = non ancora misurato. */
  readBottom?: number;
}

/* Le quattro piastre HUD (lettura, parallelismo, legenda, scala) NON stanno
   qui dentro: sono HTML agli angoli del vetro, con posizioni fisse e corridoi
   che non si incrociano (agents-plancia.css §6). Le fasce entro cui possono
   cadere le etichette di telemetria devono quindi stare LONTANE da quegli
   angoli, o una piastra SVG finirebbe sotto una piastra HTML.
   Sinistra: sotto la lettura. (Sotto non c'e' piu' niente: v. il 116 qui in
             fondo.)
   Destra:   sotto il parallelismo.

   ⚠️ IL 206 ERA UN LETTERALE, E LA RIGA SOPRA LO DENUNCIAVA DA SOLA: diceva
   «sotto la lettura (280px d'altezza tipica)» mentre la costante ne guardava
   206. La fascia partiva DENTRO la piastra, e a schermo corto la lettura le
   finiva sopra — misurato dal cancello il 28/07 a s150: 198x54px, cioe' il
   100% del box OPTIONS FLOW. La cura del mattino (accorciare la piastra alle
   altezze corte) l'ha portato a 198x42px, cioe' al 78%: ha ridotto il difetto
   senza chiuderlo, perche' curava il sintomo e il numero restava un letterale.
   Ora il fondo della lettura si MISURA e la fascia parte da li'.

   ⚠️ E IL 206 NON PUO' RESTARE COME PAVIMENTO — misurato il 28/07 sulla pagina
   viva, ed e' il motivo per cui la prima stesura di questa cura fu un NO-OP:
   a s150 il box del quadrante e' alto 353px e la lettura, gia' compattata
   dalla media query, ne misura 82 (fondo a 94). Con un pavimento a 206 la
   fascia diventava [206, 353-116] = [206, 237]: TRENTUN pixel per tre piastre
   da 54. `layoutLabels` fa una passata dall'alto e poi una dal basso, e la
   seconda vince: le piastre venivano rispinte su fino a y=54, dentro la
   lettura che finisce a 96 — i 42px di sovrapposizione che il cancello
   misurava. Il 206 resta solo come ripiego finche' la misura non c'e'.

   ⚠️ E IL 116 SOTTO ERA SPAZIO MORTO: riservava il posto a `.legend`, la
   piastra in basso a sinistra TOLTA il 28/07 (agents-plancia.css:320). Il
   commento di quel lotto lo diceva gia' — «ora sotto non c'e' piu' niente:
   quello spazio e' libero e le fasce possono scendere» — e lo lasciava da
   fare. A s150 la fascia passa da 31px utili a 229 — 96 li restituisce il
   fondo a 20, il resto lo da' la cima misurata — contro i 184 che servono a
   tre piastre (56px l'una nel layout, piu' due gap da 8).
   ⚠️ IL MARGINE E' 45px, MENO DI UNA PIASTRA: con quattro desk attivi a
   sinistra alla stessa altezza la pila non ci sta piu' e `layoutLabels`
   ricomincia a ricompattare verso l'alto, cioe' il difetto torna. La cura
   regge sui dati di oggi, non su una regola: serve un pavimento nella seconda
   passata di `layoutLabels`. E' a TODO, trovato dalla review del 28/07.    */
const BAND_L: [number, number] = [206, 20];    // [ripiego dall'alto, dal basso]
const BAND_R: [number, number] = [104, 34];
/* aria fra il fondo della piastra di lettura e la prima etichetta sotto */
const ARIA_SOTTO_LETTURA = 10;

export default function Quadrante({ p, w, h, cursor, pinned, onCursor, onPin, koIds, fmtEur,
  costoRun, koCost, memoLabel, readBottom = 0 }: QuadranteProps) {
  const tr = useT();
  const g = useMemo(() => {
    const pad = 14;
    /* la fascia laterale porta la telemetria FUORI dal quadrante: larga
       quanto serve al blocco piu' largo, non di piu' */
    const band = Math.max(176, Math.min(238, w * 0.155));
    const R = Math.max(60, Math.min((h - 2 * pad) / 2, (w - 2 * band - 2 * pad) / 2));
    const rc = Math.max(112, Math.min(170, R * 0.32));     // nucleo
    const rOut = R - 92, rIn = rc + 20;
    const n = Math.max(1, p.desks.length - 1);
    return { pad, band, R, rc, rOut, rIn, cx: w / 2, cy: h / 2, st: (rOut - rIn) / n,
             LX: band - 12, RX: w - band + 12 };
  }, [w, h, p.desks.length]);

  const S = p.scaleSec;
  const PT = (r: number, t: number) => pointAt(g.cx, g.cy, r, t, S);
  const arc = (r: number, t0: number, t1: number) => arcPath(g.cx, g.cy, r, t0, t1, S);
  const rOf = (i: number) => g.rOut - i * g.st;

  /* ── telemetria: una piastra per desk, spinta sulla fascia laterale.
        Il lato lo decide dove il desk ha CHIUSO (destra del quadrante ->
        piastra a destra), poi layoutLabels separa quelle dello stesso lato. */
  const labels = useMemo(() => {
    const mk = (d: Desk, i: number) => {
      const r = rOf(i);
      const tEnd = d.obs1 ?? 0;
      const [bx, by] = pointAt(g.cx, g.cy, r, tEnd, S);
      const right = bx >= g.cx;
      const ko = koIds.includes(d.id);
      /* #7 (28/07): mancava il ramo running e OGNI desk in corsa diceva «OK ·
         nessun errore API» mentre la tabella accanto diceva RUN (F45, blocco A) */
      const run = p.live && d.statusRun === 'running';
      const err = !ko && d.statusRun === 'error';
      /* `d.nCalls` e' quanto sta nel log RICEVUTO (a run viva le ultime 50 righe,
         N8): si dice «nel log ricevuto», mai «finora» come fosse il totale */
      const lines: LabLine[] = [
        { t: d.name.toUpperCase(), col: d.color, size: 10.5, ls: 1.1 },
        { t: tr('dashboard.dial_calls', {a: fmtDurShort(d.dur), b: d.nCalls, c: p.logTappato ? tr('dashboard.dial_log') : '', d: d.nTools}), col: '#8D9FC4', size: 8.5, ls: 0.2 },
        { t: tr('dashboard.dial_api_calls', {a: fmtEur(d.cost), b: d.apiCalls}), col: ko ? '#FFA51E' : '#D4AF37', size: 9, ls: 0.2 },
        { t: ko && run ? tr('dashboard.dial_previous_ko')
             : ko ? tr('dashboard.dial_api_ko')
             : run ? tr('dashboard.dial_running_calls', {a: d.nCalls})
             : err ? tr('dashboard.dial_specialist_ko')
             : d.statusRun === 'done' ? tr('dashboard.dial_no_api_error') : tr('dashboard.dial_result_missing'),
          col: (ko || err) ? '#FF3D60' : run ? '#FFA51E' : d.statusRun === 'done' ? '#21E0A0' : '#8D9FC4',
          size: 8, ls: 0.3 },
      ];
      const box = plateBox(lines);
      return { d, i, r, bx, by, right, ko, lines, y: by, h: box.h + 10 };
    };
    const all = p.desks.map(mk);
    const R = layoutLabels(all.filter(x => x.right), BAND_R[0], h - BAND_R[1], 8);
    /* la fascia sinistra parte sotto il fondo VERO della piastra di lettura,
       non sotto un numero scritto a mano (v. il commento su BAND_L) */
    const cima = readBottom > 0 ? readBottom + ARIA_SOTTO_LETTURA : BAND_L[0];
    const L = layoutLabels(all.filter(x => !x.right), cima, h - BAND_L[1], 8);
    return [...R, ...L];
  }, [p.desks, g, h, S, koIds, fmtEur, readBottom, tr]);

  /* L'uscita anticipata sta DOPO tutti gli hook, mai in mezzo: al primo
     render useBox non ha ancora misurato (w=h=0) e con il return prima di
     `labels` React contava due numeri di hook diversi fra un render e
     l'altro — "Rendered more hooks than during the previous render", cioe'
     la pagina in schermata d'errore. Trovato dal collaudo, non a occhio. */
  if (w < 240 || h < 240) return <svg width={Math.max(0, w)} height={Math.max(0, h)} />;

  const live = p.live;
  const activeNow = p.windows.filter(x => cursor >= x.t0 && cursor <= x.t1);
  const [hx, hy] = PT(g.R - 6, cursor);

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="dial"
      onMouseMove={e => {
        if (pinned) return;
        const b = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const dx = e.clientX - b.left - g.cx, dy = e.clientY - b.top - g.cy;
        let th = Math.atan2(dx, -dy); if (th < 0) th += TAU;
        /* su una run VIVA il presente e' un tetto: oltre la lancetta non c'e'
           niente da leggere, e senza tetto la piastra scriveva ragionamenti e
           memo in minuti non ancora accaduti (review F45) */
        onCursor(Math.min((th / TAU) * S, live ? p.runSec : S));
      }}
      onClick={onPin}>
      <defs>
        {/* vetro: riflesso speculare in alto a sinistra, come su uno strumento vero */}
        <radialGradient id="f4glass" cx="32%" cy="24%" r="62%">
          <stop offset="0%" stopColor="#9FC6FF" stopOpacity=".085" />
          <stop offset="55%" stopColor="#4E7CC0" stopOpacity=".022" />
          <stop offset="100%" stopColor="#000" stopOpacity="0" />
        </radialGradient>
        {/* ghiera: luce da alto-sinistra, ombra in basso-destra */}
        <linearGradient id="f4bezel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#4A5A85" />
          <stop offset="42%" stopColor="#222D4C" />
          <stop offset="100%" stopColor="#0D1526" />
        </linearGradient>
        <radialGradient id="f4hub">
          <stop offset="0%" stopColor="#16203A" />
          <stop offset="70%" stopColor="#0A1120" />
          <stop offset="100%" stopColor="#060A14" />
        </radialGradient>
        <radialGradient id="f4core">
          <stop offset="0%" stopColor="#FFA51E" stopOpacity=".13" />
          <stop offset="100%" stopColor="#FFA51E" stopOpacity="0" />
        </radialGradient>
        <filter id="f4glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="3.2" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <filter id="f4soft" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="1.4" />
        </filter>
      </defs>

      {/* ── ghiera e vetro: la scatola dello strumento ───────────────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.R + 9} fill="none" stroke="#2A3760" strokeWidth={1} strokeOpacity={0.5} />
      <circle cx={g.cx} cy={g.cy} r={g.R + 5} fill="none" stroke="#3D4F8A" strokeWidth={1.6} strokeOpacity={0.32} />
      <circle cx={g.cx} cy={g.cy} r={g.R} fill="url(#f4glass)" />
      {/* tacche di indice ai quattro quarti: e' una ghiera lavorata, non un cerchio */}
      {[0, 0.25, 0.5, 0.75].map((f, i) => {
        const [a0, b0] = PT(g.R + 2, f * S), [a1, b1] = PT(g.R + 12, f * S);
        return <line key={i} x1={a0} y1={b0} x2={a1} y2={b1} stroke="#4A5A85" strokeWidth={1.4} strokeOpacity={0.7} />;
      })}

      {/* ── graticola radiale, appena percepibile: da' profondita' al fondo ── */}
      {Array.from({ length: 12 }, (_, k) => {
        const t = (k / 12) * S;
        const [a0, b0] = PT(g.rc, t), [a1, b1] = PT(g.rOut + 8, t);
        return <line key={k} x1={a0.toFixed(1)} y1={b0.toFixed(1)} x2={a1.toFixed(1)} y2={b1.toFixed(1)}
          stroke="#111A2E" strokeWidth={0.8} />;
      })}

      {/* ── corona dei minuti: tacche lavorate + numeri incisi ───────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.R} fill="none" stroke="#1A2440" strokeWidth={1} />
      {(() => {
        const out: React.ReactNode[] = [];
        const step = S > 4200 ? 120 : 60;                 // oltre l'ora, tacca ogni 2'
        for (let s = 0; s <= S + 0.5; s += step) {
          const m = Math.round(s / 60);
          const big = m % 5 === 0;
          const [x0, y0] = PT(g.R - (big ? 13 : 6.5), s), [x1, y1] = PT(g.R, s);
          out.push(<line key={'t' + s} x1={x0.toFixed(1)} y1={y0.toFixed(1)} x2={x1.toFixed(1)} y2={y1.toFixed(1)}
            stroke={big ? '#4A5A85' : '#26314F'} strokeWidth={big ? 1.3 : 0.8} />);
          /* 0 e fine giro cadono sullo stesso punto: li' ci va la piastra di
             start, non due numeri sovrapposti */
          if (big && m > 0 && s < S - step * 0.6) {
            const [nx, ny] = PT(g.R - 26, s);
            /* effetto inciso con UN SOLO nodo di testo: contorno scuro
               dipinto sotto il riempimento (paint-order). Con due <text>
               sovrapposti l'effetto era identico ma il collaudo li leggeva
               — giustamente — come due scritte una sull'altra. */
            out.push(
              <text key={'n' + s} x={nx.toFixed(1)} y={ny.toFixed(1)} fill="#6E7C9E"
                stroke="#05070E" strokeWidth={2} paintOrder="stroke" strokeLinejoin="round"
                fontSize={9} textAnchor="middle" dominantBaseline="middle"
                fontFamily={MONO}>{m}&#39;</text>);
          }
        }
        return out;
      })()}

      {/* ── le targhe di fase sono dipinte IN CODA all'SVG (v. prima della
          chiusura): a s150 la geometria si inverte (R−66 = 91 < nucleo
          rc = 112), le targhe cadono DENTRO il disco del nucleo e il disco,
          dipinto dopo, le copriva (misurato 1,02:1 — #2). Qui restano solo
          gli ARCHI, che sotto il nucleo ci possono stare. */}
      {p.phases.map(ph => {
        const dashed = !ph.measured;
        return (
          <g key={'arc-' + ph.k}>
            <path d={arc(g.R - 44, ph.t0, ph.t1)} fill="none"
              stroke={dashed ? '#5A6685' : '#FFA51E'} strokeOpacity={dashed ? 0.5 : 0.6}
              strokeWidth={8} strokeDasharray={dashed ? '5 4' : undefined} />
            {[ph.t0, ph.t1].map((t, k) => {
              const [a0, b0] = PT(g.R - 53, t), [a1, b1] = PT(g.R - 35, t);
              return <line key={k} x1={a0.toFixed(1)} y1={b0.toFixed(1)} x2={a1.toFixed(1)} y2={b1.toFixed(1)}
                stroke="#FFA51E" strokeOpacity={0.55} strokeWidth={1} />;
            })}
          </g>
        );
      })}
      {/* ── le orbite ────────────────────────────────────────────────────── */}
      {p.desks.map((d, i) => {
        const r = rOf(i);
        const wins = p.windows.filter(x => x.a === d.id);
        const on = wins.some(x => cursor >= x.t0 && cursor <= x.t1);
        const dim = on ? 1 : 0.62;
        const ko = koIds.includes(d.id);
        const [bx, by] = PT(r, d.obs1 ?? 0);
        return (
          <g key={d.id}>
            {/* la scanalatura dell'orbita: due tratti, chiaro sopra e scuro
                sotto, cosi' l'anello si legge inciso e non disegnato */}
            <circle cx={g.cx} cy={g.cy} r={r + 0.6} fill="none" stroke="#05080F" strokeWidth={1} />
            <circle cx={g.cx} cy={g.cy} r={r} fill="none" stroke={d.color} strokeOpacity={0.12}
              strokeWidth={1} strokeDasharray="1.5 6" />
            {wins.map((x, k) => {
              /* finestra APERTA (desk dichiarato `running`): il tratto misurato e'
                 pieno fino all'ultima chiamata, da li' al presente e' TRATTEGGIATO —
                 il desk ragiona, ma dove sia non lo dice nessuno (F45, blocco A) */
              const tPieno = x.open && x.tCall != null ? x.tCall : x.t1;
              return (
                <g key={k}>
                  <path d={arc(r, x.t0, tPieno)} fill="none" stroke={d.color}
                    strokeOpacity={0.2 * dim} strokeWidth={14} />
                  <path d={arc(r, x.t0, tPieno)} fill="none" stroke={d.color}
                    strokeOpacity={0.95 * dim} strokeWidth={1.7} />
                  {x.open && x.t1 > tPieno + 0.5 && (
                    <path d={arc(r, tPieno, x.t1)} fill="none" stroke={d.color}
                      strokeOpacity={0.7 * dim} strokeWidth={1.7} strokeDasharray="3 4" />
                  )}
                </g>
              );
            })}
            {/* una tacca radiale per CHIAMATA */}
            {p.calls.filter(c => c.a === d.id).map((c, k) => {
              const [x0, y0] = PT(r - 6.5, c.t), [x1, y1] = PT(r + 6.5, c.t);
              return <line key={k} x1={x0.toFixed(1)} y1={y0.toFixed(1)} x2={x1.toFixed(1)} y2={y1.toFixed(1)}
                stroke={d.color} strokeOpacity={0.78 * dim} strokeWidth={0.9} />;
            })}
            {/* il corpo: dove ha chiuso, con la scia da cui e' arrivato */}
            <path d={arc(r, Math.max(wins[0]?.t0 ?? 0, (d.obs1 ?? 0) - S * 0.055), d.obs1 ?? 0)}
              fill="none" stroke={d.color} strokeOpacity={0.95 * dim} strokeWidth={3.4}
              strokeLinecap="round" filter="url(#f4glow)" />
            {on && live && (
              <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={11} fill="none" stroke={d.color} strokeWidth={1}>
                <animate attributeName="r" values="9;22;9" dur="2.6s" repeatCount="indefinite" />
                <animate attributeName="stroke-opacity" values=".7;0;.7" dur="2.6s" repeatCount="indefinite" />
              </circle>
            )}
            <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={9.5} fill="#070B14"
              stroke={d.color} strokeWidth={on ? 1.7 : 1.1} strokeOpacity={dim} />
            <Sigil id={d.id} color={d.color} size={12} x={bx} y={by} />
            <Bracket cx={bx} cy={by} w={30} h={30} color={d.color} op={on ? 0.9 : 0.42} />
            {ko && <circle cx={bx.toFixed(1)} cy={by.toFixed(1)} r={16} fill="none"
              stroke="#FF3D60" strokeWidth={1} strokeDasharray="2.5 3.5" />}
          </g>
        );
      })}

      {/* ── etichette di telemetria, gia' separate da layoutLabels ───────── */}
      {labels.map(L => (
        <g key={L.d.id}>
          <path d={`M${L.bx.toFixed(1)} ${L.by.toFixed(1)}
                    L${(L.right ? g.RX - 14 : g.LX + 14).toFixed(1)} ${L.ly.toFixed(1)}
                    L${(L.right ? g.RX : g.LX).toFixed(1)} ${L.ly.toFixed(1)}`}
            fill="none" stroke={L.d.color} strokeOpacity={0.34} strokeWidth={0.9} />
          <Plate x={L.right ? g.RX : g.LX} y={L.ly} lines={L.lines}
            align={L.right ? 'start' : 'end'} stroke={`${L.d.color}66`} num={L.i + 1} />
        </g>
      ))}

      {/* ── la lancetta: affusolata, con contrappeso oltre il mozzo ──────── */}
      <path d={arc(g.R - 6, Math.max(0, cursor - S * 0.075), cursor)} fill="none"
        stroke="#FFA51E" strokeOpacity={0.13} strokeWidth={22} />
      {(() => {
        const th = angleAt(cursor, S);
        const ux = Math.sin(th), uy = -Math.cos(th);      // versore lancetta
        const px = -uy, py = ux;                          // perpendicolare
        const tip = [g.cx + ux * (g.R - 8), g.cy + uy * (g.R - 8)];
        const tail = [g.cx - ux * (g.rc * 0.42), g.cy - uy * (g.rc * 0.42)];
        const b = 3.1;
        const poly = `${tip[0].toFixed(1)},${tip[1].toFixed(1)} ` +
          `${(g.cx + px * b).toFixed(1)},${(g.cy + py * b).toFixed(1)} ` +
          `${(tail[0] + px * b * 1.5).toFixed(1)},${(tail[1] + py * b * 1.5).toFixed(1)} ` +
          `${(tail[0] - px * b * 1.5).toFixed(1)},${(tail[1] - py * b * 1.5).toFixed(1)} ` +
          `${(g.cx - px * b).toFixed(1)},${(g.cy - py * b).toFixed(1)}`;
        return (
          <g>
            <polygon points={poly} fill="#05070E" fillOpacity={0.55} filter="url(#f4soft)"
              transform="translate(1.6,1.8)" />
            <polygon points={poly} fill="#FFA51E" fillOpacity={0.9} />
            <circle cx={tip[0].toFixed(1)} cy={tip[1].toFixed(1)} r={3.4} fill="#FFD27A" filter="url(#f4glow)" />
          </g>
        );
      })()}
      <circle cx={g.cx} cy={g.cy} r={7} fill="#16203A" stroke="#4A5A85" strokeWidth={1.2} />
      <circle cx={g.cx} cy={g.cy} r={2.4} fill="#FFA51E" fillOpacity={0.85} />

      {/* ── il nucleo: mozzo lavorato + verdetto ─────────────────────────── */}
      <circle cx={g.cx} cy={g.cy} r={g.rc + 26} fill="url(#f4core)" />
      <circle cx={g.cx} cy={g.cy} r={g.rc} fill="url(#f4hub)" stroke="#243257" strokeWidth={1} />
      <circle cx={g.cx} cy={g.cy} r={g.rc - 5} fill="none" stroke="#0C1424" strokeWidth={1} strokeOpacity={0.6} />
      <circle cx={g.cx} cy={g.cy} r={g.rc - 9} fill="none" stroke="#1B2540" strokeWidth={1} />

      {/* ── marca dello start a mezzogiorno: una tacca incisa, non una piastra
             (li' sopra passano i corridoi delle due piastre HUD alte) ────── */}
      <g>
        <line x1={g.cx} y1={g.cy - g.R - 4} x2={g.cx} y2={g.cy - g.R + 16}
          stroke="#FFA51E" strokeOpacity={0.85} strokeWidth={1.6} />
        <text x={g.cx} y={g.cy - g.R + 27} fill="#FFA51E" fillOpacity={0.8} fontSize={8}
          letterSpacing={1.2} textAnchor="middle" fontFamily={MONO}>{tr('dashboard.start')}</text>
      </g>

      {/* ── il nucleo: il verdetto della run, dove nient'altro puo' finire ── */}
      <g>
        <text x={g.cx} y={g.cy - 34} fill="#4E5C82" fontSize={8} letterSpacing={2.2}
          textAnchor="middle" fontFamily={MONO}>{tr('dashboard.run_cost')}</text>
        <text x={g.cx} y={g.cy - 6} fill="#ECF1FA" fontSize={26} fontWeight={300}
          textAnchor="middle" fontFamily={MONO}>{fmtEur(costoRun)}</text>
        {koCost != null && koCost > 0 && (
          <text x={g.cx} y={g.cy + 12} fill="#FFA51E" fontSize={9} textAnchor="middle" fontFamily={MONO}>
            {tr('dashboard.of_which')} {fmtEur(koCost)} {tr('dashboard.from_failed_desks')}
          </text>
        )}
        <line x1={g.cx - 58} y1={g.cy + 24} x2={g.cx + 58} y2={g.cy + 24} stroke="#1E2740" />
        <text x={g.cx} y={g.cy + 40} fill="#8D9FC4" fontSize={9} textAnchor="middle" fontFamily={MONO}>
          {memoLabel}
        </text>
        {/* il totale VERO quando e' dichiarato (N8): a run viva `calls` sono le
            ultime 50 ricevute e scrivere quel numero come totale era il bug;
            a tappato SENZA totale (contratto monco) niente fallback zitto su
            calls.length: «totale n.d.» dichiarato (review 31/08) */}
        <text x={g.cx} y={g.cy + 54} fill="#8D9FC4" fontSize={8} textAnchor="middle" fontFamily={MONO}>
          {fmtDurShort(p.runSec)} · {p.logTappato && p.nCallsTot == null
            ? tr('dashboard.calls_total_missing') : tr((p.nCallsTot ?? p.calls.length) === 1 ? 'dashboard.calls_count_one' : 'dashboard.calls_count', {a: p.nCallsTot ?? p.calls.length})}{p.logTappato ? tr('dashboard.calls_recent', {a: p.calls.length}) : ''}
        </text>
        {/* quante ne sono state fatte fino al cursore: cambia col mouse.
            La forma tappata «N del log fino a MM:SS» e' corta APPOSTA: col
            suffisso « nel log» la riga sbordava di ~10-17px per lato sotto
            l'anello a s150 (review 31/08, metrica svg-kit 0,6 em) */}
        <text x={g.cx} y={g.cy + 70} fontWeight={600} fill="#73829F" fontSize={9} textAnchor="middle" fontFamily={MONO}>
          {p.logTappato
            ? tr('dashboard.calls_until', {a: p.calls.filter(c => c.t <= cursor).length, b: fmtClock(cursor)})
            : tr('dashboard.calls_at_minute', {a: p.calls.filter(c => c.t <= cursor).length, b: fmtClock(cursor)})}
          {activeNow.length > 0 && tr('dashboard.working_count', {a: activeNow.length})}
        </text>
      </g>

      {/* ── LE TARGHE DI FASE, dipinte per ULTIME (#2, blocco B 31/08) ──────
          Il difetto aveva DUE meccanismi, entrambi misurati a s150: (1) la
          geometria si inverte (R−66 = 91 < nucleo rc = 112) e le targhe
          cadevano DENTRO il disco del nucleo, che essendo dipinto dopo le
          COPRIVA — R0 a 1,02:1 con nominale 9,43; (2) due targhe contigue
          possono coprirsi FRA LORO vicino a mezzogiorno. Cura: (1) targhe in
          coda all'SVG (niente sotto cui sparire — gli archi restano al loro
          posto nella pila); (2) le targhe che si sovrappongono in orizzontale
          formano un gruppo e `layoutLabels` le separa in verticale; chi non
          collide non si muove di un pixel. L'ordine del DOM resta quello
          delle fasi (il cancello legge [data-fase] in ordine). */}
      {(() => {
        const items = p.phases.map(ph => {
          const dashed = !ph.measured;
          const [lx, ly] = PT(g.R - 66, (ph.t0 + ph.t1) / 2);
          const lines: LabLine[] = [
            { t: ph.k === 'SINTESI' ? tr('dashboard.synthesis') : ph.k === 'FRA ROUND' ? tr('dashboard.between_rounds') : ph.k, col: dashed ? '#8D9FC4' : '#FFA51E', size: 9, ls: 1.1 },
            /* round APERTO: la fine non c'e' ancora, e non si scrive l'orologio come se fosse un estremo */
            { t: `${fmtClock(ph.t0)}→${ph.open ? tr('dashboard.progress_lower') : fmtClock(ph.t1)}`, col: '#8D9FC4', size: 7.5, ls: 0.2 },
          ];
          /* 11 e non 9,5: con l'interlinea stretta la riga da 9px e la riga
             degli orari si toccavano dentro la loro stessa piastra (misurato
             in collaudo: 16% di sovrapposizione su tutte e quattro le fasi) */
          const bw = plateBox(lines, 11).w;
          return { ph, dashed, lx, lines, bw, y: ly, h: 32 };
        });
        /* cluster transitivi per sovrapposizione orizzontale delle piastre */
        const capo_ = items.map((_, i) => i);
        const trova = (i: number): number => capo_[i] === i ? i : (capo_[i] = trova(capo_[i]));
        for (let i = 0; i < items.length; i++)
          for (let j = i + 1; j < items.length; j++)
            if (Math.abs(items[i].lx - items[j].lx) < (items[i].bw + items[j].bw) / 2 + 6)
              capo_[trova(i)] = trova(j);
        const gruppi = new Map<number, typeof items>();
        items.forEach((it, i) => {
          const k = trova(i);
          gruppi.set(k, [...(gruppi.get(k) || []), it]);
        });
        const ySep = new Map<string, number>();
        for (const gr of gruppi.values()) {
          if (gr.length < 2) continue;
          for (const it of layoutLabels(gr, g.pad + 20, h - g.pad - 20, 6)) ySep.set(it.ph.k, it.ly);
        }
        return items.map(({ ph, dashed, lx, lines, bw, y }) => (
          <g key={ph.k} data-fase={ph.k}>
            <Plate x={lx - bw / 2} y={ySep.get(ph.k) ?? y} lines={lines} align="start" lh={11}
              stroke={dashed ? '#2A3760' : 'rgba(255,165,30,.45)'} />
          </g>
        ));
      })()}
    </svg>
  );
}
