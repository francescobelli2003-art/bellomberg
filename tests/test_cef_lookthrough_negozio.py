# -*- coding: utf-8 -*-
"""Il look-through CEF legge i gestori dal NEGOZIO, e il blocco del Capo esiste davvero.

Perche' questa batteria nasce (05/09, chat e3, Opus 5): il lotto 5a (`bc905de`) ha
tolto la costante `CEF_MANAGERS` da `cef_lookthrough.py` portando i gestori nel negozio
privato `data/istituzioni.json`, ma `capo_block()` continuava a usarla: due usi, zero
definizioni. Risultato misurato: `capo_block()` sollevava `NameError` a OGNI chiamata e
l'`except` di `capo.py:545-554` lo inghiottiva — il blocco look-through CEF (sconto sul
NAV + 13F del gestore, finding #1a della run #45) NON entrava piu' nel memo del PM, e la
run lo diceva solo nel log.

Nessuna batteria l'aveva visto perche' TUTTI i test che nominano `capo_block` lo
sostituiscono con `lambda: ""` (`test_capo_collasso`, `test_capo_streaming`,
`test_cash_source_dichiarata`, `test_mandato_pm`, `test_red_team_assente_dichiarato`) e
`test_negozi_privati.py:470` asserisce solo che il LETTERALE sia sparito dal sorgente —
cioe' passa proprio mentre il codice e' rotto. Qui la funzione VERA viene eseguita, e
c'e' la guardia di classe (`test_nessun_nome_globale_non_definito`) che avrebbe preso il
difetto il giorno stesso, senza sapere niente di CEF.

Simboli e nomi sono INVENTATI e verificati contro basi E nomi del DB con le funzioni del
cancello (`verifica_pubblico.ticker_db` / `nomi_db`): 0 collisioni.
"""
import builtins
import json
import os
import sqlite3
import symtable

import pytest

from bellomberg.valuation import cef_lookthrough
from bellomberg.storage import negozi_privati


# --- il negozio finto: due fondi chiusi inventati, forma del negozio vero ----------
NEGOZIO = {
    "cik": {"zenon_capital": "0000000101", "quill_asset": "0000000102"},
    "cef": {
        "ZENON.L": {"investor": "zenon_capital", "manager": "Zenon Capital Management",
                    "not_in_13f": "partecipazioni non quotate negli USA"},
        "QUILL.L": {"investor": "quill_asset", "manager": "Quill Asset Management"},
    },
}


def _scrivi_negozio(tmp_path, monkeypatch, contenuto=NEGOZIO, testo=None):
    """Negozio delle istituzioni su disco + il percorso che il modulo rilegge."""
    p = tmp_path / "istituzioni.json"
    p.write_text(json.dumps(contenuto) if testo is None else testo, encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ISTITUZIONI", str(p))
    return str(p)


def _db_con_posizioni(tmp_path, monkeypatch, tickers):
    """DB finto con le sole colonne che `capo_block` legge (ticker, is_active)."""
    from bellomberg.storage import memory_db
    db = tmp_path / "finto.db"
    cx = sqlite3.connect(str(db))
    cx.execute("CREATE TABLE positions (ticker TEXT, is_active INTEGER)")
    cx.executemany("INSERT INTO positions VALUES (?, 1)", [(t,) for t in tickers])
    cx.commit()
    cx.close()
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(db))
    return str(db)


@pytest.fixture(autouse=True)
def _cache_pulita(monkeypatch):
    """La cache del modulo parte pulita a ogni test: un esito non viaggia fra i test."""
    monkeypatch.setattr(cef_lookthrough, "_CACHE", {})


@pytest.fixture
def look_finto(monkeypatch):
    """`get_lookthrough` interroga NAV e SEC: dove serve solo il BLOCCO, il
    look-through e' finto. NON e' autouse: i due test sul messaggio di copertura
    devono esercitare la funzione VERA — e `monkeypatch.undo()` qui smonterebbe
    anche le fixture di produzione del conftest (stessa istanza function-scoped)."""
    monkeypatch.setattr(cef_lookthrough, "get_lookthrough",
                        lambda tkr, max_holdings=12: {
                            "ticker": tkr, "manager": "gestore finto",
                            "nav": {"discount_to_nav_pct": -12.5,
                                    "nav_per_share_usd": 42.0, "nav_as_of": "2026-09-01"},
                            "holdings_13f": {"top_holdings": [], "error": "13F finto"},
                        })


# --- 1. la funzione VERA gira e usa il negozio -------------------------------------
def test_capo_block_gira_e_prende_i_fondi_dal_negozio(tmp_path, monkeypatch, look_finto):
    """Il difetto vero: oggi solleva NameError. Il blocco deve esistere e nominare
    SOLO i fondi del negozio che sono anche in portafoglio."""
    _scrivi_negozio(tmp_path, monkeypatch)
    _db_con_posizioni(tmp_path, monkeypatch, ["ZENON.L", "ALTRO.MI"])

    blocco = cef_lookthrough.capo_block()

    assert "LOOK-THROUGH CEF" in blocco
    assert "ZENON.L" in blocco
    # QUILL.L e' nel negozio ma NON in portafoglio: non deve entrare nel prompt
    assert "QUILL.L" not in blocco


def test_capo_block_senza_cef_in_book_resta_vuoto(tmp_path, monkeypatch, look_finto):
    """Contratto invariato: nessun fondo del negozio in portafoglio -> stringa vuota,
    e la dichiarazione la fa il chiamante (capo.py:551). Non e' un buco, e' un fatto."""
    _scrivi_negozio(tmp_path, monkeypatch)
    _db_con_posizioni(tmp_path, monkeypatch, ["ALTRO.MI"])

    assert cef_lookthrough.capo_block() == ""


# --- 2. i buchi si DICHIARANO al modello, non solo al log (regola PM 14/07) ---------
def test_capo_block_negozio_assente_lo_dichiara(tmp_path, monkeypatch, look_finto):
    """Negozio assente: il Capo deve SAPERE che il look-through non e' calcolabile.
    Una stringa vuota qui sarebbe un ripiego muto (il modello non distingue
    «nessun CEF in book» da «non ho potuto guardare»)."""
    monkeypatch.setattr(negozi_privati, "PERCORSO_ISTITUZIONI",
                        str(tmp_path / "non_esiste.json"))
    _db_con_posizioni(tmp_path, monkeypatch, ["ZENON.L"])

    blocco = cef_lookthrough.capo_block()

    assert blocco, "il negozio assente non puo' uscire come blocco vuoto"
    assert "ASSENTE" in blocco.upper()
    assert "negozio" in blocco.lower()
    # il MOTIVO del caricatore viaggia fino al modello: dice che il file non c'e' e da
    # quale esempio si parte per farlo (non il percorso del tmp, che e' del test)
    assert "non trovato" in blocco
    assert "istituzioni.example.json" in blocco


def test_capo_block_negozio_illeggibile_lo_dichiara(tmp_path, monkeypatch, look_finto):
    """Negozio malformato: stesso trattamento, con l'origine giusta. Mezzo negozio
    caricato sarebbe un ripiego muto (negozi_privati.carica)."""
    _scrivi_negozio(tmp_path, monkeypatch, testo="{non e' json")
    _db_con_posizioni(tmp_path, monkeypatch, ["ZENON.L"])

    blocco = cef_lookthrough.capo_block()

    assert blocco
    assert "ILLEGGIBILE" in blocco.upper()
    assert "ASSENTE" not in blocco.upper(), "le due ignoranze non si confondono"


def test_capo_block_db_non_leggibile_usa_il_negozio_e_lo_dichiara(tmp_path, monkeypatch, look_finto):
    """Il ramo di ripiego (DB non leggibile) era IRRAGGIUNGIBILE: la riga che lo
    apriva sollevava prima. Deve mostrare i fondi del negozio DICHIARANDO che non
    sono confermati in portafoglio."""
    from bellomberg.storage import memory_db
    _scrivi_negozio(tmp_path, monkeypatch)
    monkeypatch.setattr(memory_db, "SQLITE_PATH", str(tmp_path / "nessun.db"))

    blocco = cef_lookthrough.capo_block()

    assert "book non leggibile dal DB" in blocco
    # senza conferma dal book si mostrano TUTTI i fondi configurati
    assert "ZENON.L" in blocco and "QUILL.L" in blocco


# --- 3. il messaggio della copertura CONTA, non elenca (lotto 2b) ------------------
def test_errore_di_copertura_conta_invece_di_elencare(tmp_path, monkeypatch):
    """`get_lookthrough` su un simbolo non configurato elencava i CEF del PM, e
    quell'errore esce sia nel payload del tool sia nel prompt del Capo. Deve dire
    QUANTI sono e come si estende la copertura, non QUALI."""
    _scrivi_negozio(tmp_path, monkeypatch)

    esito = cef_lookthrough.get_lookthrough("IGNOTO.X")

    assert "error" in esito
    msg = esito["error"]
    assert "2 fondi chiusi" in msg
    assert "ZENON" not in msg and "QUILL" not in msg
    assert "istituzioni.json" in msg


def test_errore_di_copertura_negozio_assente_dichiarato(tmp_path, monkeypatch):
    """Contratto gia' in casa dal lotto 5a: si conserva."""
    monkeypatch.setattr(negozi_privati, "PERCORSO_ISTITUZIONI",
                        str(tmp_path / "non_esiste.json"))

    esito = cef_lookthrough.get_lookthrough("ZENON.L")

    assert "assente" in esito["error"]


# --- 4. la guardia di CLASSE: nessun nome globale senza definizione -----------------
def test_nessun_nome_globale_non_definito():
    """Il difetto di oggi in forma generale: una costante rimossa e un uso rimasto.
    `symtable` dice quali nomi il modulo LEGGE dallo scope globale senza mai
    assegnarli; se non stanno fra i globali del modulo ne' fra i builtin, quella
    riga solleva NameError appena viene eseguita."""
    percorso = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "bellomberg", "valuation", "cef_lookthrough.py")
    with open(percorso, encoding="utf-8") as fh:
        sorgente = fh.read()

    tavola = symtable.symtable(sorgente, "cef_lookthrough.py", "exec")
    # «definito» = legato a livello di modulo (assegnato O importato — un `import x`
    # risulta `is_assigned() False`, anche dentro il blocco `__main__`), piu' cio' che
    # esiste davvero dopo l'import e i builtin.
    definiti = set(dir(builtins)) | set(vars(cef_lookthrough))
    definiti |= {s.get_name() for s in tavola.get_symbols()
                 if s.is_assigned() or s.is_imported()}

    def liberi(t, dove):
        for s in t.get_symbols():
            if (s.is_global() and not s.is_assigned() and not s.is_imported()
                    and s.get_name() not in definiti):
                yield "%s -> %s" % (dove, s.get_name())
        for figlio in t.get_children():
            yield from liberi(figlio, "%s.%s" % (dove, figlio.get_name()))

    mancanti = sorted(liberi(tavola, "cef_lookthrough"))
    assert not mancanti, ("nomi usati e mai definiti (NameError garantito quando la "
                          "riga viene eseguita): " + "; ".join(mancanti))


# --- 5. il CABLAGGIO: il blocco arriva davvero nel prompt del Capo -----------------
def _capo_con_blocco(monkeypatch, blocco):
    """Fa girare `capo.run_capo` con modello, fatti e segnali finti, e rende i
    kwargs delle chiamate al modello. `blocco` e' cio' che `capo_block` fa: una
    stringa da rendere, oppure un'eccezione da sollevare."""
    from types import SimpleNamespace

    from bellomberg.agents import capo
    from bellomberg.core import current_facts
    from bellomberg.portfolio import signal_engine

    chiamate = []

    class _Stream:
        def __init__(self, testo):
            self.testo = testo

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            return iter([self.testo])

        def get_final_message(self):
            return SimpleNamespace(
                content=[SimpleNamespace(text=self.testo, type="text")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    class _Messages:
        def create(self, **kw):
            raise AssertionError("il Capo deve restare in streaming")

        def stream(self, **kw):
            chiamate.append(kw)
            return _Stream("SINTESI ESECUTIVA\nmemo finto per il cablaggio.")

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages()

    def _blocco():
        if isinstance(blocco, Exception):
            raise blocco
        return blocco

    monkeypatch.setattr(capo, "OpenRouterClient", _Client)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    monkeypatch.setattr(cef_lookthrough, "capo_block", _blocco)

    capo.run_capo(SimpleNamespace(data={"macro": {2: "report macro finto"}}))

    assert chiamate, "il Capo non ha chiamato il modello"
    return str(chiamate[0].get("messages"))


def test_il_blocco_arriva_nel_prompt_del_capo(monkeypatch):
    """Il test che mancava: non prova la funzione, prova la RIGA CHE COLLEGA.
    Cancella `user_msg_parts.append(_cef)` in capo.py e questo test cade; con
    `capo_block` monkeypatchato a "" (come fanno tutte le altre batterie) non
    cadrebbe mai."""
    marcatore = "=== BLOCCO CEF DI PROVA (marcatore) ==="

    inviato = _capo_con_blocco(monkeypatch, marcatore)

    assert marcatore in inviato, ("il blocco look-through CEF non arriva al modello: "
                                 "la riga che lo appende in capo.py non c'e' piu'")


def test_il_guasto_del_look_through_e_dichiarato_nel_memo(monkeypatch):
    """Ordine del PM (05/09): l'`except` di capo.py non deve piu' INGHIOTTIRE. Se il
    look-through fallisce — com'e' successo per mesi... anzi, per una sera: il
    `NameError` della costante rimossa — il modello deve SAPERE che quel dato manca,
    altrimenti legge l'assenza come «nessun fondo chiuso da guardare»."""
    inviato = _capo_con_blocco(monkeypatch, RuntimeError("fonte NAV giu'"))

    assert "LOOK-THROUGH CEF" in inviato
    assert "NON DISPONIBILE" in inviato
    assert "RuntimeError" in inviato and "fonte NAV giu'" in inviato
    # e la run non muore: il memo esce lo stesso, col buco dichiarato
    assert "dedurne niente" in inviato
