/** Generic research questions. Portfolio instruments are supplied by the user's API. */
export const AGENT_QUESTIONS: Record<string, string[]> = {
  capo: ['Quali decisioni aperte meritano una nuova valutazione e quali dati mancano?', 'Metti alla prova la tesi complessiva del portafoglio: rischi, catalizzatori e alternative.', 'Confronta le conclusioni degli specialisti e spiegami i disaccordi ancora aperti.'],
  macro: ['Quali cambiamenti macro possono incidere sulle mie esposizioni attuali?', 'Costruisci uno scenario centrale e due scenari alternativi, con segnali da monitorare.', 'Distingui i rischi di crescita, inflazione e tassi rilevanti per il portafoglio.'],
  options: ['Quali rischi del portafoglio si possono studiare con opzioni compatibili con il mandato?', 'Confronta due coperture: costo, payoff, liquidità, greche e condizioni di fallimento.', 'Dove la struttura a termine e lo skew segnalano rischi da approfondire?'],
  quant: ['Quali posizioni contribuiscono maggiormente al rischio e con quale qualità dei dati?', 'Confronta fattori, correlazioni e concentrazione per individuare rischi sovrapposti.', 'Quali segnali quantitativi contraddicono le tesi attuali del portafoglio?'],
  fundamentals: ['Valuta una posizione usando il modello adatto alla sua natura e spiega le ipotesi.', 'Quali tesi dipendono maggiormente da crescita, margini o costo del capitale?', 'Confronta qualità degli utili, bilancio e allocazione del capitale delle mie posizioni.'],
  crypto: ['Quali rischi crypto diretti e indiretti sono presenti nel mio portafoglio?', 'Distingui esposizione al token, rischio societario e premio sul NAV nelle mie posizioni.', 'Quali dati di funding, liquidità e flussi possono confermare o smentire la tesi?'],
  eventdesk: ['Quali eventi futuri possono cambiare le tesi delle mie posizioni?', 'Separa notizie confermate, indiscrezioni e interpretazioni, indicando fonti e date.', 'Valuta gli scenari politici e regolamentari rilevanti per il portafoglio.'],
  politics: ['Quali rischi politici e regolamentari incidono sulle mie posizioni?', 'Confronta scenari geopolitici alternativi e canali di trasmissione al portafoglio.', 'Quali eventi verificabili potrebbero smentire lo scenario politico centrale?'],
  news: ['Quali notizie recenti cambiano concretamente le tesi del portafoglio?', 'Distingui eventi confermati, indiscrezioni e commenti, citando le fonti.', 'Quali eventi richiedono un approfondimento da parte degli altri specialisti?'],
};
const FOCUS: Record<string, string> = {
  capo: 'sintesi della tesi, confronto fra specialisti, rischi, catalizzatori e decisioni da rivalutare',
  macro: 'sensibilità a crescita, inflazione, tassi, valute e scenari macro alternativi',
  options: 'scadenze, volatilità implicita, skew, liquidità, greche e strategie compatibili con il mandato',
  quant: 'fattori, correlazioni, volatilità, contributo al rischio e robustezza del campione',
  fundamentals: 'valutazione adatta alla natura del titolo, bilancio, utili, moat e ipotesi della tesi',
  crypto: 'esposizioni crypto economiche, liquidità, tokenomics e rischi societari; dichiara se il dominio non è pertinente',
  eventdesk: 'catalizzatori, notizie verificate, eventi societari e rischi politici o regolamentari',
  politics: 'rischi politici e regolamentari, scenari geopolitici e canali di trasmissione economici',
  news: 'notizie confermate, fonti primarie, cronologia e impatto sulla tesi',
};
function checkedTicker(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Z0-9^][A-Z0-9.^=_/\-]{0,39}$/i.test(value.trim())) {
    throw new Error('ticker del portafoglio assente o non valido');
  }
  return value.trim();
}
export function portfolioTickers(positions: unknown): string[] {
  if (!Array.isArray(positions)) throw new Error('elenco posizioni non disponibile');
  return [...new Set(positions.map(row => checkedTicker(row?.ticker)))];
}
export function tickerPrompt(agent: string, ticker: string): string {
  const symbol = checkedTicker(ticker);
  const focus = FOCUS[agent] || 'tesi, rischi e dati pertinenti al tuo dominio di analisi';
  return `Analizza ${symbol} dal punto di vista del tuo desk: ${focus}. Usa il mandato corrente e i dati aggiornati disponibili nei tool. Sviluppa un'analisi approfondita con fonti, ipotesi, controtesi e dati mancanti dichiarati. Distingui fatti, interpretazioni e condizioni che cambierebbero la conclusione.`;
}
