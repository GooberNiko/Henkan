"""Failed steps get a plain-words explanation for the Easy tab.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import explain as E  # noqa: E402


class Explain(unittest.TestCase):
    def test_side_by_side(self):
        title, fix = E.explain('error: the DBTool (C:\\x\\VocaloidDBTool3.exe) cannot start: Windows says its '
                               '"side-by-side configuration is incorrect"')
        self.assertIn('Visual C++ 2008', fix)

    def test_missing_package(self):
        title, fix = E.explain("Traceback ...\nModuleNotFoundError: No module named 'pywinauto'\n")
        self.assertIn('pywinauto', title)
        self.assertIn('pip install -r requirements.txt', fix)

    def test_no_stationaries(self):
        title, _fix = E.explain('error: could not read X.tree as a singer database: no STA block: the DB has '
                                'no stationaries? Packing the voicebank failed (exit 1)')
        self.assertIn('stationar', title.lower())

    def test_unknown_shows_last_error_line(self):
        title, fix = E.explain('converting\nsomething odd\nValueError: bad thing happened\nmore output\n')
        self.assertIn('ValueError: bad thing happened', fix)

    def test_empty(self):
        title, fix = E.explain('')
        self.assertTrue(title and fix)


if __name__ == '__main__':
    unittest.main()
