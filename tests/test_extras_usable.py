"""Custom extras are usable: they reach dictionary.txt, manifest, .trans and validate OK.

Builds a tiny synthetic bank (sine vowels + noise breath + rr), maps the breath (and the rr) as custom
extras, and checks the outputs. --extras only knows vowel endings now.

    python -I -m unittest discover -s tests -v
"""
import glob
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert, dbtool_io  # noqa: E402
from u2v import phonemes as P  # noqa: E402

SR = 44100
QUIET = lambda *a: None  # noqa: E731


def make_bank(root, nmains=22):
    bank = os.path.join(root, 'bank')
    os.makedirs(os.path.join(bank, 'main'))
    os.makedirs(os.path.join(bank, 'extras'))

    def write(path, y):
        audio.write_wav(path, np.asarray(y, dtype=np.float32), SR)

    for i in range(nmains):
        t = np.arange(int(SR * 1.0)) / SR
        write(os.path.join(bank, 'main', 'a%d.wav' % i), 0.5 * np.sin(2 * np.pi * 440 * t))
    with open(os.path.join(bank, 'main', 'oto.ini'), 'w', encoding='utf-8') as f:
        for i in range(nmains):
            f.write('a%d.wav=a,100,50,-300,30,10\n' % i)
    sil = np.zeros(int(SR * 0.25), dtype=np.float32)
    rng = np.random.default_rng(1)
    noise = (0.35 * rng.standard_normal(int(SR * 0.4))).astype(np.float32)
    write(os.path.join(bank, 'extras', 'br.wav'), np.concatenate([sil, noise, sil]))
    t2 = np.arange(int(SR * 0.7)) / SR
    write(os.path.join(bank, 'extras', 'rr.wav'), (0.4 * np.sin(2 * np.pi * 180 * t2)).astype(np.float32))
    with open(os.path.join(bank, 'extras', 'oto.ini'), 'w', encoding='utf-8') as f:
        f.write('br.wav=breath1,100,50,-300,30,10\n')
        f.write('rr.wav=rr,50,30,-300,20,10\n')
    dpath = os.path.join(root, 'base_dict.txt')
    with open(dpath, 'w', encoding='latin-1') as f:
        f.write('phonetic_group(name="Vowels", voiced=true):\n\ta\n')
        f.write('phonetic_group(name="Silence", voiced=false):\n\tSil\n')
    return bank, dpath


def convert_bank(bank, dpath, out, spec):
    conv = convert.Converter(bank, out, lang='ja', dict_path=dpath, colors='main',
                             custom_extras=spec, log=QUIET)
    return conv, conv.run()


class ExtrasUsable(unittest.TestCase):
    def test_selected_extra_is_usable(self):
        with tempfile.TemporaryDirectory() as root:
            bank, dpath = make_bank(root)
            out = os.path.join(root, 'out')
            conv, manifest = convert_bank(bank, dpath, out, 'breath1=br1:Breaths')
            self.assertEqual(conv.extras_found, ['br1'])
            d = dbtool_io.load_dictionary(os.path.join(out, 'dictionary.txt'))
            self.assertIn('br1', d)
            self.assertNotIn('rr', d)
            units = {r[2] for r in manifest if r[1] == 'art'}
            self.assertIn('Sil br1', units)
            self.assertIn('br1 Sil', units)
            # transitions into/out of singing exist (spliced from the vowel takes)
            self.assertTrue(any('br1' in u and 'a' in u for u in units),
                            'no br1<->vowel transition in %s' % sorted(units))
            trans_hits = [t for t in glob.glob(os.path.join(out, '*.trans'))
                          if 'br1' in open(t).read()]
            self.assertTrue(trans_hits, 'no .trans file uses br1')
            self.assertEqual(dbtool_io.validate_folder(out, conv.dict_out), [])

    def test_selection_filters(self):
        with tempfile.TemporaryDirectory() as root:
            bank, dpath = make_bank(root)
            _, manifest = convert_bank(bank, dpath, os.path.join(root, 'out'),
                                       'breath1=br1:Breaths; rr=rr:Liquids')
            units = {r[2] for r in manifest if r[1] == 'art'}
            self.assertIn('Sil rr', units)
            _, manifest2 = convert_bank(bank, dpath, os.path.join(root, 'out2'), 'breath1=br1:Breaths')
            units2 = {r[2] for r in manifest2 if r[1] == 'art'}
            self.assertFalse(any('rr' in u.split() for u in units2), sorted(units2))

    def test_custom_extras_rejected(self):
        with self.assertRaises(ValueError):
            P.parse_extras_want('cough')
        with self.assertRaises(ValueError):
            P.parse_extras_want('br6')
        self.assertIsNone(P.extra_alias('cough'))
        self.assertIsNone(P.extra_alias('breath1'))


if __name__ == '__main__':
    unittest.main()
