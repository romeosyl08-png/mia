import io
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from .tools import DEFAULT_READ_LINES, ToolSession, execute_tool


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve() / 'project'
        self.root.mkdir()
        self.enterContext(patch.dict('os.environ', {'XDG_CONFIG_HOME': self.temp_dir.name}))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.session = ToolSession()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def run_tool(self, name, args, answers=None):
        with patch('builtins.input', side_effect=list(answers or [])) as ask:
            result = execute_tool(name, args, self.root, self.session)
        return result, ask.call_count


class ReadFileTests(ToolTestCase):
    def test_large_file_is_read_in_chunks_without_losing_lines(self):
        self.write('big.txt', ''.join(f'ligne {n}\n' for n in range(1, 3001)))
        first, _ = self.run_tool('read_file', {'path': 'big.txt'})
        self.assertEqual((first['start_line'], first['end_line'], first['total_lines']), (1, DEFAULT_READ_LINES, 3000))
        self.assertTrue(first['truncated'])
        rest, _ = self.run_tool('read_file', {'path': 'big.txt', 'offset': first['next_offset']})
        self.assertEqual((rest['start_line'], rest['end_line']), (2001, 3000))
        self.assertFalse(rest['truncated'])
        self.assertNotIn('next_offset', rest)
        self.assertEqual(first['content'] + rest['content'], (self.root / 'big.txt').read_text())

    def test_long_lines_are_split_by_the_character_budget(self):
        self.write('wide.txt', ('x' * 40_000 + '\n') * 3)
        first, _ = self.run_tool('read_file', {'path': 'wide.txt'})
        self.assertEqual(first['end_line'], 1)
        self.assertEqual(first['next_offset'], 2)

    def test_binary_and_missing_files_return_clear_errors(self):
        (self.root / 'image.bin').write_bytes(b'\x89PNG\x00\x01')
        self.assertIn('binaire', self.run_tool('read_file', {'path': 'image.bin'})[0]['error'])
        self.assertIn('introuvable', self.run_tool('read_file', {'path': 'absent.txt'})[0]['error'])

    def test_invalid_range_is_reported(self):
        self.write('a.txt', 'a\n')
        self.assertIn('entiers', self.run_tool('read_file', {'path': 'a.txt', 'offset': 'début'})[0]['error'])

    def test_paths_and_symlinks_cannot_leave_the_project(self):
        outside = Path(self.temp_dir.name) / 'secret.txt'
        outside.write_text('secret', encoding='utf-8')
        (self.root / 'link.txt').symlink_to(outside)
        for path in ('../secret.txt', 'link.txt', '/etc/passwd'):
            with self.subTest(path=path):
                result, _ = self.run_tool('read_file', {'path': path})
                self.assertIn('hors du dossier', result['error'])


class EditSafetyTests(ToolTestCase):
    def test_existing_file_cannot_be_edited_before_being_read(self):
        self.write('app.py', 'value = 1\n')
        result, asked = self.run_tool('edit_file', {'path': 'app.py', 'old_text': '1', 'new_text': '2'})
        self.assertIn('Lis le fichier', result['error'])
        self.assertEqual(asked, 0)
        self.assertEqual((self.root / 'app.py').read_text(), 'value = 1\n')

    def test_external_change_after_reading_blocks_the_edit(self):
        self.write('app.py', 'value = 1\n')
        self.run_tool('read_file', {'path': 'app.py'})
        self.write('app.py', 'value = 1\nother = 3\n')
        result, asked = self.run_tool('edit_file', {'path': 'app.py', 'old_text': 'value = 1', 'new_text': 'value = 2'})
        self.assertIn('a changé', result['error'])
        self.assertEqual(asked, 0)
        self.assertEqual((self.root / 'app.py').read_text(), 'value = 1\nother = 3\n')

    def test_successive_edits_after_one_read_are_allowed(self):
        self.write('app.py', 'a = 1\nb = 1\n')
        self.run_tool('read_file', {'path': 'app.py'})
        self.assertEqual(self.run_tool('edit_file', {'path': 'app.py', 'old_text': 'a = 1', 'new_text': 'a = 2'}, ['o'])[0]['status'], 'updated')
        self.assertEqual(self.run_tool('edit_file', {'path': 'app.py', 'old_text': 'b = 1', 'new_text': 'b = 2'}, ['o'])[0]['status'], 'updated')
        self.assertEqual((self.root / 'app.py').read_text(), 'a = 2\nb = 2\n')

    def test_overwriting_requires_a_read_but_creating_does_not(self):
        self.write('old.txt', 'contenu\n')
        self.assertIn('Lis le fichier', self.run_tool('write_file', {'path': 'old.txt', 'content': 'x'})[0]['error'])
        result, _ = self.run_tool('write_file', {'path': 'dir/new.txt', 'content': 'nouveau\n'}, ['o'])
        self.assertEqual(result['status'], 'written')
        self.assertEqual((self.root / 'dir/new.txt').read_text(), 'nouveau\n')

    def test_refused_edit_leaves_the_file_unchanged(self):
        self.write('app.py', 'value = 1\n')
        self.run_tool('read_file', {'path': 'app.py'})
        result, _ = self.run_tool('edit_file', {'path': 'app.py', 'old_text': '1', 'new_text': '2'}, ['n'])
        self.assertEqual(result['status'], 'refused')
        self.assertEqual((self.root / 'app.py').read_text(), 'value = 1\n')

    def test_ambiguous_missing_and_identical_replacements_are_rejected(self):
        self.write('app.py', 'x = 0\nx = 0\n')
        self.run_tool('read_file', {'path': 'app.py'})
        for args, message in [({'old_text': 'x = 0', 'new_text': 'x = 1'}, '2 fois'),
                              ({'old_text': 'absent', 'new_text': 'y'}, 'introuvable'),
                              ({'old_text': 'x = 0', 'new_text': 'x = 0'}, 'identique'),
                              ({'old_text': '', 'new_text': 'y'}, 'vide')]:
            with self.subTest(args=args):
                result, asked = self.run_tool('edit_file', {'path': 'app.py', **args})
                self.assertIn(message, result['error'])
                self.assertEqual(asked, 0)

    def test_replace_all_changes_every_occurrence(self):
        self.write('app.py', 'x = 0\nx = 0\ny = 0\n')
        self.run_tool('read_file', {'path': 'app.py'})
        result, _ = self.run_tool('edit_file', {'path': 'app.py', 'old_text': 'x = 0', 'new_text': 'x = 1',
                                                'replace_all': True}, ['o'])
        self.assertEqual((result['status'], result['replacements']), ('updated', 2))
        self.assertEqual((self.root / 'app.py').read_text(), 'x = 1\nx = 1\ny = 0\n')

    def test_edits_are_recorded_for_undo(self):
        from .project import load_session
        self.write('app.py', 'value = 1\n')
        self.run_tool('read_file', {'path': 'app.py'})
        self.run_tool('edit_file', {'path': 'app.py', 'old_text': '1', 'new_text': '2'}, ['o'])
        self.assertEqual(load_session(self.root)['edits'][-1]['before'], 'value = 1\n')


class PermissionTests(ToolTestCase):
    def test_always_for_files_skips_later_file_confirmations_only(self):
        result, asked = self.run_tool('write_file', {'path': 'a.txt', 'content': 'a'}, ['t'])
        self.assertEqual((result['status'], asked), ('written', 1))
        result, asked = self.run_tool('write_file', {'path': 'b.txt', 'content': 'b'})
        self.assertEqual((result['status'], asked), ('written', 0))
        result, asked = self.run_tool('run_command', {'command': 'echo ok'}, ['n'])
        self.assertEqual((result['status'], asked), ('refused', 1))

    def test_always_for_a_command_covers_only_that_exact_command(self):
        result, asked = self.run_tool('run_command', {'command': 'echo un'}, ['t'])
        self.assertEqual((result['exit_code'], asked), (0, 1))
        result, asked = self.run_tool('run_command', {'command': 'echo un'})
        self.assertEqual((result['stdout'], asked), ('un\n', 0))
        result, asked = self.run_tool('run_command', {'command': 'echo un; rm -rf x'}, ['n'])
        self.assertEqual((result['status'], asked), ('refused', 1))

    def test_always_is_not_offered_without_a_session(self):
        with patch('builtins.input', return_value='t'):
            result = execute_tool('write_file', {'path': 'a.txt', 'content': 'a'}, self.root)
        self.assertEqual(result['status'], 'refused')
        self.assertFalse((self.root / 'a.txt').exists())


class CommandTests(ToolTestCase):
    def test_exit_code_and_outputs_are_returned(self):
        result, _ = self.run_tool('run_command', {'command': 'echo out; echo err >&2; exit 3'}, ['o'])
        self.assertEqual((result['exit_code'], result['stdout'], result['stderr']), (3, 'out\n', 'err\n'))

    def test_timeout_is_reported_with_partial_output(self):
        result, _ = self.run_tool('run_command', {'command': 'echo début; sleep 5', 'timeout': 1}, ['o'])
        self.assertTrue(result['timed_out'])
        self.assertIn('1 secondes', result['error'])

    def test_invalid_arguments_never_run_anything(self):
        self.assertIn('vide', self.run_tool('run_command', {'command': '  '})[0]['error'])
        self.assertIn('entier', self.run_tool('run_command', {'command': 'echo', 'timeout': 'long'})[0]['error'])
        self.assertIn('manquant', self.run_tool('run_command', {})[0]['error'])
        self.assertIn('inconnu', self.run_tool('delete_everything', {})[0]['error'])


class SearchTests(ToolTestCase):
    def test_glob_finds_nested_and_root_files_newest_first(self):
        old = self.write('src/old.py', '')
        self.write('src/pkg/new.py', '')
        self.write('README.md', '')
        past = time.time() - 100
        os.utime(old, (past, past))
        files, _ = self.run_tool('glob', {'pattern': '**/*.py'})
        self.assertEqual(files['files'], ['src/pkg/new.py', 'src/old.py'])
        self.assertEqual(self.run_tool('glob', {'pattern': 'src/**/old.py'})[0]['files'], ['src/old.py'])
        self.assertIn('vide', self.run_tool('glob', {'pattern': ''})[0]['error'])

    def test_git_projects_respect_gitignore(self):
        if subprocess.run(['git', '--version'], capture_output=True).returncode:
            self.skipTest('Git indisponible')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.write('.gitignore', 'secret.env\nlogs/\n')
        self.write('app.py', 'TOKEN = 1\n')
        self.write('secret.env', 'TOKEN = 2\n')
        self.write('logs/run.log', 'TOKEN = 3\n')
        listed = self.run_tool('list_files', {})[0]['files']
        self.assertEqual(listed, ['.gitignore', 'app.py'])
        matches = self.run_tool('search_code', {'query': 'TOKEN'})[0]['matches']
        self.assertEqual([m['path'] for m in matches], ['app.py'])

    def test_search_supports_regex_and_case_options(self):
        self.write('a.py', 'def Hello():\n    return 42\n')
        self.assertEqual(len(self.run_tool('search_code', {'query': 'hello'})[0]['matches']), 1)
        self.assertEqual(self.run_tool('search_code', {'query': 'hello', 'case_sensitive': True})[0]['matches'], [])
        regex = self.run_tool('search_code', {'query': r'return \d+', 'regex': True})[0]['matches']
        self.assertEqual((regex[0]['path'], regex[0]['line']), ('a.py', 2))
        # Sans regex=true, les caractères spéciaux sont cherchés tels quels.
        self.assertEqual(self.run_tool('search_code', {'query': r'\d+'})[0]['matches'], [])
        self.assertIn('invalide', self.run_tool('search_code', {'query': '(', 'regex': True})[0]['error'])

    def test_search_skips_binary_files_and_virtualenvs(self):
        (self.root / 'data.bin').write_bytes(b'needle\x00\x01')
        self.write('venv/lib.py', 'needle\n')
        self.write('ok.py', 'needle\n')
        matches = self.run_tool('search_code', {'query': 'needle'})[0]['matches']
        self.assertEqual([m['path'] for m in matches], ['ok.py'])


if __name__ == '__main__':
    unittest.main()
