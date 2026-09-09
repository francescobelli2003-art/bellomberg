"""B10 (02/09, pubblicazione, Fable 5): Apache-2.0 + NOTICE (decisione PM 02/09).

LICENSE e' il testo UFFICIALE della Apache Software Foundation, senza modifiche (il
copyright va in NOTICE, come prescrive l'appendice della licenza); NOTICE porta il
titolare e le attribuzioni dovute (Lightweight Charts di TradingView, i dati di
Damodaran); `app/package.json` dichiara la stessa licenza; Impostazioni cita
TradingView col link che la licenza di Lightweight Charts richiede.
"""
import hashlib
import json
import os
import re

# `LICENZA_REPO` serve a misurare il RED su una copia manomessa (review 02/09).
REPO = os.environ.get("LICENZA_REPO") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

# sha256 di https://www.apache.org/licenses/LICENSE-2.0.txt (11.358 byte, LF),
# calcolata sui byte con i CRLF normalizzati: con `core.autocrlf=true` un clone
# Windows riceve il file a CRLF e l'impronta grezza cambierebbe (misurato dalla
# review del 02/09: 11.560 byte), quella normalizzata no.
SHA256_APACHE_2 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def _testo(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def _byte(rel):
    with open(os.path.join(REPO, rel), "rb") as fh:
        return fh.read()


def test_license_e_il_testo_ufficiale_apache_2():
    t = _testo("LICENSE")
    assert t.lstrip().startswith("Apache License"), "LICENSE non comincia col titolo ufficiale"
    assert "Licensed under the Apache License, Version 2.0" in t, "manca l'appendice"
    assert "Copyright 2026" not in t, "il testo ufficiale non si tocca: il copyright sta in NOTICE"
    impronta = hashlib.sha256(_byte("LICENSE").replace(b"\r\n", b"\n")).hexdigest()
    assert impronta == SHA256_APACHE_2, (
        "LICENSE non e' il testo ufficiale byte per byte (sha256 %s)" % impronta[:16])


def test_notice_porta_titolare_e_attribuzioni():
    t = _testo("NOTICE")
    assert t.startswith("Bellomberg"), "NOTICE deve aprirsi col nome del prodotto"
    assert "Copyright 2026" in t
    assert "Lightweight Charts" in t and "tradingview" in t.lower()
    assert "https://github.com/tradingview/lightweight-charts" in t
    assert "Damodaran" in t


def test_package_json_licenza_allineata():
    pkg = json.loads(_testo("app/package.json"))
    assert pkg["license"] == "Apache-2.0", pkg["license"]


def test_impostazioni_citano_tradingview_col_link():
    """L'ancora VISIBILE, non un commento: href richiesto dalla licenza di Lightweight
    Charts, apertura esterna (`target`/`rel` come gli altri link dell'app) e il nome
    del creatore nel testo del link (README di LWC: «specifying TradingView as the
    product creator»)."""
    t = _testo("app/src/components/SettingsPanel.tsx")
    assert "https://www.tradingview.com/" in t, "manca il link richiesto dalla licenza di Lightweight Charts"
    ancora = re.search(r'<a\b[^>]*href="https://www\.tradingview\.com/"[^>]*>([^<]*)</a>', t)
    assert ancora, "il link a tradingview.com non e' un'ancora <a> visibile"
    tag = ancora.group(0)
    assert 'target="_blank"' in tag and 'rel="noreferrer"' in tag, tag
    assert "TradingView" in ancora.group(1) and "Lightweight Charts" in ancora.group(1), ancora.group(1)
