"""The DBTool is an ANSI program: it only ever gets paths in this PC's Windows code page.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build as B  # noqa: E402


@unittest.skipUnless(os.name == 'nt', 'Windows code pages')
class Paths(unittest.TestCase):
    def test_ansi_safe(self):
        self.assertTrue(B.ansi_safe(r'C:\VOCALOID Developer\Teto_seg\dictionary.txt'))
        # no Windows code page has emoji: the DBTool never sees this file
        self.assertFalse(B.ansi_safe('C:\\bank \U0001F3B5\\dictionary.txt'))

    def test_safe_temp(self):
        d = B.safe_temp()
        self.assertTrue(os.path.isdir(d))
        self.assertTrue(B.ansi_safe(d))


if __name__ == '__main__':
    unittest.main()
