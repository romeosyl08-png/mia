"""Deterministic harness selection; no extra agents or provider calls."""
import shlex

from .base import GeneralHarness
from .code import CodeHarness, DEVELOPMENT_COMMANDS
from .documents import DocumentsHarness
from .research import ResearchHarness


HARNESSES = {harness.id: harness for harness in (ResearchHarness(), DocumentsHarness(), CodeHarness(), GeneralHarness())}


def select_harness(objective, selection='auto'):
    if selection == 'auto':
        return next(harness for harness in HARNESSES.values() if harness.can_handle(objective))
    if selection not in HARNESSES:
        raise ValueError('Harnais inconnu.')
    return HARNESSES[selection]


def configure_harness(objective, selection='auto', command=''):
    harness = select_harness(objective, selection)
    config = {'id': harness.id}
    if command:
        if harness.id != 'code':
            raise ValueError('Choisis Bara Code pour définir une commande de contrôle.')
        try:
            args = shlex.split(command)
        except ValueError:
            raise ValueError('Commande de contrôle invalide : vérifie les guillemets.') from None
        if len(command) > 1000 or not args or len(args) > 32 or args[0] not in DEVELOPMENT_COMMANDS or '\x00' in command:
            raise ValueError('Commande de contrôle invalide. Utilise python, python3, pytest, git, npm ou node.')
        config['verification_command'] = args
    return config


def task_harness(task):
    config = task.state.get('harness', {})
    return select_harness(task.objective, config.get('id', 'auto'))
