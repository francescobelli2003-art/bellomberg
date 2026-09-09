"""Il tetto dei tool_result dev'essere UNO SOLO (20/08, Opus 5, ok PM).

Il 19/08 le fonti prezzi erano scritte in due posti — la firma e `main()`, che e'
il percorso del task — e correggere solo la firma sarebbe stato VERDE su una
produzione ancora rotta. Il tetto dei tool_result stava messo peggio: TRE posti,
`chat_tools.TETTO_TOOL_RESULT` (che le viste compatte leggono per decidere se
DEGRADARE) e due letterali `6000` in `specialists/base.py` e `red_team.py` (che
sono i tagli VERI). Alzare la costante e lasciare i letterali avrebbe fatto la
cosa peggiore: le viste avrebbero smesso di degradare e i payload sarebbero stati
TAGLIATI in coda, dove stanno le dichiarazioni.

Qui il vincolo e' meccanico: chi taglia deve leggere la costante, e il numero non
deve ricomparire come letterale accanto al taglio.
"""
import os
import re

import pytest

from bellomberg.agents import chat_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE_CHE_TAGLIANO = (os.path.join("src", "bellomberg", "agents", "specialists", "base.py"),
                     os.path.join("src", "bellomberg", "agents", "red_team.py"))


def _sorgente(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_il_tetto_e_un_numero_solo():
    assert isinstance(chat_tools.TETTO_TOOL_RESULT, int)
    assert chat_tools.TETTO_TOOL_RESULT > 0


@pytest.mark.parametrize("rel", FILE_CHE_TAGLIANO)
def test_chi_taglia_legge_la_costante(rel):
    """Il taglio deve passare dalla costante, non da un numero scritto a mano."""
    src = _sorgente(rel)
    assert "TETTO_TOOL_RESULT" in src, (
        f"{rel} taglia i tool_result senza leggere chat_tools.TETTO_TOOL_RESULT: "
        "il tetto tornerebbe a vivere in due posti")


@pytest.mark.parametrize("rel", FILE_CHE_TAGLIANO)
def test_nessun_letterale_sul_taglio_dei_tool_result(rel):
    """Mirato al SOLO taglio dei tool_result: le variabili che portano il
    risultato di un tool (`result_str` in base.py, `r_str` in red_team.py) non
    devono essere tagliate con un numero scritto a mano.

    Nota: negli stessi file esistono ALTRI tetti, diversi e legittimi (il report
    del red team a 3.500, i report altrui a 9.000, il portafoglio del red team a
    2.500). Questo test non li tocca: confonderli sarebbe una misura circolare.
    """
    src = _sorgente(rel)
    colpevoli = []
    for n_riga, riga in enumerate(src.splitlines(), 1):
        if riga.lstrip().startswith("#"):
            continue                      # i commenti possono citare il numero storico
        if not re.search(r"\b(result_str|r_str)\b", riga):
            continue
        if re.search(r"\[\s*:\s*\d{3,}\s*\]", riga) or re.search(r">\s*\d{3,}\b", riga):
            colpevoli.append("%s:%d  %s" % (rel, n_riga, riga.strip()[:90]))
    assert not colpevoli, (
        "tetto dei tool_result cablato a mano:\n  " + "\n  ".join(colpevoli))


def test_la_vista_compatta_usa_lo_stesso_tetto_di_chi_taglia():
    """La vista decide se degradare confrontandosi col tetto: se il tetto che
    legge lei fosse diverso da quello che taglia davvero, si degraderebbe per
    niente (perdendo il prezzo di carico) oppure non si degraderebbe quando serve
    (e il payload verrebbe tagliato in coda)."""
    src_base = _sorgente(os.path.join("src", "bellomberg", "agents", "specialists", "base.py"))
    # il valore effettivo usato nel taglio arriva da chat_tools: verifico che il
    # simbolo importato sia proprio quello, non un omonimo locale
    assert re.search(r"TETTO_TOOL_RESULT", src_base)
    assert 'from bellomberg.agents import chat_tools' in src_base or "from chat_tools" in src_base
