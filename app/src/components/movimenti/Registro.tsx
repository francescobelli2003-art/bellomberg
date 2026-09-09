// F14 · vista REGISTRO — l'atterraggio (Opus 5, 27/07; cassa 21/08)
//
// Le righe in ordine di DATA VERA, coi separatori di mese che portano la
// cadenza. Tre cose che la pagina di prima non faceva: rende l'ORA (c'e' su
// tutte le righe e veniva tagliata), mostra ENTRAMBI i testi di una riga
// (`pm_rationale || note` ne buttava uno: 306 caratteri su una vendita sintetica), e marca
// le uscite dichiarando lo stato del loro P&L.
// ⚠️ I conteggi del 27/07 dicevano «69 righe»: erano quelle di allora. Rimisurati
// il 21/08 sul DB vero: **75 trade su 29 ticker**, e l'ora e' un segnaposto
// `12:00:00` su **36 righe su 75** (v. `contaOreSegnaposto`) — quindi «rende
// l'ORA» non vuol dire che quell'ora sia misurata, e la pagina lo dichiara.
// Da oggi il registro ospita anche i FLUSSI DI CASSA (impianto scelto dal PM il
// 21/08): due specie di riga, non due vestiti della stessa.
import { Fragment } from 'react';
import { fmtNum } from '@/lib/format';
import {
  Trade, Mese, StatoCancello, RigaRegistro,
  ggmmaa, oraDi, testiDi, controvalore, esce, segnoPL, chiaveMese, versoDi,
} from '@/lib/movimenti';

/** La sola variante «cassa» dell'unione, senza intersezioni: `A & {specie:'cassa'}`
 *  lascerebbe in gioco anche il ramo titolo con `specie: never`. */
type RigaCassaT = Extract<RigaRegistro, { specie: 'cassa' }>;

interface Props {
  righe: RigaRegistro[];             // gia' ordinate per data vera, decrescente
  mesi: Mese[];
  cancello: StatoCancello;
}

/** Il realizzato di una riga, con la tricotomia dichiarata. */
function realizzato(t: Trade, cancello: StatoCancello) {
  if (cancello === 'chiuso')
    return <span className="gate">P&amp;L AL CANCELLO</span>;
  /* ⚠️ `n.d.` e non `—`: qui il cancello e' APERTO e il numero manca lo stesso,
     cioe' e' un'assenza VERA del dato. Il `—` in questa stessa colonna significa
     gia' «non applicabile» (riga che non e' un'uscita, :204) e da oggi anche
     «riga di cassa». Tre significati per un glifo solo. Il Diario rendeva gia'
     questo caso come `REALIZZATO n.d.` (`Diario.tsx`, review 27/07): la cura era
     stata applicata li' e non qui. */
  if (typeof t.realized_eur !== 'number' || !isFinite(t.realized_eur))
    return <span className="t-no">n.d.</span>;
  const s = segnoPL(t.realized_eur);
  return (
    <span className={'pl ' + s}>
      {(s === 'su' ? '+' : '') + fmtNum(t.realized_eur, 2)} €
    </span>
  );
}

/**
 * Una riga di cassa nelle OTTO colonne del registro.
 *
 * ⚠️ QUATTRO celle su otto non hanno niente da dire, e il trattino e' la
 * verita': un versamento non ha ticker, quantita', prezzo ne' realizzato.
 * L'unica colonna che lo accoglie senza forzarlo e' CONTROVALORE, perche'
 * porta gia' la valuta accanto al numero — e un flusso di cassa e' in EUR
 * per costruzione (`amount_eur`).
 *
 * ⚠️ La cella QUANDO porta la sola data, SENZA il trattino dell'ora. Il `—` in
 * quella colonna significa gia' «l'ora manca dal payload» (:189, e su 36 righe
 * su 75 l'ora c'e' ma e' il segnaposto `12:00:00` dell'importatore): usarlo
 * anche per «questa specie non ha un'ora» sarebbe un terzo significato sullo
 * stesso glifo. Meglio niente che un segno ambiguo.
 *
 * ⚠️ E `created_at` NON e' la data valuta: e' quando la riga e' entrata nel
 * libro. Vive nel `title`/`aria-label`, dichiarato per quello che e' e col fuso
 * detto — SQLite lo scrive con `datetime('now')`, cioe' in **UTC**
 * (`memory_db.py:337`), e renderlo nudo faceva leggere al PM le 16:18 dove il
 * suo orologio diceva 18:18. E' la stessa trappola gia' pagata su
 * `position_prices.timestamp` (ponte (73), 19/08).
 *
 * ⚠️ E nemmeno `date` e' garantita essere la data valuta: `memory_db.py:1090`
 * fa `data = data or datetime.now()...`, quindi la colonna vale «data valuta
 * OPPURE giorno di registrazione» e dal payload le due non si distinguono. La
 * riga vera a registro lo dice da sola nella causale («data valuta effettiva
 * non specificata»). Quindi l'etichetta dice `Data`, non `Data valuta`.
 */
function RigaCassa({ m }: { m: RigaCassaT }) {
  const mov = m.m;
  const v = mov.amount_eur;
  const leggibile = typeof v === 'number' && isFinite(v);
  const verso = versoDi(mov.type);
  const nota = (mov.note || '').trim();
  const segno = verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '';
  const quando = `Data ${ggmmaa(mov.date)}`
    + (mov.created_at ? ` · registrato il ${mov.created_at} UTC` : '');
  /* ⚠️ `title` SENZA `aria-label`. Con tutti e due — misurato sull'albero AX di
     Chromium — `aria-label` vinceva come NOME della cella, `title` la seguiva
     come DESCRIZIONE, e un lettore di schermo leggeva la stessa frase due volte
     mentre il contenuto visibile (`12/08/26`) non veniva mai annunciato. Col
     solo `title`, il nome torna al contenuto e la frase resta descrizione. */
  return (
    <tr className="cassa">
      <td className="d num nw" title={quando}>
        {ggmmaa(mov.date)}
      </td>
      <td className="nw">
        {/* un tipo lungo dilatava la colonna AZIONE da 98 a 417px (misurato):
            in cella ne sta un pezzo, il resto nel title */}
        <span className="flx" title={verso === 'ignoto' ? mov.type : undefined}>
          {verso === 'dentro' ? '↓ VERSA'
            : verso === 'fuori' ? '↑ PRELEVA'
            : `VERSO n.d. (${String(mov.type).slice(0, 14)})`}
        </span>
      </td>
      <td className="d nw"><span className="t-no">—</span></td>
      <td className="r num nw"><span className="t-no">—</span></td>
      <td className="r num nw"><span className="t-no">—</span></td>
      {/* importo illeggibile = buco DICHIARATO, mai uno zero (regola 14/07) */}
      <td className="r num d nw">
        {leggibile
          ? `${segno}${fmtNum(Math.abs(v as number), 2)} EUR`
          : <span className="t-no">n.d.</span>}
      </td>
      <td className="r num nw"><span className="t-no">—</span></td>
      <td className="cm">
        {nota ? <span>{nota}</span> : <span className="t-no">—</span>}
      </td>
    </tr>
  );
}

export default function Registro({ righe, mesi, cancello }: Props) {
  const perChiave = new Map(mesi.map(m => [m.chiave, m]));
  /* ⚠️ `null` e non `''`: da quando esiste il gruppo «data non leggibile», `''`
     E' una chiave valida, e partire da lei avrebbe saltato il suo separatore. */
  let meseCorrente: string | null = null;

  return (
    <table>
      <thead>
        <tr>
          <th>Quando</th>
          <th>Azione</th>
          <th>Ticker</th>
          <th className="r">Qtà</th>
          <th className="r">Prezzo</th>
          <th className="r">Controvalore</th>
          <th className="r">Realizzato</th>
          <th>Commento del PM</th>
        </tr>
      </thead>
      <tbody>
        {righe.map((r, i) => {
          /* ⚠️ LA STESSA funzione che usa `raggruppaPerMese`. Quando i due
             criteri divergevano, una riga con data illeggibile veniva resa
             sotto un separatore che non la contava. */
          const k = chiaveMese(r.quando);
          const nuovo = k !== meseCorrente;
          if (nuovo) meseCorrente = k;
          const m = nuovo ? perChiave.get(k) : undefined;

          // il separatore del mese, identico per le due specie: e' la cadenza
          // delle righe RESE, e da oggi le righe rese comprendono la cassa
          const sep = m && (
            <tr className="msep">
              <td colSpan={8}>
                <span className="mm">{m.etichetta}</span>
                <span className="mn">
                  {/* il singolare esiste: un mese da una riga sola c'e' gia'
                      sui dati veri (gennaio 2026, il versamento iniziale) */}
                  {m.n} {m.n === 1 ? 'MOVIMENTO' : 'MOVIMENTI'}
                  {/* ⚠️ «0 TITOLI» e' rumore, non un dato: da quando il registro
                      ospita anche la cassa esistono mesi di SOLI flussi, e li'
                      quel conteggio non ha niente da contare. Misurato sulla
                      sonda: «GENNAIO 2026 · 1 MOVIMENTI · 0 TITOLI · 1 DI CASSA». */}
                  {/* «SU n TITOLI» e non «n TITOLI»: in una fila di conteggi di
                      RIGHE, l'unico che conta altro (ticker distinti) si
                      leggeva come una riga in piu' e la somma non tornava. */}
                  {m.nTicker > 0 && ` · SU ${m.nTicker} ${m.nTicker === 1 ? 'TITOLO' : 'TITOLI'}`}
                  {Object.entries(m.perAzione)
                    .sort((a, b) => b[1] - a[1])
                    .map(([a, n]) => ` · ${n} ${a}`)}
                  {m.nCassa > 0 && ` · ${m.nCassa} DI CASSA`}
                </span>
              </td>
            </tr>
          );

          if (r.specie === 'cassa') {
            return (
              <Fragment key={`c-${r.m.id}-${i}`}>
                {sep}
                <RigaCassa m={r} />
              </Fragment>
            );
          }

          const t = r.t;
          const { rationale, nota, vuoto } = testiDi(t);
          const ctrl = controvalore(t);
          const ora = oraDi(t.data);

          // la chiave sta sul Fragment: una riga puo' portarsi dietro il
          // separatore del suo mese, e senza chiave qui React perde il conto
          return (
            <Fragment key={`t-${t.data}-${t.ticker}-${i}`}>
              {sep}
              <tr className={esce(t.action) ? 'uscita' : undefined}>
                <td className="d num nw">
                  {ggmmaa(t.data)}{' '}
                  <span className="t-no">{ora || '—'}</span>
                </td>
                <td className="nw"><span className={'act ' + t.action}>{t.action}</span></td>
                <td className="d nw">{t.ticker}</td>
                <td className="r num nw">{t.quantita == null ? 'n.d.' : fmtNum(t.quantita, 0)}</td>
                <td className="r num nw">
                  {t.prezzo == null ? 'n.d.' : fmtNum(t.prezzo, 2)} {t.valuta}
                </td>
                {/* valuta NATIVA: mai un simbolo € su un numero in GBX o USD,
                    e mai un cambio calcolato qui dentro */}
                <td className="r num d nw">
                  {ctrl == null ? 'n.d.' : `${fmtNum(ctrl, 2)} ${t.valuta || '?'}`}
                </td>
                <td className="r num nw">
                  {esce(t.action) ? realizzato(t, cancello) : <span className="t-no">—</span>}
                </td>
                <td className="cm">
                  {vuoto
                    ? <span className="t-no">—</span>
                    : <>
                        {rationale && <span>{rationale}</span>}
                        {/* i DUE testi, non uno: dove ci sono entrambi il
                            secondo veniva perso in silenzio */}
                        {nota && <span className={rationale ? 'due' : ''}>{nota}</span>}
                      </>}
                </td>
              </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
