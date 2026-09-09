# -*- coding: utf-8 -*-
"""Le PAROLE DIRETTE del PM devono arrivare INTERE agli agenti (21/08, Opus 5).

La forma del guasto (review del percorso contesto, audit/25), con cifre inventate:

    20 decisioni con un commento del PM, 3.000 caratteri in tutto.
    Tagliate a [:120]: PERSI 600 char = 20% del testo, su 10 righe.
    Se entrassero TUTTE E 20, INTERE, costerebbero 3.000 char — e il margine
    libero nei due prompt era di alcune migliaia di caratteri. Ci stava tutto.

Due difetti distinti, curati qui insieme perche' vivono nella stessa riga:

(A) IL TAGLIO SI TRAVESTE DA CITAZIONE COMPLETA. memory_db:2593 faceva
    `(pm_feedback or "").strip()[:120] + "\\""`: la virgoletta di chiusura veniva
    RIMESSA dopo il taglio, dentro un blocco intitolato "PAROLE DIRETTE DEL PM
    (VINCOLANTI, prevalgono su ogni regola)". Il Capo nella run non ha tool
    (capo.py:424 non passa `tools=`): non poteva recuperare la coda in nessun modo.
    Due esempi inventati della stessa forma: un commento che perde in coda
    "oppure si aggiunge solo se la conviction resta altissima" — un vincolo
    condizionale tagliato prima della condizione diventa un divieto secco; e uno
    che perde "quel mercato mi interessa parecchio", cioe' l'istruzione di
    ricerca, lasciando solo il rimprovero.

(B) LA FINESTRA ERA SATURATA DAL BUCKET SKIPPED. get_decisions_with_pm_feedback
    ordina `CASE WHEN status IN ('SKIPPED','EXPIRED') THEN 0 ELSE 1 END, id DESC`:
    con 11 righe SKIPPED davanti, il LIMIT 5 degli specialisti e il LIMIT 10 del Capo
    erano riempiti SOLO da SKIPPED. Nessun commento su una decisione EXECUTED o
    PARTIAL poteva entrare, per costruzione, quale che fosse la sua data.

La regola di casa: se un limite deve esistere, il taglio si DICHIARA coi numeri.
"""
import re
import os
import sqlite3

import pytest

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Commento inventato, 312 caratteri: e' la misura che la policy deve reggere.
# La parte dopo il 120esimo carattere e' quella che decide se il cap va alzato.
COMMENTO_LUNGO = (
    "comprate altre 7 azioni per ora non me la sentirei di esporre il portafoglio "
    "su un singolo titolo di quel settore per liberare cassa da mettere sulle "
    "altre idee dobbiamo studiare meglio tutto il comparto magari salta fuori un "
    "ingresso migliore piu' avanti oppure si aggiunge solo se la conviction resta altissima"
)
CODA_DECISIVA = "solo se la conviction resta altissima"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """DB vero (schema di produzione) con la stessa FORMA del caso reale:
    tante SKIPPED davanti e le EXECUTED/PARTIAL dietro.

    Lo scorekeeper e la reflection vanno stubbati o aprono il DB di PRODUZIONE
    (lo dice il tripwire di tests/conftest.py, che qui ha fatto il suo lavoro)."""
    from bellomberg.agents import scorekeeper
    from bellomberg.agents import reflection
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist",
                        lambda *a, **k: "", raising=False)
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo",
                        lambda *a, **k: "", raising=False)
    monkeypatch.setattr(reflection, "get_latest_lesson_block",
                        lambda *a, **k: "", raising=False)
    d = MemoryDB(db_path=str(tmp_path / "data" / "consigliere.db"),
                 chroma_path=str(tmp_path / "chroma"))
    with sqlite3.connect(d.db_path) as con:
        # 11 SKIPPED davanti (la forma del caso reale): da sole saturavano LIMIT 5 e LIMIT 10
        for i in range(11):
            con.execute(
                "INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
                "VALUES (?,?,?,?,?)",
                ("2026-06-%02d" % (i + 1), "BUY", "SKIP%d" % i, "SKIPPED",
                 "non mi convince per ora"))
        # la PARTIAL col commento lungo: e' quella che oggi non entra MAI
        con.execute(
            "INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
            "VALUES (?,?,?,?,?)",
            ("2026-08-20", "ADD", "KAPPA", "PARTIAL", COMMENTO_LUNGO))
        con.commit()
    return d


# --------------------------------------------------------------- (A) il testo intero

def test_il_commento_lungo_del_PM_arriva_INTERO_al_capo(db):
    """Il Capo e' quello che DECIDE e non ha tool nella run: se la coda non e'
    nel prompt, non esiste per lui."""
    b = db.build_capo_memory_context(max_chars=16000)
    assert COMMENTO_LUNGO in b, "il commento del PM non arriva intero al Capo"
    assert CODA_DECISIVA in b, (
        "manca la CONDIZIONE in coda: tagliata prima della condizione, "
        "'non me la sentirei di esporre' diventa un divieto secco invece che "
        "un 'non ancora'")


def test_il_commento_lungo_del_PM_arriva_INTERO_agli_specialisti(db):
    b = db.build_specialist_memory_context("quant", max_chars=8000)
    assert COMMENTO_LUNGO in b
    assert CODA_DECISIVA in b


def test_il_taglio_non_si_traveste_da_citazione(db, monkeypatch):
    """Il difetto peggiore non era il taglio: era la virgoletta di chiusura
    rimessa DOPO il taglio, che rendeva il troncamento invisibile."""
    monkeypatch.setattr(memory_db, "MAX_CHAR_FEEDBACK_PM", 60)
    b = db.build_capo_memory_context(max_chars=16000)
    assert COMMENTO_LUNGO not in b, "precondizione: con cap 60 il testo dev'essere tagliato"
    frammento = COMMENTO_LUNGO[:60]
    assert frammento + '"' not in b, (
        "la virgoletta di chiusura e' stata rimessa subito dopo il taglio: "
        "il troncamento si traveste da citazione completa")


def test_se_taglia_lo_DICHIARA_coi_numeri(db, monkeypatch):
    monkeypatch.setattr(memory_db, "MAX_CHAR_FEEDBACK_PM", 60)
    b = db.build_capo_memory_context(max_chars=16000)
    assert "TAGLIATO" in b or "TRONCAT" in b.upper()
    assert str(len(COMMENTO_LUNGO)) in b, (
        "il blocco deve dire quanto e' lungo il testo VERO del PM, "
        "non solo che e' stato tagliato")


def test_il_limite_e_una_costante_esplicita():
    """Una policy con un nome, come MAX_CHAR_TESI e PRICE_STALE_AFTER_MIN."""
    assert isinstance(memory_db.MAX_CHAR_FEEDBACK_PM, int)
    assert memory_db.MAX_CHAR_FEEDBACK_PM >= 312, (
        "sotto i 312 caratteri un commento lungo come quello della fixture "
        "(312 char) tornerebbe mozzo")


# --------------------------------------------------------------- (B) la finestra

def _blocco_vincolante(testo):
    """Solo la sezione delle PAROLE DIRETTE, non tutto il prompt.

    Serve perche' altrove nel blocco esiste(va) una riga 'Recent recommendations'
    che ripeteva lo STESSO campo con un cap diverso: senza questo ritaglio il
    test passerebbe grazie al duplicato invece che grazie al canale vincolante
    — cioe' misurerebbe la cosa sbagliata."""
    for testa in ("PAROLE DIRETTE DEL PM", "PM feedback on past decisions"):
        i = testo.find(testa)
        if i >= 0:
            coda = testo[i:]
            fine = coda.find("\n--- ")
            return coda[:fine] if fine > 0 else coda
    return ""


def test_un_commento_su_una_decisione_ESEGUITA_riesce_a_entrare(db):
    """Il bug (B): 11 SKIPPED saturavano LIMIT 5 e LIMIT 10, quindi nessun
    commento su EXECUTED/PARTIAL poteva entrare, per costruzione."""
    b = _blocco_vincolante(db.build_capo_memory_context(max_chars=16000))
    assert "KAPPA" in b, (
        "la PARTIAL con commento del PM non entra nel blocco VINCOLANTE: il "
        "bucket SKIPPED satura la finestra e nessun feedback su un trade "
        "ESEGUITO raggiunge il Capo")


def test_anche_gli_specialisti_vedono_le_decisioni_ESEGUITE(db):
    b = _blocco_vincolante(db.build_specialist_memory_context("quant", max_chars=8000))
    assert "KAPPA" in b


def test_se_restano_fuori_delle_righe_il_blocco_lo_DICE(db, monkeypatch):
    """Un tetto puo' esistere, ma non puo' essere muto: il blocco deve dire
    quante voci restano fuori.

    La prima stesura faceva `assert "9" in b` ed era un assert FINTO: passava
    grazie al ticker 'SKIP9' della fixture anche con la dichiarazione rimossa.
    L'ha trovato la review avversariale del 21/08. Qui si asserisce la FRASE."""
    monkeypatch.setattr(memory_db, "MAX_RIGHE_FEEDBACK_PM", 3)
    b = db.build_capo_memory_context(max_chars=16000)
    assert "9 commenti del PM su 12 restano FUORI" in b, (
        "con 12 righe e tetto 3 il blocco deve dichiarare NUMERO e TOTALE, "
        "non limitarsi a tagliare")


def test_il_tetto_sulle_righe_basta_per_quelle_che_esistono(db):
    """Un tetto sulle righe senza pavimento e' un taglio che aspetta: con 60 e
    le 12 righe della fixture non scatta nessuna dichiarazione, ed e' cosi' che
    dev'essere."""
    assert memory_db.MAX_RIGHE_FEEDBACK_PM >= 28, (
        "il tetto deve reggere un diario di 28 decisioni commentate: sotto 28 "
        "il blocco comincia a dichiarare righe fuori senza motivo")
    b = db.build_capo_memory_context(max_chars=16000)
    assert "restano FUORI" not in b, (
        "con 12 righe e tetto 60 non deve restare fuori nulla")


def test_la_dichiarazione_del_taglio_nomina_la_colonna_giusta(db, monkeypatch):
    """Il messaggio diceva sempre 'decisions.pm_feedback' anche quando il testo
    veniva da un'altra colonna: mandava l'agente a cercare nel posto sbagliato."""
    monkeypatch.setattr(memory_db, "MAX_CHAR_FEEDBACK_PM", 10)
    reso = memory_db.pm_verbatim("x" * 50, ident=7, fonte="pm_feedback.feedback_text")
    assert "pm_feedback.feedback_text" in reso
    assert "decisions.pm_feedback" not in reso


# --------------------------------------------- il numero non deve tornare a vivere in due posti

def test_nessun_taglio_a_letterale_sul_testo_del_PM():
    """Vincolo MECCANICO (stessa forma di tests/test_tetto_tool_result.py): il
    tetto dei tool_result era finito in tre posti e i due letterali erano quelli
    che tagliavano davvero. Qui il testo del PM deve passare SOLO dalla policy."""
    sospetti = []
    for rel in ("src/bellomberg/storage/memory_db.py",
                "src/bellomberg/agents/action_validator.py"):
        testo = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for n, riga in enumerate(testo.splitlines(), 1):
            if riga.lstrip().startswith("#"):
                continue
            if re.search(r'(pm_feedback|veto_reason|feedback_text)[^\n]{0,40}\[\s*:\s*\d+\s*\]', riga):
                sospetti.append("%s:%d  %s" % (rel, n, riga.strip()))
    assert not sospetti, (
        "il testo del PM viene tagliato con un letterale invece che dalla policy "
        "MAX_CHAR_FEEDBACK_PM:\n" + "\n".join(sospetti))


def test_il_validator_non_chiude_le_virgolette_su_un_taglio():
    """action_validator.py ripeteva lo STESSO difetto (`[:120] + '»'`) dentro un
    blocco che dice «citate testualmente»."""
    testo = open(os.path.join(ROOT, "src", "bellomberg", "agents", "action_validator.py"),
                 encoding="utf-8").read()
    # solo CODICE: un commento che racconta il difetto storico non e' il difetto
    # (e serve, perche' spiega perche' la riga oggi e' com'e')
    righe = [r for r in testo.splitlines() if not r.lstrip().startswith("#")]
    codice = "\n".join(righe)
    assert '[:120] + "»"' not in codice and "[:120] + '»'" not in codice, (
        "il validator taglia a 120 e rimette il caporale di chiusura: "
        "stesso travestimento del difetto curato in memory_db")


def test_il_validator_non_importa_la_policy_dentro_un_except_muto():
    """L'import stava DENTRO un `try/except Exception: pass` che avvolge tutto
    il blocco: se fosse fallito, TUTTI gli avvertimenti sui feedback del PM
    sarebbero spariti dal memo senza una riga. Sta al modulo (nessuna
    circolarita': memory_db non importa action_validator)."""
    testo = open(os.path.join(ROOT, "src", "bellomberg", "agents", "action_validator.py"),
                 encoding="utf-8").read()
    prima_di_def = testo.split("\ndef ")[0]
    assert "pm_verbatim" in prima_di_def, (
        "l'import della policy dev'essere al modulo, non dentro il try/except "
        "che inghiotte gli errori")


def test_il_puntatore_al_blocco_BINDING_non_afferma_il_falso(db, monkeypatch):
    """La riga «testo intero nel blocco BINDING sopra» era incondizionata: se la
    decisione resta fuori dalla finestra, quella frase manda l'agente a cercare
    in un blocco dove il testo non c'e'. Una frase di stato dev'essere vera in
    OGNI stato, non solo in quello di oggi."""
    monkeypatch.setattr(memory_db, "MAX_RIGHE_FEEDBACK_PM", 1)
    b = db.build_specialist_memory_context("quant", max_chars=99999)
    assert "KAPPA" not in _blocco_vincolante(b), (
        "precondizione: con tetto 1 la PARTIAL deve restare fuori dal BINDING")
    assert COMMENTO_LUNGO in b, (
        "il commento non e' nel blocco BINDING e non viene reso da nessun'altra "
        "parte: il puntatore promette un testo che nel prompt non esiste")


def test_il_cap_della_memoria_regge_la_policy_delle_parole_del_PM():
    """La lezione del 20/08, ripetuta il 21/08 dalla review su questa stessa cura:
    alzare un limite senza guardare il cap a valle sposta solo il punto del taglio.

    Misurato il 21/08 sul DB vero, DOPO la cura: blocco specialisti 6.543 char,
    blocco Capo 11.113. Un solo commento del PM alla policy piena (2.000) deve
    poter entrare senza far scattare [MEMORIA TRONCATA], che taglia in CODA e si
    porta via il TRACK RECORD."""
    from bellomberg.agents import capo
    import bellomberg.agents.specialists.base as sbase
    assert sbase.MAX_CHAR_MEMORIA_SPECIALISTA >= 6543 + memory_db.MAX_CHAR_FEEDBACK_PM, (
        "il cap degli specialisti non regge il blocco misurato piu' un commento "
        "del PM a policy piena: il taglio e' gia' armato")
    assert capo.MAX_CHAR_MEMORIA_CAPO >= 11113 + memory_db.MAX_CHAR_FEEDBACK_PM, (
        "idem per il Capo, che e' quello che decide e non ha tool per recuperare")


def test_la_memoria_troncata_NOMINA_cio_che_ha_perso(db, monkeypatch):
    """«coda persa — dichiarato» dice CHE ha tagliato, non COSA: e cio' che cade
    per primo e' il TRACK RECORD. Un marcatore che non nomina la sezione persa
    non permette a nessuno — ne' all'agente ne' al PM nel log — di sapere cosa manca."""
    b = db.build_capo_memory_context(max_chars=400)
    assert "MEMORIA TRONCATA" in b
    assert "sezioni perse" in b.lower() or "perse:" in b.lower(), (
        "il marcatore deve elencare le sezioni cadute, non solo dire che c'e' una coda")


# --------------------------------- lo stesso travestimento nell'ALTRO canale del PM

def test_le_note_del_PM_sui_preferiti_non_sono_tagliate_di_nascosto(tmp_path, monkeypatch):
    """current_facts.favorites_block() portava la nota del PM sui titoli preferiti
    tagliata a un letterale 500 con la virgoletta di CHIUSURA rimessa dopo — lo
    stesso travestimento curato in memory_db, nello stesso prompt degli
    specialisti, sullo stesso autore. E l'app ne accetta 1.000 in scrittura
    (bellomberg_api.py:870/884): 500 caratteri che il PM scrive non arrivavano."""
    from bellomberg.core import current_facts
    nota = "Compro sotto 40. " + ("dettaglio " * 60) + "MA SOLO SE il rame tiene."
    assert len(nota) > 500
    p = tmp_path / "data"
    p.mkdir()
    con = sqlite3.connect(str(p / "consigliere.db"))
    # nomi di colonna VERI (current_facts.py:213): ticker, name, sector, industry,
    # note + added_at nell'ORDER BY. Con nomi diversi la query cade nel ramo di
    # ripiego e il test misurerebbe un blocco vuoto invece del taglio.
    con.execute("CREATE TABLE favorite_companies (ticker TEXT, name TEXT, sector TEXT, "
                "industry TEXT, note TEXT, added_at TEXT)")
    con.execute("INSERT INTO favorite_companies VALUES "
                "('FCX','Freeport','Materials','Copper',?,'2026-08-01')", (nota,))
    con.commit()
    con.close()
    # B4 (02/09): current_facts legge memory_db.SQLITE_PATH (fonte unica), non __file__
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(p / "consigliere.db"))
    current_facts._FAV_CACHE["text"] = None
    current_facts._FAV_CACHE["ts"] = 0
    try:
        b = current_facts.favorites_block()
        assert "MA SOLO SE il rame tiene." in b, (
            "la condizione in CODA alla nota del PM non arriva: tagliata prima "
            "della condizione, l'istruzione si rovescia di senso")
        assert nota[:500] + '"' not in b, (
            "virgoletta di chiusura rimessa dopo il taglio: la nota tagliata "
            "sembra il pensiero completo del PM")
    finally:
        current_facts._FAV_CACHE["text"] = None
        current_facts._FAV_CACHE["ts"] = 0
