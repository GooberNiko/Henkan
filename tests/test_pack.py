"""Pack a built DB -> .ddb/.ddi/.vvd, CompIDs, VVD cipher, install scripts.

    python -I -m unittest discover -s tests -v

DB-level tests need the devkit's OU sample database next to the repo and skip without it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import pack as P  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVKIT = os.path.dirname(REPO)
OU_TREE = os.path.join(DEVKIT, 'OU', 'OU.tree')
HAVE_OU = os.path.exists(OU_TREE)

# encoded with the VVD cipher: '"ID" = "VOCALOID VIRTUAL VOICE"\n"FORMAT" = "4.0.0.0"\n'
KAITO_VVD_HEAD = '8S^8 \' 8LUY[VUS^ LSHNO[V LUSY_8'


class Vvd(unittest.TestCase):
    def test_roundtrip(self):
        text = P.VVD_TEMPLATE.format(fmt='4.0.0.0', compid='BKGHF5N5PSTN4KD6', name='KAITO',
                                     growl='"Growl" = "1"\n')
        self.assertEqual(P.decode_vvd(P.encode_vvd(text)), text)

    def test_real_bank_decodes(self):
        plain = P.decode_vvd(KAITO_VVD_HEAD)
        self.assertEqual(plain, '"ID" = "VOCALOID VIRTUAL VOICE"')

    def test_make_vvd(self):
        enc = P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto', fmt='4.0.0.0', growl=True)
        self.assertIn('\r\n', enc)
        plain = P.decode_vvd(enc)
        self.assertIn('"VOICEIDSTR" = "BKGHF5N5PSTN4KD6"', plain)
        self.assertIn('"VOICENAME" = "Teto"', plain)
        self.assertIn('"Growl" = "1"', plain)
        no_growl = P.decode_vvd(P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto', fmt='3.0.0.0', growl=False))
        self.assertNotIn('Growl', no_growl)

    def test_vvd_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'X.vvd')
            with open(p, 'w', encoding='latin-1', newline='') as f:
                f.write(P.make_vvd('BKGHF5N5PSTN4KD6', 'Teto'))
            self.assertEqual(P.vvd_info(p), {'compid': 'BKGHF5N5PSTN4KD6', 'name': 'Teto'})


class CompId(unittest.TestCase):
    def test_new_compid(self):
        import random
        rng = random.Random(42)
        cids = {P.new_compid(rng) for _ in range(20)}
        self.assertEqual(len(cids), 20)
        for c in cids:
            self.assertTrue(P.COMPID_RE.match(c))
            self.assertTrue(c.startswith('B'))

    def test_check_compid(self):
        self.assertEqual(P.check_compid('bkghf5n5pstn4kd6'), 'BKGHF5N5PSTN4KD6')
        with self.assertRaises(P.PackError):
            P.check_compid('too short')
        with self.assertRaises(P.PackError):
            P.check_compid('lowercase-is-ok-but-!!')


class Editors(unittest.TestCase):
    def test_guess_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            for exe, kind in (('VOCALOID4.exe', 'V4'), ('VoiceLoader.exe', 'V4FE-dev'),
                              ('VOCALOID2.exe', 'V2')):
                d = os.path.join(tmp, kind)
                os.makedirs(d)
                open(os.path.join(d, exe), 'wb').close()
                self.assertEqual(P.guess_editor(d)['kind'], kind)
            os.makedirs(os.path.join(tmp, 'empty'))
            with self.assertRaises(P.PackError):
                P.guess_editor(os.path.join(tmp, 'empty'))

    def test_default_bank_dir(self):
        ed = {'kind': 'V4', 'root': 'C:\\e', 'label': 'x'}
        self.assertTrue(P.default_bank_dir(ed, 'Teto').endswith(os.path.join('VoiceDB', 'Teto')))
        ed2 = {'kind': 'V2', 'root': 'C:\\e2', 'label': 'x'}
        self.assertEqual(P.default_bank_dir(ed2, 'Teto'),
                         os.path.join('C:\\e2', 'voicedbdir', 'Teto'))

    def test_install_script_v4(self):
        bat = P.install_script('BKGHF5N5PSTN4KD6', 'Teto', 'C:\\V\\Teto', 'Teto', 'V4')
        self.assertIn('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE\\BKGHF5N5PSTN4KD6', bat)
        self.assertIn('HKLM\\SOFTWARE\\POCALOID4\\DATABASE\\BKGHF5N5PSTN4KD6', bat)
        self.assertIn('VOCALOID4 Library (Teto)', bat)
        self.assertIn('/reg:32', bat)
        # working commercial layout: files into a CompID subfolder, DBVERSION + real DRP, no KEYS
        self.assertIn('%DB_PATH%\\%DB_COMPID%', bat)
        self.assertIn('/V DBVERSION /T REG_DWORD /D "4"', bat)
        self.assertIn('0B0A51', bat)
        self.assertNotIn('/V KEYS', bat)  # no placeholder key value; stale KEYS subkeys are deleted
        self.assertIn('KEYS" /F', bat)
        un = P.install_script('BKGHF5N5PSTN4KD6', 'Teto', 'C:\\V\\Teto', 'Teto', 'V4', uninstall=True)
        self.assertIn('reg DELETE', un)
        self.assertNotIn('copy /Y', un)

    def test_dev_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, 'DB_Dev.ini')
            open(ini, 'w').write('[VVoice 0]\nProgramChange = 0\nVVoiceName = \"X\"\n')
            dbfolder = os.path.join(tmp, 'Singer')
            os.makedirs(dbfolder)
            open(os.path.join(tmp, 'Singer.tree'), 'wb').close()
            bat = P.write_dev_slot(tmp, os.path.join(dbfolder), 'Singer', log=lambda *a: None)
            self.assertTrue(os.path.exists(bat))
            slots = P.read_dev_slots(ini)
            self.assertEqual(slots, [(0, 'X', '')])
            snippet = open(os.path.join(tmp, 'devslot_1', 'slot.txt')).read()
            self.assertIn('[VVoice 1]', snippet)
            self.assertIn('DBName = \"Singer.tree\"', snippet)


@unittest.skipUnless(HAVE_OU, 'devkit OU sample DB not next to the repo')
class PackOU(unittest.TestCase):
    def test_ddi_reads(self):
        with open(OU_TREE, 'rb') as f:
            m = P.DDIModel(f.read())
        m.read()
        self.assertTrue(len(m.ddi_data_dict['sta']) > 0)
        self.assertTrue(len(m.ddi_data_dict['art']) > 100)

    def test_pack_and_verify(self):
        db = OU_TREE[:-5]
        with tempfile.TemporaryDirectory() as tmp:
            ddb_src, ddi_src = P.pack_db(db, os.path.join(tmp, 'pkg'), log=lambda *a: None)
            with open(ddb_src, 'rb') as f:
                ddb = f.read()
            with open(ddi_src, 'rb') as f:
                ddi = f.read()
            self.assertTrue(ddb.startswith(b'FRM2'))
            m = P.DDIModel(ddi)
            m.read()
            with open(OU_TREE, 'rb') as f:
                ref = P.DDIModel(f.read())
            ref.read()
            self.assertEqual(set(m.ddi_data_dict['art']), set(ref.ddi_data_dict['art']))
            self.assertEqual(set(m.ddi_data_dict['sta']), set(ref.ddi_data_dict['sta']))
            # every 50th offset must land in its chunk (full check is slow on 650 MB)
            bad = tot = n = 0

            def check(pos, is_snd):
                R = int.from_bytes(ddi[pos:pos + 8], 'little')
                if not is_snd:
                    return ddb[R:R + 4] == b'FRM2'
                S = ddb.rfind(b'SND ', 0, R)
                L = int.from_bytes(ddb[S + 4:S + 8], 'little') if S >= 0 else 0
                return S >= 0 and S + L > R

            for items in list(m.ddi_data_dict['art'].values()) + list(m.ddi_data_dict['sta'].values()):
                for it in items:
                    work = [(e, False) for e in it['epr']] + [(it['snd'], True)]
                    if 'snd_start' in it:
                        work.append((it['snd_start'], True))
                    for e, is_snd in work:
                        n += 1
                        if n % 50 == 0:
                            tot += 1
                            if not check(int(e.split('=')[0], 16), is_snd):
                                bad += 1
            self.assertEqual(bad, 0, '%d/%d sampled offsets bad' % (bad, tot))
            self.assertTrue(tot > 100)


if __name__ == '__main__':
    unittest.main()
