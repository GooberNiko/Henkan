"""English r-coloured vowels (Q@, O@, I@, e@, U@) built from an Arpasing bank that never recorded them.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio, convert, dbtool_io  # noqa: E402

SR = 44100
QUIET = lambda *a: None  # noqa: E731


def make_bank(root):
    """Two Arpasing strings: "- aa t aa r aa -" and "- er t er -" (sines for vowels and r, a burst for t)."""
    bank = os.path.join(root, 'bank')
    os.makedirs(bank)
    rng = np.random.default_rng(3)

    def tone(ms, amp=0.5, hz=220):
        t = np.arange(int(SR * ms / 1000)) / SR
        return (amp * (np.sin(2 * np.pi * hz * t) + 0.3 * np.sin(4 * np.pi * hz * t))).astype(np.float32)

    def stop():
        return np.concatenate([np.zeros(int(SR * 0.06)), 0.3 * rng.standard_normal(int(SR * 0.02))]).astype(np.float32)

    sil = lambda ms: np.zeros(int(SR * ms / 1000), dtype=np.float32)  # noqa: E731
    # 100 sil | V 100-400 | t 400-480 | V 480-780 | (r 780-880 | V 880-1180) | sil
    audio.write_wav(os.path.join(bank, 'aa.wav'), np.concatenate(
        [sil(100), tone(300), stop(), tone(300), tone(100, 0.3), tone(300), sil(300)]), SR)
    audio.write_wav(os.path.join(bank, 'er.wav'), np.concatenate(
        [sil(100), tone(300, hz=220), stop(), tone(300), sil(300)]), SR)
    rows = ['aa.wav=- aa,0,80,-250,100,30\n', 'aa.wav=aa t,300,120,-220,100,30\n',
            'aa.wav=t aa,400,100,-200,80,10\n', 'aa.wav=aa r,680,120,-220,100,30\n',
            'aa.wav=r aa,780,120,-250,100,30\n', 'aa.wav=aa -,1080,120,-220,100,30\n',
            'er.wav=- er,0,80,-250,100,30\n', 'er.wav=er t,300,120,-220,100,30\n',
            'er.wav=t er,400,100,-200,80,10\n', 'er.wav=er -,680,120,-220,100,30\n']
    with open(os.path.join(bank, 'oto.ini'), 'w', encoding='utf-8') as f:
        f.writelines(rows)
    dpath = os.path.join(root, 'dict.txt')
    with open(dpath, 'w', encoding='latin-1') as f:
        f.write('phonetic_group(name="Vowels", voiced=true):\n\tQ\n\t@r\n\tQ@\n')
        f.write('phonetic_group(name="Liquids", voiced=true):\n\tr\n')
        f.write('phonetic_group(name="UnvoicedPlosives", voiced=false):\n\tt\n\tth\n')
        f.write('phonetic_group(name="Silence", voiced=false):\n\tSil\n')
    return bank, dpath


class Rhotic(unittest.TestCase):
    def test_q_at_from_aa_r(self):
        with tempfile.TemporaryDirectory() as tmp:
            bank, dpath = make_bank(tmp)
            c = convert.Converter(bank, os.path.join(tmp, 'out'), lang='en', dict_path=dpath, log=QUIET)
            m = c.run()
            art = {r[2]: r[8] for r in m if r[1] == 'art'}
            stat = {r[2]: r[8] for r in m if r[1] == 'stationary'}
            # starts as its vowel: the same unit as [th Q]
            self.assertTrue(art.get('th Q@', '').startswith('rhotic th Q@<-th Q'), art.get('th Q@'))
            self.assertIn('Sil Q@', art)
            # ends in r: [Q r] spliced into [@r t] / [@r Sil]
            self.assertEqual(art.get('Q@ t'), 'rhotic Q@+t')
            self.assertEqual(art.get('Q@ Sil'), 'rhotic Q@+Sil')
            # held notes sing the vowel
            self.assertIn('from Q', stat.get('Q@', ''))
            self.assertEqual(dbtool_io.validate_folder(c.out, c.dict_out), [])


if __name__ == '__main__':
    unittest.main()
