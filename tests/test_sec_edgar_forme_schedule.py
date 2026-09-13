"""Schedule 13D/13G col nome di EDGAR prima e dopo il 18/12/2024.

Dal 18/12/2024 le Schedule 13D/13G si depositano in XML strutturato (EDGAR Release 23.4) e
l'indice le registra col tipo «SCHEDULE 13D» / «SCHEDULE 13G» (verificato su un indice EDGAR
vero del 2025: «Form SCHEDULE 13G/A»). `get_corporate_events_for_portfolio` cercava solo
«SC 13D» / «SC 13G»: una partecipazione oltre il 5% depositata col nome nuovo non entrava nel
pannello e nessuno lo diceva (buco muto, regola PM 14/07).

Qui girano VERI `get_corporate_events_for_portfolio`, `get_recent_filings`, `get_8k_events` e
`get_insider_trades`; e' finta solo la rete (nessuna chiamata vera) e il CIK.
"""
from datetime import datetime

import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload


class _Risposta:
    def __init__(self, payload):
        self.status_code, self._payload = 200, payload

    def json(self):
        return self._payload


FORME = ["SCHEDULE 13D", "SCHEDULE 13G", "SC 13D", "SC 13G"]

# tipo -> (titolo it, titolo en, importanza): scritti qui, non letti da STRUCT_FORMS
ATTESI = {
    "SCHEDULE 13D": ("SYNTH: SCHEDULE 13D - Partecipazione attivista >5%",
                     "SYNTH: SCHEDULE 13D - Activist stake >5%", 5),
    "SCHEDULE 13G": ("SYNTH: SCHEDULE 13G - Partecipazione passiva >5%",
                     "SYNTH: SCHEDULE 13G - Passive stake >5%", 3),
    "SC 13D": ("SYNTH: SC 13D - Partecipazione attivista >5%",
               "SYNTH: SC 13D - Activist stake >5%", 5),
    "SC 13G": ("SYNTH: SC 13G - Partecipazione passiva >5%",
               "SYNTH: SC 13G - Passive stake >5%", 3),
}


@pytest.fixture
def sec_con_schedule(monkeypatch):
    from bellomberg.market_data import sec_edgar as sec
    oggi = datetime.now().strftime("%Y-%m-%d")
    chiamate = []

    def get(url, headers=None, timeout=None, **kw):
        chiamate.append(url)
        assert url == "https://data.sec.gov/submissions/CIK0000000042.json", url
        return _Risposta({"name": "Synthetic Corp", "filings": {"recent": {
            "form": list(FORME),
            "filingDate": [oggi] * len(FORME),
            "accessionNumber": ["0000000042-26-00000%d" % i for i in range(len(FORME))],
            "primaryDocument": ["doc%d.xml" % i for i in range(len(FORME))],
            "primaryDocDescription": [""] * len(FORME)}}})

    monkeypatch.setattr(sec, "lookup_cik", lambda *a, **k: "0000000042")
    monkeypatch.setattr(sec.requests, "get", get)
    monkeypatch.setattr(sec.time, "sleep", lambda *_: None)
    return sec, chiamate


def test_schedule_13d_13g_col_nome_nuovo_e_col_vecchio_arrivano_al_pannello(sec_con_schedule):
    sec, chiamate = sec_con_schedule
    motivi = []
    with language_context("it"):
        eventi = sec.get_corporate_events_for_portfolio(["SYNTH"], motivo=motivi)
    inglese = render_payload(eventi, language="en")
    visti = {e["type"]: (e["title"], en["title"], e["importance"]) for e, en in zip(eventi, inglese)}
    assert visti == ATTESI
    assert len(eventi) == len(ATTESI), eventi
    assert motivi == []
    assert chiamate, "senza chiamate alla rete finta la prova non misura niente"
