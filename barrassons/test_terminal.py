import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from .terminal import Terminal, clean


class TerminalTests(unittest.TestCase):
    def test_plain_output_has_no_terminal_escape_codes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            ui = Terminal()
            ui.welcome('/tmp/project', 'test-model', 'main', 'https://example.test/v1')
            ui.assistant('Une réponse.\n```python\nprint(1)\n```')
            ui.status('test-model', tokens=10, credits=2, balance=100)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('100 jetons disponibles', output.getvalue())
        self.assertIn('2 jetons utilisés', output.getvalue())
        self.assertIn('10 jetons de texte', output.getvalue())

    def test_untrusted_text_cannot_inject_terminal_controls(self):
        self.assertEqual(clean('hello\x1b[31mworld\x1b[0m\x1b]0;title\x07\x00'), 'helloworld')

    def test_no_color_is_honored_on_an_interactive_terminal(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        output = TTY()
        with redirect_stdout(output), patch.dict('os.environ', {'NO_COLOR': '1'}):
            Terminal().assistant('Une réponse')
        self.assertNotIn('\x1b', output.getvalue())

    def test_permission_panels_show_the_end_of_a_long_command(self):
        output = io.StringIO()
        command = 'python3 check.py ' + '--label example ' * 40 + '--output final-file.txt'
        with redirect_stdout(output), patch.object(Terminal, 'width', property(lambda _: 50)):
            Terminal().panel('Autorisation', [command], wrap=True)
        self.assertIn('final-file.txt', output.getvalue())
        self.assertNotIn('…', output.getvalue())

    def test_multiline_prompt_is_preserved_as_one_request(self):
        with patch('builtins.input', side_effect=['Explique\\', 'ce code']), redirect_stdout(io.StringIO()):
            self.assertEqual(Terminal().request(), 'Explique\nce code')

    def test_busy_preserves_the_original_failure(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                with Terminal().busy('Analyse…'):
                    raise RuntimeError('failure')
