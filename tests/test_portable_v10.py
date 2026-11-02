import csv, hashlib, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock
import turbo_scraper as core
from web_backend import Job, JobManager, completed_queries

class PortableTests(unittest.TestCase):
    def test_restore(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(core, 'STATE', Path(td)/'state'), mock.patch.object(core, 'RESULTS', Path(td)/'out'):
                cities=[dict(name='Lecce',prov='LE',region='Puglia',population=92000)]
                slug=hashlib.sha256(b'tester').hexdigest()[:12]
                base,dest=core.make_plan(cities,['autonoleggio'],f'web_{slug}_veloce_p2')
                with sqlite3.connect(str(base/'progress.sqlite3')) as db:
                    db.execute('CREATE TABLE searches (query TEXT PRIMARY KEY, status TEXT)')
                    db.execute("INSERT INTO searches VALUES ('q','done')")
                with (base/'attivita_originali.csv').open('w',newline='') as fh:
                    writer=csv.DictWriter(fh,fieldnames=['name','category','query','phone'])
                    writer.writeheader()
                    writer.writerow(dict(name='Salento Rent',category='Car rental agency',query='q',phone='123'))
                job=Job(user='tester',id=dest.stem,name=dest.stem,base=base,dest=dest,
                        categories=['autonoleggio'],search_categories=['autonoleggio'],
                        depth='Veloce',exclude_chains=True,extra_brands=['Avis'],
                        search_count=1,n_cities=1,deep_local=True,selected_cities=cities,
                        filter_mode='rigoroso',workers=2,pages=2,batch=80,done=True)
                manager=JobManager()
                manager.jobs[job.user]=job
                saved=manager.create_portable_backup('tester',{'p_geo':'Comuni'})
                (base/'progress.sqlite3').unlink()
                (base/'attivita_originali.csv').unlink()
                restored, preferences=JobManager().restore_portable_backup('tester',saved)
                self.assertEqual(completed_queries(base),1)
                self.assertEqual(restored.found,1)
                self.assertEqual(preferences['p_geo'],'Comuni')
                with self.assertRaises(ValueError):
                    JobManager().restore_portable_backup('another-user',saved)
