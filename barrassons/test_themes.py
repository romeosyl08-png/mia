import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from .cli import main, run_session
from .terminal import Terminal, terminal
from .themes import ACCENTS, apply_theme, configure_theme


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(terminal.set_theme, 'light', '#6C6FCB')
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_persona_sync_uses_only_the_appearance_endpoint(self):
        request = Mock(return_value={'theme': 'dark', 'accent': '#3F8E8A'})
        config = {'api_key': 'fixture'}
        self.assertTrue(apply_theme(config, request))
        request.assert_called_once_with(config, '/appearance', timeout=5)
        self.assertEqual((terminal.theme, terminal.accent), ('dark', '#3F8E8A'))

    def test_local_choice_is_saved_and_survives_a_restart_without_sync(self):
        config, request, save = {}, Mock(), Mock()
        configure_theme(config, 'sombre', request, save)
        configure_theme(config, 'ambre', request, save)
        saved = save.call_args.args[0]
        terminal.set_theme('light', '#6C6FCB')
        apply_theme(saved, request)
        self.assertEqual((terminal.theme, terminal.accent), ('dark', ACCENTS['ambre']))
        request.assert_not_called()

    def test_unavailable_sync_falls_back_without_disrupting_work(self):
        request = Mock(side_effect=RuntimeError('Old server'))
        terminal.set_theme('dark', ACCENTS['vert'])
        self.assertFalse(apply_theme({}, request))
        self.assertEqual((terminal.theme, terminal.accent), ('light', ACCENTS['violet']))

    def test_menu_can_keep_the_current_choice_and_reject_invalid_values(self):
        save = Mock()
        with patch('builtins.input', return_value=''):
            configure_theme({}, '', Mock(), save)
        with self.assertRaises(ValueError):
            configure_theme({}, 'unknown', Mock(), save)
        save.assert_not_called()

    def test_theme_and_help_commands_do_not_call_a_model(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict('os.environ', {'XDG_CONFIG_HOME': directory}), \
                patch('builtins.input', side_effect=['/help', '/help tout', '/theme', '3', '/theme vert', '/oops', '/exit']), \
                patch('barrassons.cli.api_request') as request, patch('barrassons.cli.save_config') as save:
            config = {}
            run_session(config, Path(directory), 'fixture')
        request.assert_not_called()
        self.assertEqual(config, {'theme': 'dark', 'accent': ACCENTS['vert']})
        self.assertEqual(save.call_count, 2)

    def test_first_launch_starts_a_session_without_model_selection_input(self):
        config = {'api_base': 'https://example.test/v1', 'api_key': 'fixture'}
        responses = [{'theme': 'dark', 'accent': ACCENTS['brique']},
                     {'data': [{'id': 'default'}], 'default_model': 'default'}, {'credits': 100}]
        with patch('sys.argv', ['barrassons']), patch('barrassons.cli.load_config', return_value=config), \
                patch('barrassons.cli.api_request', side_effect=responses), patch('barrassons.cli.save_config'), \
                patch('builtins.input') as ask, patch('barrassons.cli.run_session') as session:
            self.assertEqual(main(), 0)
        ask.assert_not_called()
        self.assertEqual(session.call_args.args[2], 'default')
        self.assertEqual((terminal.theme, terminal.accent), ('dark', ACCENTS['brique']))

    def test_themes_emit_distinct_colors_and_honor_plain_output(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True

        output = TTY()
        ui = Terminal()
        with redirect_stdout(output), patch.dict('os.environ', {'TERM': 'xterm-256color'}, clear=True):
            ui.set_theme('light', ACCENTS['violet'])
            light = ui.style('Hello', 'accent')
            ui.set_theme('dark', ACCENTS['vert'])
            dark = ui.style('Hello', 'accent')
            self.assertNotEqual(light, dark)
            self.assertIn('48;2;25;28;40', dark)
            with patch.dict('os.environ', {'NO_COLOR': '1'}):
                self.assertEqual(ui.style('Hello', 'accent'), 'Hello')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ui.style('Hello', 'accent'), 'Hello')
