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
        # CJK is encodable in cp936, but the ANSI DBTool still exits on it — the case that broke
        # builds for a user named 廖泽鑫 (temp under C:\Users\廖泽鑫\AppData\Local\Temp).
        self.assertFalse(B.ansi_safe('C:\\Users\\\u5Ed6\u6CFD\u9E21\\AppData\\Local\\Temp\\dictionary.txt'))
        if hasattr(B, 'ascii_path'):
            self.assertFalse(B.ascii_path('C:\\Users\\\u5ED6\u6CFD\u9E21\\Temp'))

    def test_safe_temp(self):
        d = B.safe_temp()
        self.assertTrue(os.path.isdir(d))
        self.assertTrue(B.ansi_safe(d))
        if hasattr(B, 'ascii_path'):
            self.assertTrue(B.ascii_path(d))     # plain ASCII, not merely cp936-encodable


if __name__ == '__main__':
    unittest.main()
