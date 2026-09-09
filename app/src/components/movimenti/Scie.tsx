// F14 · vista SCIE — l'oggetto per cui la pagina si riconosce (Opus 5, 27/07)
//
// 27 corsie (una per ticker, ordinate per PRIMO movimento) su 172 giorni di
// tempo vero. Ogni corsia ha una LINEA DI GALLEGGIAMENTO: sopra si entra,
// sotto si esce. Le nove uscite si vedono perche' sono le uniche cose che
// scendono in mezzo a cinquantotto ingressi.
//
// L'ALTEZZA DI UN SEGNO E' IL PESO DENTRO LA SUA CORSIA, ed e' la scelta che
// rende l'oggetto onesto: dentro una corsia la valuta e' una sola, quindi
// confrontare due segni e' lecito. Fra corsie diverse NON lo e' (EUR/USD/GBX)
// e l'intestazione lo scrive, invece di lasciarlo intuire.
//
// Niente SVG: le posizioni sono percentuali del contenitore. Cosi' non c'e'
// un `preserveAspectRatio` che stira le scritte e non serve misurare il box
// in pixel. Ogni segno e' un <button>: si raggiunge col Tab e col fuoco apre
// la stessa lettura numerica del passaggio col mouse.
import type { CSSProperties } from 'react';
import { fmtNum } from '@/lib/format';
import {
  Corsia, Mossa, Arco, StatoCancello,
  ggmmaa, oraDi, testiDi, mesiDellArco, entra, esce, segnoPL,
} from '@/lib/movimenti';

interface Props {
  corsie: Corsia[];
  arco: Arco;
  cancello: StatoCancello;
  selezione: string | null;
  onSeleziona: (ticker: string) => void;
}

/** Vicino ai bordi la lettura si ancora dal lato giusto, invece di uscire dal riquadro. */
const ancora = (f: number) => (f > 0.72 ? ' dx' : f < 0.14 ? ' sx' : '');

interface PropsSegno {
  m: Mossa;
  corsia: Corsia;
  cancello: StatoCancello;
  /** posizione della corsia e quante ce ne sono: decide DA CHE PARTE si apre la lettura */
  riga: number;
  righe: number;
}

function Segno({ m, corsia, cancello, riga, righe }: PropsSegno) {
  const t = m.trade;
  const uscita = esce(t.action);
  const dividendo = t.action === 'DIVIDEND';
  const { rationale, nota } = testiDi(t);
  const commento = rationale || nota;
  const ora = oraDi(t.data);

  // col cancello aperto l'uscita prende da sola il colore del suo esito
  let esito = '';
  if (uscita && cancello === 'aperto' && typeof t.realized_eur === 'number' && isFinite(t.realized_eur))
    esito = ' ' + segnoPL(t.realized_eur);

  const cls = dividendo ? 'mk div'
    : uscita ? 'mk out' + esito
    : 'mk in' + (t.action === 'ADD' ? ' ADD' : '');

  const stile: CSSProperties = { left: `${(m.f * 100).toFixed(3)}%` };
  if (!dividendo) stile.height = `${(m.rel * (uscita ? 34 : 58)).toFixed(2)}%`;

  const peso = m.ctrl == null ? 'n.d.' : `${(m.rel * 100).toFixed(0)}%`;

  // ⚠️ DA CHE PARTE SI APRE LA LETTURA. La tip e' assoluta dentro la corsia,
  // e l'unico antenato che ritaglia e' `.pb{overflow:auto}`: quello che esce
  // SOPRA l'origine di uno scroller non e' recuperabile — non esiste scroll
  // negativo. Con 27 corsie da ~25px e una tip da ~110px, aprendo sempre
  // verso l'alto le prime quattro corsie perdevano la lettura numerica, col
  // mouse E col fuoco (review 27/07, ALTA: 13 tooltip su 93 tagliati, fino a
  // 104px su 115). Ora la meta' alta apre in giu' e la meta' bassa in su:
  // qualunque sia la corsia, la lettura ha sempre l'intero riquadro davanti.
  const apreGiu = riga < righe / 2;

  const etichetta = `${corsia.ticker} ${t.action} del ${ggmmaa(t.data)}, `
    + `${fmtNum(t.quantita, 0)} per ${fmtNum(t.prezzo, 2)} ${t.valuta}`
    + `, controvalore ${m.ctrl == null ? 'non disponibile' : `${fmtNum(m.ctrl, 2)} ${t.valuta}`}`
    + `, peso nella corsia ${peso}`
    + (uscita
        ? cancello === 'chiuso'
          ? ', P e L realizzato non disponibile: non consegnato dal backend'
          : typeof t.realized_eur === 'number' && isFinite(t.realized_eur)
            ? `, realizzato ${fmtNum(t.realized_eur, 2)} euro`
            : ', nessun realizzato su questa riga'
        : '');

  return (
    <>
      <button
        type="button"
        className={cls}
        style={stile}
        aria-label={etichetta}
        // ESC libera la lettura senza dover cercare un altro bersaglio col Tab
        onKeyDown={e => { if (e.key === 'Escape') (e.currentTarget as HTMLElement).blur(); }}
      />
      <div
        className={'tip' + ancora(m.f)}
        style={apreGiu
          ? { left: stile.left, top: 'calc(62% + 12px)' }
          : { left: stile.left, bottom: 'calc(38% + 12px)' }}
      >
        <b>{corsia.ticker} · {t.action}</b>
        <div className="kv"><span>quando</span>
          <span className="num">{ggmmaa(t.data)} {ora || '—'}</span></div>
        <div className="kv"><span>quanto</span>
          <span className="num">{fmtNum(t.quantita, 0)} × {fmtNum(t.prezzo, 2)} {t.valuta}</span></div>
        <div className="kv"><span>controvalore</span>
          <span className="num">
            {m.ctrl == null ? 'n.d.' : `${fmtNum(m.ctrl, 2)} ${t.valuta || '?'}`}
          </span></div>
        {/* Senza controvalore il peso non e' 0%, e' incalcolabile: uno 0%
            sarebbe un numero inventato su un dato che non c'e'.
            E il metro e' DICHIARATO: il peso e' relativo alle mosse della
            corsia nella STESSA valuta, non a tutta la corsia — su MSTR, che
            ha righe in USD e in EUR, le due cose sono diverse. */}
        <div className="kv">
          <span>peso fra i {t.valuta} della corsia</span>
          <span className="num">{peso}</span></div>
        {uscita && (cancello === 'chiuso'
          ? <i>P&amp;L REALIZZATO: DIETRO IL CANCELLO — GET /trades non consegna realized_eur</i>
          : typeof t.realized_eur === 'number' && isFinite(t.realized_eur)
            ? <div className="kv"><span>realizzato</span>
                <span className="num">
                  {(t.realized_eur > 0 ? '+' : '') + fmtNum(t.realized_eur, 2)} €
                </span></div>
            : <i>nessun realizzato su questa riga</i>)}
        {commento && <i>« {commento} »</i>}
      </div>
    </>
  );
}

export default function Scie({ corsie, arco, cancello, selezione, onSeleziona }: Props) {
  const mesi = mesiDellArco(arco);

  return (
    <div className="scie">
      <div className="guide" aria-hidden="true">
        {mesi.map(m => (
          <div className="gm" key={m.f} style={{ left: `${(m.f * 100).toFixed(3)}%` }} />
        ))}
      </div>

      <div className="corsie">
        {corsie.map((c, riga) => (
          <div className={'lane' + (c.ticker === selezione ? ' sel' : '')} key={c.ticker}>
            {/* Il bottone della corsia NAVIGA (porta al diario di quel titolo),
                non commuta un interruttore: `aria-pressed` lo annunciava come
                "non premuto" e poi la pagina cambiava vista sotto le mani.
                E il riassunto stava in un `title`, che col fuoco da tastiera
                non compare mai — ora e' nell'etichetta. (review 27/07) */}
            <button
              type="button"
              className="lk"
              onClick={() => onSeleziona(c.ticker)}
              aria-label={`${c.ticker}, ${c.n} movimenti dal ${ggmmaa(c.primo)} al ${ggmmaa(c.ultimo)}`
                + `, in ${c.valute.join(' e ')}`
                + `. ${c.ticker === selezione ? 'Corsia scelta: togli la scelta' : 'Apri il diario di questa corsia'}`}
              title={`${c.ticker} — ${c.n} movimenti, dal ${ggmmaa(c.primo)} al ${ggmmaa(c.ultimo)}`}
            >
              <span className="tk">{c.ticker}</span>
              {/* una corsia MISTA lo dice: MSTR ha righe in USD e in EUR, e
                  scrivere solo "USD" avrebbe etichettato male due mosse su
                  cinque (review 27/07, ALTA) */}
              <span className={'vl' + (c.valute.length > 1 ? ' mista' : '')}>
                {c.valute.join('·')}
              </span>
            </button>
            <div className="lt">
              {c.mosse.map((m, i) => (
                <Segno key={`${m.trade.data}-${i}`} m={m} corsia={c} cancello={cancello}
                  riga={riga} righe={corsie.length} />
              ))}
            </div>
            <div className="ln num">{c.n}</div>
          </div>
        ))}
      </div>

      <div className="asse" aria-hidden="true">
        {mesi.map(m => (
          <div className="gm" key={m.f} style={{ left: `${(m.f * 100).toFixed(3)}%` }}>{m.nome}</div>
        ))}
      </div>
    </div>
  );
}

/**
 * I verbi contati PER COLORE, non per famiglia: la legenda mette ogni numero
 * accanto alla pastiglia che lo disegna, e BUY (ambra) e ADD (ciano) sono due
 * colori diversi. Sommarli sotto "INGRESSO" scriveva 58 accanto a un colore
 * che sul grafico ne disegna 56 (review 27/07).
 */
export function contaVerbi(corsie: Corsia[]) {
  let buy = 0, add = 0, uscite = 0, dividendi = 0;
  for (const c of corsie)
    for (const m of c.mosse) {
      const a = m.trade.action;
      if (a === 'ADD') add++;
      else if (entra(a)) buy++;
      else if (esce(a)) uscite++;
      else if (a === 'DIVIDEND') dividendi++;
    }
  return { buy, add, ingressi: buy + add, uscite, dividendi };
}
