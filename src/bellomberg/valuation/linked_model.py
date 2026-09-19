"""Shared workbook plumbing; financial equations stay in the method modules."""
from datetime import date
from math import isfinite

from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font
from openpyxl.workbook.properties import CalcProperties
from bellomberg.core.language import text as tr
from .documented_presentation import _sheet, _put, _line, _header, _finish, col, AMOUNT, PRICE, PERCENT
from .documented_formulas import _formula, _input_rows


SCENARIOS = ('bear', 'base', 'bull')


def ready(payload, methods):
    if payload.get('method') not in methods or not payload.get('valuation_usability', {}).get('usable'):
        return False
    cases = payload.get('calculation_details', {}).get('scenarios', {})
    calendar = next((r.get('values') for r in payload.get('analytical_quality', {}).get('rows', [])
                     if r.get('scenario') == 'model' and r.get('driver') == 'calendar'), None)
    if not isinstance(calendar, dict) or not isinstance(calendar.get('periods'), list):
        return False
    convention = calendar.get('discount_convention')
    if convention == 'snapshot':
        if calendar['periods']:
            return False
    elif convention not in ('annual_end', 'ACT/365F') or not calendar['periods']:
        return False
    try:
        date.fromisoformat(calendar['valuation_date'])
        for period in calendar['periods']:
            date.fromisoformat(period['start']); date.fromisoformat(period['end'])
    except (KeyError, TypeError, ValueError):
        return False
    return all(type(cases.get(s, {}).get('fair_value_per_share')) in (int, float)
               and isfinite(cases[s]['fair_value_per_share']) for s in SCENARIOS)


def total(expressions):
    """Arithmetic addition propagates invalid cells; SUM would ignore text."""
    expressions = list(expressions)
    return '(' + '+'.join(expressions) + ')' if expressions else '0'


class LinkedModel:
    def __init__(self, wb, payload, *, skip_drivers=()):
        self.wb, self.payload = wb, payload
        evidence = payload['analytical_quality']['rows']
        self.values = {(r['scenario'], r['driver']): r['values'] for r in evidence}
        self.refs, self.checks, self.results = {}, {s: [] for s in SCENARIOS}, {}
        self.support_calls = {s: [] for s in SCENARIOS}
        self.input_cells = {s: [] for s in ('model', *SCENARIOS)}
        self.calendar = self.values[('model', 'calendar')]
        self.years = [p['end'] for p in self.calendar.get('periods', [])]
        self.n = len(self.years)
        self.end = max(6, self.n + 3)
        ws = _sheet(wb, 'Model Inputs', tr('Ipotesi e provenienza', 'Assumptions and provenance'), self.end)
        _line(ws, 4, tr('Blu: simulazioni locali. Struttura, date e convenzioni restano quelle documentate. Verificare tutti i controlli.',
                        'Blue: local simulations. Structure, dates and conventions remain as documented. Review every check.'), self.end, height=40)
        row = 7
        for scenario in ('model', *SCENARIOS):
            _header(ws, row, [scenario, tr('Unita', 'Units'), *(self.years or [tr('Valore', 'Value')])])
            row += 1
            for record in (r for r in evidence if r['scenario'] == scenario and r['driver'] not in skip_drivers):
                for path, value in _input_rows(record['values']):
                    _put(ws, row, 2, ' / '.join(map(str, (record['driver'], *path))).replace('_', ' '))
                    ws.cell(row, 2).alignment = Alignment(wrap_text=True, vertical='center')
                    ws.row_dimensions[row].height = 34
                    proof = record.get('evidence', {})
                    _put(ws, row, 3, proof.get('unit'))
                    vector = isinstance(value, list)
                    for i, number in enumerate(value if vector else [value]):
                        fmt = PERCENT if proof.get('unit') == 'ratio' else AMOUNT
                        cell = _put(ws, row, i + 4, number, fmt)
                        cell.font = Font(name='Arial', size=10, color='0000FF')
                        cell.comment = Comment(str(proof), 'Source')
                        ref = "'Model Inputs'!" + cell.coordinate
                        self.refs[(scenario, record['driver'], *path, *((i,) if vector else ()))] = ref
                        self.input_cells[scenario].append(ref)
                    row += 1
            row += 2
        _finish(ws, row, self.end)

    def ref(self, scenario, driver, *path):
        return self.refs[(scenario, driver, *path)]

    def value(self, scenario, driver):
        return self.values[(scenario, driver)]

    def bind(self, scenario, driver, path, expression):
        """A reconciled total has one formula owner, never a second editable input."""
        ref = self.ref(scenario, driver, *path)
        cell = self.wb['Model Inputs'][ref.split('!')[1]]
        _formula(cell.parent, cell.row, cell.column, f'IFERROR({expression},"n.d.")', cell.number_format)
        return ref

    def sheet(self, name, title, years=None):
        years = self.years if years is None else years
        ws = _sheet(self.wb, name, title, max(self.end, len(years) + 3))
        _line(ws, 5, tr('Importi in milioni di ', 'Amounts in millions of ') + str(self.payload.get('financial_currency') or 'n.d.')
              + tr('; azioni in milioni; valori per azione e rapporti esclusi.', '; shares in millions; except per-share amounts and ratios.'),
              max(self.end, len(years) + 3), height=30)
        _header(ws, 7, [tr('Voce', 'Item'), tr('Unita', 'Units'), *years])
        return ws

    def calc(self, ws, row, column, label, expression, fmt=AMOUNT):
        _put(ws, row, 2, label)
        _formula(ws, row, column, f'IFERROR({expression},"n.d.")', fmt)
        return "'" + ws.title.replace("'", "''") + "'!" + ws.cell(row, column).coordinate

    def check(self, scenario, label, expression):
        self.checks[scenario].append((label, expression))

    def equal(self, scenario, label, first, second, tolerance='0.00000001'):
        self.check(scenario, label, f'ABS(({first})-({second}))<={tolerance}*MAX(1,ABS({first}),ABS({second}))')

    def period(self, i):
        if self.calendar['discount_convention'] == 'annual_end':
            return str(i + 1)
        if self.calendar['discount_convention'] == 'ACT/365F':
            return str((date.fromisoformat(self.years[i]) - date.fromisoformat(self.calendar['valuation_date'])).days / 365)
        raise ValueError('Unsupported discount convention')

    def finish(self):
        wb = self.wb
        checks = _sheet(wb, 'Input Checks', tr('Validita delle simulazioni', 'Simulation validity'), 6)
        _line(checks, 4, tr('I controlli verificano numeri e vincoli modellati, non approvano le ipotesi o le fonti.',
                            'Checks verify numbers and modeled constraints; they do not approve assumptions or sources.'), 6, height=40)
        guards = {}
        row = 7
        for scenario in SCENARIOS:
            _header(checks, row, [scenario, tr('Esito', 'Status')]); row += 1
            start = row
            refs = self.input_cells['model'] + self.input_cells[scenario]
            conditions = [(tr('Input numerici', 'Numeric inputs'), 'AND(' + ','.join(f'ISNUMBER({r})' for r in refs[i:i+35]) + ')')
                          for i in range(0, len(refs), 35)] + self.checks[scenario]
            for label, expression in conditions:
                _put(checks, row, 2, label)
                _formula(checks, row, 3, f'IFERROR({expression},FALSE)', 'General'); row += 1
            _put(checks, row, 2, tr('Controllo complessivo', 'Overall check'), bold=True)
            _formula(checks, row, 3, f'IF(COUNTIF(C{start}:C{row-1},FALSE)=0,"OK","KO")', 'General')
            guards[scenario] = f"'Input Checks'!C{row}"
            row += 3
        _finish(checks, row, 6)
        summary = _sheet(wb, 'Summary', self.payload['ticker'] + ' — ' + tr('Valutazione', 'Valuation'), 6)
        _line(summary, 4, tr('Formule collegate. Le modifiche sono simulazioni; i record approvati restano nel modello originale.',
                             'Linked formulas. Edits are simulations; approved records remain in the original model.'), 6, height=42)
        _header(summary, 7, [tr('Voce', 'Item'), tr('Unita', 'Units'), tr('Ribassista','Bear'), 'Base', tr('Rialzista','Bull')])
        baseline = _sheet(wb, 'Model Checks', tr('Riconciliazione al motore', 'Engine reconciliation'), 6)
        _header(baseline, 7, [tr('Scenario', 'Scenario'), tr('Motore', 'Engine'), 'Excel', tr('Differenza', 'Difference'), tr('Stato', 'State')])
        for i, scenario in enumerate(SCENARIOS):
            c = i + 4
            guard = guards[scenario]
            ref = self.results[scenario]
            factors = [self.ref('model', 'quotation', k) for k in ('financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote')]
            _put(summary, 8, 2, tr('Valore per azione', 'Value per share'), bold=True)
            _put(summary, 8, 3, self.payload.get('currency'))
            _formula(summary, 8, c, f'IFERROR(IF({guard}="OK",ROUND({ref}*{"*".join(factors)},2),"n.d."),"n.d.")', PRICE)
            _put(summary, 10, 2, tr('Prezzo alla data di valutazione', 'Price at valuation date'))
            quote = self.ref('model', 'quotation', 'price')
            _formula(summary, 10, c, quote, PRICE)
            _put(summary, 11, 2, tr('Potenziale alla stessa data', 'Upside at the same date'))
            _formula(summary, 11, c, f'IFERROR({col(c)}8/{quote}-1,"n.d.")', PERCENT)
            _put(summary, 13, 2, tr('Validita input', 'Input validity'))
            _formula(summary, 13, c, guard, 'General')
            r = i + 8
            _put(baseline, r, 2, scenario)
            original = self.payload['calculation_details']['scenarios'][scenario]['fair_value_per_share']
            _put(baseline, r, 3, original, PRICE)
            _formula(baseline, r, 4, f'IF({guard}="OK",{ref},"n.d.")', PRICE)
            _formula(baseline, r, 5, f'IFERROR(D{r}-C{r},"n.d.")', PRICE)
            _formula(baseline, r, 6, f'IF({guard}<>"OK","KO",IFERROR(IF(ABS(E{r})<0.0000001*MAX(1,ABS(C{r})),"OK","SIMULAZIONE"),"KO"))', 'General')
        _put(summary,15,2,tr('Data della valutazione','Valuation date'))
        _put(summary,15,4,self.payload.get('valuation_date'))
        _line(summary, 16, self.payload.get('valuation_basis', ''), 6, height=55)
        _line(summary, 19, tr('Aprire i prospetti di scenario e Model Inputs. Fonti e convenzioni complete nei fogli di qualita.',
                              'Open scenario schedules and Model Inputs. Full sources and conventions remain in the quality sheets.'), 6, height=40)
        _finish(summary, 21, 6); _finish(baseline, 12, 6)
        summary.page_setup.orientation='portrait'
        summary.page_setup.fitToHeight=1
        summary.print_title_rows=None
        wb.move_sheet(summary, -wb.index(summary))
        wb.calculation = CalcProperties(calcId=191029, fullCalcOnLoad=True, forceFullCalc=True)
        wb._model_link = {'refs': self.refs, 'raw': {s: f"'Model Checks'!D{i+8}" for i, s in enumerate(SCENARIOS)},
                          'guards': guards, 'calls': self.support_calls,
                          'shares': {s: self.refs.get((s, 'capital.shares_m'), self.refs.get(('model', 'shares'))) for s in SCENARIOS}}

    def quotation_checks(self):
        for s in SCENARIOS:
            for key in ('price', 'financial_to_quote_rate', 'quote_units_per_currency', 'shares_per_quote'):
                self.check(s, 'Quotation: ' + key, self.ref('model', 'quotation', key) + '>0')
            quotation = self.value('model', 'quotation')
            self.check(s, 'Quotation unit scale', self.ref('model', 'quotation', 'quote_units_per_currency')
                       + '=' + str(100 if quotation['quote_unit'] in ('GBX', 'GBp') else 1))
            if quotation['financial_currency'] == quotation['quote_currency']:
                self.check(s, 'Same currency FX', self.ref('model', 'quotation', 'financial_to_quote_rate') + '=1')
