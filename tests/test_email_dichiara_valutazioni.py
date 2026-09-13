# -*- coding: utf-8 -*-
"""L'email settimanale dichiara le valutazioni chieste e i modelli NON allegati.

Audit run 10/09 (memo #54): il PM ha chiesto il fair value di un titolo, il desk ha
chiamato get_valuation, il motore ha risposto FV n.d. (dati incompleti) e l'email e'
partita con due PDF e un testo fisso che prometteva "DCF Excel models for
GREEN-validated candidates" e "Capo on Opus 4.8". Chi legge l'email non poteva sapere
se il modello mancava per un guasto o per un rifiuto dichiarato.

Cosa deve essere vero:
  - se nessun Excel e' allegato, l'email lo dice;
  - ogni valutazione tentata dal comitato compare con esito (FV o n.d. + motivo);
  - il piede non nomina un modello cablato nel codice.

Nessuna rete: SMTP finto. Simboli e numeri INVENTATI.
"""
import bellomberg.reporting.email_sender as es


class _SMTP:
    inviati = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def send_message(self, msg):
        _SMTP.inviati.append(msg)


def _html(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("parte html assente")


RISULTATI = {
    "ALFA.DE": {"ok": False, "fair_value_weighted": None, "error": "FV n.d.; dati incompleti",
                "sanity": {"severity": "BLOCK", "headline": "Dati o riconciliazioni incompleti: FV n.d."},
                "valuation_usability": {"usable": False, "missing_fields": ["diluted_shares", "discount_rate_inputs", "fair_value"]},
                "snapshot_id": "abc123def456"},
    "BETA": {"ok": True, "fair_value_weighted": 42.5, "currency": "USD", "price": 30.0,
             "valuation_usability": {"usable": True, "missing_fields": []}, "snapshot_id": "fedcba"},
}


def test_il_corpo_dichiara_gli_excel_mancanti_e_ogni_esito():
    corpo = es.corpo_valutazioni(RISULTATI, [])
    assert "Nessun modello Excel allegato" in corpo
    assert "ALFA.DE" in corpo and "FV n.d." in corpo and "diluted_shares" in corpo
    assert "BETA" in corpo and "42.5" in corpo


def test_senza_valutazioni_lo_dice():
    corpo = es.corpo_valutazioni({}, [])
    assert "Nessuna valutazione" in corpo and "Nessun modello Excel allegato" in corpo


def test_con_excel_allegati_li_elenca():
    corpo = es.corpo_valutazioni(RISULTATI, ["C:/x/VAL_BETA.xlsx"])
    assert "VAL_BETA.xlsx" in corpo and "Nessun modello Excel allegato" not in corpo


def test_l_email_porta_il_corpo_e_non_promette_modelli_che_non_ci_sono(tmp_path, monkeypatch):
    pdf = tmp_path / "weekly.pdf"
    pdf.write_bytes(b"%PDF-1.4 finto")
    monkeypatch.setattr(es, "EMAIL_FROM", "a@b.c")
    monkeypatch.setattr(es, "EMAIL_PASSWORD", "x")
    monkeypatch.setattr(es, "EMAIL_TO", "d@e.f")
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _SMTP)
    _SMTP.inviati.clear()
    ok = es.invia_email_multi_allegati([str(pdf)], oggetto="prova",
                                       body_extra=es.corpo_valutazioni(RISULTATI, []))
    assert ok is True and len(_SMTP.inviati) == 1
    html = _html(_SMTP.inviati[0])
    assert "Nessun modello Excel allegato" in html
    assert "Opus 4.8" not in html, "modello cablato nel piede dell'email"
    assert "DCF Excel models" not in html, "promessa di modelli senza nessun .xlsx allegato"
