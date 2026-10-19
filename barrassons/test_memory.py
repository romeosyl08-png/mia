import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from .cli import run_session
from .memory import configure_link, load_link, remember
from .project import load_session, save_session


class LinkedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        env = patch.dict('os.environ', {'XDG_CONFIG_HOME': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.config = {'api_base': 'https://example.test/v1', 'api_key': 'synthetic-key'}
        self.catalog = {'personal': {'available': True}, 'workspaces': [
            {'id': 42, 'title': 'Project', 'available': True, 'can_write': True},
        ]}
        self.request = Mock(return_value=self.catalog)

    def test_links_are_opt_in_project_and_connection_scoped_and_preserve_session(self):
        self.assertIsNone(load_link(self.root, self.config))
        save_session(self.root, {'messages': [{'role': 'user', 'content': 'Keep this'}]})
        configure_link(self.config, self.root, 'both 42', self.request)
        self.assertEqual(load_link(self.root, self.config), {'personal': True, 'workspace_id': 42})
        self.assertIsNone(load_link(self.root / 'other', self.config))
        self.assertIsNone(load_link(self.root, {**self.config, 'api_base': 'https://other.test/v1'}))
        self.assertIsNone(load_link(self.root, {**self.config, 'api_key': 'other-key'}))
        state = load_session(self.root)
        self.assertEqual(state['messages'][0]['content'], 'Keep this')
        self.assertNotIn('synthetic-key', str(state))
        configure_link(self.config, self.root, 'off', self.request)
        self.assertIsNone(load_link(self.root, self.config))
        self.assertEqual(load_session(self.root)['messages'], state['messages'])

    def test_unavailable_memory_never_creates_a_link(self):
        self.request.return_value = {'personal': {'available': False}, 'workspaces': []}
        for choice in ('chat', 'bara 42', 'both 42', 'bara True', 'bara -1', 'bara 999999999999999999999'):
            with self.assertRaises(ValueError):
                configure_link(self.config, self.root, choice, self.request)
        self.assertIsNone(load_link(self.root, self.config))

    def test_remember_requires_confirmation_and_uses_current_revision_without_local_content_copy(self):
        configure_link(self.config, self.root, 'both 42', self.request)
        current = {'name': 'Project', 'content': 'PRIVATE_SHARED_MEMORY', 'revision': 'current', 'can_write': True}
        self.request.return_value = current
        remember(self.config, self.root, 'Use pytest', self.request, lambda _: False)
        self.assertEqual(self.request.call_args.args[1], '/memory?target=workspace&workspace_id=42')
        self.request.reset_mock()
        remember(self.config, self.root, 'Use pytest', self.request, lambda _: True)
        self.assertEqual(self.request.call_args.args[2], {
            'target': 'workspace', 'workspace_id': 42, 'content': 'Use pytest', 'revision': 'current',
        })
        self.assertNotIn('PRIVATE_SHARED_MEMORY', str(load_session(self.root)))
        self.assertNotIn('Use pytest', str(load_session(self.root)))
        self.request.return_value = {**current, 'can_write': False}
        self.request.reset_mock()
        with self.assertRaises(ValueError):
            remember(self.config, self.root, 'Use pytest', self.request, lambda _: True)
        self.assertEqual(self.request.call_count, 1)

    def test_session_sends_link_selection_and_off_takes_effect_without_saving_shared_content(self):
        configure_link(self.config, self.root, 'chat', self.request)
        reply = {'choices': [{'message': {'role': 'assistant', 'content': 'Done'}}]}
        with patch('barrassons.cli.api_request', return_value=reply) as request, \
                patch('builtins.input', side_effect=['First', '/memory off', 'Second', '/exit']):
            run_session(self.config, self.root, 'fixture')
        self.assertEqual(request.call_args_list[0].args[2]['memory'], {'personal': True, 'workspace_id': None})
        self.assertNotIn('memory', request.call_args_list[1].args[2])
        self.assertIsNone(load_link(self.root, self.config))

    def test_personal_remember_and_rejected_confirmation_never_post(self):
        configure_link(self.config, self.root, 'chat', self.request)
        self.request.return_value = {'name': 'Personal', 'content': '', 'revision': 'current', 'can_write': True}
        self.request.reset_mock()
        remember(self.config, self.root, 'Speak French', self.request, lambda _: False)
        self.request.assert_called_once_with(self.config, '/memory?target=personal')
        remember(self.config, self.root, 'Speak French', self.request, lambda _: True)
        self.assertEqual(self.request.call_args.args[2]['target'], 'personal')
