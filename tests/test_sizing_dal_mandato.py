# -*- coding: utf-8 -*-
"""A2 (criterio 5): i tetti del motore di sizing NON sono piu' costanti del modulo — vengono
dal MANDATO del PM, letto dal disco a OGNI chiamata.

Perche' questa batteria esiste (audit 26 §3, spec 2026-09-05-mandato-pm-design §4): chi
clonava il repo riceveva il dimensionamento costruito sulla propensione al rischio di un
altro — baseline 10%, cap 12%, veicoli 30/33, settore 30 — scritta nel codice. Da A2 quei
numeri sono dichiarati dall'utente e il motore li rilegge a ogni chiamata; **senza mandato
il motore NON dimensiona**, dichiara (regola 14/07: mai i tetti di ieri come ripiego zitto).

Le prove qui dentro devono cadere se qualcuno rimette una costante nel modulo o dimentica
di rileggere il file: per questo ci sono il test AST col suo CANARINO e il test «A poi B».

Simboli INVENTATI (esempio tracciato dei veicoli): nessun dato del PM in un file tracciato.
"""
import ast
import copy
import io
import json
import os

import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.core.mandato_pm as mp
import bellomberg.portfolio.sizing_engine as se

# --- book sintetico: sei nomi inventati, pesi scelti per avere sia tagli che spazio ---
BOOK = {
    "positions": [
        {"ticker": "FONDO.L", "valore_mercato_eur": 30400},
        {"ticker": "ACME.MI", "valore_mercato_eur": 13300},
        {"ticker": "GAMMA", "valore_mercato_eur": 11100},
        {"ticker": "ETFX.MI", "valore_mercato_eur": 9800},
        {"ticker": "IGNOTO.X", "valore_mercato_eur": 4100},
        {"ticker": "METALLO", "valore_mercato_eur": 52500},
    ],
    "cash_disponibile_eur": 20000,
}
RISK = {"per_asset": {"FONDO.L": {"vol_annual_pct": 14}, "ACME.MI": {"vol_annual_pct": 38},
                      "GAMMA": {"vol_annual_pct": 52}, "ETFX.MI": {"vol_annual_pct": 28},
                      "IGNOTO.X": {"vol_annual_pct": 34}, "METALLO": {"vol_annual_pct": 13}}}

NAV = 121200 + 20000  # investito + cassa, come lo calcola il motore

# --- ORACOLO congelato dal codice di HEAD PRIMA di A2 (regola «oracolo non dal codice sotto
# test»): coi valori di ieri i numeri non si devono muovere di un centesimo. ---
ATTESO_IERI = {
    "FONDO.L": [33.0, 14322.0, 0.0],
    "ACME.MI": [6.53, 0.0, 5758.0],
    "GAMMA": [4.8, 0.0, 5549.0],
    "ETFX.MI": [27.6, 32667.0, 0.0],
    "IGNOTO.X": [7.6, 5532.0, 0.0],
    "METALLO": [33.0, 0.0, 18663.0],
}
SOMMARIO_IERI = {
    "invested_capital_eur": 121200.0,
    "total_room_existing_eur": 52521.0,
    "total_trim_eur": 29969.0,
    "n_names_with_room": 3,
    "names_over_limit": ["ACME.MI", "GAMMA", "METALLO"],
}
# i valori CABLATI fino a ieri, ora dichiarati dal PM. `posizione_minima_pct` e' scelta per
# rendere la soglia = 500,00 EUR sul NAV di questo book (500/141200*100), cosi' il confronto
# col congelato e' a UNA variabile: i tetti, non la soglia.
PARAMS_IERI = {
    "base_single": 0.10, "cap_single": 0.12,
    "base_veicolo": 0.30, "cap_veicolo": 0.33,
    "cap_settore": 0.30, "limite_minimo": 0.02,
    "posizione_minima_pct": 500.0 / NAV * 100.0,
    "budget_stress_nav_pct": -25.0, "budget_var99_nav_pct": -4.0,
    "size_nuova_posizione": (0.02, 0.04), "max_posizioni": None, "top3_max": None,
    "impronta": "ieri0000",
}

COSTANTI_VIETATE = {
    "BASE_SINGLE", "CAP_SINGLE", "BASE_VEHICLE", "CAP_VEHICLE", "SECTOR_CAP",
    "MIN_LIMIT", "MIN_ROOM_EUR", "STRESS_GFC_NAV_BUDGET_PCT", "VAR99_NAV_BUDGET_PCT",
}


@pytest.fixture
def negozio():
    return cl.carica_veicoli(cl.ESEMPIO_VEICOLI)


@pytest.fixture
def params():
    """I parametri dal mandato di ESEMPIO che il conftest mette in tmp (cap single 8%,
    veicolo 25%, settore 25%, pavimento 1%): numeri DIVERSI da quelli di ieri, cosi' un
    tetto cablato che sopravvive si vede."""
    return mp.sizing_params(mp.carica())


def _riga(s, t):
    return next(p for p in s["positions"] if p["ticker"] == t)


def _scrivi_mandato(tmp_path, **sizing):
    """Un mandato valido col blocco sizing modificato, salvato in tmp e reso corrente."""
    m = copy.deepcopy(mp.profilo_esempio())
    m["sizing"].update(sizing)
    p = str(tmp_path / "mandato_pm.json")
    mp.salva(m, p)
    return p


# ---------------------------------------------------------------- il motore senza mandato

def test_senza_mandato_il_motore_dichiara_e_non_dimensiona(monkeypatch, tmp_path, negozio):
    """Regola 14/07: mandato assente = buco DICHIARATO, mai i tetti di ieri applicati zitti."""
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "non_esiste.json"))
    out = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert out.get("error"), "senza mandato il motore ha comunque prodotto un sizing"
    assert "mandato" in out["error"].lower()
    # e NON deve aver prodotto numeri: nessuna riga, nessun sommario
    assert "positions" not in out and "summary" not in out
    # nessun tetto di ieri trapelato nel messaggio
    testo = json.dumps(out, ensure_ascii=False)
    for vietato in ("0.12", "0.33", "12%", "33%"):
        assert vietato not in testo, "il tetto di ieri %r e' finito nell'errore" % vietato


def test_senza_mandato_format_for_capo_non_inventa_una_policy(monkeypatch, tmp_path, negozio):
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "non_esiste.json"))
    out = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert se.format_for_capo(out) == ""


# ---------------------------------------------------------------- letto a OGNI chiamata

def test_a_poi_b_il_secondo_sizing_porta_i_tetti_di_b(monkeypatch, tmp_path, negozio):
    """Ordine PM 05/09: «se domani li cambio, il consigliere deve seguire il nuovo mandato».
    Nessuna cache di modulo, nessuna costante a import-time."""
    pa = _scrivi_mandato(tmp_path, base_single_pct=6, cap_single_pct=8)
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", pa)
    a = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert a["summary"]["params"]["single_cap_pct"] == 8
    assert _riga(a, "ACME.MI")["max_position_pct"] <= 8

    # stesso processo, stesso modulo gia' importato: cambio il FILE
    _scrivi_mandato(tmp_path, base_single_pct=14, cap_single_pct=20)
    b = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert b["summary"]["params"]["single_cap_pct"] == 20, "il secondo sizing porta ancora A"
    assert _riga(b, "ACME.MI")["max_position_pct"] > 8


def test_nessuna_costante_di_sizing_a_import_time():
    """AST sul sorgente: i tetti del PM non possono stare in un assegnamento di modulo,
    altrimenti tornerebbero a essere la dottrina di uno scritta nel codice di tutti."""
    sorgente = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "src", "bellomberg", "portfolio", "sizing_engine.py"),
                       encoding="utf-8").read()
    trovate = _costanti_di_modulo(sorgente)
    assert not (trovate & COSTANTI_VIETATE), (
        "tetti di sizing cablati a import-time in sizing_engine.py: %s"
        % sorted(trovate & COSTANTI_VIETATE))


def test_canarino_del_controllo_ast():
    """Il test qui sopra passerebbe anche se `_costanti_di_modulo` non trovasse mai niente:
    questo lo falsifica su un sorgente che la costante CE L'HA."""
    finto = "CAP_SINGLE = 0.12\ndef f():\n    MIN_ROOM_EUR = 1\n    return MIN_ROOM_EUR\n"
    trovate = _costanti_di_modulo(finto)
    assert "CAP_SINGLE" in trovate, "il controllo AST non vede una costante di modulo"
    assert "MIN_ROOM_EUR" not in trovate, "il controllo AST scambia una locale per una costante"


def _costanti_di_modulo(sorgente):
    nomi = set()
    for nodo in ast.parse(sorgente).body:          # SOLO il livello del modulo
        if isinstance(nodo, ast.Assign):
            for t in nodo.targets:
                if isinstance(t, ast.Name):
                    nomi.add(t.id)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            nomi.add(nodo.target.id)
    return nomi


# ---------------------------------------------------------------- i parametri per chiamata

def test_limits_for_classe_legge_dai_parametri(params):
    assert se._limits_for_classe("veicolo", params) == (params["base_veicolo"], params["cap_veicolo"])
    assert se._limits_for_classe("single", params) == (params["base_single"], params["cap_single"])
    with pytest.raises(ValueError):
        se._limits_for_classe("grande", params)


def test_parametri_espliciti_non_toccano_il_disco(monkeypatch, tmp_path, negozio, params):
    """Chi ha gia' i parametri (la run, la prova A) non deve rileggere il file."""
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(tmp_path / "non_esiste.json"))
    out = se.compute_sizing(BOOK, RISK, negozio=negozio, parametri=params)
    assert not out.get("error")
    assert out["summary"]["params"]["single_cap_pct"] == params["cap_single"] * 100


def test_il_sommario_e_il_capo_dicono_i_numeri_DEL_MANDATO(negozio, params):
    out = se.compute_sizing(BOOK, RISK, negozio=negozio)
    p = out["summary"]["params"]
    assert p["single_cap_pct"] == 8 and p["vehicle_cap_pct"] == 25 and p["sector_cap_pct"] == 25
    testo = se.format_for_capo(out)
    riga = next(l for l in testo.split("\n") if l.startswith("POLICY (bande"))
    assert "8%" in riga and "25%" in riga
    assert "12%" not in riga and "33%" not in riga, "il Capo legge ancora i tetti di ieri"


def test_impronta_del_mandato_nel_sommario(negozio):
    out = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert out["summary"]["params"]["impronta"] == mp.impronta(mp.carica())[:8]


def test_il_ripiego_di_classe_cita_il_cap_DEL_MANDATO(negozio, params):
    e = se._classifica("IGNOTO.X", negozio, params)
    assert e.fonte == "ripiego"
    assert "8%" in e.dichiarazione and "12%" not in e.dichiarazione


# ---------------------------------------------------------------- la soglia in % del NAV

def test_la_soglia_minima_viene_dal_mandato_in_pct_del_nav(monkeypatch, tmp_path, negozio):
    """`MIN_ROOM_EUR` non e' piu' 500 EUR fissi: e' `posizione_minima_pct` del NAV, quindi
    segue il patrimonio. Con una soglia altissima nessuna riga ha piu' «spazio»."""
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", _scrivi_mandato(tmp_path, posizione_minima_pct=0.2))
    bassa = se.compute_sizing(BOOK, RISK, negozio=negozio)
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", _scrivi_mandato(tmp_path, posizione_minima_pct=8))
    alta = se.compute_sizing(BOOK, RISK, negozio=negozio)
    assert bassa["summary"]["n_names_with_room"] > alta["summary"]["n_names_with_room"]
    # 8% del NAV = 11.296 EUR: le righe sotto quella soglia non dicono piu' «spazio»
    assert all("spazio" not in r["verdict"]
               for r in alta["positions"] if r["remaining_capacity_eur"] < 11296)


# ---------------------------------------------------------------- non-regressione

def test_coi_valori_di_ieri_i_numeri_NON_si_muovono(negozio):
    """Terza condizione della chat classificazione: un centesimo che si muove e' una
    decisione del PM. Coi valori cablati fino a ieri, il motore deve rendere gli stessi
    identici numeri congelati da HEAD prima di A2."""
    out = se.compute_sizing(BOOK, RISK, negozio=negozio, parametri=PARAMS_IERI)
    for tk, (max_pct, room, trim) in ATTESO_IERI.items():
        r = _riga(out, tk)
        assert r["max_position_pct"] == max_pct, "%s: size max %s invece di %s" % (tk, r["max_position_pct"], max_pct)
        assert r["remaining_capacity_eur"] == room, "%s: spazio %s invece di %s" % (tk, r["remaining_capacity_eur"], room)
        assert r["trim_eur"] == trim, "%s: taglio %s invece di %s" % (tk, r["trim_eur"], trim)
    for k, atteso in SOMMARIO_IERI.items():
        assert out["summary"][k] == atteso, "sommario %s: %s invece di %s" % (k, out["summary"][k], atteso)
