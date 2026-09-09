// F14 · vista DIARIO — le parole (Opus 5, 27/07)
//
// Misurato il 27/07: 54 righe su 69 hanno un commento del PM, e una
// (una vendita sintetica) ne ha DUE — 306 caratteri di `note` piu' 52 di
// `pm_rationale`. La pagina di prima li metteva in `||` e ne mostrava
// uno: qui ci sono entrambi, etichettati, in prosa e non in monospazio.
//
// I numeri restano, ma a servizio del testo: in una colonna di destra
// che non ruba la riga di lettura.
import { fmtNum } from '@/lib/format';
import {
  StatoCancello, RigaRegistro,
  ggmmaa, oraDi, testiDi, controvalore, esce, segnoPL, versoDi,
} from '@/lib/movimenti';

interface Props {
  righe: RigaRegistro[];             // gia' filtrate e ordinate dal contenitore
  cancello: StatoCancello;
}

export default function Diario({ righe, cancello }: Props) {
  return (
    <>
      {righe.map((r, i) => {
        /* La causale di un movimento e' una parola del PM come le altre, ed e'
           per questo che la cassa entra anche qui. Ma NON prende il vestito di
           un trade: niente verbo colorato (in questa pagina l'ambra e' gia'
           BUY), niente ticker, niente «quantita' × prezzo» — un versamento non
           ha nessuna di quelle cose. */
        if (r.specie === 'cassa') {
          const mov = r.m;
          const v = mov.amount_eur;
          const leggibile = typeof v === 'number' && isFinite(v);
          const verso = versoDi(mov.type);
          const segno = verso === 'dentro' ? '+' : verso === 'fuori' ? '−' : '';
          const causale = (mov.note || '').trim();
          return (
            <div className="voce" key={`c-${mov.id}-${i}`}>
              <div className="cap">
                <span className="flx">
                  {verso === 'dentro' ? '↓ VERSA'
                    : verso === 'fuori' ? '↑ PRELEVA'
                    : `VERSO n.d. (${mov.type})`}
                </span>
                <span className="tkn">CASSA</span>
                <span className="dt num">{ggmmaa(mov.date)}</span>
                <span className="qp num">
                  {leggibile
                    ? `${segno}${fmtNum(Math.abs(v as number), 2)} EUR`
                    : 'importo n.d.'}
                </span>
              </div>
              {/* ⚠️ etichettata «Causale» come le altre portano «Motivo»/«Nota»:
                  questa vista si chiama «LE PAROLE DEL PM», e il testo di un
                  movimento spesso NON e' del PM — quello a registro oggi dice
                  «bonifico ~30k dichiarato dal PM», cioe' parla di lui in terza
                  persona: l'ha scritto chi ha importato la riga. */}
              {causale && (
                <div className="tx pro"><span className="et">Causale</span>{causale}</div>
              )}
            </div>
          );
        }

        const t = r.t;
        const { rationale, nota } = testiDi(t);
        const ctrl = controvalore(t);
        const ora = oraDi(t.data);
        // le due etichette compaiono SOLO quando i testi sono due: su una
        // riga sola sarebbero rumore, su due dicono chi ha scritto cosa
        const due = !!rationale && !!nota;

        return (
          <div className="voce" key={`t-${t.data}-${t.ticker}-${i}`}>
            <div className="cap">
              <span className={'act ' + t.action}>{t.action}</span>
              <span className="tkn">{t.ticker}</span>
              <span className="dt num">{ggmmaa(t.data)} <em>{ora || ''}</em></span>
              {esce(t.action) && cancello === 'chiuso' && (
                <span className="gate">P&amp;L AL CANCELLO</span>
              )}
              {/* Col cancello aperto e la riga senza numero qui non compariva
                  NULLA: la stessa uscita passava dal dire "al cancello" al non
                  dire niente, e l'assenza spariva invece di essere dichiarata
                  (review 27/07, MEDIA). */}
              {esce(t.action) && cancello === 'aperto' && (
                typeof t.realized_eur === 'number' && isFinite(t.realized_eur)
                  ? <span className={'pl ' + segnoPL(t.realized_eur)}>
                      {(t.realized_eur > 0 ? '+' : '') + fmtNum(t.realized_eur, 2)} €
                    </span>
                  : <span className="gate">REALIZZATO n.d.</span>
              )}
              <span className="qp num">
                {fmtNum(t.quantita, 0)} × {fmtNum(t.prezzo, 2)} {t.valuta}
                {ctrl != null && <> · {fmtNum(ctrl, 2)} {t.valuta || '?'}</>}
              </span>
            </div>
            {rationale && (
              <div className="tx pro">
                {due && <span className="et">Motivo</span>}{rationale}
              </div>
            )}
            {nota && (
              <div className={'tx pro' + (rationale ? ' due' : '')}>
                {due && <span className="et">Nota</span>}{nota}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
