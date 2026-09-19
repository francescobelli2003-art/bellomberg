"""Le illustrazioni F9, F13 e F18 disegnano l'impianto che l'app ha dal 12/09 (13/09, Claude Opus 5).

Il generatore docs/guide/build_mockups.py disegnava ancora la vista «Chain–Strategie» e il tag
«CATALOGO», e nessun test leggeva il contenuto degli SVG. Qui i testi degli SVG si confrontano con
le etichette dei cataloghi italiani e con le soglie del sorgente. Solo file di testo: nessun import
del repository.
"""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'docs/assets/product'
SVG_TEXT = '{http://www.w3.org/2000/svg}text'
SLUGS = ('09-vol-deck', '13-agent-progress', '18-mandate-journal')


def _testi(slug):
    root = ET.fromstring((ASSETS / f'{slug}.svg').read_text(encoding='utf-8'))
    return [''.join(node.itertext()) for node in root.iter(SVG_TEXT)]


def _righe(slug):
    """(y, testo) di ogni <text>: l'ordine verticale di un elenco disegnato."""
    root = ET.fromstring((ASSETS / f'{slug}.svg').read_text(encoding='utf-8'))
    return [(float(node.get('y')), ''.join(node.itertext())) for node in root.iter(SVG_TEXT)]


def _ordine_disegnato(slug, etichette):
    righe = _righe(slug)
    presenti = [min(y for y, testo in righe if testo == etichetta) for etichetta in etichette
                if any(testo == etichetta for _, testo in righe)]
    return presenti


def _sorgente(percorso):
    return (ROOT / percorso).read_text(encoding='utf-8')


def _voce_json(catalogo, chiave):
    trovata = re.search(rf'"{chiave}":\s*"([^"]*)"', _sorgente(f'app/src/i18n/it/{catalogo}.ts'))
    assert trovata, f'{chiave} assente in app/src/i18n/it/{catalogo}.ts'
    return trovata.group(1)


def _schede_vol_deck():
    pagina = _sorgente('app/src/pages/VolSurfacePage.tsx')
    blocco = re.search(r"\(\[([^\]]+)\] as const\)\.map\(mode =>", pagina)
    assert blocco, 'elenco delle schede non trovato in VolSurfacePage.tsx'
    catalogo = _sorgente('app/src/i18n/it/voldeck.ts')
    return [re.search(rf"\b{i}: '([^']+)'", catalogo).group(1) for i in re.findall(r"'([a-z]+)'", blocco.group(1))]


def _schede_progressi():
    pagina = _sorgente('app/src/pages/AgentProgressPage.tsx')
    blocco = re.search(r"\(\{ measurements: 'progress\.tabMeasurements'[^}]*\} as const\)\[id\]", pagina)
    assert blocco, 'etichette delle schede non trovate in AgentProgressPage.tsx'
    return [_voce_json('progress', chiave) for chiave in re.findall(r"'progress\.(\w+)'", blocco.group(0))]


def _schede_mandato():
    chiavi = re.findall(r"role=\"tab\"[^\n]*?>\{tr\('mandate\.(\w+)'\)\}</button>", _sorgente('app/src/pages/MandatoPage.tsx'))
    assert chiavi, 'schede non trovate in MandatoPage.tsx'
    return [_voce_json('mandate', chiave) for chiave in chiavi]


SCHEDE = {'09-vol-deck': _schede_vol_deck, '13-agent-progress': _schede_progressi,
          '18-mandate-journal': _schede_mandato}


@pytest.mark.parametrize('slug', SLUGS)
def test_illustrazione_non_disegna_la_vista_di_prima_del_12_09(slug):
    testo = '\n'.join(_testi(slug))
    assert [vecchio for vecchio in ('Chain–Strategie', 'CATALOGO') if vecchio in testo] == []


@pytest.mark.parametrize('slug', SLUGS)
def test_illustrazione_mostra_le_schede_con_le_etichette_del_catalogo_italiano(slug):
    etichette = SCHEDE[slug]()
    assert len(etichette) >= 2
    testi = set(_testi(slug))
    assert [etichetta for etichetta in etichette if etichetta not in testi] == []


def test_vol_deck_illustrazione_mostra_le_quattro_schede_di_voldeck_ts():
    assert _schede_vol_deck() == ['Acquisizione', 'Strumenti', 'Chain', 'Laboratorio']


def test_progressi_dichiara_campione_piccolo_solo_sotto_la_soglia_dello_scorekeeper():
    soglia = int(re.search(r'^SMALL_SAMPLE_N\s*=\s*(\d+)', _sorgente('src/bellomberg/agents/scorekeeper.py'), re.M).group(1))
    piccolo = _voce_json('progress', 'quality_small_sample')
    letture = [(int(trovata.group(1)), testo) for testo in _testi('13-agent-progress')
               for trovata in [re.search(r'\bn=(\d+)', testo)] if trovata]
    assert letture, "nessuna lettura «n=» nell'illustrazione 13: il controllo sulla soglia sarebbe vuoto"
    sbagliate = [testo for n, testo in letture if (piccolo in testo) != (0 < n < soglia)]
    assert sbagliate == []


def test_progressi_elenca_gli_agenti_nell_ordine_di_score_history():
    blocco = re.search(r'^AGENTS = \((.*?)^\)', _sorgente('src/bellomberg/agents/score_history.py'), re.M | re.S)
    etichette = re.findall(r'\("[^"]+", "([^"]+)", "[^"]+"\)', blocco.group(1))
    assert etichette[0] == 'Capo / Comitato'
    disegnate = _ordine_disegnato('13-agent-progress', etichette)
    assert len(disegnate) >= 2, "l'illustrazione 13 non disegna l'elenco Agenti e ruoli"
    assert disegnate == sorted(disegnate)


def test_mandato_disegna_i_campi_del_rischio_nell_ordine_dello_schema():
    schema = re.findall(r'^\s*"(\w+)": _c\("rischio"', _sorgente('src/bellomberg/core/mandato_pm.py'), re.M)
    assert schema[:3] == ['volatilita_target_pct', 'var99_1g_pct', 'drawdown_max_pct']
    etichette = [_voce_json('mandate', f'field_{nome}') for nome in schema]
    disegnate = _ordine_disegnato('18-mandate-journal', etichette)
    assert len(disegnate) >= 2, "l'illustrazione 18 non disegna i campi della sezione Rischio"
    assert disegnate == sorted(disegnate)
