"""Un prompt non descrive un comportamento inventato (05/09, Fable 5.1, lotto (a) dei desk).

Le dieci righe curate nel lotto (a) hanno sostituito i simboli del book con REGOLE che
rimandano a cio' che i tool restituiscono davvero: una stringa d'errore, l'etichetta di un
bucket, il nome di una industry. Il rischio nuovo e' la deriva: il giorno in cui qualcuno
cambia il messaggio d'errore o l'etichetta nel codice, il prompt continua a citare la forma
vecchia e il modello riceve un'istruzione che non puo' eseguire (classe I-1, «fili rotti»).
Il precedente misurato dallo scettico del 04/09: una prima proposta per options.py:54 citava
un errore "US only" che NON ESISTE in nessun tool — e stava per entrare in un prompt la cui
regola e' «MAI inventare».

Questi test legano ogni citazione del prompt alla sua fonte nel codice. Zero rete, zero DB,
zero LLM: si leggono stringhe e tabelle. Nessun simbolo del book compare qui: si asseriscono
FORME (prefissi, chiavi, messaggi), mai i valori del portafoglio.
"""
import os
import re

import pytest
from bellomberg.core import mandato_pm

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sorgente(nome):
    with open(os.path.join(RADICE, nome), encoding="utf-8") as fh:
        return fh.read()


def _desk(nome):
    from bellomberg.agents.specialists import ALL_SPECIALISTS
    for cls in ALL_SPECIALISTS:
        if getattr(cls, "name", "") == nome:
            return cls
    raise AssertionError("desk %r non trovato in ALL_SPECIALISTS" % nome)


# --------------------------------------------------------------------------
# 1. options: gli errori citati per riconoscere «senza chain US» esistono nei tool
# --------------------------------------------------------------------------

def test_options_cita_solo_errori_che_i_tool_producono_davvero():
    """La riga di Round 0 dice al desk quali messaggi CONFERMANO la classe «senza chain
    US». Ogni messaggio citato fra virgolette in quella riga deve essere prodotto da un
    tool del repo: polygon_data.py (chain/expirations) o agent_tools.py (get_options_data)."""
    prompt = _desk("options").system_prompt
    riga = next(r for r in prompt.splitlines() if "SENZA chain US" in r)
    citati = re.findall(r'"([^"]+)"', riga.split("CONFERMA")[0])
    assert citati, "la riga non cita nessun messaggio d'errore: la regola e' cieca"
    fonti = (_sorgente("src/bellomberg/market_data/polygon_data.py")
             + _sorgente("src/bellomberg/agents/agent_tools.py"))
    for msg in citati:
        assert msg in fonti, (
            "il prompt di options cita l'errore %r ma nessun tool lo produce: "
            "comportamento inventato (regola «MAI inventare» dello stesso prompt)" % msg)


# --------------------------------------------------------------------------
# 2. fundamentals: i prefissi dei bucket citati sono etichette VERE di get_sector_exposure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("prefisso", ["Fondo chiuso", "Crypto treasury"])
def test_fundamentals_cita_prefissi_di_bucket_esistenti(prefisso):
    """Il punto 4 di Round 0 ordina di individuare CEF e DAT dal bucket di by_sector il cui
    nome inizia con un prefisso. Il prefisso deve comparire nel prompt E corrispondere ad
    almeno un'etichetta `settore_tema` del negozio dei veicoli (05/09, lotto 2b: le etichette
    di esposizione vivono li', non nel codice): il contratto pubblico e' `veicoli.example.json`
    (tracciato); se il negozio PRIVATO e' leggibile, anche le SUE etichette devono portare il
    prefisso, altrimenti la regola del prompt non matcha nessun bucket sul book, in silenzio."""
    import bellomberg.storage.classificazione as cl
    from bellomberg.portfolio import portfolio_sectors
    prompt = _desk("fundamentals").system_prompt
    assert ('"%s' % prefisso) in prompt, "il prompt non cita piu' il prefisso %r" % prefisso
    esempio = cl.carica_veicoli(cl.ESEMPIO_VEICOLI)
    assert esempio["motivo"] is None, esempio
    etichette = list(portfolio_sectors.settori_tema_del_negozio(esempio).values())
    assert any(e.startswith(prefisso) for e in etichette), (
        "nessun settore_tema di veicoli.example.json inizia con %r: la regola del prompt "
        "non puo' matchare nessun bucket" % prefisso)
    # sul negozio PRIVATO la regola e' per TIPO (review 05/09: un book senza fondi chiusi e'
    # uno stato legittimo — il prompt stesso dice «se nessun bucket cosi' compare, DICHIARALO»):
    # ogni voce di quel tipo deve portare un settore_tema col prefisso, altrimenti il prompt non
    # la trova mai
    privato = cl.carica_veicoli()
    if privato["motivo"] is None:
        tipo = {"Fondo chiuso": "cef", "Crypto treasury": "dat"}[prefisso]
        for t, v in privato["veicoli"].items():
            if v["tipo"] == tipo:
                assert (v["settore_tema"] or "").startswith(prefisso), (
                    "una voce di tipo %s del negozio privato ha settore_tema %r senza il prefisso %r: "
                    "la regola del prompt non la trova" % (tipo, v["settore_tema"], prefisso))


# --------------------------------------------------------------------------
# 3. fundamentals: l'industry citata nel playbook e' una chiave della tassonomia
# --------------------------------------------------------------------------

def test_fundamentals_industry_oil_services_e_una_chiave_della_tassonomia():
    """La riga OIL SERVICES ancora il settore alla stringa `industry` che get_fundamentals
    restituisce. Quella stringa deve essere (senza maiuscole) una chiave dei profili di
    sector_taxonomy: e' la stessa che il motore usa per instradare, cosi' prompt e motore
    parlano della stessa classe."""
    from bellomberg.market_data import sector_taxonomy
    prompt = _desk("fundamentals").system_prompt
    riga = next(r for r in prompt.splitlines() if r.startswith("- OIL SERVICES"))
    m = re.search(r'industry "([^"]+)"', riga)
    assert m, "la riga OIL SERVICES non cita piu' una industry fra virgolette"
    chiavi = {k.lower() for k in _profili(sector_taxonomy)}
    assert m.group(1).lower() in chiavi, (
        "il prompt cita l'industry %r ma la tassonomia non ha quella chiave" % m.group(1))


def _profili(modulo):
    """La tabella dei profili di sotto-settore, qualunque nome porti: si cerca il dict di
    modulo le cui chiavi contengono la chiave nota «oil & gas e&p» (presente dal V4)."""
    for nome in dir(modulo):
        val = getattr(modulo, nome)
        if isinstance(val, dict) and "oil & gas e&p" in {str(k).lower() for k in val}:
            return val
    raise AssertionError("tabella dei profili di sotto-settore non trovata in sector_taxonomy")


# --------------------------------------------------------------------------
# 4. quant: il tool nuovo ordinato in Round 0 e' dichiarato anche in tools_used
# --------------------------------------------------------------------------

def test_quant_dichiara_get_sector_exposure_dove_lo_ordina():
    """La caccia ai cluster ordina get_sector_exposure. Il subset VIVO lo ha (lo prova la
    guardia di test_i1_fili_rotti); qui si pretende che anche `tools_used`, il ripiego del
    ramo degradato di specialists/base.py, lo dichiari: un tool ordinato in Round 0 e
    assente dalla lista del desk e' documentazione che mente."""
    quant = _desk("quant")
    assert "get_sector_exposure" in quant.system_prompt
    assert "get_sector_exposure" in quant.tools_used


def test_i_tre_desk_portano_segnaposto_del_mandato_senza_preferenze_storiche():
    options = _desk("options").system_prompt
    macro = _desk("macro").system_prompt
    fundamentals = _desk("fundamentals").system_prompt

    assert "{MANDATO:intestazione}" in options
    assert "{MANDATO:opzioni}" in options
    assert "SOLO 2 TIPI DI TRADE" not in options
    for prompt in (macro, fundamentals):
        assert "{MANDATO:intestazione}" in prompt
        assert "{MANDATO:caccia}" in prompt
        assert "MANDATO GLOBALE (15/07" not in prompt
        assert "Col book concentrato (oggi:" not in prompt


def test_options_a_b_cambia_strategie_ma_non_la_dottrina_iv_hv_e_catalyst():
    template = _desk("options").system_prompt
    a = mandato_pm.profilo_esempio()
    b = mandato_pm.profilo_esempio()
    b["opzioni"].update({
        "opzioni_abilitate": True,
        "strumenti_ammessi": ["short_premium_nudo", "straddle_strangle"],
        "budget_premio_pct": 3,
    })

    pa = mandato_pm.compila(template, a)
    pb = mandato_pm.compila(template, b)

    assert "NON usa opzioni" in pa
    assert "VENDITA DI PREMIO NUDA" in pb
    assert "STRADDLE/STRANGLE" in pb
    for testo in (pa, pb):
        assert "IV ATM vs vol realizzata/forecast" in testo
        assert "CATALYST" in testo
        assert "{MANDATO:" not in testo
