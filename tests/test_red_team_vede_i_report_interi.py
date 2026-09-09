# -*- coding: utf-8 -*-
"""Il red team deve ricevere i report dei desk INTERI (21/08, Opus 5, audit/25 finding D).

MISURATO sui report veri del memo #50 PRIMA di toccare qualsiasi cosa — non stimato:

    red_team.py:131 tagliava OGNI report a 5.000 char (`txt[:5000] + "...[tronco]"`).

        report -> red team     vero    passa    fuori
        eventdesk            12.918    5.000     61%
        fundamentals         12.029    5.000     58%
        quant                11.240    5.000     56%
        crypto               10.517    5.000     52%
        macro                 9.929    5.000     50%
        options               8.707    5.000     43%
        TOTALE               65.340  6x5.000   54,1%

⚠️ QUESTE SONO LE TAGLIE R1, ED È IL PUNTO. La prima stesura di questo test usava
i report FINALI (71.220 char, 57,9%) — ma il red team gira **TRA R1 e R2**
(`consigliere_multi.py:491`) e legge `max(rounds.keys())`, che in quel momento vale 1:
l'R2 di fundamentals (19.031) non esiste ancora, e quello che vede è il suo R1
(12.029). A DB i due si distinguono così: per i tre desk che replicano in R2
(fundamentals, quant, options) il `round_n=1` è il draft R1; per gli altri tre il
report R1 è già il finale ed è persistito come `round_n=2` (promozione,
`specialists/base.py`). Misurare sul corpus sbagliato è la lezione «misure
circolari»: si riproducono gli input VERI della funzione, non un'approssimazione
comoda — e qui l'approssimazione gonfiava il difetto del 9%.
Col corpus giusto il report più lungo è **eventdesk (12.918)**, non fundamentals.

Il taglio ERA dichiarato ("...[tronco]") — non e' il difetto muto — ma SENZA numeri,
e cade sistematicamente sulla parte PROPOSITIVA dei report: "Validazione candidati"
di Quant (offset 5.767: e' il gate che autorizza Options), "Le due tesi" di
Fundamentals (11.388), l'income leg di Options (6.554), il trade politico di
EventDesk.

⚠️ CORREZIONE DEL CONFUTATORE, da tenere: la put spread SPY 735/700 di Options STA
DENTRO il cap ed e' stata attaccata nel merito in produzione. NON si puo' dire che
"il red team lascia passare senza esame le proposte": il difetto e' che cio' che
cade e' sistematicamente propositivo, non che il red team non attacchi nulla.

E IL RED TEAM NON HA `ask_specialist`: ha 3 soli tool READ-ONLY sui numeri del
portafoglio (red_team.py:137). La via di recupero che `_blocco_blackboard` offre ai
desk, per lui NON ESISTE — indirizzarcelo sarebbe la lezione (iii) del 21/08
("se indirizzi a una via di recupero, ESEGUILA e conta i caratteri") ripetuta sul
consumatore successivo.

Scelta del PM 21/08 sulla rosa (a)/(b): **(a) FINESTRA PIENA** — stessa finestra e
stessa funzione dei desk (`MAX_CHAR_BLACKBOARD`), un numero solo.
"""
import json
import os
import re

import pytest

import bellomberg.agents.specialists.base as sbase
from bellomberg.agents.specialists.base import Blackboard


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# taglie VERE che il red team vede in produzione (memo #50, stato R1 — v. docstring):
# se la fixture e' piu' corta o presa dal round sbagliato, il test misura un caso che
# in produzione non esiste (lezione "misure circolari")
TAGLIE = {
    "macro": 9929, "options": 8707, "eventdesk": 12918,
    "fundamentals": 12029, "crypto": 10517, "quant": 11240,
}
PIU_LUNGO = "eventdesk"      # 12.918 char: il primo a cadere sotto un tetto
FIRMA = "FIRMA-%s-FINE"      # in CODA: e' la parte propositiva, la prima a sparire


def _report(nome):
    firma = FIRMA % nome.upper()
    utile = TAGLIE[nome] - len(firma)
    unita = "Report di %s. " % nome
    corpo = unita * (utile // len(unita) + 2)
    testo = corpo[:utile] + firma
    assert len(testo) == TAGLIE[nome]
    return testo


class _Catturato(BaseException):
    """Deriva da BaseException apposta: `run_red_team` e' tutto dentro un
    `except Exception` (best-effort, red_team.py:298) che inghiottirebbe una
    Exception normale e restituirebbe "" senza far vedere nulla al test."""

    def __init__(self, kwargs):
        super().__init__("prompt catturato")
        self.kwargs = kwargs


class _FintoMessages:
    def create(self, **kw):
        raise _Catturato(kw)


class _FintoClient:
    def __init__(self, *a, **kw):
        self.messages = _FintoMessages()


@pytest.fixture
def bb():
    b = Blackboard(memory_db=None, memo_id=None)
    # ordine di scrittura VOLUTAMENTE diverso dal roster: e' l'ordine di FINE del
    # round 0 (4 worker in parallelo), cioe' una corsa fra thread. Il prompt non
    # deve dipenderne.
    for nome in ("quant", "crypto", "fundamentals", "eventdesk", "options", "macro"):
        b.data[nome] = {1: _report(nome)}
    b.data["_red_team"] = {1: "critica precedente"}
    return b


@pytest.fixture
def prompt(bb, monkeypatch):
    """Il messaggio user VERO del red team, assemblato dalla produzione."""
    from bellomberg.core import llm_client
    from bellomberg.core import current_facts
    monkeypatch.setattr(llm_client, "OpenRouterClient", _FintoClient)
    # le tesi del PM passano da un altro canale e leggerebbero il DB: fuori scopo
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda *a, **k: "")

    def _fai():
        from bellomberg.agents.red_team import run_red_team
        try:
            run_red_team(bb, portfolio_data=None, memory_db=None)
        except _Catturato as c:
            return "\n".join(
                m["content"] if isinstance(m["content"], str)
                else json.dumps(m["content"], default=str, ensure_ascii=False)
                for m in c.kwargs["messages"])
        raise AssertionError(
            "nessuna chiamata LLM intercettata: l'assemblaggio e' uscito PRIMA di "
            "costruire il prompt")

    return _fai


# ------------------------------------------------------------------- il difetto

def test_i_report_arrivano_INTERI_verificati_dalla_coda(prompt):
    """Il difetto misurato: 57,9% fuori. Si verifica DALLA CODA, perche' e' la
    parte che il taglio porta via — un test sulla testa passerebbe anche col
    difetto vivo."""
    p = prompt()
    persi = [n for n in TAGLIE if (FIRMA % n.upper()) not in p]
    assert not persi, (
        "la CODA di %s non arriva al red team: e' li' che stanno le proposte "
        "(la validazione candidati di Quant, le due tesi di Fundamentals, "
        "l'income leg di Options)" % ", ".join(sorted(persi)))


def test_nessun_report_esce_col_marcatore_muto_di_prima(prompt):
    """`...[tronco]` dichiarava CHE, mai QUANTO: il modello non poteva sapere se
    gli mancava una riga o il 74% del report."""
    p = prompt()
    assert "[tronco]" not in p, (
        "il marcatore senza numeri e' ancora nel prompt del red team")


# ------------------------------------------------- quando la finestra morde davvero

def test_se_taglia_DICHIARA_il_desk_coi_numeri(bb, prompt, monkeypatch):
    """Un tetto puo' esistere: e' la cintura contro un report fuori scala. Muto no."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    p = prompt()
    assert "NE LEGGI" in p, "manca la dichiarazione per desk"
    assert str(TAGLIE[PIU_LUNGO]) in p, (
        "deve dichiarare la lunghezza VERA del report piu' lungo (%s, %d char), "
        "non solo che c'e' stato un taglio" % (PIU_LUNGO, TAGLIE[PIU_LUNGO]))


def test_non_indirizza_a_ask_specialist_che_il_red_team_NON_HA(bb, prompt, monkeypatch):
    """`_blocco_blackboard` dice ai desk «chiedi quel desk con ask_specialist».
    Il red team ha 3 tool read-only e NON ha ask_specialist (red_team.py:137):
    quella riga gli prometterebbe una porta che non esiste."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    p = prompt()
    assert "ask_specialist" not in p, (
        "il prompt del red team indirizza a un tool che non ha nella sua "
        "tools_schema: e' la lezione (iii) del 21/08 ripetuta sul consumatore "
        "successivo")


def test_dichiara_che_il_resto_NON_e_recuperabile(bb, prompt, monkeypatch):
    """Togliere la bugia non basta: senza una riga che dice come stanno le cose,
    il red team resta con un numero e nessuna istruzione — e il rischio e' che
    deduca ASSENZA da cio' che non vede (un desk che "non ha proposto nulla")."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    p = prompt().lower()
    assert "non e' recuperabile" in p, (
        "manca la dichiarazione che per il red team il resto non e' recuperabile")
    assert "non dedurre" in p, (
        "manca l'istruzione di non dedurre assenze da cio' che non vede")


def test_nessun_desk_sparisce_SENZA_NOME_quando_la_finestra_morde(bb, prompt, monkeypatch):
    """Il difetto peggiore non e' il taglio: e' il desk che evapora insieme alla
    sua chiave, indistinguibile da un desk che non ha scritto nulla."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 3000)
    p = prompt()
    for nome in TAGLIE:
        assert nome.upper() in p, (
            "%s e' sparito del tutto dal prompt del red team" % nome)


# ------------------------------------------------------------ regressioni da tenere

def test_l_ordine_dei_desk_e_quello_del_ROSTER_non_una_corsa_fra_thread(prompt):
    """Guardia di regressione: l'ordine delle chiavi del blackboard e' l'ordine di
    FINE del round 0 (4 worker in parallelo). Il red team lo normalizza sul roster
    e deve continuare a farlo — la fixture scrive apposta in ordine diverso.
    Verificato per MUTAZIONE: passando il dict non ordinato questo test cade."""
    from bellomberg.agents.specialists import ALL_SPECIALISTS
    p = prompt()
    atteso = [s.name for s in ALL_SPECIALISTS if s.name in TAGLIE]
    posizioni = [p.index("--- %s " % n.upper()) for n in atteso]
    assert posizioni == sorted(posizioni), (
        "i desk compaiono in ordine %s invece dell'ordine del roster %s"
        % ([n for _, n in sorted(zip(posizioni, atteso))], atteso))


def test_un_recupero_SCONOSCIUTO_non_ricade_zitto_sul_ramo_che_mente(bb):
    """Trovato dalla review avversariale del 21/08. `recupero` era discriminato per
    uguaglianza di stringa senza `else` di controllo: un typo (`'NESSUNO'`,
    `'nessunO'`, `None`) o un terzo consumatore che dimentica il kwarg finivano sul
    ramo dei desk, cioe' si sentivano promettere `ask_specialist`. Il parametro nato
    per NON promettere una porta inesistente la prometteva, e zitto: e' la classe
    "fallback silenzioso" proprio sul ramo il cui unico scopo e' non mentire."""
    from bellomberg.agents.specialists.base import _blocco_blackboard
    altri = {"macro": {"round": 1, "report": "m" * 5000}}
    with pytest.raises(ValueError) as e:
        _blocco_blackboard(altri, budget=100, recupero="NESSUNO")
    assert "recupero" in str(e.value)


# ----------------------------------------------------- il numero in un posto solo

def test_il_cap_del_red_team_NON_e_un_letterale():
    """5.000 era un letterale nudo dentro il ciclo. Stessa malattia dei 9.000
    della blackboard e dei 6.000 del tetto tool_result: un numero che vive in un
    posto solo non puo' divergere da cio' che lo descrive."""
    src = open(os.path.join(ROOT, "src", "bellomberg", "agents", "red_team.py"),
               encoding="utf-8").read()
    codice = "\n".join(r for r in src.splitlines() if not r.lstrip().startswith("#"))
    assert "[:5000]" not in codice, (
        "il cap dei report al red team e' ancora un letterale nello slice")


def test_la_finestra_del_red_team_e_LA_STESSA_dei_desk(bb, prompt, monkeypatch):
    """Non basta togliere il letterale: la finestra dev'essere QUELLA dei desk,
    cioe' la scelta (i) del PM del 21/08, non un secondo cap che le somiglia.
    La prova non e' testuale ma comportamentale: spostando SOLO
    `MAX_CHAR_BLACKBOARD` si sposta il prompt del red team. Col codice vecchio
    (5.000 fisso) i due prompt erano identici."""
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 300000)
    largo = len(prompt())
    monkeypatch.setattr(sbase, "MAX_CHAR_BLACKBOARD", 12000)
    stretto = len(prompt())
    assert stretto < largo, (
        "la finestra della blackboard non governa il prompt del red team: "
        "%d char in entrambi i casi" % largo)


def test_lo_strumento_di_misura_IMPORTA_il_cap_invece_di_ricopiarlo():
    """prova_contesto_agenti.py teneva `CAP_REPORT_AL_RED_TEAM = 5000` come
    letterale con accanto il commento `# red_team.py:131`. E' lo stesso difetto
    che lo strumento sta cercando: il 21/08 aveva gia' continuato a stampare 8.000
    per un cap diventato 12.000, senza lamentarsi."""
    strumento = os.path.join(ROOT, "tests", "system", "prova_contesto_agenti.py")
    if not os.path.exists(strumento):   # e' uno strumento privato: nel repo pubblico non esce (P2/T9)
        pytest.skip("prova_contesto_agenti.py assente: strumento privato, fuori dal perimetro pubblico")
    src = open(strumento, encoding="utf-8").read()
    codice = "\n".join(r for r in src.splitlines() if not r.lstrip().startswith("#"))
    assert not re.search(r"CAP_REPORT_AL_RED_TEAM\s*=\s*\d", codice), (
        "il cap del red team e' ancora ricopiato come numero nello strumento che "
        "lo misura: va IMPORTATO da dove vive")
