"""Test OFFLINE voce I-1 (26/07 sera-4, Opus 5): i "fili rotti" fra prompt e registro tool.

Il bug, misurato prima del fix: `specialists/macro.py:29` ORDINA al desk macro
*"1b. STRUTTURA A TERMINE REALE: get_yield_curves (UNA volta) ... citali"*, ma
`get_yield_curves` non era nel subset `macro` di `chat_tools.get_tools_for_agent`.
Lo schema viveva SOLO in `agent_tools.TOOLS_SCHEMA`, che e' il registro LEGACY:
`specialists/base.py:573` usa `chat_tools.get_tools_for_agent`, e ripiega su
`agent_tools` solo se l'import fallisce (:574), cioe' mai. Stessa storia per
`add_research_note`, ordinato da `current_facts.py:416` e assente dal subset
`fundamentals` (canale note PM<->AI muto: `decision_notes` = 0 righe).

Un ordine ineseguibile e' peggio di un dato mancante: il modello non puo'
obbedire, ma la sezione del memo la scrive lo stesso — senza la fonte.

I test 1-4 inchiodano i due casi noti. Il test 5 e' quello che vale: e' una
GUARDIA DI CLASSE — legge i system prompt dei desk, ne estrae i nomi di tool
citati come chiamate e pretende che ognuno sia nel subset di QUEL desk. Se un
domani qualcuno ordina in un prompt un tool che l'agente non ha, fallisce qui
invece che dentro una run da ~10 EUR.

Zero rete, zero DB, zero LLM: si guardano registri e stringhe.
"""
import re

import pytest

from bellomberg.agents import chat_tools


def _nomi(tools):
    return {t["name"] for t in tools}


# --------------------------------------------------------------------------
# 1-2. I due fili riattaccati (i casi noti della voce I-1)
# --------------------------------------------------------------------------

def test_get_yield_curves_e_nel_registro_vivo():
    """Lo schema deve stare in TOOL_DEFINITIONS, non solo nel registro legacy."""
    assert "get_yield_curves" in _nomi(chat_tools.TOOL_DEFINITIONS), (
        "get_yield_curves e' tornato invisibile agli specialisti: lo schema deve "
        "vivere in chat_tools.TOOL_DEFINITIONS (agent_tools e' fallback-only)")


def test_macro_riceve_le_curve():
    """Il desk che ha l'ordine nel prompt deve avere il tool nel subset."""
    assert "get_yield_curves" in _nomi(chat_tools.get_tools_for_agent("macro")), (
        "specialists/macro.py punto 1b ORDINA get_yield_curves: senza il nome nel "
        "subset macro l'ordine e' ineseguibile e la sezione tassi del memo nasce "
        "senza la fonte validata")


def test_fundamentals_riceve_il_canale_note():
    """current_facts.py:416 dice 'DEVI rispondere con add_research_note'."""
    assert "add_research_note" in _nomi(chat_tools.get_tools_for_agent("fundamentals")), (
        "canale note PM<->AI di nuovo muto: il blocco TITOLI IN RICERCA ordina "
        "add_research_note al desk fundamentals")


# --------------------------------------------------------------------------
# 3. Il dispatch esiste davvero (uno schema senza ramo = errore a run in corso)
# --------------------------------------------------------------------------

def test_ogni_tool_dichiarato_ha_un_ramo_di_dispatch():
    """Uno schema senza dispatch non fallisce all'avvio: fallisce quando il
    modello lo chiama, cioe' dentro la run. Qui invece fallisce subito."""
    import inspect
    sorgente = inspect.getsource(chat_tools.dispatch)
    # forma canonica: `if tool_name == "x":`
    citati = set(re.findall(r'tool_name\s*==\s*"([a-z0-9_]+)"', sorgente))
    # forme collettive: `tool_name in ("a", "b")` / `in ["a", "b"]` / `in {...}`
    for gruppo in re.findall(r'tool_name\s+in\s+[\(\[\{]([^\)\]\}]*)[\)\]\}]', sorgente):
        citati |= set(re.findall(r'"([a-z0-9_]+)"', gruppo))
    mancanti = sorted(n for n in _nomi(chat_tools.TOOL_DEFINITIONS) if n not in citati)
    assert not mancanti, (
        f"tool dichiarati agli agenti ma senza ramo di dispatch: {mancanti} — "
        "il modello li vedrebbe, li chiamerebbe e prenderebbe un errore in run")


# --------------------------------------------------------------------------
# 4. Nessun nome fantasma nei subset
# --------------------------------------------------------------------------

def _subsets_grezzi():
    """I SUBSETS come sono SCRITTI, non come escono filtrati.

    Review 26/07: la prima stesura confrontava l'output di `get_tools_for_agent`
    con TOOL_DEFINITIONS ed era una TAUTOLOGIA — quella funzione filtra proprio
    su TOOL_DEFINITIONS (`[t for t in TOOL_DEFINITIONS if t["name"] in ...]`),
    quindi l'inclusione era vera per costruzione e un typo in un subset
    (`add_reserach_note`) veniva scartato in silenzio col test verde.
    Qui si legge il dict letterale dal sorgente via AST.
    """
    import ast as _ast
    import inspect
    albero = _ast.parse(inspect.getsource(chat_tools.get_tools_for_agent).lstrip())
    for nodo in _ast.walk(albero):
        if isinstance(nodo, _ast.Assign):
            bersagli = [t.id for t in nodo.targets if isinstance(t, _ast.Name)]
            if "SUBSETS" in bersagli:
                return _ast.literal_eval(nodo.value)
    raise AssertionError("dict SUBSETS non trovato in get_tools_for_agent: "
                         "e' stato rinominato? la guardia anti-typo va riadattata")


def test_i_subsets_grezzi_sono_stati_trovati():
    """Se l'estrazione AST si rompe, i due test sotto diventerebbero vacui."""
    s = _subsets_grezzi()
    assert isinstance(s, dict) and len(s) >= 6, f"SUBSETS sospetto: {list(s)}"


def test_nessun_nome_fantasma_nei_subset():
    """Un nome scritto in un subset che non esiste in TOOL_DEFINITIONS viene
    scartato dal filtro SENZA dire niente: l'agente non lo riceve e nessuno se
    ne accorge. Un typo costa un tool, in silenzio."""
    dichiarati = _nomi(chat_tools.TOOL_DEFINITIONS)
    fantasmi = {}
    for agente, nomi in _subsets_grezzi().items():
        mancanti = sorted(n for n in nomi if n not in dichiarati)
        if mancanti:
            fantasmi[agente] = mancanti
    assert not fantasmi, (
        f"nomi scritti nei SUBSETS ma inesistenti in TOOL_DEFINITIONS (scartati in "
        f"silenzio dal filtro): {fantasmi}")


# --------------------------------------------------------------------------
# 5. LA GUARDIA DI CLASSE: nessun prompt ordina un tool che il desk non ha
# --------------------------------------------------------------------------

def _tool_ordinati_nel_prompt(testo, universo):
    """Nomi di tool citati nel prompt come CHIAMATE, non come prosa.

    Si accettano solo le forme in cui il prompt sta dando un ordine operativo:
    `nome(`, `nome (UNA volta)`, `[src: nome]`. Cosi' una menzione discorsiva
    ("il quant guarda get_portfolio_risk") non produce falsi positivi.
    """
    trovati = set()
    for nome in universo:
        if re.search(r'\b' + re.escape(nome) + r'\s*\(', testo):
            trovati.add(nome)
        elif re.search(r'\[src:\s*' + re.escape(nome) + r'\s*\]', testo):
            trovati.add(nome)
    return trovati


# i due meta-tool che base.py appende a TUTTI i desk fuori dal subset
_META_TOOLS = {"ask_specialist", "read_blackboard"}


def _universo_dei_nomi_di_tool():
    """TUTTI i nomi di tool esistenti nel progetto, non solo quelli del registro vivo.

    Review 26/07 — questo era il difetto GRAVE della prima stesura: l'universo era
    `TOOL_DEFINITIONS`, cioe' il registro VIVO. Ma il bug I-1 era esattamente un nome
    che NON stava nel registro vivo (lo schema viveva solo in agent_tools). Con quel
    perimetro la guardia era **cieca sul caso che dice di coprire**: verde sia prima
    sia dopo il fix. L'universo giusto include i registri legacy.
    """
    from bellomberg.agents import agent_tools
    return (_nomi(chat_tools.TOOL_DEFINITIONS)
            | _nomi(agent_tools.TOOLS_SCHEMA)
            | set(agent_tools.TOOL_DISPATCHER))


def test_nessun_prompt_ordina_un_tool_che_il_desk_non_ha():
    """LA guardia di classe: e' il test che avrebbe beccato I-1 da solo, 3 run fa.

    Provato per mutazione (v. changelog): rimuovendo `get_yield_curves` da
    TOOL_DEFINITIONS *e* dal subset macro — cioe' ricreando il bug originale — questo
    test FALLISCE. Con l'universo sbagliato della prima stesura restava verde.
    """
    from bellomberg.agents.specialists import ALL_SPECIALISTS

    universo = _universo_dei_nomi_di_tool()
    rotti = []
    for spec in ALL_SPECIALISTS:
        prompt = getattr(spec, "system_prompt", "") or ""
        if not isinstance(prompt, str):
            continue
        suoi = _nomi(chat_tools.get_tools_for_agent(spec.name)) | _META_TOOLS
        for nome in sorted(_tool_ordinati_nel_prompt(prompt, universo)):
            if nome not in suoi:
                rotti.append(f"{spec.name}: il prompt chiama {nome}() ma non e' nel suo subset")
    assert not rotti, (
        "FILO ROTTO prompt->registro (classe del bug I-1): un desk riceve un ordine "
        "che non puo' eseguire.\n  " + "\n  ".join(rotti))


# --------------------------------------------------------------------------
# 6. Le due copie della descrizione non devono divergere
# --------------------------------------------------------------------------

def test_le_due_copie_della_descrizione_non_divergono():
    """`get_yield_curves` ha lo schema in due registri. Se le descrizioni divergono,
    il modello riceve promesse diverse a seconda di quale registro lo serve — e la
    descrizione e' l'unica cosa che gli dice quali campi sono CONDIZIONALI."""
    from bellomberg.agents import agent_tools
    viva = next(t["description"] for t in chat_tools.TOOL_DEFINITIONS
                if t["name"] == "get_yield_curves")
    assert viva == agent_tools.YIELD_CURVES_DESCRIPTION, (
        "le due copie della descrizione di get_yield_curves sono divergenti: "
        "allineale (agent_tools.YIELD_CURVES_DESCRIPTION e' la sorgente nominata)")


def test_la_descrizione_dichiara_i_campi_CONDIZIONALI():
    """Regola 14/07 applicata alla descrizione di un tool: promettere al modello un
    campo che il payload emette solo a condizione e' un invito ad allucidare con
    tanto di [src] falso — e il prompt macro ORDINA di citare proprio quei campi."""
    from bellomberg.agents import agent_tools
    d = agent_tools.YIELD_CURVES_DESCRIPTION
    for atteso in ("CONDITIONAL", "change_bps_1m", "slope_2s10s_bps", "curve_shape",
                   "PRECEDENCE", "yield_curve_10y_2y_bps"):
        assert atteso in d, (
            f"la descrizione ha perso '{atteso}': i campi condizionali e la precedenza "
            "contro get_macro_dashboard vanno DICHIARATI al modello")
