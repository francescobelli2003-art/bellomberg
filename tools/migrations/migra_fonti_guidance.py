# -*- coding: utf-8 -*-
"""Migrazione una-tantum: il dict FONTI di fonti_guidance.py -> data/fonti_guidance.json.
Legge con ast.literal_eval (senza ESEGUIRE il modulo, come lista_privata nel cancello) e
RICONCILIA prima di dichiarare fatto: una garanzia e' una misura, non una frase.
Dry-run per default; scrive solo con --apply.

GIA' ESEGUITA (Task 1 del piano fonti IR, 03/09): il letterale `FONTI = {` non esiste
piu' in `fonti_guidance.py`, che ora legge il negozio da `data/`. Lo strumento resta nel
repo per la convenzione di casa sui `migra_*` — la migrazione si legge dal codice che
l'ha fatta — e perche' `tests/test_migra_fonti.py` ne misura le due funzioni su sorgenti
finti. Lanciato nudo NON e' rotto: MISURA lo stato e lo DICHIARA (v. `stato()`), invece
di uscire in errore sull'ancora mancante e farsi leggere come un guasto da chi clona il
repo pubblico e lo prova."""
import argparse
import ast
import io
import json
import os
import re
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", RADICE)
from bellomberg.core.paths import DATA_DIR
SORGENTE = os.path.join(RADICE, 'src/bellomberg/market_data/fonti_guidance.py')


ANCORA = r"^FONTI = (\{.*?^\})"


def _dest_default():
    return os.path.join(DATA_DIR, "fonti_guidance.json")


def leggi_fonti(path=SORGENTE):
    """Il dict FONTI letto SENZA eseguire il modulo."""
    testo = io.open(path, encoding="utf-8").read()
    m = re.search(ANCORA, testo, re.S | re.M)
    if not m:
        raise SystemExit("ANCORA NON TROVATA: 'FONTI = {' in %s" % path)
    return ast.literal_eval(m.group(1))


def stato(sorgente=SORGENTE, dest=None):
    """(codice, frase) sullo stato della migrazione, MISURATO su entrambi i capi.

    Tre stati, e nessuno dei tre e' muto:
      'da_fare'  l'ancora `FONTI = {` c'e' ancora nel sorgente -> si migra;
      'fatta'    l'ancora non c'e' PIU' e il negozio di destinazione esiste ed e'
                 popolato: e' lo stato normale dal 03/09, e l'uscita e' 0 perche' cio'
                 che lo strumento doveva ottenere e' gia' vero;
      'rotta'    l'ancora non c'e' e nemmeno il negozio: qui manca DAVVERO qualcosa e
                 l'uscita e' 1.
    Il numero di voci del negozio si conta per decidere, e NON si stampa: `data/` e'
    privato e un conteggio e' una cifra derivata da cio' che sta li' dentro."""
    if dest is None:
        dest = _dest_default()
    if not os.path.isfile(sorgente):
        return "rotta", "sorgente assente: %s" % sorgente
    testo = io.open(sorgente, encoding="utf-8").read()
    if re.search(ANCORA, testo, re.S | re.M):
        return "da_fare", "ancora 'FONTI = {' presente in %s" % sorgente
    if not os.path.isfile(dest):
        return "rotta", ("l'ancora 'FONTI = {' non e' in %s E il negozio %s non esiste: "
                         "non c'e' ne' da dove migrare ne' cio' che sarebbe migrato"
                         % (sorgente, dest))
    try:
        with open(dest, encoding="utf-8") as fh:
            voci = [k for k in json.load(fh) if not k.startswith("_")]
    except (ValueError, OSError) as e:
        return "rotta", ("il negozio %s non e' leggibile (%s): lo stato della migrazione "
                         "non e' misurabile" % (dest, type(e).__name__))
    if not voci:
        return "rotta", "il negozio %s non ha nessuna voce" % dest
    return "fatta", ("l'ancora 'FONTI = {' non e' piu' in %s e il negozio %s esiste ed e' "
                     "popolato (il conteggio non si stampa: e' un dato privato)"
                     % (sorgente, dest))


def riconcilia(atteso, path_json):
    """Conta voci e campi da ENTRAMBE le parti e elenca le differenze."""
    with open(path_json, encoding="utf-8") as fh:
        scritto = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    differenze = []
    for tk in sorted(set(atteso) | set(scritto)):
        if tk not in scritto:
            differenze.append("%s: PERSA" % tk)
        elif tk not in atteso:
            differenze.append("%s: COMPARSA dal nulla" % tk)
        elif atteso[tk] != scritto[tk]:
            differenze.append("%s: campi diversi" % tk)
    return {"voci": len(scritto),
            "campi": sum(len(v) for v in scritto.values()),
            "differenze": differenze}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="scrive davvero")
    ap.add_argument("--dest", default=None)
    a = ap.parse_args(argv)

    dest = a.dest or _dest_default()
    codice, frase = stato(SORGENTE, dest)
    if codice == "fatta":
        print("MIGRAZIONE UNA-TANTUM GIA' ESEGUITA (Task 1, 03/09): niente da fare.")
        print("misura : %s" % frase)
        print("Questo strumento non e' rotto: e' finito. Resta nel repo perche' la "
              "migrazione si legge dal codice che l'ha fatta.")
        return 0
    if codice == "rotta":
        print("MIGRAZIONE NON MISURABILE: %s" % frase)
        return 1

    fonti = leggi_fonti()
    print("sorgente : %s" % SORGENTE)
    print("voci     : %d" % len(fonti))
    print("campi    : %d" % sum(len(v) for v in fonti.values()))
    print("dest     : %s" % dest)
    if not a.apply:
        print("\nDRY-RUN: niente scritto. Rilancia con --apply")
        return 0

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    payload = {"_leggimi": "Negozio PRIVATO delle fonti IR. Non viene mai pubblicato."}
    payload.update(fonti)
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

    r = riconcilia(fonti, dest)
    print("\nRICONCILIAZIONE: %d voci, %d campi" % (r["voci"], r["campi"]))
    if r["differenze"]:
        for d in r["differenze"]:
            print("  DIFFERENZA: %s" % d)
        raise SystemExit("MIGRAZIONE NON RICONCILIATA: non togliere il letterale")
    print("riconciliata: identica alla sorgente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
