"""Versioned weekly prompts; read-only diagnostics from the sealed priming stage."""
from datetime import datetime
import json
import math
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked

POLICY = 'weekly-evidence-prompts/1'
KEY = 'evidence_prompt_policy'

def enabled(blackboard):
    if getattr(blackboard, 'run_scope', None) != 'weekly':
        return False
    store = getattr(blackboard, 'weekly_store', None)
    if store is None:
        return False
    contract = store.context.get('contract', {})
    if KEY not in contract:
        return False
    if contract[KEY] != POLICY:
        raise WeeklyRunBlocked('Policy evidence prompt non compatibile')
    return True

def _replace_exact(text, old, new):
    if text.count(old) != 1:
        raise WeeklyRunBlocked('Evidence prompt template non compatibile')
    return text.replace(old, new, 1)

def select_template(blackboard, desk, template):
    from bellomberg.core.evidence_followup_policy import board_enabled
    followup = board_enabled(blackboard)
    if not enabled(blackboard) and not followup:
        return template
    replacements = EVENT_REPLACEMENTS if desk == 'eventdesk' else RED_REPLACEMENTS if desk == 'red_team' else ()
    for old, new in replacements:
        template = _replace_exact(template, old, new)
    if followup and desk == 'eventdesk':
        from bellomberg.agents.specialists.eventdesk import EventDeskSpecialist
        template += EventDeskSpecialist.evidence_followup_instructions
    elif followup and desk == 'red_team':
        from bellomberg.agents.red_team import EVIDENCE_FOLLOWUP_INSTRUCTIONS
        from bellomberg.core.evidence_followup_policy import instructions
        template += instructions(blackboard) + EVIDENCE_FOLLOWUP_INSTRUCTIONS
    return template

def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)

def _date(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    except ValueError:
        return None

def _beta(value):
    from bellomberg.portfolio.advanced_metrics import testo_guardrail_beta_capo
    out = {'status': 'UNAVAILABLE', 'observed_at': None, 'verdict': 'UNAVAILABLE',
           'decision_eligible': False, 'diagnostic_text': testo_guardrail_beta_capo(None)}
    if not isinstance(value, dict):
        return out
    out['observed_at'] = _date(value.get('calcolato_il'))
    verdict = value.get('verdict')
    if value.get('error') or verdict not in ('RECONCILED', 'UNRELIABLE', 'INSUFFICIENT_SOURCES'):
        return out
    eligible = verdict == 'RECONCILED' and value.get('beta_per_decisioni') is True
    safe = {'verdict': verdict, 'beta_per_decisioni': eligible}
    betas = value.get('betas')
    if eligible:
        known = {'advanced_metrics_twr', 'portfolio_risk_spy', 'factor_model_mkt'}
        if (not isinstance(betas, dict) or len(betas) < 2 or not set(betas) <= known
                or not all(_finite(n) for n in betas.values())):
            return out
        safe['betas'] = dict(betas)
        consensus = value.get('beta_consensus')
        if consensus is not None:
            if not _finite(consensus):
                return out
            safe['beta_consensus'] = consensus
    elif verdict == 'INSUFFICIENT_SOURCES':
        if not isinstance(betas, dict):
            return out
        # Formatter uses only this observed count; no private source labels/values.
        safe['betas'] = dict.fromkeys(range(len(betas)))
    if verdict == 'UNRELIABLE':
        if not _finite(value.get('threshold')):
            return out
        safe['threshold'] = value['threshold']
    out.update(status='AVAILABLE', verdict=verdict, decision_eligible=eligible,
               diagnostic_text=testo_guardrail_beta_capo(safe))
    return out

def _freshness(value):
    from bellomberg.core.freshness import format_for_capo
    out = {'status': 'UNAVAILABLE', 'checked': None, 'fresh': None,
           'stale_count': None, 'unknown_count': None, 'diagnostic_text': 'Freshness n.d.: diagnostica assente o non valida.'}
    if not isinstance(value, dict) or value.get('error'):
        return out
    checked, fresh = value.get('checked'), value.get('fresh')
    stale, unknown = value.get('stale'), value.get('unknown')
    if (type(checked) is not int or type(fresh) is not int or checked < 0 or fresh < 0
            or not isinstance(stale, list) or not isinstance(unknown, list)
            or not all(isinstance(s, str) and s.strip() for s in stale + unknown)
            or checked != fresh + len(stale) + len(unknown)):
        return out
    text = format_for_capo({'stale': stale, 'unknown': unknown})
    if text is None:
        text = ('Zero controlli: freshness non attestata.' if checked == 0 else
                'Nessuna anomalia nel report salvato; esito limitato alle osservazioni controllate.')
    out.update(status='AVAILABLE', checked=checked, fresh=fresh, stale_count=len(stale),
               unknown_count=len(unknown), diagnostic_text=text)
    return out

def diagnostic_block(blackboard):
    if not enabled(blackboard):
        return ''
    # Integrity errors and missing prerequisites must never become best-effort prompts.
    priming = blackboard.weekly_store.get('priming')
    if not isinstance(priming, dict):
        raise WeeklyRunBlocked('Evidence prompt: priming persistito assente')
    payload = {'policy': POLICY, 'source_stage': 'priming'}
    for name, field, normalize in (('beta', 'beta_reconcile', _beta),
                                   ('freshness', 'freshness_report', _freshness)):
        try:
            payload[name] = normalize(priming.get(field))
        except Exception:
            payload[name] = {'status': 'UNAVAILABLE', 'diagnostic_text': 'CHECK_UNAVAILABLE: diagnostica n.d.'}
    return '\n\n=== DIAGNOSTICA SALVATA (disponibilita distinta da freshness e validita del metodo) ===\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


# Pure hooks for the NEXT evidence contract. They are deliberately not called by
# /1: composing these rules into a paid historical request would change its wire.
QUANT_INTERPRETATION_RULES = (
    "Quant: ogni KO appartiene al calcolo che lo ha prodotto. Per INSUFFICIENT_HISTORY "
    "riporta observations e minimum_observations; non attribuirlo a proxy o limiti di "
    "un altro calcolo. get_sector_exposure.econ_axis.effective_n = 1/HHI dei bucket economici con pesi sul capitale "
    "investito EUR (cash escluso), NON numero di scommesse statisticamente indipendenti. "
    "Se scali il VaR con sqrt(time), etichetta il risultato come approssimazione: "
    "rendimenti indipendenti/assenza di autocorrelazione, varianza costante e finita, "
    "distribuzione stabile; la legge sqrt(time) della volatilita' non garantisce "
    "lo stesso scaling dei quantili. Una soglia al 95% NON e' una frequenza osservata "
    "di 1 settimana su 20. Cita separatamente orizzonte e backtest; campione "
    "insufficiente NON dimostra clustering, il clustering NON prova una causa economica. "
    "RF source_metadata_unavailable significa provenienza/freschezza non attestate: "
    "mai chiamarlo live. Mantieni benchmark, valuta, finestra e origine congelata; "
    "assenza di questi metadati = n.d., mai inferenza da un'altra metrica."
)


def quant_semantics(priming):
    """Project supplied/frozen quant metadata only; no acquisition or policy change.

    Input keys: quant_advanced_metrics (native frozen entry), or advanced_metrics
    (payload); risk_data, sector_exposure, var_contribution, drawdowns, var_backtest.
    Absent producers remain absent diagnostics, never inferred from tool-health.
    """
    from copy import deepcopy
    source = priming if isinstance(priming, dict) else {}
    def mapping(name):
        value = source.get(name)
        return value if isinstance(value, dict) else {}
    advanced = mapping('advanced_metrics')
    if 'quant_advanced_metrics' in source:
        from bellomberg.core.quant_render_snapshot import payload_for
        advanced = payload_for({'entries': {'advanced_metrics': source['quant_advanced_metrics']}}, 'advanced_metrics')
    risk = mapping('risk_data')
    rate = advanced.get('risk_free_used')
    rf = {'value': rate if _finite(rate) else None,
          'status': advanced.get('risk_free_status') or 'source_metadata_unavailable',
          'source': advanced.get('risk_free_source'),
          'observed_at': advanced.get('risk_free_observed_at')}
    # Even an unavailable result retains its own measured sample and minimum.
    def failure(name):
        value = mapping(name)
        fields = ('error_code', 'calculation', 'observations', 'minimum_observations', 'observation_basis')
        out = {field: value.get(field) for field in fields}
        out['status'] = ('UNAVAILABLE' if not value or value.get('error') or value.get('error_code')
                         else 'AVAILABLE')
        return out
    backtest = failure('var_backtest')
    backtest.update(clustering_assessed=False if backtest['status'] == 'UNAVAILABLE' else None,
                    causal_inference_assessed=False)
    axis = mapping('sector_exposure').get('econ_axis') or {}
    out = {'risk_free': rf, 'var_contribution': failure('var_contribution'),
           'drawdowns': failure('drawdowns'), 'var_backtest': backtest,
           'sector_effective_n': {'value': axis.get('effective_n'),
                                  'metadata': axis.get('effective_n_metadata')},
           'time_scaling': risk.get('time_scaling'),
           'interpretation_rules': QUANT_INTERPRETATION_RULES}
    return deepcopy(out)


def quant_semantics_notes(risk=None, advanced=None):
    """Bilingual PDF notes from the supplied inputs; absent metadata says n/a."""
    from bellomberg.core.presentation import message
    from bellomberg.reporting.i18n import number
    risk = risk if isinstance(risk, dict) else {}
    advanced = advanced if isinstance(advanced, dict) else {}
    nd = message('n.d.', 'n/a')
    def shown(value):
        if _finite(value):
            return number(value, '.15g')
        return nd if value is None or isinstance(value, (dict, list, bool, int, float)) else str(value)
    notes = []
    if risk:
        window = risk.get('analysis_window') or {}
        notes.extend([
            message('Rischio: rf={rate} ({status}); Sharpe su questa convenzione, non attestazione del tasso di un altro calcolo.',
                    'Risk: rf={rate} ({status}); Sharpe uses this convention, without attesting the rate of another calculation.',
                    rate=shown(risk.get('risk_free_used')), status=shown(risk.get('risk_free_status'))),
            message('Benchmark {benchmark}; valuta/base {currency}; finestra {start} / {end}; osservazioni {n}; origine {source}.',
                    'Benchmark {benchmark}; currency/basis {currency}; window {start} / {end}; observations {n}; origin {source}.',
                    benchmark=shown(risk.get('benchmark_ticker')), currency=shown(risk.get('returns_basis')),
                    start=shown(window.get('start')), end=shown(window.get('end')),
                    n=shown(window.get('return_observations')), source=shown(risk.get('_source'))),
            message("Scaling sqrt(time): ipotesi di rendimenti non autocorrelati e varianza costante e finita, non testate qui. Il VaR a piu' giorni richiede anche ipotesi sulla distribuzione; non e' una frequenza osservata. Backtest: {status}; nessuna conclusione su clustering o cause economiche senza il relativo test.",
                    'Scaling sqrt(time): assumes uncorrelated returns and constant, finite variance, not tested here. Multi-day VaR also requires distributional assumptions; it is not an observed frequency. Backtest: {status}; no conclusion about clustering or economic causes without the relevant test.',
                    status=shown((risk.get('time_scaling') or {}).get('backtest_status'))),
        ])
    if advanced:
        rf = quant_semantics({'advanced_metrics': advanced})['risk_free']
        notes.append(message('Performance: rf={rate}; stato {status}; fonte {source}; data fonte {date}.',
                             'Performance: rf={rate}; status {status}; source {source}; source date {date}.',
                             rate=shown(rf['value']), status=shown(rf['status']),
                             source=shown(rf['source']), date=shown(rf['observed_at'])))
        if rf['status'] == 'source_metadata_unavailable':
            notes.append(message('Provenienza e freschezza del tasso non attestate dal contratto scalare.',
                                 'Rate provenance and freshness are not attested by the scalar contract.'))
        elif advanced.get('risk_free_note'):
            notes.append(str(advanced['risk_free_note']))
        notes.append(message('Benchmark {benchmark}; allineamento/valuta/finestra {alignment}; origine {source}.',
                             'Benchmark {benchmark}; alignment/currency/window {alignment}; origin {source}.',
                             benchmark=shown(advanced.get('benchmark_ticker')),
                             alignment=shown(advanced.get('benchmark_alignment')),
                             source=shown(advanced.get('_source'))))
    # 09/10 (Opus 5.5): un valore che finisce col punto («n.d.») davanti al punto del
    # modello dava «origine n.d..». Si toglie solo il punto doppio, mai l'ellissi.
    return [n[:-1] if n.endswith('..') and not n.endswith('...') else n for n in notes]

EVENT_REPLACEMENTS = (('3. PROBABILITA\' DI MERCATO, NON OPINIONI: per ogni evento politico/regolatorio/geopolitico get_polymarket_events -> probabilita\' implicita SEMPRE col testo della domanda e la variazione vs 7 e 30 giorni [src: get_polymarket_events]. I sondaggi RITARDANO, i mercati ANTICIPANO. IL GAP E\' IL TRADE: dove headline/sentiment e prezzo del prediction market divergono c\'e\' l\'opportunita\' - articola sempre "le news dicono X, il mercato prezza Y%, io leggo il gap cosi\'".', "3. PROBABILITA' DI MERCATO, NON OPINIONI: get_polymarket_events -> riporta probabilita' implicita corrente con testo della domanda [src: get_polymarket_events]. Variazione 7/30 giorni SOLO se una fonte storica esplicita identifica lo stesso mercato e outcome, le due date e valori confrontabili. Senza quella storia scrivi delta 7/30 giorni n.d. e indica il dato mancante; prezzi correnti, volume o due eventi diversi non consentono quel calcolo. Articola il gap tra news e prezzo solo quando documentato, senza inventarlo."), ('- MAI dichiarare "mercato inesistente" (o "nessun numero disponibile") senza PROVA documentata: cita nel report la QUERY ESATTA passata a get_polymarket_events e il count restituito (es. \'query "<paese> <carica> election <anno>" -> count 0\', poi \'query "<candidato> <anno>" -> count 0\'), dopo aver riprovato con almeno 2 formulazioni alternative (titolo in inglese, paese + carica, nome del candidato): i nomi propri spesso stanno solo nelle question dei sub-market. Mercato davvero inesistente = dichiarato, mai probabilita\' inventate, e segnala che il rischio non e\' prezzabile direttamente.', "- Cita query esatta, count e limiti restituiti da get_polymarket_events. Dopo almeno 2 formulazioni alternative (titolo in inglese, paese + carica, nome del candidato), zero risultati significa nessun match nelle ricerche osservate, non mercato inesistente nell'universo. Dichiara paginazione/cap/copertura parziale o ignota. Una chiamata riuscita non attesta lettura o comprensione della fonte. Mai probabilita' inventate; rischio non prezzabile direttamente senza evidenza pertinente."), ('delta_7gg, scadenza', 'delta_7gg (n.d. senza storia documentata), scadenza'), ('Ogni catalyst della top 5 etichettato PRICED / PARTIALLY PRICED / NOT YET PRICED nei prediction market.', "Etichette PRICED / PARTIALLY PRICED / NOT YET PRICED solo con evidenza pertinente al medesimo evento/mercato/outcome; altrimenti PRICING NON VALUTABILE e motivo. Non dedurre NOT YET PRICED dal solo count=0 o da probabilita' assente; nessun gap inventato per riempire la voce high conviction. Sono giudizi motivati, non fatti verificati da una chiamata riuscita."))
RED_REPLACEMENTS = (('   - beta/VaR/vol citati coincidono con quelli ufficiali? Se due specialisti danno numeri\n     INCOMPATIBILI tra loro (es. beta 0,04 e 0,9 nello stesso giro), dillo col numero vero accanto.', "   - Confronta beta/VaR/vol con le misure disponibili e le rispettive fonti, convenzioni e diagnostiche salvate. In caso di valori incompatibili segnala divergenza e limiti; non designare un numero come vero se il guardrail e' indisponibile, insufficiente o non riconciliato. Non confondere freshness dell'osservazione, esito dell'acquisizione e validita' del metodo."),)
