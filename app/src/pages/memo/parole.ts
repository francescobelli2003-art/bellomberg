import { linguaCorrente, type Lingua } from '@/i18n/lingua';
import { traduci, type Chiave, type Parametri } from '@/i18n/t';
import type { Esito } from './logica';

// I testi stanno nel catalogo (i18n/it|en/memoarchive.ts): qui solo la forma che le viste usano.
function costruisci(l: Lingua) {
  const tr = (k: Chiave, p?: Record<string, string | number>) => traduci(l, k, p as Parametri | undefined);
  const plur = (n: number, uno: Chiave, altri: Chiave, p: Record<string, string | number> = {}) => tr(n === 1 ? uno : altri, { n, ...p });
  return {
    title: tr('memoarchive.nTitle'),
    readable: (n: number) => plur(n, 'memoarchive.nReadable_one', 'memoarchive.nReadable_other'),
    readableNd: tr('memoarchive.nReadableNd'),
    lastRun: tr('memoarchive.nLastRun'),
    decisions: (n: number) => plur(n, 'memoarchive.oneDecision', 'memoarchive.decisionsCount'),
    decisionsNd: tr('memoarchive.nDecisionsNd'),
    dcfFlagged: (n: number) => plur(n, 'memoarchive.nDcfFlagged_one', 'memoarchive.nDcfFlagged_other'),
    dcfChipHint: (tot: number, flag: number) => tr('memoarchive.nDcfChipHint', { tot, flag }),
    dcfInvalid: tr('memoarchive.dcfInvalid'),
    unknown: tr('memoarchive.errorUnknown'),
    nd: tr('memoarchive.nNd'),

    searchLabel: tr('memoarchive.nSearchLabel'),
    searchPh: tr('memoarchive.nSearchPh'),
    searchKbd: tr('memoarchive.nSearchKbd'),
    searchHint: tr('memoarchive.nSearchHint'),
    searching: tr('memoarchive.nSearching'),
    searchHits: (n: number, m: number, ms: number) => plur(n, 'memoarchive.nSearchHits_one', 'memoarchive.nSearchHits_other', { m, ms }),
    searchOrder: tr('memoarchive.nSearchOrder'),
    searchOrderHint: tr('memoarchive.nSearchOrderHint'),
    searchNone: (q: string) => tr('memoarchive.nSearchNone', { q }),
    searchErr: (d: string) => tr('memoarchive.nSearchErr', { d }),
    dist: (d: string) => tr('memoarchive.nDist', { d }),
    distHint: tr('memoarchive.nDistHint'),
    noText: tr('memoarchive.nNoText'),
    keyArrows: tr('memoarchive.nKeyArrows'), keyPick: tr('memoarchive.nKeyPick'),
    keyEnter: tr('memoarchive.nKeyEnter'), keyOpen: tr('memoarchive.nKeyOpen'),
    keyEsc: tr('memoarchive.nKeyEsc'), keyClose: tr('memoarchive.nKeyClose'),
    searchFoot: tr('memoarchive.nSearchFoot'),

    runs: tr('memoarchive.nRuns'),
    runsNote: (n: number) => tr('memoarchive.nRunsNote', { n }),
    runLine: (dec: string, ese: number | string) => tr('memoarchive.nRunLine', { dec, ese }),
    runOpen: (id: number) => tr('memoarchive.nRunOpen', { id }),
    invested: tr('memoarchive.nInvested'),
    investedNd: tr('memoarchive.nInvestedNd'),
    empty: tr('memoarchive.nEmpty'),
    loading: tr('memoarchive.nLoading'),
    archiveErr: tr('memoarchive.nArchiveErr'),
    archiveErrBody: (d: string) => tr('memoarchive.nArchiveErrBody', { d }),
    retry: tr('memoarchive.nRetry'),
    sampleError: (detail: string) => tr('memoarchive.sampleError', { detail }),
    sampleLoading: tr('memoarchive.sampleLoading'),
    sampleUndeclared: (n: number) => tr('memoarchive.sampleUndeclared', { n }),
    sampleCount: (excluded: number, rows: number) => tr('memoarchive.sampleCount', {
      excluded, rows,
      excludedUnit: tr(excluded === 1 ? 'memoarchive.rowOne' : 'memoarchive.rows'),
      rowUnit: tr(rows === 1 ? 'memoarchive.rowOne' : 'memoarchive.rows'),
    }),

    noMemo: tr('memoarchive.nNoMemo'),
    noMemoErr: tr('memoarchive.nNoMemoErr'),
    pick: tr('memoarchive.nPick'),
    memoOf: (d: string) => tr('memoarchive.nMemoOf', { d }),
    kicker: (id: number, d: string) => tr('memoarchive.nKicker', { id, d }),
    pdf: tr('memoarchive.nPdf'), pdfOff: tr('memoarchive.nPdfOff'),
    appendix: tr('memoarchive.nAppendix'), appendixOff: tr('memoarchive.nAppendixOff'),
    chars: (n: string) => tr('memoarchive.nChars', { n }),
    sections: (n: number) => plur(n, 'memoarchive.nSections_one', 'memoarchive.nSections_other'),
    originalTitle: (t: string) => tr('memoarchive.nOriginalTitle', { t }),
    output: (lang: string | null | undefined) => tr(lang === 'it' ? 'communications.originalOutputIt'
      : lang === 'en' ? 'communications.originalOutputEn' : 'communications.originalOutputUnknown'),
    mandate: (d: string) => tr('memoarchive.nMandate', { d }),
    mandateHint: (f: string, o: string) => tr('memoarchive.nMandateHint', { f, o }),
    summary: tr('memoarchive.nSummary'),
    priority: tr('memoarchive.nPriority'),
    objection: tr('memoarchive.nObjection'),
    toc: tr('memoarchive.nToc'),
    textLoading: tr('memoarchive.nTextLoading'),
    textErr: (d: string) => tr('memoarchive.nTextErr', { d }),
    textEmpty: tr('memoarchive.nTextEmpty'),

    outcomes: tr('memoarchive.nOutcomes'),
    outcomesNote: tr('memoarchive.nOutcomesNote'),
    outcomesRows: (n: number) => plur(n, 'memoarchive.nOutcomesRows_one', 'memoarchive.nOutcomesRows_other'),
    executedOf: (n: number) => tr('memoarchive.nExecutedOf', { n }),
    noActionTable: tr('memoarchive.nNoActionTable'),
    noDecisions: tr('memoarchive.nNoDecisions'),
    decErr: (d: string) => tr('memoarchive.nDecErr', { d }),
    esito: {
      EXECUTED: tr('memoarchive.nExecuted'), PARTIAL: tr('memoarchive.nPartial'), PENDING: tr('memoarchive.nPending'),
      SKIPPED: tr('memoarchive.nSkipped'), EXPIRED: tr('memoarchive.nExpired'),
    } as Record<Esito, string>,
    legenda: {
      EXECUTED: tr('memoarchive.nLegExecuted'), PARTIAL: tr('memoarchive.nLegPartial'), PENDING: tr('memoarchive.nLegPending'),
      SKIPPED: tr('memoarchive.nLegSkipped'), EXPIRED: tr('memoarchive.nLegExpired'),
    } as Record<Esito, string>,
    unlinked: tr('memoarchive.nUnlinked'),
    unlinkedLeg: tr('memoarchive.nLegUnlinked'),
    amount: tr('memoarchive.nAmount'),
    conviction: tr('memoarchive.nConviction'),
    convShort: (c: string) => tr('memoarchive.nConvShort', { c }),

    kit: tr('memoarchive.nKit'),
    kitNote: (id: number) => tr('memoarchive.nKitNote', { id }),
    investedAt: tr('memoarchive.nInvestedAt'),
    investedSub: tr('memoarchive.nInvestedSub'),
    securitiesBasis: tr('memoarchive.securitiesBasis'),
    market: tr('memoarchive.nMarket'),
    marketSub: (a: number, b: number) => tr('memoarchive.nMarketSub', { a, b }),
    marketHint: tr('memoarchive.nMarketHint'),
    tokens: tr('memoarchive.nTokens'),
    tokensSub: (a: string, b: string) => tr('memoarchive.nTokensSub', { a, b }),
    tokensNd: tr('memoarchive.nTokensNd'),
    dcf: tr('memoarchive.nDcf'),
    dcfNone: tr('memoarchive.nDcfNone'),
    dcfFlags: (n: number) => plur(n, 'memoarchive.nDcfFlags_one', 'memoarchive.nDcfFlags_other'),
    dcfNoFlags: tr('memoarchive.nDcfNoFlags'),
    dcfFlag: tr('memoarchive.nDcfFlag'),
    sources: tr('memoarchive.nSources'),
    sourcesNote: (a: number, b: number) => tr('memoarchive.nSourcesNote', { a, b }),
    sourcesAll: tr('memoarchive.nSourcesAll'),
    sourcesTop: tr('memoarchive.nSourcesTop'),
    sourcesNone: tr('memoarchive.nSourcesNone'),
    link: tr('memoarchive.nLink'),
  };
}
export type Parole = ReturnType<typeof costruisci>;

const cache = new Map<Lingua, Parole>();
/** Le parole della pagina nella lingua corrente (ricostruite solo al cambio lingua). */
export function parole(): Parole {
  const l = linguaCorrente();
  let p = cache.get(l);
  if (!p) { p = costruisci(l); cache.set(l, p); }
  return p;
}
