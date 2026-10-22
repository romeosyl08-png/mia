import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from .cli import (COMPACT_REQUEST, INIT_REQUEST, MAX_HISTORY_MESSAGES, close_pending_tool_calls, expand_mentions,
                  project_instructions, run_session, system_message, trim_history)
from .project import load_session
from .tools import ToolSession, execute_tool


def reply(**message):
    return {'choices': [{'message': {'role': 'assistant', **message}}], 'usage': {'total_tokens': 10, 'credits_charged': 1}}


def tool_call(name, args, call_id):
    return reply(content=None, tool_calls=[{'id': call_id, 'type': 'function',
                                             'function': {'name': name, 'arguments': json.dumps(args)}}])


def assert_history_is_valid(test, messages):
    """Chaque appel d'outil a exactement une réponse : sinon l'API refuse la suite."""
    calls = [call['id'] for m in messages for call in m.get('tool_calls') or []]
    answers = [m['tool_call_id'] for m in messages if m.get('role') == 'tool']
    test.assertEqual(sorted(calls), sorted(answers))


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve() / 'project'
        self.root.mkdir()
        self.enterContext(patch.dict('os.environ', {'XDG_CONFIG_HOME': self.temp_dir.name}))
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))

    def tearDown(self):
        self.temp_dir.cleanup()

    def session(self, inputs, responses, **kwargs):
        with patch('barrassons.cli.api_request', side_effect=responses) as api, \
                patch('builtins.input', side_effect=inputs):
            run_session({}, self.root, 'fixture-model', **kwargs)
        self.api = api
        return load_session(self.root).get('messages', [])


class InterruptTests(SessionTestCase):
    def test_ctrl_c_while_thinking_returns_to_the_prompt(self):
        messages = self.session(['première demande', 'deuxième demande', '/exit'],
                                [KeyboardInterrupt(), reply(content='Réponse à la deuxième')])
        self.assertIn('Interrompu', self.output.getvalue())
        self.assertEqual(self.api.call_count, 2)
        self.assertEqual(messages[-1]['content'], 'Réponse à la deuxième')
        assert_history_is_valid(self, messages)

    def test_ctrl_c_during_a_confirmation_answers_every_pending_tool_call(self):
        both = reply(content=None, tool_calls=[
            {'id': 'a', 'type': 'function', 'function': {'name': 'run_command', 'arguments': '{"command": "echo a"}'}},
            {'id': 'b', 'type': 'function', 'function': {'name': 'run_command', 'arguments': '{"command": "echo b"}'}},
        ])
        messages = self.session(['lance', KeyboardInterrupt(), 'suite', '/exit'], [both, reply(content='Prêt')])
        assert_history_is_valid(self, messages)
        interrupted = [m for m in messages if m.get('role') == 'tool']
        self.assertTrue(all('Interrompu' in m['content'] for m in interrupted))
        self.assertEqual(messages[-1]['content'], 'Prêt')

    def test_server_error_keeps_the_session_open(self):
        messages = self.session(['demande', 'encore', '/exit'],
                                [RuntimeError('Serveur indisponible'), reply(content='OK')])
        self.assertIn('Serveur indisponible', self.output.getvalue())
        self.assertEqual(messages[-1]['content'], 'OK')

    def test_malformed_server_response_keeps_the_session_open(self):
        messages = self.session(['demande', '/exit'], [{'unexpected': True}])
        self.assertIn('Réponse inattendue', self.output.getvalue())
        assert_history_is_valid(self, messages)

    def test_resume_closes_tool_calls_left_open_by_a_crash(self):
        from .project import save_session
        save_session(self.root, {'messages': [
            {'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'},
            {'role': 'assistant', 'tool_calls': [{'id': 'lost', 'function': {'name': 'run_command'}}]},
        ]})
        messages = self.session(['continue', '/exit'], [reply(content='Repris')], resume=True)
        assert_history_is_valid(self, messages)
        sent = self.api.call_args.args[2]['messages']
        self.assertIn('incertain', next(m['content'] for m in sent if m.get('tool_call_id') == 'lost'))


class CommandTests(SessionTestCase):
    def test_bang_runs_a_command_without_confirmation_or_model_call(self):
        messages = self.session(['!echo bonjour', '/exit'], [])
        self.assertEqual(self.api.call_count, 0)
        self.assertIn('bonjour', messages[-2]['content'])
        self.assertIn('echo bonjour', messages[-2]['content'])

    def test_unknown_slash_command_never_reaches_the_model(self):
        self.session(['/inexistant', '/exit'], [])
        self.assertEqual(self.api.call_count, 0)
        self.assertIn('Commande inconnue', self.output.getvalue())

    def test_init_asks_for_the_instruction_file(self):
        messages = self.session(['/init', '/exit'], [reply(content='Fichier prêt')])
        self.assertEqual([m['content'] for m in messages if m['role'] == 'user'], [INIT_REQUEST])

    def test_compact_replaces_history_with_the_summary(self):
        messages = self.session(['tâche 1', 'tâche 2', '/compact', '/exit'],
                                [reply(content='r1'), reply(content='r2'), reply(content='Résumé des tâches')])
        compact_call = self.api.call_args.args[2]
        self.assertEqual(compact_call['messages'][-1]['content'], COMPACT_REQUEST)
        self.assertNotIn('tools', compact_call)
        self.assertEqual(len(messages), 3)
        self.assertIn('Résumé des tâches', messages[1]['content'])

    def test_empty_summary_keeps_the_history(self):
        messages = self.session(['tâche', '/compact', '/exit'], [reply(content='r'), reply(content='')])
        self.assertEqual([m['content'] for m in messages[1:]], ['tâche', 'r'])

    def test_permissions_reset_requires_confirmation_again(self):
        self.session(['écris', 't', '/permissions reset', 'encore', 'n', '/exit'], [
            tool_call('write_file', {'path': 'a.txt', 'content': 'a'}, '1'), reply(content='fait'),
            tool_call('write_file', {'path': 'b.txt', 'content': 'b'}, '2'), reply(content='refusé'),
        ])
        self.assertTrue((self.root / 'a.txt').exists())
        self.assertFalse((self.root / 'b.txt').exists())

    def test_agent_edits_a_file_after_reading_it_in_the_same_turn(self):
        (self.root / 'app.py').write_text('value = 1\n', encoding='utf-8')
        self.session(['corrige', 'o', '/exit'], [
            tool_call('read_file', {'path': 'app.py'}, 'r'),
            tool_call('edit_file', {'path': 'app.py', 'old_text': '1', 'new_text': '2'}, 'e'),
            reply(content='Corrigé'),
        ])
        self.assertEqual((self.root / 'app.py').read_text(), 'value = 2\n')


class ContextTests(SessionTestCase):
    def test_project_instructions_are_loaded_in_order_and_capped(self):
        (self.root / 'AGENTS.md').write_text('Règle agents', encoding='utf-8')
        (self.root / 'BARRASSONS.md').write_text('Règle barrassons', encoding='utf-8')
        text = project_instructions(self.root)
        self.assertLess(text.index('Règle barrassons'), text.index('Règle agents'))
        (self.root / 'CLAUDE.md').write_text('x' * 50_000, encoding='utf-8')
        self.assertLess(len(project_instructions(self.root)), 21_000)
        self.assertIn('Règle agents', system_message(self.root)['content'])

    def test_instructions_are_sent_with_each_request(self):
        (self.root / 'BARRASSONS.md').write_text('Toujours utiliser pytest.', encoding='utf-8')
        self.session(['teste', '/exit'], [reply(content='ok')])
        self.assertIn('Toujours utiliser pytest.', self.api.call_args.args[2]['messages'][0]['content'])

    def test_mentions_attach_files_and_count_as_a_read(self):
        (self.root / 'src').mkdir()
        (self.root / 'src/app.py').write_text('value = 1\n', encoding='utf-8')
        session = ToolSession()
        text = expand_mentions('Corrige @src/app.py, écris à moi@example.com et ignore @../secret', self.root, session)
        self.assertIn('Contenu de src/app.py', text)
        self.assertIn('value = 1', text)
        self.assertEqual(text.count('Contenu de'), 1)
        with patch('builtins.input', return_value='o'):
            result = execute_tool('edit_file', {'path': 'src/app.py', 'old_text': '1', 'new_text': '2'}, self.root, session)
        self.assertEqual(result['status'], 'updated')

    def test_close_pending_tool_calls_is_idempotent(self):
        messages = [{'role': 'assistant', 'tool_calls': [{'id': 'x'}, {'id': 'y'}]},
                    {'role': 'tool', 'tool_call_id': 'x', 'content': 'fait'}]
        close_pending_tool_calls(messages, 'interrompu')
        close_pending_tool_calls(messages, 'interrompu')
        self.assertEqual([m.get('tool_call_id') for m in messages[1:]], ['x', 'y'])

    def test_trimmed_history_keeps_the_system_prompt_and_starts_with_a_request(self):
        messages = [{'role': 'system', 'content': 's'}]
        for index in range(30):
            messages += [{'role': 'user', 'content': f'u{index}'},
                         {'role': 'assistant', 'tool_calls': [{'id': str(index)}]},
                         {'role': 'tool', 'tool_call_id': str(index), 'content': 'r'}]
        trimmed = trim_history(messages)
        self.assertLessEqual(len(trimmed), MAX_HISTORY_MESSAGES)
        self.assertEqual(trimmed[0]['role'], 'system')
        self.assertEqual(trimmed[1]['role'], 'user')
        assert_history_is_valid(self, trimmed)


if __name__ == '__main__':
    unittest.main()
