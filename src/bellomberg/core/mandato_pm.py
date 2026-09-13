"""
mandato_pm.py — il MANDATO del PM: un profilo dichiarato dall'app, letto DAL DISCO a ogni chiamata.

Criterio (5) dell'audit esterno (audit/26, 05/09): i criteri (1)-(4) tolgono dal repo i ticker e le
cifre del book, ma nei prompt restava la DOTTRINA del creatore (quanta cassa e quanto impiegarne,
orizzonte e concentrazione, tetti del sizing, disciplina dei tagli, mandato sulle opzioni, caccia
globale): chi clonava riceveva raccomandazioni costruite sul mandato di un altro. Da oggi quelle
frasi nascono dai valori di `data/mandato_pm.json` (PRIVATO, mai esportato: `data/` sta in
ESCLUSI), e il contratto pubblico e' `mandato_pm.example.json` (campi VUOTI + profilo di esempio).

Regole di casa che questo modulo incarna:
  - `carica()` legge il file a OGNI chiamata: nessuna costante a import-time, nessuna cache di
    modulo (ordine PM 05/09: «se domani li cambio, consigliere e chat devono seguire il nuovo
    mandato, non si devono sbagliare»); `tests/test_mandato_pm.py` lo prova con «A poi B» e con
    un controllo AST sui moduli che leggono il mandato.
  - assente / illeggibile / incompleto = `MandatoMancante` col percorso e la causa (regola 14/07):
    mai i valori di ieri come ripiego. Il comitato non parte; la chat lo dichiara.
  - il testo per i modelli e' DETERMINISTICO dai campi (`sezioni`, `blocco_prompt`, `compila`):
    quello che la pagina Mandato mostra in anteprima e' quello che l'AI riceve.
  - «campi VUOTI al primo accesso» (decisione PM): nessun default nel codice; il PROFILO DI
    ESEMPIO e' un'azione dell'utente («Compila con un profilo di esempio»), e finche' i valori
    coincidono con l'esempio il file vale `origine: "esempio"` e l'intestazione lo dice.

SOLO stdlib (come classificazione.py): le prove lo importano senza far girare DB ne' cache.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from bellomberg.core.paths import DATA_DIR, EXAMPLES_DIR
from bellomberg.core.language import text as _ui_text, current_language, scoped_language, language_context
from bellomberg.core.presentation import message as _message, join_messages, error_text, render_payload

PERCORSO_MANDATO = str(DATA_DIR / "mandato_pm.json")
ESEMPIO_MANDATO = str(EXAMPLES_DIR / "mandato_pm.example.json")
VERSIONE_SCHEMA = 1

BLOCCHI = ("profilo", "rischio", "sizing", "cassa", "disciplina", "opzioni", "note")
# Le chiavi che il mandato porta FUORI dai blocchi. Serve a `valida` per sapere cosa non
# conosce: cio' che non e' qui dentro e non comincia per «_» viene nominato, mai scartato zitto.
CHIAVI_PRIMO_LIVELLO = ("versione", "dichiarato_il", "origine") + BLOCCHI

# Traduzione VaR <-> volatilita': ipotesi del MOTORE (normali i.i.d., media zero), non
# preferenze del PM — per questo stanno qui e non nel mandato. z a UNA coda per il 99%.
Z99_UNA_CODA = 2.3263478740408408
GIORNI_BORSA = 252
# Quanto la volatilita' implicita dal VaR puo' superare il target dichiarato prima che
# sia un'incoerenza. **1,5 = decisione del PM del 06/09**: banda LARGA, prende solo
# l'incoerenza grossa (vol target bassa con un VaR da portafoglio molto piu' mosso) e
# lascia passare una prudenza dichiarata al ribasso. Non e' un numero tecnico.
BANDA_VOL_SU_VAR = 1.5
TIPI = ("scelta", "bool", "int", "num", "pct", "intervallo_pct", "intervallo_int", "lista",
        "lista_testo", "interruttori", "testo", "valuta")

# piazze: codice (il suffisso Yahoo del listino; US = senza suffisso) -> citta' per la frase
# «come si compra da …». Vocabolario chiuso: un codice fuori lista e' un errore dichiarato.
PIAZZE = {
    "MI": "Milano", "L": "Londra", "DE": "Francoforte", "FRA": "Francoforte", "PA": "Parigi",
    "AS": "Amsterdam", "BR": "Bruxelles", "MC": "Madrid", "SW": "Zurigo", "VI": "Vienna",
    "US": "New York", "TO": "Toronto", "T": "Tokyo", "HK": "Hong Kong", "NS": "Mumbai",
    "SA": "San Paolo", "AX": "Sydney",
}
STRUMENTI_OPZIONI = ("long_call_catalyst", "put_hedge", "put_spread", "covered_call",
                     "cash_secured_put", "short_premium_nudo", "straddle_strangle")
CONDIZIONI_TAGLIO = ("sharpe_12m_negativo", "nessun_catalyst_90g", "tesi_smentita")
TIPI_INVESTIMENTO = ("long_term", "medio_termine", "trading")
STILI = ("concentrato", "diversificato")
POLITICHE_IMPIEGO = ("prudente", "neutra", "aggressiva")

# Sotto questa liquidita' tipica la frase per i modelli dice «contenuta» invece di
# «importante»: e' una regola di RESA del testo, non una preferenza del PM.
LIQUIDITA_IMPORTANTE_DA_PCT = 30

MAX_CHAR_NOTE = 2000
MAX_CHAR_TESTO = 120


def _c(blocco: str, tipo: str, obbligatorio: bool, descrizione: str, unita: str = "",
       intervallo: Optional[Tuple[float, float]] = None, scelte: Optional[Tuple[str, ...]] = None,
       massimo_char: Optional[int] = None) -> Dict[str, Any]:
    return {"blocco": blocco, "tipo": tipo, "obbligatorio": obbligatorio, "descrizione": descrizione,
            "unita": unita, "intervallo": list(intervallo) if intervallo else None,
            "scelte": list(scelte) if scelte else None, "massimo_char": massimo_char}


# I campi che il codice legge: la pagina Mandato (F18) li rende da qui, `mandato_pm.example.json`
# li elenca nei due versi (test), `valida` li controlla. Un campo nuovo si aggiunge QUI e in
# `sezioni` (dove entra nel testo per i modelli): un campo che nessuno legge e' una bugia.
CAMPI: Dict[str, Dict[str, Any]] = {
    # 1 — PROFILO
    "tipo_investimento": _c("profilo", "scelta", True, "Orizzonte con cui investe: long_term (anni), medio_termine (mesi), trading (settimane).", scelte=TIPI_INVESTIMENTO),
    "stile": _c("profilo", "scelta", True, "concentrato = accetta conviction pesanti; diversificato = nessun nome sopra i cap.", scelte=STILI),
    "orizzonte_anni": _c("profilo", "int", True, "Orizzonte medio di detenzione di una posizione.", unita="anni", intervallo=(1, 30)),
    "valuta_base": _c("profilo", "valuta", True, "Valuta in cui misura il patrimonio (codice ISO: EUR, USD, GBP...)."),
    "broker": _c("profilo", "testo", False, "Il broker con cui opera (solo informativo).", massimo_char=MAX_CHAR_TESTO),
    "residenza_fiscale": _c("profilo", "testo", False, "Paese di residenza fiscale (informa la preferenza UCITS e i veicoli).", massimo_char=MAX_CHAR_TESTO),
    "mercati_accessibili": _c("profilo", "lista", True, "Listini su cui puo' comprare, nell'ordine di preferenza: il primo e' la piazza di casa («come si compra da …»).", scelte=tuple(PIAZZE)),
    "preferenza_ucits": _c("profilo", "bool", True, "Preferisce ETF/fondi UCITS (.MI/.L/.DE) per efficienza fiscale."),
    "leva_ammessa": _c("profilo", "bool", True, "Il comitato puo' proporre strumenti a leva o margine."),
    "short_ammesso": _c("profilo", "bool", True, "Il comitato puo' proporre posizioni corte (short)."),
    # 2 — RISCHIO
    "volatilita_target_pct": _c("rischio", "pct", True, "Volatilita' annua del portafoglio che cerca.", unita="% annua", intervallo=(5, 60)),
    "var99_1g_pct": _c("rischio", "pct", True, "Perdita massima a un giorno che accetta (VaR 99%), in % del patrimonio.", unita="% del patrimonio", intervallo=(0.5, 15)),
    "drawdown_max_pct": _c("rischio", "pct", True, "Drawdown massimo dal picco che accetta sul portafoglio.", unita="%", intervallo=(5, 70)),
    "stress_gfc_pct": _c("rischio", "pct", True, "Perdita massima accettata in uno stress tipo 2008 (replay GFC), in % del NAV.", unita="% del NAV", intervallo=(5, 60)),
    "drawdown_bilaterale": _c("rischio", "bool", True, "Un drawdown si valuta in modo BILATERALE (puo' essere occasione d'acquisto o segnale di vendita): mai vendita automatica."),
    "drawdown_significativo_pct": _c("rischio", "pct", True, "Da quale calo dal massimo a 52 settimane un drawdown e' «significativo» e accende la valutazione bilaterale.", unita="%", intervallo=(5, 60)),
    # 3 — SIZING
    "base_single_pct": _c("sizing", "pct", True, "Size di riferimento di un'azione singola prima degli aggiustamenti per volatilita' e correlazione.", unita="% dell'investito", intervallo=(1, 50)),
    "cap_single_pct": _c("sizing", "pct", True, "Tetto massimo di un'azione singola.", unita="% dell'investito", intervallo=(2, 50)),
    "base_veicolo_pct": _c("sizing", "pct", True, "Size di riferimento di un veicolo gia' diversificato (ETF, fondo, holding).", unita="% dell'investito", intervallo=(2, 100)),
    "cap_veicolo_pct": _c("sizing", "pct", True, "Tetto massimo di un veicolo diversificato.", unita="% dell'investito", intervallo=(5, 100)),
    "cap_settore_pct": _c("sizing", "pct", True, "Tetto della somma delle azioni singole dello stesso settore.", unita="% dell'investito", intervallo=(5, 100)),
    "limite_minimo_pct": _c("sizing", "pct", True, "Sotto questa size gli aggiustamenti non scendono (pavimento del motore).", unita="% dell'investito", intervallo=(0.5, 10)),
    "posizione_minima_pct": _c("sizing", "pct", True, "Sotto questa soglia un taglio o uno spazio non vale un'azione («in linea»).", unita="% del NAV", intervallo=(0.1, 10)),
    "size_nuova_posizione_pct": _c("sizing", "intervallo_pct", True, "Size tipica di una posizione NUOVA (minimo-massimo).", unita="% del capitale", intervallo=(0.5, 20)),
    "max_posizioni": _c("sizing", "int", False, "Numero massimo di posizioni in portafoglio.", unita="posizioni", intervallo=(1, 100)),
    "top3_max_pct": _c("sizing", "pct", False, "Peso massimo delle prime tre posizioni insieme.", unita="% dell'investito", intervallo=(10, 100)),
    # 4 — CASSA
    "cassa_tipica_pct": _c("cassa", "intervallo_pct", True, "Quanta liquidita' tiene di solito (minimo-massimo).", unita="% del capitale", intervallo=(0, 100)),
    "cassa_max_senza_giustificazione_pct": _c("cassa", "intervallo_pct", True, "Oltre questa liquidita' il cash fermo va MOTIVATO con un rischio datato.", unita="% del capitale", intervallo=(0, 100)),
    "cassa_minima_pct": _c("cassa", "pct", False, "Liquidita' sotto cui non si scende mai (sotto, si ricostituisce).", unita="% del capitale", intervallo=(0, 100)),
    "politica_impiego": _c("cassa", "scelta", True, "Come si impiega il cash: prudente (solo con catalyst), neutra, aggressiva (bias al deployment).", scelte=POLITICHE_IMPIEGO),
    "impiego_default_pct": _c("cassa", "intervallo_pct", True, "Quota della liquidita' da impiegare per default in assenza di rischi datati (minimo-massimo).", unita="% della cassa", intervallo=(0, 100)),
    "impiego_finestra_settimane": _c("cassa", "intervallo_int", True, "In quante settimane si impiega quella quota (minimo-massimo).", unita="settimane", intervallo=(1, 12)),
    # 5 — DISCIPLINA
    "taglio_max_senza_condizioni_pct": _c("disciplina", "intervallo_pct", True, "Quanto si puo' tagliare una posizione senza che scattino le condizioni (minimo-massimo).", unita="% della posizione", intervallo=(0, 100)),
    "taglio_con_condizioni_oltre_pct": _c("disciplina", "pct", True, "Oltre questo taglio servono TUTTE le condizioni accese.", unita="% della posizione", intervallo=(0, 100)),
    "condizioni_taglio_oltre": _c("disciplina", "interruttori", True, "Le condizioni richieste per un taglio grande: Sharpe negativo a 12 mesi, nessun catalyst entro 90 giorni, tesi smentita.", scelte=CONDIZIONI_TAGLIO),
    "riproporre_skipped": _c("disciplina", "bool", True, "Una decisione saltata si ripropone se la tesi regge (mai maggiorata dopo due skip)."),
    "pair_trade_per_memo": _c("disciplina", "int", True, "Quanti pair trade al massimo in un memo (0 = nessuno).", unita="per memo", intervallo=(0, 2)),
    "caccia_globale": _c("disciplina", "bool", True, "Le idee nuove si cercano SENZA vincolo di geografia (Giappone, India, Brasile, Golfo, frontiera)."),
    "nuove_idee_per_memo": _c("disciplina", "intervallo_int", True, "Quanti candidati NUOVI (non nel book, non riproposte) deve portare ogni memo (minimo-massimo; 0-0 = sezione non richiesta).", unita="candidati", intervallo=(0, 5)),
    "rotazione_settoriale": _c("disciplina", "bool", True, "Nessun ancoraggio ai settori del book: un settore con tesi degradata va tagliato e il capitale riallocato."),
    "sfidare_le_view": _c("disciplina", "bool", True, "Le view del PM non sono ordini: il comitato deve sfidarle coi numeri quando i dati le smentiscono."),
    # 6 — OPZIONI
    "opzioni_abilitate": _c("opzioni", "bool", True, "Il comitato puo' proporre strutture in opzioni."),
    "strumenti_ammessi": _c("opzioni", "lista", False, "Le strutture ammesse (obbligatorio se le opzioni sono abilitate).", scelte=STRUMENTI_OPZIONI),
    "budget_premio_pct": _c("opzioni", "pct", False, "Premio massimo spendibile in opzioni.", unita="% del patrimonio", intervallo=(0, 20)),
    # 7 — NOTE
    "note_per_il_comitato": _c("note", "testo", False, "Cose che il comitato deve sapere: aree gradite, esclusioni, vincoli personali.", massimo_char=MAX_CHAR_NOTE),
    "aree_gradite": _c("note", "lista_testo", False, "Aree, paesi o temi che gradisce (una voce per riga)."),
    "esclusioni": _c("note", "lista_testo", False, "Settori, paesi o strumenti che NON vuole (una voce per riga)."),
}

# Il PROFILO DI ESEMPIO (spec §2-bis: prudente e generico). Sono numeri INVENTATI di un profilo
# prudente, NON quelli di chi ha scritto il programma: chi lo usa lo rivede e lo salva, e il file
# vale «esempio» finche' non cambia un valore.
ESEMPIO: Dict[str, Dict[str, Any]] = {
    "profilo": {"tipo_investimento": "long_term", "stile": "diversificato", "orizzonte_anni": 5,
                "valuta_base": "EUR", "broker": "", "residenza_fiscale": "",
                "mercati_accessibili": ["MI", "DE", "US"], "preferenza_ucits": True,
                "leva_ammessa": False, "short_ammesso": False},
    "rischio": {"volatilita_target_pct": 15, "var99_1g_pct": 2, "drawdown_max_pct": 20,
                "stress_gfc_pct": 20, "drawdown_bilaterale": True, "drawdown_significativo_pct": 25},
    "sizing": {"base_single_pct": 6, "cap_single_pct": 8, "base_veicolo_pct": 20, "cap_veicolo_pct": 25,
               "cap_settore_pct": 25, "limite_minimo_pct": 1, "posizione_minima_pct": 1,
               "size_nuova_posizione_pct": [1, 2], "max_posizioni": 25, "top3_max_pct": 40},
    "cassa": {"cassa_tipica_pct": [10, 15], "cassa_max_senza_giustificazione_pct": [20, 25],
              "cassa_minima_pct": 5, "politica_impiego": "neutra", "impiego_default_pct": [20, 30],
              "impiego_finestra_settimane": [2, 4]},
    "disciplina": {"taglio_max_senza_condizioni_pct": [15, 25], "taglio_con_condizioni_oltre_pct": 60,
                   "condizioni_taglio_oltre": {"sharpe_12m_negativo": True, "nessun_catalyst_90g": True,
                                               "tesi_smentita": True},
                   "riproporre_skipped": True, "pair_trade_per_memo": 0, "caccia_globale": False,
                   "nuove_idee_per_memo": [1, 2], "rotazione_settoriale": True, "sfidare_le_view": True},
    "opzioni": {"opzioni_abilitate": False, "strumenti_ammessi": [], "budget_premio_pct": None},
    "note": {"note_per_il_comitato": "", "aree_gradite": [], "esclusioni": []},
}

LEGGIMI = ("Il MANDATO del PM: il profilo con cui il comitato e la chat lavorano (quanta cassa e come "
           "impiegarla, orizzonte e concentrazione, tetti del sizing, disciplina dei tagli, opzioni "
           "ammesse, caccia globale). Il file vero e' data/mandato_pm.json, PRIVATO e mai pubblicato: "
           "lo scrive la pagina Mandato (F18) solo dopo anteprima valida e salvataggio; il comitato "
           "non parte finche' non e' dichiarato. `_campi` e' lo schema (tipo, unita', intervallo, "
           "descrizione) e `_esempio` e' un profilo prudente e generico che la pagina puo' copiare "
           "col comando «Compila con un profilo di esempio»: finche' i valori coincidono, il file "
           "vale `origine: esempio` e i memo lo dichiarano. Gli intervalli sono liste [minimo, massimo] "
           "(minimo uguale a massimo = un numero solo).")

SEGNAPOSTO = re.compile(r"\{MANDATO:([a-z_0-9]+)\}")


class MandatoMancante(Exception):
    """Il mandato non c'e' o non vale: percorso, causa (assente | illeggibile | incompleto),
    dettaglio e — se incompleto — i campi da compilare. Chi la riceve DICHIARA, non ripiega."""

    def __init__(self, percorso: str, causa: str, dettaglio: str = "", campi: Optional[List[str]] = None,
                 valori: Optional[Dict[str, Any]] = None, errori: Optional[List[str]] = None):
        self.percorso, self.causa, self.dettaglio = percorso, causa, dettaglio
        self.campi = list(campi or [])
        self.valori = copy.deepcopy(valori) if isinstance(valori, dict) else None
        self.errori = list(errori or [])
        super().__init__(self._testo())

    def _testo(self) -> str:
        if self.causa == "assente":
            return (_message_fmt('mandato del PM assente: %s non esiste — compila la pagina Mandato (F18), oppure copia %s in data/mandato_pm.json e dichiaraci i TUOI valori (campi vuoti = il comitato non parte)', 'PM mandate absent: %s does not exist — complete Mandate (F18), or copy %s to data/mandato_pm.json and declare YOUR values (empty fields prevent committee execution)', (self.percorso, os.path.basename(ESEMPIO_MANDATO))))
        if self.causa == "illeggibile":
            return (_message_fmt('mandato del PM illeggibile (%s): %s — JSON non valido: correggi il file o risalvalo dalla pagina Mandato (F18)', 'PM mandate unreadable (%s): %s — invalid JSON: correct the file or save it again from Mandate (F18)', (self.percorso, self.dettaglio)))
        if self.causa == "in_uso":
            return (_message_fmt('mandato del PM in uso (%s): un altro processo lo sta scrivendo (%s) — riprova fra un istante', 'PM mandate in use (%s): another process is writing it (%s) — retry shortly', (self.percorso, self.dettaglio)))
        if self.causa == "esempio":
            return (_message_fmt("il profilo di esempio del repo (%s) non si legge: %s — il checkout e' rotto, non il tuo mandato", 'The repository example profile (%s) cannot be read: %s — the checkout is broken, not your mandate', (self.percorso, self.dettaglio)))
        return (_message_fmt('mandato del PM incompleto o non valido (%s): %d campi da sistemare — %s — compilali nella pagina Mandato (F18)', 'PM mandate incomplete or invalid (%s): %d fields need attention — %s — complete them in Mandate (F18)', (self.percorso, len(self.campi), self.dettaglio)))


# --------------------------------------------------------------------------- schema e validazione

def campi_vuoti() -> Dict[str, Any]:
    """Il profilo del primo accesso: ogni campo a None (decisione PM: nessun default nel codice)."""
    out: Dict[str, Any] = {"versione": VERSIONE_SCHEMA, "dichiarato_il": None, "origine": None}
    for b in BLOCCHI:
        out[b] = {n: None for n, c in CAMPI.items() if c["blocco"] == b}
    return out


_FIELD_DESCRIPTIONS_EN = {'tipo_investimento': 'Investment horizon: long_term (years), medio_termine (months), trading (weeks).', 'stile': 'concentrato = accepts concentrated convictions; diversificato = no name above its cap.', 'orizzonte_anni': 'Average holding period of a position.', 'valuta_base': 'Currency used to measure wealth (ISO code: EUR, USD, GBP...).', 'broker': 'Broker used (information only).', 'residenza_fiscale': 'Country of tax residence (informs the UCITS and vehicle preference).', 'mercati_accessibili': 'Accessible exchanges, in order of preference: the first is the home exchange (how to buy from there).', 'preferenza_ucits': 'Prefers UCITS ETFs/funds (.MI/.L/.DE) for tax efficiency.', 'leva_ammessa': 'The committee may propose leveraged instruments or margin.', 'short_ammesso': 'The committee may propose short positions.', 'volatilita_target_pct': 'Target annual portfolio volatility.', 'var99_1g_pct': 'Maximum accepted one-day loss (VaR 99%), as a percentage of wealth.', 'drawdown_max_pct': 'Maximum accepted peak-to-trough portfolio drawdown.', 'stress_gfc_pct': 'Maximum accepted loss in a 2008-type stress (GFC replay), as a percentage of NAV.', 'drawdown_bilaterale': 'Assess drawdowns from both sides: possible buying opportunity or selling signal, never an automatic sale.', 'drawdown_significativo_pct': 'Decline from the 52-week high that counts as significant and triggers the two-sided assessment.', 'base_single_pct': 'Reference single-stock weight before volatility and correlation adjustments.', 'cap_single_pct': 'Maximum single-stock weight.', 'base_veicolo_pct': 'Reference weight of an already diversified vehicle (ETF, fund, holding company).', 'cap_veicolo_pct': 'Maximum diversified-vehicle weight.', 'cap_settore_pct': 'Cap on the combined weight of single stocks in the same sector.', 'limite_minimo_pct': 'Adjustments cannot go below this weight (engine floor).', 'posizione_minima_pct': 'Below this threshold, a reduction or available room does not justify action (in line).', 'size_nuova_posizione_pct': 'Typical NEW-position weight (minimum–maximum).', 'max_posizioni': 'Maximum number of portfolio positions.', 'top3_max_pct': 'Maximum combined weight of the three largest positions.', 'cassa_tipica_pct': 'Typical cash holding (minimum–maximum).', 'cassa_max_senza_giustificazione_pct': 'Above this cash range, idle cash requires justification with a dated risk.', 'cassa_minima_pct': 'Cash floor: replenish cash below it.', 'politica_impiego': 'Cash deployment: prudent (only with a catalyst), neutral, aggressive (deployment bias).', 'impiego_default_pct': 'Default share of cash to deploy in the absence of dated risks (minimum–maximum).', 'impiego_finestra_settimane': 'Weeks for deploying that share (minimum–maximum).', 'taglio_max_senza_condizioni_pct': 'Position reduction permitted without the additional conditions (minimum–maximum).', 'taglio_con_condizioni_oltre_pct': 'Beyond this reduction, ALL enabled conditions must hold.', 'condizioni_taglio_oltre': 'Conditions for a large reduction: negative 12-month Sharpe, no catalyst within 90 days, disproved thesis.', 'riproporre_skipped': 'Revisit a skipped decision if the thesis holds (never increase it after two skips).', 'pair_trade_per_memo': 'Maximum pair trades in one memo (0 = none).', 'caccia_globale': 'Search for new ideas WITHOUT geographical restrictions (Japan, India, Brazil, Gulf, frontier markets).', 'nuove_idee_per_memo': 'NEW candidates per memo (outside the book, not repeated proposals), minimum–maximum; 0–0 means the section is not required.', 'rotazione_settoriale': 'No anchoring to existing sectors: reduce a sector with a deteriorated thesis and reallocate the capital.', 'sfidare_le_view': 'The PM’s views are not orders: challenge them with numbers when the evidence contradicts them.', 'opzioni_abilitate': 'The committee may propose option structures.', 'strumenti_ammessi': 'Allowed structures (required when options are enabled).', 'budget_premio_pct': 'Maximum option premium expenditure.', 'note_per_il_comitato': 'Information for the committee: preferred areas, exclusions and personal constraints.', 'aree_gradite': 'Preferred areas, countries or themes (one item per line).', 'esclusioni': 'Excluded sectors, countries or instruments (one item per line).'}
_UNITS_EN = {'anni': 'years', '% annua': '% annually', '% del patrimonio': '% of wealth', '% del NAV': '% of NAV', "% dell'investito": '% of invested assets', '% del capitale': '% of capital', 'posizioni': 'positions', '% della cassa': '% of cash', 'settimane': 'weeks', '% della posizione': '% of the position', 'per memo': 'per memo', 'candidati': 'candidates'}


def descrizione_campi() -> Dict[str, Dict[str, Any]]:
    """Lo schema per la pagina e per il file di esempio (una copia, JSON-compatibile)."""
    out = {n: dict(c) for n, c in CAMPI.items()}
    for n, c in out.items():
        c["descrizione"] = _message(c["descrizione"], _FIELD_DESCRIPTIONS_EN[n])
        if c["unita"] and c["unita"] != "%":
            c["unita"] = _message(c["unita"], _UNITS_EN[c["unita"]])
    return out


def profilo_esempio() -> Dict[str, Any]:
    """Il profilo di esempio, letto dal file committato (la sezione `_esempio`)."""
    with open(ESEMPIO_MANDATO, encoding="utf-8") as fh:
        es = json.load(fh)
    return {b: dict(es["_esempio"][b]) for b in BLOCCHI}


def _numero(v: Any) -> bool:
    """Un numero vero: niente bool, niente NaN/Infinity (json.load li accetta: review 05/09)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _canonico(v: Any) -> Any:
    """15.0 -> 15, 0.5 resta 0.5: cosi' l'impronta non cambia per la forma del numero."""
    return int(v) if float(v).is_integer() else float(v)


def _fmt_conteggio(v: Any) -> str:
    """[2, 3] -> «2-3 candidati»; [0, 3] -> «fino a 3 candidati» (un minimo a zero non e' un obbligo)."""
    a, b = v
    return (_ui_text('fino a %s candidati', 'up to %s candidates') % _fmt(b)) if a == 0 else (_fmt_int(v) + _ui_text(' candidati', ' candidates'))


def _fmt(v: Any) -> str:
    """Un numero come lo scrive il PM: 12 -> «12», 0.25 -> «0,25», 2.5 -> «2,5»."""
    if _numero(v) and float(v) == int(v):
        return str(int(v))
    return ("%g" % v).replace(".", "," if current_language() == "it" else ".")


def _fmt_int(v: Any) -> str:
    """Un intervallo [a, b] come «a-b», o «a» se a == b."""
    a, b = v
    return _fmt(a) if a == b else _fmt(a) + "-" + _fmt(b)


def _message_fmt(italian, english, values):
    return _message(italian % render_payload(values, language="it"),
                    english % render_payload(values, language="en"))


def _valida_campo(nome: str, c: Dict[str, Any], v: Any) -> Tuple[Any, Optional[str]]:
    """(valore normalizzato, errore). None = campo vuoto: errore solo se obbligatorio."""
    t = c["tipo"]
    vuoto = v is None or (isinstance(v, str) and t in ("testo",) and v == "")
    if vuoto:
        if c["obbligatorio"]:
            return None, _message_fmt("%s: mancante (obbligatorio)", '%s: missing (required)', nome)
        return ("" if t == "testo" else [] if t in ("lista", "lista_testo") else None), None
    lo, hi = (c["intervallo"] or (None, None))
    if t == "scelta":
        if v not in c["scelte"]:
            return None, _message_fmt("%s: valore %r non ammesso (scelte: %s)", '%s: value %r is not allowed (choices: %s)', (nome, v, ", ".join(c["scelte"])))
        return v, None
    if t == "bool":
        if not isinstance(v, bool):
            return None, _message_fmt("%s: atteso si/no (true/false), trovato %r", '%s: expected yes/no (true/false), got %r', (nome, v))
        return v, None
    if t == "int":
        if not (isinstance(v, int) and not isinstance(v, bool)):
            return None, _message_fmt("%s: atteso un intero, trovato %r", '%s: expected an integer, got %r', (nome, v))
        if not (lo <= v <= hi):
            return None, _message_fmt("%s: %s fuori dall'intervallo %s-%s", '%s: %s outside the range %s-%s', (nome, _fmt(v), _fmt(lo), _fmt(hi)))
        return v, None
    if t in ("num", "pct"):
        if not _numero(v):
            return None, _message_fmt("%s: atteso un numero, trovato %r", '%s: expected a number, got %r', (nome, v))
        if not (lo <= v <= hi):
            return None, _message_fmt("%s: %s fuori dall'intervallo %s-%s", '%s: %s outside the range %s-%s', (nome, _fmt(v), _fmt(lo), _fmt(hi)))
        return _canonico(v), None
    if t in ("intervallo_pct", "intervallo_int"):
        ok = (isinstance(v, (list, tuple)) and len(v) == 2 and all(_numero(x) for x in v)
              and (t == "intervallo_pct" or all(isinstance(x, int) for x in v)))
        if not ok:
            return None, _message_fmt("%s: atteso un intervallo [minimo, massimo], trovato %r", '%s: expected a range [minimum, maximum], got %r', (nome, v))
        a, b = v
        if not (lo <= a <= b <= hi):
            return None, (_message_fmt("%s: intervallo [%s, %s] non valido (minimo <= massimo, entrambi fra %s e %s)", '%s: invalid range [%s, %s] (minimum <= maximum, both between %s and %s)', (nome, _fmt(a), _fmt(b), _fmt(lo), _fmt(hi))))
        return [_canonico(a), _canonico(b)], None
    if t == "lista":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return None, _message_fmt("%s: attesa una lista di codici, trovato %r", '%s: expected a list of codes, got %r', (nome, v))
        fuori = [x for x in v if x not in c["scelte"]]
        if fuori:
            return None, _message_fmt("%s: %s fuori dal vocabolario (%s)", '%s: %s outside the allowed vocabulary (%s)', (nome, ", ".join(fuori), ", ".join(c["scelte"])))
        if len(set(v)) != len(v):
            return None, _message_fmt("%s: voci ripetute", '%s: duplicate entries', nome)
        if c["obbligatorio"] and not v:
            return None, _message_fmt("%s: lista vuota (obbligatorio)", '%s: empty list (required)', nome)
        return list(v), None
    if t == "lista_testo":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return None, _message_fmt("%s: attesa una lista di testi, trovato %r", '%s: expected a list of text entries, got %r', (nome, v))
        if len(v) > 50 or any(len(x) > 200 for x in v):
            return None, _message_fmt("%s: al massimo 50 voci da 200 caratteri", '%s: up to 50 entries of 200 characters', nome)
        if any("{MANDATO:" in x for x in v):
            return None, _message_fmt("%s: il testo non puo' contenere «{MANDATO:» (e' il segnaposto dei prompt)", '%s: text cannot contain «{MANDATO:» (the prompt placeholder)', nome)
        return [x.strip() for x in v if x.strip()], None
    if t == "interruttori":
        if not isinstance(v, dict) or set(v) != set(c["scelte"]) or not all(isinstance(x, bool) for x in v.values()):
            return None, _message_fmt("%s: attesi gli interruttori %s (si/no ciascuno), trovato %r", '%s: expected switches %s (yes/no each), got %r', (nome, ", ".join(c["scelte"]), v))
        return {k: bool(v[k]) for k in c["scelte"]}, None
    if t == "testo":
        if not isinstance(v, str):
            return None, _message_fmt("%s: atteso un testo, trovato %r", '%s: expected text, got %r', (nome, v))
        if len(v) > (c["massimo_char"] or MAX_CHAR_TESTO):
            return None, _message_fmt("%s: %d caratteri, il massimo e' %d", '%s: %d characters, maximum %d', (nome, len(v), c["massimo_char"] or MAX_CHAR_TESTO))
        if "{MANDATO:" in v:
            # review 05/09: passava valida e faceva sollevare `compila` («segnaposto rimasti») al Capo
            return None, _message_fmt("%s: il testo non puo' contenere «{MANDATO:» (e' il segnaposto dei prompt)", '%s: text cannot contain «{MANDATO:» (the prompt placeholder)', nome)
        return v.strip(), None
    if t == "valuta":
        if not (isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v.strip().upper())):
            return None, _message_fmt("%s: atteso un codice ISO di tre lettere (EUR, USD...), trovato %r", '%s: expected a three-letter ISO code (EUR, USD...), got %r', (nome, v))
        return v.strip().upper(), None
    return None, _message_fmt("%s: tipo %r sconosciuto", '%s: unknown type %r', (nome, t))


def vol_annua_implicita(var99_1g_pct: Optional[float]) -> Optional[float]:
    """La volatilita' annua (in %) che un VaR 99% a un giorno IMPLICA.

    Ipotesi DICHIARATE, non nascoste: rendimenti normali, indipendenti, media zero,
    252 giorni di borsa. Non e' una misura del portafoglio — e' la traduzione di un
    numero dichiarato dal PM in un altro numero dichiarato dal PM, per poterli
    confrontare. VaR non dichiarato = None, mai uno zero di comodo.
    La usa `valida` per la coerenza, e serve alla pagina Mandato per mostrare a chi
    compila che volatilita' sta implicando col VaR che scrive.
    """
    if var99_1g_pct is None:
        return None
    return (float(var99_1g_pct) / Z99_UNA_CODA) * math.sqrt(GIORNI_BORSA)


def var_da_vol_annua(vol_annua_pct: Optional[float]) -> Optional[float]:
    """L'inverso di `vol_annua_implicita`: il VaR 99% a un giorno che una volatilita'
    annua implica. Stesse ipotesi. Serve alla pagina per proporre un VaR coerente."""
    if vol_annua_pct is None:
        return None
    return (float(vol_annua_pct) / math.sqrt(GIORNI_BORSA)) * Z99_UNA_CODA


def valida(grezzo: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(mandato normalizzato, errori). Errori = lista di «campo: motivo», vuota se il mandato vale.
    Pura: non legge ne' scrive niente. La usano `carica`, `salva` e `PUT /mandato`."""
    errori: List[str] = []
    m: Dict[str, Any] = {"versione": VERSIONE_SCHEMA, "dichiarato_il": None, "origine": None}
    if not isinstance(grezzo, dict):
        return m, [_message_fmt("mandato: atteso un oggetto JSON con i blocchi %s", 'mandato: expected a JSON object with sections %s', ", ".join(BLOCCHI))]
    m["dichiarato_il"] = grezzo.get("dichiarato_il")
    m["origine"] = grezzo.get("origine")
    ver = grezzo.get("versione")
    if ver is not None and ver != VERSIONE_SCHEMA:
        errori.append(_message_fmt("versione: schema %r non supportato (atteso %d)", 'versione: schema %r not supported (expected %d)', (ver, VERSIONE_SCHEMA)))
    if m["dichiarato_il"] is not None and not (isinstance(m["dichiarato_il"], str)
                                                and _data_it(m["dichiarato_il"]) != "data n.d."):
        errori.append(_message_fmt("dichiarato_il: attesa una data AAAA-MM-GG, trovato %r", 'dichiarato_il: expected a YYYY-MM-DD date, got %r', (m["dichiarato_il"],)))
    for b in BLOCCHI:
        blocco = grezzo.get(b)
        if blocco is None:
            blocco = {}
        if not isinstance(blocco, dict):
            errori.append(_message_fmt("%s: atteso un oggetto con i campi del blocco", '%s: expected an object containing the section fields', b))
            blocco = {}
        m[b] = {}
        for n, c in CAMPI.items():
            if c["blocco"] != b:
                continue
            val, err = _valida_campo(n, c, blocco.get(n))
            if err:
                errori.append(err)
            m[b][n] = val
        ignoti = sorted(k for k in blocco if k not in m[b] and not k.startswith("_"))
        if ignoti:
            errori.append(_message_fmt("%s: campi sconosciuti %s", '%s: unknown fields %s', (b, ", ".join(ignoti))))
    # 06/09 (lotto B, difetto 2 della coda di A2): al PRIMO LIVELLO niente spariva ne'
    # restava — spariva e basta, e con esso il `_nota` di provenienza del file del PM.
    # Le chiavi «_» sono meta (provenienza, commenti), non valori: si CONSERVANO, e non
    # muovono impronta ne' origine perche' `_valori` guarda i soli sette blocchi.
    # Ogni altra chiave ignota viene NOMINATA, come fanno gia' i blocchi: cosi' alla
    # prossima chiave nuova il difetto non torna identico e invisibile.
    for k in grezzo:
        if k.startswith("_"):
            m[k] = grezzo[k]
    ignoti_top = sorted(k for k in grezzo
                        if k not in CHIAVI_PRIMO_LIVELLO and not k.startswith("_"))
    if ignoti_top:
        errori.append(_message_fmt("mandato: campi sconosciuti di primo livello %s", 'mandato: unknown top-level fields %s', ", ".join(ignoti_top)))
    if errori:
        return m, errori

    # coerenze fra campi (solo su valori gia' validi, cosi' ogni errore nomina un campo)
    s, r, k, d, o = m["sizing"], m["rischio"], m["cassa"], m["disciplina"], m["opzioni"]
    if s["base_single_pct"] > s["cap_single_pct"]:
        errori.append(_message_fmt("base_single_pct: la baseline (%s) supera il cap single (%s)", 'base_single_pct: baseline (%s) exceeds the single-stock cap (%s)', (_fmt(s["base_single_pct"]), _fmt(s["cap_single_pct"]))))
    if s["base_veicolo_pct"] > s["cap_veicolo_pct"]:
        errori.append(_message_fmt("base_veicolo_pct: la baseline (%s) supera il cap veicolo (%s)", 'base_veicolo_pct: baseline (%s) exceeds the vehicle cap (%s)', (_fmt(s["base_veicolo_pct"]), _fmt(s["cap_veicolo_pct"]))))
    if s["limite_minimo_pct"] > s["base_single_pct"]:
        errori.append(_message_fmt("limite_minimo_pct: il pavimento (%s) supera la baseline single (%s)", 'limite_minimo_pct: floor (%s) exceeds the single-stock baseline (%s)', (_fmt(s["limite_minimo_pct"]), _fmt(s["base_single_pct"]))))
    if s["posizione_minima_pct"] > s["cap_single_pct"]:
        errori.append(_message_fmt("posizione_minima_pct: la soglia (%s) supera il cap single (%s)", 'posizione_minima_pct: threshold (%s) exceeds the single-stock cap (%s)', (_fmt(s["posizione_minima_pct"]), _fmt(s["cap_single_pct"]))))
    if s["top3_max_pct"] is not None and s["top3_max_pct"] < s["cap_single_pct"]:
        errori.append(_message_fmt("top3_max_pct: il peso delle prime tre (%s) e' sotto il cap di una singola (%s)", 'top3_max_pct: combined top-three weight (%s) is below the single-stock cap (%s)', (_fmt(s["top3_max_pct"]), _fmt(s["cap_single_pct"]))))
    if r["var99_1g_pct"] > r["drawdown_max_pct"]:
        errori.append(_message_fmt("var99_1g_pct: il VaR a un giorno (%s) supera il drawdown massimo (%s)", 'var99_1g_pct: one-day VaR (%s) exceeds maximum drawdown (%s)', (_fmt(r["var99_1g_pct"]), _fmt(r["drawdown_max_pct"]))))
    if r["var99_1g_pct"] > r["stress_gfc_pct"]:
        errori.append(_message_fmt("var99_1g_pct: il VaR a un giorno (%s) supera la perdita massima nello stress (%s)", 'var99_1g_pct: one-day VaR (%s) exceeds maximum stress loss (%s)', (_fmt(r["var99_1g_pct"]), _fmt(r["stress_gfc_pct"]))))
    # 06/09 (lotto B, difetto 3): il VaR dichiarato e il target di volatilita' parlavano di
    # due portafogli diversi senza che niente lo dicesse. La banda e' LARGA per decisione
    # del PM: si rifiuta solo l'incoerenza grossa, non una prudenza dichiarata al ribasso.
    # Questa e' la PRIMA regola che consuma `volatilita_target_pct`: fino a oggi il campo
    # viveva solo nello schema, nell'esempio e nella prosa del prompt (segnalazione di
    # `bellomberg-65`, verificata col grep). Cioe' una dichiarazione diventa un vincolo.
    # BASE DEI DUE TERMINI, dichiarata perche' non sia ereditata a scatola chiusa: si
    # confrontano due campi DEL MANDATO, entrambi dichiarati dal PM sul suo patrimonio —
    # nessun numero dei motori entra qui, quindi non c'e' il riscalamento investito->NAV
    # (`var99_1d_book_pct` -> `var99_1d_nav_pct`) che serve quando si confronta il mandato
    # con l'uscita di `sizing_engine`. Resta un'ambiguita' nello SCHEMA, non nella regola:
    # `var99_1g_pct` dice «% del patrimonio», `volatilita_target_pct` dice «del
    # portafoglio» senza precisare. Sono trattati come la stessa base.
    _vol_var = vol_annua_implicita(r["var99_1g_pct"])
    if _vol_var is not None and _vol_var > r["volatilita_target_pct"] * BANDA_VOL_SU_VAR:
        errori.append(_message_fmt("volatilita_target_pct: il VaR dichiarato (%s%%) implica una volatilita' annua del %s%%, oltre %s volte il target dichiarato (%s%%): alza il target o abbassa il VaR", 'volatilita_target_pct: declared VaR (%s%%) implies annual volatility of %s%%, above %s times the declared target (%s%%): raise the target or lower VaR', (_fmt(r["var99_1g_pct"]), _fmt(round(_vol_var, 1)),
                         _fmt(BANDA_VOL_SU_VAR), _fmt(r["volatilita_target_pct"]))))
    # 06/09 (lotto B, difetto 4): uno stress «2008» E' un drawdown, quindi non puo' stare
    # sopra il drawdown massimo dichiarato. UGUALI e' lecito di proposito: e' il profilo di
    # esempio del repo (20 e 20), e la regola stretta lo rifiuterebbe facendo cadere la
    # fixture di sessione che lo salva, cioe' l'intera suite. Misurato prima di scriverla.
    if r["drawdown_max_pct"] < r["stress_gfc_pct"]:
        errori.append(_message_fmt("drawdown_max_pct: il drawdown massimo (%s) sta sotto la perdita nello stress (%s): uno stress e' un drawdown", 'drawdown_max_pct: maximum drawdown (%s) is below stress loss (%s): a stress is a drawdown', (_fmt(r["drawdown_max_pct"]), _fmt(r["stress_gfc_pct"]))))
    if not o["opzioni_abilitate"] and (o["strumenti_ammessi"] or o["budget_premio_pct"] is not None):
        errori.append(_message("strumenti_ammessi: opzioni disabilitate ma strumenti o budget dichiarati: svuotali o abilita le opzioni", 'strumenti_ammessi: options disabled but structures or budget declared: clear them or enable options'))
    if k["cassa_minima_pct"] is not None and k["cassa_minima_pct"] > k["cassa_tipica_pct"][0]:
        errori.append(_message_fmt("cassa_minima_pct: la minima (%s) supera la banda tipica (%s)", 'cassa_minima_pct: minimum (%s) exceeds the typical range (%s)', (_fmt(k["cassa_minima_pct"]), _fmt_int(k["cassa_tipica_pct"]))))
    if d["taglio_max_senza_condizioni_pct"][1] > d["taglio_con_condizioni_oltre_pct"]:
        errori.append(_message_fmt("taglio_max_senza_condizioni_pct: il taglio libero (%s) supera la soglia delle condizioni (%s)", 'taglio_max_senza_condizioni_pct: unconditional reduction (%s) exceeds the conditions threshold (%s)', (_fmt_int(d["taglio_max_senza_condizioni_pct"]), _fmt(d["taglio_con_condizioni_oltre_pct"]))))
    if o["opzioni_abilitate"] and not o["strumenti_ammessi"]:
        errori.append(_message("strumenti_ammessi: vuoto con le opzioni abilitate: scegli almeno uno strumento", 'strumenti_ammessi: empty with options enabled: choose at least one structure'))
    return m, errori


def _valori(m: Dict[str, Any]) -> Dict[str, Any]:
    return {b: m[b] for b in BLOCCHI}


def _origine(m: Dict[str, Any]) -> str:
    """«esempio» se i valori coincidono col profilo di esempio, altrimenti «personalizzato»:
    si MISURA dai valori, la parola nel file e' solo informativa. Esempio del repo illeggibile =
    dichiarato (review 05/09: prima tornava «personalizzato» zitto)."""
    try:
        es, err = valida(profilo_esempio())
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise MandatoMancante(ESEMPIO_MANDATO, "esempio", "%s: %s" % (type(e).__name__, str(e)[:120]))
    if err:
        raise MandatoMancante(ESEMPIO_MANDATO, "esempio", _ui_text("profilo di esempio non valido: ", "invalid example profile: ") + "; ".join(err)[:200])
    return "esempio" if _valori(m) == _valori(es) else "personalizzato"


# --------------------------------------------------------------------------- lettura e scrittura

def _campi_obbligatori_vuoti(grezzo: Any) -> List[str]:
    """Solo campi obbligatori davvero vuoti; un valore invalido resta un errore, non un mancante."""
    if not isinstance(grezzo, dict):
        return ["mandato"]
    mancanti = []
    for nome, campo in CAMPI.items():
        if not campo["obbligatorio"]:
            continue
        blocco = grezzo.get(campo["blocco"])
        valore = blocco.get(nome) if isinstance(blocco, dict) else None
        if valore is None or valore == "" or valore == [] or valore == {}:
            mancanti.append(nome)
    return mancanti


def _esempio_per_api() -> Dict[str, Any]:
    """Profilo copiabile della pagina, validato ma tenuto separato dai valori del PM."""
    try:
        esempio = profilo_esempio()
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise MandatoMancante(ESEMPIO_MANDATO, "esempio", "%s: %s" % (type(e).__name__, str(e)[:120]))
    _normalizzato, errori = valida(esempio)
    if errori:
        raise MandatoMancante(ESEMPIO_MANDATO, "esempio",
                              _ui_text("profilo di esempio non valido: ", "invalid example profile: ") + "; ".join(errori)[:200],
                              errori=errori)
    return copy.deepcopy(esempio)

def carica(path: Optional[str] = None) -> Dict[str, Any]:
    """Il mandato, letto DAL DISCO ORA. Assente / illeggibile / incompleto = MandatoMancante."""
    p = path or PERCORSO_MANDATO
    if not os.path.exists(p):
        raise MandatoMancante(p, "assente")
    ultimo = None
    for _tentativo in range(3):
        try:
            with open(p, encoding="utf-8") as fh:
                grezzo = json.load(fh)
            break
        except PermissionError as e:
            # `salva` fa os.replace mentre qualcuno legge: su Windows il lettore prende
            # PermissionError (lezione 03/09). Tre tentativi, poi dichiarato come «in uso».
            ultimo = e
            time.sleep(0.05)
        except Exception as e:
            raise MandatoMancante(p, "illeggibile", "%s: %s" % (type(e).__name__, str(e)[:160]))
    else:
        raise MandatoMancante(p, "in_uso", "PermissionError dopo 3 tentativi: %s" % str(ultimo)[:120])
    m, errori = valida(grezzo)
    if errori:
        raise MandatoMancante(p, "incompleto", join_messages("; ", errori),
                              campi=_campi_obbligatori_vuoti(grezzo),
                              valori=grezzo, errori=errori)
    m["origine"] = _origine(m)
    return m


# Le cause che NON vogliono dire «non dichiarato» ma «non leggibile ADESSO»: il file c'e'
# (o l'installazione e' rotta), e chi serve un'API ne fa un 503 invece di un modulo vuoto.
# Appiattirle su `dichiarato: false` fa aprire alla pagina il primo accesso a schermo
# intero SOPRA un mandato buono, e il PM crede di aver perso tutto.
CAUSE_NON_LEGGIBILE = ("in_uso", "illeggibile", "esempio")


def stato_per_api(path: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[str]]:
    """(corpo, non_leggibile) — lo stato del mandato come lo consuma la pagina Mandato (F18).

    `corpo` porta SEMPRE `campi` (lo schema): senza, la pagina non sa cosa chiedere
    nemmeno al primo accesso, quando di valori non ce n'e' nessuno.
    `non_leggibile` e' la causa da rendere come guasto (v. `CAUSE_NON_LEGGIBILE`), None
    quando lo stato e' uno di quelli che la pagina deve DISEGNARE: mandato valido,
    mai compilato (`assente`), o compilato a meta' (`incompleto`, coi campi da riempire).
    Copiare il profilo di esempio E' un modo legittimo di dichiarare: il mandato vale, e
    `origine: esempio` permette alla pagina di dirlo.
    Rilegge dal disco a ogni chiamata, come tutto il resto del mandato."""
    corpo: Dict[str, Any] = {
        "dichiarato": False,
        "causa": None,
        "dettaglio": None,
        "campi_mancanti": [],
        "valori": None,
        "origine": None,
        "impronta": None,
        "dichiarato_il": None,
        "campi": descrizione_campi(),
        "errori": [],
        "esempio": None,
    }
    try:
        corpo["esempio"] = _esempio_per_api()
    except MandatoMancante as e:
        corpo["causa"] = e.causa
        corpo["dettaglio"] = error_text(e)
        corpo["errori"] = list(e.errori)
        return corpo, "esempio"
    try:
        m = carica(path)
    except MandatoMancante as e:
        corpo["causa"] = e.causa
        corpo["dettaglio"] = error_text(e)
        corpo["campi_mancanti"] = list(e.campi)
        corpo["valori"] = copy.deepcopy(e.valori)
        corpo["errori"] = list(e.errori)
        return corpo, (e.causa if e.causa in CAUSE_NON_LEGGIBILE else None)
    corpo.update(dichiarato=True, valori=m, origine=m.get("origine"),
                 impronta=impronta(m), dichiarato_il=m.get("dichiarato_il"))
    return corpo, None


@scoped_language
def anteprima(grezzo: Any) -> Dict[str, Any]:
    """Valida un mandato e rende il testo esatto dei modelli, senza scrivere su disco."""
    m, errori = valida(grezzo)
    if errori:
        raise ValueError(join_messages("\n", errori))
    m["origine"] = _origine(m)
    return {"testo": blocco_prompt(m), "impronta": impronta(m), "origine": m["origine"], "output_language": current_language()}


def dichiarato(path: Optional[str] = None) -> bool:
    try:
        carica(path)
        return True
    except MandatoMancante:
        return False


def _scrivi_atomico(dest: str, testo: str) -> None:
    cartella = os.path.dirname(os.path.abspath(dest))
    os.makedirs(cartella, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(dest) + ".", suffix=".tmp", dir=cartella)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(testo)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def salva(mandato: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    """Valida, copia il precedente in <cartella>/backups/mandato_pm_<ts>.json, scrive atomico
    (temporaneo + os.replace) e RILEGGE col caricatore: torna cio' che il disco dice.
    Un mandato non valido = ValueError coi campi, e il file resta com'era."""
    p = path or PERCORSO_MANDATO
    m, errori = valida(mandato)
    if errori:
        raise ValueError(join_messages("", [_message("mandato non valido: ", "invalid mandate: "), join_messages("; ", errori)]))
    m["versione"] = VERSIONE_SCHEMA
    m["dichiarato_il"] = date.today().isoformat()
    m["origine"] = _origine(m)
    if os.path.exists(p):
        cartella = os.path.join(os.path.dirname(os.path.abspath(p)), "backups")
        os.makedirs(cartella, exist_ok=True)
        base = os.path.join(cartella, "mandato_pm_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        dest, n = base + ".json", 0
        while os.path.exists(dest):
            n += 1
            dest = "%s_%d.json" % (base, n)
        with open(p, encoding="utf-8") as fh:
            vecchio = fh.read()
        _scrivi_atomico(dest, vecchio)
        # 06/09 (lotto B, difetto 2): la pagina Mandato rimanda i sette blocchi, non le meta.
        # Le chiavi «_» che stanno gia' nel file (la provenienza dei valori del PM) restano:
        # un salvataggio non e' il posto dove si perde una nota. Se il file di prima non si
        # legge non c'e' nessuna nota da conservare — non e' un ripiego, e' il vuoto.
        try:
            precedente = json.loads(vecchio)
        except (ValueError, TypeError):
            precedente = {}
        if isinstance(precedente, dict):
            for k, v in precedente.items():
                if k.startswith("_") and k not in m:
                    m[k] = v
    _scrivi_atomico(p, json.dumps(m, ensure_ascii=False, indent=2) + "\n")
    return carica(p)


def scrivi_esempio(path: Optional[str] = None) -> str:
    """Genera mandato_pm.example.json dallo schema: campi VUOTI, `_campi`, `_esempio`."""
    p = path or ESEMPIO_MANDATO
    es = campi_vuoti()
    out: Dict[str, Any] = {"_leggimi": LEGGIMI, "versione": es["versione"], "dichiarato_il": None, "origine": None}
    for b in BLOCCHI:
        out[b] = es[b]
    # 13/09: il file di esempio committato e' italiano; senza contesto le descrizioni uscivano
    # nella preferenza salvata da chi lancia il generatore
    with language_context("it"):
        out["_campi"] = descrizione_campi()
    out["_esempio"] = {b: dict(ESEMPIO[b]) for b in BLOCCHI}
    _scrivi_atomico(p, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return p


# --------------------------------------------------------------------------- impronta e intestazione

def impronta(m: Dict[str, Any]) -> str:
    """sha256 dei sette blocchi in JSON canonico (chiavi ordinate): stabile al riordino,
    sensibile a ogni valore. Le stampe usano i primi 8 esadecimali."""
    canon = json.dumps(_valori(m), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _data_it(iso: Optional[str]) -> str:
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return "data n.d."


def intestazione(m: Dict[str, Any]) -> str:
    riga = _ui_text("MANDATO DEL PM (dichiarato il %s, impronta %s)", 'PM MANDATE (declared on %s, fingerprint %s)') % (_data_it(m.get("dichiarato_il")), impronta(m)[:8])
    if m.get("origine") == "esempio":
        riga += _ui_text(" — PROFILO DI ESEMPIO, NON PERSONALIZZATO: l'utente non ha ancora dichiarato il suo mandato", ' — EXAMPLE PROFILE, NOT PERSONALIZED: the user has not yet declared their own mandate')
    return riga


def riga_senza_mandato() -> str:
    return (_ui_text("MANDATO NON DICHIARATO: nessuna preferenza del PM va assunta (cassa, concentrazione, tagli, "
            "opzioni, caccia globale); dillo nel testo e invita a compilare la pagina Mandato (F18).", 'MANDATE NOT DECLARED: do not assume any PM preference (cash, concentration, reductions, options, global search); say so and invite the user to complete Mandate (F18).'))


# --------------------------------------------------------------------------- le frasi per i modelli

def _si_no(v: bool) -> str:
    return _ui_text("si", "yes") if v else "no"


def _tipo_frase(tipo: str) -> str:
    return {"long_term": "LONG-TERM", "medio_termine": _ui_text('a MEDIO TERMINE', 'MEDIUM-TERM'),
            "trading": _ui_text('con orizzonte di TRADING', 'with a TRADING horizon')}[tipo]


def _view_frase(tipo: str) -> str:
    return {"long_term": "long-term", "medio_termine": _ui_text('di medio termine', 'medium-term'), "trading": _ui_text('di trading', 'trading')}[tipo]


def _city(code):
    translated = {"MI":"Milan","L":"London","DE":"Frankfurt","FRA":"Frankfurt","PA":"Paris","BR":"Brussels","SW":"Zurich","SA":"Sao Paulo"}
    return _ui_text(PIAZZE[code], translated.get(code, PIAZZE[code]))


def _piazza_di_casa(m: Dict[str, Any]) -> str:
    codici = m["profilo"]["mercati_accessibili"]
    return _city(codici[0]) if codici else _ui_text("casa", "home")


def _piazze(m: Dict[str, Any]) -> str:
    return ", ".join("%s (%s)" % (_city(c), c) for c in m["profilo"]["mercati_accessibili"])


def _sez_profilo_rischio(m: Dict[str, Any]) -> str:
    p, r, s = m["profilo"], m["rischio"], m["sizing"]
    stile = _ui_text('CONCENTRATO sulle conviction', 'CONCENTRATED convictions') if p["stile"] == "concentrato" else _ui_text('DIVERSIFICATO', 'DIVERSIFIED')
    righe = [
        _ui_text("PROFILO DEL PM: investe %s (orizzonte %d anni), book %s; valuta base %s; mercati accessibili: %s; "
        "preferenza UCITS: %s; broker: %s; residenza fiscale: %s; leva: %s; short: %s.", 'PM PROFILE: invests %s (%d-year horizon), %s book; base currency %s; accessible markets: %s; UCITS preference: %s; broker: %s; tax residence: %s; leverage: %s; short: %s.')
        % (_tipo_frase(p["tipo_investimento"]), p["orizzonte_anni"], stile, p["valuta_base"], _piazze(m),
           _si_no(p["preferenza_ucits"]), p["broker"] or "n.d.", p["residenza_fiscale"] or "n.d.",
           _ui_text('ammessa', 'allowed') if p["leva_ammessa"] else _ui_text('NON ammessa', 'NOT allowed'), _ui_text('ammesso', 'allowed') if p["short_ammesso"] else _ui_text('NON ammesso', 'NOT allowed')),
        _ui_text("RISCHIO ACCETTATO: volatilita' target %s%% annua; VaR 99%% a 1 giorno max %s%% del patrimonio; "
        "drawdown massimo %s%%; perdita massima in uno stress tipo 2008: %s%% del NAV.", 'RISK ACCEPTED: target volatility %s%% annually; maximum one-day VaR 99%% %s%% of wealth; maximum drawdown %s%%; maximum loss in a 2008-type stress: %s%% of NAV.')
        % (_fmt(r["volatilita_target_pct"]), _fmt(r["var99_1g_pct"]), _fmt(r["drawdown_max_pct"]), _fmt(r["stress_gfc_pct"])),
        _ui_text("SIZING: single-stock baseline %s%% / cap %s%% dell'investito; veicoli diversificati %s%% / cap %s%%; "
        "cap di settore %s%% (sui single-stock); pavimento %s%%; posizione minima %s%% del NAV; nuove posizioni "
        "%s%% del capitale; max posizioni: %s; peso massimo delle prime tre: %s.", 'SIZING: single-stock baseline %s%% / cap %s%% of invested assets; diversified vehicles %s%% / cap %s%%; sector cap %s%% (single stocks); floor %s%%; minimum position %s%% of NAV; new positions %s%% of capital; maximum positions: %s; maximum top-three weight: %s.')
        % (_fmt(s["base_single_pct"]), _fmt(s["cap_single_pct"]), _fmt(s["base_veicolo_pct"]), _fmt(s["cap_veicolo_pct"]),
           _fmt(s["cap_settore_pct"]), _fmt(s["limite_minimo_pct"]), _fmt(s["posizione_minima_pct"]),
           _fmt_int(s["size_nuova_posizione_pct"]),
           str(s["max_posizioni"]) if s["max_posizioni"] is not None else "n.d.",
           (_fmt(s["top3_max_pct"]) + "%") if s["top3_max_pct"] is not None else "n.d."),
    ]
    n = m["note"]
    extra = []
    if n["aree_gradite"]:
        extra.append(_ui_text("aree gradite: ", 'preferred areas: ') + "; ".join(n["aree_gradite"]))
    if n["esclusioni"]:
        extra.append(_ui_text("ESCLUSIONI (mai proporle): ", 'EXCLUSIONS (never propose them): ') + "; ".join(n["esclusioni"]))
    if n["note_per_il_comitato"]:
        extra.append(_ui_text("note del PM: ", 'PM notes: ') + n["note_per_il_comitato"])
    if extra:
        righe.append(_ui_text("NOTE DEL PM PER IL COMITATO: ", 'PM NOTES FOR THE COMMITTEE: ') + " | ".join(extra) + ".")
    return "\n".join(righe)


def _sez_cassa(m: Dict[str, Any]) -> str:
    k, s = m["cassa"], m["sizing"]
    tipica, oltre = _fmt_int(k["cassa_tipica_pct"]), _fmt_int(k["cassa_max_senza_giustificazione_pct"])
    quota, sett = _fmt_int(k["impiego_default_pct"]), _fmt_int(k["impiego_finestra_settimane"])
    size = _fmt_int(s["size_nuova_posizione_pct"])
    importante = _ui_text('importante', 'substantial') if k["cassa_tipica_pct"][0] >= LIQUIDITA_IMPORTANTE_DA_PCT else _ui_text('contenuta', 'modest')
    pol = k["politica_impiego"]
    if pol == "aggressiva":
        testa = _ui_text("## GESTIONE DEL CASH (regola attiva, BIAS AL DEPLOYMENT)", '## CASH MANAGEMENT (active rule, DEPLOYMENT BIAS)')
        apertura = (_ui_text("Il PM ha una liquidita' %s (tipicamente %s%% del capitale) e VUOLE METTERLA A LAVORO. "
                    "Il cash fermo che rende zero e' un costo opportunita', non un porto sicuro. In ogni memo DEVI:", 'The PM holds %s cash (typically %s%% of capital) and WANTS TO PUT IT TO WORK. Idle cash earning zero has an opportunity cost; it is not a safe harbour. In every memo you MUST:')
                    % (importante, tipica))
        due = (_ui_text("2. DEFAULT AGGRESSIVO: in assenza di un rischio imminente PRECISO e DATATO (es. una riunione FOMC "
               "tra 5 giorni, un dato CPI dopodomani), proponi un deployment DECISO del cash — orientativamente "
               "il %s%% della liquidita' disponibile messa al lavoro nelle prossime %s settimane su piu' idee.", '2. AGGRESSIVE DEFAULT: without a PRECISE and DATED imminent risk (for example an FOMC meeting in 5 days or CPI in two days), propose DECISIVE cash deployment: roughly %s%% of available cash over the next %s weeks across several ideas.')
               % (quota, sett))
        tre = (_ui_text("3. Il DRY POWDER (cash tenuto fermo) e' l'ECCEZIONE che va GIUSTIFICATA, non il default. Se vuoi "
               "tenere liquidita' oltre il %s%%, devi nominare il rischio specifico e datato che giustifica "
               "l'attesa, e dire a quali livelli di prezzo dispiegheresti.", '3. DRY POWDER (retained cash) is the EXCEPTION requiring JUSTIFICATION, not the default. To hold cash above %s%%, name the specific dated risk justifying the wait and the prices at which you would deploy.') % oltre)
    elif pol == "neutra":
        testa = _ui_text("## GESTIONE DEL CASH (regola attiva, IMPIEGO NEUTRO)", '## CASH MANAGEMENT (active rule, NEUTRAL DEPLOYMENT)')
        apertura = (_ui_text("Il PM tiene una liquidita' %s (tipicamente %s%% del capitale) e la impiega quando le idee "
                    "lo meritano. Il cash fermo ha un costo opportunita', ma non e' un errore in se'. In ogni memo DEVI:", 'The PM holds %s cash (typically %s%% of capital) and deploys it when ideas merit it. Idle cash has an opportunity cost, but is not inherently a mistake. In every memo you MUST:')
                    % (importante, tipica))
        due = (_ui_text("2. DEFAULT NEUTRO: proponi un deployment del cash proporzionato alle idee con catalyst datato — "
               "orientativamente il %s%% della liquidita' disponibile nelle prossime %s settimane — e se le idee "
               "non ci sono, dichiara che il cash resta fermo e perche'.", '2. NEUTRAL DEFAULT: propose deployment proportional to ideas with dated catalysts: roughly %s%% of available cash over the next %s weeks. If no ideas qualify, declare that cash remains idle and explain why.') % (quota, sett))
        tre = (_ui_text("3. Il DRY POWDER e' legittimo, ma oltre il %s%% va MOTIVATO: nomina il rischio o l'assenza di idee "
               "che giustifica l'attesa, e di' a quali livelli di prezzo dispiegheresti.", '3. DRY POWDER is legitimate, but above %s%% it needs JUSTIFICATION: name the risk or absence of ideas justifying the wait and the prices at which you would deploy.') % oltre)
    else:
        testa = _ui_text("## GESTIONE DEL CASH (regola attiva, PRUDENZA)", '## CASH MANAGEMENT (active rule, PRUDENCE)')
        apertura = (_ui_text("Il PM tiene una liquidita' %s (tipicamente %s%% del capitale) e la impiega con PRUDENZA: "
                    "il cash fermo e' una scelta legittima, non un costo da azzerare. In ogni memo DEVI:", 'The PM holds %s cash (typically %s%% of capital) and deploys it PRUDENTLY: retaining cash is legitimate, not a cost to eliminate. In every memo you MUST:')
                    % (importante, tipica))
        due = (_ui_text("2. DEFAULT PRUDENTE: il cash si impiega solo su idee con catalyst datato e tesi sopra la soglia — "
               "orientativamente non oltre il %s%% della liquidita' disponibile nelle prossime %s settimane; "
               "in assenza di idee, il cash resta fermo e lo dichiari.", '2. PRUDENT DEFAULT: deploy cash only into ideas with dated catalysts and a thesis above the threshold: roughly no more than %s%% of available cash over the next %s weeks. Without ideas, cash remains idle; declare it.') % (quota, sett))
        tre = (_ui_text("3. Il DRY POWDER e' il default: oltre il %s%% di liquidita' di' comunque a quali livelli di "
               "prezzo dispiegheresti.", '3. DRY POWDER is the default: above %s%% cash, still state the prices at which you would deploy.') % oltre)
    righe = [testa, apertura,
             _ui_text("1. Dichiarare esplicitamente quanto cash c'e' e che percentuale del capitale rappresenta.", '1. Explicitly state available cash and its percentage of total capital.'),
             due, tre,
             _ui_text("4. Ragiona in deployment plan esplicito: quanto questa settimana, su quali idee e con quali size, "
             "quanto come munizione per i ribassi e a quali livelli. Le size delle nuove posizioni devono essere "
             "materiali (tipicamente ", '4. Present an explicit deployment plan: how much this week, into which ideas and at which weights, and how much to reserve for declines at specified prices. New-position weights must be material (typically ') + size + _ui_text("% del capitale ciascuna", '% of capital each')
             + (_ui_text(", non 0,5%", ', not 0.5%') if s["size_nuova_posizione_pct"][0] > 0.5 else "") + ")."]
    if k["cassa_minima_pct"] is not None:
        righe.append(_ui_text("5. CASSA MINIMA del mandato: mai sotto il %s%% del capitale; sotto quella soglia non si "
                     "impiega, si ricostituisce.", '5. MANDATED MINIMUM CASH: never below %s%% of capital; below that threshold, replenish cash instead of deploying it.') % _fmt(k["cassa_minima_pct"]))
    return "\n".join(righe)


_NOMI_CONDIZIONI = {"sharpe_12m_negativo": "Sharpe negativo su 12+ mesi",
                    "nessun_catalyst_90g": "ZERO catalyst attesi entro 90 giorni",
                    "tesi_smentita": "Tesi originale empiricamente smentita"}
_IN_LETTERE = {1: "UNA", 2: "DUE", 3: "TRE"}


def _sez_trim(m: Dict[str, Any]) -> str:
    d, r = m["disciplina"], m["rischio"]
    accese = [c for c in CONDIZIONI_TAGLIO if d["condizioni_taglio_oltre"][c]]
    soglia = _fmt(d["taglio_con_condizioni_oltre_pct"])
    libero = _fmt_int(d["taglio_max_senza_condizioni_pct"])
    righe = []
    if accese:
        righe.append(_ui_text("Una raccomandazione di tagliare una posizione di PIU' del %s%% richiede %s condizion%s simultanee:", 'A recommendation to reduce a position by MORE than %s%% requires %s simultaneous condition%s:')
                     % (soglia, _ui_text(_IN_LETTERE[len(accese)], {1:"ONE",2:"TWO",3:"THREE"}[len(accese)]), _ui_text("e", "") if len(accese) == 1 else _ui_text("i", "s"))
                     if len(accese) > 1 else
                     _ui_text("Una raccomandazione di tagliare una posizione di PIU' del %s%% richiede UNA condizione:", 'A recommendation to reduce a position by MORE than %s%% requires ONE condition:') % soglia)
        for i, c in enumerate(accese, 1):
            righe.append("%d. %s" % (i, _ui_text(_NOMI_CONDIZIONI[c], {"sharpe_12m_negativo":"Negative Sharpe over 12+ months", "nessun_catalyst_90g":"ZERO catalysts expected within 90 days", "tesi_smentita":"Original thesis empirically disproved"}[c])))
        righe.append((_ui_text("Se ne manca anche una sola, il taglio massimo e' %s%%.", 'If even one is missing, the maximum reduction is %s%%.') if len(accese) > 1
                      else _ui_text("Se manca, il taglio massimo e' %s%%.", 'If it is missing, the maximum reduction is %s%%.')) % libero
                     + _ui_text(" La size e' inversamente proporzionale ai catalyst pendenti.", ' Position size is inversely proportional to pending catalysts.'))
    else:
        righe.append(_ui_text("Un taglio oltre il %s%% non richiede condizioni aggiuntive (mandato del PM): resta obbligatoria "
                     "la motivazione coi numeri e l'ingaggio della tesi; senza, il taglio massimo e' %s%%. La size e' "
                     "inversamente proporzionale ai catalyst pendenti.", 'A reduction above %s%% requires no additional conditions (PM mandate): a quantified rationale and engagement with the thesis remain mandatory; without them, maximum reduction is %s%%. Position size is inversely proportional to pending catalysts.') % (soglia, libero))
    if r["drawdown_bilaterale"]:
        righe.append(
            _ui_text("VALUTAZIONE BILATERALE DEL DRAWDOWN (regola del PM, obbligatoria): un drawdown NON e' di per se' un motivo "
            "di vendita — puo' essere sia un'opportunita' di acquisto che un segnale di vendita; considerarlo solo come "
            "vendita porterebbe a vendere sempre ai minimi e perdere il rimbalzo. TRIGGER (unico, vale per tutti): OGNI "
            "proposta di riduzione (TRIM/SELL/HEDGE sul nome) su una posizione in drawdown significativo (indicativamente "
            ">%s%% dal massimo 52 settimane) O motivata da metriche backward-looking (Sharpe/maxDD/momentum trailing, track "
            "record del cluster). In quei casi la tesi della decisione deve argomentare ANCHE il lato opposto — perche' "
            "quel livello potrebbe essere un punto d'acquisto (mean-reversion, livelli chiave, sconto sul NAV per i CEF, "
            "view %s del PM) — e spiegare coi numeri perche' l'uscita vince. Lo Sharpe trailing e' un'autopsia del "
            "passato: da solo puo' APRIRE la proposta di trim, mai chiuderla. BILATERALE VUOL DIRE BILATERALE: l'errore "
            "simmetrico — tenere o mediare un titolo con tesi ROTTA (value trap, disposition effect) — e' altrettanto "
            "vietato. Non stai difendendo i hold: stai pesando ENTRAMBI i lati coi numeri, e poi scegli.", 'TWO-SIDED DRAWDOWN ASSESSMENT (mandatory PM rule): a drawdown is NOT itself a reason to sell; it may be either a buying opportunity or a selling signal. Treating it only as a sale would systematically sell lows and miss rebounds. ONE TRIGGER for everyone: EVERY proposed reduction (TRIM/SELL/HEDGE on the name) of a position in significant drawdown (indicatively >%s%% from its 52-week high) OR justified by backward-looking metrics (trailing Sharpe/maxDD/momentum, cluster track record). The decision thesis must ALSO argue the other side: why this level might be a buying point (mean reversion, key levels, NAV discount for CEFs, the PM’s %s view), then explain with numbers why exit wins. Trailing Sharpe describes the past: alone it may OPEN a trim proposal, never settle it. TWO-SIDED MEANS TWO-SIDED: the symmetric error, holding or averaging a name with a BROKEN thesis (value trap, disposition effect), is equally forbidden. Do not defend holds automatically: weigh BOTH sides with numbers, then choose.')
            % (_fmt(r["drawdown_significativo_pct"]), _view_frase(m["profilo"]["tipo_investimento"])))
    else:
        righe.append(_ui_text("DRAWDOWN: il PM non ha dichiarato la regola bilaterale: un drawdown si valuta coi numeri, senza "
                     "automatismi ne' in acquisto ne' in vendita; una tesi ROTTA (value trap) resta un motivo di uscita.", 'DRAWDOWN: the PM has not declared the two-sided rule. Assess drawdowns using numbers without automatic buying or selling; a BROKEN thesis (value trap) remains an exit reason.'))
    return "\n".join(righe)


def _sez_pair(m: Dict[str, Any]) -> str:
    n = m["disciplina"]["pair_trade_per_memo"]
    if n == 0:
        return (_ui_text("NESSUN pair trade nel memo (mandato del PM): la sezione 6 lo dichiara in una riga e non propone "
                "coppie.", 'NO pair trades in the memo (PM mandate): section 6 states this in one line and proposes no pairs.'))
    if n == 1:
        return _ui_text("UN solo pair trade per memo, con una VERA tesi di valore relativo (stesso settore/industria/paese/fattore).", 'Only ONE pair trade per memo, with a REAL relative-value thesis (same sector/industry/country/factor).')
    return (_ui_text("Al massimo DUE pair trade per memo, ognuno con una VERA tesi di valore relativo (stesso "
            "settore/industria/paese/fattore).", 'At most TWO pair trades per memo, each with a REAL relative-value thesis (same sector/industry/country/factor).'))


def _sez_opzioni(m: Dict[str, Any]) -> str:
    o = m["opzioni"]
    testa = _ui_text("## DISCIPLINA SULLE OPZIONI", '## OPTIONS DISCIPLINE')
    if not o["opzioni_abilitate"]:
        return (testa + _ui_text("\nIl PM NON usa opzioni: nessuna struttura in opzioni nel memo; la sezione 4 legge il tape "
                "(IV, skew, gamma) solo come informazione sul posizionamento.", '\nThe PM does NOT use options: no option structures in the memo; section 4 reads the tape (IV, skew, gamma) only as positioning information.'))
    a = set(o["strumenti_ammessi"])
    voci = []
    if "long_call_catalyst" in a:
        voci.append(_ui_text("LONG CALL con catalyst NOMINATO e DATATO", 'LONG CALL with a NAMED and DATED catalyst'))
    if {"put_hedge", "put_spread"} & a:
        cosa = (_ui_text("PUT o PUT SPREAD", 'PUT or PUT SPREAD') if {"put_hedge", "put_spread"} <= a
                else "PUT" if "put_hedge" in a else "PUT SPREAD")
        voci.append(cosa + _ui_text(" come copertura del portafoglio su una finestra di rischio identificata", ' as a portfolio hedge for an identified risk window'))
    if {"covered_call", "cash_secured_put"} & a:
        cosa = (_ui_text("COVERED CALL o CASH-SECURED PUT", 'COVERED CALL or CASH-SECURED PUT') if {"covered_call", "cash_secured_put"} <= a
                else "COVERED CALL" if "covered_call" in a else "CASH-SECURED PUT")
        voci.append(cosa + _ui_text(" su posizioni esistenti", ' on existing positions'))
    if "short_premium_nudo" in a:
        voci.append(_ui_text("VENDITA DI PREMIO NUDA (short premium) solo con margine e rischio dichiarati", 'NAKED SHORT PREMIUM only with declared margin and risk'))
    if "straddle_strangle" in a:
        voci.append(_ui_text("STRADDLE/STRANGLE solo su eventi datati con volatilita' implicita sotto la realizzata", 'STRADDLE/STRANGLE only around dated events with implied volatility below realized volatility'))
    solo = _ui_text("Solo: ", 'Only: ') + ", ".join("(%d) %s" % (i, v) for i, v in enumerate(voci, 1)) + "."
    vietati = ["call vertical", "ratio spread", "butterfly", "condor", _ui_text("speculazione binaria", 'binary speculation')]
    if "short_premium_nudo" not in a:
        vietati.append(_ui_text("vendita di premio nuda", 'naked short premium'))
    if "straddle_strangle" not in a:
        vietati.append("straddle/strangle")
    if o["budget_premio_pct"] is not None:
        solo += _ui_text(" Premio complessivo in opzioni: al massimo il %s%% del patrimonio.", ' Total option premiums: at most %s%% of wealth.') % _fmt(o["budget_premio_pct"])
    return "\n".join([testa, solo, _ui_text("NON ammesso: ", 'NOT allowed: ') + ", ".join(vietati) + "."])


def _sez_caccia(m: Dict[str, Any]) -> str:
    d = m["disciplina"]
    lo, hi = d["nuove_idee_per_memo"]
    righe = [_ui_text("## ROTAZIONE SETTORIALE + CACCIA GLOBALE (mandato del PM)", '## SECTOR ROTATION + GLOBAL SEARCH (PM mandate)')]
    if d["rotazione_settoriale"]:
        righe.append(_ui_text("- NIENTE ANCORAGGIO AI SETTORI DEL BOOK: se la tesi di un SETTORE si e' degradata (driver esaurito, "
                     "catalyst passati a vuoto, track record scorekeeper negativo sul cluster), devi DIRLO e proporre il "
                     "TRIM esplicito del cluster — dentro la Disciplina degli Alleggerimenti qui sotto — indicando la "
                     "destinazione del capitale liberato. Tenere un settore \"perche' c'e' gia'\" e' un errore di processo, "
                     "non una posizione.", '- NO ANCHORING TO CURRENT SECTORS: if a SECTOR thesis deteriorates (exhausted driver, failed catalysts, negative cluster scorekeeper record), SAY SO and propose an explicit cluster TRIM within the Reduction Discipline below, naming the destination of the released capital. Keeping a sector just because it is already held is a process error, not a position.'))
    if hi > 0:
        dove = (_ui_text("SENZA vincolo di geografia — USA/Europa ma anche Giappone, India, Brasile, Indonesia, Vietnam, Golfo, "
                "LatAm e mercati di frontiera", 'WITHOUT geographical restrictions: USA/Europe and also Japan, India, Brazil, Indonesia, Vietnam, Gulf, LatAm and frontier markets') if d["caccia_globale"]
                else _ui_text("dai mercati accessibili al PM (%s)", 'from the PM’s accessible markets (%s)') % _piazze(m))
        righe.append(_ui_text("- SEZIONE OBBLIGATORIA \"NUOVE IDEE DAL MONDO\": ogni memo deve avere %s che NON sono nel "
                     "book e NON sono riproposte, pescati dagli specialisti %s. Per ciascuno: tesi in 2 righe con numeri "
                     "[src: tool], catalyst datato, COME si compra da %s (ADR, UCITS, listino accessibile) e il rischio "
                     "specifico del paese (FX, governance, liquidita'). Se gli specialisti non hanno prodotto candidati "
                     "%sdegni, la sezione DICHIARA il buco (\"nessuna idea nuova sopra la soglia questa settimana: ecco cosa "
                     "e' stato scartato e perche'\") — mai riempirla con riproposte travestite.", '- MANDATORY "NEW IDEAS FROM THE WORLD" SECTION: every memo must include %s OUTSIDE the book and NOT repeated proposals, sourced by specialists %s. For each: a two-line thesis with [src: tool] numbers, a dated catalyst, HOW to buy from %s (ADR, UCITS, accessible listing), and country-specific risk (FX, governance, liquidity). If specialists have not produced worthy %scandidates, DECLARE the gap ("no new idea above the threshold this week: here is what was rejected and why"); never fill it with disguised repeat proposals.')
                     % (_fmt_conteggio(d["nuove_idee_per_memo"]), dove, _piazza_di_casa(m),
                        _ui_text("globali ", 'global ') if d["caccia_globale"] else ""))
    else:
        righe.append(_ui_text("- SEZIONE \"NUOVE IDEE DAL MONDO\" NON richiesta dal mandato: la sezione 5-bis lo dichiara in una riga.", '- "NEW IDEAS FROM THE WORLD" is NOT required by the mandate: section 5-bis declares this in one line.'))
    if not d["rotazione_settoriale"]:
        # review 05/09: il test era `len(righe) == 1`, mai vero — la rotazione spenta restava muta
        righe.append(_ui_text("- Il PM non chiede rotazione settoriale: i settori del book si valutano tesi per tesi.", '- The PM does not request sector rotation: assess current sectors thesis by thesis.'))
    return "\n".join(righe)


def _sez_5bis(m: Dict[str, Any]) -> str:
    d = m["disciplina"]
    lo, hi = d["nuove_idee_per_memo"]
    if hi == 0:
        return _ui_text("## 5-bis. Nuove Idee dal Mondo (una riga: il mandato del PM non richiede candidati nuovi)", '## 5-bis. New Ideas from the World (one line: the PM mandate does not require new candidates)')
    dove = (_ui_text("GLOBALI — anche EM/frontiera — della caccia globale", 'GLOBAL — including EM/frontier — from the global search') if d["caccia_globale"]
            else _ui_text("dai mercati accessibili al PM", 'from the PM’s accessible markets'))
    quanti = _fmt_conteggio(d["nuove_idee_per_memo"])
    quanti = (_ui_text("i ", 'the ') + quanti) if lo > 0 else quanti
    return (_ui_text("## 5-bis. Nuove Idee dal Mondo (300-500 parole: %s %s, ognuno con numeri [src], catalyst "
            "datato, come si compra da %s e rischio paese; se niente sopra la soglia, dichiara il buco e cosa e' "
            "stato scartato)", '## 5-bis. New Ideas from the World (300-500 words: %s %s, each with [src] numbers, a dated catalyst, how to buy from %s and country risk; if none qualify, declare the gap and what was rejected)') % (quanti, dove, _piazza_di_casa(m)))


def _sez_skipped(m: Dict[str, Any]) -> str:
    if m["disciplina"]["riproporre_skipped"]:
        return (_ui_text("Le decisioni SKIPPED non sono rifiuti definitivi: significano \"non eseguita QUESTA settimana\". Se la "
                "tesi regge, RIPROPONILE esplicitamente (a size invariata o ridotta), ricordando che era gia' stata "
                "suggerita. MA: se dalla memoria risulta gia' proposta e skippata PIU' DI UNA VOLTA (stesso ticker "
                "e stessa azione), NON riproporla MAGGIORATA — lo skip ripetuto e' un segnale del PM: dichiara il "
                "disaccordo nella sezione della decisione, chiedi una decisione esplicita, e presentala declassata (size "
                "ridotta o RESEARCH a 0). Lasciala cadere solo a tesi decaduta, dichiarandolo. Se la finestra di memoria "
                "e' troppo corta per contare gli skip, dillo.", 'SKIPPED decisions are not final rejections: they mean "not executed THIS week". If the thesis holds, explicitly PROPOSE THEM AGAIN at the same or lower weight, recalling the prior suggestion. BUT: if memory shows the same ticker and action proposed and skipped MORE THAN ONCE, do NOT INCREASE the repeated proposal. Repeated skipping is a PM signal: declare disagreement in the decision section, request an explicit decision, and downgrade it (reduced weight or RESEARCH at 0). Drop it only when the thesis expires, and say so. If the memory window is too short to count skips, declare that limitation.'))
    return (_ui_text("Le decisioni SKIPPED NON si ripropongono (mandato del PM): una proposta saltata cade; se torna, e' con "
            "una tesi NUOVA dichiarata come tale, mai maggiorata. Se la finestra di memoria e' troppo corta per "
            "riconoscere uno skip, dillo.", 'SKIPPED decisions are NOT repeated (PM mandate): a skipped proposal lapses; if it returns, present an explicitly NEW thesis, never an increased weight. If the memory window is too short to recognize a skip, declare that limitation.'))


def _sez_sfida(m: Dict[str, Any]) -> str:
    if m["disciplina"]["sfidare_le_view"]:
        return (_ui_text("- Le view NON sono ordini: il PM vuole essere sfidato. Se i dati smentiscono una sua view, dillo "
                "apertamente e coi numeri — il compiacimento e' un errore di processo quanto ignorarla.", '- Views are NOT orders: the PM wants to be challenged. If data contradict a view, say so openly with numbers; agreeing to please is as much a process error as ignoring it.'))
    return (_ui_text("- Le view del PM sono vincolanti salvo tesi smentita coi numeri: se i dati le contraddicono, dillo coi "
            "numeri e lascia a lui la decisione.", '- The PM’s views are binding unless disproved with numbers: if data contradict them, present the numbers and leave the decision to the PM.'))


def _sez_profilo_tesi(m: Dict[str, Any]) -> str:
    p = m["profilo"]
    stile = (_ui_text("e accetta concentrazione sulle conviction", 'and accepts concentrated convictions') if p["stile"] == "concentrato"
             else _ui_text("e vuole un book DIVERSIFICATO (nessuna conviction sopra i cap)", 'and wants a DIVERSIFIED book (no conviction above its cap)'))
    if m["rischio"]["drawdown_bilaterale"]:
        coda = (_ui_text("un drawdown va valutato in modo BILATERALE (puo' essere opportunita' di acquisto O segnale di "
                "vendita); trattarlo solo come vendita = vendere ai minimi e perdere il rimbalzo. Vale anche l'errore "
                "SIMMETRICO: difendere un hold su tesi ROTTA (value trap) e' altrettanto vietato — bilaterale = pesare "
                "entrambi i lati.", 'assess a drawdown from BOTH SIDES (buying opportunity OR selling signal); treating it only as a sale means selling lows and missing rebounds. The SYMMETRIC error also applies: defending a hold with a BROKEN thesis (value trap) is equally forbidden. Two-sided means weighing both sides.'))
    else:
        coda = (_ui_text("un drawdown si valuta coi numeri, senza automatismi ne' in acquisto ne' in vendita; una tesi ROTTA "
                "(value trap) resta un motivo di uscita.", 'assess drawdowns with numbers, without automatic buying or selling; a BROKEN thesis (value trap) remains an exit reason.'))
    due = _ui_text("2. Il PM investe %s %s: %s\n", '2. The PM invests %s %s: %s\n') % (_tipo_frase(p["tipo_investimento"]), stile, coda)
    if m["disciplina"]["sfidare_le_view"]:
        tre = (_ui_text("3. Le view NON sono ordini: se i dati le smentiscono, dillo apertamente — il PM vuole essere sfidato, "
               "non assecondato.\n", '3. Views are NOT orders: if data contradict them, say so openly. The PM wants to be challenged, not appeased.\n'))
    else:
        tre = (_ui_text("3. Le view del PM sono vincolanti salvo tesi smentita coi numeri: se i dati le contraddicono, dillo "
               "coi numeri e lascia a lui la decisione.\n", '3. The PM’s views are binding unless disproved with numbers: if data contradict them, present the numbers and leave the decision to the PM.\n'))
    return due + tre


@scoped_language
def sezioni(m: Dict[str, Any]) -> Dict[str, str]:
    """Il testo di ogni pezzo del mandato, deterministico dai campi. Le chiavi sono i segnaposto
    `{MANDATO:<chiave>}` che i prompt portano al posto delle frasi cablate di ieri."""
    return {
        "intestazione": intestazione(m),
        "profilo_rischio": _sez_profilo_rischio(m),
        "cassa": _sez_cassa(m),
        "trim": _sez_trim(m),
        "pair": _sez_pair(m),
        "opzioni": _sez_opzioni(m),
        "caccia": _sez_caccia(m),
        "sezione_5bis": _sez_5bis(m),
        "skipped": _sez_skipped(m),
        "sfida": _sez_sfida(m),
        "profilo_tesi": _sez_profilo_tesi(m),
        # le due soglie dei tagli citate anche nella GRADAZIONE delle tesi (review 05/09: erano
        # rimaste cablate in una riga del Capo che nessun segnaposto copriva)
        "soglia_taglio": _fmt(m["disciplina"]["taglio_con_condizioni_oltre_pct"]) + "%",
        "taglio_libero": _fmt_int(m["disciplina"]["taglio_max_senza_condizioni_pct"]) + "%",
        "condizione_tesi": _condizione_tesi(m),
    }


def _condizione_tesi(m: Dict[str, Any]) -> str:
    """« (e' la condizione N della Disciplina)» solo se la tesi smentita e' fra le condizioni accese,
    con N = il suo numero nell'elenco reso; altrimenti niente (review 05/09)."""
    accese = [c for c in CONDIZIONI_TAGLIO if m["disciplina"]["condizioni_taglio_oltre"][c]]
    if "tesi_smentita" not in accese:
        return ""
    return _ui_text(" (e' la condizione %d della Disciplina)", ' (this is condition %d of the Discipline)') % (accese.index("tesi_smentita") + 1)


@scoped_language
def blocco_prompt(m: Dict[str, Any]) -> str:
    """Il mandato INTERO per i desk, il red team, la chat e l'anteprima della pagina: quello che
    vedi e' quello che l'AI riceve."""
    s = sezioni(m)
    return "\n".join([
        s["intestazione"], s["profilo_rischio"], "", s["cassa"], "",
        _ui_text("## DISCIPLINA DEGLI ALLEGGERIMENTI (TRIM)", '## REDUCTION DISCIPLINE (TRIM)'), s["trim"], "",
        _ui_text("## DISCIPLINA DEL PAIR TRADE", '## PAIR TRADE DISCIPLINE'), s["pair"], "",
        s["opzioni"], "", s["caccia"], "",
        _ui_text("## DECISIONI SALTATE", '## SKIPPED DECISIONS'), s["skipped"], "",
        _ui_text("## LE VIEW DEL PM", '## THE PM’S VIEWS'), s["sfida"],
    ])


def compila(template: str, m: Dict[str, Any]) -> str:
    """Sostituisce ogni `{MANDATO:chiave}` del template con la sezione resa dai valori di `m`.
    Una chiave sconosciuta e' un errore di programmazione, non un buco zitto."""
    s = sezioni(m)

    def _sost(match):
        chiave = match.group(1)
        if chiave not in s:
            raise ValueError("segnaposto sconosciuto {MANDATO:%s} (chiavi: %s)" % (chiave, ", ".join(s)))
        return s[chiave]
    out = SEGNAPOSTO.sub(_sost, template)
    if "{MANDATO:" in out:
        raise ValueError("segnaposto rimasti nel testo compilato")
    return out


def compila_o_dichiara(template: str, m: Optional[Dict[str, Any]]) -> str:
    """Come `compila`; con `m` None (mandato non dichiarato) ogni segnaposto diventa la
    DICHIARAZIONE, mai una regola di ripiego. Per chi risponde comunque (la chat)."""
    if m is not None:
        return compila(template, m)

    def _sost(match):
        if match.group(1) == "intestazione":
            return riga_senza_mandato()
        return _ui_text("(mandato non dichiarato: nessuna regola del PM in questa sezione)", '(mandate not declared: no PM rule in this section)')
    return SEGNAPOSTO.sub(_sost, template)


def frase_cassa_runtime(m: Dict[str, Any], cash_eur: float, nav_eur: float, mkt_eur: float,
                        cassa_misurata: bool = True) -> str:
    """Il blocco «CAPITALE DISPONIBILE» del Capo: la prima riga e' la misura; il giudizio sul cash
    dipende dalla banda tipica e dalla politica del mandato, non dalla sola presenza del book
    (audit F01: «QUESTO E' MOLTO CASH FERMO» usciva anche con la cassa al 7%). Cassa NON misurata
    (buco di lettura di portfolio.json) o NAV a zero = nessun giudizio, dichiarato."""
    k = m["cassa"]
    cash_pct = (cash_eur / nav_eur * 100.0) if nav_eur else 0.0
    tipica = _fmt_int(k["cassa_tipica_pct"])
    lo, hi = k["impiego_default_pct"]
    sett = _fmt_int(k["impiego_finestra_settimane"])
    righe = [_ui_text("Cash liquido da impiegare: EUR {:,.0f} ({:.0f}% del capitale totale di EUR {:,.0f}). Il valore di "
             "mercato investito e' EUR {:,.0f}.",'Available cash to deploy: EUR {:,.0f} ({:.0f}% of total capital of EUR {:,.0f}). Invested market value is EUR {:,.0f}.').format(cash_eur, cash_pct, nav_eur, mkt_eur)]
    if not cassa_misurata or nav_eur <= 0:
        righe.append(_ui_text("CAPITALE NON MISURATO: il mandato non da' nessun giudizio sul cash in questo giro (ne' "
                     "«fermo» ne' «da impiegare»); dichiara il buco nel memo e dimensiona sul capitale investito.",'CAPITAL NOT MEASURED: the mandate makes no cash judgment this time (neither idle nor to deploy); declare the gap in the memo and size against invested capital.'))
        return "\n".join(righe)
    pct = _fmt(round(cash_pct, 1))   # nel giudizio un decimale: 34,6% non e' «35% contro 35-45%»
    piano = "EUR {:,.0f}-{:,.0f}".format(cash_eur * lo / 100.0, cash_eur * hi / 100.0)
    if cash_pct >= k["cassa_tipica_pct"][0]:
        if k["politica_impiego"] == "aggressiva":
            righe.append(
                _ui_text("QUESTO E' MOLTO CASH FERMO. Le tue proposte di acquisto devono impiegare il capitale in modo "
                "PROPORZIONATO: con {}% di liquidita' non ha senso proporre solo aggiunte briciola (sotto il {}% "
                "del capitale, la size minima di una nuova posizione nel mandato). Dimensiona le nuove posizioni e gli "
                "incrementi in relazione al cash disponibile, salvo che il regime di mercato imponga esplicitamente "
                "prudenza (in quel caso DICHIARA perche' tieni il cash fermo). Tratta il cash come una decisione "
                "attiva, non come un residuo. Con EUR {:,.0f} di cash, un piano da EUR {:,.0f} e' troppo timido: "
                "pensa in termini di {} impiegati nelle prossime {} settimane salvo controindicazioni esplicite.",'THIS IS SUBSTANTIAL IDLE CASH. Proposed purchases must deploy capital PROPORTIONALLY: with {}% cash, proposing only tiny additions (below {}% of capital, the mandated minimum new-position size) does not make sense. Size new positions and additions relative to available cash, unless the market regime explicitly requires prudence (then DECLARE why you retain cash). Treat cash as an active decision, not a residual. With EUR {:,.0f} cash, a EUR {:,.0f} plan is too timid: think of {} deployed over the next {} weeks unless there are explicit contraindications.')
                .format(pct, _fmt(m["sizing"]["size_nuova_posizione_pct"][0]), cash_eur,
                        cash_eur * (lo / 2.0) / 100.0, piano, sett))
        elif k["politica_impiego"] == "neutra":
            righe.append(
                _ui_text("CASSA NELLA BANDA TIPICA o sopra ({}% contro {}%). Il mandato e' NEUTRO: proponi un deployment "
                "proporzionato alle idee con catalyst datato — orientativamente {} nelle prossime {} settimane — e se "
                "non ci sono idee sopra la soglia, dichiara che il cash resta fermo e perche'.",'CASH WITHIN OR ABOVE THE TYPICAL RANGE ({}% versus {}%). The mandate is NEUTRAL: propose deployment proportional to ideas with dated catalysts, roughly {} over the next {} weeks. If no ideas qualify, declare that cash remains idle and explain why.')
                .format(pct, tipica, piano, sett))
        else:
            righe.append(
                _ui_text("CASSA NELLA BANDA TIPICA o sopra ({}% contro {}%). Il mandato e' di PRUDENZA: proponi impieghi "
                "solo su idee con catalyst datato e tesi sopra la soglia (al massimo {} nelle prossime {} settimane); "
                "altrimenti dichiara che il cash resta fermo, ed e' una scelta legittima.",'CASH WITHIN OR ABOVE THE TYPICAL RANGE ({}% versus {}%). The mandate is PRUDENT: deploy only into ideas with dated catalysts and a thesis above the threshold (at most {} over the next {} weeks); otherwise declare that retaining cash is a legitimate choice.')
                .format(pct, tipica, piano, sett))
    else:
        righe.append(
            _ui_text("CASSA SOTTO LA BANDA TIPICA del mandato ({}% contro {}%): non c'e' «cash fermo» da mettere al lavoro. "
            "Dimensiona ogni proposta sul cash vero (EUR {:,.0f}) e dichiara che la munizione e' limitata invece di "
            "gonfiare le size; il piano cash del BLUF dice quanto resta.",'CASH BELOW THE MANDATED TYPICAL RANGE ({}% versus {}%): there is no idle cash to deploy. Size each proposal against actual cash (EUR {:,.0f}) and declare that reserves are limited instead of inflating weights; the BLUF cash plan states the remainder.')
            .format(pct, tipica, cash_eur))
    if k["cassa_minima_pct"] is not None and cash_pct < k["cassa_minima_pct"]:
        righe.append(
            _ui_text("CASSA SOTTO LA MINIMA DICHIARATA ({}%): nessun impiego; le proposte devono ricostituire la cassa "
            "(TRIM/SELL dove la tesi lo regge) o restare HOLD, dichiarandolo.",'CASH BELOW THE DECLARED MINIMUM ({}%): no deployment; proposals must replenish cash (TRIM/SELL where justified by the thesis) or remain HOLD, stating this explicitly.').format(_fmt(k["cassa_minima_pct"])))
    return "\n".join(righe)


# --------------------------------------------------------------------------- le viste per i motori

def sizing_params(m: Dict[str, Any]) -> Dict[str, Any]:
    """I parametri del motore di sizing, in FRAZIONI (0.12 = 12%), da passare a compute_sizing
    per chiamata: i tetti, il pavimento, la posizione minima in % del NAV, i budget di coda
    (negativi: perdita massima), la size delle nuove posizioni."""
    s, r = m["sizing"], m["rischio"]
    return {
        "base_single": s["base_single_pct"] / 100.0,
        "cap_single": s["cap_single_pct"] / 100.0,
        "base_veicolo": s["base_veicolo_pct"] / 100.0,
        "cap_veicolo": s["cap_veicolo_pct"] / 100.0,
        "cap_settore": s["cap_settore_pct"] / 100.0,
        "limite_minimo": s["limite_minimo_pct"] / 100.0,
        "posizione_minima_pct": float(s["posizione_minima_pct"]),
        "budget_stress_nav_pct": -float(r["stress_gfc_pct"]),
        "budget_var99_nav_pct": -float(r["var99_1g_pct"]),
        "size_nuova_posizione": (s["size_nuova_posizione_pct"][0] / 100.0, s["size_nuova_posizione_pct"][1] / 100.0),
        "max_posizioni": s["max_posizioni"],
        "top3_max": (s["top3_max_pct"] / 100.0) if s["top3_max_pct"] is not None else None,
        "impronta": impronta(m)[:8],
    }


def cassa(m: Dict[str, Any]) -> Dict[str, Any]:
    return dict(m["cassa"])


def opzioni(m: Dict[str, Any]) -> Dict[str, Any]:
    return dict(m["opzioni"])


def disciplina(m: Dict[str, Any]) -> Dict[str, Any]:
    return dict(m["disciplina"])


if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--scrivi-esempio":
        print("scritto", scrivi_esempio())
    else:
        try:
            _m = carica()
            print(blocco_prompt(_m))
        except MandatoMancante as _e:
            print(str(_e))
            sys.exit(2)
