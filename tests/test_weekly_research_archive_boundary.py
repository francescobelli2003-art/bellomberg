"""Archived weekly runs permit explicit saved-artifact delivery, never new AI/Excel."""
from hashlib import sha256
from pathlib import Path

import pytest

from test_cablaggio_consigliere_multi import run_offline
from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents import weekly_lifecycle as lifecycle
from bellomberg.agents.specialists.base import Blackboard
from bellomberg.core import current_facts
from bellomberg.storage.memory_db import MemoryDB
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked


def archived_store(observed):
    db = MemoryDB()
    contract = observed.native_weekly_contract(analysis_mode=None)
    # This archive predates note capture and evidence follow-up, not just research mode.
    for key in ('research_notes_policy', 'evidence_followup_policy', 'evidence_followup_as_of'):
        contract.pop(key)
    store = lifecycle.create_run(db, {'n_positions': 0, 'positions': []}, cm.mandato_o_esci(), contract, 'it')
    assert not current_facts.research_notes_enabled(store.context['contract'])
    assert store.get(current_facts.RESEARCH_NOTES_STAGE) is None
    return store


@pytest.mark.parametrize('finalized', [False, True])
def test_legacy_analytical_resume_is_rejected_before_claim_and_keeps_state(run_offline, monkeypatch, finalized):
    store = archived_store(run_offline)
    if finalized:
        store.complete('decisions_finalized', {'decisions': []})
    before = store._row()
    assert store.status()['resume_available'] is False
    assert 'archivio' in store.status()['blocked_reason']
    monkeypatch.setattr(cm, '_run_multi_agent', lambda *a, **k: pytest.fail('Legacy analytic dispatch'))
    monkeypatch.setattr(lifecycle, 'recover_delivery', lambda *a, **k: pytest.fail('Implicit archive delivery'))
    for _ in range(2):
        with pytest.raises(WeeklyRunBlocked, match='archivio'):
            cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
        assert store._row() == before
    assert run_offline.sondati == [] and run_offline.inviati == []


def test_legacy_delivery_without_a_saved_memo_is_rejected_without_mutation(run_offline):
    store = archived_store(run_offline)
    before = store._row()
    with pytest.raises(WeeklyRunBlocked, match='Memo validato assente'):
        cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=False)
    assert store._row() == before


def test_legacy_saved_pdf_recovers_twice_without_ai_or_workbook(run_offline, monkeypatch, tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    from bellomberg.valuation import dcf_engine, preparation_service
    store = archived_store(run_offline)
    board = Blackboard(memory_db=store.db, memo_id=store.memo_id, run_id=store.run_id)
    lifecycle.bind_blackboard(board, store)
    memo = '# Archived synthetic weekly memo\n\n' + ('Research retained with explicit uncertainty. ' * 45)
    usage = {'complete': True, 'stop_reason': 'end_turn'}
    store.complete('memo_validated', {'memo': memo, 'capo_usage': usage}, board)
    store.complete('decisions_finalized', {'decisions': []}, board)
    markdown = Path(cm.RESEARCH_NOTES_DIR) / ('bellomberg_memo_' + str(store.memo_id) + '.md')
    lifecycle.write_frozen_text(markdown, memo)
    pdf = Path(cm.REPORT_DIR) / 'archived-synthetic.pdf'
    canvas = Canvas(str(pdf))
    canvas.drawString(30, 700, 'Archived synthetic weekly memo')
    canvas.save()
    delivery = lifecycle.build_weekly_delivery(board, roots=[cm.REPORT_DIR, cm.MODELS_DIR])
    bundle = lifecycle.preserve_delivery_bundle(store, board, md_path=str(markdown), pdf_path=str(pdf),
        appendix_path=None, delivery=delivery, allowed_roots=[cm.REPORT_DIR, cm.MODELS_DIR, cm.RESEARCH_NOTES_DIR])
    original_context = store._row()['context_json']
    original_checkpoints = {stage: store.get(stage) for stage in ('memo_validated', 'decisions_finalized')}
    for owner, name in ((cm, '_run_multi_agent'), (cm, 'run_capo'), (dcf_engine, 'generate_valuation'),
                        (preparation_service, 'collect_and_prepare')):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail('Archive recovery invoked AI/workbook'))
    for path in (pdf, markdown):
        assert path.resolve().is_relative_to(tmp_path.resolve())
        path.unlink()
    for _ in range(2):
        result = cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=False)
        assert result['status'] == 'completed'
        assert store._row()['context_json'] == original_context
        assert {stage: store.get(stage) for stage in original_checkpoints} == original_checkpoints
        assert all(sha256(Path(row['path']).read_bytes()).hexdigest() == row['sha256'] for row in bundle['artifacts'])
    assert run_offline.sondati == [] and run_offline.inviati == []
    assert not list(tmp_path.rglob('*.xlsx'))


@pytest.mark.parametrize('workbook_trace', ['valuation_results', 'valuation_attempts', 'valuation_generations', 'artifact_bundle'])
def test_research_delivery_rejects_workbook_history_before_restoring_artifacts(run_offline, monkeypatch, tmp_path, workbook_trace):
    db = MemoryDB()
    store = lifecycle.create_run(db, {'n_positions': 0, 'positions': []}, cm.mandato_o_esci(),
        run_offline.native_weekly_contract(), 'it')
    # Match native acceptance before testing the later workbook-history boundary.
    notes = current_facts.freeze_research_notes(store, capture=True)
    assert notes['policy'] == current_facts.RESEARCH_NOTES_POLICY
    assert store.get(current_facts.RESEARCH_NOTES_STAGE) == notes
    monkeypatch.setattr(current_facts, 'capture_research_notes', lambda *a, **k:
                        pytest.fail('Research delivery recaptured live notes'))
    board = Blackboard(memory_db=db, memo_id=store.memo_id, run_id=store.run_id)
    lifecycle.bind_blackboard(board, store)
    if workbook_trace == 'artifact_bundle':
        store.complete('artifact_bundle', {'artifacts': [{'kind': 'Excel', 'path': str(tmp_path / 'uncreated.xlsx')}]})
    else:
        setattr(board, workbook_trace, {'SYNTH': {'generation_id': 'unexpected'}}
                if workbook_trace == 'valuation_results' else [{'generation_id': 'unexpected'}])
    store.complete('memo_validated', {'memo': 'Research memo with explicit uncertainty. ' * 45,
        'capo_usage': {'complete': True, 'stop_reason': 'end_turn'}}, board)
    monkeypatch.setattr(lifecycle, 'restore_delivery_bundle', lambda *a, **k:
                        pytest.fail('Research restored artifacts before rejecting workbook history'))
    with pytest.raises(WeeklyRunBlocked, match='workbook activity'):
        cm.run_multi_agent(resume_memo_id=store.memo_id, delivery_only=True, send_email=False)
    assert store.get(current_facts.RESEARCH_NOTES_STAGE) == notes
    assert not list(tmp_path.rglob('*.xlsx'))


def test_research_manifest_rejects_a_generation_without_result_or_attempt():
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    board = Blackboard()
    board.analysis_mode = RESEARCH_ANALYSIS_MODE
    board.valuation_generations = [{'generation_id': 'unexpected'}]
    with pytest.raises(WeeklyRunBlocked, match='workbook activity'):
        lifecycle.build_weekly_delivery(board, roots=[])
