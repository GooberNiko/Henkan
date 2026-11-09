"""A DBTool that stops responding is reported as that (not as a bad dictionary) and is started again.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build as B, explain as E  # noqa: E402


def tool(responding):
    t = B.DBTool.__new__(B.DBTool)
    t.log = lambda *a: None
    t._menu = lambda cmd: None
    t._file_dialog = lambda title, path: None
    t._alive = lambda: True
    t._responding = lambda: responding

    def no_dialog(title, timeout=30):
        raise B.DBToolError('timed out waiting for %s. On screen: [] ; []' % title)
    t._dialog = no_dialog
    return t


class NewDb(unittest.TestCase):
    def test_frozen_is_not_blamed_on_the_dictionary(self):
        with self.assertRaises(B.DBToolFrozen) as cm:
            tool(False).new_db(r'C:\t\dictionary.txt', r'C:\db\X')
        self.assertIn('stopped responding', str(cm.exception))
        self.assertNotIn('did not take', str(cm.exception))

    def test_a_responding_tool_keeps_the_plain_error(self):
        with self.assertRaises(B.DBToolError) as cm:
            tool(True).new_db(r'C:\t\dictionary.txt', r'C:\db\X')
        self.assertNotIsInstance(cm.exception, B.DBToolFrozen)
        self.assertIn('Singer database Path & Name', str(cm.exception))


class Retry(unittest.TestCase):
    def build(self, effects, hidden):
        calls = []

        def fake(exe, work, db, name, dic, lang, log, desktop=None):
            r = effects[len(calls)]
            calls.append(desktop)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(B, '_build', fake), mock.patch.object(B, '_copy_seg'), mock.patch.object(B, '_boost'), \
                mock.patch.object(B, '_remove_db'), mock.patch.object(B, '_mkdtemp', return_value='nowhere'), \
                mock.patch.object(B.shutil, 'rmtree'), mock.patch.object(B.shutil, 'copy'), \
                mock.patch.object(B, '_on_hidden_desktop', lambda fn: fn('desk')):
            out = B._build_fresh('exe', 'seg', 'db', 'n', os.path.join('seg', 'dictionary.txt'), 'Japanese',
                                 lambda *a: None, {}, hidden)
        return out, calls

    def test_shown_build_starts_over_once(self):
        out, calls = self.build([B.DBToolFrozen('stopped responding'), ['ok']], False)
        self.assertEqual(out, ['ok'])
        self.assertEqual(len(calls), 2)

    def test_hidden_build_moves_on_to_shown(self):
        out, calls = self.build([B.DBToolFrozen('stopped responding'), []], True)
        self.assertEqual(out, [])
        self.assertEqual(calls, ['desk', None])

    def test_gives_up_after_the_last_try(self):
        with self.assertRaises(B.DBToolFrozen):
            self.build([B.DBToolFrozen('a'), B.DBToolFrozen('b')], False)


class Words(unittest.TestCase):
    def test_says_the_dictionary_is_fine(self):
        title, fix = E.explain('error: ' + B.FROZEN % r'C:\t\dictionary.txt')
        self.assertIn('froze', title)
        self.assertNotIn('could not find', title)
        self.assertIn('Show the DBTool', fix)

    def test_a_missing_dictionary_is_still_that(self):
        title, _fix = E.explain('error: no dictionary.txt in the folder')
        self.assertIn('could not find the phonetic dictionary', title)


if __name__ == '__main__':
    unittest.main()
