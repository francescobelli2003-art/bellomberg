"""LOTTO F3 — le 5 richieste del frontend evase il 26/07 sera-6 (Opus 5).

Copre: n.1 PATCH titolo · n.2 retro-titolatura · n.3 first_user_message ·
n.5 tokens_in/out in get_messages · n.6 cost_eur sull'evento done.

NOTA DI METODO (lezione della review avversariale 26/07 sera-5, che trovo'
DUE miei test incapaci di fallire): ogni test qui sotto e' stato provato per
MUTAZIONE — si e' rimessa la riga vecchia e si e' verificato che il test
diventasse rosso. Dove il test inchioda una scelta invisibile (il TTL unico,
`last_activity` non toccata) c'e' scritto cosa si rompe se qualcuno la disfa.
"""
import ast
import json
import os
import sqlite3
import sys

import pytest

from bellomberg.agents import chat_engine
from bellomberg.core import llm_pricing
from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_ENGINE_PATH = os.path.join(
    ROOT, "src", "bellomberg", "agents", "chat_engine.py")

# Le prove dei contratti di correzione usano un corpus sintetico completo.
# Non dipendono dalla presenza del profilo privato del PM o dalla cwd.


@pytest.fixture(autouse=True)
def fx_deterministico(monkeypatch):
    """La suite e' OFFLINE per contratto (conftest.py:1-9): niente rete.

    Senza questo, `_costo_risposta` -> `llm_pricing.cost_eur` -> `_fx_usd_to_eur`
    -> `price_updater.get_fx_to_eur` -> yfinance, cioe' un fetch VIVO a Yahoo
    (misurato 0,55 s dalla review pre-commit 26/07 sera-6). Due danni: in CI,
    senza rete, `cost` sarebbe None e l'assert `con > senza` diventerebbe un
    TypeError — il test ERRA invece di fallire, che e' il peggiore dei due; e
    `_FX_MEMO` e' globale di processo, quindi un cambio vivo si sarebbe
    appiccicato a tutto il resto della suite. Idioma gia' in casa:
    tests/test_run_robustness.py:84-87.
    """
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "test"))
    llm_pricing.reset_fx_memo()
    yield
    llm_pricing.reset_fx_memo()


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    """DB temporaneo + le `MemoryDB()` interne di chat_engine rediritte li'.

    ⚠️ Patchare `memory_db.SQLITE_PATH` NON basta e non e' una svista: la firma
    e' `def __init__(self, db_path=SQLITE_PATH, chroma_path=CHROMA_PATH)`
    (memory_db.py:569) e i default di una funzione Python sono valutati
    **all'import**, non alla chiamata. Quindi una `MemoryDB()` senza argomenti
    va sul DB del PM qualunque cosa dica il modulo dopo. Le funzioni di
    chat_engine costruiscono `MemoryDB()` cosi': l'unico aggancio e' il nome
    locale che chat_engine ha importato. (Verificato dal tripwire di conftest,
    che infatti scattava.)
    """
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    db_path = str(tmp_path / "data" / "test.db")
    d = MemoryDB(db_path=db_path, chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(memory_db, "SQLITE_PATH", db_path)
    monkeypatch.setattr(memory_db, "CHROMA_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(chat_engine, "MemoryDB", lambda: d)
    return d


def _sessione(db, agente="quant", titolo="Chat con Quant"):
    with db._conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_sessions(specialist, title, started_at, last_activity) "
            "VALUES (?, ?, datetime('now'), datetime('now'))", (agente, titolo))
        return cur.lastrowid


# ============================================================
# n.3 — first_user_message nella lista sessioni
# ============================================================

def test_first_user_message_e_la_prima_domanda_del_pm(db):
    """Deve essere la PRIMA riga `user`, non la prima riga qualunque.

    L'ordine di inserimento e' scelto per DISCRIMINARE: la riga con id piu'
    basso e' di un `assistant`, e c'e' una seconda domanda dopo. Cosi' entrambe
    le mutazioni diventano rosse — togliere `AND m.role='user'` restituisce il
    testo dell'assistente, e `ASC` -> `DESC` restituisce la seconda domanda.
    (Prima versione di questo test: la domanda era anche la prima riga in
    assoluto, quindi il filtro sul ruolo non era provato da niente. Trovato
    dalla prova di mutazione, non a occhio.)
    """
    sid = _sessione(db)
    chat_engine._save_message(sid, "assistant", "APERTURA DELL-AGENTE")
    chat_engine._save_message(sid, "user", "prima domanda del PM")
    chat_engine._save_message(sid, "assistant", "risposta dell'agente")
    chat_engine._save_message(sid, "user", "SECONDA domanda del PM")

    riga = [s for s in chat_engine.list_sessions("quant") if s["id"] == sid][0]
    assert riga["first_user_message"] == "prima domanda del PM"
    assert riga["first_user_message_len"] == len("prima domanda del PM")


def test_first_user_message_none_se_non_ci_sono_domande(db):
    """Le 12 sessioni vuote in DB: None DICHIARATO, non stringa vuota.

    None e "" si renderebbero uguali a schermo ma dicono cose diverse: "non ha
    mai chiesto niente" contro "ha chiesto qualcosa di vuoto" (regola 14/07).
    """
    sid = _sessione(db)
    riga = [s for s in chat_engine.list_sessions("quant") if s["id"] == sid][0]
    assert riga["first_user_message"] is None
    assert riga["first_user_message_len"] is None


def test_troncamento_dichiarato_non_zitto(db):
    """Il taglio deve essere leggibile dal payload, non indovinabile.

    Muta cosi' e diventa rosso: togli la subquery `length()` e il frontend non
    puo' piu' sapere se sta guardando una domanda intera o una mozzata.
    """
    sid = _sessione(db)
    lunga = "x" * 500
    chat_engine._save_message(sid, "user", lunga)
    riga = [s for s in chat_engine.list_sessions("quant") if s["id"] == sid][0]
    assert len(riga["first_user_message"]) == chat_engine.FIRST_MSG_PREVIEW_CHARS
    assert riga["first_user_message_len"] == 500       # la lunghezza VERA
    assert riga["first_user_message_len"] > len(riga["first_user_message"])


# ============================================================
# n.5 — tokens_in/tokens_out consegnati da get_messages
# ============================================================

def test_get_messages_consegna_i_token(db):
    """Le colonne c'erano ed erano piene: mancava solo la SELECT.

    Muta cosi' e diventa rosso: rimetti la SELECT a
    `id, role, content, timestamp` -> KeyError su tokens_in.
    """
    sid = _sessione(db)
    chat_engine._save_message(sid, "user", "domanda")
    chat_engine._save_message(sid, "assistant", "risposta", tokens_in=1234, tokens_out=567)

    msgs = chat_engine.get_messages(sid)
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert assistant["tokens_in"] == 1234
    assert assistant["tokens_out"] == 567
    # sulla riga user restano NULL: e' il contratto, non un buco
    assert [m for m in msgs if m["role"] == "user"][0]["tokens_in"] is None


def test_history_dello_stream_non_si_rompe_coi_campi_in_piu(db):
    """`get_messages` la usa anche stream_chat per ricostruire l'history.

    Due colonne in piu' non devono cambiare cio' che finisce nel contesto del
    modello: si riproduce QUI il ciclo vero di stream_chat (chat_engine.py, blocco
    `api_messages`) invece di fidarsi che "tanto legge solo role e content".
    """
    sid = _sessione(db)
    chat_engine._save_message(sid, "user", "domanda")
    chat_engine._save_message(sid, "assistant", "risposta", tokens_in=10, tokens_out=20)

    api_messages = [{"role": m["role"], "content": m["content"]}
                    for m in chat_engine.get_messages(sid)
                    if m["role"] in ("user", "assistant") and m.get("content")]
    assert api_messages == [{"role": "user", "content": "domanda"},
                            {"role": "assistant", "content": "risposta"}]


# ============================================================
# n.6 — cost_eur, campi cache, forma unica del `done`
# ============================================================

def test_done_ha_la_stessa_forma_su_tutte_le_uscite():
    """Le tre uscite anticipate emettevano un payload SENZA `ok`.

    Cioe' proprio quando qualcosa andava storto mancava la chiave che lo dice.
    Muta cosi' e diventa rosso: rimetti a mano il dict
    `{"tokens_in": 0, "tokens_out": 0, "session_id": sid}` su un'uscita.
    """
    anticipata = chat_engine._done_payload(7, ok=False)
    normale = chat_engine._done_payload(7, ok=True, model="claude-sonnet-5",
                                        tokens_in=100, tokens_out=50,
                                        cache_read=10, cache_write=5, iterations=2,
                                        usage_visto=True)
    assert set(anticipata) == set(normale)
    assert set(anticipata["cost_eur"]) == set(normale["cost_eur"])
    for chiave in ("ok", "session_id", "model", "tokens_in", "tokens_out",
                   "cache_read", "cache_write", "iterations", "cost_eur"):
        assert chiave in anticipata
    assert anticipata["ok"] is False and normale["ok"] is True


def test_ogni_uscita_done_passa_dallhelper():
    """Il test qui sopra prova che l'HELPER e' coerente; questo prova che le
    uscite lo USANO.

    Senza di lui la coppia era bucata: si poteva rimettere un dict a mano su
    un'uscita anticipata e la suite restava verde (misurato con la prova di
    mutazione il 26/07 sera-6 — il test sopra passava lo stesso). Il payload
    del `done` e' il pezzo che il frontend legge per sapere se e' andata bene e
    quanto e' costata: una forma diversa proprio sui rami d'errore e' il buco
    che la regola 14/07 vieta.
    """
    src = open(CHAT_ENGINE_PATH, encoding="utf-8").read()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_chat"][0]
    uscite = []
    for nodo in ast.walk(fn):
        if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "_format_sse"):
            continue
        if not (nodo.args and isinstance(nodo.args[0], ast.Constant)
                and nodo.args[0].value == "done"):
            continue
        uscite.append(nodo)
    assert len(uscite) >= 4, f"attese >=4 uscite `done` in stream_chat, trovate {len(uscite)}"
    for u in uscite:
        payload = u.args[1]
        # niente dict scritti a mano: e' esattamente la forma da cui veniamo
        assert not isinstance(payload, ast.Dict), (
            f"l'uscita `done` a riga {u.lineno} passa un dict LETTERALE: la forma "
            "puo' divergere dalle altre e `cost_eur` sparire proprio sul ramo "
            "d'errore, che e' quello che il frontend legge per capire cos'e' "
            "andato storto")
    # e ogni uscita deve avere il suo _done_payload (una e' via asyncio.to_thread,
    # perche' il calcolo del costo tocca la rete e non puo' stare sull'event loop)
    riferimenti = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Name) and n.id == "_done_payload"]
    assert len(riferimenti) == len(uscite), (
        f"{len(uscite)} uscite `done` ma {len(riferimenti)} riferimenti a "
        "_done_payload: qualcuna costruisce il payload per conto suo")


def test_i_campi_cache_alzano_il_costo_non_lo_abbassano():
    """Il cuore della n.6, e la correzione a una premessa del frontend.

    Loro avevano scritto che senza cache_read/cache_write il numero e' "un
    tetto". E' il contrario: `usage.input_tokens` esclude gia' i token cachati,
    quindi senza i due campi mancano sia la lettura (0,10x) sia la SCRITTURA di
    cache, che a TTL 1h costa 2,00x l'input. Il costo vero e' PIU ALTO.

    Muta cosi' e diventa rosso: smetti di passare cache_read/cache_write a
    `llm_pricing.cost_eur` -> i due numeri tornano uguali.
    """
    senza = chat_engine._costo_risposta("claude-sonnet-5", 12936, 1751, None, None)
    con = chat_engine._costo_risposta("claude-sonnet-5", 12936, 1751, 9000, 3000)
    # L4 09/09: il costo parziale non viene piu' spacciato per totale "ok".
    assert senza["status"] == "usage_unknown" and senza["cost"] is None
    assert con["status"] == "ok" and con["cost"] > 0
    zero_misurato = chat_engine._costo_risposta("claude-sonnet-5", 12936, 1751, 0, 0)
    assert con["cost"] > zero_misurato["cost"]
    assert senza["cache_fields"] == "assenti" and con["cache_fields"] == "ok"


def test_stream_morto_a_meta_non_dichiara_zero_euro():
    """Finding MEDIA della review pre-commit 26/07 sera-6.

    Se lo stream muore DOPO che il PM ha gia' visto uscire dei token ma prima di
    `get_final_message()`, i totali sono ancora a zero. Prima usciva
    `0,00 € status:"ok" cache_fields:"ok"` su una risposta che era gia' costata:
    un costo inventato, non un buco. Ora i token escono None e il costo lo dice.

    Muta cosi' e diventa rosso: togli il ramo `usage_visto` da _done_payload.
    """
    p = chat_engine._done_payload(1, ok=False, model="claude-sonnet-5",
                                  tokens_in=0, tokens_out=0,
                                  cache_read=0, cache_write=0, iterations=3,
                                  usage_visto=False)
    assert p["tokens_in"] is None and p["tokens_out"] is None
    assert p["cost_eur"]["cost"] is None
    assert "non_calcolato" in p["cost_eur"]["status"]


def test_stream_morto_a_iterazione_2_non_esce_status_ok():
    """B.12 del dossier 24 (ALTA, review 01/08). `usage_visto` e' sticky: se lo
    stream muore a iterazione >=2 i totali contano solo le iterazioni complete,
    ma il ramo 'costo VERO' usciva `status:"ok", cache_fields:"ok", nota:null`
    — e il contratto frontend (`if status==='ok'`) benediva un minimo come
    misura. Ora esce `parziale: ...`; il costo resta (la spesa nota e gia'
    avvenuta non si cancella) ma etichettato come minimo.

    Muta cosi' e diventa rosso: togli `stream_failed` dal ramo usage_visto
    di _done_payload.
    """
    p = chat_engine._done_payload(99, ok=False, model="claude-sonnet-5",
                                  tokens_in=12936, tokens_out=1751,
                                  cache_read=9000, cache_write=3000,
                                  iterations=2, usage_visto=True,
                                  stream_failed=True)
    c = p["cost_eur"]
    assert c["status"] != "ok" and c["status"].startswith("parziale")
    assert c["cost"] is not None and c["cost"] > 0  # minimo, non cancellato
    assert c["nota"] and "MINIMO" in c["nota"]
    # e l'esito pulito NON cambia forma:
    p2 = chat_engine._done_payload(99, ok=True, model="claude-sonnet-5",
                                   tokens_in=12936, tokens_out=1751,
                                   cache_read=9000, cache_write=3000,
                                   iterations=2, usage_visto=True)
    assert p2["cost_eur"]["status"] == "ok" and p2["cost_eur"]["nota"] is None


def test_tokens_parziali_non_si_archiviano_come_misura():
    """B.12, seconda meta': in chat_messages i totali parziali entravano come
    numeri (condizione su usage_visto, non su stream_failed) — un
    sotto-conteggio archiviato per sempre come misura. NULL = n.d. dichiarato,
    stessa convenzione gia' certificata per la morte pre-prima-iterazione."""
    assert chat_engine._tokens_da_archiviare(True, False, 100, 50) == (100, 50)
    assert chat_engine._tokens_da_archiviare(True, True, 100, 50) == (None, None)
    assert chat_engine._tokens_da_archiviare(False, False, 100, 50) == (None, None)
    assert chat_engine._tokens_da_archiviare(False, True, 100, 50) == (None, None)


def test_uscita_prima_di_chiamare_il_modello_e_zero_vero():
    """Zero speso davvero (agente sconosciuto / API key assente): lo zero e' un
    fatto, ma si etichetta invece di spacciarlo per misura — e non si tocca la
    rete per un FX che non serve a prezzare niente."""
    p = chat_engine._done_payload(1, ok=False, iterations=0, usage_visto=False)
    assert p["cost_eur"]["cost"] == 0.0
    assert p["cost_eur"]["status"].startswith("nessuna_chiamata")


def test_fx_non_disponibile_non_resta_status_ok(monkeypatch):
    """Finding ALTA della review: `llm_pricing.cost_eur` lascia status="ok"
    anche quando l'FX non si risolve e il costo esce None. Il frontend legge
    proprio `status` per decidere se rendere il numero.

    Muta cosi' e diventa rosso: togli la correzione dell'etichetta in
    `_costo_risposta`.
    """
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (None, "n.d."))
    llm_pricing.reset_fx_memo()
    c = chat_engine._costo_risposta("claude-sonnet-5", 100, 50, 10, 5)
    assert c["cost"] is None
    assert c["status"] != "ok", "costo assente ma etichettato 'ok': buco travestito da misura"
    assert "non_calcolato" in c["status"]


def test_il_costo_non_gira_sullevent_loop():
    """Il calcolo del costo tocca la rete (FX via yfinance). `stream_chat` e' un
    async generator servito da StreamingResponse: eseguirlo inline congela
    l'intero backend (0,55 s misurati a freddo, e quanto dura un hang di Yahoo).

    Muta cosi' e diventa rosso: rimetti `_done_payload(...)` inline al posto di
    `await asyncio.to_thread(_done_payload, ...)`.
    """
    src = open(CHAT_ENGINE_PATH, encoding="utf-8").read()
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_chat"][0]
    to_thread = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "to_thread"
                 and any(isinstance(a, ast.Name) and a.id == "_done_payload"
                         for a in n.args)]
    assert to_thread, ("il payload del `done` non passa piu' da asyncio.to_thread: "
                       "il fetch FX torna sull'event loop del backend")


def test_cache_assente_e_dichiarata_mai_uno_zero_zitto():
    """Campi mancanti = buco etichettato, non 0 spacciato per misura."""
    c = chat_engine._costo_risposta("claude-sonnet-5", 100, 50, None, None)
    assert c["cache_fields"] == "assenti"
    assert c["nota"] and "token parziali" in c["nota"]
    assert c["cost"] is None and c["status"] == "usage_unknown"


def test_modello_non_risolto_non_produce_un_costo_zero():
    """Uscita anticipata: nessun modello -> nessun costo, e lo dice."""
    c = chat_engine._costo_risposta(None, 0, 0, None, None)
    assert c["cost"] is None
    assert "non_calcolato" in c["status"]


def test_modello_fuori_listino_dichiarato():
    """`cost_eur` risponde model_unknown: va passato, non convertito in 0."""
    c = chat_engine._costo_risposta("modello-che-non-esiste", 100, 50, 0, 0)
    assert c["cost"] is None
    assert c["status"] == "model_unknown"


def test_ttl_della_cache_e_una_costante_sola():
    """ANTI-DERIVA. La richiesta ad Anthropic e il calcolo del costo devono
    leggere lo STESSO TTL.

    Se qualcuno riporta un letterale "1h"/"5m" dentro `_cc`, la richiesta e il
    prezzo possono divergere: a 5m la cache_write costa 1,25x invece di 2,00x e
    il costo in pagina mente senza che nessun errore lo segnali. Questo test
    guarda l'AST, non il comportamento, perche' la deriva e' proprio silenziosa.
    """
    src = open(CHAT_ENGINE_PATH, encoding="utf-8").read()
    albero = ast.parse(src)
    fn = [n for n in ast.walk(albero)
          if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_chat"][0]
    assegnazioni = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_cc" for t in n.targets)]
    assert assegnazioni, "non trovo piu' l'assegnazione di _cc in stream_chat"
    ttl = [kv for kv in assegnazioni[0].value.keys if getattr(kv, "value", None) == "ttl"]
    assert ttl, "_cc non porta piu' un 'ttl'"
    valore = assegnazioni[0].value.values[assegnazioni[0].value.keys.index(ttl[0])]
    assert isinstance(valore, ast.Name) and valore.id == "CHAT_CACHE_TTL", (
        "il TTL della cache in stream_chat e' tornato a essere un letterale: "
        "richiesta e costo possono divergere in silenzio")


# ============================================================
# n.1 — PATCH titolo
# ============================================================

@pytest.mark.parametrize("cattivo", ["", "   ", "\n\t "])
def test_titolo_vuoto_rifiutato(cattivo):
    with pytest.raises(ValueError):
        chat_engine.normalizza_titolo(cattivo)


def test_titolo_troppo_lungo_rifiutato():
    with pytest.raises(ValueError):
        chat_engine.normalizza_titolo("x" * (chat_engine.MAX_TITLE_CHARS + 1))
    # al tetto esatto passa: il confine e' quello dichiarato nel ponte
    assert len(chat_engine.normalizza_titolo("x" * chat_engine.MAX_TITLE_CHARS)) == \
        chat_engine.MAX_TITLE_CHARS


def test_titolo_normalizzato_su_una_riga():
    """Un titolo e' una riga: newline e tab collassano, o la lista si rompe."""
    assert chat_engine.normalizza_titolo("  BOJ\n\te   JPY  ") == "BOJ e JPY"


def test_update_title_scrive_e_ritorna_il_normalizzato(db):
    sid = _sessione(db)
    res = chat_engine.update_session_title(sid, "  ALFA:\n gamma  settimanale ")
    assert res == {"ok": True, "id": sid, "title": "ALFA: gamma settimanale"}
    assert chat_engine.get_session_info(sid)["title"] == "ALFA: gamma settimanale"


def test_update_title_sessione_inesistente_e_none(db):
    """None -> l'endpoint ne fa un 404. Mai un ok:true su un id che non c'e'."""
    assert chat_engine.update_session_title(999_999, "titolo") is None


def test_rinominare_non_e_attivita_last_activity_intatta(db):
    """LA cintura della retro-titolatura, e la piu' facile da rompere.

    `list_sessions` ordina per COALESCE(last_activity, started_at) DESC. Se
    l'UPDATE del titolo bussasse anche last_activity, riscrivere i 74 titoli
    storici rimescolerebbe l'INTERO archivio del PM in un colpo, portando in
    cima le chat piu' vecchie.

    Muta cosi' e diventa rosso: aggiungi `, last_activity=datetime('now')`
    all'UPDATE di update_session_title.
    """
    sid = _sessione(db)
    with db._conn() as conn:
        conn.execute("UPDATE chat_sessions SET last_activity=? WHERE id=?",
                     ("2020-01-01 00:00:00", sid))
    chat_engine.update_session_title(sid, "titolo nuovo")
    assert chat_engine.get_session_info(sid)["last_activity"] == "2020-01-01 00:00:00"


# ============================================================
# n.2 — retro-titolatura
# ============================================================

def test_le_6_correzioni_combaciano_ancora_col_json(corpus_correzioni_sintetico):
    """Le correzioni sono ancorate al titolo che ho AUDITATO a mano.

    Se il frontend rigenera `titles_haiku.json`, questo test diventa rosso
    PRIMA che lo script scriva in DB una correzione basata su un testo che non
    esiste piu'. E' la stessa idea del guard `atteso` dentro carica_piano().
    """
    import json
    import bellomberg.cli.retro_title_chats as rt
    with open(rt.JSON_TITOLI, encoding="utf-8") as f:
        dati = json.load(f)
    per_id = {int(s["id"]): (s.get("haiku") or "").strip() for s in dati["sessions"]}
    for sid, c in rt.correzioni().items():
        atteso = c["cosa_c_era"]
        assert sid in per_id, f"la sessione {sid} non e' piu' nel JSON"
        assert per_id[sid] == atteso.strip(), (
            f"#{sid}: il JSON e' cambiato, la correzione va ri-auditata")


@pytest.fixture
def corpus_correzioni_sintetico(tmp_path, monkeypatch):
    """Sei errori distinti e un'espansione valida: stessi vincoli, nessun dato PM."""
    import bellomberg.cli.retro_title_chats as rt
    from bellomberg.storage import negozi_privati
    errors = {34: "AVXL", 35: "AVXL", 55: "BPMBK", 61: "PSTH", 84: "MSBH"}
    sessions, corrections = [], {}
    for sid, wrong in errors.items():
        original = f"Titolo sintetico con {wrong}"
        sessions.append({"id": sid, "q": f"Domanda sintetica {sid}?", "haiku": original})
        corrections[str(sid)] = {"titolo": f"Verifica sintetica {sid}: simbolo corretto",
            "cosa_c_era": original, "perche": "Refuso sintetico", "deve_contenere": ["simbolo corretto"]}
    sessions.extend([
        {"id": 63, "q": "La relazione sintetica è confermata?", "haiku": "Esito negativo con variabile X"},
        {"id": 90, "q": "Cosa indica il prodotto interno lordo?", "haiku": "PIL: prodotto interno lordo"},
    ])
    corrections["63"] = {"titolo": "La relazione sintetica è confermata?",
        "cosa_c_era": "Esito negativo con variabile X", "perche": "Mantieni la domanda sintetica", "deve_contenere": ["?"]}
    titles = tmp_path / "synthetic_titles.json"
    titles.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    shop = tmp_path / "synthetic_corrections.json"
    shop.write_text(json.dumps({"correzioni": corrections, "intatti": {"90": ["PIL", "prodotto interno lordo"]}}), encoding="utf-8")
    monkeypatch.setattr(rt, "JSON_TITOLI", str(titles))
    monkeypatch.setattr(rt, "ATTESI", len(sessions))
    monkeypatch.setattr(negozi_privati, "PERCORSO_CORREZIONI_TITOLI", str(shop))
    return titles, shop


@pytest.fixture
def piano_titoli_sintetico(tmp_path, monkeypatch):
    """Piano minimo pubblico per provare le cinture, senza dati del PM."""
    import bellomberg.cli.retro_title_chats as rt
    domande = {
        1: "Quali notizie contano oggi?",
        2: "Come valuti il rischio crypto?",
        4: "Quale rischio politico osservi?",
        12: "Come cambia il VaR?",
    }
    sessions = [
        {"id": 1, "q": domande[1], "haiku": "Notizie che contano oggi"},
        {"id": 2, "q": domande[2], "haiku": "Rischio crypto"},
        {"id": 4, "q": domande[4], "haiku": "Rischio politico"},
        {"id": 12, "q": domande[12], "haiku": "Variazione del VaR"},
    ]
    percorso = tmp_path / "titles_sintetici.json"
    percorso.write_text(json.dumps({"sessions": sessions}), encoding="utf-8")
    monkeypatch.setattr(rt, "JSON_TITOLI", str(percorso))
    monkeypatch.setattr(rt, "ATTESI", len(sessions))
    correzioni = {
        2: {
            "titolo": "Rischio crypto corretto",
            "cosa_c_era": "Rischio crypto",
            "perche": "correzione sintetica di test",
            "deve_contenere": ["crypto"],
        },
    }
    monkeypatch.setattr(rt, "correzioni", lambda: correzioni)
    return rt, domande


def test_nessun_titolo_pianificato_viola_il_contratto_endpoint(
        piano_titoli_sintetico):
    """Cio' che lo script scrive dev'essere accettabile anche dal PATCH.

    Altrimenti nascono in DB titoli che il PM non puo' piu' rinominare a mano
    senza prendersi un 400 — e se ne accorgerebbe mesi dopo.
    """
    rt, _domande = piano_titoli_sintetico
    piano, _scarti = rt.carica_piano()
    assert len(piano) == rt.ATTESI, f"attesi {rt.ATTESI} titoli, trovati {len(piano)}"
    assert {sid: (titolo, origine) for sid, titolo, origine, _q in piano}[2] == (
        "Rischio crypto corretto", "CORRETTO A MANO")
    for sid, titolo, _origine, _q in piano:
        assert chat_engine.normalizza_titolo(titolo) == titolo
        assert len(titolo) <= chat_engine.MAX_TITLE_CHARS


def test_nessun_titolo_pianificato_contiene_i_ticker_inventati(corpus_correzioni_sintetico):
    """I ticker che il PM non ha mai scritto, e che NON esistono o sono
    sbagliati, non devono tornare. Muta cosi' e diventa rosso: togli una voce
    da CORREZIONI."""
    import re
    import bellomberg.cli.retro_title_chats as rt
    piano, _ = rt.carica_piano()
    testo = {sid: t for sid, t, _o, _q in piano}
    for sid, vietato in ((34, "AVXL"), (35, "AVXL"), (55, "BPMBK"),
                         (61, "PSTH"), (84, "MSBH")):
        assert not re.search(rf"\b{vietato}\b", testo[sid]), (
            f"#{sid}: il ticker inventato {vietato} e' tornato nel piano")


def test_il_titolo_63_non_afferma_cio_che_la_chat_ha_smentito(corpus_correzioni_sintetico):
    """Il caso piu' grave del lotto: la sessione 63 chiedeva una VERIFICA
    ('...giusto?') e il desk ha risposto 'No, la logica e' invertita'.

    Il titolo di Haiku la trasformava in un'affermazione finanziaria falsa,
    scritta per sempre in archivio. Il titolo nuovo deve restare interrogativo.
    """
    import bellomberg.cli.retro_title_chats as rt
    piano, _ = rt.carica_piano()
    t = {sid: x for sid, x, _o, _q in piano}[63]
    assert t.endswith("?"), f"il titolo di #63 non e' piu' una domanda: {t!r}"
    assert "negativo con" not in t.lower(), "e' tornata l'affermazione smentita"


def test_ogni_correzione_contiene_le_parole_che_il_pm_ha_chiesto(corpus_correzioni_sintetico):
    """Il caso #47 del 26/07: il PM scrive un simbolo sbagliato e due messaggi dopo si
    corregge; la prima stesura della correzione cristallizzava il refuso. Oggi ogni
    correzione dichiara nel negozio privato le parole che il titolo corretto DEVE avere
    (`deve_contenere`), e qui si misura che le abbia davvero."""
    import bellomberg.cli.retro_title_chats as rt
    piano, _ = rt.carica_piano()
    t = {sid: x for sid, x, _o, _q in piano}
    con_vincolo = {sid: c for sid, c in rt.correzioni().items() if c["deve_contenere"]}
    assert con_vincolo, "nessuna correzione con deve_contenere: il vincolo del PM non e' nel negozio"
    for sid, c in con_vincolo.items():
        for parola in c["deve_contenere"]:
            assert parola in t[sid], f"#{sid} non contiene {parola!r}: {t[sid]!r}"


def test_le_espansioni_corrette_restano_intatte(corpus_correzioni_sintetico):
    """Decisione PM 26/07 sera-6 (opzione B): si correggono i ticker SBAGLIATI
    o inventati, non le abbreviazioni giuste verso ticker veri. Gli id `intatti` del
    negozio portano le parole che il titolo generato deve conservare.

    Muta cosi' e diventa rosso: sposta un id da `intatti` a `correzioni` senza passare
    dal PM — la regola e' sua, non mia.
    """
    import bellomberg.cli.retro_title_chats as rt
    from bellomberg.storage.negozi_privati import carica_correzioni_titoli
    piano, _ = rt.carica_piano()
    t = {sid: x for sid, x, _o, _q in piano}
    n = carica_correzioni_titoli()
    assert n["intatti"], "nessun id intatto nel negozio: la regola del PM non e' misurabile"
    corr = rt.correzioni()
    for sid, parole in n["intatti"].items():
        assert sid not in corr, f"#{sid} e' fra le correzioni: la regola del PM e' violata"
        for parola in parole:
            assert parola in t[sid], f"#{sid} ha perso {parola!r}: {t[sid]!r}"


def _mini_db(tmp_path, righe):
    """DB finto con le due tabelle che servono. `righe` = [(id, sp, title, domanda)]."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, specialist TEXT,
                    title TEXT, started_at TEXT, last_activity TEXT)""")
    conn.execute("""CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id INTEGER,
                    role TEXT, content TEXT, tokens_in INTEGER, tokens_out INTEGER,
                    timestamp TEXT)""")
    for i, (sid, sp, title, domanda) in enumerate(righe, start=1):
        conn.execute("INSERT INTO chat_sessions VALUES (?,?,?,'x','x')", (sid, sp, title))
        if domanda is not None:
            conn.execute("INSERT INTO chat_messages VALUES (?,?,'user',?,NULL,NULL,'x')",
                         (i, sid, domanda))
    conn.commit()
    conn.close()
    return db_path


def _titoli(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT id, title FROM chat_sessions").fetchall())
    finally:
        conn.close()


def _senza_cinture(monkeypatch):
    import bellomberg.cli.retro_title_chats as rt
    monkeypatch.setattr(rt, "_porta_8765_occupata", lambda: False)
    monkeypatch.setattr(rt, "_backup", lambda p: "(backup finto nei test)")


def test_retro_non_sovrascrive_una_rinomina_manuale(
        tmp_path, monkeypatch, piano_titoli_sintetico):
    """Cintura 3: si toccano SOLO i titoli generici, per UGUAGLIANZA esatta.

    Il caso `#3` e' quello che la prima stesura sbagliava: `startswith("Chat con ")`
    avrebbe cancellato senza traccia un titolo scritto dal PM che comincia per
    caso con quelle parole (finding MEDIA della review pre-commit).
    """
    rt, domande = piano_titoli_sintetico
    db_path = _mini_db(tmp_path, [
        (1, "news", "Chat con News", domande[1]),          # generico STORICO
        (2, "crypto", "IL MIO TITOLO", domande[2]),        # rinominata a mano
        (12, "macro", "Chat con Macro sul VaR", domande[12]),  # prefisso ingannevole
    ])
    _senza_cinture(monkeypatch)
    rt.main(apply=True, db_path=db_path)
    t = _titoli(db_path)
    assert t[1] != "Chat con News", "la generica storica doveva essere titolata"
    assert t[2] == "IL MIO TITOLO", "una rinomina manuale e' stata sovrascritta"
    assert t[12] == "Chat con Macro sul VaR", \
        "un titolo del PM che INIZIA per 'Chat con' e' stato cancellato"


def test_le_chat_storiche_dei_desk_rinominati_sono_incluse(
        tmp_path, monkeypatch, piano_titoli_sintetico):
    """news e politics oggi si chiamano '(legacy)': le loro 16 chat vecchie
    portano ancora il titolo generico PRE-rinomina.

    Muta cosi' e diventa rosso: svuota GENERICI_STORICI — le 16 sessioni che il
    PM non riesce nemmeno a raggiungere dalla UI resterebbero senza titolo, che
    e' esattamente il contrario dello scopo del lotto.
    """
    rt, domande = piano_titoli_sintetico
    assert chat_engine.AGENT_DISPLAY_NAMES["news"] == "News (legacy)"
    assert chat_engine.AGENT_DISPLAY_NAMES["politics"] == "Politics (legacy)"
    db_path = _mini_db(tmp_path, [(1, "news", "Chat con News", domande[1]),
                                  (4, "politics", "Chat con Politics", domande[4])])
    _senza_cinture(monkeypatch)
    rt.main(apply=True, db_path=db_path)
    t = _titoli(db_path)
    assert t[1] != "Chat con News" and t[4] != "Chat con Politics"


def test_identita_non_provata_niente_scrittura(
        tmp_path, monkeypatch, piano_titoli_sintetico):
    """Cintura 5: il JSON e' una foto. Se la sessione non contiene piu' la
    domanda da cui quel titolo e' nato, non le si scrive addosso.

    E' la cura del difetto documentato in fix_decisions_eur.py:43 (scrivere
    sull'oggetto sbagliato). Muta cosi' e diventa rosso: togli il confronto
    fra `q_db` e la domanda del piano.
    """
    rt, domande = piano_titoli_sintetico
    db_path = _mini_db(tmp_path, [
        (1, "news", "Chat con News", "una domanda COMPLETAMENTE diversa"),
        (2, "crypto", "Chat con Crypto", domande[2]),
    ])
    _senza_cinture(monkeypatch)
    rt.main(apply=True, db_path=db_path)
    t = _titoli(db_path)
    assert t[1] == "Chat con News", "titolo scritto su una sessione non verificata"
    assert t[2] != "Chat con Crypto", "quella verificata doveva essere titolata"


def test_il_backup_e_wal_safe(tmp_path):
    """ALTA della review pre-commit, e una lezione gia' scritta 4 volte nel repo.

    `shutil.copy2` copia solo il `.db` e lascia indietro il `-wal`: su un DB in
    WAL con transazioni committate ma non checkpointate, il "backup riuscito"
    perde righe. Qui si costruisce esattamente quello scenario e si verifica che
    lo snapshot le contenga tutte.

    Muta cosi' e diventa rosso: rimetti `shutil.copy2(db_path, dst)` in _backup.
    """
    import bellomberg.cli.retro_title_chats as rt
    db_path = str(tmp_path / "wal.db")
    src = sqlite3.connect(db_path)
    src.execute("PRAGMA journal_mode=wal")
    src.execute("CREATE TABLE t (v INTEGER)")
    src.commit()
    src.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    src.commit()                    # committato, ma vive nel -wal
    assert os.path.exists(db_path + "-wal"), "scenario non riprodotto: manca il -wal"

    monkeypatch_dir = tmp_path / "backups"
    rt_backup_dir = rt.BACKUP_DIR
    rt.BACKUP_DIR = str(monkeypatch_dir)
    try:
        dst = rt._backup(db_path)
    finally:
        rt.BACKUP_DIR = rt_backup_dir
        src.close()

    conn = sqlite3.connect(dst)
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50, \
            "il backup ha perso le righe che stavano nel -wal"
    finally:
        conn.close()


def test_retro_si_ferma_se_la_porta_8765_e_occupata(
        tmp_path, monkeypatch, piano_titoli_sintetico):
    """Backend vivo = DB lockato (lezione CLAUDE.md, run perse). Fail-closed."""
    rt, _domande = piano_titoli_sintetico
    db_path = str(tmp_path / "test.db")
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(rt, "_porta_8765_occupata", lambda: True)
    def _mai(_p):
        raise AssertionError("backup (e quindi scrittura) tentato a porta occupata")
    monkeypatch.setattr(rt, "_backup", _mai)
    assert rt.main(apply=True, db_path=db_path) == 2
