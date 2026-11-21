"""The pitch tracker reads a clean periodic sound at its period, not an octave (or two) below.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import audio  # noqa: E402

SR = 44100


def formant_tone(f0, dur=0.6, formants=(785.0, 1200.0, 2600.0)):
    """A formant-synth-like vowel: a falling glottal spectrum (1/h) lifted at a few resonances."""
    t = np.arange(int(dur * SR)) / SR
    y = sum(np.sin(2 * np.pi * h * f0 * t) / h
            * (1 + sum(4 / (1 + ((h * f0 - f) / 150.0) ** 2) for f in formants))
            for h in range(1, int(4000 / f0)))
    return (0.3 * y / np.max(np.abs(y))).astype(np.float32)


class Pitch(unittest.TestCase):
    def test_no_octave_drop(self):
        for f0 in (130.8, 261.6, 523.3):
            ft = audio.Features(formant_tone(f0), SR)
            got = audio.mean_f0(ft, 0.1, 0.5)
            self.assertLess(abs(1200 * np.log2(got / f0)), 50, 'f0 %.1f read as %.1f' % (f0, got))


if __name__ == '__main__':
    unittest.main()
