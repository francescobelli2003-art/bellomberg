# -*- coding: utf-8 -*-
"""I desk devono LEGGERSI fra loro in R1/R2 (21/08, Opus 5, audit/25 finding C).

MISURATO sulla run vera (memo #50) prima di toccare qualsiasi cosa:

    il preambolo prometteva "Other specialists' latest reports are on the
    blackboard below" e consegnava `json.dumps(others, indent=2)[:9000]` — MUTO.
    Il JSON vero pesa 57.084-67.312 char: passava il 13-16%.

    richiedente     JSON vero   chi sopravviveva ai 9.000
    quant              67.312   red_team, macro
    options            66.539   red_team, macro
    macro              66.308   red_team, eventdesk
    crypto             65.705   red_team, macro
    eventdesk          63.355   red_team, macro
    fundamentals       57.084   red_team, macro

FUNDAMENTALS (19.031 char), QUANT e OPTIONS non comparivano MAI nel preambolo di
nessuno — nemmeno col nome della chiave. Sono i tre che decidono nomi, size e
strutture, e gli unici che replicano in R2: la barriera di pipeline costruita il
15/07 ("Quant valida i numeri dei candidati NELLO STESSO round", "Options
struttura SOLO sui candidati gia' validati da Quant") esisteva nel codice e NON
nel prompt.

E chi sopravviveva lo decideva l'ordine in cui i desk avevano FINITO il round 0
(le chiavi nascono in Blackboard.write), con 4 worker in parallelo: un dettaglio
di scheduling decideva cosa legge chi alloca i soldi.

Il red team da solo si prendeva il 43% della finestra.

Scelta del PM 21/08 sulla rosa (i)/(ii): **(i) PIENO** — i report arrivano interi,
+496.238 char per run (stima per ECCESSO: in R1 la blackboard porta i R0,
piu' corti dei R2 usati per la misura). Il costo VERO e' 1,94-2,17 EUR a run,
il 17-19% di una run da 11,23: il blocco sta nel primo messaggio user, che
viene rispedito a ogni giro del tool-loop (moltiplicatore di cache 3,40-3,80x).
Il primo numero dato al PM, 0,57 EUR, era sbagliato di 3,5x — v. base.py.
"""
import json
import os
import re

import pytest

import bellomberg.agents.specialists.base as sbase
from bellomberg.agents.specialists.base import Blackboard, Specialist


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# taglie VERE dei report del memo #50, cosi' il test misura il caso di produzione
# e non un corpus comodo (lezione "misure circolari")
TAGLIE = {
    "macro": 9929, "eventdesk": 12918, "crypto": 10517,
    "fundamentals": 19031, "quant": 9057, "options": 9768,
}
FIRMA = "FIRMA-%s-FINE"    # messa in CODA: e' la parte che sparisce per prima


def _report(nome):
    """Lungo ESATTAMENTE come il report vero: se la fixture e' piu' corta, il test
    misura un caso che in produzione non esiste (la prima stesura generava 9.600
    char per un report che ne pesa 19.031, e il taglio non scattava mai)."""
    firma = FIRMA % nome.upper()
    utile = TAGLIE[nome] - len(firma)
    corpo = ("Report di %s. " % nome) * (utile // len("Report di %s. " % nome) + 2)
    testo = corpo[:utile] + firma
    assert len(testo) == TAGLIE[nome]
    return testo


class _Desk(Specialist):
    """Specialista minimo: serve il preambolo, non lo scoring (che farebbe rete)."""
    name = "quant"
    system_prompt = "test"
    tools_used = []

    def compute_score(self):
        return None


@pytest.fixture
def bb():
    b = Blackboard(memory_db=None, memo_id=None)
    for nome in TAGLIE:
        b.data[nome] = {1: _report(nome)}
    b.data["_red_team"] = {1: "Critica del red team. " * 240}
    return b


def _preambolo(bb, nome="quant", round_n=1):
    d = _Desk(bb, client=object())
    d.name = nome
    return d._build_round_context(round_n)


# ----------------------------------------------------------------- il difetto

def test_ogni_desk_compare_nel_preambolo_di_ogni_altro(bb):
    """Il caso vero: fundamentals/quant/options non comparivano MAI."""
    for chi in TAGLIE:
        p = _preambolo(bb, chi)
        for altro in TAGLIE:
            if altro == chi:
                continue
            assert altro in p, (
                "%s non vede nemmeno il NOME di %s nel proprio preambolo" % (chi, altro))


def test_i_report_arrivano_INTERI_verificati_dalla_coda(bb):
    """(i) PIENO, scelta del PM: si verifica la FIRMA in coda, non l'inizio."""
    p = _preambolo(bb, "quant")
    for altro in TAGLIE:
        if altro == "quant":
            continue
        assert (FIRMA % altro.upper()) in p, (
            "di %s arriva solo l'inizio: la coda e' dove stanno le conclusioni" % altro)


def test_l_ordine_di_arrivo_non_decide_chi_si_vede(bb):
    """Chi finiva prima il round 0 decideva chi era visibile: 4 worker in
    parallelo, quindi una corsa fra thread sceglieva cosa legge chi alloca.

    ⚠️ La prima stesura di questo test era VERDE anche sul codice pre-cura, perche'
    confrontava due insiemi VUOTI: cercava la FIRMA in coda, che col vecchio
    `[:9000]` non sopravvive in NESSUNO dei due ordini. Un test il cui nome e' una
    garanzia deve misurare qualcosa: l'ha trovato la review avversariale del 21/08.
    Ora c'e' l'assert di non-vacuita', ed e' quello che lo rende un test."""
    visti = []
    for ordine in (list(TAGLIE), list(reversed(list(TAGLIE)))):
        b = Blackboard(memory_db=None, memo_id=None)
        for nome in ordine:
            b.data[nome] = {1: _report(nome)}
        p = _preambolo(b, "quant")
        visti.append({n for n in TAGLIE if n != "quant" and (FIRMA % n.upper()) in p})
    assert visti[0], (
        "nessun desk visibile in nessuno dei due ordini: il confronto sarebbe fra "
        "due insiemi vuoti e il test non misurerebbe nulla")
    assert len(visti[0]) == len(TAGLIE) - 1, (
        "non tutti i desk sono leggibili: %s" % visti[0])
    assert visti[0] == visti[1], (
        "l'insieme dei desk leggibili cambia con l'ordine di arrivo: %s vs %s"
        % (visti[0], visti[1]))


def test_vale_anche_in_R2_dove_si_decidono_size_e_strutture(bb):
    p = _preambolo(bb, "options", round_n=2)
    assert (FIRMA % "QUANT") in p, (
        "Options struttura SOLO sui candidati gia' validati da Quant "
        "(consigliere_multi.py) e non vedeva il verdetto R2 di Quant")
    assert (FIRMA % "FUNDAMENTALS") in p


# ----------------------------------------------------------------- se taglia, lo dice

def test_se_taglia_DICHIARA_per_desk_coi_numeri_e_come_recuperare(bb, monkeypatch):
    """Un tetto puo' esistere. Muto no: senza dichiarazione il modello non ha
    nemmeno il SEGNALE che gli manca qualcosa.

    Nota: `assert "ask_specialist" in p` da solo NON puo' fallire in R1 — quella
    stringa e' gia' nel preambolo statico ("Use ask_specialist for targeted
    questions"). Qui si asserisce la riga di recupero PER DESK."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    p = _preambolo(bb, "quant")
    assert "19031" in p or "19.031" in p, (
        "deve dichiarare la lunghezza VERA del report piu' lungo (fundamentals), "
        "non solo che c'e' stato un taglio")
    assert "NE LEGGI" in p, "manca la dichiarazione per desk"
    assert 'ask_specialist("fundamentals"' in p, (
        "la riga di recupero deve nominare IL DESK, non parlare in generale")


def test_la_via_di_recupero_non_promette_un_INTERO_che_non_puo_dare(bb, monkeypatch):
    """La prima stesura di questa cura scriveva in tre punti che ask_specialist
    «restituisce il report INTERO» / «non taglia». FALSO: anche quel risultato
    passa dal tetto dei tool_result (12.000), e su fundamentals (19.031 char) ne
    arriva il 62%. E' la stessa malattia appena curata su read_blackboard,
    reintrodotta sul tool a cui la cura manda il traffico."""
    from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    p = _preambolo(bb, "quant")
    basso = p.lower()
    assert "report intero" not in basso and "non taglia" not in basso, (
        "il preambolo promette un INTERO che ask_specialist non puo' dare")
    assert str(TETTO_TOOL_RESULT) in p, (
        "se si indirizza a un tool che a sua volta taglia, il preambolo deve dire "
        "a quale tetto")


def test_read_blackboard_non_fa_sparire_i_desk(bb):
    """read_blackboard passa dal tetto tool_result: prima ne uscivano due desk su
    sei e gli altri sparivano senza nome — lo stesso difetto del preambolo, sul
    tool. E togliere il cap 3.500 al red team lo aveva PEGGIORATO del 19%."""
    from bellomberg.agents.chat_tools import TETTO_TOOL_RESULT
    d = _Desk(bb, client=object())
    d.name = "quant"
    reso = d._execute_meta_tool("read_blackboard", {})
    # il taglio VERO avviene dopo, sulla serializzazione (base.py, ramo tool_result):
    # un test che si ferma al valore di ritorno non misura il difetto
    testo = json.dumps(reso, default=str, ensure_ascii=False)
    assert len(testo) <= TETTO_TOOL_RESULT, (
        "il risultato di read_blackboard (%d char) sfora il tetto dei tool_result "
        "(%d): verra' tagliato dallo slice cieco e i desk in coda spariranno senza "
        "nome — il budget della vista dev'essere scelto per starci dentro"
        % (len(testo), TETTO_TOOL_RESULT))
    for altro in TAGLIE:
        if altro == "quant":
            continue
        assert altro in testo.lower(), (
            "%s sparisce da read_blackboard senza nome" % altro)


def test_un_report_vuoto_non_viene_dichiarato_INTERO(bb):
    """«report INTERO, 0 caratteri» e' una frase che afferma il falso su un buco."""
    bb.data["crypto"] = {1: ""}
    p = _preambolo(bb, "quant")
    i = p.find("--- CRYPTO")
    assert i >= 0
    # solo la SUA intestazione: 160 caratteri secchi sconfinavano nella sezione
    # successiva, e il test leggeva l'"INTERO" del desk accanto
    riga = p[i:p.find("\n", i)]
    assert "INTERO" not in riga, (
        "un report vuoto viene annunciato come intero: %r" % riga)
    assert "NESSUN TESTO" in riga


def test_un_round_mancante_non_diventa_la_parola_None():
    """Sui report legacy il round puo' mancare: «Round None» e' un n.d.
    travestito da numero. Si chiama la funzione DIRETTAMENTE con la forma che
    la produzione puo' produrre."""
    b = sbase._blocco_blackboard({"politics": {"report": "testo legacy"}})
    assert "None" not in b, b[:200]
    assert "n.d." in b


def test_nessun_desk_sparisce_SENZA_NOME_quando_si_taglia(bb, monkeypatch):
    """Il difetto peggiore non era il taglio: era che quattro desk sparivano
    senza lasciare traccia, nemmeno la chiave."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 3000)
    p = _preambolo(bb, "quant")
    for altro in TAGLIE:
        if altro == "quant":
            continue
        assert altro in p, (
            "%s e' sparito del tutto: un desk assente e' indistinguibile da un "
            "desk che non ha scritto nulla" % altro)


def test_il_red_team_non_si_prende_la_finestra_degli_altri(bb, monkeypatch):
    """Con 9.000 char il red team ne occupava 3.853, il 43%, perche' sta in testa
    al dict. Sotto pressione la quota dev'essere DIVISA, non presa da chi arriva
    prima. La prima stesura calcolava una variabile e non la usava: asseriva solo
    la presenza dei nomi, che e' un'altra cosa. Qui si misurano le QUOTE."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    quote = {}
    for m in re.finditer(r"--- ([A-Z_]+) \(Round [^)]*\)[^-]*?NE LEGGI (\d+) SU (\d+)",
                         _preambolo(bb, "quant")):
        quote[m.group(1).lower()] = int(m.group(2))
    assert len(quote) >= 5, "attesi tutti i desk tagliati e dichiarati: %s" % quote
    rt = quote.get("red_team", 0)
    assert rt <= 12000 * 0.30, (
        "il red team si prende il %.0f%% della finestra: sopra il 30%% sta "
        "mangiando la quota dei desk" % (100.0 * rt / 12000))
    piu_piccola = min(quote.values())
    assert piu_piccola >= 12000 / (len(quote) * 3), (
        "riparto troppo sbilanciato: la quota piu' piccola e' %d su %s"
        % (piu_piccola, quote))


def test_la_critica_del_red_team_arriva_INTERA_ai_desk(bb):
    """Il cap a 3.500 sulla critica esisteva PER LA FINESTRA — lo dice il suo
    stesso marcatore, «TRONCATA A 3500 CHAR PER LA FINESTRA». Ora la finestra e'
    120.000 e quel taglio non ha piu' una ragione: sulla run vera la critica pesa
    5.122 char e ne perdeva il 32%, incluso il capitolo intitolato «I 3 RISCHI CHE
    IL CAPO NON DEVE IGNORARE». Quando cambi un limite, cerca cio' che si
    giustificava con quel limite."""
    coda = "ULTIMO-RISCHIO-DA-NON-IGNORARE"
    bb.data["_red_team"] = {1: ("Critica. " * 700) + coda}
    assert len(bb.data["_red_team"][1]) > 3500
    p = _preambolo(bb, "quant")
    assert coda in p, (
        "la coda della critica del red team non arriva ai desk: e' la parte dove "
        "stanno i rischi da non ignorare")


# ----------------------------------------------------- il numero in un posto solo

def test_il_tetto_e_UNA_costante_non_due_letterali():
    """Il 9.000 viveva in due righe, e il commento che lo descriveva ne citava un
    terzo (6.000, dal 15/07). Stessa malattia del tetto tool_result."""
    src = open(os.path.join(ROOT, "src", "bellomberg", "agents", "specialists", "base.py"),
               encoding="utf-8").read()
    # la forma di CODICE, non la parola: i commenti e i docstring che raccontano
    # il difetto storico devono poter citare il numero vecchio
    assert "indent=2)[:9000]" not in src, (
        "il tetto della blackboard e' ancora un letterale sullo slice del JSON")
    assert isinstance(sbase.MAX_CHAR_BLACKBOARD, int)
    assert sbase.MAX_CHAR_BLACKBOARD >= 67312, (
        "il preambolo piu' pesante misurato sulla run vera (memo #50, quant) "
        "pesa 67.312 char: sotto questo valore la scelta (i) PIENO del PM non "
        "e' rispettata e si torna a tagliare")


def test_read_blackboard_non_promette_un_full_che_non_puo_dare():
    """La description diceva «Read the full blackboard — all specialists' latest
    reports», ma il risultato passa dal tetto dei tool_result (12.000): non e'
    full. La legge l'agente per decidere se chiamarlo."""
    src = open(os.path.join(ROOT, "src", "bellomberg", "agents", "specialists", "base.py"),
               encoding="utf-8").read()
    i = src.find('"name": "read_blackboard"')
    assert i >= 0, "tool read_blackboard non trovato"
    j = src.find('"input_schema"', i)
    assert j > i
    # solo CODICE: il commento che cita la description vecchia non e' la description
    blocco = "\n".join(r for r in src[i:j].splitlines()
                       if not r.lstrip().startswith("#"))
    # puo' essere una stringa sola o piu' frammenti fra parentesi: si guarda tutto
    # il blocco, cosi' il test non dipende da come e' formattata
    desc = " ".join(re.findall(r'"([^"]*)"', blocco)).lower()
    assert "full blackboard" not in desc, (
        "la description promette il blackboard COMPLETO mentre il tool_result "
        "viene tagliato al tetto. Description: " + desc)
    assert "tagliat" in desc or "tronc" in desc, (
        "la description deve DIRE che il risultato puo' essere tagliato: e' la "
        "frase che l'agente legge per decidere se fidarsi di cio' che riceve")
