"""ヴ as its own phonemes v / v' (--v-mode), or as b / b' like the stock Japanese dictionary.

    python -I -m unittest discover -s tests -v
"""
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


class Mapping(unittest.TestCase):
    def test_v_off_is_b(self):
        self.assertEqual(P.ja_alias('ヴぁ'), ['b', 'a'])
        self.assertEqual(P.ja_alias('a ヴぃ'), ['a', "b'", 'i'])

    def test_v_on(self):
        self.assertEqual(P.ja_alias('ヴぁ', True), ['v', 'a'])
        self.assertEqual(P.ja_alias('ゔ', True), ['v', 'M'])
        self.assertEqual(P.ja_alias('a ヴぃ', True), ['a', "v'", 'i'])
        self.assertEqual(P.ja_alias('ゔゅ', True), ["v'", 'M'])
        self.assertEqual(P.ja_alias('- ヴぇ', True), ['Sil', 'v', 'e'])
        self.assertEqual(P.ja_alias('a v', True), ['a', 'v'])           # CVVC VC part
        self.assertEqual(P.ja_alias('a vy', True), ['a', "v'"])
        self.assertEqual(P.ja_alias('va', True), ['v', 'a'])            # romaji
        self.assertEqual(P.ja_alias('n ヴぁ', True), ['m', 'v', 'a'])   # ん before v is labial

    def test_other_syllables_unchanged(self):
        for a in ('か', 'a ば', 'び', 'a ふ', 'n き'):
            self.assertEqual(P.ja_alias(a, True), P.ja_alias(a))

    def test_v_aliases(self):
        self.assertEqual(P.v_aliases(['ヴぁ', 'か', 'a v', 'a b', 'ぶ']), ['ヴぁ', 'a v'])

    def test_fallback(self):
        self.assertEqual(P.fallback("v'", {'b'}), 'b')                 # v' -> b' -> b
        self.assertEqual(P.fallback("v'", {"b'", 'b'}), "b'")
        self.assertEqual(P.fallback('v', {'v'}), 'v')
        self.assertEqual(P.fallback('x', set()), 'x')


def make_bank(root):
    bank = os.path.join(root, 'bank')
    os.makedirs(bank)
    t = np.arange(int(SR * 1.0)) / SR
    rng = np.random.default_rng(3)
    rows = []
    for i, alias in enumerate(['あ', 'ヴぁ', 'a ヴぃ', 'ば']):
        y = 0.5 * np.sin(2 * np.pi * 220 * t)
        y[:int(SR * 0.15)] = 0
        if alias != 'あ':
            y[int(SR * 0.15):int(SR * 0.25)] = 0.2 * rng.standard_normal(int(SR * 0.1))
        audio.write_wav(os.path.join(bank, 's%d.wav' % i), y.astype(np.float32), SR)
        rows.append('s%d.wav=%s,100,150,-600,40,10\n' % (i, alias))
    with open(os.path.join(bank, 'oto.ini'), 'w', encoding='utf-8') as f:
        f.writelines(rows)
    dpath = os.path.join(root, 'dict.txt')
    with open(dpath, 'w', encoding='latin-1') as f:
        f.write('phonetic_group(name="Vowels", voiced=true):\n\ta\n\ti\n\tM\n\te\n\to\n')
        f.write('phonetic_group(name="Silence", voiced=false):\n\tSil\n')
    return bank, dpath


class Converting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bank, self.dpath = make_bank(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def conv(self, mode):
        return convert.Converter(self.bank, os.path.join(self.tmp.name, 'out_' + mode), lang='ja',
                                 dict_path=self.dpath, v_mode=mode, log=QUIET)

    def test_auto_uses_v_when_recorded(self):
        c = self.conv('auto')
        self.assertTrue(c.v_real)
        self.assertEqual(c.n_v, 2)
        self.assertIn('v', c.dict)
        self.assertIn("v'", c.dict)
        self.assertEqual(c.dict.groups['v'], 'VoicedFricatives')

    def test_b_mode(self):
        c = self.conv('b')
        self.assertFalse(c.v_real)
        self.assertNotIn('v', c.dict)

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            self.conv('w')

    def test_run_writes_v(self):
        c = self.conv('v')
        manifest = c.run()
        units = {r[2] for r in manifest if r[1] == 'art'}
        self.assertIn('v a', units)
        from_v = [r[2].split() for r in manifest if r[1] == 'art' and 'ヴ' in r[4]]
        self.assertTrue(from_v)
        self.assertFalse([u for u in from_v if 'b' in u or "b'" in u], from_v)
        d = dbtool_io.load_dictionary(os.path.join(c.out, 'dictionary.txt'))
        self.assertIn('v', d)
        self.assertEqual(dbtool_io.validate_folder(c.out, c.dict_out), [])
        with open(os.path.join(c.out, 'report.txt'), encoding='utf-8') as f:
            self.assertIn("sung as v / v'", f.read())


if __name__ == '__main__':
    unittest.main()
