"""B1 (02/09, pubblicazione): .env.example elenca ESATTAMENTE le variabili che il
codice di prodotto legge — nei due versi. Una variabile letta e non documentata e'
un buco per chi installa; una documentata e non letta e' una bugia.
"""
import os
import re
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, ".env.example")
LETTURA = re.compile(r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]+)["']|os\.environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\](?!\s*=)""")
RUNTIME_PATH = re.compile(r'_runtime_path\("([A-Z][A-Z0-9_]+)"')
# Variabili di sistema lette dal codice ma che NON si configurano in .env
SISTEMA = {"TEMP", "TMP", "USERPROFILE", "PATH", "PYTHONIOENCODING"}
# Le uniche righe del template a cui e' permesso un valore: sono DEFAULT dichiarati
CON_DEFAULT = {"PM_NAME", "CONSIGLIERE_PARALLEL", "CONSIGLIERE_R0_MODEL"}
# 05/09 (OpenRouter, ordine PM): le variabili MODELLO portano la tabella scelta dal PM
# (uno slug `provider/modello` per funzione) e CHAT_MAX_TOKENS il tetto: sono la
# configurazione consigliata, NON un default nel codice (assente = errore col nome).
MODELLI_OPENROUTER = re.compile(r"^(?:CHAT_[A-Z]+_MODEL|CHAT_MODEL|CONSIGLIERE_[A-Z0-9]+_MODEL|"
                                r"CONSIGLIERE_MODEL|CAPO_MODEL|RED_TEAM_MODEL|REFLECTION_MODEL|"
                                r"ACTION_EXTRACTOR_MODEL|BRIEFING_MODEL|NEWS_CLASSIFIER_MODEL|"
                                r"CHAT_MAX_TOKENS)$")
SLUG_OPENROUTER = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._:-]+$")


def _file_prodotto():
    root = Path(REPO)
    files = list(root.glob("*.py")) + list((root / "src" / "bellomberg").rglob("*.py"))
    assert files, "nessun sorgente di prodotto trovato"
    return [str(path.relative_to(root)) for path in files]


def _lette_dal_codice():
    nomi = set()
    for f in _file_prodotto():
        source = Path(REPO, f).read_text(encoding="utf-8")
        for a, b in LETTURA.findall(source):
            nomi.add(a or b)
        nomi.update(RUNTIME_PATH.findall(source))
    return nomi - SISTEMA


def _righe_template():
    for riga in open(TEMPLATE, encoding="utf-8"):
        m = re.match(r"^([A-Z][A-Z0-9_]+)=(.*)$", riga.rstrip("\r\n"))
        if m:
            yield m.group(1), m.group(2).strip()


def _nel_template():
    return {nome for nome, _ in _righe_template()}


def test_template_esiste_e_non_ha_valori():
    con_valore = [nome for nome, valore in _righe_template()
                  if valore and nome not in CON_DEFAULT and not MODELLI_OPENROUTER.match(nome)]
    assert not con_valore, f"valori nel template (una chiave vera?): {con_valore}"


def test_i_default_dichiarati_coincidono_col_codice():
    valori = dict(_righe_template())
    assert valori["PM_NAME"] == "PM"
    assert valori["CONSIGLIERE_PARALLEL"] == "4"


def test_le_variabili_modello_portano_slug_openrouter_e_il_tetto_un_intero():
    """05/09: ogni riga modello del template e' uno slug OpenRouter `provider/modello`
    (eventuale variante `:exacto`), non vuota e non una chiave; CHAT_MAX_TOKENS e' un
    intero. Il codice NON ha default (llm_client: variabile assente = errore col nome),
    quindi il template e' l'unico posto dove la tabella del PM e' scritta per chi installa."""
    valori = dict(_righe_template())
    modelli = {n: v for n, v in valori.items() if MODELLI_OPENROUTER.match(n) and n != "CHAT_MAX_TOKENS"}
    assert len(modelli) >= 11, sorted(modelli)
    brutti = {n: v for n, v in modelli.items() if not SLUG_OPENROUTER.match(v)}
    assert not brutti, brutti
    assert valori["CHAT_MAX_TOKENS"].isdigit() and int(valori["CHAT_MAX_TOKENS"]) > 0
    # la chiave di OpenRouter e' documentata e VUOTA (mai una chiave vera nel template)
    assert valori.get("OPENROUTER_API_KEY") == ""


def test_ogni_variabile_letta_e_documentata():
    mancano = _lette_dal_codice() - _nel_template()
    assert not mancano, f"lette dal codice ma non in .env.example: {sorted(mancano)}"


def test_ogni_variabile_documentata_e_letta():
    inutili = _nel_template() - _lette_dal_codice()
    assert not inutili, f"in .env.example ma nessun modulo le legge: {sorted(inutili)}"


def test_nessun_riferimento_al_template_fantasma():
    for f in ("src/bellomberg/core/config.py", "README.md"):
        assert "env.example.txt" not in open(os.path.join(REPO, f), encoding="utf-8").read(), f


def test_ogni_variabile_ha_una_riga_di_commento_sopra():
    """Chi installa legge il template: ogni variabile (o blocco di variabili contigue,
    es. le tre EMAIL_*) ha SUBITO sopra un commento che dice a cosa serve — non un
    header di sezione, non un'altra variabile (review 02/09: la versione a «3 righe
    sopra» passava anche con nessun commento).

    Non scandisce app/: process.env vi appare solo per BELLOMBERG_LAUNCH_ID e
    VITE_DEV_SERVER_URL, variabili interne che l'utente non configura."""
    righe = open(TEMPLATE, encoding="utf-8").read().splitlines()
    senza = []
    for i, riga in enumerate(righe):
        if re.match(r"^[A-Z][A-Z0-9_]+=", riga):
            j = i - 1
            while j >= 0 and re.match(r"^[A-Z][A-Z0-9_]+=", righe[j]):
                j -= 1                       # risali il blocco di variabili contigue
            sopra = righe[j] if j >= 0 else ""
            if not (sopra.startswith("#") and not sopra.startswith("# ===")):
                senza.append(riga.split("=")[0])
    assert not senza, senza
