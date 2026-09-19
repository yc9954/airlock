"""Offline guarantees: the pipeline runs, measures for real, prunes, and diversifies.

    python3 -m unittest airlock.tests.test_pipeline    (from the repo root)
    python3 -m unittest tests.test_pipeline            (from inside airlock/)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from airlock.config import Config
from airlock.measure import measure
from airlock.diversity import select_diverse
from airlock.pipeline import Pipeline

GOOD = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Nimbus</title>'
        '<style>body{font-size:1rem;background:#0f1117;color:#eef1f7}'
        '.f{border:1px solid #333}@media(max-width:600px){body{font-size:.9rem}}</style></head>'
        '<body><nav>Nimbus</nav><header><h1>Hi</h1><a href="#">시작</a></header>'
        '<section><div class="f">a</div><div class="f">b</div><div class="f">c</div>'
        '<div class="f">d</div><div class="f">e</div></section>'
        '<section>pricing</section><footer>© 2026</footer></body></html>')
NO_CTA = GOOD.replace('<a href="#">시작</a>', '<p>no action here</p>')
NO_VIEWPORT = GOOD.replace('<meta name="viewport" content="width=device-width,initial-scale=1">', '')


class MeasureTests(unittest.TestCase):
    def test_good_passes(self):
        sig, fit, verdict, reasons = measure(GOOD)
        self.assertEqual(verdict, 'pass')
        self.assertTrue(sig['has_cta'] and sig['has_viewport'] and sig['responsive'])
        self.assertGreater(fit, 70)

    def test_missing_cta_fails_hard(self):
        _, _, verdict, reasons = measure(NO_CTA)
        self.assertEqual(verdict, 'fail')
        self.assertIn('hard:has_cta', reasons)

    def test_missing_viewport_fails_hard(self):
        _, _, verdict, reasons = measure(NO_VIEWPORT)
        self.assertEqual(verdict, 'fail')
        self.assertIn('hard:has_viewport', reasons)


class DiversityTests(unittest.TestCase):
    def test_picks_distinct(self):
        survivors = [
            {'id': 'a', 'fitness': 90, 'signals': {'nodes': 30, 'weight_kb': 10, 'responsive': True},
             'meta': {'layout': 'centered', 'palette': 'midnight', 'font': 'serif', 'tone': 'bold'}},
            {'id': 'b', 'fitness': 88, 'signals': {'nodes': 31, 'weight_kb': 10, 'responsive': True},
             'meta': {'layout': 'centered', 'palette': 'midnight', 'font': 'serif', 'tone': 'bold'}},
            {'id': 'c', 'fitness': 70, 'signals': {'nodes': 80, 'weight_kb': 40, 'responsive': False},
             'meta': {'layout': 'bento', 'palette': 'mint', 'font': 'mono', 'tone': 'technical'}},
        ]
        picked = select_diverse(survivors, 2)
        ids = {v['id'] for v in picked}
        self.assertEqual(picked[0]['id'], 'a')          # best first
        self.assertIn('c', ids)                          # then the most different, not the near-twin b


class PipelineTests(unittest.TestCase):
    def test_end_to_end_offline(self):
        cfg = Config(); cfg.daytona_key = ""; cfg.n_generate = 20
        pl = Pipeline(cfg, 'http://127.0.0.1:8770')
        events = []
        fin = pl.run('t', 'landing for a test product', events.append)
        types = [e['type'] for e in events]
        self.assertEqual(types[0], 'run_started')
        self.assertEqual(types[-1], 'done')
        measured = [e for e in events if e['type'] == 'measured']
        self.assertEqual(len(measured), 20)
        self.assertTrue(any(e['verdict'] == 'pass' for e in measured))
        self.assertTrue(1 <= len(fin) <= cfg.k_finalists)
        champ = pl.select('t', fin[0]['id'])
        self.assertIn('url', champ)


if __name__ == '__main__':
    unittest.main(verbosity=2)
