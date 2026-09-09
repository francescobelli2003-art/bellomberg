# -*- coding: utf-8 -*-
"""Il mandato non perde in SILENZIO le chiavi che `valida` non conosce (criterio (5),
lotto B, difetto 2 della coda di A2 — MASTER §9-sexoctogies).

Cosa deve essere vero per chi usa il programma:
  - il file del mandato porta una NOTA di provenienza («questi valori vengono da...»):
    salvare dalla pagina Mandato non deve cancellargliela senza dirglielo;
  - una chiave di primo livello che il programma non conosce non sparisce zitta:
    o resta, o viene NOMINATA fra gli errori. Mai il silenzio.
  - la nota non e' un valore del mandato: non deve muovere l'IMPRONTA, che e' cio' con
    cui si riconcilia un memo col mandato che l'ha prodotto.

La cura non e' «ricopia _nota» (allora la prossima chiave nuova si perde identica e
invisibile): e' che nessuna chiave sparisca senza essere dichiarata.

Tutti i valori qui sotto sono INVENTATI: il file di casa non entra mai in un test.
"""
import copy
import json
import os

import pytest

import bellomberg.core.mandato_pm as mp


NOTA = "Provenienza inventata: valori di prova, non di nessun portafoglio."


def _grezzo_valido():
    """Un mandato che vale, in forma GREZZA (come arriva dal disco o da un PUT)."""
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"] = mp.VERSIONE_SCHEMA
    m["dichiarato_il"] = "2026-01-02"
    m["origine"] = "personalizzato"
    return m


def _scrivi(path, m):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)


# --------------------------------------------------------------- la nota sopravvive

def test_valida_conserva_la_nota_di_primo_livello():
    """`_nota` e' la provenienza dei valori: `valida` la ricostruisce da zero e la perdeva."""
    grezzo = _grezzo_valido()
    grezzo["_nota"] = NOTA
    m, errori = mp.valida(grezzo)
    assert errori == [], errori
    assert m.get("_nota") == NOTA, "valida ha scartato la nota di provenienza senza dirlo"


def test_carica_rende_la_nota_del_file(tmp_path):
    """Chi legge il mandato deve VEDERE la nota: se `carica` non la rende, il primo
    `salva(carica())` la cancella e nessuno se ne accorge."""
    p = str(tmp_path / "mandato_pm.json")
    grezzo = _grezzo_valido()
    grezzo["_nota"] = NOTA
    _scrivi(p, grezzo)
    m = mp.carica(p)
    assert m.get("_nota") == NOTA


def test_salva_non_perde_la_nota_quando_il_payload_non_la_porta(tmp_path):
    """Il caso della pagina Mandato: la pagina rimanda i sette blocchi, non la nota.
    Il salvataggio non deve cancellare la provenienza che sta gia' nel file."""
    p = str(tmp_path / "mandato_pm.json")
    grezzo = _grezzo_valido()
    grezzo["_nota"] = NOTA
    _scrivi(p, grezzo)

    senza_nota = _grezzo_valido()                      # esattamente cio' che manderebbe il PUT
    senza_nota["cassa"]["cassa_minima_pct"] = 3        # una modifica qualunque
    assert "_nota" not in senza_nota
    mp.salva(senza_nota, p)

    with open(p, encoding="utf-8") as fh:
        sul_disco = json.load(fh)
    assert sul_disco.get("_nota") == NOTA, "il salvataggio ha cancellato la provenienza"
    assert sul_disco["cassa"]["cassa_minima_pct"] == 3, "la modifica non e' stata scritta"


# --------------------------------------------------- cio' che si scarta si DICHIARA

def test_una_chiave_di_primo_livello_sconosciuta_viene_nominata():
    """Non `_`-prefissa e non nota: e' un errore che NOMINA la chiave, come fanno gia' i
    blocchi («campi sconosciuti»). Altrimenti alla prossima chiave nuova il difetto torna
    identico e invisibile."""
    grezzo = _grezzo_valido()
    grezzo["orizzonte_temporale"] = 5          # chiave plausibile, scritta nel posto sbagliato
    m, errori = mp.valida(grezzo)
    assert errori, "una chiave sconosciuta di primo livello e' passata senza una parola"
    assert any("orizzonte_temporale" in e for e in errori), errori
    assert all(":" in e for e in errori), "gli errori devono restare nella forma «campo: motivo»"


@pytest.mark.parametrize("chiave, valore", [
    ("_nota", NOTA),
    ("_provenienza", "un'altra chiave meta, inventata domani"),
    ("chiave_nuova_di_domani", 42),
    ("note_del_pm", ["una", "due"]),
])
def test_nessuna_chiave_di_primo_livello_sparisce_in_silenzio(chiave, valore):
    """L'INVARIANTE, ed e' questa che protegge dalla prossima chiave: ogni chiave di primo
    livello o RESTA nel mandato, o viene NOMINATA fra gli errori. Il terzo esito — sparire
    senza una parola — non deve esistere."""
    grezzo = _grezzo_valido()
    grezzo[chiave] = valore
    m, errori = mp.valida(grezzo)
    resta = chiave in m
    nominata = any(chiave in e for e in errori)
    assert resta or nominata, (
        "la chiave %r e' sparita in silenzio: non e' nel mandato e nessun errore la nomina "
        "(errori: %r)" % (chiave, errori))


def test_l_invariante_vale_per_ogni_chiave_del_grezzo():
    """La stessa cosa detta su TUTTE le chiavi insieme, come la vedrebbe un endpoint che
    riceve un corpo JSON scritto da qualcun altro."""
    grezzo = _grezzo_valido()
    grezzo["_nota"] = NOTA
    grezzo["_altra_meta"] = {"a": 1}
    grezzo["refuso"] = "chiave non prevista"
    m, errori = mp.valida(grezzo)
    sparite = [k for k in grezzo
               if k not in m and not any(k in e for e in errori)]
    assert sparite == [], "chiavi sparite senza dichiarazione: %r" % (sparite,)


# ------------------------------------------------------- la nota non e' un valore

def test_la_nota_non_muove_l_impronta():
    """L'impronta identifica i VALORI (i sette blocchi): con essa si riconcilia un memo col
    mandato che l'ha prodotto. Una nota di provenienza non e' un valore e non deve muoverla,
    altrimenti aggiungere una riga di commento farebbe sembrare cambiato il mandato."""
    senza, err1 = mp.valida(_grezzo_valido())
    grezzo = _grezzo_valido()
    grezzo["_nota"] = NOTA
    con, err2 = mp.valida(grezzo)
    assert err1 == [] and err2 == []
    assert mp.impronta(con) == mp.impronta(senza)


def test_la_nota_non_cambia_l_origine_misurata():
    """`origine` si MISURA dai valori: una nota non deve far sembrare «personalizzato» un
    profilo che e' ancora quello di esempio."""
    grezzo = copy.deepcopy(mp.profilo_esempio())
    grezzo["_nota"] = NOTA
    m, errori = mp.valida(grezzo)
    assert errori == [], errori
    assert mp._origine(m) == "esempio"
