"""Opt-in unverified-catalog policy and operational state readiness.

This does not register sources, grant consent, or change backend v2 verdicts.
"""
import os

from app.infrastructure import external_ai_state


def _enabled(name):
    return os.getenv(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def unverified_catalog_enabled():
    return _enabled('FOOD_CHATBOT_TEAM_UNVERIFIED_CATALOG')


def deployment_errors(*, external_enabled, conversations_enabled):
    errors = []
    if unverified_catalog_enabled():
        if conversations_enabled or (external_enabled and not _enabled('FOOD_CHATBOT_TEAM_EXTERNAL_AI')):
            errors.append('team_unverified_catalog_requires_stateless_rules')
        if os.getenv('FOOD_CHATBOT_PRODUCT_LABEL_ELIGIBLE_SOURCES', '').strip():
            errors.append('team_unverified_catalog_requires_empty_source_registry')
    if external_enabled and not external_ai_state.status()['configured']:
        errors.append('production_external_ai_persistent_state_missing')
    return errors
