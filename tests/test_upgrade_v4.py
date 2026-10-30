import tempfile
import unittest
from pathlib import Path
from turbo_scraper import brand_verdict, search_variants, split_by_relevance, write_normalized

class UpgradeTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(search_variants(['palestre'], 'Veloce'), ['palestre'])
        self.assertGreater(len(search_variants(['palestre'], 'Approfondita')), 2)
    def test_chains(self):
        self.assertFalse(brand_verdict({'name': 'FitActive Milano'})[0])
        self.assertFalse(brand_verdict({'name': 'Basic-Fit Lecce'})[0])
        self.assertTrue(brand_verdict({'name': 'Palestra Centro Lecce'})[0])
    def test_filter(self):
        rows = [
            {'name':'FitActive Milano','category':'Gym'},
            {'name':'Palestra Locale','category':'Gym'},
            {'name':'Terranova','category':'Clothing store'},
        ]
        accepted, rejected = split_by_relevance(rows, ['palestre'], 'rigoroso', True)
        self.assertEqual([x['name'] for x in accepted], ['Palestra Locale'])
        self.assertEqual(len(rejected), 2)
    def test_csv(self):
        rows=[{'name':'Palestra Locale','category':'Gym','query':'palestre in Lecce, LE'},
              {'name':'FitActive','category':'Gym','query':'palestre in Lecce, LE'}]
        with tempfile.TemporaryDirectory() as d:
            dst=Path(d)/'out.csv'
            self.assertEqual(write_normalized(rows, dst, categories=['palestre'],exclude_chains=True),1)
            self.assertIn('Palestra Locale',dst.read_text(encoding='utf-8-sig'))

if __name__ == '__main__':
    unittest.main()
