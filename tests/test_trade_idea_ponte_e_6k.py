"""APERTO-TI (decisione PM 06/10 «piu' aperti»): ponte Filing nella Trade Idea, non verificati
ammessi con etichetta, 6-K delle societa' estere riconosciuti, varianti non primarie conservate.

Tutto SINTETICO (ticker ZZTEST/SYNTH-EXT, CIK inventati, numeri finti), in tmp_path; nessuna rete,
nessuna AI. Le attese sono scritte a mano qui (oracolo fuori dal codice sotto test).
"""
from copy import deepcopy
from functools import partial
from hashlib import sha256
import json
import re
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import sqlite3

import pytest

from bellomberg.agents import ponte_filing_dossier as ponte
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE, research_context, seal_research_thesis
from bellomberg.market_data import filing_pipeline, sec_edgar
from bellomberg.storage.filing_store import FilingStore, ensure_schema

CIK = '0009990077'
CIK_ALTRO = '0009990078'
BASE_URL = 'https://www.sec.gov/Archives/edgar/data/9990077/'
AS_OF_CONTEXT = '2030-01-15T09:00:00+00:00'
BASE = {"lingua": "en", "perimetro": "consolidato", "sezioni": {}, "sezioni_intero": True,
        "verifica": {"lingua": "English", "tipo": "report", "perimetro": "consolidated"}}


def _doc(root, ticker, name, body):
    folder = root / ticker / 'documents'
    folder.mkdir(parents=True, exist_ok=True)
    raw = body.encode('utf-8')
    (folder / name).write_bytes(raw)
    return str(folder / name), sha256(raw).hexdigest()


def _cand(path, digest, url, *, periodo, tipo=None, filed=None, form='6-K', stato='verificato', motivi=(), **extra):
    row = {'fonte': 'SEC EDGAR', 'path': path, 'sha256': digest, 'url': url, 'stato': stato,
           'motivi': list(motivi), 'report_date': periodo, 'filed_date': filed, 'form': form}
    if stato == 'verificato':
        row['metadati'] = {'emittente_id': 'CIK:' + CIK, 'lingua': 'en', 'perimetro': 'consolidato',
                           'periodo_fine': periodo, 'periodo_inizio': periodo[:4] + '-01-01', 'tipo': tipo}
    row.update(extra)
    return row


def _run(store, ticker, status, result, reason=None):
    run = store.start_run(ticker, 'manual', language='it')
    store.claim_execution(run['id'])
    return store.finish_run(run['id'], status=status, reason=reason, result=result)


def _archivio(tmp_path, ticker='ZZTEST', *, extra_nv=0):
    """Archivio Filing sintetico di un emittente estero: 6-K trimestrali + 20-F nella variante non primaria."""
    db = tmp_path / 'filing.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    root = tmp_path / 'filing_archive'
    store.set_profile(ticker, {**BASE, 'tipo': 'trimestrale', 'emittente_id': 'CIK:' + CIK, 'ticker': ticker})
    q1 = _doc(root, ticker, 'q1.htm', '<html><body>FORM 6-K Zztest Holdings first quarter ended March 31, 2029. '
              'Revenue 111.11 million.</body></html>')
    q2 = _doc(root, ticker, 'q2.htm', '<html><body>FORM 6-K Zztest Holdings period ended June 30, 2029. '
              'Revenue 222.22 million.</body></html>')
    annuale = _doc(root, ticker, 'f20.htm', '<html><body>FORM 20-F Zztest Holdings year ended December 31, 2028. '
                   'Revenue 876.54 million.</body></html>')
    vecchio = _doc(root, ticker, 'q3old.htm', '<html><body>FORM 6-K Zztest Holdings 2028 interim.</body></html>')
    altro = _doc(root, ticker, 'partner.htm', '<html><body>FORM 6-K Partner Corp results.</body></html>')
    tedesco = _doc(root, ticker, 'de.htm', '<html><body>Zwischenbericht Zztest.</body></html>')
    url = lambda n, name: BASE_URL + '00099900772900%04d/' % n + name
    candidati = [
        _cand(*q1, url(1, 'q1.htm'), periodo='2029-03-31', tipo='trimestrale', filed='2029-05-02'),
        # 6-K del trimestre piu' recente: periodo non certo (regola standard), byte conservati
        _cand(*q2, url(2, 'q2.htm'), periodo='2029-06-30', filed='2029-08-14', stato='periodo_da_confermare',
              motivi=["periodo da confermare: ValueError: periodo testuale assente o ambiguo"],
              regola_verifica='standard_6k_fpi (regola del codice, non del profilo)',
              periodo_stato='da_confermare', periodi_visti=['2029-06-30']),
        # non verificato ma PIU' VECCHIO dell'ultimo verificato: dichiarato come non selezionato
        _cand(*vecchio, url(3, 'q3old.htm'), periodo='2028-09-30', filed='2028-11-10', stato='non_verificato',
              motivi=['ValueError: tipo: prova testuale assente']),
        # identita' diversa (piu' recente di tutto): MAI ammesso
        _cand(*altro, url(4, 'partner.htm'), periodo='2029-09-30', filed='2029-10-20', stato='non_applicabile',
              motivi=['ValueError: registrante del documento (Partner Corp) diverso dal titolo del portafoglio']),
        # altra lingua dello stesso deposito: fuori
        _cand(*tedesco, url(5, 'de.htm'), periodo='2029-07-31', filed='2029-08-15', stato='non_applicabile',
              motivi=['lingua catalogo de diversa dal profilo en: documento non scaricato, nessuna traduzione'])]
    for i in range(extra_nv):
        extra = _doc(root, ticker, 'nv%d.htm' % i, '<html><body>FORM 6-K Zztest Holdings note %d.</body></html>' % i)
        candidati.append(_cand(*extra, url(10 + i, 'nv%d.htm' % i), periodo='2029-07-%02d' % (10 + i),
                               filed='2029-07-%02d' % (11 + i), stato='non_verificato',
                               motivi=['ValueError: periodo testuale assente o ambiguo']))
    _run(store, ticker, 'ok', {'stato': 'ok', 'motivi': [], 'variante': 'trimestrale', 'candidati': candidati,
        'candidati_varianti': [_cand(*annuale, url(9, 'f20.htm'), periodo='2028-12-31', tipo='annuale',
                                     filed='2029-04-10', form='20-F', variante='annuale')]})
    return SimpleNamespace(store=store, archive_root=root, db=db)


class _Store:
    def __init__(self):
        self.context = {'research_started_at': AS_OF_CONTEXT}
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def complete(self, key, payload, bb=None):
        self.rows.setdefault(key, json.loads(json.dumps(payload, sort_keys=True)))


class _Board:
    def __init__(self, tickers=('ZZTEST',)):
        self.data, self._lock = {}, RLock()
        self.analysis_mode, self.run_scope = RESEARCH_ANALYSIS_MODE, 'weekly'
        self.research_tickers = list(tickers)
        self.tool_receipts = []
        self.company_source_session_if_known = lambda ticker: None

    def read(self, desk, round_n):
        return 'Report sintetico ' + desk


REGISTRO = {'origine': 'sintetico', 'veicoli': {'ZZTEST': {'tipo': 'operating'}}}


def _ammetti(archivio):
    board = _Board()
    record = ponte.ammetti_archivio_filing(board, _Store(), servizio_factory=lambda: archivio,
                                           registro_factory=lambda: REGISTRO)
    return board, record


# ---------------------------------------------------------------------------------------------
# Cura 2: i non verificati entrano con etichetta; identita' e altra lingua restano fuori
# ---------------------------------------------------------------------------------------------

def test_non_verificato_piu_recente_entra_con_etichetta_e_il_20f_della_variante_resta(tmp_path):
    _, record = _ammetti(_archivio(tmp_path))
    voce = record['tickers']['ZZTEST']
    assert voce['esito'] == 'ammesso'
    assert [(d['periodo'], d['form'], d.get('verifica', 'verificato')) for d in voce['documenti']] == [
        ('2029-06-30', '6-K', 'non_verificato'), ('2029-03-31', '6-K', 'verificato'),
        ('2028-12-31', '20-F', 'verificato')]
    nv = voce['documenti'][0]
    assert nv['etichetta_verifica'].startswith('non verificato: periodo da confermare')
    assert nv['periodo_stato'] == 'da_confermare' and nv['stato_archivio'] == 'periodo_da_confermare'
    assert nv['regola_verifica'].startswith('standard_6k_fpi')
    assert voce['documenti'][2]['variante'] == 'annuale'
    assert voce['documenti_non_verificati'] == 1
    esclusi = {e['url'].rsplit('/', 1)[1]: e['motivo'] for e in voce['esclusi']}
    assert set(esclusi) == {'partner.htm', 'de.htm'}
    assert "identita'" in esclusi['partner.htm'] and "altra societa'" in esclusi['partner.htm']
    assert esclusi['de.htm'].startswith('altra lingua')
    altri = {e['url'].rsplit('/', 1)[1]: e['motivo'] for e in voce['non_selezionati']}
    assert "non piu' recente dell'ultimo verificato" in altri['q3old.htm']
    assert 'regola_non_verificati' in record and 'mai fonte primaria verificata' in record['regola_non_verificati']


def test_tetto_dei_non_verificati_dichiarato(tmp_path):
    _, record = _ammetti(_archivio(tmp_path, extra_nv=3))
    voce = record['tickers']['ZZTEST']
    nv = [d for d in voce['documenti'] if d.get('verifica') == 'non_verificato']
    assert len(nv) == ponte.MAX_NON_VERIFICATI == 2
    assert sum('oltre il tetto di 2 non verificati' in e['motivo'] for e in voce['non_selezionati']) == 2


def test_dossier_indice_e_lettura_portano_l_etichetta(tmp_path):
    from bellomberg.valuation.company_dossier import read_company_dossier
    board, record = _ammetti(_archivio(tmp_path))
    sealed = seal_research_thesis(board, desks=('macro', 'fundamentals'))
    dossier = sealed['dossiers']['ZZTEST']
    nv = dossier['documents'][0]
    fb = nv['metadata']['filing_bridge']
    assert fb['verifica'] == 'non_verificato' and fb['fonte_primaria_verificata'] is False
    issues = [i for i in dossier['issues'] if i['code'] == 'filing_archive_documento_non_verificato']
    assert [i['document_id'] for i in issues] == [nv['id']]
    assert 'NON conta come fonte primaria verificata' in issues[0]['reason']
    assert 'periodo da confermare' in issues[0]['reason']
    # Capo/Red Team/R2: indice compatto con l'etichetta, mai testo
    indice = research_context(board)['dossiers']['ZZTEST']
    voce = indice['documenti'][0]
    assert voce['verificato'] is False and voce['fonte_primaria_verificata'] is False
    assert voce['etichetta'].startswith('non verificato:') and voce['periodo_stato'] == 'da_confermare'
    assert all('verificato' not in d for d in indice['documenti'][1:])
    # desk: la pagina letta dice che non e' verificato
    letto = read_company_dossier(board, {'ticker': 'ZZTEST', 'section': 'document', 'document_id': nv['id']},
                                 max_chars=12000)
    assert letto['ok'] is True and 'Revenue 222.22' in letto['text']
    assert letto['fonte_primaria_verificata'] is False and letto['etichetta'].startswith('non verificato:')
    verificato = read_company_dossier(board, {'ticker': 'ZZTEST', 'section': 'document',
                                              'document_id': dossier['documents'][1]['id']}, max_chars=12000)
    assert verificato['ok'] is True and 'fonte_primaria_verificata' not in verificato


def test_ricevuta_senza_non_verificati_identica_nei_documenti_verificati(tmp_path):
    """Un documento verificato non riceve chiavi nuove (ricevute e indici di prima invariati)."""
    _, record = _ammetti(_archivio(tmp_path))
    verificato = record['tickers']['ZZTEST']['documenti'][1]
    assert not {'verifica', 'etichetta_verifica', 'fonte_primaria_verificata'} & set(verificato)


# ---------------------------------------------------------------------------------------------
# Pipeline: varianti non primarie conservate
# ---------------------------------------------------------------------------------------------

def test_esegui_profilo_conserva_i_candidati_delle_varianti_non_primarie(monkeypatch, tmp_path):
    def singolo(profilo, **_kw):
        tipo = profilo['tipo']
        fine = '2029-06-30' if tipo == 'trimestrale' else '2028-12-31'
        dopo = {'metadati': {'periodo_fine': fine}}
        return {'ticker': 'ZZTEST', 'stato': 'ok', 'motivi': [], 'fonti': [],
                'candidati': [{'url': 'https://www.sec.gov/x/' + tipo, 'stato': 'verificato', 'form': tipo}],
                'coppia': {'dopo': dopo, 'prima': dopo}, 'confronto_corrente': {'cambiamenti': []}}
    monkeypatch.setattr(filing_pipeline, '_esegui_singolo', singolo)
    profilo = {'ticker': 'ZZTEST', 'emittente_id': 'CIK:' + CIK, 'tipo': 'annuale', 'verifica': {},
               'varianti': [{'tipo': 'annuale'}, {'tipo': 'trimestrale'}]}
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path)
    assert out['variante'] == 'trimestrale'
    assert [c['form'] for c in out['candidati']] == ['trimestrale']
    assert out['candidati_varianti'] == [{'url': 'https://www.sec.gov/x/annuale', 'stato': 'verificato',
                                          'form': 'annuale', 'variante': 'annuale'}]


# ---------------------------------------------------------------------------------------------
# Cura 3: 6-K delle societa' estere (FPI)
# ---------------------------------------------------------------------------------------------

def _sei_k(nome, frase, *, registrante=None):
    copertina = (f'<p>FORM 6-K</p><p>For the month of August, 2025</p><p>{registrante or nome}</p>'
                 '<p>(Exact name of registrant as specified in its charter)</p>')
    return (f'<html lang="en"><body>{copertina}<p>{nome} Reports Results</p>'
            f'<p>{nome} today released its consolidated financial results for the {frase}.</p>'
            '<p>Revenue 333.33 million; net income 44.44 million.</p>'
            '<p>Forward-looking statements</p><p>Boilerplate.</p></body></html>').encode()


PROFILO_6K = {'ticker': 'ZZTEST', 'emittente_id': 'CIK:' + CIK, 'cik': CIK, 'nome': 'Zztest Holdings',
              'lingua': 'en', 'tipo': 'trimestrale', 'perimetro': 'consolidato', 'fonti': ['sec'],
              'forme_sec': ['6-K'], 'sezioni': {}, 'sezioni_intero': True, 'periodo_regola': 'piu_recente',
              'stesso_periodo': 'piu_lungo', 'origine_collegamento': 'confermato_utente',
              # le regole SALVATE nei profili automatici prima della cura (com'e' oggi nel DB)
              'verifica': {'lingua': r'\b(?:the|and|of)\b', 'perimetro': 'consolidated',
                           'tipo': r'months\s+ended|half[-\s]year', 'emittente': r'\bzztest\s+holdings\b',
                           'periodo': r'(?P<mesi>three|3)(?:\s+and\s+(?:six|nine))?\s+months\s+ended\s+'
                                      r'(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})'}}


def verifica_6k_standard(*args, **kwargs):
    from bellomberg.market_data.filing_verifica import verifica_6k_standard as funzione
    return funzione(*args, **kwargs)


def _scrivi(tmp_path, nome, contenuto):
    path = tmp_path / nome
    path.write_bytes(contenuto)
    return path


@pytest.mark.parametrize('frase,inizio,fine', [
    ('second quarter ended June 30, 2025', '2025-04-01', '2025-06-30'),
    ('three and six-month periods ended June 30, 2025', '2025-04-01', '2025-06-30'),
    ('quarter ended on March 31, 2025', '2025-01-01', '2025-03-31')])
def test_6k_fpi_trimestre_riconosciuto_con_regola_dichiarata(tmp_path, frase, inizio, fine):
    from bellomberg.market_data.filing_verifica import verifica_documento
    path = _scrivi(tmp_path, 'q.htm', _sei_k('Zztest Holdings Ltd.', frase))
    url = BASE_URL + '000999007729000001/q.htm'
    assert verifica_documento(path, url=url, profilo=PROFILO_6K, catalogo={'form': '6-K'})['stato'] != 'ok'
    esito = verifica_6k_standard(path, url=url, profilo=PROFILO_6K, catalogo={'form': '6-K', 'report_date': '2025-08-14'})
    assert esito['stato'] == 'ok', esito
    meta = esito['documento']['metadati']
    assert (meta['periodo_inizio'], meta['periodo_fine'], meta['tipo']) == (inizio, fine, 'trimestrale')
    assert esito['regola_verifica'].startswith('standard_6k_fpi')


def test_6k_fpi_periodo_senza_durata_resta_da_confermare(tmp_path):
    path = _scrivi(tmp_path, 'p.htm', _sei_k('Zztest Holdings Ltd.', 'period ended June 30, 2025'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000002/p.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'periodo_da_confermare'
    assert esito['periodo_stato'] == 'da_confermare' and esito['periodi_visti'] == ['2025-06-30']
    assert esito['motivi'][0].startswith('periodo da confermare')


def test_6k_di_un_altra_societa_mai_ammesso_anche_col_cik_giusto(tmp_path):
    # allegato che riproduce il comunicato di un partner: CIK del percorso giusto, nome dell'emittente assente
    path = _scrivi(tmp_path, 'x.htm', _sei_k('Partner Corp', 'second quarter ended June 30, 2025',
                                             registrante='Partner Corp'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000003/x.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'non_verificato'
    assert any("identita' non provata" in m for m in esito['motivi'])


@pytest.mark.parametrize("corpo", ["Partner Corp", "Zztest Pagamentos S.A."])
def test_r_fase_m1_6k_del_registrante_col_corpo_di_un_altra_societa_non_verificato(tmp_path, corpo):
    # revisione R-FASE M1: configurazione VERA, copertina del registrante (sotto il suo CIK) e corpo del
    # partner o della controllata. Il nome in copertina non prova che il comunicato sia del titolo.
    path = _scrivi(tmp_path, 'm1.htm', _sei_k(corpo, 'second quarter ended June 30, 2025',
                                              registrante='Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000003/m1.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'non_verificato', esito
    assert any("corpo del 6-K" in m for m in esito['motivi']), esito['motivi']


def test_r_fase_m1_6k_del_registrante_col_suo_comunicato_resta_verificato(tmp_path):
    path = _scrivi(tmp_path, 'ok.htm', _sei_k('Zztest Holdings Ltd.', 'second quarter ended June 30, 2025',
                                              registrante='Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000005/ok.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'ok', esito


def test_6k_identita_dal_cik_del_filing_e_dal_registrante_di_copertina(tmp_path):
    # il corpo usa solo il marchio, la copertina dichiara il registrante: vale il CIK del percorso EDGAR
    profilo = {**PROFILO_6K, 'verifica': {**PROFILO_6K['verifica'], 'emittente': r'\bzztest\s+holdings\s+group\b'}}
    path = _scrivi(tmp_path, 'y.htm', _sei_k('Zztest', 'second quarter ended June 30, 2025',
                                             registrante='Zztest Holdings Ltd.'))
    giusto = verifica_6k_standard(path, url=BASE_URL + '000999007729000004/y.htm', profilo=profilo,
                                  catalogo={'form': '6-K'})
    assert giusto['stato'] == 'ok', giusto
    assert 'CIK del filing EDGAR' in giusto['identita_verifica']
    sbagliato = verifica_6k_standard(path, url='https://www.sec.gov/Archives/edgar/data/9990078/1/y.htm',
                                     profilo=profilo, catalogo={'form': '6-K'})
    assert sbagliato['stato'] == 'non_verificato'


def test_pipeline_6k_fpi_entra_e_il_comunicato_senza_periodo_resta_da_confermare(monkeypatch, tmp_path):
    from tests.test_filing_pipeline import rete
    righe = [{'ticker': 'ZZTEST', 'form': '6-K', 'filed_date': f, 'accession': a, 'url': BASE_URL + a.replace('-', '') + '/' + d,
              'emittente_id': 'CIK:' + CIK, 'issuer': 'Synthetic', 'report_date': '', 'items': [], 'fonte': 'SEC EDGAR'}
             for f, a, d in (('2025-08-14', '0009990077-29-000009', 'q2-results.htm'),
                             ('2025-08-20', '0009990077-29-000010', 'h1-update-results.htm'),
                             ('2025-07-01', '0009990077-29-000008', 'production-results.htm'),
                             ('2024-08-14', '0009990077-28-000009', 'q2-2024-results.htm'))]
    monkeypatch.setattr(sec_edgar, 'get_filing_catalog', lambda *a, **k: {'stato': 'ok', 'motivi': [], 'documenti': righe})
    monkeypatch.setattr(sec_edgar, 'allegati_filing', lambda cik, acc, **k: [
        {'seq': 1, 'descrizione': 'EX-99.1', 'tipo': 'EX-99.1', 'ixbrl': False,
         'url': next(r['url'] for r in righe if r['accession'] == acc)}])
    rete(monkeypatch, {
        righe[0]['url']: _sei_k('Zztest Holdings Ltd.', 'second quarter ended June 30, 2025'),
        righe[1]['url']: _sei_k('Zztest Holdings Ltd.', 'period ended July 31, 2025'),
        righe[2]['url']: b'<html lang="en"><body><p>Zztest Holdings Ltd.</p><p>Second quarter production update.</p>'
                         b'<p>The consolidated output rose.</p></body></html>',
        righe[3]['url']: _sei_k('Zztest Holdings Ltd.', 'second quarter ended June 30, 2024')})
    out = filing_pipeline.esegui_profilo(PROFILO_6K, archivio=tmp_path / 'a', oggi='2025-10-31')
    stati = {c['url'].rsplit('/', 2)[-2][-6:] + '/' + c['url'].rsplit('/', 1)[-1]: c for c in out['candidati']}
    q2 = stati['000009/q2-results.htm']
    # R-FONTI 10/10: le regole automatiche salvate prima del 06/10 si aggiornano a quelle correnti del generatore
    # (dichiarato nei limiti): il Q2 passa col profilo aggiornato, non piu' dal ripiego standard.
    assert q2['stato'] == 'verificato' and 'regola_verifica' not in q2
    assert q2['metadati']['periodo_fine'] == '2025-06-30' and q2['motivi'] == []
    assert any('regole tipo e periodo del profilo automatico' in x for x in out['copertura']['limiti'])
    h1 = stati['000010/h1-update-results.htm']
    assert h1['stato'] == 'periodo_da_confermare' and h1['periodo_stato'] == 'da_confermare'
    assert h1['path'] and h1['sha256']                       # byte conservati per il ponte
    assert stati['000008/production-results.htm']['stato'] == 'non_applicabile'
    assert out['confronto_corrente'] is not None, out['motivi']
    assert out['ultimo_non_verificato'] is False             # il «da confermare» non degrada il confronto


def test_profili_automatici_nuovi_usano_le_regex_standard():
    from bellomberg.market_data.filing_profili_auto import profilo_sec, sonda_tipo_6k
    profilo = profilo_sec('ZZTEST', cik=CIK, sec_ticker='ZZTEST', nome='Zztest Holdings', origine='ticker',
                          forme={'20-F', '6-K'}, tipo_6k='trimestrale')
    variante = profilo['varianti'][1]
    import re
    assert re.search(variante['verifica']['tipo'], 'for the second quarter ended June 30, 2025', re.I)
    assert re.search(variante['verifica']['periodo'], 'three and six-month periods ended June 30, 2025', re.I)
    assert sonda_tipo_6k('Results for the second quarter ended June 30, 2025') == 'trimestrale'


# ---------------------------------------------------------------------------------------------
# Cura 1: ponte nella Trade Idea
# ---------------------------------------------------------------------------------------------

def _board_ti(**data):
    return SimpleNamespace(data=dict(data), _lock=RLock(), target_ticker='ZZTEST')


def test_trade_idea_ammette_una_volta_e_la_ripresa_riusa(tmp_path):
    archivio = _archivio(tmp_path)
    board = _board_ti()
    record = ponte.ammetti_per_trade_idea(board, as_of='2030-01-15', servizio_factory=lambda: archivio,
                                          registro_factory=lambda: REGISTRO)
    assert board.data[ponte.CHIAVE_BOARD] == record and record['stato'] == 'ok'
    voce = record['tickers']['ZZTEST']
    assert voce['aggiornamento_pre_run'] == {'stato': 'non_eseguito', 'motivo': ponte.MOTIVO_TRADE_IDEA}
    assert len(voce['documenti']) == 3

    def vietato():
        raise AssertionError('la ripresa non deve rileggere il DB Filing')
    assert ponte.ammetti_per_trade_idea(board, as_of='2030-01-15', servizio_factory=vietato) == record


def test_trade_idea_run_gia_sigillata_senza_ponte_resta_invariata(tmp_path):
    board = _board_ti(_research_thesis={'sigillo': 'vecchio'})
    def vietato():
        raise AssertionError('mai ammettere documenti dopo il sigillo')
    assert ponte.ammetti_per_trade_idea(board, as_of='2030-01-15', servizio_factory=vietato) is None
    assert ponte.CHIAVE_BOARD not in board.data


def test_trade_idea_archivio_guasto_e_lacuna_dichiarata(tmp_path):
    board = _board_ti()
    def guasto():
        raise RuntimeError('DB alternativo: archivio Filing isolato non fornito (finto)')
    record = ponte.ammetti_per_trade_idea(board, as_of='2030-01-15', servizio_factory=guasto,
                                          registro_factory=lambda: REGISTRO)
    voce = record['tickers']['ZZTEST']
    assert record['stato'] == 'errore' and voce['esito'] == 'archivio_in_errore'
    assert 'archivio Filing isolato non fornito' in voce['motivi'][0]


# ---------------------------------------------------------------------------------------------
# End-to-end Trade Idea offline (fixture no_workbook_case): dossier dal ponte fino al Capo
# ---------------------------------------------------------------------------------------------

from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: E402,F401  (fixture)
from test_trade_idea_store import db_path, migrated, store  # noqa: E402,F401
from _smtp_cattura import smtp  # noqa: E402,F401
from test_trade_idea_economic import IDENTITY  # noqa: E402


def _archivio_e2e(root, *, solo_non_verificati=False):
    db = root / 'filing-e2e.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store_ = FilingStore(db)
    archivio = root / 'filing_archive_e2e'
    ticker = IDENTITY['ticker']
    store_.set_profile(ticker, {**BASE, 'tipo': 'trimestrale', 'emittente_id': 'CIK:' + CIK, 'ticker': ticker})
    annuale = _doc(archivio, ticker, 'annual.htm', '<html><body>Synthetic issuer annual report year ended '
                   'December 31, 2025. Revenue 100 EUR million.</body></html>')
    semestre = _doc(archivio, ticker, 'h1.htm', '<html><body>Synthetic issuer interim period ended June 30, 2026. '
                    'Revenue 55 EUR million.</body></html>')
    url = lambda name: BASE_URL + '000999007726000001/' + name
    candidati = [_cand(*semestre, url('h1.htm'), periodo='2026-06-30', filed='2026-08-10',
                       stato='periodo_da_confermare', motivi=['periodo da confermare: periodo testuale assente o ambiguo'])]
    if not solo_non_verificati:
        candidati.append(_cand(*annuale, url('annual.htm'), periodo='2025-12-31', tipo='annuale',
                               filed='2026-03-01', form='20-F'))
    _run(store_, ticker, 'ok', {'stato': 'ok', 'motivi': [], 'candidati': candidati})
    return SimpleNamespace(store=store_, archive_root=archivio)


def _con_ponte(monkeypatch, servizio):
    from bellomberg.agents import trade_idea
    originale = trade_idea.execute_trade_idea
    chiamate = []

    def factory():
        chiamate.append(1)
        return servizio
    monkeypatch.setattr(trade_idea, 'execute_trade_idea', partial(originale, filing_service_factory=factory))
    return chiamate


def test_e2e_trade_idea_dossier_dal_ponte_fino_al_capo(no_workbook_case, monkeypatch):
    from bellomberg.agents import trade_idea
    case = no_workbook_case
    servizio = _archivio_e2e(case.root)
    chiamate = _con_ponte(monkeypatch, servizio)
    run_id = case.current.create_run(case.request, idempotency_key='ponte-e2e')['run']['id']
    detail = case.execute(run_id, 'ponte')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    assert chiamate == [1]
    data = detail['progress']['checkpoint']['data']
    ricevuta = data[ponte.CHIAVE_BOARD]
    assert ricevuta['tickers'][IDENTITY['ticker']]['esito'] == 'ammesso'
    dossier = data['_research_thesis']['dossiers'][IDENTITY['ticker']]
    ponte_docs = [d for d in dossier['documents'] if (d.get('metadata') or {}).get('filing_bridge')]
    assert [(d['metadata']['filing_bridge']['periodo'], d['metadata']['filing_bridge'].get('verifica', 'verificato'))
            for d in ponte_docs] == [('2026-06-30', 'non_verificato'), ('2025-12-31', 'verificato')]
    assert dossier['filing_bridge']['esito'] == 'ammesso'
    # l'identita' resta quella della qualificazione della run, mai l'impronta del ponte
    assert dossier['source_fingerprint'] == detail['progress']['source_qualification']['fingerprint']
    assert dossier['source_fingerprint'] != dossier['filing_bridge']['entry_sha256']
    assert any(i['code'] == 'filing_archive_documento_non_verificato' for i in dossier['issues'])
    # i documenti acquisiti dal desk restano accanto (nessuna sostituzione)
    assert any(not (d.get('metadata') or {}).get('filing_bridge') for d in dossier['documents'])
    # il Capo (ultima chiamata) vede l'indice con l'etichetta e gli ID citabili
    capo = json.dumps(case.providers[-1], ensure_ascii=False, default=str)
    assert ponte_docs[0]['id'] in capo and ponte_docs[1]['id'] in capo
    assert 'non verificato: periodo da confermare' in capo
    assert re.search(r'fonte_primaria_verificata[^a-z]{1,8}false', capo)
    assert not any('nessun bilancio' in gap for gap in detail['result']['data_gaps'])


def test_e2e_trade_idea_solo_non_verificati_dichiara_il_limite(no_workbook_case, monkeypatch):
    from bellomberg.agents import trade_idea
    case = no_workbook_case
    servizio = _archivio_e2e(case.root, solo_non_verificati=True)
    _con_ponte(monkeypatch, servizio)
    run_id = case.current.create_run(case.request, idempotency_key='ponte-e2e-nv')['run']['id']
    detail = case.execute(run_id, 'ponte-nv')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    board = SimpleNamespace(analysis_mode=RESEARCH_ANALYSIS_MODE, target_ticker=IDENTITY['ticker'],
        data={'_research_thesis': {'dossiers': {IDENTITY['ticker']: {'documents': [
            d for d in detail['progress']['checkpoint']['data']['_research_thesis']['dossiers'][IDENTITY['ticker']]['documents']
            if (d.get('metadata') or {}).get('filing_bridge')]}}}})
    assert trade_idea.official_documents_gaps(board) == [
        "solo documenti dell'emittente NON verificati nel dossier (ammessi con etichetta): l'analisi "
        "li legge, ma non poggia su filing primari verificati"]


def test_e2e_trade_idea_ripresa_riusa_la_ricevuta(no_workbook_case, monkeypatch):
    case = no_workbook_case
    servizio = _archivio_e2e(case.root)
    chiamate = _con_ponte(monkeypatch, servizio)
    parent = case.current.create_run(case.request, idempotency_key='ponte-ripresa')['run']['id']
    case.state['stop_after'] = 0
    first = case.execute(parent, 'p0')
    assert first['run']['technical_status'] == 'incomplete'
    salvata = first['progress']['checkpoint']['data'][ponte.CHIAVE_BOARD]
    child = case.current.create_continuation(parent, idempotency_key='ponte-ripresa-1',
                                             authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = None
    final = case.execute(child, 'p1')
    assert final['run']['technical_status'] == 'completed', final['run']['reason']
    assert chiamate == [1]                                            # DB Filing letto UNA volta
    assert final['progress']['checkpoint']['data'][ponte.CHIAVE_BOARD] == salvata


def test_e2e_trade_idea_db_alternativo_senza_archivio_isolato_lo_dichiara(no_workbook_case):
    case = no_workbook_case
    run_id = case.current.create_run(case.request, idempotency_key='ponte-assente')['run']['id']
    detail = case.execute(run_id, 'assente')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    dossier = detail['progress']['checkpoint']['data']['_research_thesis']['dossiers'][IDENTITY['ticker']]
    assert dossier['filing_bridge']['esito'] == 'archivio_in_errore'
    assert 'archivio Filing isolato non fornito' in dossier['filing_bridge']['motivi'][0]


def test_lettura_di_un_non_verificato_non_attesta_numeri_operativi():
    """Il desk puo' leggere e citare il non verificato, ma la sua ricevuta non lega un'Evidence numerica."""
    from bellomberg.agents import trade_idea
    uscita = {'ok': True, 'as_of': '2030-01-10', 'text': 'Revenue 222.22 EUR million', 'document_id': 'd1'}
    board = SimpleNamespace(execution_policy='trade-idea-research/3', analysis_mode=RESEARCH_ANALYSIS_MODE,
                            data={}, tool_receipts=[
        {'tool': 'read_company_dossier', 'success': True, 'input': {'ticker': 'ZZTEST'},
         'output': json.dumps({**uscita, 'fonte_primaria_verificata': False, 'etichetta': 'non verificato: x'})}])
    result = {'dossier': [{'key': 'executive', 'evidence_ids': ['e1']}],
              'evidence': [{'id': 'e1', 'source': '[src: read_company_dossier] archivio', 'as_of': '2030-01-10',
                            'summary': 'Revenue 222.22 EUR million'}]}
    bound, numeric = trade_idea._bound_evidence_details(result, board, 'ZZTEST', '2030-01-15')
    assert bound == {} and numeric == set()
    board.tool_receipts[0]['output'] = json.dumps(uscita)       # stesso testo, documento verificato
    bound, numeric = trade_idea._bound_evidence_details(result, board, 'ZZTEST', '2030-01-15')
    assert set(bound) == {'e1'} and numeric == {'e1'}


def test_pdf_con_pagina_muta_entra_come_testo_incompleto_e_si_legge(tmp_path):
    from test_ponte_filing_dossier import _pdf
    from bellomberg.valuation.company_dossier import read_company_dossier
    db = tmp_path / 'filing.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store_ = FilingStore(db)
    root = tmp_path / 'filing_archive'
    store_.set_profile('ZZTEST', {**BASE, 'tipo': 'semestrale', 'emittente_id': 'LEI:999900ZZTEST000001', 'ticker': 'ZZTEST'})
    pdf = _pdf(root / 'ZZTEST' / 'documents' / 'h1.pdf', ['ZZTEST SpA relazione semestrale 2029 ricavi 12.34', ''])
    _run(store_, 'ZZTEST', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [
        _cand(*pdf, 'https://ir.zztest.example/h1.pdf', periodo='2029-06-30', tipo='semestrale', fonte='IR', form=None)]})
    board = _Board()
    ponte.ammetti_archivio_filing(board, _Store(), servizio_factory=lambda: SimpleNamespace(store=store_, archive_root=root),
                                  registro_factory=lambda: REGISTRO)
    doc = board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']['documenti'][0]
    assert doc['verifica'] == 'non_verificato' and doc['periodo_stato'] == 'certo'
    assert doc['etichetta_verifica'].endswith('(testo incompleto)')
    letto = read_company_dossier(board, {'ticker': 'ZZTEST', 'section': 'document', 'document_id': doc['id']},
                                 max_chars=12000)
    assert letto['ok'] is True and 'ricavi 12.34' in letto['text'] and letto['fonte_primaria_verificata'] is False


def test_campi_sito_ir_e_periodo_fiscale_arrivano_fino_al_capo(tmp_path):
    """Contratto APERTO-SITI: esercizio non solare senza data inventata, sito IR scoperto su altro dominio."""
    db = tmp_path / 'filing.db'
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store_ = FilingStore(db)
    root = tmp_path / 'filing_archive'
    store_.set_profile('ZZTEST', {**BASE, 'tipo': 'trimestrale', 'emittente_id': 'EMITTENTE:Zztest Holdings',
                                  'ticker': 'ZZTEST'})
    path, digest = _doc(root, 'ZZTEST', 'q4fy.htm', '<html><body>Zztest Holdings Q4 FY2029 results, revenue 77.77.</body></html>')
    riga = {'fonte': 'IR', 'origine': 'sito_emittente', 'path': path, 'sha256': digest, 'stato': 'non_verificato',
            'url': 'https://ir.zztest-group.example/q4fy2029.htm', 'filed_date': '2029-11-20',
            'motivi': ['periodo non verificabile: trimestre fiscale senza data di chiusura'],
            'tipo_documento': 'estratto', 'periodo_fiscale': 'Q4 FY2029', 'sito_ir': 'ir.zztest-group.example',
            'sito_ir_origine': 'sito di gruppo linkato dalla home, scoperto da https://zztest.example/',
            'etichetta': "sito IR dell'emittente ir.zztest-group.example (scoperto dal sito zztest.example), "
                         "non archivio ufficiale (OAM)"}
    _run(store_, 'ZZTEST', 'ok', {'stato': 'ok', 'motivi': [], 'candidati': [riga]})
    board = _Board()
    ponte.ammetti_archivio_filing(board, _Store(), servizio_factory=lambda: SimpleNamespace(store=store_, archive_root=root),
                                  registro_factory=lambda: REGISTRO)
    doc = board.data[ponte.CHIAVE_BOARD]['tickers']['ZZTEST']['documenti'][0]
    assert doc['periodo'] is None and doc['periodo_fiscale'] == 'Q4 FY2029'
    assert doc['sito_ir'] == 'ir.zztest-group.example' and doc['sito_ir_origine'].startswith('sito di gruppo')
    assert doc['tipo_documento'] == 'estratto'
    seal_research_thesis(board, desks=('macro', 'fundamentals'))
    dossier = board.data['_research_thesis']['dossiers']['ZZTEST']
    eta = [i['reason'] for i in dossier['issues'] if i['code'] == 'filing_archive_eta_non_valutabile']
    assert eta and 'n.d. (esercizio non solare: Q4 FY2029)' in eta[0]
    voce = research_context(board)['dossiers']['ZZTEST']['documenti'][0]
    assert voce['eta_giorni'] == 'n.d. (esercizio non solare: Q4 FY2029)'
    assert (voce['periodo_fiscale'], voce['sito_ir']) == ('Q4 FY2029', 'ir.zztest-group.example')
    assert voce['sito_ir_origine'].startswith('sito di gruppo') and voce['verificato'] is False


# ---------------------------------------------------------------- R-SITI2 D8 e mutanti Q19-Q21 (6-K)

def _sei_k_libero(corpo, registrante, copertina_extra=""):
    return ('<html lang="en"><body><p>FORM 6-K</p><p>For the month of August, 2025</p>'
            f'<p>{registrante}</p><p>(Exact name of registrant as specified in its charter)</p>{copertina_extra}'
            '<p>Indicate by check mark whether the registrant files annual reports under cover of Form 20-F or Form 40-F.</p>'
            f'{corpo}<p>Revenue 333.33 million; net income 44.44 million.</p>'
            '<p>Forward-looking statements</p><p>Boilerplate.</p></body></html>').encode()


def test_r_siti2_D8_partner_senza_forma_giuridica_non_verificato(tmp_path):
    corpo = ('<p>Zzpartner Reports Second Quarter Results</p><p>Zzpartner, the payments partner of Zztest Holdings, '
             'today released its consolidated financial results for the second quarter ended June 30, 2025.</p>')
    path = _scrivi(tmp_path, 'd8.htm', _sei_k_libero(corpo, 'Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000003/d8.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'non_verificato', esito
    assert any("frase del periodo" in m for m in esito['motivi']), esito['motivi']


def test_r_siti2_comunicato_del_registrante_con_dateline_resta_ok(tmp_path):
    corpo = ('<p>SAO PAULO, August 13, 2025 -- Zztest Holdings Ltd. (NYSE: ZZT) today released its consolidated '
             'financial results for the second quarter ended June 30, 2025.</p>')
    path = _scrivi(tmp_path, 'dl.htm', _sei_k_libero(corpo, 'Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000006/dl.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'ok', esito


def test_r_siti2_Q19_corpo_dopo_l_ULTIMA_riga_di_copertina(tmp_path):
    extra = '<p>Zzagent Services Ltd., agent for service of process</p>'
    corpo = ('<p>Zztest Holdings Ltd. today released its consolidated financial results for the second quarter '
             'ended June 30, 2025.</p>')
    path = _scrivi(tmp_path, 'q19.htm', _sei_k_libero(corpo, 'Zztest Holdings Ltd.', extra))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000007/q19.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'ok', esito


def _profilo_cik(nome):
    return {**PROFILO_6K, 'nome': nome, 'verifica': {**PROFILO_6K['verifica'], 'emittente': r'\bzzassente\s+nome\b'}}


def test_r_siti2_Q20_marchio_corto_non_e_alias(tmp_path):
    corpo = ('<p>Zzt Partners today released its consolidated financial results for the second quarter ended '
             'June 30, 2025.</p>')
    path = _scrivi(tmp_path, 'q20.htm', _sei_k_libero(corpo, 'Zzt Group Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000008/q20.htm', profilo=_profilo_cik('Zzt Group'),
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'non_verificato', esito


def test_r_siti2_Q21_registrante_alias_nel_corpo(tmp_path):
    corpo = ('<p>Zzt Holdings Ltd. today released its consolidated financial results for the second quarter ended '
             'June 30, 2025.</p>')
    path = _scrivi(tmp_path, 'q21.htm', _sei_k_libero(corpo, 'Zzt Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000009/q21.htm', profilo=_profilo_cik('Zzt H'),
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'ok', esito


def test_r_siti2_N20_abbreviazione_della_forma_non_spezza_la_frase(tmp_path):
    corpo = ('<p>Zztest Holdings Corp. Announces Consolidated Results for the Second Quarter Ended June 30, 2025</p>'
             '<p>The consolidated financial statements of the group are attached.</p>')
    path = _scrivi(tmp_path, 'n20.htm', _sei_k_libero(corpo, 'Zztest Holdings Corp.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000010/n20.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] in ('ok', 'periodo_da_confermare'), esito
    assert not any("frase del periodo" in m for m in esito.get('motivi', [])), esito


# ---------------------------------------------------------------- impianto nuovo: 6-K (E8, F1)

def test_impianto_E8_possessivo_del_registrante_soggetto_il_partner(tmp_path):
    corpo = ("<p>Zzpartner Reports Second Quarter Results</p><p>Zztest Holdings' payments partner Zzpartner today "
             "released its consolidated financial results for the second quarter ended June 30, 2025.</p>")
    path = _scrivi(tmp_path, 'e8.htm', _sei_k_libero(corpo, 'Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000011/e8.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] == 'non_verificato' and any("frase del periodo" in m for m in esito['motivi']), esito


@pytest.mark.parametrize("corpo", [
    "<p>Zztest Holdings Ltd.</p><p>Unaudited Interim Condensed Consolidated Financial Statements</p>"
    "<p>As of and for the three months ended June 30, 2025</p>",
    "<p>Zztest Holdings Reports Second Quarter 2025 Results</p><p>In the second quarter ended June 30, 2025, Zztest "
    "Holdings grew its consolidated revenue.</p>"])
def test_impianto_F1_frase_senza_altra_entita_vale_per_il_registrante(tmp_path, corpo):
    path = _scrivi(tmp_path, 'f1.htm', _sei_k_libero(corpo, 'Zztest Holdings Ltd.'))
    esito = verifica_6k_standard(path, url=BASE_URL + '000999007729000012/f1.htm', profilo=PROFILO_6K,
                                 catalogo={'form': '6-K'})
    assert esito['stato'] in ('ok', 'periodo_da_confermare'), esito
    assert not any("frase del periodo" in m for m in esito.get('motivi', [])), esito
