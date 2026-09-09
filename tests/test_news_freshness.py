# -*- coding: utf-8 -*-
"""P2 freschezza feed news (12/08): l'ora e l'esito dell'ultimo giro sono un
fatto leggibile, mai un default zitto (pattern 25 / regola 14/07).

Scrittore: `_scrivi_stato_giro(out, path)` — atomico (tmp + os.replace),
scritto a fine giro da auto_pull_feed. Lettore: `stato_ultimo_giro(path, now)`
— puro, `stato` sempre presente: ok | degradato | n.d. | illeggibile.
Niente DB, niente migrazioni: file JSON in data/.
"""
import json
import os
from datetime import datetime

import pytest

from bellomberg.market_data.news_aggregator import _scrivi_stato_giro, stato_ultimo_giro

OUT_OK = {"fetched": 76, "classified": 10, "saved": 10,
          "skipped_duplicates": 66, "providers_blocked": {}, "degraded": False}
OUT_DEG = {"fetched": 12, "classified": 0, "saved": 0,
           "skipped_duplicates": 12,
           "providers_blocked": {"newsapi": "SKIP_BUDGET"}, "degraded": True}


def test_scrittore_forma_e_esito_ok(tmp_path):
    p = str(tmp_path / "stato.json")
    _scrivi_stato_giro(OUT_OK, path=p)
    raw = json.load(open(p, encoding="utf-8"))
    assert raw["esito"] == "ok"
    assert raw["fetched"] == 76 and raw["saved"] == 10
    assert raw["providers_blocked"] == {}
    datetime.fromisoformat(raw["timestamp"])  # parsabile o esplode


def test_scrittore_esito_degradato(tmp_path):
    p = str(tmp_path / "stato.json")
    _scrivi_stato_giro(OUT_DEG, path=p)
    raw = json.load(open(p, encoding="utf-8"))
    assert raw["esito"] == "degradato"
    assert raw["providers_blocked"] == {"newsapi": "SKIP_BUDGET"}


def test_scrittore_atomico_niente_tmp_residuo(tmp_path):
    p = str(tmp_path / "stato.json")
    _scrivi_stato_giro(OUT_OK, path=p)
    residui = [f for f in os.listdir(str(tmp_path)) if f != "stato.json"]
    assert residui == [], f"file temporanei residui: {residui}"


def test_scrittore_sovrascrive_il_precedente(tmp_path):
    p = str(tmp_path / "stato.json")
    _scrivi_stato_giro(OUT_DEG, path=p)
    _scrivi_stato_giro(OUT_OK, path=p)
    assert json.load(open(p, encoding="utf-8"))["esito"] == "ok"


def test_lettore_file_assente_dichiara_nd(tmp_path):
    r = stato_ultimo_giro(path=str(tmp_path / "non_esiste.json"))
    assert r["stato"] == "n.d."
    assert "motivo" in r and r["motivo"]


def test_lettore_json_corrotto_dichiara_illeggibile(tmp_path):
    p = str(tmp_path / "stato.json")
    open(p, "w", encoding="utf-8").write("{mezzo json")
    r = stato_ultimo_giro(path=p)
    assert r["stato"] == "illeggibile"
    assert "motivo" in r and r["motivo"]


def test_lettore_esito_sconosciuto_dichiara_illeggibile(tmp_path):
    p = str(tmp_path / "stato.json")
    json.dump({"timestamp": "2026-08-12T10:00:00", "esito": "boh"},
              open(p, "w", encoding="utf-8"))
    r = stato_ultimo_giro(path=p)
    assert r["stato"] == "illeggibile"


def test_lettore_timestamp_rotto_dichiara_illeggibile(tmp_path):
    p = str(tmp_path / "stato.json")
    json.dump({"timestamp": "ieri sera", "esito": "ok"},
              open(p, "w", encoding="utf-8"))
    r = stato_ultimo_giro(path=p)
    assert r["stato"] == "illeggibile"


def test_lettore_eta_deterministica_con_now_iniettato(tmp_path):
    p = str(tmp_path / "stato.json")
    _scrivi_stato_giro(OUT_OK, path=p)
    raw = json.load(open(p, encoding="utf-8"))
    raw["timestamp"] = "2026-08-12T10:00:00"
    json.dump(raw, open(p, "w", encoding="utf-8"))
    r = stato_ultimo_giro(path=p, now=datetime(2026, 8, 12, 10, 45, 30))
    assert r["stato"] == "ok"
    assert r["age_minutes"] == 45.5
    assert r["fetched"] == 76 and r["saved"] == 10
    assert r["timestamp"] == "2026-08-12T10:00:00"
