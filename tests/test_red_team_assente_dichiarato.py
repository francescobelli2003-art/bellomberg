"""Il memo del Capo dichiara quando il RED TEAM e' ASSENTE (voce A, 25/08, Fable 5).

Misurato sul cablaggio vero (client finto, run_capo VERO) prima della cura:
con `_red_team` assente dalla blackboard il user_msg conteneva ZERO menzioni
del red team, mentre la regola fissa 4 del system prompt («RISPONDI AL RED
TEAM: ... Decidere ignorando le obiezioni invalida il memo») resta in vigore:
il Capo scriveva il memo senza contraddittorio, senza saperlo, e con una
regola che lo spinge a citare obiezioni che non esistono.

L'assenza non e' teorica: `consigliere_multi` importa `run_red_team` in un
try/except che logga «[!] Red team skipped» e PROSEGUE. (Il memo di RESCUE
invece la critica di solito CE L'HA: `_red_team` viene persistito
incondizionatamente — base.py, «regenerate_memo lo richiede» — e
regenerate_memo rimonta TUTTI i report; resta senza critica solo se la run e'
morta PRIMA del red team o se il save DB e' fallito. La prima stesura di
questa docstring affermava il contrario su un grep circolare — review 25/08.)

E il red team non scrive MAI vuoto: quando non produce critica lascia un
SEGNAPOSTO dichiarato («[RED TEAM: nessuna critica prodotta...»), e un rifiuto
dei safeguard e' un blocco unico di testo. Prima della cura quei testi
finivano nel ramo «un risk manager ha attaccato le tesi» — l'affermazione
opposta a quella scritta nel testo stesso (review 25/08, CRITICO).

Idioma: client Anthropic FINTO di test_capo_collasso. Zero rete, zero DB.
"""
from types import SimpleNamespace

from bellomberg.agents import capo
from bellomberg.valuation import cef_lookthrough
from bellomberg.core import current_facts
from bellomberg.portfolio import signal_engine

MEMO_VERO = ("## SINTESI ESECUTIVA\nIl comitato conferma il posizionamento. " +
             "Analisi e numeri dai tool, buchi dichiarati n.d. " * 80)
CRITICA = ("CRITICA FINTA DEL ROUND 1: la tesi su TCK0.MI ignora il rischio "
           "di rifinanziamento; il sizing di TCK3.MI non regge lo stress test.")


def _msg(testo):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=testo)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=100, output_tokens=100),
    )


class _StreamFinto:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


def _prepara(monkeypatch):
    """Client finto + moduli contorno stubbati. Ritorna le chiamate catturate."""
    chiamate = []

    class _Messages:
        def create(self, **kw):
            raise AssertionError("il Capo deve restare in STREAMING (voce 1, 01/08)")

        def stream(self, **kw):
            chiamate.append(kw)
            return _StreamFinto(_msg(MEMO_VERO))

    class _AnthropicFinto:
        def __init__(self, **kw):
            self.messages = _Messages()

    monkeypatch.setattr(capo, "OpenRouterClient", _AnthropicFinto)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    return chiamate


def _user_msg(chiamate):
    return chiamate[0]["messages"][0]["content"]


def test_red_team_assente_il_prompt_lo_dichiara(monkeypatch):
    """Blackboard senza `_red_team` (import saltato, run morta, memo rigenerato):
    il Capo deve LEGGERE che il contraddittorio e' mancato — non dedurre dal
    silenzio che le tesi siano passate indenni dall'attacco."""
    chiamate = _prepara(monkeypatch)
    capo.run_capo(SimpleNamespace(data={"macro": {2: "report macro finto"}}))

    um = _user_msg(chiamate)
    assert "RED TEAM — ASSENTE in questa run" in um
    # le tre frasi che cambiano il memo: niente validazione dedotta, niente
    # obiezioni inventate (la regola fissa RISPONDI AL RED TEAM va disinnescata
    # per nome), e l'assenza va DICHIARATA al PM nel memo — frase ESATTA, non
    # ancore deboli tipo "dichiara" che matchano anche "dichiarati" (review).
    assert "non dedurne che siano state validate" in um.lower()
    assert "RISPONDI AL RED TEAM" in um and "non si applica" in um
    assert "il contraddittorio e' mancato" in um
    # anti-invecchiamento: la regola citata per nome deve ESISTERE nelle regole
    # fisse — se un giorno viene rinominata, il disinnesco punterebbe nel vuoto
    # e la regola rinominata resterebbe armata (review 25/08).
    assert "RISPONDI AL RED TEAM" in capo.CAPO_SYSTEM_PROMPT


def test_red_team_presente_critica_iniettata_e_niente_riga_assente(monkeypatch):
    """Col round piu' recente della critica in blackboard il blocco di sempre
    resta intatto — e la riga d'assenza NON compare."""
    chiamate = _prepara(monkeypatch)
    capo.run_capo(SimpleNamespace(data={
        "macro": {2: "report macro finto"},
        "_red_team": {1: "critica vecchia del round 0", 2: CRITICA},
    }))

    um = _user_msg(chiamate)
    assert "RED TEAM — CRITICA AVVERSARIALE ALLE TESI" in um
    assert CRITICA in um                       # l'ULTIMO round, non il primo
    assert "critica vecchia del round 0" not in um
    assert "RED TEAM — ASSENTE" not in um


def test_critica_vuota_conta_come_assente(monkeypatch):
    """`_red_team` presente ma senza testo (dict vuoto o report di soli spazi):
    per il Capo e' la stessa cecita' di una chiave mancante, e si dichiara."""
    for rt in ({}, {1: "   "}, {1: ""}):
        chiamate = _prepara(monkeypatch)
        capo.run_capo(SimpleNamespace(data={"macro": {2: "report"}, "_red_team": rt}))
        um = _user_msg(chiamate)
        assert "RED TEAM — ASSENTE in questa run" in um, "rt=%r" % (rt,)
        assert "RED TEAM — CRITICA AVVERSARIALE" not in um, "rt=%r" % (rt,)


def test_guasto_di_lettura_dichiara_assenza_col_motivo(monkeypatch):
    """Se leggere la blackboard ESPLODE, il memo non deve ne' morire ne'
    tacere: prima c'era un `except Exception: pass` che ingoiava tutto.
    E la verita' di questo stato e' «non SO se una critica esista», non
    «non c'e'»: header distinto (NON DISPONIBILE, come l'edge scan guasto),
    mai l'affermazione forte del ramo ASSENTE (review 25/08)."""

    class _DataGuasta(dict):
        def get(self, k, *a):
            if k == "_red_team":
                raise RuntimeError("blackboard corrotta (finta)")
            return dict.get(self, k, *a)

    chiamate = _prepara(monkeypatch)
    final, usage = capo.run_capo(
        SimpleNamespace(data=_DataGuasta({"macro": {2: "report"}})))

    um = _user_msg(chiamate)
    assert "RED TEAM — NON DISPONIBILE in questa run" in um
    assert "RuntimeError" in um and "blackboard corrotta" in um
    assert "non so se una critica esista" in um.lower()
    assert "RED TEAM — ASSENTE" not in um      # l'affermazione forte sarebbe falsa
    assert "SINTESI ESECUTIVA" in final        # il memo e' uscito lo stesso


SEGNAPOSTO = ("[RED TEAM: nessuna critica prodotta (limite iterazioni o "
              "risposta vuota) — buco dichiarato]")
RIFIUTO = ("[[RED_TEAM] RIFIUTO DEL MODELLO (categoria: n.d.): la richiesta "
           "e' stata declinata dai safeguard del modello, NON e' un errore "
           "di rete e NON e' un output vuoto. Il contenuto di questo blocco "
           "e' assente per rifiuto, non per mancanza di dati.]")


def test_segnaposto_rifiuto_ed_error_contano_come_assenti(monkeypatch):
    """Il red team non scrive MAI vuoto: il «vuoto» reale della produzione e'
    il segnaposto (red_team.py), il rifiuto safeguard (llm_refusal) o un
    testo [ERROR...] (che la blackboard persiste per contratto:
    test_placeholder_non_finale). Sono contraddittori MANCATI travestiti:
    dirli «un risk manager ha attaccato le tesi» sarebbe falso, e con la
    regola fissa armata spingerebbe il Capo a inventare obiezioni."""
    for crit in (SEGNAPOSTO, RIFIUTO, "[ERROR red team]: Error code: 400"):
        chiamate = _prepara(monkeypatch)
        capo.run_capo(SimpleNamespace(
            data={"macro": {2: "report"}, "_red_team": {1: crit}}))
        um = _user_msg(chiamate)
        assert "RED TEAM — ASSENTE in questa run" in um, crit[:40]
        assert "RED TEAM — CRITICA AVVERSARIALE" not in um, crit[:40]
        assert crit[:60] in um, crit[:40]   # il testo a registro arriva nel motivo


def test_rifiuto_con_critica_parziale_resta_una_critica(monkeypatch):
    """Rifiuto ARRIVATO A META' generazione: c'e' testo gia' pagato e il
    rifiuto e' dichiarato in testa (llm_refusal: `_rif + "\\n\\n" + critique`).
    Una critica parziale E' una critica: ramo presente."""
    chiamate = _prepara(monkeypatch)
    capo.run_capo(SimpleNamespace(
        data={"macro": {2: "report"}, "_red_team": {1: RIFIUTO + "\n\n" + CRITICA}}))
    um = _user_msg(chiamate)
    assert "RED TEAM — CRITICA AVVERSARIALE" in um
    assert CRITICA in um
    assert "RED TEAM — ASSENTE" not in um
