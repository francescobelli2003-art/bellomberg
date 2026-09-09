from bellomberg.agents import chat_engine


def test_specialists_can_deliver_complete_deep_analysis_without_word_ceiling():
    for role in chat_engine.SYSTEM_PROMPTS_BASE:
        prompt = chat_engine._build_system(role, mandato=None, errore_mandato='fixture: non configurato')
        assert 'Default 200-400 parole' not in prompt, role
        assert 'Non aggiungere altre sezioni' not in prompt, role
        assert 'approfondita' in prompt.lower(), role
