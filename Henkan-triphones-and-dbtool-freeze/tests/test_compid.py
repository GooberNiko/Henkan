"""Voicebank CompIDs: pools, random generation, local collision checks.

    python -I -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import compid as C  # noqa: E402
from u2v import pack as P  # noqa: E402


class Pools(unittest.TestCase):
    def test_pools_valid(self):
        for lang, pool in C.POOLS.items():
            if pool is None:
                continue
            self.assertTrue(pool, lang)
            for cid in pool:
                self.assertTrue(C.COMPID_RE.match(cid), '%s: %r' % (lang, cid))
                self.assertTrue(cid.startswith('B'), cid)

    def test_known_members(self):
        self.assertIn('BX4PEFHANWRW3XEL', C.POOLS['Japanese'])
        self.assertIn('BK8H79SAEHXWSKDB', C.POOLS['Chinese'])
        self.assertIn('BXI5WRTWNYF9CSG6', C.POOLS['English'])

    def test_no_pool_languages(self):
        for lang in ('Korean', 'Spanish', 'Other'):
            self.assertIsNone(C.POOLS[lang])
            with self.assertRaises(C.CompIDError):
                C.pick_from_pool(lang, random.Random(0))


class Generate(unittest.TestCase):
    def test_random_format_unique(self):
        rng = random.Random(7)
        cids = {C.random_compid(rng) for _ in range(30)}
        self.assertEqual(len(cids), 30)
        for cid in cids:
            self.assertTrue(C.COMPID_RE.match(cid))

    def test_suggest_prefers_pool(self):
        cid = C.suggest('Japanese', random.Random(1))
        self.assertIn(cid, C.POOLS['Japanese'])
        cid = C.suggest('English', random.Random(1))
        self.assertIn(cid, C.POOLS['English'])
        cid = C.suggest('Korean', random.Random(1))
        self.assertTrue(C.COMPID_RE.match(cid))
        self.assertEqual(C.describe_compid(cid)['lang_name'], 'Korean')

    def test_status(self):
        ok, _msg = C.status_of('BKGHF5N5PSTN4KD6')
        self.assertTrue(ok)
        ok, _msg = C.status_of('nope')
        self.assertFalse(ok)
        ok, _msg = C.status_of('bkghf5n5pstn4kd6')
        self.assertTrue(ok)  # case-insensitive

    def test_generate_lang_digits(self):
        for lang, digit in (('Japanese', '0'), ('English', '1'), ('Korean', '2'),
                            ('Spanish', '3'), ('Chinese', '4')):
            cid = C.generate(lang, random.Random(11))
            raw = C.decode(cid)
            self.assertIsNotNone(raw, lang)
            self.assertEqual(raw[3], digit, lang)
            self.assertEqual(C.describe(raw)['lang_name'], lang)

    def test_pack_names_still_work(self):
        self.assertIs(P.COMPID_RE, C.COMPID_RE)
        self.assertEqual(P.new_compid(random.Random(3)), C.random_compid(random.Random(3)))
        with self.assertRaises(P.PackError):
            P.check_compid('bad')


class Codec(unittest.TestCase):
    def test_round_trip(self):
        for raw in ('00C11100040000', '00L014L0040000', '00501040000000'):
            cid = C.encode_raw(raw)
            self.assertTrue(C.COMPID_RE.match(cid))
            self.assertEqual(C.decode(cid), raw)

    def test_teto_is_japanese(self):
        parts = C.describe_compid('BX4PEFHANWRW3XEL')
        self.assertEqual(parts['raw'], '00L014L0040000')
        self.assertEqual(parts['lang_name'], 'Japanese')
        self.assertEqual(parts['version'], '40000')

    def test_bad_raw_rejected(self):
        for bad in ('too short', '00C1110004000!', 'ZZZZZZZ0000000'):
            with self.assertRaises(C.CompIDError):
                C.encode_raw(bad)

    def test_undecodable_is_none_not_crash(self):
        # real bank IDs can lack a valid checksum; the editor doesn't check it
        self.assertIsNone(C.decode('BXI5WRTWNYF9CSG6'))
        self.assertIsNone(C.decode('nope'))
        self.assertIsNone(C.describe_compid('BXI5WRTWNYF9CSG6'))

    def test_status_ignores_checksum(self):
        # shape + local uniqueness gate an install, not the checksum
        ok, _msg = C.status_of('BXI5WRTWNYF9CSG6')
        self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main()
