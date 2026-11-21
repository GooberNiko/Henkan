"""Vendored V3V2 backport engine (V3/V4 banks -> VOCALOID2): imports, sources, voice ids.

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v3v2 import convert, ddi, install, sources, tree  # noqa: E402


class Imports(unittest.TestCase):
    def test_convert_api(self):
        for fn in ('convert_bank', 'root_to_v2', 'to_v2', 'fill_gaps',
                   'recover_from_triphones', 'find_bank', 'link_or_copy'):
            self.assertTrue(callable(getattr(convert, fn)), fn)

    def test_install_api(self):
        for fn in ('install', 'uninstall', 'installed', 'detect_language',
                   'encode_id', 'decode_id', 'new_id', 'make_vvd'):
            self.assertTrue(callable(getattr(install, fn)), fn)
        self.assertEqual(install.LANGUAGES, {0: 'Japanese', 1: 'English'})

    def test_tree_api(self):
        for fn in ('pack', 'unit_files', 'escape'):
            self.assertTrue(callable(getattr(tree, fn)), fn)


class Sources(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(sources.safe_name('Kasane Teto (V4)!'), 'Kasane_Teto_V4')
        self.assertEqual(sources.safe_name('VY2V3'), 'VY2V3')
        self.assertEqual(sources.safe_name('???'), 'Singer')

    def test_classify_tree_file(self):
        with tempfile.TemporaryDirectory() as root:
            tree_path = os.path.join(root, 'Miku.tree')
            open(tree_path, 'wb').close()
            src = sources.classify(tree_path)
            self.assertEqual((src.name, src.kind), ('Miku', 'tree'))

    def test_classify_rejects(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                sources.classify(os.path.join(root, 'nope'))
            empty = os.path.join(root, 'empty')
            os.makedirs(empty)
            with self.assertRaises(ValueError):
                sources.classify(empty)
            v2 = os.path.join(root, 'bank')
            os.makedirs(v2)
            with open(os.path.join(v2, 'Miku.ddi'), 'wb') as f:
                f.write(b'12345678ARR2....')
            with self.assertRaises(ValueError):
                sources.classify(v2)
            bad = os.path.join(root, 'bad')
            os.makedirs(bad)
            with open(os.path.join(bad, 'X.ddi'), 'wb') as f:
                f.write(b'12345678XXXX....')
            with self.assertRaises(ValueError):
                sources.classify(bad)


class VoiceIds(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        for dec in ('00R0AB00000000', '00R1CD00000000'):
            vid = install.encode_id(dec)
            self.assertEqual(len(vid), 16)
            self.assertEqual(install.decode_id(vid), dec)

    def test_new_id_format_unique(self):
        taken = set()
        vids = set()
        for lang in (0, 1):
            vid = install.new_id('TestSinger', lang, taken)
            self.assertEqual(len(vid), 16)
            taken.add(vid)
            vids.add(vid)
        self.assertEqual(len(vids), 2)


if __name__ == '__main__':
    unittest.main()
