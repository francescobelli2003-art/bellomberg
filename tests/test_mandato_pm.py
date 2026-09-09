# -*- coding: utf-8 -*-
"""Il MANDATO del PM esce dai prompt e diventa un profilo dichiarato dall'app (05/09,
criterio (5) dell'audit esterno, spec docs/superpowers/specs/2026-09-05-mandato-pm-design.md).

Cosa deve essere vero per chi usa il programma:
  - al primo accesso i campi sono VUOTI e il comitato non parte finche' non li dichiara;
  - il mandato si legge DAL DISCO a ogni chiamata: se lo cambia oggi, il prossimo prompt lo porta;
  - le frasi che i modelli leggono nascono dai SUOI valori, non da quelli del creatore;
  - nessun modulo congela un valore del mandato in una costante a import-time.

Tutti i numeri qui sotto sono INVENTATI (profilo di prova): il file di casa non entra mai.
"""
import ast
import copy
import glob
import json
import os
import re
import sqlite3
from types import SimpleNamespace

import pytest

import bellomberg.core.mandato_pm as mp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- utilita'

def _scrivi(path, m):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)


def _aggiorna(m, **per_blocco):
    """m con i valori di `per_blocco` sovrascritti: _aggiorna(m, cassa={"cassa_tipica_pct": [35, 45]})."""
    n = copy.deepcopy(m)
    for blocco, valori in per_blocco.items():
        n[blocco].update(valori)
    return n


@pytest.fixture
def esempio():
    """Il profilo di esempio della spec (prudente, generico): valido per costruzione."""
    return mp.profilo_esempio()


@pytest.fixture
def prova(esempio):
    """Un profilo di PROVA con numeri inventati che NON coincidono con l'esempio, cosi' l'origine
    e' «personalizzato» e le frasi rese portano numeri riconoscibili."""
    return _aggiorna(
        esempio,
        profilo={"tipo_investimento": "long_term", "stile": "concentrato", "orizzonte_anni": 7,
                 "mercati_accessibili": ["AS", "L", "US"], "preferenza_ucits": True},
        rischio={"drawdown_bilaterale": True, "drawdown_significativo_pct": 22},
        sizing={"base_single_pct": 9, "cap_single_pct": 11, "base_veicolo_pct": 24,
                "cap_veicolo_pct": 27, "cap_settore_pct": 28, "limite_minimo_pct": 1.5,
                "posizione_minima_pct": 0.3, "size_nuova_posizione_pct": [2, 5]},
        cassa={"cassa_tipica_pct": [35, 45], "cassa_max_senza_giustificazione_pct": [25, 35],
               "cassa_minima_pct": None, "politica_impiego": "aggressiva",
               "impiego_default_pct": [30, 55], "impiego_finestra_settimane": [1, 3]},
        disciplina={"taglio_max_senza_condizioni_pct": [15, 25], "taglio_con_condizioni_oltre_pct": 55,
                    "condizioni_taglio_oltre": {"sharpe_12m_negativo": True, "nessun_catalyst_90g": True,
                                                "tesi_smentita": True},
                    "riproporre_skipped": True, "pair_trade_per_memo": 2, "caccia_globale": True,
                    "nuove_idee_per_memo": [3, 4], "rotazione_settoriale": True, "sfidare_le_view": True},
        opzioni={"opzioni_abilitate": True,
                 "strumenti_ammessi": ["long_call_catalyst", "put_spread", "covered_call", "straddle_strangle"]},
    )


@pytest.fixture
def file_prova(tmp_path, prova, monkeypatch):
    """Il profilo di prova su disco, e mandato_pm che legge QUEL file."""
    p = tmp_path / "mandato_prova.json"
    _scrivi(str(p), prova)
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(p))
    return str(p)


# --------------------------------------------------------------------------- l'esempio committato

def test_l_esempio_esiste_ed_elenca_i_campi_del_codice_nei_due_versi():
    """mandato_pm.example.json e' il contratto pubblico: gli stessi campi che il codice legge,
    tutti VUOTI (decisione PM 05/09), piu' lo schema per la pagina e il profilo di esempio."""
    with open(mp.ESEMPIO_MANDATO, encoding="utf-8") as fh:
        es = json.load(fh)
    for blocco in mp.BLOCCHI:
        attesi = {n for n, c in mp.CAMPI.items() if c["blocco"] == blocco}
        assert set(es[blocco]) == attesi, blocco
        assert all(v is None for v in es[blocco].values()), "campi NON vuoti in " + blocco
    assert es["versione"] == mp.VERSIONE_SCHEMA
    assert es["dichiarato_il"] is None and es["origine"] is None
    assert es["_campi"] == mp.descrizione_campi(), "lo schema nel file e' scivolato dal codice"
    assert set(es["_esempio"]) == set(mp.BLOCCHI)
    assert mp.valida(es["_esempio"])[1] == [], "il profilo di esempio non e' valido"


def test_i_campi_vuoti_non_sono_un_mandato(tmp_path):
    vuoto = mp.campi_vuoti()
    _, errori = mp.valida(vuoto)
    obbligatori = {n for n, c in mp.CAMPI.items() if c["obbligatorio"]}
    nominati = {e.split(":")[0] for e in errori}
    assert obbligatori <= nominati, obbligatori - nominati
    p = tmp_path / "m.json"
    _scrivi(str(p), vuoto)
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica(str(p))
    assert "incompleto" in str(ei.value) and "cap_single_pct" in str(ei.value)
    assert ei.value.campi and "cap_single_pct" in ei.value.campi


def test_lo_scheletro_dei_campi_e_coerente():
    for nome, c in mp.CAMPI.items():
        assert c["blocco"] in mp.BLOCCHI, nome
        assert c["tipo"] in mp.TIPI, (nome, c["tipo"])
        assert c["descrizione"], nome
    assert set(mp.STRUMENTI_OPZIONI) == {"long_call_catalyst", "put_hedge", "put_spread", "covered_call",
                                         "cash_secured_put", "short_premium_nudo", "straddle_strangle"}
    assert set(mp.CONDIZIONI_TAGLIO) == {"sharpe_12m_negativo", "nessun_catalyst_90g", "tesi_smentita"}
    assert "MI" in mp.PIAZZE and "US" in mp.PIAZZE


# --------------------------------------------------------------------------- assente / illeggibile / non valido

def test_assente_solleva_col_percorso_e_dice_cosa_fare(tmp_path):
    p = str(tmp_path / "non_esiste.json")
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica(p)
    msg = str(ei.value)
    assert p in msg and "assente" in msg and 'mandato_pm.example.json' in msg
    assert ei.value.percorso == p and ei.value.causa == "assente"
    assert mp.dichiarato(p) is False


def test_illeggibile_solleva_con_la_causa(tmp_path):
    p = tmp_path / "rotto.json"
    p.write_text("{ non e' json", encoding="utf-8")
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica(str(p))
    assert ei.value.causa == "illeggibile" and "JSON" in str(ei.value)


def test_fuori_intervallo_e_scelte_sbagliate(esempio):
    m = _aggiorna(esempio, sizing={"cap_single_pct": 80}, profilo={"tipo_investimento": "yolo"},
                  cassa={"politica_impiego": "spericolata"})
    _, errori = mp.valida(m)
    testo = "\n".join(errori)
    assert "cap_single_pct" in testo and "2-50" in testo
    assert "tipo_investimento" in testo and "long_term" in testo
    assert "politica_impiego" in testo and "aggressiva" in testo


def test_un_intervallo_rovesciato_e_un_errore_del_campo(esempio):
    _, errori = mp.valida(_aggiorna(esempio, cassa={"cassa_tipica_pct": [15, 10]}))
    assert any(e.startswith("cassa_tipica_pct") and "minimo <= massimo" in e for e in errori), errori


def test_le_coerenze_fra_campi_sono_dichiarate(esempio):
    """Ogni valore sta nel suo intervallo: e' il LEGAME fra due campi a non tornare."""
    m = _aggiorna(esempio,
                  sizing={"base_single_pct": 13, "cap_single_pct": 12, "top3_max_pct": 10},
                  rischio={"var99_1g_pct": 6, "drawdown_max_pct": 5},
                  cassa={"cassa_minima_pct": 20, "cassa_tipica_pct": [10, 15]},
                  disciplina={"taglio_max_senza_condizioni_pct": [30, 40], "taglio_con_condizioni_oltre_pct": 35})
    _, errori = mp.valida(m)
    testo = "\n".join(errori)
    # 06/09 (lotto B): due legami nuovi sono entrati e sono VERI tutti e due su questa
    # fixture, non sono numeri aggiustati.
    #  - difetto 4 (drawdown_max_pct): drawdown massimo 5 con lo stress dell'esempio a 20.
    #    Non e' aggirabile abbassando lo stress: qui il VaR (6) deve restare sopra il
    #    drawdown (5), quindi qualunque stress sotto il drawdown farebbe scattare
    #    «var99 > stress» al posto suo.
    #  - difetto 3 (volatilita_target_pct): un VaR a un giorno del 6% implica una
    #    volatilita' annua ben oltre una volta e mezza il target dell'esempio.
    # Si dichiarano, non si nascondono.
    for atteso in ("base_single_pct", "var99_1g_pct", "cassa_minima_pct", "top3_max_pct",
                   "taglio_max_senza_condizioni_pct", "drawdown_max_pct",
                   "volatilita_target_pct"):
        assert atteso in testo, (atteso, testo)
    assert len(errori) == 7, errori


def test_mercati_e_strumenti_fuori_vocabolario(esempio):
    m = _aggiorna(esempio, profilo={"mercati_accessibili": ["MI", "XX"]},
                  opzioni={"opzioni_abilitate": True, "strumenti_ammessi": ["long_call_catalyst", "leva_10x"]})
    _, errori = mp.valida(m)
    testo = "\n".join(errori)
    assert "mercati_accessibili" in testo and "XX" in testo
    assert "strumenti_ammessi" in testo and "leva_10x" in testo


def test_opzioni_abilitate_senza_strumenti_e_un_errore(esempio):
    m = _aggiorna(esempio, opzioni={"opzioni_abilitate": True, "strumenti_ammessi": []})
    _, errori = mp.valida(m)
    assert any(e.startswith("strumenti_ammessi") for e in errori)


# --------------------------------------------------------------------------- dal disco, a ogni chiamata

def test_carica_legge_dal_disco_a_ogni_chiamata(tmp_path, prova):
    p = str(tmp_path / "m.json")
    _scrivi(p, prova)
    assert mp.carica(p)["sizing"]["cap_single_pct"] == 11
    _scrivi(p, _aggiorna(prova, sizing={"cap_single_pct": 10}))
    assert mp.carica(p)["sizing"]["cap_single_pct"] == 10, "una cache ha nascosto la modifica"


def test_carica_usa_il_percorso_del_modulo_e_lo_rilegge(file_prova, prova):
    assert mp.carica()["profilo"]["orizzonte_anni"] == 7
    _scrivi(file_prova, _aggiorna(prova, profilo={"orizzonte_anni": 9}))
    assert mp.carica()["profilo"]["orizzonte_anni"] == 9


def test_impronta_stabile_al_riordino_e_sensibile_a_un_valore(prova):
    a = mp.impronta(prova)
    riordinato = {k: prova[k] for k in reversed(list(prova))}
    riordinato["sizing"] = dict(reversed(list(prova["sizing"].items())))
    assert mp.impronta(riordinato) == a and len(a) == 64
    assert mp.impronta(_aggiorna(prova, sizing={"cap_single_pct": 10})) != a
    assert mp.impronta(_aggiorna(prova, note={"note_per_il_comitato": "x"})) != a


def test_intestazione_porta_data_impronta_e_origine(prova, esempio):
    prova = dict(prova, dichiarato_il="2031-04-09", origine="personalizzato")
    riga = mp.intestazione(prova)
    assert riga.startswith("MANDATO DEL PM (dichiarato il 09/04/2031, impronta " + mp.impronta(prova)[:8] + ")")
    assert "ESEMPIO" not in riga
    es = dict(esempio, dichiarato_il="2031-04-09", origine="esempio")
    assert "PROFILO DI ESEMPIO, NON PERSONALIZZATO" in mp.intestazione(es)


def test_l_origine_si_misura_dai_valori_non_dalla_parola(tmp_path, esempio):
    p = str(tmp_path / "m.json")
    _scrivi(p, dict(esempio, dichiarato_il="2031-04-09", origine="personalizzato"))
    assert mp.carica(p)["origine"] == "esempio", "valori identici all'esempio = profilo di esempio"
    _scrivi(p, dict(_aggiorna(esempio, sizing={"cap_single_pct": 7}), dichiarato_il="2031-04-09", origine="esempio"))
    assert mp.carica(p)["origine"] == "personalizzato", "un valore cambiato = personalizzato"


def test_salva_valida_scrive_atomico_e_tiene_il_backup(tmp_path, prova, monkeypatch):
    monkeypatch.setattr(mp, "DATA_DIR", str(tmp_path))
    p = str(tmp_path / "mandato_pm.json")
    m1 = mp.salva(prova, p)
    assert os.path.exists(p) and m1["dichiarato_il"] and m1["origine"] == "personalizzato"
    assert mp.dichiarato(p) is True
    mp.salva(_aggiorna(prova, sizing={"cap_single_pct": 10}), p)
    backup = glob.glob(os.path.join(str(tmp_path), "backups", "mandato_pm_*.json"))
    assert len(backup) == 1, "il precedente va copiato in data/backups/ a ogni salvataggio"
    assert json.load(open(backup[0], encoding="utf-8"))["sizing"]["cap_single_pct"] == 11
    with pytest.raises(ValueError) as ei:
        mp.salva(_aggiorna(prova, sizing={"cap_single_pct": 80}), p)
    assert "cap_single_pct" in str(ei.value)
    assert mp.carica(p)["sizing"]["cap_single_pct"] == 10, "un salvataggio rifiutato non tocca il file"
    assert not glob.glob(p + "*.tmp")


# --------------------------------------------------------------------------- le frasi per i modelli

def test_la_cassa_aggressiva_rende_le_frasi_di_ieri_coi_numeri_del_mandato(prova):
    s = mp.sezioni(prova)["cassa"]
    assert s.startswith("## GESTIONE DEL CASH (regola attiva, BIAS AL DEPLOYMENT)")
    assert ("Il PM ha una liquidita' importante (tipicamente 35-45% del capitale) e VUOLE METTERLA "
            "A LAVORO." in s)
    assert "2. DEFAULT AGGRESSIVO:" in s
    assert ("orientativamente il 30-55% della liquidita' disponibile messa al lavoro nelle prossime "
            "1-3 settimane su piu' idee." in s)
    assert "Se vuoi tenere liquidita' oltre il 25-35%," in s
    assert "(tipicamente 2-5% del capitale ciascuna, non 0,5%)" in s
    assert not re.search(r"\d+k\b", s), "nessun esempio in migliaia cablato: la cassa vera vive a runtime"
    assert "CASSA MINIMA" not in s
    piccolo = mp.sezioni(_aggiorna(prova, sizing={"size_nuova_posizione_pct": [0.5, 1]}))["cassa"]
    assert "(tipicamente 0,5-1% del capitale ciascuna)" in piccolo and "non 0,5%" not in piccolo


def test_la_liquidita_contenuta_sotto_la_soglia_di_resa(prova):
    s = mp.sezioni(_aggiorna(prova, cassa={"cassa_tipica_pct": [10, 15], "cassa_minima_pct": None}))["cassa"]
    assert "liquidita' contenuta (tipicamente 10-15% del capitale)" in s and "importante" not in s


def test_un_intervallo_a_un_numero_solo_si_scrive_una_volta(prova):
    s = mp.sezioni(_aggiorna(prova, cassa={"cassa_tipica_pct": [45, 45]}))["cassa"]
    assert "(tipicamente 45% del capitale)" in s


def test_politica_neutra_e_prudente_cambiano_il_default(prova):
    n = mp.sezioni(_aggiorna(prova, cassa={"politica_impiego": "neutra"}))["cassa"]
    assert "IMPIEGO NEUTRO" in n and "2. DEFAULT NEUTRO:" in n and "AGGRESSIVO" not in n
    p = mp.sezioni(_aggiorna(prova, cassa={"politica_impiego": "prudente"}))["cassa"]
    assert "PRUDENZA" in p and "2. DEFAULT PRUDENTE:" in p and "AGGRESSIVO" not in p
    assert "30-55%" in p, "anche il prudente porta la sua banda di impiego"


def test_la_cassa_minima_dichiarata_aggiunge_la_regola(prova):
    s = mp.sezioni(_aggiorna(prova, cassa={"cassa_minima_pct": 12}))["cassa"]
    assert "5. CASSA MINIMA" in s and "12%" in s


def test_il_trim_rende_le_condizioni_accese(prova):
    s = mp.sezioni(prova)["trim"]
    assert "di PIU' del 55% richiede TRE condizioni simultanee:" in s
    assert "1. Sharpe negativo su 12+ mesi" in s and "2. ZERO catalyst attesi entro 90 giorni" in s
    assert "3. Tesi originale empiricamente smentita" in s
    assert "Se ne manca anche una sola, il taglio massimo e' 15-25%." in s
    due = mp.sezioni(_aggiorna(prova, disciplina={"condizioni_taglio_oltre": {
        "sharpe_12m_negativo": True, "nessun_catalyst_90g": False, "tesi_smentita": True}}))["trim"]
    assert "DUE condizioni simultanee" in due and "catalyst attesi entro 90 giorni" not in due
    assert "2. Tesi originale empiricamente smentita" in due
    zero = mp.sezioni(_aggiorna(prova, disciplina={"condizioni_taglio_oltre": {
        "sharpe_12m_negativo": False, "nessun_catalyst_90g": False, "tesi_smentita": False}}))["trim"]
    assert "non richiede condizioni aggiuntive" in zero and "condizioni simultanee" not in zero
    una = mp.sezioni(_aggiorna(prova, disciplina={"condizioni_taglio_oltre": {
        "sharpe_12m_negativo": False, "nessun_catalyst_90g": False, "tesi_smentita": True}}))["trim"]
    assert "richiede UNA condizione:" in una and "1. Tesi originale empiricamente smentita" in una
    assert "Se manca, il taglio massimo e' 15-25%." in una


def test_la_gradazione_delle_tesi_cita_le_soglie_e_la_condizione_giusta(prova):
    """capo.py:119 (GRADAZIONE) citava «>50%», «(20-30%)» e «condizione 3» cablati (review 05/09)."""
    s = mp.sezioni(prova)
    assert s["soglia_taglio"] == "55%" and s["taglio_libero"] == "15-25%"
    assert s["condizione_tesi"] == " (e' la condizione 3 della Disciplina)"
    due = mp.sezioni(_aggiorna(prova, disciplina={"condizioni_taglio_oltre": {
        "sharpe_12m_negativo": True, "nessun_catalyst_90g": False, "tesi_smentita": True}}))
    assert due["condizione_tesi"] == " (e' la condizione 2 della Disciplina)"
    senza = mp.sezioni(_aggiorna(prova, disciplina={"condizioni_taglio_oltre": {
        "sharpe_12m_negativo": True, "nessun_catalyst_90g": True, "tesi_smentita": False}}))
    assert senza["condizione_tesi"] == ""
    from bellomberg.agents import capo
    compilato = mp.compila(capo.CAPO_SYSTEM_PROMPT, dict(prova, dichiarato_il="2031-04-09", origine="personalizzato"))
    assert "un taglio >55% contro una view dichiarata" in compilato and ">50%" not in compilato
    assert "Un trim PARZIALE (15-25%) NON richiede" in compilato


def test_il_drawdown_bilaterale_si_accende_e_si_spegne(prova):
    s = mp.sezioni(prova)["trim"]
    assert "VALUTAZIONE BILATERALE DEL DRAWDOWN" in s and ">22% dal massimo 52 settimane" in s
    assert "view long-term del PM" in s
    off = mp.sezioni(_aggiorna(prova, rischio={"drawdown_bilaterale": False}))["trim"]
    assert "BILATERALE" not in off and "senza automatismi" in off


def test_il_pair_trade_0_1_2(prova):
    assert "Al massimo DUE pair trade per memo, ognuno con una VERA tesi di valore relativo" in mp.sezioni(prova)["pair"]
    assert "NESSUN pair trade" in mp.sezioni(_aggiorna(prova, disciplina={"pair_trade_per_memo": 0}))["pair"]
    assert "UN solo pair trade per memo, con una VERA tesi di valore relativo" in mp.sezioni(_aggiorna(prova, disciplina={"pair_trade_per_memo": 1}))["pair"]


def test_le_opzioni_nascono_dalle_caselle(prova):
    s = mp.sezioni(prova)["opzioni"]
    ammesse, vietate = s.split("NON ammesso")
    assert "Solo: (1) LONG CALL con catalyst NOMINATO e DATATO, (2) PUT SPREAD come copertura del portafoglio" in ammesse
    assert "(3) COVERED CALL su posizioni esistenti" in ammesse and "(4) STRADDLE/STRANGLE" in ammesse
    assert "CASH-SECURED" not in ammesse and "PUT o PUT SPREAD" not in ammesse
    assert vietate.startswith(": call vertical, ratio spread, butterfly, condor, speculazione binaria")
    assert "vendita di premio nuda" in vietate and "straddle/strangle" not in vietate
    ridotto = mp.sezioni(_aggiorna(prova, opzioni={"strumenti_ammessi": ["long_call_catalyst", "put_hedge", "cash_secured_put"]}))["opzioni"]
    assert "(2) PUT come copertura" in ridotto and "(3) CASH-SECURED PUT su posizioni esistenti" in ridotto
    assert "PUT SPREAD" not in ridotto.split("NON ammesso")[0] and "straddle/strangle" in ridotto
    tutto = mp.sezioni(_aggiorna(prova, opzioni={"strumenti_ammessi": list(mp.STRUMENTI_OPZIONI), "budget_premio_pct": 3}))["opzioni"]
    assert "(2) PUT o PUT SPREAD come copertura" in tutto and "(3) COVERED CALL o CASH-SECURED PUT su posizioni esistenti" in tutto
    assert "(4) VENDITA DI PREMIO NUDA" in tutto and "(5) STRADDLE/STRANGLE" in tutto
    assert "Premio complessivo in opzioni: al massimo il 3% del patrimonio." in tutto
    spente = mp.sezioni(_aggiorna(prova, opzioni={"opzioni_abilitate": False, "strumenti_ammessi": []}))["opzioni"]
    assert "Il PM NON usa opzioni" in spente and "LONG CALL" not in spente


def test_caccia_globale_nuove_idee_e_rotazione(prova):
    s = mp.sezioni(prova)["caccia"]
    assert "NIENTE ANCORAGGIO AI SETTORI DEL BOOK" in s
    assert "ogni memo deve avere 3-4 candidati che NON sono nel book" in s
    assert "Giappone, India, Brasile" in s and "COME si compra da Amsterdam" in s
    assert "candidati globali degni" in s
    b = mp.sezioni(prova)["sezione_5bis"]
    assert "i 3-4 candidati GLOBALI" in b and "come si compra da Amsterdam" in b
    casa = _aggiorna(prova, profilo={"mercati_accessibili": ["DE", "US"]},
                     disciplina={"caccia_globale": False, "nuove_idee_per_memo": [1, 2], "rotazione_settoriale": False})
    c = mp.sezioni(casa)["caccia"]
    assert "NIENTE ANCORAGGIO" not in c and "1-2 candidati" in c
    assert "frontiera" not in c and "COME si compra da Francoforte" in c
    assert "Il PM non chiede rotazione settoriale" in c, "rotazione spenta = dichiarata (review 05/09: era codice morto)"
    assert "candidati degni" in c and "candidati globali" not in c
    assert "dai mercati accessibili al PM" in mp.sezioni(casa)["sezione_5bis"]
    zero = mp.sezioni(_aggiorna(prova, disciplina={"nuove_idee_per_memo": [0, 0]}))
    assert "NON richiesta" in zero["caccia"] and "non richiede candidati nuovi" in zero["sezione_5bis"]
    fino = mp.sezioni(_aggiorna(prova, disciplina={"nuove_idee_per_memo": [0, 3]}))
    assert "deve avere fino a 3 candidati" in fino["caccia"] and "0-3" not in fino["caccia"]
    assert "parole: fino a 3 candidati GLOBALI" in fino["sezione_5bis"]


def test_skipped_e_sfida_hanno_i_due_stati(prova):
    on = mp.sezioni(prova)
    assert "Le decisioni SKIPPED non sono rifiuti definitivi" in on["skipped"]
    assert "il PM vuole essere sfidato" in on["sfida"]
    off = mp.sezioni(_aggiorna(prova, disciplina={"riproporre_skipped": False, "sfidare_le_view": False}))
    assert "NON si ripropongono" in off["skipped"] and "vuole essere sfidato" not in off["sfida"]
    assert "vincolanti" in off["sfida"]


def test_il_profilo_per_le_tesi_viene_dal_mandato(prova):
    s = mp.sezioni(prova)["profilo_tesi"]
    assert s.startswith("2. Il PM investe LONG-TERM e accetta concentrazione sulle conviction: un drawdown "
                        "va valutato in modo BILATERALE")
    assert "3. Le view NON sono ordini: se i dati le smentiscono, dillo apertamente — il PM vuole essere sfidato, non assecondato." in s
    m = mp.sezioni(_aggiorna(prova, profilo={"tipo_investimento": "medio_termine", "stile": "diversificato"},
                             rischio={"drawdown_bilaterale": False}, disciplina={"sfidare_le_view": False}))["profilo_tesi"]
    assert "a MEDIO TERMINE" in m and "DIVERSIFICATO" in m and "BILATERALE" not in m
    assert "vincolanti" in m


def test_profilo_e_rischio_per_i_desk(prova):
    s = mp.sezioni(prova)["profilo_rischio"]
    assert "PROFILO DEL PM: investe LONG-TERM (orizzonte 7 anni), book CONCENTRATO" in s
    assert "Amsterdam (AS)" in s and "Londra (L)" in s and "preferenza UCITS: si" in s
    assert "RISCHIO ACCETTATO:" in s and "SIZING:" in s and "9% / cap 11%" in s


def test_blocco_prompt_e_intero_e_apre_con_l_intestazione(prova):
    prova = dict(prova, dichiarato_il="2031-04-09", origine="personalizzato")
    b = mp.blocco_prompt(prova)
    assert b.startswith("MANDATO DEL PM (dichiarato il 09/04/2031")
    for pezzo in ("PROFILO DEL PM:", "## GESTIONE DEL CASH", "condizioni simultanee", "pair trade",
                  "LONG CALL", "CACCIA GLOBALE", "SKIPPED"):
        assert pezzo in b, pezzo
    assert "{MANDATO:" not in b


def test_compila_riempie_ogni_segnaposto_e_rifiuta_gli_ignoti(prova):
    prova = dict(prova, dichiarato_il="2031-04-09", origine="personalizzato")
    out = mp.compila("a {MANDATO:intestazione} b {MANDATO:pair} c", prova)
    assert "{MANDATO:" not in out and "pair trade per memo" in out and "impronta" in out
    with pytest.raises(ValueError) as ei:
        mp.compila("x {MANDATO:boh}", prova)
    assert "boh" in str(ei.value)


def test_compila_o_dichiara_senza_mandato_scrive_la_riga(prova):
    out = mp.compila_o_dichiara("a {MANDATO:intestazione} b {MANDATO:pair} c", None)
    assert "{MANDATO:" not in out and "MANDATO NON DICHIARATO" in out
    assert mp.riga_senza_mandato() in out


# --------------------------------------------------------------------------- il blocco della cassa a runtime

def test_la_frase_della_cassa_dipende_dalla_banda_del_mandato(prova):
    molto = mp.frase_cassa_runtime(prova, cash_eur=45_000.0, nav_eur=100_000.0, mkt_eur=55_000.0)
    assert molto.startswith("Cash liquido da impiegare: EUR 45,000 (45% del capitale totale di EUR 100,000)")
    assert "QUESTO E' MOLTO CASH FERMO." in molto
    assert "Con EUR 45,000 di cash, un piano da EUR 6,750 e' troppo timido: pensa in termini di EUR 13,500-24,750" in molto
    assert "nelle prossime 1-3 settimane" in molto
    poco = mp.frase_cassa_runtime(prova, cash_eur=7_000.0, nav_eur=100_000.0, mkt_eur=93_000.0)
    assert "MOLTO CASH FERMO" not in poco and "CASSA SOTTO LA BANDA TIPICA" in poco
    assert "7% contro 35-45%" in poco and "SOTTO LA MINIMA" not in poco
    minima = mp.frase_cassa_runtime(_aggiorna(prova, cassa={"cassa_minima_pct": 10}), 7_000.0, 100_000.0, 93_000.0)
    assert "CASSA SOTTO LA MINIMA DICHIARATA (10%)" in minima


def test_la_frase_della_cassa_segue_la_politica(prova):
    neutra = mp.frase_cassa_runtime(_aggiorna(prova, cassa={"politica_impiego": "neutra"}), 45_000.0, 100_000.0, 55_000.0)
    assert "MOLTO CASH FERMO" not in neutra and "NEUTRO" in neutra and "EUR 13,500-24,750" in neutra
    prud = mp.frase_cassa_runtime(_aggiorna(prova, cassa={"politica_impiego": "prudente"}), 45_000.0, 100_000.0, 55_000.0)
    assert "MOLTO CASH FERMO" not in prud and "PRUDENZA" in prud


# --------------------------------------------------------------------------- le viste per i motori

def test_sizing_params_da_percentuali_a_frazioni(prova):
    p = mp.sizing_params(prova)
    assert p["base_single"] == pytest.approx(0.09) and p["cap_single"] == pytest.approx(0.11)
    assert p["base_veicolo"] == pytest.approx(0.24) and p["cap_veicolo"] == pytest.approx(0.27)
    assert p["cap_settore"] == pytest.approx(0.28) and p["limite_minimo"] == pytest.approx(0.015)
    assert p["posizione_minima_pct"] == pytest.approx(0.3)
    assert p["budget_stress_nav_pct"] < 0 and p["budget_var99_nav_pct"] < 0
    assert p["size_nuova_posizione"] == (pytest.approx(0.02), pytest.approx(0.05))


def test_le_viste_cassa_opzioni_disciplina(prova):
    c = mp.cassa(prova)
    assert c["cassa_tipica_pct"] == [35, 45] and c["politica_impiego"] == "aggressiva"
    o = mp.opzioni(prova)
    assert o["opzioni_abilitate"] is True and "covered_call" in o["strumenti_ammessi"]
    d = mp.disciplina(prova)
    assert d["pair_trade_per_memo"] == 2 and d["caccia_globale"] is True


# --------------------------------------------------------------------------- il Capo legge il mandato a ogni run

def _capo_offline(monkeypatch):
    from bellomberg.agents import capo
    from bellomberg.valuation import cef_lookthrough
    from bellomberg.core import current_facts
    from bellomberg.portfolio import signal_engine
    chiamate = []
    memo = SimpleNamespace(content=[SimpleNamespace(type="text", text="## ACTION TABLE\n" + "x " * 1500)],
                           stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                           model="finto")

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return memo

    class _Messages:
        def stream(self, **kw):
            chiamate.append(kw)
            return _Stream()

    class _Client:
        def __init__(self, **kw):
            self.messages = _Messages()
    monkeypatch.setattr(capo, "OpenRouterClient", _Client)
    monkeypatch.setattr(current_facts, "current_facts_block", lambda: "(fatti finti)")
    monkeypatch.setattr(current_facts, "pm_theses_block", lambda: "")
    monkeypatch.setattr(cef_lookthrough, "capo_block", lambda: "")
    monkeypatch.setattr(signal_engine, "scan_portfolio", lambda **k: {"signals": []})
    return capo, chiamate


def _book(cash, mkt):
    return {"source": "memory_db", "fx_incomplete": None, "stale_positions": None, "n_positions": 0,
            "positions": [], "totale_valore_mercato_eur": mkt, "cash_disponibile_eur": cash,
            "cash_source": "portfolio.json", "cash_source_note": None, "nav_total_eur": cash + mkt,
            "totale_pl_eur": 0.0, "timestamp": "2031-04-09T10:00:00"}


def test_il_system_prompt_del_capo_e_un_template_senza_la_dottrina():
    from bellomberg.agents import capo
    for chiave in ("intestazione", "profilo_rischio", "cassa", "trim", "pair", "opzioni", "caccia",
                   "sezione_5bis", "skipped", "sfida", "soglia_taglio", "taglio_libero", "condizione_tesi"):
        assert "{MANDATO:" + chiave + "}" in capo.CAPO_SYSTEM_PROMPT, chiave
    for frase in ("DEFAULT AGGRESSIVO", "VUOLE METTERLA A LAVORO", "condizioni simultanee",
                  "pair trade per memo", "Solo: (1) LONG CALL", "candidati che NON sono nel book",
                  "vuole essere sfidato", "RIPROPONILE"):
        assert frase not in capo.CAPO_SYSTEM_PROMPT, frase


def test_il_capo_compila_il_mandato_dal_disco_a_ogni_run(monkeypatch, file_prova, prova):
    capo, chiamate = _capo_offline(monkeypatch)
    bb = SimpleNamespace(data={"macro": {2: "report macro finto"}})
    capo.run_capo(bb, portfolio_data=_book(45_000.0, 55_000.0))
    assert len(chiamate) == 1, "il memo finto e' collassato: il retry sposterebbe gli indici"
    system = chiamate[0]["system"]
    assert "{MANDATO:" not in system and "MANDATO DEL PM (dichiarato il" in system
    assert "(tipicamente 35-45% del capitale)" in system
    assert "QUESTO E' MOLTO CASH FERMO" in chiamate[0]["messages"][0]["content"]
    _scrivi(file_prova, _aggiorna(prova, cassa={"cassa_tipica_pct": [20, 30]}))
    capo.run_capo(bb, portfolio_data=_book(7_000.0, 93_000.0))
    assert len(chiamate) == 2
    system2 = chiamate[-1]["system"]
    assert "(tipicamente 20-30% del capitale)" in system2 and "35-45%" not in system2
    um2 = chiamate[-1]["messages"][0]["content"]
    assert "CASSA SOTTO LA BANDA TIPICA" in um2 and "MOLTO CASH FERMO" not in um2


def test_il_capo_senza_mandato_muore_dichiarato_prima_di_chiamare(monkeypatch, tmp_path):
    capo, chiamate = _capo_offline(monkeypatch)
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "manca.json"))
    with pytest.raises(mp.MandatoMancante):
        capo.run_capo(SimpleNamespace(data={"macro": {2: "r"}}), portfolio_data=_book(1.0, 1.0))
    assert chiamate == [], "nessuna chiamata al modello senza mandato"


def test_il_consigliere_esce_prima_del_blackboard_senza_mandato(monkeypatch, tmp_path, capsys):
    from bellomberg.agents import consigliere_multi
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "manca.json"))
    with pytest.raises(SystemExit) as ei:
        consigliere_multi.mandato_o_esci()
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert "MANDATO NON DICHIARATO" in out and "Mandato" in out


def test_il_consigliere_con_mandato_non_esce(file_prova):
    from bellomberg.agents import consigliere_multi
    m = consigliere_multi.mandato_o_esci()
    assert m["sizing"]["cap_single_pct"] == 11


def test_il_consigliere_chiama_il_controllo_prima_di_costruire_il_blackboard():
    """Testare l'helper non e' testare il CABLAGGIO: la riga che lo chiama deve stare in
    run_multi_agent PRIMA della costruzione del blackboard (cioe' prima di pagare i desk)."""
    with open(os.path.join(REPO, 'src/bellomberg/agents/consigliere_multi.py'), encoding="utf-8", errors="replace") as fh:
        albero = ast.parse(fh.read())
    fn = next(n for n in albero.body if isinstance(n, ast.FunctionDef) and n.name == "run_multi_agent")
    chiamate = []
    for i, stmt in enumerate(fn.body):
        for sotto in ast.walk(stmt):
            if isinstance(sotto, ast.Call):
                f = sotto.func
                nome = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if nome in ("mandato_o_esci", "Blackboard"):
                    chiamate.append((nome, i))
    idx = {}
    for nome, i in chiamate:
        idx.setdefault(nome, i)
    assert "mandato_o_esci" in idx, "run_multi_agent non chiama mandato_o_esci"
    assert "Blackboard" in idx and idx["mandato_o_esci"] < idx["Blackboard"], idx


# --------------------------------------------------------------------------- le tesi del PM portano il profilo VIVO

@pytest.fixture
def db_tesi(tmp_path, monkeypatch):
    from bellomberg.core import current_facts
    from bellomberg.storage import memory_db
    path = str(tmp_path / "consigliere.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE positions (ticker TEXT, tesi TEXT, is_active INT, quantita REAL, prezzo_medio REAL)")
    con.execute("CREATE TABLE trade_history (id INTEGER PRIMARY KEY, ticker TEXT, data TEXT, action TEXT, pm_rationale TEXT)")
    con.execute("INSERT INTO positions VALUES ('IOTA.L', 'tesi inventata di prova', 1, 7, 111.11)")
    con.commit()
    con.close()
    monkeypatch.setattr(memory_db, "SQLITE_PATH", path)
    current_facts._TESI_CACHE["text"] = None
    current_facts._TESI_CACHE["ts"] = 0
    yield path
    current_facts._TESI_CACHE["text"] = None
    current_facts._TESI_CACHE["ts"] = 0


def test_le_tesi_portano_il_profilo_del_mandato_e_lo_rileggono_anche_a_cache_calda(db_tesi, file_prova, prova):
    from bellomberg.core import current_facts
    b1 = current_facts.pm_theses_block()
    assert "tesi inventata di prova" in b1
    assert "2. Il PM investe LONG-TERM e accetta concentrazione sulle conviction" in b1
    _scrivi(file_prova, _aggiorna(prova, profilo={"tipo_investimento": "medio_termine", "stile": "diversificato"}))
    b2 = current_facts.pm_theses_block()          # la cache delle tesi (600 s) e' ancora calda
    assert "a MEDIO TERMINE" in b2 and "LONG-TERM" not in b2, "il profilo era finito nella cache"
    assert "tesi inventata di prova" in b2


def test_le_tesi_senza_mandato_lo_dichiarano_e_restano(db_tesi, monkeypatch, tmp_path):
    from bellomberg.core import current_facts
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "manca.json"))
    b = current_facts.pm_theses_block()
    assert "MANDATO NON DICHIARATO" in b and "tesi inventata di prova" in b
    assert "LONG-TERM" not in b


# --------------------------------------------------------------------------- nessuna costante a import-time

MODULI_CHE_LEGGONO_IL_MANDATO = ('src/bellomberg/agents/capo.py', 'src/bellomberg/core/current_facts.py', 'src/bellomberg/portfolio/sizing_engine.py', 'src/bellomberg/agents/red_team.py',
                                 'src/bellomberg/agents/chat_engine.py', 'src/bellomberg/agents/consigliere_multi.py', 'src/bellomberg/cli/regenerate_memo.py',
                                 'src/bellomberg/agents/action_validator.py', 'src/bellomberg/api/bellomberg_api.py', 'src/bellomberg/agents/specialists/base.py',
                                 'src/bellomberg/agents/specialists/options.py', 'src/bellomberg/agents/specialists/fundamentals.py')


def _chiamate_al_mandato_a_livello_modulo(sorgente):
    """Le chiamate a mandato_pm.* (o a nomi importati da mandato_pm) fuori da funzioni e classi."""
    albero = ast.parse(sorgente)
    importati = set()
    for nodo in albero.body:
        if isinstance(nodo, ast.ImportFrom) and nodo.module in {"mandato_pm", "bellomberg.core.mandato_pm"}:
            importati |= {a.asname or a.name for a in nodo.names}
    alias = {"mandato_pm"}
    for nodo in albero.body:
        if isinstance(nodo, ast.Import):
            alias |= {a.asname or a.name for a in nodo.names if a.name in {"mandato_pm", "bellomberg.core.mandato_pm"}}
    colpevoli = []
    for nodo in albero.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        for sotto in ast.walk(nodo):
            if not isinstance(sotto, ast.Call):
                continue
            f = sotto.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in alias:
                colpevoli.append("riga %d: %s.%s()" % (sotto.lineno, f.value.id, f.attr))
            elif isinstance(f, ast.Name) and f.id in importati:
                colpevoli.append("riga %d: %s()" % (sotto.lineno, f.id))
    return colpevoli


def test_nessun_modulo_congela_il_mandato_in_una_costante_a_import_time():
    trovati = {}
    for rel in MODULI_CHE_LEGGONO_IL_MANDATO:
        with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as fh:
            c = _chiamate_al_mandato_a_livello_modulo(fh.read())
        if c:
            trovati[rel] = c
    assert not trovati, trovati


def test_il_controllo_ast_sa_vedere_una_costante_congelata():
    """Il canarino del controllo: senza, un test verde direbbe «nessuna costante» anche se
    non guardasse niente (lezione 21/08)."""
    assert _chiamate_al_mandato_a_livello_modulo('from bellomberg.core import mandato_pm\nMANDATO = mandato_pm.carica()\n')
    assert _chiamate_al_mandato_a_livello_modulo("from bellomberg.core.mandato_pm import carica\nX = {'m': carica()}\n")
    assert _chiamate_al_mandato_a_livello_modulo('import bellomberg.core.mandato_pm as mp\nmp.carica()\n')
    assert not _chiamate_al_mandato_a_livello_modulo('from bellomberg.core import mandato_pm\ndef f():\n    return mandato_pm.carica()\n')


# --------------------------------------------------------------------------- dalla review del 05/09 (3 agenti)

def test_lo_schema_dichiara_l_intervallo_di_ogni_campo_numerico():
    for nome, c in mp.CAMPI.items():
        if c["tipo"] in ("int", "num", "pct", "intervallo_pct", "intervallo_int"):
            assert c["intervallo"] and len(c["intervallo"]) == 2, nome


def test_nan_e_infinito_sono_errori_dichiarati_non_traceback(esempio):
    _, err = mp.valida(_aggiorna(esempio, rischio={"volatilita_target_pct": float("nan")}))
    assert any(e.startswith("volatilita_target_pct") for e in err), err
    _, err = mp.valida(_aggiorna(esempio, cassa={"cassa_tipica_pct": [10, float("inf")]}))
    assert any(e.startswith("cassa_tipica_pct") for e in err), err


def test_il_testo_libero_non_puo_portare_un_segnaposto(esempio):
    _, err = mp.valida(_aggiorna(esempio, note={"note_per_il_comitato": "vedi {MANDATO:cassa}"}))
    assert any(e.startswith("note_per_il_comitato") for e in err), err
    _, err = mp.valida(_aggiorna(esempio, note={"esclusioni": ["{MANDATO:pair}"]}))
    assert any(e.startswith("esclusioni") for e in err), err


def test_versione_e_data_del_file_sono_controllate(esempio):
    _, err = mp.valida(dict(esempio, versione=99))
    assert any(e.startswith("versione") for e in err), err
    _, err = mp.valida(dict(esempio, dichiarato_il="ieri"))
    assert any(e.startswith("dichiarato_il") for e in err), err
    assert mp.valida(dict(esempio, versione=mp.VERSIONE_SCHEMA, dichiarato_il="2031-04-09"))[1] == []


def test_altre_coerenze_var_stress_opzioni_e_sizing(esempio):
    _, err = mp.valida(_aggiorna(esempio, rischio={"var99_1g_pct": 14, "drawdown_max_pct": 30, "stress_gfc_pct": 10}))
    assert any("stress" in e and e.startswith("var99_1g_pct") for e in err), err
    _, err = mp.valida(_aggiorna(esempio, opzioni={"opzioni_abilitate": False, "strumenti_ammessi": ["put_hedge"]}))
    assert any(e.startswith("strumenti_ammessi") and "disabilitate" in e for e in err), err
    _, err = mp.valida(_aggiorna(esempio, sizing={"base_veicolo_pct": 30, "cap_veicolo_pct": 25}))
    assert any(e.startswith("base_veicolo_pct") for e in err), err
    _, err = mp.valida(_aggiorna(esempio, sizing={"limite_minimo_pct": 7, "base_single_pct": 6}))
    assert any(e.startswith("limite_minimo_pct") for e in err), err
    _, err = mp.valida(_aggiorna(esempio, sizing={"posizione_minima_pct": 9, "cap_single_pct": 8}))
    assert any(e.startswith("posizione_minima_pct") for e in err), err


def test_carica_su_una_lista_e_su_campi_sconosciuti(tmp_path, esempio):
    p = tmp_path / "m.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica(str(p))
    assert ei.value.causa == "incompleto" and ei.value.campi == ["mandato"]
    _, err = mp.valida(_aggiorna(esempio, cassa={"boh": 1}))
    assert any("campi sconosciuti" in e and "boh" in e for e in err), err


def test_la_forma_del_numero_non_cambia_l_impronta(esempio):
    a, _ = mp.valida(esempio)
    b, _ = mp.valida(_aggiorna(esempio, rischio={"volatilita_target_pct": 15.0}, cassa={"cassa_tipica_pct": [10.0, 15]}))
    assert mp.impronta(a) == mp.impronta(b)


def test_carica_ritenta_se_il_file_e_in_uso(file_prova, monkeypatch):
    import builtins
    vera = builtins.open
    colpi = {"n": 0}

    def _una_volta(path, *a, **k):
        if str(path) == file_prova and colpi["n"] == 0:
            colpi["n"] += 1
            raise PermissionError("in uso da un altro processo")
        return vera(path, *a, **k)
    monkeypatch.setattr(builtins, "open", _una_volta)
    assert mp.carica()["profilo"]["orizzonte_anni"] == 7 and colpi["n"] == 1

    def _sempre(path, *a, **k):
        if str(path) == file_prova:
            raise PermissionError("in uso")
        return vera(path, *a, **k)
    monkeypatch.setattr(builtins, "open", _sempre)
    monkeypatch.setattr(mp.time, "sleep", lambda s: None)
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica()
    assert ei.value.causa == "in_uso" and "riprova" in str(ei.value)


def test_l_esempio_del_repo_rotto_e_dichiarato_non_personalizzato(file_prova, monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "ESEMPIO_MANDATO", str(tmp_path / "manca.example.json"))
    with pytest.raises(mp.MandatoMancante) as ei:
        mp.carica()
    assert ei.value.causa == "esempio" and "checkout" in str(ei.value)


def test_tre_salvataggi_nello_stesso_secondo_tengono_tre_backup_distinti(tmp_path, prova):
    p = str(tmp_path / "mandato_pm.json")
    for cap in (11, 12, 13, 14):
        mp.salva(_aggiorna(prova, sizing={"cap_single_pct": cap}), p)
    backup = sorted(glob.glob(os.path.join(str(tmp_path), "backups", "mandato_pm_*.json")))
    assert len(backup) == 3 and len(set(backup)) == 3, backup
    assert sorted(json.load(open(b, encoding="utf-8"))["sizing"]["cap_single_pct"] for b in backup) == [11, 12, 13]


def test_la_cassa_non_misurata_non_riceve_un_giudizio(prova, monkeypatch):
    s = mp.frase_cassa_runtime(prova, 0.0, 100_000.0, 100_000.0, cassa_misurata=False)
    assert "CAPITALE NON MISURATO" in s and "SOTTO" not in s and "MOLTO CASH FERMO" not in s
    z = mp.frase_cassa_runtime(_aggiorna(prova, cassa={"cassa_minima_pct": 10}), 0.0, 0.0, 0.0)
    assert "CAPITALE NON MISURATO" in z and "SOTTO LA MINIMA" not in z
    capo, chiamate = _capo_offline(monkeypatch)
    book = dict(_book(0.0, 100_000.0), cash_source=None, cash_source_note="portfolio.json assente in X")
    capo.run_capo(SimpleNamespace(data={"macro": {2: "r"}}), portfolio_data=book)
    um = chiamate[-1]["messages"][0]["content"]
    assert "CASSA NON MISURATA" in um and "CAPITALE NON MISURATO" in um
    assert "SOTTO LA BANDA" not in um and "SOTTO LA MINIMA" not in um and "MOLTO CASH FERMO" not in um


def test_il_giudizio_sulla_cassa_stampa_il_decimale(prova):
    s = mp.frase_cassa_runtime(prova, 34_600.0, 100_000.0, 65_400.0)
    assert "CASSA SOTTO LA BANDA TIPICA del mandato (34,6% contro 35-45%)" in s


def test_un_guasto_nel_profilo_delle_tesi_e_dichiarato_e_le_tesi_restano(db_tesi, file_prova, monkeypatch):
    from bellomberg.core import current_facts
    monkeypatch.setattr(mp, "sezioni", lambda m: (_ for _ in ()).throw(RuntimeError("boom")))
    b = current_facts.pm_theses_block()
    assert "PROFILO DEL PM n.d. (RuntimeError: boom)" in b and "tesi inventata di prova" in b


def test_il_consigliere_lascia_il_motivo_per_l_heartbeat(monkeypatch, tmp_path):
    from bellomberg.agents import consigliere_multi
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "manca.json"))
    consigliere_multi._MOTIVO_USCITA["testo"] = ""
    with pytest.raises(SystemExit):
        consigliere_multi.mandato_o_esci()
    assert consigliere_multi._MOTIVO_USCITA["testo"].startswith("MANDATO NON DICHIARATO")


def test_regenerate_memo_controlla_il_mandato_prima_del_blackboard():
    with open(os.path.join(REPO, 'src/bellomberg/cli/regenerate_memo.py'), encoding="utf-8", errors="replace") as fh:
        albero = ast.parse(fh.read())
    fn = next(n for n in albero.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    idx = {}
    for i, stmt in enumerate(fn.body):
        for sotto in ast.walk(stmt):
            if isinstance(sotto, ast.Call):
                f = sotto.func
                nome = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if nome in ("carica", "Blackboard"):
                    idx.setdefault(nome, i)
    assert "carica" in idx and "Blackboard" in idx and idx["carica"] < idx["Blackboard"], idx
