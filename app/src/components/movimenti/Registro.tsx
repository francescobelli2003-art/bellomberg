import { t as tr } from '@/i18n/t';
import { useT } from '@/i18n/provider';
// Registro unificato: tempi convenzionali e provenienza restano dichiarati.
import { Fragment } from 'react';
import { fmtNum } from '@/lib/format';
import {
  Trade, Mese, StatoCancello, RigaRegistro,
  ggmmaa, oraTrade, legameMovimento, testiDi, controvalore, esce, segnoPL, chiaveMese, versoDi,
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
    return <span className="gate">{tr('movements.pnlGate')}</span>;
  /* ⚠️ `n.d.` e non `—`: qui il cancello e' APERTO e il numero manca lo stesso,
     cioe' e' un'assenza VERA del dato. Il `—` in questa stessa colonna significa
     gia' «non applicabile» (riga che non e' un'uscita, :204) e da oggi anche
     «riga di cassa». Tre significati per un glifo solo. Il Diario rendeva gia'
     questo caso come `REALIZZATO n.d.` (`Diario.tsx`, review 27/07): la cura era
     stata applicata li' e non qui. */
  if (typeof t.realized_eur !== 'number' || !isFinite(t.realized_eur))
    return <span className="t-no">{tr('movements.nd')}</span>;
  const s = segnoPL(t.realized_eur);
  return (
    <span className={'pl ' + s}>
      {(s === 'su' ? '+' : '') + fmtNum(t.realized_eur, 2)} €
    </span>
  );
}

/** Riga di cassa nelle colonne comuni: senza ticker, quantita, prezzo o P&L.
 * L'importo e in EUR. La data della riga e distinta da created_at, che e
 * l'istante UTC di registrazione; una data presente non prova un'ora misurata.
 */
function RigaCassa({ m }: { m: RigaCassaT }) {
  const tr = useT();
  const mov = m.m;
  const v = mov.amount_eur;
  const leggibile = typeof v === 'number' && isFinite(v);
  const verso = versoDi(mov.type);
  const nota = (mov.note || '').trim();
  const segno = verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '';
  const quando = tr('movements.dateTitle', {a: ggmmaa(mov.date)})
    + (mov.created_at ? tr('movements.recordedAt', {a: mov.created_at}) : '');
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
          {verso === 'dentro' ? tr('movements.deposit')
            : verso === 'fuori' ? tr('movements.withdraw')
            : tr('movements.directionUnknown', {a: String(mov.type).slice(0, 14)})}
        </span>
      </td>
      <td className="d nw"><span className="t-no">—</span></td>
      <td className="r num nw"><span className="t-no">—</span></td>
      <td className="r num nw"><span className="t-no">—</span></td>
      {/* importo illeggibile = buco DICHIARATO, mai uno zero (regola 14/07) */}
      <td className="r num d nw">
        {leggibile
          ? `${segno}${fmtNum(Math.abs(v as number), 2)} EUR`
          : <span className="t-no">{tr('movements.nd')}</span>}
      </td>
      <td className="r num nw"><span className="t-no">—</span></td>
      <td className="cm">
        {nota ? <span>{nota}</span> : <span className="t-no">—</span>}
      </td>
    </tr>
  );
}

export default function Registro({ righe, mesi, cancello }: Props) {
  const tr = useT();
  const perChiave = new Map(mesi.map(m => [m.chiave, m]));
  /* ⚠️ `null` e non `''`: da quando esiste il gruppo «data non leggibile», `''`
     E' una chiave valida, e partire da lei avrebbe saltato il suo separatore. */
  let meseCorrente: string | null = null;

  return (
    <table>
      <thead>
        <tr>
          <th>{tr('movements.when')}</th>
          <th>{tr('movements.action')}</th>
          <th>Ticker</th>
          <th className="r">{tr('movements.quantity')}</th>
          <th className="r">{tr('movements.price')}</th>
          <th className="r">{tr('movements.notional')}</th>
          <th className="r">{tr('movements.realized')}</th>
          <th>{tr('movements.pmComment')}</th>
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
                  {/* Una sola riga richiede il singolare. */}
                  {m.n} {m.n === 1 ? tr('movements.oneMovement') : tr('movements.manyMovements')}
                  {/* Un mese composto solo da flussi non conta titoli. */}
                  {/* «SU n TITOLI» e non «n TITOLI»: in una fila di conteggi di
                      RIGHE, l'unico che conta altro (ticker distinti) si
                      leggeva come una riga in piu' e la somma non tornava. */}
                  {m.nTicker > 0 && tr('movements.onSecurities', {a: m.nTicker, b: m.nTicker === 1 ? tr('movements.oneSecurity') : tr('movements.manySecurities')})}
                  {Object.entries(m.perAzione)
                    .sort((a, b) => b[1] - a[1])
                    .map(([a, n]) => ` · ${n} ${a}`)}
                  {m.nCassa > 0 && tr('movements.cashCount', {a: m.nCassa})}
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
          const ora = oraTrade(t);

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
                <td className="r num nw">{t.quantita == null ? tr('movements.nd') : fmtNum(t.quantita, 0)}</td>
                <td className="r num nw">
                  {t.prezzo == null ? tr('movements.nd') : fmtNum(t.prezzo, 2)} {t.valuta}
                </td>
                {/* valuta NATIVA: mai un simbolo € su un numero in GBX o USD,
                    e mai un cambio calcolato qui dentro */}
                <td className="r num d nw">
                  {ctrl == null ? tr('movements.nd') : `${fmtNum(ctrl, 2)} ${t.valuta || '?'}`}
                </td>
                <td className="r num nw">
                  {esce(t.action) ? realizzato(t, cancello) : <span className="t-no">—</span>}
                </td>
                <td className="cm">
                  <span>{legameMovimento(t)}{t.created_at ? tr('movements.recordedTrade', {a: t.created_at}) : ''}</span>
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
