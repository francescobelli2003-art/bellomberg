import React from 'react';

/* ============================================================================
   MEMO-MD — il markdown dei memo reso in nodi React (Opus 5, 27/07, F9 v3).

   Sta in lib/ e non dentro la pagina perche' non e' roba di F9: lo stesso
   `full_markdown` serve all'appendice, e servira' ai 147 report degli
   specialisti quando il backend li esporra' (voce I-18 del dossier).

   Sottoinsieme di markdown trattato — quello che il Capo scrive DAVVERO,
   verificato sui 18 memo in archivio (536.846 caratteri):
     ##  / ###   titoli di sezione (22 sul memo 47, struttura fissa su tutti)
     | a | b |   tabelle (ACTION TABLE, Tabella Scenari)
     - / *       elenchi
     **testo**   grassetto

   Due rimandi che NON sono markdown ma sono la lingua di casa, e qui
   diventano oggetti invece di stringhe in mezzo alla prosa:
     [src: tool]  la citazione della fonte — regola invalicabile "numeri SOLO
                  dai tool, tag [src: tool]". Renderla visibile e' rendere
                  visibile la catena di custodia di ogni cifra.
     #123         il richiamo a una decisione in DB.

   Niente `dangerouslySetInnerHTML`: i memo sono scritti da un LLM e finiscono
   in una pagina che mostra soldi veri. Si costruiscono nodi, non HTML.
   ========================================================================== */

export interface MemoSezione {
  /** id dell'ancora nel documento, per la spina di navigazione */
  id: string;
  /** 2 = sezione, 3 = sotto-sezione (le "Decisione N:") */
  livello: 2 | 3;
  titolo: string;
}

export interface MemoReso {
  nodi: React.ReactNode[];
  sezioni: MemoSezione[];
}

/** Spezza una riga nei suoi pezzi: grassetto, [src: …], #123, testo nudo. */
export function inline(testo: string, chiave: string): React.ReactNode[] {
  const pezzi = testo.split(/(\*\*[^*]+\*\*|\[src:[^\]]+\]|#\d{1,3}\b)/g);
  const out: React.ReactNode[] = [];
  pezzi.forEach((p, i) => {
    if (!p) return;
    const k = `${chiave}-${i}`;
    if (p.startsWith('**') && p.endsWith('**')) out.push(<strong key={k}>{p.slice(2, -2)}</strong>);
    else if (p.startsWith('[src:')) out.push(
      <span className="mdsrc" key={k} title="fonte dichiarata dal memo">
        {p.slice(1, -1).replace(/^src:\s*/, '')}
      </span>);
    else if (/^#\d/.test(p)) out.push(
      <span className="mdref" key={k} title="richiamo a una decisione in archivio">{p}</span>);
    else out.push(<React.Fragment key={k}>{p}</React.Fragment>);
  });
  return out;
}

/** Titoli di sezione senza rendere il corpo: serve alla spina quando il testo
 *  del memo non e' ancora stato caricato. */
export function sezioniDi(md: string): MemoSezione[] {
  const out: MemoSezione[] = [];
  md.split('\n').forEach(riga => {
    const m = /^(#{2,3})\s+(.*)$/.exec(riga);
    if (m) out.push({ id: `sez-${out.length}`, livello: m[1].length as 2 | 3, titolo: m[2].trim() });
  });
  return out;
}

/**
 * Rende il markdown di un memo.
 * @param saltaSezioni titoli (in maiuscolo, confronto per prefisso) da NON
 *        rendere nel corpo. F9 ci passa ACTION TABLE, perche' quella tabella
 *        e' gia' in cima alla pagina come strumento, con l'esito di ogni riga
 *        accanto: renderla due volte darebbe la stessa tabella, muta.
 *        La sezione resta comunque contata nella spina.
 */
export function rendiMemo(md: string, saltaSezioni: string[] = []): MemoReso {
  const righe = md.split('\n');
  const nodi: React.ReactNode[] = [];
  const sezioni: MemoSezione[] = [];
  let i = 0;

  const daSaltare = (t: string) =>
    saltaSezioni.some(s => t.toUpperCase().startsWith(s.toUpperCase()));

  while (i < righe.length) {
    const riga = righe[i];

    // ── titoli ──────────────────────────────────────────────────────────
    const h = /^(#{1,3})\s+(.*)$/.exec(riga);
    if (h) {
      // il titolo del documento (#) sta gia' nella testata del pannello
      if (h[1].length === 1) { i++; continue; }
      const livello = h[1].length as 2 | 3;
      const titolo = h[2].trim();
      const id = `sez-${sezioni.length}`;
      sezioni.push({ id, livello, titolo });
      i++;
      if (livello === 2 && daSaltare(titolo)) {
        while (i < righe.length && !/^##\s/.test(righe[i])) i++;
        continue;
      }
      nodi.push(livello === 2
        ? <h2 id={id} key={id}>{titolo}</h2>
        : <h3 id={id} key={id}>{inline(titolo, id)}</h3>);
      continue;
    }

    // ── tabelle ─────────────────────────────────────────────────────────
    if (/^\s*\|/.test(riga)) {
      const intestazione: string[] = [];
      const corpo: string[][] = [];
      while (i < righe.length && /^\s*\|/.test(righe[i])) {
        const celle = righe[i].trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
        // la riga di separazione (|---|---|) non e' un dato: si scarta
        const separatore = celle.every(c => /^:?-+:?$/.test(c.replace(/\s/g, '')));
        if (!separatore) {
          if (intestazione.length === 0) intestazione.push(...celle);   // prima riga = testata
          else corpo.push(celle);
        }
        i++;
      }
      const kt = `tab-${nodi.length}`;
      nodi.push(
        <table className="mdtab" key={kt}>
          {intestazione.length > 0 && (
            <thead><tr>{intestazione.map((c, k) => <th key={k}>{c}</th>)}</tr></thead>
          )}
          <tbody>
            {corpo.map((r, k) => (
              <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c, `${kt}-${k}-${j}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
      continue;
    }

    // ── elenchi ─────────────────────────────────────────────────────────
    if (/^\s*[-*]\s+/.test(riga)) {
      const voci: string[] = [];
      while (i < righe.length && /^\s*[-*]\s+/.test(righe[i])) {
        voci.push(righe[i].replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      const kl = `ul-${nodi.length}`;
      nodi.push(<ul className="mdul" key={kl}>{voci.map((v, k) =>
        <li key={k}>{inline(v, `${kl}-${k}`)}</li>)}</ul>);
      continue;
    }

    // ── prosa ───────────────────────────────────────────────────────────
    if (riga.trim()) {
      const kp = `p-${nodi.length}`;
      nodi.push(<p key={kp}>{inline(riga.trim(), kp)}</p>);
    }
    i++;
  }

  return { nodi, sezioni };
}

/** Censimento delle citazioni `[src: …]` di un memo, dalla piu' usata.
 *  ATTENZIONE alla parola giusta: sono STRINGHE DISTINTE, non "strumenti".
 *  Dentro le parentesi il Capo scrive anche "FRED, ground truth" o
 *  "edge scan forza 100": contarle come tool sarebbe un numero gonfiato. */
export function citazioni(md: string): { fonte: string; n: number }[] {
  const conta = new Map<string, number>();
  const re = /\[src:\s*([^\]]+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(md))) {
    const k = m[1].trim();
    conta.set(k, (conta.get(k) || 0) + 1);
  }
  return [...conta.entries()].map(([fonte, n]) => ({ fonte, n })).sort((a, b) => b.n - a.n);
}
