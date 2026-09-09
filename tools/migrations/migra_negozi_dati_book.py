# -*- coding: utf-8 -*-
"""Migrazione una-tantum: i dati del book che vivevano nei moduli FUORI dalla classificazione
-> i negozi privati in data/ (lotto 5 del criterio (1), 05/09, Fable 5.1).

Famiglie (costante nel sorgente -> negozio):
  alias        finnhub_news._norm_ticker (mappa esplicita) + sec_edgar (alias SEC)  -> data/alias_fonti.json
  iv           iv_history (lista dichiarata)                                        -> data/iv_tickers.json
  fattori      portfolio_factors (crypto-correlati + override di regione)           -> data/fattori_portafoglio.json
  lei          esef (LEI per simbolo)                                               -> data/lei_emittenti.json
  istituzioni  sec_edgar + agent_tools (slug -> CIK, uniti) + cef_lookthrough (CEF) -> data/istituzioni.json
  correzioni   retro_title_chats (correzioni a mano dei titoli)                     -> data/retro_title_correzioni.json

Legge le costanti dal SORGENTE A UNA REVISIONE GIT (`--da <rev>`, default HEAD: dopo il commit
del lotto le costanti non stanno piu' nel tree ma restano nella storia), con ast.literal_eval sul
testo — senza ESEGUIRE i moduli, come lista_privata nel cancello — e RICONCILIA prima di dire
fatto: rilegge ogni negozio col caricatore VERO (negozi_privati) e confronta voce per voce. Stampa
CONTEGGI, mai i simboli. Dry-run per default; scrive solo con --apply; non sovrascrive un negozio
esistente senza --sovrascrivi.

Le due informazioni che il codice non porta (correzioni dei titoli) le passa chi migra sulla riga
di comando, cosi' questo file resta senza simboli del book: `--deve-contenere ID=parola`
(ripetibile: parole che il titolo corretto DEVE avere) e `--intatto ID=parola` (ripetibile: id
che NON si corregge e parola del titolo generato da conservare).
"""
import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys

RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RADICE)
import bellomberg.storage.negozi_privati as np_  # noqa: E402

ANCORE = {
    "finnhub_esplicita": ('src/bellomberg/market_data/finnhub_news.py', r"^    EXPLICIT = (\{.*?^    \})"),
    "sec_alias": ('src/bellomberg/market_data/sec_edgar.py', r"^_TICKER_ALIASES = (\{.*?\})\s*$"),
    "iv": ('src/bellomberg/market_data/iv_history.py', r"^IV_TICKERS: Sequence\[str\] = (\(.*?\))\s*$"),
    "crypto": ('src/bellomberg/portfolio/portfolio_factors.py', r"^CRYPTO_CORRELATED_TICKERS = (\{.*?^\})"),
    "regioni": ('src/bellomberg/portfolio/portfolio_factors.py', r"^REGION_OVERRIDES: Dict\[str, str\] = (\{.*?^\})"),
    "lei": ('src/bellomberg/market_data/esef.py', r"^LEI_BY_TICKER = (\{.*?^\})"),
    "cik_sec": ('src/bellomberg/market_data/sec_edgar.py', r"^INSTITUTIONAL_CIKS = (\{.*?^\})"),
    "cik_tools": ('src/bellomberg/agents/agent_tools.py', r"^INSTITUTIONS_CIK = (\{.*?^\})"),
    "cef": ('src/bellomberg/valuation/cef_lookthrough.py', r"^CEF_MANAGERS = (\{.*?^\})"),
    "correzioni": ('src/bellomberg/cli/retro_title_chats.py', r"^CORREZIONI = (\{.*?^\})"),
}

NEGOZI = {
    "alias": ("alias_fonti.json", np_.carica_alias, "alias"),
    "iv": ("iv_tickers.json", np_.carica_iv, "tickers"),
    "fattori": ("fattori_portafoglio.json", np_.carica_fattori, "fattori"),
    "lei": ("lei_emittenti.json", np_.carica_lei, "lei"),
    "istituzioni": ("istituzioni.json", np_.carica_istituzioni, "istituzioni"),
    "correzioni": ("retro_title_correzioni.json", np_.carica_correzioni_titoli, None),
}

LEGGIMI = ("Negozio PRIVATO: non viene mai pubblicato (data/ e' ignorato da git). "
           "Forma e regole nel .example.json tracciato con lo stesso nome. Migrato da "
           "scripts/migra_negozi_dati_book.py; le chiavi con `_` davanti sono note.")


def sorgente(rev, rel):
    r = subprocess.run(["git", "show", "%s:%s" % (rev, rel)], cwd=RADICE, capture_output=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("git show %s:%s fallito: %s" % (rev, rel, r.stderr.strip()[:200]))
    return r.stdout


def estrai(rev, chiave):
    rel, ancora = ANCORE[chiave]
    m = re.search(ancora, sorgente(rev, rel), re.S | re.M)
    if not m:
        raise SystemExit("ANCORA NON TROVATA per %s in %s@%s" % (chiave, rel, rev))
    return ast.literal_eval(m.group(1))


def _coppie(voci, cosa):
    out = {}
    for v in voci or []:
        if "=" not in v:
            raise SystemExit("%s: atteso ID=parola, ricevuto %r" % (cosa, v))
        sid, parola = v.split("=", 1)
        if not sid.strip().isdigit() or not parola.strip():
            raise SystemExit("%s: atteso ID=parola, ricevuto %r" % (cosa, v))
        out.setdefault(sid.strip(), []).append(parola.strip())
    return out


def costruisci(rev, deve_contenere, intatti):
    """{famiglia: oggetto JSON del negozio} dal sorgente alla revisione `rev`."""
    finnhub = estrai(rev, "finnhub_esplicita")
    sec = estrai(rev, "sec_alias")
    iv = estrai(rev, "iv")
    crypto = estrai(rev, "crypto")
    regioni = estrai(rev, "regioni")
    lei = estrai(rev, "lei")
    cik = dict(estrai(rev, "cik_sec"))
    for slug, v in estrai(rev, "cik_tools").items():
        if slug in cik and cik[slug] != v:
            raise SystemExit("slug %r con due CIK diversi nei due moduli: decide il PM" % slug)
        cik[slug] = v
    cef = {k: {c: v[c] for c in ("investor", "manager", "not_in_13f") if c in v}
           for k, v in estrai(rev, "cef").items()}
    corr = {str(sid): {"titolo": t[0], "cosa_c_era": t[1], "perche": t[2],
                       "deve_contenere": deve_contenere.get(str(sid), [])}
            for sid, t in estrai(rev, "correzioni").items()}
    for sid in deve_contenere:
        if sid not in corr:
            raise SystemExit("--deve-contenere %s: l'id non e' fra le correzioni" % sid)
    for sid in intatti:
        if sid in corr:
            raise SystemExit("--intatto %s: l'id e' fra le correzioni" % sid)
    return {
        "alias": {"_leggimi": LEGGIMI, "finnhub": dict(finnhub), "sec": dict(sec)},
        "iv": {"_leggimi": LEGGIMI, "tickers": list(iv)},
        "fattori": {"_leggimi": LEGGIMI, "crypto_correlati": sorted(crypto),
                    "regioni": dict(regioni)},
        "lei": {"_leggimi": LEGGIMI, **dict(lei)},
        "istituzioni": {"_leggimi": LEGGIMI, "cik": cik, "cef": cef},
        "correzioni": {"_leggimi": LEGGIMI, "correzioni": corr, "intatti": intatti},
    }


def conteggi(fam, obj):
    if fam == "alias":
        return "finnhub %d, sec %d" % (len(obj["finnhub"]), len(obj["sec"]))
    if fam == "iv":
        return "tickers %d" % len(obj["tickers"])
    if fam == "fattori":
        return "crypto_correlati %d, regioni %d" % (len(obj["crypto_correlati"]), len(obj["regioni"]))
    if fam == "lei":
        return "voci %d" % len([k for k in obj if not k.startswith("_")])
    if fam == "istituzioni":
        return "cik %d, cef %d" % (len(obj["cik"]), len(obj["cef"]))
    return "correzioni %d, intatti %d" % (len(obj["correzioni"]), len(obj["intatti"]))


def riconcilia(fam, obj, path):
    """Il negozio scritto, riletto col caricatore VERO, deve dire esattamente le voci costruite."""
    nome, carica, chiave = NEGOZI[fam]
    if fam == "fattori":
        # con le regioni AMMESSE DAL CONSUMATORE: una regione che portfolio_factors non sa
        # regredire renderebbe il negozio illeggibile a runtime, e la migrazione non deve
        # certificare cio' che il consumatore rifiuta (review 05/09)
        from bellomberg.portfolio.portfolio_factors import REGIONAL_FF
        r = carica(path, regioni_valide=set(REGIONAL_FF))
    else:
        r = carica(path)
    if r["motivo"] is not None:
        return "KO: il caricatore vero lo rifiuta: %s" % r["motivo"]
    if fam == "alias":
        ok = r["alias"] == {"finnhub": obj["finnhub"], "sec": obj["sec"]}
    elif fam == "iv":
        ok = list(r["tickers"]) == obj["tickers"]
    elif fam == "fattori":
        ok = (sorted(r["fattori"]["crypto_correlati"]) == obj["crypto_correlati"]
              and r["fattori"]["regioni"] == obj["regioni"])
    elif fam == "lei":
        ok = r["lei"] == {k: v for k, v in obj.items() if not k.startswith("_")}
    elif fam == "istituzioni":
        ok = r["istituzioni"] == {"cik": obj["cik"], "cef": obj["cef"]}
    else:
        ok = ({str(k): v for k, v in r["correzioni"].items()} == obj["correzioni"]
              and {str(k): v for k, v in r["intatti"].items()} == obj["intatti"])
    return "OK riconciliato" if ok else "KO: le voci rilette NON coincidono con quelle costruite"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--da", default="HEAD", help="revisione git da cui leggere le costanti")
    ap.add_argument("--apply", action="store_true", help="scrive i negozi (default: dry-run)")
    ap.add_argument("--sovrascrivi", action="store_true", help="riscrive un negozio gia' presente")
    ap.add_argument("--deve-contenere", action="append", default=[], metavar="ID=parola")
    ap.add_argument("--intatto", action="append", default=[], metavar="ID=parola")
    ap.add_argument("--solo", action="append", default=[], choices=sorted(NEGOZI),
                    help="limita alle famiglie indicate (ripetibile)")
    a = ap.parse_args(argv)
    negozi = costruisci(a.da, _coppie(a.deve_contenere, "--deve-contenere"), _coppie(a.intatto, "--intatto"))
    esito = 0
    for fam, obj in negozi.items():
        if a.solo and fam not in a.solo:
            continue
        nome = NEGOZI[fam][0]
        path = os.path.join(np_.DATA_DIR, nome)
        stato = "esiste" if os.path.exists(path) else "manca"
        print("%-12s %-30s %-7s %s" % (fam, nome, stato, conteggi(fam, obj)))
        if not a.apply:
            continue
        if stato == "esiste" and not a.sovrascrivi:
            print("             non sovrascrivo senza --sovrascrivi")
            esito = 1
            continue
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        verdetto = riconcilia(fam, obj, path)
        print("             scritto: %s" % verdetto)
        if not verdetto.startswith("OK"):
            esito = 1
    if not a.apply:
        print("dry-run: nessun file scritto (--apply per scrivere)")
    return esito


if __name__ == "__main__":
    sys.exit(main())
