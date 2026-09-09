# -*- coding: utf-8 -*-
"""Migrazione una-tantum: il dict TICKER_SEARCH_TERMS di news_aggregator.py ->
data/news_search_terms.json (lotto 1 del criterio (1), 04/09 sera, Fable 5.1).
Legge con ast.literal_eval (senza ESEGUIRE il modulo, come lista_privata nel cancello) e
RICONCILIA prima di dichiarare fatto: una garanzia e' una misura, non una frase.
Dry-run per default; scrive solo con --apply.

`--escludi TICKER` (ripetibile) aggiunge al negozio una voce `null`: e' cosi' che i
simboli che il codice saltava con un set letterale (`in {"..."}`, ripetuto quattro volte)
entrano nel negozio come esclusione DICHIARATA. Anche queste voci si riconciliano.

Stessa forma di scripts/migra_fonti_guidance.py: lanciato nudo DOPO la migrazione non e'
rotto, MISURA lo stato e lo DICHIARA (v. `stato()`)."""
import argparse
import ast
import io
import json
import os
import re
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SORGENTE = os.path.join(RADICE, 'src/bellomberg/market_data/news_aggregator.py')

ANCORA = r"^TICKER_SEARCH_TERMS: Dict\[str, List\[str\]\] = (\{.*?^\})"
LEGGIMI = ("Negozio PRIVATO dei termini di ricerca news. Non viene mai pubblicato. "
           "TICKER -> lista di NOMI da cercare; null = simbolo escluso dal giro news e "
           "dagli eventi societari SEC (esclusione dichiarata). Le chiavi con `_` davanti "
           "sono note.")


def leggi_termini(path=SORGENTE):
    """Il dict TICKER_SEARCH_TERMS letto SENZA eseguire il modulo."""
    testo = io.open(path, encoding="utf-8").read()
    m = re.search(ANCORA, testo, re.S | re.M)
    if not m:
        raise SystemExit("ANCORA NON TROVATA: 'TICKER_SEARCH_TERMS: ... = {' in %s" % path)
    return ast.literal_eval(m.group(1))


def _dest_default():
    """Lo STESSO percorso che legge il modulo (memory_db.DB_DIR onora BELLOMBERG_DATA_DIR):
    se il migratore scrivesse in RADICE/data con la variabile impostata, riconcilierebbe
    verde un negozio che a runtime risulta assente (review 04/09). La regola e' replicata,
    non importata: importare memory_db carica il .env nell'ambiente."""
    env = (os.environ.get("BELLOMBERG_DATA_DIR") or "").strip()
    cartella = os.path.join(RADICE, os.path.expanduser(env)) if env else os.path.join(RADICE, "data")
    return os.path.join(cartella, "news_search_terms.json")


def stato(sorgente=SORGENTE, dest=None):
    """(codice, frase) sullo stato della migrazione, MISURATO su entrambi i capi:
    'da_fare' (ancora presente), 'fatta' (ancora assente e negozio popolato),
    'rotta' (ne' l'una ne' l'altro). Il numero di voci non si stampa: e' privato."""
    if dest is None:
        dest = _dest_default()
    if not os.path.isfile(sorgente):
        return "rotta", "sorgente assente: %s" % sorgente
    testo = io.open(sorgente, encoding="utf-8").read()
    if re.search(ANCORA, testo, re.S | re.M):
        return "da_fare", "ancora 'TICKER_SEARCH_TERMS = {' presente in %s" % sorgente
    if not os.path.isfile(dest):
        return "rotta", ("l'ancora 'TICKER_SEARCH_TERMS = {' non e' in %s E il negozio %s non "
                         "esiste: non c'e' ne' da dove migrare ne' cio' che sarebbe migrato"
                         % (sorgente, dest))
    try:
        with open(dest, encoding="utf-8") as fh:
            voci = [k for k in json.load(fh) if not k.startswith("_")]
    except (ValueError, OSError) as e:
        return "rotta", ("il negozio %s non e' leggibile (%s): lo stato della migrazione "
                         "non e' misurabile" % (dest, type(e).__name__))
    if not voci:
        return "rotta", "il negozio %s non ha nessuna voce" % dest
    return "fatta", ("l'ancora 'TICKER_SEARCH_TERMS = {' non e' piu' in %s e il negozio %s "
                     "esiste ed e' popolato (il conteggio non si stampa: e' un dato privato)"
                     % (sorgente, dest))


def riconcilia(atteso, path_json):
    """Conta voci e termini da ENTRAMBE le parti e elenca le differenze.
    `termini` conta le stringhe delle voci con lista; le voci null contano come voci."""
    with open(path_json, encoding="utf-8") as fh:
        scritto = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    differenze = []
    for tk in sorted(set(atteso) | set(scritto)):
        if tk not in scritto:
            differenze.append("%s: PERSA" % tk)
        elif tk not in atteso:
            differenze.append("%s: COMPARSA dal nulla" % tk)
        elif atteso[tk] != scritto[tk]:
            differenze.append("%s: termini diversi" % tk)
    return {"voci": len(scritto),
            "termini": sum(len(v) for v in scritto.values() if v),
            "differenze": differenze}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="scrive davvero")
    ap.add_argument("--dest", default=None)
    ap.add_argument("--escludi", action="append", default=[],
                    help="simbolo da scrivere come voce null (escluso dalle news), ripetibile")
    a = ap.parse_args(argv)

    dest = a.dest or _dest_default()
    codice, frase = stato(SORGENTE, dest)
    if codice == "fatta":
        print("MIGRAZIONE UNA-TANTUM GIA' ESEGUITA (lotto 1 criterio (1), 04/09): niente da fare.")
        print("misura : %s" % frase)
        print("Questo strumento non e' rotto: e' finito. Resta nel repo perche' la "
              "migrazione si legge dal codice che l'ha fatta.")
        return 0
    if codice == "rotta":
        print("MIGRAZIONE NON MISURABILE: %s" % frase)
        return 1

    termini = leggi_termini(SORGENTE)
    atteso = dict(termini)
    for e in a.escludi:
        atteso[e.strip().upper()] = None
    print("sorgente : %s" % SORGENTE)
    print("voci     : %d (di cui %d esclusioni da --escludi)" % (len(atteso), len(a.escludi)))
    print("termini  : %d" % sum(len(v) for v in termini.values()))
    print("dest     : %s" % dest)
    if not a.apply:
        print("\nDRY-RUN: niente scritto. Rilancia con --apply")
        return 0

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    payload = {"_leggimi": LEGGIMI}
    payload.update(atteso)
    tmp = dest + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    for _ in range(25):
        try:
            os.replace(tmp, dest)
            break
        except PermissionError:
            pass
    else:
        raise SystemExit("os.replace fallito: un altro processo tiene aperto %s" % dest)

    r = riconcilia(atteso, dest)
    print("\nRICONCILIAZIONE: %d voci, %d termini" % (r["voci"], r["termini"]))
    if r["differenze"]:
        for d in r["differenze"]:
            print("  DIFFERENZA: %s" % d)
        raise SystemExit("MIGRAZIONE NON RICONCILIATA: non togliere il letterale")
    print("riconciliata: identica alla sorgente (piu' le esclusioni dichiarate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
