import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import turbo_scraper as core
from web_backend import Job, JobManager, completed_queries


class NationalResumeTests(unittest.TestCase):
    def test_completed_queries_counts_only_done(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with sqlite3.connect(str(base / 'progress.sqlite3')) as db:
                db.execute('CREATE TABLE searches (query TEXT PRIMARY KEY, status TEXT)')
                db.executemany('INSERT INTO searches VALUES (?, ?)',
                               [('a', 'done'), ('b', 'done'), ('c', 'failed')])
            self.assertEqual(completed_queries(base), 2)

    def test_multiple_groups_continue_until_plan_complete(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / 'job'
            base.mkdir()
            (base / 'locations').mkdir()
            (base / 'categories.txt').write_text('palestra\n', encoding='utf-8')
            engine = Path(td) / 'engine'
            engine.mkdir()
            (engine / 'main.py').write_text('')
            dest = Path(td) / 'results.csv'
            job = Job(
                user='utente', id='plan', name='plan', base=base, dest=dest,
                categories=['palestra'], search_categories=['palestra'],
                depth='Veloce', exclude_chains=True, extra_brands=[],
                search_count=6, n_cities=6, deep_local=False,
                selected_cities=[{'name':'Lecce','prov':'LE'}],
                filter_mode='rigoroso', workers=1, pages=1, batch=2,
                log_file=base / 'log.txt'
            )
            class FakeProcess:
                def __init__(self, cmd, **kwargs):
                    self.cmd = cmd
                    with sqlite3.connect(str(base / 'progress.sqlite3')) as db:
                        db.execute('CREATE TABLE IF NOT EXISTS searches (query TEXT PRIMARY KEY, status TEXT)')
                        current = db.execute("SELECT COUNT(*) FROM searches WHERE status='done'").fetchone()[0]
                        for i in range(current, min(current + 2, 6)):
                            db.execute('INSERT INTO searches VALUES (?, ?)', (str(i), 'done'))
                    path = Path(cmd[cmd.index('--out') + 1])
                    with path.open('w', newline='', encoding='utf-8') as fh:
                        import csv
                        writer=csv.DictWriter(fh, fieldnames=['name', 'category','query', 'phone'])
                        writer.writeheader()
                        writer.writerow(dict(name='Palestra Locale',category='Gym',
                                             query='palestra in Lecce, LE',phone='0832123456'))
                def poll(self): return 0
                def wait(self, *args, **kwargs): return 0

            with mock.patch.object(core,'ensure_engine', return_value=engine / 'main.py'), \
                 mock.patch.object(core,'ENGINE', engine), \
                 mock.patch('web_backend.subprocess.Popen', FakeProcess):
                JobManager()._execute(job)
            self.assertEqual(completed_queries(base), 6)
            self.assertEqual(job.found, 1)
            self.assertIn('completata', job.status.lower())

if __name__ == '__main__':
    unittest.main()
