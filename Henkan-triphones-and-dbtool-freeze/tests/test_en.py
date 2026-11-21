"""English (Arpasing / X-SAMPA) aliases: r is a consonant, plosives and l take VOCALOID's allophones.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import phonemes as P  # noqa: E402


class Arpasing(unittest.TestCase):
    def test_r_is_a_consonant(self):
        self.assertEqual(P.en_alias('aa r'), ['Q', 'r'])
        self.assertEqual(P.en_alias('r aa'), ['r', 'Q'])
        self.assertEqual(P.en_alias('- r'), ['Sil', 'r'])
        self.assertEqual(P.en_alias('aa R'), ['Q', 'Sil'])

    def test_aspirated_onsets(self):
        self.assertEqual(P.en_alias('k aa'), ['kh', 'Q'])
        self.assertEqual(P.en_alias('- k'), ['Sil', 'kh'])
        self.assertEqual(P.en_alias('b uh'), ['bh', 'U'])
        self.assertEqual(P.en_alias('t aa'), ['th', 'Q'])

    def test_plain_codas(self):
        self.assertEqual(P.en_alias('aa k'), ['Q', 'k'])
        self.assertEqual(P.en_alias('k -'), ['k', 'Sil'])
        self.assertEqual(P.en_alias('uh b'), ['U', 'b'])

    def test_fricatives_keep_arpa_meaning(self):
        # ARPA th / dh are the fricatives T / D, not VOCALOID's aspirated th / dh
        self.assertEqual(P.en_alias('th aa'), ['T', 'Q'])
        self.assertEqual(P.en_alias('dh aa'), ['D', 'Q'])

    def test_clear_and_dark_l(self):
        self.assertEqual(P.en_alias('l aa'), ['l0', 'Q'])
        self.assertEqual(P.en_alias('- l'), ['Sil', 'l0'])
        self.assertEqual(P.en_alias('aa l'), ['Q', 'l'])
        self.assertEqual(P.en_alias('l -'), ['l', 'Sil'])


class Xsampa(unittest.TestCase):
    def test_clusters(self):
        self.assertEqual(P.en_alias('klaI'), ['kh', 'l0', 'aI'])
        self.assertEqual(P.en_alias('skaI'), ['s', 'k', 'aI'])


class Fallback(unittest.TestCase):
    def test_dictionary_without_allophones(self):
        self.assertEqual(P.fallback('kh', {'k', 'Q'}), 'k')
        self.assertEqual(P.fallback('l0', {'l'}), 'l')
        self.assertEqual(P.fallback('kh', {'kh', 'k'}), 'kh')


if __name__ == '__main__':
    unittest.main()
