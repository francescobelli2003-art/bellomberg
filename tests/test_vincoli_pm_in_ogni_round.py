"""Le PAROLE VINCOLANTI del PM devono arrivare in OGNI round, non solo in R0.
(22/08 sera-2, Fable 5, chat backend — voce (2b) del MASTER, scelta dal PM il
21/08 e confermata il 22/08 sera: «salta la misura, fai subito la cura»)

La voce storica (MASTER §9-terquadragies, 21/08) dichiarava le ancore dei
feedback/veti del PM assenti in «11 prompt su 17». RICONTATO dalla review
22/08 sera-2 sul roster VERO: sono **10** — 6 Round 1 + 3 Round 2 + red team
(il 17 conta anche i 6 Round 0 e il Capo, che hanno i loro canali). La
discrepanza e' segnalata al PM; la voce storica non si corregge da soli
(lezione 28/07). Il blocco «PM feedback on past decisions
(BINDING)» vive in `build_specialist_memory_context`, che `specialists/base.py`
inietta SOLO nel preambolo del Round 0 (`_build_round_context`); il red team
(`red_team.py`) non lo vede mai. Quindi un veto ETERNO del PM ("mai riproporre
la covered call") valeva per il round di ricognizione e spariva nei round in
cui le tesi si SCRIVONO e si ATTACCANO.

Premesse corrette il 22/08 (§9-quaterquadragies): `MAX_CHAR_BLACKBOARD` non e'
il tetto del preambolo (limita solo il blocco dei report altrui) e sul totale
del preambolo NON esiste alcun cap — quindi l'iniezione non puo' spostare nessun
taglio: la condizione del PM («nessun taglio nuovo») vale per costruzione.

Tre stati, tutti DICHIARATI (regola 14/07, lezione «frase di stato»):
  - ci sono feedback/veti  -> il blocco BINDING, identico a quello di R0;
  - non ce ne sono          -> una riga che lo dice (uno zero misurato);
  - il DB e' guasto         -> una riga che dice che NON e' misurato.
"""
import json
import sqlite3

import pytest

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB
from bellomberg.agents.specialists.base import Blackboard, Specialist


VETO = "mai piu' covered call su IOTA, nemmeno in variante"
FEEDBACK = "basta proporre di vendere ALFA sotto i 400: non mi va"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """DB vero (schema di produzione) in tmp, con un veto e un feedback del PM.
    Scorekeeper e reflection stubbati: altrimenti aprono il DB di PRODUZIONE
    (lo dice il tripwire (b) del conftest)."""
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
        con.execute(
            "INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
            "VALUES (?,?,?,?,?)",
            ("2026-08-10", "SELL", "ALFA", "SKIPPED", FEEDBACK))
        con.execute(
            "INSERT INTO decisions (timestamp, action, ticker, status, veto, "
            "veto_reason, veto_at) VALUES (?,?,?,?,?,?,?)",
            ("2026-08-12", "SELL_CALL", "IOTA.L", "SKIPPED", 1, VETO, "2026-08-12"))
        con.commit()
    return d


@pytest.fixture
def db_vuoto(tmp_path, monkeypatch):
    from bellomberg.agents import scorekeeper
    from bellomberg.agents import reflection
    monkeypatch.setattr(scorekeeper, "get_track_record_for_specialist",
                        lambda *a, **k: "", raising=False)
    monkeypatch.setattr(reflection, "get_latest_lesson_block",
                        lambda *a, **k: "", raising=False)
    return MemoryDB(db_path=str(tmp_path / "data" / "consigliere.db"),
                    chroma_path=str(tmp_path / "chroma"))


class _Desk(Specialist):
    """Specialista minimo: serve il preambolo, non lo scoring (che farebbe rete)."""
    name = "quant"
    system_prompt = "test"
    tools_used = []

    def compute_score(self):
        return None


def _preambolo(db, round_n, nome="quant"):
    bb = Blackboard(memory_db=db, memo_id=None)
    bb.data["macro"] = {1: "Report di macro. " * 50}
    bb.data["fundamentals"] = {1: "Report di fundamentals. " * 50}
    d = _Desk(bb, client=object())
    d.name = nome
    return d._build_round_context(round_n)


# ============================================================
# 1. IL BLOCCO ESTRATTO DA SOLO — identico a quello che R0 vede gia'
# ============================================================

def test_il_blocco_binding_e_estraibile_da_solo(db):
    b = db.build_pm_binding_block()
    assert "BINDING" in b
    assert VETO in b and FEEDBACK in b
    assert "VINCOLANTI" in b, "manca la regola che dice COME pesano le righe"


def test_R0_non_cambia_il_blocco_e_lo_stesso(db):
    """Refactor senza cambio di comportamento: la sezione BINDING dentro la
    memoria di R0 e' BYTE-IDENTICA al blocco estratto."""
    intero = db.build_specialist_memory_context("quant", max_chars=99999)
    b = db.build_pm_binding_block().strip()
    assert b.startswith("--- PM feedback on past decisions (BINDING) ---")
    # (la prima stesura ritagliava fino al prossimo "--- " e, senza report
    # in R0, inglobava «(No previous reports...)»: misurava il test, non R0)
    assert b in intero, "R0 non contiene il blocco estratto byte per byte"
    assert intero.count(b) == 1, "R0 lo contiene piu' di una volta"


def test_senza_feedback_il_blocco_e_vuoto(db_vuoto):
    assert db_vuoto.build_pm_binding_block() == ""


# ============================================================
# 2. ROUND 1 E ROUND 2 DEGLI SPECIALISTI
# ============================================================

@pytest.mark.parametrize("round_n", [1, 2])
def test_il_round_porta_le_parole_del_pm(db, round_n):
    p = _preambolo(db, round_n)
    assert VETO in p, "il veto ETERNO del PM non arriva al round %d" % round_n
    assert FEEDBACK in p
    assert "VINCOLANTI" in p


@pytest.mark.parametrize("round_n", [1, 2])
def test_il_blocco_sta_PRIMA_della_blackboard(db, round_n):
    """Il preambolo non ha un cap totale (premessa verificata il 22/08), ma
    l'ordine conta lo stesso: i vincoli prima dei report altrui, come in R0
    (review 23/07: «il blocco sta IN TESTA, prima dei report»)."""
    p = _preambolo(db, round_n)
    assert 0 <= p.find("BINDING") < p.find("BLACKBOARD:")


def test_il_round_0_resta_com_era(db):
    """R0 aveva gia' il blocco dentro YOUR MEMORY: non deve averlo DUE volte."""
    p = _preambolo(db, 0)
    assert p.count("--- PM feedback on past decisions (BINDING) ---") == 1


@pytest.mark.parametrize("round_n", [1, 2])
def test_senza_feedback_il_round_lo_DICE(db_vuoto, round_n):
    """Uno zero misurato si dichiara: «nessun feedback ne' veto a registro» e'
    un'informazione, il silenzio no."""
    p = _preambolo(db_vuoto, round_n)
    assert "nessun feedback" in p.lower() and "veto" in p.lower()


@pytest.mark.parametrize("round_n", [1, 2])
def test_se_il_db_e_guasto_il_round_dice_che_NON_e_misurato(db, round_n, monkeypatch):
    """Il terzo stato: un guasto non deve sembrare «nessun vincolo»."""
    monkeypatch.setattr(MemoryDB, "build_pm_binding_block",
                        lambda self: (_ for _ in ()).throw(RuntimeError("DB locked")))
    p = _preambolo(db, round_n)
    assert "NON DISPONIBIL" in p.upper()
    assert "DB locked" in p
    assert "non dedurre" in p.lower()


def test_un_desk_senza_memoria_non_cambia(db):
    """Blackboard(memory_db=None): niente memoria, niente blocco, nessun
    errore — com'era (i test esistenti del blackboard lo usano cosi')."""
    bb = Blackboard(memory_db=None, memo_id=None)
    bb.data["macro"] = {1: "Report di macro."}
    d = _Desk(bb, client=object())
    p = d._build_round_context(1)
    assert "BINDING" not in p


# ============================================================
# 3. IL RED TEAM
# ============================================================

class _Catturato(BaseException):
    """Deriva da BaseException apposta: `run_red_team` e' tutto dentro un
    `except Exception` best-effort che inghiottirebbe una Exception normale."""

    def __init__(self, kwargs):
        super().__init__("prompt catturato")
        self.kwargs = kwargs


class _FintoMessages:
    def create(self, **kw):
        raise _Catturato(kw)


class _FintoClient:
    def __init__(self, *a, **kw):
        self.messages = _FintoMessages()


def _prompt_red_team(db, monkeypatch):
    """Il messaggio user VERO del red team, assemblato dalla produzione."""
    from bellomberg.core import llm_client
    from bellomberg.core import current_facts
    monkeypatch.setattr(llm_client, "OpenRouterClient", _FintoClient)
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda *a, **k: "")
    bb = Blackboard(memory_db=db, memo_id=None)
    for nome in ("quant", "macro"):
        bb.data[nome] = {1: "Report di %s. " % nome * 30}
    from bellomberg.agents.red_team import run_red_team
    try:
        run_red_team(bb, portfolio_data=None, memory_db=db)
    except _Catturato as c:
        return "\n".join(
            m["content"] if isinstance(m["content"], str)
            else json.dumps(m["content"], default=str, ensure_ascii=False)
            for m in c.kwargs["messages"])
    raise AssertionError("nessuna chiamata LLM intercettata")


def test_il_red_team_riceve_le_parole_del_pm(db, monkeypatch):
    p = _prompt_red_team(db, monkeypatch)
    assert VETO in p and FEEDBACK in p
    assert "VINCOLANTI" in p


def test_nel_red_team_il_blocco_sta_prima_dei_report(db, monkeypatch):
    p = _prompt_red_team(db, monkeypatch)
    assert 0 <= p.find("BINDING") < p.find("Report di quant")


def test_il_red_team_senza_feedback_lo_dice(db_vuoto, monkeypatch):
    p = _prompt_red_team(db_vuoto, monkeypatch)
    assert "nessun feedback" in p.lower()


def test_il_red_team_senza_parametro_pesca_il_db_dalla_blackboard(db, monkeypatch):
    """Il ramo `getattr(blackboard, "memory_db", None)`: la produzione passa
    `memory_db=db` esplicito (consigliere_multi.py:498), ma il fallback esiste
    e un ramo mai misurato e' un ramo che nessuno guarda (review 22/08 sera-2:
    prima di questo test una mutazione `_mdb = memory_db` sopravviveva)."""
    from bellomberg.core import llm_client
    from bellomberg.core import current_facts
    monkeypatch.setattr(llm_client, "OpenRouterClient", _FintoClient)
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda *a, **k: "")
    bb = Blackboard(memory_db=db, memo_id=None)
    bb.data["quant"] = {1: "Report di quant."}
    from bellomberg.agents.red_team import run_red_team
    try:
        run_red_team(bb)  # NIENTE memory_db: deve pescarlo dalla blackboard
    except _Catturato as c:
        testo = "\n".join(
            m["content"] if isinstance(m["content"], str)
            else json.dumps(m["content"], default=str, ensure_ascii=False)
            for m in c.kwargs["messages"])
        assert VETO in testo, "il fallback dalla blackboard non porta i vincoli"
        return
    raise AssertionError("nessuna chiamata LLM intercettata")


# ============================================================
# 27/08 (run V9, checklist punto 3): il blocco entrava nel prompt senza lasciare
# traccia nel log, quindi a run viva NON si poteva dire se i desk R1/R2 lo
# avessero ricevuto (i report citavano #186/#159 ma potevano venire dai R0 in
# blackboard). Una riga per desk-round e una per il red team, con lo STATO
# letto dall'intestazione del blocco (`stato_vincoli_pm`); la firma di
# `blocco_vincoli_pm` resta intatta: le ancore del banco la citano.
# ============================================================

def _log_preambolo(db, round_n, capsys, nome="quant"):
    _preambolo(db, round_n, nome=nome)
    return capsys.readouterr().out


@pytest.mark.parametrize("round_n", [1, 2])
def test_il_log_dice_che_i_vincoli_sono_entrati_e_quanti(db, round_n, capsys):
    out = _log_preambolo(db, round_n, capsys)
    righe = [r for r in out.splitlines() if "vincoli PM R%d:" % round_n in r]
    assert len(righe) == 1, out
    assert righe[0].startswith("  [quant] "), righe[0]
    from bellomberg.agents.specialists.base import blocco_vincoli_pm
    atteso = "VINCOLANTI (2 righe, %d char)" % len(blocco_vincoli_pm(db))  # 1 feedback + 1 veto
    assert atteso in righe[0], (atteso, righe[0])


def test_le_righe_contate_sono_le_decisioni_non_le_parole_del_pm_a_capo(db, capsys):
    """`pm_verbatim` conserva gli a-capo del PM: una riga del suo testo che
    inizia per `#` non e' una decisione (review 27/08)."""
    with sqlite3.connect(db.db_path) as con:
        con.execute(
            "INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
            "VALUES (?,?,?,?,?)",
            ("2026-08-13", "BUY", "RHO", "SKIPPED",
             "prima riga del PM\n#186 basta covered call\n# nota a capo"))
        con.commit()
    out = _log_preambolo(db, 1, capsys)
    assert "VINCOLANTI (3 righe, " in out, out  # 3 decisioni, non 5 righe che iniziano per #


def test_le_azioni_a_piu_parole_e_i_ticker_vuoti_si_contano(db, capsys):
    """Forme VERE del registro (review 27/08, seconda passata: 22 azioni con
    spazi e 24 ticker vuoti nel DB di produzione): `ADD (new)`, `HEDGE - BUY
    PUT`, ticker '' e NULL. Una regex a token (`\\S+ \\S+`) le perdeva e il log
    dichiarava «2 righe» su 5."""
    with sqlite3.connect(db.db_path) as con:
        for ts, action, ticker in (("2026-08-14", "ADD (new)", "KAPPA"),
                                   ("2026-08-15", "HEDGE - BUY PUT", "SPY"),
                                   ("2026-08-16", "TRIM", ""),
                                   ("2026-08-17", "SELL", None)):
            con.execute(
                "INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
                "VALUES (?,?,?,?,?)", (ts, action, ticker, "SKIPPED", "ok, fai cosi'"))
        con.commit()
    out = _log_preambolo(db, 1, capsys)
    assert "VINCOLANTI (6 righe, " in out, out  # 2 della fixture + 4 nuove


@pytest.mark.parametrize("round_n", [1, 2])
def test_il_log_dice_lo_zero_misurato(db_vuoto, round_n, capsys):
    out = _log_preambolo(db_vuoto, round_n, capsys)
    assert "[quant] vincoli PM R%d: nessun feedback" % round_n in out, out
    riga = out.split("vincoli PM R%d: " % round_n)[1].splitlines()[0]
    assert "righe" not in riga and "char)" not in riga, riga  # a zero righe niente conteggio


@pytest.mark.parametrize("round_n", [1, 2])
def test_il_log_dice_il_registro_guasto(db, round_n, capsys, monkeypatch):
    def _boom(self):
        raise RuntimeError("db lockato")
    monkeypatch.setattr(MemoryDB, "build_pm_binding_block", _boom)
    out = _log_preambolo(db, round_n, capsys)
    assert "[quant] vincoli PM R%d: NON DISPONIBILI" % round_n in out, out


def test_in_R0_nessuna_riga_di_vincoli(db, capsys):
    out = _log_preambolo(db, 0, capsys)
    assert "vincoli PM" not in out, out


def test_un_desk_senza_registro_lo_dice_nel_log(capsys):
    bb = Blackboard(memory_db=None, memo_id=None)
    d = _Desk(bb, client=object())
    d._build_round_context(1)
    assert "[quant] vincoli PM R1: assenti" in capsys.readouterr().out


def test_il_red_team_logga_lo_stato_dei_vincoli(db, monkeypatch, capsys):
    _prompt_red_team(db, monkeypatch)
    assert "[RED_TEAM] vincoli PM: VINCOLANTI (2 righe, " in capsys.readouterr().out


def test_il_red_team_logga_lo_zero_misurato(db_vuoto, monkeypatch, capsys):
    _prompt_red_team(db_vuoto, monkeypatch)
    assert "[RED_TEAM] vincoli PM: nessun feedback" in capsys.readouterr().out
