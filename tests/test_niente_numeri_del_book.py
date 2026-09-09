"""B9 (02/09, pubblicazione, Fable 5): i numeri VERI del book del PM non stanno nel
codice che va pubblico — ne' nelle docstring, ne' nei commenti, ne' nelle fixture.

QUESTA BATTERIA E' PUBBLICA, quindi NON contiene le cifre vere (la prima stesura le
elencava in chiaro come «stringhe vietate»: la review del 02/09 l'ha bocciata, era
il leak travestito da guardia). Qui si misurano PROPRIETA' che le cifre vere non
hanno e quelle finte si':
  1. la fixture `LIBRO_VERO` di `tests/test_edge_scan_copre_il_book.py` e' FINTA
     per costruzione: 28 voci, strettamente decrescente, ogni valore multiplo di
     50, totale fisso — nessun valore di mercato vero cade su tutte e tre;
  2. il lotto di `tests/test_tesi_posizioni.py` e' quello finto dichiarato, e
     nessun prezzo nel file ha piu' di 4 decimali (un prezzo a 12 decimali e' un
     numero copiato dal DB, non un esempio);
  3. nei file censiti non compaiono importi con separatore delle migliaia seguiti
     da €/EUR, percentuali FIRMATE a due decimali (la forma dei rendimenti del
     portafoglio) ne' valori della quota base 100 con i decimali.

E' una tenda, non una serratura: la lista ESATTA delle cifre vive fuori dal
perimetro pubblico in `prova_numeri_del_book.py` (radice, escluso dall'export) e
dal cancello di P2. Ammessi per decisione PM 02/09: conteggi di posizioni, nomi dei
ticker, fatti pubblici di societa' quotate, conteggi di caratteri, esempi di formato
dichiarati in `ESEMPI_DI_FORMATO`.

RED misurato contro le versioni di HEAD (02/09) copiate in una cartella temporanea
via `NIENTE_NUMERI_REPO=<cartella>`: le tre proprieta' cadevano tutte.
"""
import os
import re

REPO = os.environ.get("NIENTE_NUMERI_REPO") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

FILE_CENSITI = [
    "tests/test_edge_scan_copre_il_book.py",
    "tests/test_tesi_posizioni.py",
    'src/bellomberg/storage/memory_db.py',
    "app/src/pages/Dashboard.tsx",
    "app/src/pages/MovementsPage.tsx",
    "app/src/pages/dashboard-command.css",
    "app/src/lib/quota.ts",
    "app/src/lib/curva.ts",
]

# esempi di formato dichiarati (non coincidono con nessun movimento vero)
ESEMPI_DI_FORMATO = {"12.500 €"}

# 03/09: la fixture di test_edge_scan_copre_il_book.py e' stata rifatta INVENTATA
# (nomi e forma): 28 -> 25 voci e 102.000 -> 96.500 di totale. Il 28 di prima era
# la dimensione VERA del book, cablata qui dentro in un test pubblico.
FIXTURE_VOCI = 25
FIXTURE_PASSO = 50
FIXTURE_TOTALE = 96500
LOTTO_FINTO = "quantita=50, prezzo_medio=4321.5"

IMPORTO_EUR = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?\s?(?:€|EUR(?![A-Za-z]))")
PCT_FIRMATA = re.compile(r"[+\-−]\d{1,3},\d{2}\s?%")
QUOTA_BASE_100 = re.compile(r"(?<![\d,.])1[01]\d,\d{2,4}(?!\d)")
PREZZO_COPIATO = re.compile(r"prezzo_medio=\d+\.\d{5,}")
VALORE_MERCATO = re.compile(r'"valore_mercato":\s*([0-9.]+)')


def _testo(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _fixture_libro():
    t = _testo("tests/test_edge_scan_copre_il_book.py")
    inizio = t.index("LIBRO_VERO = [")
    blocco = t[inizio:t.index("\n]", inizio)]
    return [(tk, float(v)) for tk, v in re.findall(r'\("([^"]+)",\s*([0-9.]+)\)', blocco)]


def test_i_file_censiti_esistono_ancora():
    """Se un file sparisce o cambia nome, il test non deve passare per vacuita'."""
    mancanti = [f for f in FILE_CENSITI if not os.path.isfile(os.path.join(REPO, f))]
    assert not mancanti, "file censiti non trovati: %r" % mancanti


def test_la_fixture_del_libro_e_finta_per_costruzione():
    libro = _fixture_libro()
    valori = [v for _, v in libro]
    assert len(libro) == FIXTURE_VOCI, "la fixture ha %d voci, non %d" % (len(libro), FIXTURE_VOCI)
    assert valori == sorted(valori, reverse=True) and len(set(valori)) == len(valori), (
        "la fixture non e' strettamente decrescente")
    non_tondi = [(tk, v) for tk, v in libro if v % FIXTURE_PASSO != 0]
    assert not non_tondi, "valori non multipli di %d (cifre vere?): %r" % (FIXTURE_PASSO, non_tondi)
    assert sum(valori) == FIXTURE_TOTALE, "totale %s != %s" % (sum(valori), FIXTURE_TOTALE)
    # anche le copie parziali sparse nel file usano i valori finti (o valori-giocattolo)
    sparsi = [float(v) for v in VALORE_MERCATO.findall(_testo("tests/test_edge_scan_copre_il_book.py"))]
    sospetti = [v for v in sparsi if v > 10 and v % FIXTURE_PASSO != 0]
    assert not sospetti, "valore_mercato non tondi fuori dalla fixture: %r" % sospetti


def test_il_lotto_di_prova_delle_tesi_e_finto():
    t = _testo("tests/test_tesi_posizioni.py")
    assert LOTTO_FINTO in t, "il lotto finto dichiarato non c'e' piu'"
    copiati = PREZZO_COPIATO.findall(t)
    assert not copiati, "prezzo con 5+ decimali = copiato dal DB: %r" % copiati


def test_niente_importi_rendimenti_o_quote_nei_file_censiti():
    colpe = []
    for rel in FILE_CENSITI:
        t = _testo(rel)
        for m in IMPORTO_EUR.findall(t):
            if m not in ESEMPI_DI_FORMATO:
                colpe.append("%s: importo %r" % (rel, m))
        colpe += ["%s: rendimento firmato %r" % (rel, m) for m in PCT_FIRMATA.findall(t)]
        colpe += ["%s: valore quota %r" % (rel, m) for m in QUOTA_BASE_100.findall(t)]
    assert not colpe, "cifre della classe vietata nei file censiti:\n" + "\n".join(colpe)
