from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def check_comparison_limits(app_configs, **kwargs):
    errors = []
    positive = ('COMPARISON_MAX_OUTPUT_TOKENS', 'COMPARISON_MAX_PROMPT_CHARS', 'COMPARISON_MAX_CREDITS',
                'COMPARISON_AUTOMATIC_MAX_CREDITS', 'COMPARISON_RATE_LIMIT', 'EVALUATION_VOTE_RATE_LIMIT',
                'EVALUATION_RETENTION_DAYS', 'EVALUATION_ANALYSIS_MAX_VOTES')
    for name in positive:
        if getattr(settings, name) <= 0:
            errors.append(Error(f'{name} doit être strictement positif.', id='evaluations.E001'))
    if settings.COMPARISON_MAX_OUTPUT_TOKENS > 8192 or settings.COMPARISON_STALE_SECONDS < 300:
        errors.append(Error('Sortie limitée à 8192 tokens et récupération après au moins 300 secondes.', id='evaluations.E002'))
    if settings.COMPARISON_MAX_CANDIDATES < 2 or not 0.05 <= settings.COMPARISON_EXPLORATION_RATE <= 1:
        errors.append(Error('Au moins deux candidats et un taux d’exploration entre 0,05 et 1 sont requis.', id='evaluations.E003'))
    if settings.COMPARISON_ENABLED and not settings.COMPARISON_PROVIDER_ALLOWLIST:
        errors.append(Warning('Aucun fournisseur autorisé après revue contractuelle : les comparaisons sont indisponibles.', id='evaluations.W001'))
    if settings.EVALUATION_REWARDS_ENABLED and settings.EVALUATION_GLOBAL_REWARD_BUDGET <= 0:
        errors.append(Warning('Budget de récompenses nul : aucun bonus ne pourra être crédité.', id='evaluations.W002'))
    return errors
