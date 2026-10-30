import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_backend import build_categories, parse_geography, safe_csv_bytes, select_geography
from turbo_scraper import category_verdict


DATASET = [
    {'nome':'Lecce','popolazione':100000,'provincia':{'sigla':'LE','nome':'Lecce'}, 'regione':{'nome':'Puglia'}, 'zona':{'nome':'Sud'}},
    {'nome':'Milano','popolazione':1300000,'provincia':{'sigla':'MI','nome':'Milano'}, 'regione':{'nome':'Lombardia'}, 'zona':{'nome':'Nord-ovest'}},
    {'nome':'Monza','popolazione':120000,'provincia':{'sigla':'MB','nome':'Monza e Brianza'}, 'regione':{'nome':'Lombardia'}, 'zona':{'nome':'Nord-ovest'}},
    {'nome':'Lecce','popolazione':100,'provincia':{'sigla':'AA','nome':'Provincia omonima'}, 'regione':{'nome':'Veneto'}, 'zona':{'nome':'Nord-est'}},
]


class WebBackendTest(unittest.TestCase):
    def setUp(self):
        self.rows = parse_geography(DATASET)

    def test_comuni_con_provincia(self):
        selection = select_geography(self.rows, mode='Comuni', cities=['Lecce (LE)', 'Milano (MI)'])
        self.assertEqual({(x['name'],x['prov']) for x in selection}, {('Lecce','LE'),('Milano','MI')})

    def test_province(self):
        self.assertEqual(len(select_geography(self.rows, mode='Province', provinces=['MI','MB'])), 2)

    def test_regioni(self):
        self.assertEqual(len(select_geography(self.rows, mode='Regioni', regions=['Lombardia'])), 2)

    def test_zone(self):
        self.assertEqual(len(select_geography(self.rows, mode='Zone', zones=['Nord-ovest','Sud'])), 3)

    def test_mista_unione_senza_duplicati(self):
        x = select_geography(self.rows, mode='Selezione mista', regions=['Lombardia'], provinces=['MI'], cities=['Lecce (LE)'])
        self.assertEqual(len(x), 3)

    def test_tutta_italia(self):
        self.assertEqual(len(select_geography(self.rows, mode='Tutta Italia')), 4)

    def test_categorie(self):
        self.assertEqual(build_categories(['palestra'], 'palestra, parrucchiere'), ['palestra','parrucchiere'])

    def test_csv_excel_injection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'a.csv'
            with path.open('w',newline='', encoding='utf-8-sig') as f:
                csv.writer(f,delimiter=';').writerows([
                    ['Nome','Comune','Telefono','Email'],
                    ['=HYPERLINK("x")','Lecce','+39 123',''],
                ])
            payload = safe_csv_bytes(path).decode('utf-8-sig')
            self.assertIn("'=HYPERLINK", payload)
            self.assertIn("'+39 123", payload)

    def test_filtro_categoria(self):
        self.assertTrue(category_verdict({'name':'McFit','category':'Gym'}, ['palestre'])[0])
        self.assertFalse(category_verdict({'name':'Terranova','category':'Clothing store'}, ['palestre'])[0])


if __name__ == '__main__':
    unittest.main()

class EngineWorkflowTest(unittest.TestCase):
    def test_offline_job_simulated(self):
        from unittest import mock
        from web_backend import Job, JobManager
        import turbo_scraper as core
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)/'stato'; base.mkdir()
            results = Path(temp)/'risultati'; results.mkdir()
            engine = Path(temp)/'motore'; engine.mkdir()
            fake_main = engine/'main.py'; fake_main.write_text('')
            job_base = base/'job'; job_base.mkdir()
            (job_base/'categories.txt').write_text('palestre\n')
            (job_base/'locations').mkdir()
            dest = results/'risultati.csv'
            job = Job(user='amico', id='sample', name='sample', base=job_base, dest=dest,
                      categories=['palestre'], search_categories=['palestre'], depth='Veloce',
                      exclude_chains=False, extra_brands=[], search_count=1, n_cities=1,
                      filter_mode='rigoroso', workers=2, pages=1, batch=1,
                      log_file=job_base/'log.txt')
            class FakeProc:
                def __init__(self, cmd, **kwargs):
                    raw = Path(cmd[cmd.index('--out')+1])
                    with raw.open('w',newline='', encoding='utf-8') as f:
                        w = csv.DictWriter(f, fieldnames=['name','category','query','phone','place_id'])
                        w.writeheader()
                        w.writerow({'name':'Palestra Uno','category':'Gym', 'query':'palestre in Lecce, LE', 'phone':'0832000000','place_id':'id1'})
                        w.writerow({'name':'Terranova','category':'Clothing store','query':'palestre in Lecce, LE','phone':'0832111111','place_id':'id2'})
                def poll(self): return 0
                def wait(self, *args, **kwargs): return 0
            with mock.patch.object(core, 'ensure_engine', return_value=fake_main), \
                 mock.patch.object(core, 'ENGINE', engine), \
                 mock.patch('web_backend.subprocess.Popen', FakeProc):
                JobManager()._execute(job)
            self.assertEqual(job.found, 1)
            self.assertEqual(job.rejected, 1)
            self.assertIn('Palestra Uno', dest.read_text(encoding='utf-8-sig'))
            self.assertNotIn('Terranova', dest.read_text(encoding='utf-8-sig'))
