import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import generation_manifest as gm, run_generation as run, local_video_cli as cli, lululab_image_generate as image

class RecoveryTests(unittest.TestCase):
    def setup_manifest(self, root):
        m=gm.command_init(type('Args',(),{'output_root':str(root),'task_id':'task','reuse':False})())
        data=gm.load_manifest(m)
        board=root/'board.png';board.write_bytes(b'board')
        seg={'segmentId':1,'storyboardIds':[1],'creatorIds':[],'duration':10,'prompt':'test','globalStart':0,'globalEnd':10}
        prompts=root/'prompts.json';prompts.write_text(json.dumps({'videoPrompts':{'segments':[seg]}}))
        data['videoPrompts']={'file':str(prompts),'segments':[seg]}
        data['userConfig']['duration']=10
        data['storyboards']['generation']=[{'storyboardId':1,'file':str(board)}]
        gm.save_manifest(m,data)
        return m,board

    def invoke(self,m,board,submit,poll):
        with ExitStack() as s:
            s.enter_context(patch.object(sys,'argv',['run_generation.py','--manifest',str(m),'--skip-concat','--generation-approved']))
            s.enter_context(patch.object(gm,'validate_prompt_record'))
            s.enter_context(patch.object(cli,'lululab_cli_available',return_value=True))
            s.enter_context(patch.object(run.reference_audit,'audit',return_value={'passed':True,'warnings':[]}))
            s.enter_context(patch.object(run.reference_audit,'validate_product_references',return_value=[]))
            s.enter_context(patch.object(run,'prepare_references',return_value=[str(board)]))
            st=s.enter_context(patch.object(cli,'submit_video',side_effect=submit))
            s.enter_context(patch.object(cli,'poll_task',side_effect=poll))
            try: run.main()
            except SystemExit: pass
            return st.call_count

    def test_unknown_submit_is_durable_and_cannot_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            m,board=self.setup_manifest(Path(td))
            self.assertEqual(self.invoke(m,board,cli.LocalVideoCliError('connection lost'),AssertionError('no poll')),1)
            self.assertEqual(gm.load_manifest(m)['videos'][0]['status'],'submit_outcome_unknown')
            self.assertEqual(self.invoke(m,board,AssertionError('no resubmit'),AssertionError('no poll')),0)

    def test_poll_timeout_resumes_original_task(self):
        with tempfile.TemporaryDirectory() as td:
            m,board=self.setup_manifest(Path(td))
            self.assertEqual(self.invoke(m,board,lambda *a,**k:'v1',cli.LocalVideoCliError('timeout')),1)
            saved=gm.load_manifest(m)['videos'][0]
            self.assertEqual((saved['status'],saved['taskId']),('querying','v1'))
            self.assertEqual(self.invoke(m,board,AssertionError('no resubmit'),cli.LocalVideoCliError('timeout')),0)

    def test_terminal_failure_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as td:
            m,board=self.setup_manifest(Path(td))
            gm.set_video(m,1,'terminal_failed',taskId='v1',provider='lululab_cli')
            self.assertEqual(self.invoke(m,board,AssertionError('no resubmit'),AssertionError('no poll')),0)

    def test_video_upload_preserves_three_reference_types(self):
        with tempfile.TemporaryDirectory() as td:
            files=[Path(td)/name for name in ['board.png','product.jpg','creator.png']]
            for f in files:f.write_bytes(b'image')
            results=[{'url':'https://example.com/'+f.name,'expiresAt':'expiry'} for f in files]+[{'id':'v1'}]
            with patch.object(cli,'run_lululab_cli',side_effect=results) as calls:
                self.assertEqual(cli.submit_video('lululab_cli','seedance-2-mini','prompt',9,reference_files=files),'v1')
            payload=json.loads(calls.call_args.args[0][-1])
            self.assertEqual([x['url'] for x in payload['referenceImages']],['https://example.com/'+f.name for f in files])
            self.assertTrue(all(x['expiredAt']=='expiry' for x in payload['referenceImages']))
            self.assertEqual(cli.media({'status':'Succeeded','input':{'url':'https://example.com/source'},'output':{'video':{'url':'https://example.com/result.mp4'}}})['url'],'https://example.com/result.mp4')
