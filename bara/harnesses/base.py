"""Execution contracts layered over the shared, durable task runner."""
from abc import ABC, abstractmethod

from chat.capabilities import request_requirements
from bara.tool_reliability import ToolRun


MUTATIONS = {'write_file', 'edit_file', 'rename_file', 'delete_file', 'create_document'}
REQUIREMENT_LABELS = {
    'file_read': 'Lecture des fichiers demandés',
    'file_create': 'Création des fichiers demandés',
    'file_edit': 'Modification des fichiers demandés',
    'file_rename': 'Renommage des fichiers demandés',
    'file_delete': 'Suppression confirmée des fichiers demandés',
    'execute': 'Exécution réussie de la commande demandée',
    'web': 'Recherche web effectuée',
}


def successful(result):
    return (
        isinstance(result, dict)
        and not any(result.get(key) for key in ('error', 'is_error', 'timed_out', 'verification_failed'))
        and result.get('status') not in {'pending_confirmation', 'pending', 'running', 'rejected', 'error'}
        and ('exit_code' not in result or result['exit_code'] == 0)
    )


class BaseHarness(ABC):
    version = 1
    tool_names = None

    @abstractmethod
    def can_handle(self, objective):
        """Select a contract locally, without a billable model call."""

    def plan(self, task):
        return ['Examiner la demande et les fichiers utiles', 'Réaliser les opérations autorisées',
                'Vérifier les opérations demandées', 'Présenter les livrables et les limites']

    def category(self, objective):
        requirements = request_requirements(objective)
        return 'research' if 'web' in requirements else 'general'

    def preflight(self, objective, workspace):
        return None

    def deliver(self, task):
        return task.result

    def initialize(self, task):
        state = task.state.setdefault('harness', {'id': self.id})
        if state.get('version', self.version) != self.version:
            raise ValueError('Unsupported harness version')
        if 'plan' in state:
            return False
        state.update(version=self.version, plan=self.plan(task), completed=[], observed={})
        # Only this task's backend events are evidence. Conversation history and
        # model-written summaries must never prove an operation was performed.
        for data in task.events.filter(kind='tool_result').order_by('id').values_list('data', flat=True):
            self.observe(task, data.get('tool'), {}, data.get('result'))
        return True

    def tools(self, schemas, mcp_map):
        if self.tool_names is None:
            return schemas, mcp_map
        schemas = [schema for schema in schemas if schema['function']['name'] in self.tool_names]
        names = {schema['function']['name'] for schema in schemas}
        return schemas, {name: remote for name, remote in mcp_map.items() if name in names}

    def execute(self, task, name, arguments, mcp_map, dispatch):
        result = dispatch(task.workspace, name, arguments, mcp_map, task.conversation_id)
        self.observe(task, name, arguments, result)
        return result

    def observe(self, task, name, arguments, result):
        state = task.state['harness']
        if isinstance(result, dict):
            if result.get('uncertain_effect') or result.get('verification_failed'):
                state['uncertain_effect'] = True
            if result.get('status') == 'rejected':
                state['rejected'] = True
        if not successful(result):
            return
        run = ToolRun(task.objective)
        run.completed.update(state.get('completed', []))
        run.observed = {kind: set(names) for kind, names in state.get('observed', {}).items()}
        run.record(name, arguments, result)
        state['completed'] = sorted(run.completed)
        state['observed'] = {kind: sorted(names) for kind, names in run.observed.items()}
        if name in MUTATIONS and (name not in {'write_file', 'edit_file', 'rename_file', 'create_document'}
                                  or result.get('verified') is True):
            state['changed'] = True

    def verify(self, task):
        state = task.state['harness']
        required = request_requirements(task.objective) & REQUIREMENT_LABELS.keys()
        checks = {name: name in state.get('completed', []) for name in sorted(required)}
        missing = [REQUIREMENT_LABELS[name] for name, passed in checks.items() if not passed]
        if state.get('uncertain_effect') or task.state.get('blocked_effects'):
            checks['effects_certain'] = False
            missing.append('Résultat d’une action incertain : examiner les fichiers et le journal')
        if state.get('rejected'):
            checks['confirmation_accepted'] = False
            missing.append('Une action a été refusée : revoir la demande avant de reprendre')
        return {'status': 'passed' if all(checks.values()) else 'incomplete', 'checks': checks,
                'missing': missing, 'retryable': not state.get('rejected') and
                not state.get('uncertain_effect') and not task.state.get('blocked_effects'),
                'scope': 'technical'}

    def prompt(self, task):
        state = task.state['harness']
        text = '\nHarnais : ' + self.name + '. Plan : ' + ' → '.join(state['plan']) + '.'
        text += '\nLa tâche ne sera terminée qu’après contrôle des preuves d’exécution par 2AI.'
        report = state.get('verification', {})
        if report.get('status') == 'incomplete':
            text += '\nLa dernière réponse ne suffit pas. Contrôles manquants : ' + '; '.join(report['missing'])
            text += '. Corrige ou vérifie avec les outils autorisés ; ne répète pas une action incertaine ou refusée.'
        return text


class GeneralHarness(BaseHarness):
    id = 'general'
    name = 'Bara général'

    def can_handle(self, objective):
        return True
