"""Regole riusabili sugli originali: un catalogo non certifica il contenuto."""
import hashlib
from pathlib import Path

import pytest


def profilo(**override):
    p = {"ticker": "ACME", "emittente_id": "CIK:0000001234", "lingua": "en",
         "tipo": "annuale", "perimetro": "consolidato",
         "verifica": {"lingua": r"Risk factors", "tipo": r"Annual report",
                      "perimetro": r"Consolidated financial statements",
                      "emittente": r"Acme Corporation"},
         "sezioni": {"rischi": {"inizio": r"Risk factors", "fine": r"Management discussion"}}}
    p.update(override)
    return p


def documento(tmp_path, *, anno=2025, issuer="0000001234", lang="en", extra="", body=None):
    raw = (f'<html lang="{lang}"><body><ix:header><xbrli:context id="c-1">'
           f'<xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{issuer}</xbrli:identifier>'
           f'</xbrli:entity><xbrli:period><xbrli:startDate>{anno}-01-01</xbrli:startDate>'
           f'<xbrli:endDate>{anno}-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
           f'{extra}</ix:header><h1>Acme Corporation</h1><h2>Annual report</h2>'
           '<p>Consolidated financial statements</p>' + (body if body is not None else
           '<h2>Risk factors</h2><p>Our credit exposure increased from 10 to 12.</p>'
           '<h2>Management discussion</h2><p>End of report.</p>') + '</body></html>')
    path = tmp_path / f"arbitrary-name-{anno}.html"
    path.write_text(raw, encoding="utf-8")
    return path


def verifica(path, p=None, catalogo=None):
    from bellomberg.market_data.filing_verifica import verifica_documento
    return verifica_documento(path, url="https://issuer.example/report", profilo=p or profilo(),
                              catalogo=catalogo if catalogo is not None else
                              {"emittente_id": "CIK:0000001234", "report_date": "2025-12-31", "form": "10-K"})


def test_period_and_identity_are_read_from_same_hashed_bytes(tmp_path):
    path = documento(tmp_path)
    r = verifica(path)
    assert r["stato"] == "ok", r
    d = r["documento"]
    assert d["metadati"]["periodo_inizio"] == "2025-01-01"
    assert d["metadati"]["periodo_fine"] == "2025-12-31"
    assert d["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert d["sezioni"]["rischi"]["stato"] == "ok"
    for prova in d["prove_verifica"].values():
        source = path.read_text(encoding="utf-8") if prova["supporto"] == "markup" else d["estrazione"]["testo"]
        assert source[prova["inizio"]:prova["fine"]] == prova["testo"]
        assert prova["sha256"] == d["sha256"]


@pytest.mark.parametrize("change", ["wrong_identity", "wrong_catalog", "wrong_language", "wrong_period",
                                    "missing_scope", "amendment", "future_period"])
def test_unproven_or_conflicting_metadata_never_reaches_diff(tmp_path, change):
    path = documento(tmp_path, issuer="9999" if change == "wrong_identity" else "0000001234",
                     lang="it" if change == "wrong_language" else "en")
    c = {"emittente_id": "CIK:0000001234", "report_date": "2025-12-31", "form": "10-K"}
    if change == "wrong_catalog":
        c["emittente_id"] = "CIK:9999"
    if change == "wrong_period":
        c["report_date"] = "2024-12-31"
    if change == "future_period":
        c["report_date"] = "2099-12-31"
    if change == "amendment":
        c["form"] = "10-K/A"
    if change == "missing_scope":
        path.write_text(path.read_text().replace("Consolidated financial statements", "Separate statements"))
    r = verifica(path, catalogo=c)
    assert r["stato"] == "non_verificato" and r["motivi"]
    assert "documento" not in r


def test_ytd_context_is_not_misread_as_quarter_and_ambiguous_period_rejected(tmp_path):
    path = documento(tmp_path)
    raw = path.read_text().replace("2025-12-31", "2025-09-30")
    path.write_text(raw)
    p = profilo(tipo="trimestrale")
    p["verifica"]["tipo"] = "Annual report"  # regex non sufficiente: conta la durata nei byte
    assert verifica(path, p, {"report_date": "2025-09-30", "form": "10-Q"})["stato"] == "non_verificato"
    p["tipo"] = "nove_mesi"
    assert verifica(path, p, {"report_date": "2025-09-30", "form": "10-Q"})["stato"] == "ok"


def test_ambiguous_starts_remain_missing_not_first_match(tmp_path):
    path = documento(tmp_path, body='<h2>Risk factors</h2><p>Index</p><h2>Management discussion</h2>'
                     '<h2>Risk factors</h2><p>Real risks</p><h2>Management discussion</h2>')
    r = verifica(path)
    assert r["stato"] == "ok"
    assert r["documento"]["sezioni"]["rischi"]["stato"] == "non_disponibile"


def test_nearest_following_boundary_does_not_include_later_separate_accounts(tmp_path):
    path = documento(tmp_path, body='<h2>Risk factors</h2><p>Consolidated risks.</p>'
                     '<h2>Management discussion</h2><p>Separate financial statements.</p>'
                     '<h2>Management discussion</h2><p>Separate risks.</p>')
    r = verifica(path)
    d = r["documento"]
    section = d["sezioni"]["rischi"]
    assert section["stato"] == "ok"
    assert "Separate" not in d["estrazione"]["testo"][section["inizio"]:section["fine"]]


def _duration_report(tmp_path, months='six', ending='June 30, 2025', kind='semestrale'):
    path = tmp_path / 'duration-report.html'
    path.write_text('<html lang="en"><body><h1>Acme Corporation</h1>'
                    '<h2>Interim financial statements</h2><p>Consolidated financial statements</p>'
                    f'<p>For the {months}-month period ended {ending}</p></body></html>', encoding='utf-8')
    profile = profilo(tipo=kind)
    profile['verifica'].update(lingua='financial statements', tipo='Interim financial statements',
        periodo=r'For the (?P<mesi>[a-z]+|\d+)-month period ended (?P<fine>[A-Za-z]+ \d{1,2}, \d{4})')
    return path, profile


@pytest.mark.parametrize('months,ending,kind,start,end', [
    ('six', 'June 30, 2025', 'semestrale', '2025-01-01', '2025-06-30'),
    ('3', 'February 29, 2024', 'trimestrale', '2023-12-01', '2024-02-29'),
    ('nine', 'September 30, 2025', 'nove_mesi', '2025-01-01', '2025-09-30'),
    ('twelve', 'November 30, 2025', 'annuale', '2024-12-01', '2025-11-30')])
def test_explicit_calendar_month_duration_derives_start_with_byte_proof(tmp_path, months, ending, kind, start, end):
    path, profile = _duration_report(tmp_path, months, ending, kind)
    result = verifica(path, profile, {'form': '6-K', 'report_date': end})
    assert result['stato'] == 'ok', result
    document = result['documento']
    assert document['metadati']['periodo_inizio'] == start
    assert document['metadati']['periodo_fine'] == end
    proof = document['prove_verifica']['periodo']
    assert document['estrazione']['testo'][proof['inizio']:proof['fine']] == proof['testo']
    assert proof['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert proof['calcolo']['regola'] == 'mesi_calendario_con_fine_mese'
    assert proof['calcolo']['periodo_inizio'] == start
    assert proof['calcolo']['periodo_fine'] == end
    from bellomberg.storage.filing_store import _validate_profile
    _validate_profile('ACME', profile, 168)


@pytest.mark.parametrize('problem', ['not_month_end', 'wrong_duration', 'unknown_duration',
                                   'catalog_conflict', 'ambiguous_period', 'mixed_groups'])
def test_calendar_duration_does_not_guess_or_override_conflicting_evidence(tmp_path, problem):
    months, ending = 'six', 'June 30, 2025'
    if problem == 'not_month_end': ending = 'June 29, 2025'
    elif problem == 'wrong_duration': months = 'three'
    elif problem == 'unknown_duration': months = 'several'
    path, profile = _duration_report(tmp_path, months, ending)
    catalog = {'form': '6-K', 'report_date': '2025-06-29' if problem == 'not_month_end' else '2025-06-30'}
    if problem == 'catalog_conflict': catalog['report_date'] = '2024-06-30'
    elif problem == 'ambiguous_period':
        path.write_text(path.read_text() + '<p>For the six-month period ended December 31, 2025</p>')
        catalog.pop('report_date')
    elif problem == 'mixed_groups':
        profile['verifica']['periodo'] += r'(?P<inizio>)'
    result = verifica(path, profile, catalog)
    assert result['stato'] == 'non_verificato' and result['motivi']
    assert 'documento' not in result


def test_explicit_date_profiles_keep_supporting_additional_named_regex_groups(tmp_path):
    path, profile = _duration_report(tmp_path)
    path.write_text(path.read_text() + '<p>Reporting period 2025-01-01 to 2025-06-30</p>')
    profile['verifica']['periodo'] = (r'Reporting (?P<label>period) (?P<inizio>\d{4}-\d{2}-\d{2})'
                                      r' to (?P<fine>\d{4}-\d{2}-\d{2})')
    result = verifica(path, profile, {'form': '6-K', 'report_date': '2025-06-30'})
    assert result['stato'] == 'ok', result
    assert 'calcolo' not in result['documento']['prove_verifica']['periodo']


def test_leis_and_italian_periods_work_without_sec_labels(tmp_path):
    path = documento(tmp_path, issuer="LEITEST123", lang="it")
    raw = path.read_text().replace("http://www.sec.gov/CIK", "http://standards.iso.org/iso/17442")
    raw = raw.replace("Annual report", "Relazione annuale").replace("Risk factors", "Rischi")
    raw = raw.replace("Consolidated financial statements", "Bilancio consolidato")
    raw = raw.replace("Management discussion", "Andamento della gestione")
    path.write_text(raw)
    p = profilo(emittente_id="LEI:LEITEST123", lingua="it")
    p["verifica"] = {"lingua": "Rischi", "tipo": "Relazione annuale", "perimetro": "Bilancio consolidato"}
    p["sezioni"] = {"rischi": {"inizio": "Rischi", "fine": "Andamento della gestione"}}
    r = verifica(path, p, {"period_end": "2025-12-31", "language": "it", "emittente_id": "LEI:LEITEST123"})
    assert r["stato"] == "ok", r


def test_pdf_profile_finds_period_and_headings_after_page_shift(tmp_path):
    from reportlab.pdfgen import canvas
    from bellomberg.market_data.filing_verifica import verifica_documento
    p = profilo(emittente_id="EMITTENTE:Acme Corporation")
    p["verifica"]["periodo"] = r"Reporting period (?P<inizio>\d{4}-\d{2}-\d{2}) to (?P<fine>\d{4}-\d{2}-\d{2})"
    for year, blanks in ((2024, 0), (2025, 2)):
        path = tmp_path / f"{year}.pdf"
        c = canvas.Canvas(str(path))
        for _ in range(blanks):
            c.showPage()
        for y, line in enumerate(("Acme Corporation", "Annual report", "Consolidated financial statements",
                                 f"Reporting period {year}-01-01 to {year}-12-31", "Risk factors",
                                 "Credit exposure increased.", "Management discussion")):
            c.drawString(40, 750-y*24, line)
        c.save()
        r = verifica_documento(path, url="https://issuer.example/report", profilo=p)
        assert r["stato"] == "ok", r
        d = r["documento"]
        assert d["metadati"]["periodo_fine"] == f"{year}-12-31"
        assert d["sezioni"]["rischi"]["stato"] == "ok"


def test_ir_year_in_filename_and_end_date_alone_do_not_invent_start(tmp_path):
    path = tmp_path / "2025-report.html"
    path.write_text('<html><p>Acme Corporation Annual report Consolidated financial statements '
                    'for the year ended 31 December 2025. Risk factors</p></html>')
    r = verifica(path, profilo(emittente_id="EMITTENTE:Acme Corporation"), {})
    assert r["stato"] == "non_verificato"
    assert any("period" in m.lower() for m in r["motivi"])


def test_profile_does_not_accept_fixed_page_rules(tmp_path):
    p = profilo()
    p["sezioni"] = {"rischi": {"da_pagina": 44, "a_pagina": 44}}
    r = verifica(documento(tmp_path), p)
    assert r["stato"] == "non_verificato"


def test_six_k_with_no_periodic_report_evidence_is_not_a_report(tmp_path):
    path = tmp_path / "report.html"
    path.write_text('<html><p>Acme Corporation Risk factors. Dividend declaration.</p></html>')
    assert verifica(path, catalogo={"form": "6-K", "report_date": "2025-12-31"})["stato"] == "non_verificato"


def test_missing_file_returns_declared_failure(tmp_path):
    assert verifica(tmp_path / "missing.pdf")["stato"] == "non_verificato"


def test_prior_year_context_inside_new_report_does_not_make_it_old_report(tmp_path):
    path = documento(tmp_path)
    raw = path.read_text()
    context = raw[raw.index('<xbrli:context'):raw.index('</xbrli:context>')+len('</xbrli:context>')]
    path.write_text(raw.replace('</ix:header>', context.replace('2025-', '2024-')+'</ix:header>'))
    r = verifica(path, catalogo={"report_date": "2024-12-31", "form": "10-K"})
    assert r["stato"] == "non_verificato"


def test_two_eligible_duration_contexts_are_not_chosen_arbitrarily(tmp_path):
    path = documento(tmp_path)
    raw = path.read_text()
    context = raw[raw.index('<xbrli:context'):raw.index('</xbrli:context>')+len('</xbrli:context>')]
    path.write_text(raw.replace('</ix:header>', context.replace('2025-01-01', '2025-01-05')+'</ix:header>'))
    assert verifica(path)["stato"] == "non_verificato"


def test_race_between_metadata_and_section_read_refuses_mismatched_snapshot(tmp_path, monkeypatch):
    from bellomberg.market_data import filing_verifica as v
    path = documento(tmp_path)
    orig = v.prepara_documento
    def change(*args, **kwargs):
        path.write_text(path.read_text().replace('10 to 12', '10 to 99'))
        return orig(*args, **kwargs)
    monkeypatch.setattr(v, 'prepara_documento', change)
    r = verifica(path)
    assert r["stato"] == "non_verificato"
    assert 'hash' in str(r['motivi'])


def test_text_comparative_cannot_override_current_reporting_period(tmp_path):
    p = profilo(emittente_id='EMITTENTE:Acme Corporation')
    p['verifica']['periodo'] = r'Reporting period (?P<inizio>\d{4}-\d{2}-\d{2}) to (?P<fine>\d{4}-\d{2}-\d{2})'
    path = tmp_path/'report.html'
    path.write_text('<html><p>Acme Corporation Annual report Consolidated financial statements</p>'
                    '<p>Reporting period 2025-01-01 to 2025-12-31</p>'
                    '<h2>Risk factors</h2><p>Risk increased.</p><h2>Management discussion</h2>'
                    '<p>Comparatives: Reporting period 2024-01-01 to 2024-12-31</p></html>')
    r = verifica(path, p, {'report_date':'2024-12-31'})
    assert r['stato'] == 'non_verificato'


@pytest.mark.parametrize('attributes', ['data-lang="en" lang="it"', 'xml:lang="en" lang="it"'])
def test_exact_html_language_attributes_must_all_agree(tmp_path, attributes):
    path = documento(tmp_path)
    path.write_text(path.read_text().replace('lang="en"', attributes, 1))
    assert verifica(path)['stato'] == 'non_verificato'


@pytest.mark.parametrize('wrapper', ['<!--{context}-->', '<script type="text/template">{context}</script>',
                                     '<template><template></template>{context}</template>'])
def test_comments_and_scripts_cannot_certify_xbrl_identity(tmp_path, wrapper):
    path = documento(tmp_path, issuer='9999')
    raw = path.read_text()
    context = raw[raw.index('<xbrli:context'):raw.index('</xbrli:context>')+len('</xbrli:context>')]
    fake = context.replace('9999', '0000001234')
    path.write_text(raw.replace('</ix:header>', wrapper.format(context=fake)+'</ix:header>'))
    assert verifica(path)['stato'] == 'non_verificato'


def test_amendment_declared_in_document_overrides_unamended_catalog(tmp_path):
    path = documento(tmp_path, extra='<ix:nonNumeric name="dei:DocumentType" contextRef="c-1">10-K/A</ix:nonNumeric>')
    assert verifica(path)['stato'] == 'non_verificato'


def test_hidden_template_cannot_certify_text_identity_and_period(tmp_path):
    p = profilo(emittente_id='EMITTENTE:Acme Corporation')
    p['verifica']['periodo'] = r'Reporting period (?P<inizio>\d{4}-\d{2}-\d{2}) to (?P<fine>\d{4}-\d{2}-\d{2})'
    path = tmp_path/'report.html'
    path.write_text('<html><p>Another Corporation Annual report Consolidated financial statements</p>'
                    '<template><p>Acme Corporation</p><p>Reporting period 2025-01-01 to 2025-12-31</p></template>'
                    '<h2>Risk factors</h2><p>Exposure.</p><h2>Management discussion</h2></html>')
    assert verifica(path, p, {})['stato'] == 'non_verificato'


def test_subsequent_one_month_context_does_not_replace_annual_reporting_period(tmp_path):
    path = documento(tmp_path)
    raw = path.read_text()
    context = raw[raw.index('<xbrli:context'):raw.index('</xbrli:context>')+len('</xbrli:context>')]
    later = context.replace('2025-01-01','2026-01-01').replace('2025-12-31','2026-01-31')
    path.write_text(raw.replace('</ix:header>',later+'</ix:header>'))
    r = verifica(path)
    assert r['stato'] == 'ok', r
    assert r['documento']['metadati']['periodo_fine'] == '2025-12-31'
