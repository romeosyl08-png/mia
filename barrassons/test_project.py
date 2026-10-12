import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from .project import load_session, save_session, record_edit, undo_last, make_worktree, git


class ProjectTests(unittest.TestCase):
    def test_configured_verification_repairs_failure_before_completion(self):
        import json
        from .cli import run_session
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'XDG_CONFIG_HOME':d}):
            root = Path(d)/'repo';root.mkdir()
            (root/'check.py').write_text('from value import x\nassert x == 2\nprint("PASS")')
            def response(message):
                return {'choices':[{'message':{'role':'assistant',**message}}]}
            def call(name,args,id):
                return {'tool_calls':[{'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
            outputs = [response(call('write_file',{'path':'value.py','content':'x = 1\n'},'write')),
                       response({'content':'Terminé'}),
                       response(call('edit_file',{'path':'value.py','old_text':'x = 1','new_text':'x = 2'},'fix')),
                       response({'content':'Corrigé'})]
            with patch('barrassons.cli.api_request',side_effect=outputs), patch('builtins.input',side_effect=['corrige','y','y','y','y','/exit']), patch('builtins.print'):
                run_session({},root,'fixture',test_command='python3 check.py')
            self.assertEqual((root/'value.py').read_text(),'x = 2\n')
            messages = load_session(root)['messages']
            checks = [m['content'] for m in messages if m['role']=='user' and 'Résultat réel des tests' in m['content']]
            self.assertEqual(len(checks),2)
            self.assertIn('"exit_code": 1',checks[0])
            self.assertIn('"exit_code": 0',checks[1])
    def test_undo_preserves_later_user_changes(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'XDG_CONFIG_HOME':d}):
            root = Path(d) / 'repo'; root.mkdir()
            file = root / 'a.py'; file.write_text('new')
            record_edit(root,'a.py','old','new')
            file.write_text('user change')
            self.assertIn('error',undo_last(root,lambda _:True))
            self.assertEqual(file.read_text(),'user change')
            file.write_text('new')
            self.assertEqual(undo_last(root,lambda _:True)['status'],'reverted')
            self.assertEqual(file.read_text(),'old')

    def test_sessions_are_private_and_independent(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'XDG_CONFIG_HOME':d}):
            root = Path(d) / 'repo'
            save_session(root,{'messages':[{'role':'user','content':'hello'}]})
            self.assertEqual(load_session(root)['messages'][0]['content'],'hello')
            self.assertEqual(load_session(Path(d)/'other'),{})
            self.assertEqual(next((Path(d)/'barrassons/sessions').glob('*.json')).stat().st_mode & 0o777,0o600)

    def test_worktree_keeps_original_branch_unchanged(self):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'XDG_CONFIG_HOME':d}):
            root = Path(d) / 'repo'; root.mkdir()
            subprocess.run(['git','init',str(root)],capture_output=True,check=True)
            (root/'hello.py').write_text('print(1)')
            git(root,'add','hello.py')
            git(root,'-c','user.name=Tests','-c','user.email=tests@example.test','commit','-m','fixture')
            branch = git(root,'branch','--show-current')
            isolated = make_worktree(root)
            (isolated/'hello.py').write_text('print(2)')
            self.assertEqual((root/'hello.py').read_text(),'print(1)')
            self.assertEqual(git(root,'branch','--show-current'),branch)
