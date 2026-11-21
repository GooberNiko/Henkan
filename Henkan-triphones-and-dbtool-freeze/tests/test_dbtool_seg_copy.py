"""The build copies the converted folder; a subfolder in it (e.g. the source bank) must not stop it.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import dbtool_build as B  # noqa: E402


class SegCopy(unittest.TestCase):
    def test_subfolder_is_skipped(self):
        with tempfile.TemporaryDirectory() as seg, tempfile.TemporaryDirectory() as work:
            for fn in ('a.wav', 'a.trans', 'dictionary.txt'):
                with open(os.path.join(seg, fn), 'w') as f:
                    f.write(fn)
            os.mkdir(os.path.join(seg, 'Tsukine Sora'))
            with open(os.path.join(seg, 'Tsukine Sora', 'oto.ini'), 'w') as f:
                f.write('x')
            B._copy_seg(seg, work)
            self.assertEqual(sorted(os.listdir(work)), ['a.trans', 'a.wav', 'dictionary.txt'])


class StartError(unittest.TestCase):
    def test_side_by_side_names_the_runtime(self):
        ex = OSError(14001, 'CreateProcess', 'The application has failed to start because its side-by-side '
                                             'configuration is incorrect.')
        msg = str(B._start_error(ex, r'C:\VOCALOID Developer\VocaloidDBTool3.exe'))
        self.assertIn('Visual C++ 2008', msg)
        self.assertIn('x86', msg)

    def test_other_errors_pass_through(self):
        msg = str(B._start_error(OSError(2, 'not found'), r'C:\x\VocaloidDBTool3.exe'))
        self.assertIn('not found', msg)
        self.assertNotIn('Visual C++', msg)


if __name__ == '__main__':
    unittest.main()
