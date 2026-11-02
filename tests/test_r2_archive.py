import io
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError
from r2_archive import R2Archive, StorageError, user_key
from web_backend import restore_raw_from_database


class InMemoryS3:
    def __init__(self):
        self.objects = {}
    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code':'NoSuchKey','Message':'Not found'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key])}
    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
    def upload_file(self, Filename, Bucket, Key):
        self.objects[Key] = Path(Filename).read_bytes()
    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])
    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        entries = [{'Key': k} for k in self.objects if k.startswith(Prefix)]
        return {'Contents': entries[:MaxKeys], 'IsTruncated': False}


class R2ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.fake = InMemoryS3()
        self.archive = R2Archive.__new__(R2Archive)
        self.archive.bucket = 'turbo-leads-archive'
        self.archive.prefix = 'test-v1'
        self.archive.client = self.fake
        self.archive._lock = threading.RLock()

    def create_db(self, path, count):
        with sqlite3.connect(str(path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS searches (query TEXT, status TEXT)')
            db.execute('DELETE FROM searches')
            for i in range(count):
                db.execute('INSERT INTO searches VALUES (?,?)', (f'query{i}', 'done'))
            db.execute('CREATE TABLE IF NOT EXISTS places (name TEXT, categories TEXT, address TEXT, phone TEXT, phone_intl TEXT, website TEXT, email TEXT, place_id TEXT, query TEXT)')
            db.execute('DELETE FROM places')
            db.execute("INSERT INTO places VALUES ('Autonoleggio Uno','Car rental agency','Via Roma, Lecce','','+39 1234','','','id1','autonoleggio in Lecce, LE')")

    def test_saved_db_restores_and_rebuilds_csv(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td)/'progress.sqlite3'
            self.create_db(db, 3)
            manifest = self.archive.save_checkpoint('pipps','job_one',db,
                {'cities':[{'name':'Lecce','prov':'LE'}],
                 'categories':['autonoleggio'], 'search_count':10})
            self.assertEqual(manifest['id'], 'job_one')
            db.unlink()
            restored = self.archive.restore_checkpoint('pipps','job_one',db)
            self.assertEqual(restored['search_count'], 10)
            with sqlite3.connect(str(db)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0],3)
            raw = Path(td)/'originali.csv'
            self.assertEqual(restore_raw_from_database(db, raw), 1)
            self.assertIn('Autonoleggio Uno',raw.read_text())

    def test_user_partition_and_preferences(self):
        self.archive.save_profile('pipps',{'p_manual':'autonoleggio'})
        self.archive.save_profile('amico',{'p_manual':'palestre'})
        self.assertEqual(self.archive.load_profile('pipps')['p_manual'], 'autonoleggio')
        self.assertEqual(self.archive.load_profile('amico')['p_manual'], 'palestre')
        self.assertNotEqual(user_key('pipps'),user_key('amico'))

    def test_snapshot_not_lost_if_manifest_upload_fails(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td)/'progress.sqlite3'
            self.create_db(db, 2)
            old = self.archive.save_checkpoint('pipps','job_2',db,{'completed':2})
            self.create_db(db, 4)
            with patch.object(self.archive, '_put_json', side_effect=StorageError('Simulated outage')):
                with self.assertRaises(StorageError):
                    self.archive.save_checkpoint('pipps','job_2',db,{'completed':4})
            saved = self.archive.get_manifest('pipps','job_2')
            self.assertEqual(saved['db_key'],old['db_key'])
            self.archive.restore_checkpoint('pipps','job_2',db)
            with sqlite3.connect(str(db)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM searches').fetchone()[0],2)

    def test_saved_jobs_only_for_that_user(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td)/'db.sqlite3'
            self.create_db(db,1)
            self.archive.save_checkpoint('pipps','job_pipps',db,{'categories':['palestre']})
            self.archive.save_checkpoint('amico','job_amico',db,{'categories':['palestre']})
            self.assertEqual([x['id'] for x in self.archive.list_jobs('pipps')], ['job_pipps'])
            self.assertEqual([x['id'] for x in self.archive.list_jobs('amico')], ['job_amico'])
            with self.assertRaises(StorageError):
                self.archive.restore_checkpoint('amico','job_pipps',db)

if __name__ == '__main__':
    unittest.main()
