"""Profili filing standard per gli emittenti SEC (10-K, 10-Q, 20-F, 6-K).

Regex costruite su intestazioni osservate in documenti reali (sonda 2026-10-03):
maiuscole o no, apostrofo tipografico, indice che ripete le intestazioni
(sezioni_salta_indice), «RISK FACTORS» isolato nei 20-F. I 6-K hanno struttura
libera: testo intero, periodo piu' recente, documento piu' lungo per periodo.
Nei 20-F i contenziosi (8.A.7) non sono un modello: spesso rimandano a una nota.
"""
import re

from bellomberg.market_data.filing_identita import norm_forma

_SEP = r"\.?\s*[-–—:.]?\s*"
MODELLI_SEC = {
    "10-K": {"tipo": "annuale", "forme_sec": ["10-K"], "sezioni_salta_indice": True,
             "verifica": {"tipo": r"annual\s+report\s+pursuant\s+to\s+section\s+13"},
             "sezioni": {
                 "rischi": {"inizio": rf"item\s*1a{_SEP}risk\s+factors\.?",
                            "fine": r"item\s*(?:1b|1c|2)\b.{0,160}"},
                 "contenziosi": {"inizio": rf"item\s*3{_SEP}legal\s+proceedings\.?",
                                 "fine": r"item\s*4\b.{0,160}"},
                 "gestione": {"inizio": rf"item\s*7{_SEP}management.s\s+discussion\s+and\s+analysis\b.{{0,120}}",
                              "fine": r"item\s*7a\b.{0,160}"},
                 "rischio_mercato": {"inizio": rf"item\s*7a{_SEP}quantitative\s+and\s+qualitative\s+disclosures?\b.{{0,80}}",
                                     "fine": r"item\s*8\b.{0,160}"}}},
    "10-Q": {"tipo": "trimestrale", "forme_sec": ["10-Q"], "sezioni_salta_indice": True,
             "verifica": {"tipo": r"quarterly\s+report\s+pursuant\s+to\s+section\s+13"},
             "sezioni": {
                 "gestione": {"inizio": rf"item\s*2{_SEP}management.s\s+discussion\s+and\s+analysis\b.{{0,120}}",
                              "fine": r"item\s*3\b.{0,160}"},
                 "rischio_mercato": {"inizio": rf"item\s*3{_SEP}quantitative\s+and\s+qualitative\s+disclosures?\b.{{0,80}}",
                                     "fine": r"item\s*4\b.{0,160}"},
                 "contenziosi": {"inizio": rf"item\s*1{_SEP}legal\s+proceedings\.?",
                                 "fine": r"item\s*(?:1a|2)\b.{0,160}"},
                 "rischi": {"inizio": rf"item\s*1a{_SEP}risk\s+factors\.?",
                            "fine": r"item\s*2\b.{0,160}"}}},
    "20-F": {"tipo": "annuale", "forme_sec": ["20-F"], "sezioni_salta_indice": True,
             "verifica": {"tipo": r"annual\s+report\s+pursuant\s+to\s+section\s+13\s+or\s+15\s*\(d\)"},
             "sezioni": {
                 "rischi": {"inizio": rf"(?:item\s*3\.?\s*)?(?:d{_SEP})?risk\s+factors\.?",
                            "fine": r"item\s*4\b.{0,160}"},
                 "gestione": {"inizio": rf"item\s*5{_SEP}operating\s+and\s+financial\s+review\s+and\s+prospects\.?",
                              "fine": r"item\s*6\b.{0,160}"}}},
    "6-K": {"forme_sec": ["6-K"], "sezioni": {}, "sezioni_intero": True,
            "periodo_regola": "piu_recente", "stesso_periodo": "piu_lungo"},
}
_VERIFICA_COMUNE = {"lingua": r"\b(?:the|and|of)\b", "perimetro": r"consolidated"}
_DATA = r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"
# APERTO-TI 06/10: stesse regex della regola standard 6-K FPI («second quarter ended ...»,
# «three and six-month periods ended ...»), cosi' i profili nuovi riconoscono il trimestrale da soli.
from bellomberg.market_data.filing_verifica import PERIODO_6K_STANDARD as _PERIODO_6K, TIPO_6K_STANDARD


def forme_presenti(catalogo):
    """Forme SEC presenti nel catalogo (rettifiche /A escluse)."""
    return {d["form"] for d in catalogo.get("documenti", []) if not str(d.get("form", "")).endswith("/A")}


def sonda_tipo_6k(testo):
    """trimestrale / semestrale dal testo di una relazione 6-K, None se non e' un bilancio.

    R-FONTI 10/10 (Opus 5.5): anche «three-month period ended», «three-months period ended», «three month
    period ended» e i trattini tipografici; «six-month period ended» e' semestrale (prima: None); «twelve-month
    period» e l'esercizio restano None (non sono una relazione infrannuale)."""
    t = (testo or "").lower()
    # v4 (Opus 5.5, audit di generalita' punto 7): anche «3-month», «ending», «semester ended»
    if re.search(rf"(?<![\w{_TRATTINI}])(?:three|3)(?:\s+and\s+(?:six|nine))?[\s{_TRATTINI}]+months?(?:\s+periods?)?"
                 r"\s+end(?:ed|ing)\b|\bquarter\s+end(?:ed|ing)\b", t):
        return "trimestrale"
    if re.search(rf"(?<![\w{_TRATTINI}])(?:six|6)[\s{_TRATTINI}]+months?(?:\s+periods?)?\s+end(?:ed|ing)\b|\bhalf[-\s]year\b"
                 r"|\bsemester\s+end(?:ed|ing)\b", t):
        return "semestrale"
    return None


_TRATTINI = "\\-\u2010\u2011\u2012\u2013"
# R-FONTI 10/10 (Opus 5.5): regole 6-K che il generatore automatico scriveva nei profili fino al 06/10
# (commit ebc6dfe, «months ended» senza «period»). I profili salvati allora le tengono: il 6-K Q1 2026 di un emittente del book
# («three-month period ended March 31, 2026») restava «tipo: prova testuale assente». Si sostituiscono SOLO
# queste stringhe esatte (scritte dal codice, non da una persona) con le regole correnti del generatore;
# lingua e perimetro del profilo restano i suoi. La sostituzione si dichiara nei limiti del run. Regole piu'
# larghe su tipo/periodo: la pipeline prova l'identita' anche nel CORPO di ogni 6-K verificato (identita_6k,
# riserva ALTO-1 v2): la regola «emittente» del profilo passa sempre sulla copertina del registrante.
_DATA_LEGACY = r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"
REGOLE_6K_LEGACY = {
    "tipo": r"months\s+ended|half[-\s]year",
    "trimestrale": rf"(?P<mesi>three|3)(?:\s+and\s+(?:six|nine))?\s+months\s+ended\s+{_DATA_LEGACY}",
    "semestrale": rf"(?P<mesi>six|6)\s+months\s+ended\s+{_DATA_LEGACY}"}


def aggiorna_regole_6k(profilo):
    """(profilo, nota): le regole 6-K automatiche di prima del 06/10 diventano quelle correnti.

    Profilo (gia' fuso con la sua variante) 6-K trimestrale/semestrale: `verifica.tipo` uguale alla
    vecchia regola automatica -> TIPO_6K_STANDARD; `verifica.periodo` uguale alla vecchia regola del suo
    tipo -> PERIODO_6K_STANDARD del tipo. Regole scritte a mano o gia' correnti: invariate, nota None."""
    if (not isinstance(profilo, dict) or profilo.get("forme_sec") != ["6-K"]
            or profilo.get("tipo") not in _PERIODO_6K or not isinstance(profilo.get("verifica"), dict)):
        return profilo, None
    verifica, cambiate = dict(profilo["verifica"]), []
    if verifica.get("tipo") == REGOLE_6K_LEGACY["tipo"]:
        verifica["tipo"] = TIPO_6K_STANDARD
        cambiate.append("tipo")
    if verifica.get("periodo") == REGOLE_6K_LEGACY[profilo["tipo"]]:
        verifica["periodo"] = _PERIODO_6K[profilo["tipo"]]
        cambiate.append("periodo")
    if not cambiate:
        return profilo, None
    return {**profilo, "verifica": verifica}, (
        "SEC 6-K: regole " + " e ".join(cambiate) + " del profilo automatico (generatore anteriore al 06/10, "
        "«months ended» senza «period») aggiornate alle regole correnti del codice; identita' provata anche "
        "nel corpo di ogni 6-K (la copertina del registrante non basta).")


# Tra le parole del nome: spazi, trattini, punti, «&» o «and/und/et/e/y» («Nova & Kore-Tech»).
_SEP_NOME = r"(?:[\s\-\u2013\u2014.,]|&|\b(?:and|und|et|e|y)\b)+"


def _regex_nome(nome):
    """Prova testuale dell'emittente: TUTTE le parole del nome, forma giuridica a parte.

    Revisione G1 (04/10/2026): prima bastavano le prime 2 parole senza holding/group, e
    «ACME Holdings plc» passava su un documento di «ACME Inc.» (un altro emittente)."""
    parole = norm_forma(nome).split() or [str(nome)]
    return r"\b" + _SEP_NOME.join(re.escape(p) for p in parole) + r"\b"


def regex_confermata(nome, nome_sec):
    """Collegamento confermato dall'utente: vale il nome del portafoglio O quello SEC."""
    a, b = _regex_nome(nome), _regex_nome(nome_sec)
    return a if a == b else f"(?:{a}|{b})"


def _variante(forma, tipo_6k=None, nome="", regex_emittente=None):
    m = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
         for k, v in MODELLI_SEC[forma].items()}
    if forma == "6-K":
        m["tipo"] = tipo_6k
        m["verifica"] = {"tipo": TIPO_6K_STANDARD,
                         "emittente": regex_emittente or _regex_nome(nome), "periodo": _PERIODO_6K[tipo_6k]}
    return m


def profilo_sec(ticker, *, cik, sec_ticker, nome, origine, forme, tipo_6k=None, regex_emittente=None):
    """Profilo valido col ticker del portafoglio e varianti annuale + infrannuale.

    `regex_emittente`: prova dell'emittente gia' decisa (profilo esistente, o collegamento
    confermato: v. `regex_confermata`); senza, dal nome."""
    varianti = []
    annuale = next((f for f in ("10-K", "20-F") if f in forme), None)
    if annuale:
        varianti.append(_variante(annuale, nome=nome))
    if "10-Q" in forme:
        varianti.append(_variante("10-Q", nome=nome))
    elif "6-K" in forme and tipo_6k in _PERIODO_6K:
        varianti.append(_variante("6-K", tipo_6k, nome, regex_emittente))
    if not varianti:
        raise ValueError("nessuna forma SEC supportata (10-K, 10-Q, 20-F, 6-K) nel catalogo")
    primo = varianti[0]
    cik = str(cik).zfill(10)
    return {"ticker": ticker, "emittente_id": "CIK:" + cik, "cik": cik, "sec_ticker": sec_ticker,
            "nome": nome, "origine_collegamento": origine, "fonti": ["sec"],
            "lingua": "en", "perimetro": "consolidato", "tipo": primo["tipo"],
            "verifica": {**_VERIFICA_COMUNE, "emittente": regex_emittente or _regex_nome(nome),
                         **primo.get("verifica", {})},
            "sezioni": primo.get("sezioni", {}),
            **{k: primo[k] for k in ("forme_sec", "sezioni_salta_indice", "sezioni_intero",
                                     "periodo_regola", "stesso_periodo") if k in primo},
            "varianti": varianti}


# Lingua dei depositi ESEF: prova dalla dimensione `language` dei fatti; la regola
# testuale serve solo ai fatti senza lingua dichiarata.
_LINGUA_ESEF = {"en": r"\b(?:the|and|of)\b", "it": r"\b(?:il|di|che|della|per)\b",
                "fr": r"\b(?:le|la|les|et|des)\b", "de": r"\b(?:der|die|und|das)\b",
                "es": r"\b(?:el|la|los|y|de)\b", "nl": r"\b(?:de|het|en|van)\b",
                # REV_G2b B2 (04/10/2026): le altre lingue principali dei depositi ESEF
                "pt": r"\b(?:não|que|para|com|uma)\b", "sv": r"\b(?:och|att|för|som|med)\b",
                "da": r"\b(?:og|at|af|som|med)\b", "no": r"\b(?:og|at|av|som|med)\b",
                "nb": r"\b(?:og|at|av|som|med)\b", "fi": r"\b(?:ja|on|että|tai|sekä)\b",
                "pl": r"\b(?:oraz|że|się|jest|dla)\b", "cs": r"\b(?:že|se|na|je|a)\b",
                "hu": r"\b(?:és|hogy|az|egy)\b", "el": r"(?:και|του|της|των)",
                "ro": r"\b(?:și|şi|în|din|pentru)\b"}
# Lingua senza regola testuale: regola che non combacia mai (il negozio vuole una stringa).
# Mai r"\w" (combacia con tutto: verifica sempre vera e muta). filing_esef la riconosce e,
# se i fatti non dichiarano la lingua, scrive «lingua non verificabile (xx)» senza errore.
LINGUA_NON_VERIFICABILE = r"(?!)"


def profilo_esef(ticker, *, lei, nome, origine, lingua):
    """Profilo ESEF annuale sui text block dello xBRL-JSON, col ticker del portafoglio.

    Identita', periodo e tipo si provano sui fatti XBRL (filing_esef): `verifica.tipo`
    e' obbligatoria per il negozio ma la prova del tipo e' la durata annuale dei fatti.
    """
    lei = str(lei).upper()
    return {"ticker": ticker, "emittente_id": "LEI:" + lei, "lei": lei, "nome": nome,
            "origine_collegamento": origine, "fonti": ["esef"], "esef_modo": "blocchi",
            "lingua": lingua, "perimetro": "consolidato", "tipo": "annuale",
            "verifica": {"lingua": _LINGUA_ESEF.get(lingua, LINGUA_NON_VERIFICABILE), "tipo": r"\S",
                         "perimetro": r"consolidat|\b(?:group|gruppo|groupe|grupo|groep)\b|konzern"},
            "sezioni": {}}
