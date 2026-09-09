# -*- coding: utf-8 -*-
"""`ask_specialist("red_team")` risponde DAVVERO (voce B, 25/08, Fable 5).

Misurato sul cablaggio vero prima della cura (Blackboard + _execute_meta_tool,
zero rete): i desk vedono la chiave `red_team` nel summary — e' la PRIMA voce
del dict, whitelist esplicita di `summary_for_specialist` — la chiedono, e il
tool risponde «Not yet available - they have not produced output in any round
yet» mentre a registro ci sono 689 char di critica sotto la chiave grezza
`_red_team`. Chi in R2 vuole replicare alle obiezioni non le puo' rileggere.

E il preambolo dichiarava la voce «l'unica NON richiedibile con
ask_specialist»: era vero, e da oggi sarebbe una bugia — le dichiarazioni
vanno flippate INSIEME al codice (lezione: cambiare un limite invalida cio'
che lo citava).
"""
import pytest

import bellomberg.agents.specialists.base as sbase
from bellomberg.agents.specialists.base import Blackboard, Specialist

CRITICA = "CRITICA DEL RED TEAM: il sizing di TCK1 non regge lo stress test. " * 40


class _Desk(Specialist):
    """Specialista minimo (idioma di test_blackboard_fra_desk): serve il
    meta-tool e il preambolo, non lo scoring."""
    name = "quant"
    system_prompt = "test"
    tools_used = []

    def compute_score(self):
        return None


def _bb(rt=None):
    b = Blackboard(memory_db=None, memo_id=None)
    b.data["macro"] = {1: "report macro"}
    if rt is not None:
        b.data["_red_team"] = rt
    return b


def _ask(bb):
    return _Desk(bb, client=object())._execute_meta_tool(
        "ask_specialist", {"specialist": "red_team", "question": "dettaglio?"})


def test_ask_specialist_red_team_consegna_la_critica():
    """Il caso della misura PRE: la critica esiste, il tool la consegna."""
    r = _ask(_bb({1: CRITICA}))
    assert r.get("report") == CRITICA
    assert r.get("round") == 1
    assert "Not yet available" not in str(r)


def test_ask_specialist_red_team_consegna_l_ultimo_round():
    r = _ask(_bb({1: "critica vecchia del round 1", 2: CRITICA}))
    assert r.get("report") == CRITICA
    assert r.get("round") == 2


def test_red_team_assente_ha_la_nota_DEDICATA_non_il_generico():
    """Senza `_red_team` il generico «Not yet available - they have not
    produced output in any round YET» suggerisce un ritardo (arrivera'): per
    il red team e' fuorviante — se non e' girato non arriva piu', e l'assenza
    di critica non valida niente (stessa verita' della voce A per il Capo)."""
    r = _ask(_bb(None))
    nota = str(r.get("note", ""))
    assert "Not yet available" not in nota
    assert "red team" in nota.lower()
    assert "do not infer" in nota.lower() and "validated" in nota.lower()


def test_get_latest_risolve_l_alias_pubblico():
    """`red_team` e' il nome che i lettori CONOSCONO (summary) e `_red_team`
    quello a registro: get_latest risolve l'alias, la chiave grezza resta."""
    bb = _bb({1: CRITICA})
    via_alias = bb.get_latest("red_team")
    via_chiave = bb.get_latest("_red_team")
    assert via_alias and via_alias["report"] == CRITICA
    assert via_chiave and via_chiave["report"] == CRITICA
    assert _bb(None).get_latest("red_team") is None


def test_il_taglio_di_red_team_offre_la_via_che_ORA_esiste():
    """Quando il report del red team esce TAGLIATO dalla finestra, la riga
    per-desk diceva «l'unica voce NON richiedibile ... non e' recuperabile»:
    ora la via c'e', e va offerta come per gli altri desk — col suo nome."""
    blocco = sbase._blocco_blackboard(
        {"red_team": {"round": 1, "report": CRITICA},
         "macro": {"round": 1, "report": "report macro. " * 200}},
        budget=1000)
    assert 'ask_specialist("red_team"' in blocco
    assert "NON richiedibile" not in blocco
    assert "Not yet available" not in blocco
    assert "fa eccezione" not in blocco     # la testa non deve piu' eccepire


def test_r2_non_incornicia_il_segnaposto_come_attacco():
    """Review 25/08 (C1): con la voce A a registro puo' esserci un «vuoto
    dichiarato» (segnaposto/rifiuto). Il preambolo R2 diceva comunque «A RED
    TEAM attacked the theses ... REPLY»: i desk replicavano a obiezioni mai
    scritte, e le loro repliche facevano dichiarare al Capo (via la frase
    condizionale della voce A) un contraddittorio mai avvenuto — la catena
    che DISFA la cura appena fatta."""
    from bellomberg.agents.red_team import SEGNAPOSTO_NESSUNA_CRITICA
    bb = _bb({1: SEGNAPOSTO_NESSUNA_CRITICA})
    p = _Desk(bb, client=object())._build_round_context(2)
    assert "A RED TEAM attacked" not in p
    assert "WITHOUT producing a usable critique" in p
    assert "do not invent" in p and "validated" in p


def test_r2_con_critica_vera_tiene_la_cornice_di_attacco():
    bb = _bb({1: CRITICA})
    p = _Desk(bb, client=object())._build_round_context(2)
    assert "A RED TEAM attacked" in p
    assert "WITHOUT producing a usable critique" not in p


def test_ask_specialist_consegna_il_segnaposto_CON_la_note():
    """Review 25/08 (I2): il registro si consegna sempre com'e', ma senza una
    `note` il tool_result con `round` e `report` e' una conferma di forma che
    una critica esiste — contro il contenuto del testo stesso."""
    from bellomberg.agents.red_team import SEGNAPOSTO_NESSUNA_CRITICA
    r = _ask(_bb({1: SEGNAPOSTO_NESSUNA_CRITICA}))
    assert r.get("report") == SEGNAPOSTO_NESSUNA_CRITICA   # mai nascondere il registro
    nota = str(r.get("note", ""))
    assert "NOT a usable critique" in nota
    assert "do not infer" in nota.lower() and "validated" in nota.lower()


def test_ask_specialist_su_critica_vera_non_porta_note():
    r = _ask(_bb({1: CRITICA}))
    assert r.get("report") == CRITICA and "note" not in r


def test_il_ramo_senza_recupero_resta_onesto_anche_per_red_team():
    """Il RED TEAM come LETTORE non ha ask_specialist (3 tool read-only): il
    ramo `recupero=RECUPERO_NESSUNO` non deve promettere il tool a nessuno,
    nemmeno adesso che la voce `red_team` e' richiedibile DAI DESK."""
    blocco = sbase._blocco_blackboard(
        {"macro": {"round": 1, "report": "report macro. " * 200}},
        budget=500, recupero=sbase.RECUPERO_NESSUNO)
    assert "ask_specialist" not in blocco
