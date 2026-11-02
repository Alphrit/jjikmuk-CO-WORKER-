"""Evaluation negative controls should not demand RAG or hide external usage."""
from evaluation.run_live_usability_eval import source_expectation_failures


def test_out_of_domain_negative_control_is_allowed_to_use_rules():
    assert source_expectation_failures({'sources': [], 'answer_source': 'rules',
        'external_ai_processing': {'used': False}}, [], {}, 'generated') == []


def test_negative_control_still_rejects_external_processing():
    assert source_expectation_failures({'sources': [], 'answer_source': 'rules',
        'external_ai_processing': {'used': True}}, [], {}, 'generated') == ['unnecessary_external_call']


def test_positive_source_requires_rag_and_exact_citation():
    assert source_expectation_failures({'sources': [], 'answer_source': 'rules'},
        ['doc'], {'doc': 'citation'}, 'generated') == ['source_mismatch', 'rag_not_used']


def test_skipped_source_expectation_does_not_require_a_source():
    assert source_expectation_failures({'sources': [], 'answer_source': 'rules'}, None, {}, 'generated') == []
