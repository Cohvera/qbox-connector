import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from pipeline import capture, digest, iso, normalize, PipelineError, run, write
from seal_backup import seal
from odbc_reader import read_projects


def row(**changes):
    return dict(source_id='r1',number='123',customer='Klant',description='Project',planned='',status_label='07 - In Uitvoering',active='1',closed='0',is_current='1',revision='2',**changes)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.source.mkdir()
        self.work=self.root/'work';self.work.mkdir()
        (self.source/'PROJ.fic').write_bytes(b'test fixture only')
        self.config=dict(source_dir=str(self.source),work_dir=str(self.work),required_files=['PROJ.fic'],stability_seconds=1,reader={},_path=str(self.root/'config.json'))
        self.observed=iso(time.time()-60)
        seal(self.config,self.observed)
    def tearDown(self):self.tmp.cleanup()
    def test_capture_preserves_backup_age_and_checks_hashes(self):
        with patch('pipeline.time.sleep'):
            folder, observed=capture(self.config,self.work)
        self.assertEqual(digest(folder/'PROJ.fic'),digest(self.source/'PROJ.fic'))
        self.assertEqual(iso(observed),self.observed)
        (self.source/'PROJ.fic').write_bytes(b'changed')
        with patch('pipeline.time.sleep'),self.assertRaises(PipelineError):capture(self.config,self.work)
    def test_unstable_missing_stale_and_symlink_sources_are_rejected(self):
        with patch('pipeline.time.sleep',side_effect=lambda _: (self.source/'PROJ.fic').write_bytes(b'new')),self.assertRaises(PipelineError):
            capture(self.config,self.work)
        seal(self.config,iso(time.time()-90000))
        with self.assertRaises(PipelineError):capture(self.config,self.work)
        (self.source/'PROJ.fic').unlink()
        (self.root/'other').write_bytes(b'outside')
        (self.source/'PROJ.fic').symlink_to(self.root/'other')
        with self.assertRaises(PipelineError):seal(self.config,self.observed)
    def test_completion_receipt_is_required(self):
        (self.source/'backup-complete.json').unlink()
        with self.assertRaises(FileNotFoundError):capture(self.config,self.work)
    def test_mapping_preserves_closed_flag_and_requires_unique_current_revision(self):
        r=row();r['closed']='1'
        payload=normalize([r],time.time()-60,'batch','a'*64)
        self.assertTrue(payload['projects'][0]['closed'])
        self.assertEqual(payload['source_kind'],'qbox_capture')
        with self.assertRaises(PipelineError):normalize([r,r],time.time(),'batch','a'*64)
        r['closed']=None
        with self.assertRaises(PipelineError):normalize([r],time.time(),'batch','a'*64)
    def test_pipeline_confirmation_and_failure_keep_last_good(self):
        def reader(args,**kwargs):
            folder=Path(args[args.index('--read-capture')+1])
            write(folder/'reader.json',[row()])
        def accept(config,payload):
            return {'accepted':True,'count':1,'snapshotId':payload['snapshot_id'],'companyCode':'TOMME'}
        with patch('pipeline.time.sleep'),patch('pipeline.subprocess.run',side_effect=reader),patch('pipeline.publish',side_effect=accept):
            result=run(self.config)
        self.assertEqual(result['count'],1)
        previous=(self.work/'last-success.json').read_bytes()
        self.assertEqual(list((self.work/'captures').iterdir()),[])
        with patch('pipeline.time.sleep'),patch('pipeline.subprocess.run',side_effect=reader),patch('pipeline.publish',side_effect=OSError('transport failed')),self.assertRaises(OSError):
            run(self.config)
        self.assertEqual((self.work/'last-success.json').read_bytes(),previous)
        self.assertEqual(json.loads((self.work/'status.json').read_text())['stage'],'failed')
    def test_dry_run_does_not_publish_or_mark_success(self):
        def reader(args,**kwargs):write(Path(args[args.index('--read-capture')+1])/'reader.json',[row()])
        with patch('pipeline.time.sleep'),patch('pipeline.subprocess.run',side_effect=reader),patch('pipeline.publish') as publish:
            self.assertTrue(run(self.config,True)['validated'])
            publish.assert_not_called()
        self.assertFalse((self.work/'last-success.json').exists())
    def test_odbc_rejects_network_target_and_unconfigured_query(self):
        for template,query in [('DSN=remote;RepFic={capture}','SELECT x'),('RepFic={capture};Server Name=host','SELECT x'),('RepFic={capture}','DELETE FROM PROJ'),('RepFic={capture}','SELECT REPLACE_FIELD FROM PROJ')]:
            with self.assertRaises(ValueError):read_projects({'connection_template':template,'projects_sql':query},self.work)

if __name__=='__main__':unittest.main()
