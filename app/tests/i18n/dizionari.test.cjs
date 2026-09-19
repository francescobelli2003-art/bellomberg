// Parita' dei dizionari: stesse chiavi in it/ e en/ (oltre a tsc, che gia' lo impone),
// nessun valore vuoto, nessun valore copiato pari pari fra le due lingue salvo
// eccezioni DICHIARATE (etichette neutre: sigle, tasti, marchi).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
const carica = creaCaricatore();
const { it } = carica('i18n/it/index.ts');
const { en } = carica('i18n/en/index.ts');

// Exact shared domain term; do not exempt other content under the same namespace.
const NEUTRE_PER_CHIAVE = { 'trade.opening_ticker': 'Ticker', 'voldeck.chain': 'Chain',
  'voldeck.spot': 'Spot', 'voldeck.expected_move': 'Expected move 1σ',
  'newsdesk.blipCountOne': '{a} BLIP', 'progress.callsCountOne': '{a} call',
  'movements.bothCounts': '{a} + {b}' };

const NEUTRE = /^(OK|ESC|CTRL\+K|F\d{1,2}|N\.D\.|n\.d\.|n\/a|API|PING|LIVE|DOWN|ONLINE|OFFLINE|RUN LIVE|CONFIG|BELLOMBERG.*|PRIVATE INTELLIGENCE TERMINAL|V0\.9 OBSIDIAN|PIN AUTHENTICATION|◈ ACCESS GRANTED|◌ AUTHENTICATING…|◌ AUTHORIZE ACCESS|SAT-07|BLM-1 ASCENT|MEMORIA SQLITE|FEED NEWS \/ FRED|QUANT GARCH \/ MC|ALT|VEL|ORBIT|LINK|KM 0|Engine|Agents|SESSION|Command Center|Performance|Watchlist|Global Markets|News Desk|Fundamentals|Factor Lab|Monte Carlo|Vol Deck|Edge Scanner|Agent Chat|Agents Live|Memo Archive|Trade Entry|MKT \{ticker\}|NEWS \{ticker\}|REFRESH NEWS FEED|BACKUP DATABASE|pull \+ classify \(~60s\)|polygon -> yfinance.*|force refresh performance|weekly research note|snapshot data\/consigliere\.db|blotter \+ trade entry|\{tasto\} · \{label\} · \{gruppo\}|\{key\} \/\/ \{label\}|\{dd\}\/\{mm\}\/\{aa\}.*|\{n\} \{unita\}|\{n\}|\{testo\}|1 \{valuta\}|FX 60s|FX STALE \{minuti\}M|IMPATTO 2026-OB · 4\.2 KT|T\+\{mm\}:\{ss\}  ALT \{alt\} KM)$/;

test('ogni spazio dei nomi esiste in entrambe le lingue con le stesse chiavi', () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(it).sort());
  for (const ns of Object.keys(it)) {
    assert.deepEqual(Object.keys(en[ns]).sort(), Object.keys(it[ns]).sort(), 'namespace ' + ns);
  }
});

test('nessun valore vuoto e nessuna copia it=en fuori dalle etichette neutre dichiarate', () => {
  const copie = [], vuoti = [];
  for (const ns of Object.keys(it)) {
    for (const k of Object.keys(it[ns])) {
      const a = it[ns][k], b = en[ns][k];
      if (!String(a).trim()) vuoti.push(`it ${ns}.${k}`);
      if (!String(b).trim()) vuoti.push(`en ${ns}.${k}`);
      if (a === b && !NEUTRE.test(a) && NEUTRE_PER_CHIAVE[`${ns}.${k}`] !== a) copie.push(`${ns}.${k} = ${JSON.stringify(a)}`);
    }
  }
  assert.deepEqual(vuoti, []);
  assert.deepEqual(copie, [], 'valori identici nelle due lingue non dichiarati neutri');
  for (const [path, value] of Object.entries(NEUTRE_PER_CHIAVE)) {
    const [ns, key] = path.split('.');
    assert.equal(it[ns]?.[key], value, `stale neutral entry: ${path}`);
    assert.equal(en[ns]?.[key], value, `stale neutral entry: ${path}`);
  }
});

test('i segnaposto {nome} sono gli stessi nelle due lingue', () => {
  const segnaposto = s => (String(s).match(/\{[a-zA-Z_]+\}/g) || []).sort();
  for (const ns of Object.keys(it)) {
    for (const k of Object.keys(it[ns])) {
      assert.deepEqual(segnaposto(en[ns][k]), segnaposto(it[ns][k]), `${ns}.${k}`);
    }
  }
});

test('i dizionari non contengono cifre della classe vietata (importi con migliaia + euro, rendimenti firmati)', () => {
  const IMPORTO_EUR = /\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?\s?(?:€|EUR(?![A-Za-z]))/;
  const PCT_FIRMATA = /[+\-−]\d{1,3}[.,]\d{2}\s?%/;
  for (const diz of [it, en]) for (const ns of Object.keys(diz)) for (const k of Object.keys(diz[ns])) {
    const v = String(diz[ns][k]);
    assert.doesNotMatch(v, IMPORTO_EUR, `${ns}.${k}`);
    assert.doesNotMatch(v, PCT_FIRMATA, `${ns}.${k}`);
  }
});

// 13/09 (Claude Opus 5): glifi persi in scrittura (changelog 275: «Volatilit? obiettivo», «un?azione»;
// Impostazioni: «n.d. ? percorso», «non ? attestato», «&nbsp;» letterale nella palette). Uno script
// passato a PowerShell 5.1 in pipe esce con $OutputEncoding ASCII e scrive «?» al posto di accenti,
// apostrofi tipografici e separatori (· —); un'entita' HTML copiata da JSX in una stringa React si
// vede letterale. Una domanda vera chiude la frase o precede una maiuscola: nessuna delle forme qui
// sotto la descrive. Limite dichiarato: un accento perso in coda al valore («QUALIT?») resta
// indistinguibile da una domanda.
const GLIFI_PERSI = [
  [/\p{L}\?\p{L}/u, 'lettera?lettera'],
  [/(^|\s)\?(\s|$)/u, '? isolato'],
  [/\?\s+\p{Ll}/u, '? prima di minuscola'],
  [/\?[.,;:]/u, '? prima di punteggiatura'],
  [/\p{Lu}\?\s+\p{Lu}/u, '? fra parole maiuscole'],
  [/�/u, 'U+FFFD'],
  [/&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);/u, 'entita HTML letterale'],
];
const glifiPersi = s => GLIFI_PERSI.filter(([re]) => re.test(s)).map(([, classe]) => classe);

test('la guardia dei glifi persi cattura le forme gia viste e lascia passare le domande vere', () => {
  // Oracolo congelato qui, non letto dai cataloghi sotto prova.
  for (const s of ['Volatilit? obiettivo', 'un?azione', 'n.d. ? percorso non dichiarato dal payload',
    '? quella stessa data', '. Il legame con questo lavoro non ? attestato.', 'Qualit?.', 'VERIT? CELLA', 'x � y',
    'CTRL+K APRI/CHIUDI &nbsp;·&nbsp; ESC CHIUDI']) {
    assert.notDeepEqual(glifiPersi(s), [], s);
  }
  for (const s of ['FERMARE il consigliere in corso? I risultati parziali vanno persi.',
    "{a} è prima del {b}: refuso sull'anno?", "Revocare il veto su #{a} ({b})? La run potrà riproporre l'idea.",
    'STOP the current committee run? Partial results will be lost.', 'Delete the conversation?', 'Si può confrontare?',
    'analisi multi-agente — conferma prima del lancio', 'CTRL+K APRI/CHIUDI  ·  ESC CHIUDI']) {
    assert.deepEqual(glifiPersi(s), [], s);
  }
});

test('nessun valore dei cataloghi ha un «?» al posto di un accento, di un apostrofo o di un separatore', () => {
  const trovati = [];
  const visita = (lingua, percorso, v) => {
    if (typeof v === 'string') {
      const classi = glifiPersi(v);
      if (classi.length) trovati.push(`${lingua} ${percorso} [${classi.join(', ')}] ${JSON.stringify(v)}`);
    } else if (v && typeof v === 'object') {
      for (const [k, x] of Object.entries(v)) visita(lingua, percorso ? `${percorso}.${k}` : k, x);
    }
  };
  visita('it', '', it);
  visita('en', '', en);
  assert.deepEqual(trovati, [], 'glifi persi in scrittura');
});

test('le etichette ricostruite dicono il testo scritto dagli autori', () => {
  // Fonte primaria: i log delle sessioni che le hanno scritte (09/09 e 12/09), prima del canale
  // che le ha corrotte. Frasi congelate qui.
  const atteso = {
    it: { but_folder: "— quella stessa data nell'elenco ricevuto compare",
      inconsistent: '. Il legame con questo lavoro non è attestato.',
      run_confirm_hint: 'analisi multi-agente — conferma prima del lancio',
      run_started: 'ANALISI AVVIATA — apri Agenti in diretta',
      path_not_declared: 'n.d. — percorso non dichiarato dal payload',
      palette_keys: 'CTRL+K APRI/CHIUDI  ·  ↑↓ NAVIGA  ·  INVIO ESEGUI  ·  ESC CHIUDI' },
    en: { but_folder: '— on the same date, the returned list contains',
      run_confirm_hint: 'multi-agent analysis — confirmation before launch',
      run_started: 'ANALYSIS STARTED — open Agents Live',
      path_not_declared: 'n/a — path not declared in the payload',
      palette_keys: 'CTRL+K OPEN/CLOSE  ·  ↑↓ NAVIGATE  ·  ENTER EXECUTE  ·  ESC CLOSE' },
  };
  for (const [lingua, voci] of Object.entries(atteso)) {
    const catalogo = lingua === 'it' ? it : en;
    for (const [k, v] of Object.entries(voci)) assert.equal(catalogo.settings[k], v, `${lingua}.settings.${k}`);
  }
});
