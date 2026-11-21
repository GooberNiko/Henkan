"""Choosable extras + EVEC numbers/phonemes (oto reading and Converter setup only, no audio).

    python -I -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from u2v import convert, phonemes as P  # noqa: E402
from u2v import select as Sel  # noqa: E402

QUIET = lambda *a: None  # noqa: E731


def make_bank(root):
    folders = {
        'Standard': [('a.wav', 'ka'), ('b.wav', 'a ka')],
        'Soft': [('a.wav', 'ka'), ('b.wav', 'a ka')],
        'extras': [('br.wav', 'breath1'), ('rr.wav', 'rr')],
    }
    for folder, rows in folders.items():
        d = os.path.join(root, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
            for wav, alias in rows:
                f.write('%s=%s,100,50,-200,30,10\n' % (wav, alias))
                open(os.path.join(d, wav), 'wb').close()


def make_conv(root, **kw):
    out = os.path.join(root, 'out')
    kw.setdefault('log', QUIET)
    return convert.Converter(root, out, **kw)


class Parse(unittest.TestCase):
    def test_evec_map(self):
        self.assertEqual(Sel.parse_evec_map('Soft=#1,Whisper=#2,Power=main,Old=skip'),
                         {'Soft': '#1', 'Whisper': '#2', 'Power': '', 'Old': None})
        self.assertEqual(Sel.normalize_suffix('2'), '#2')
        self.assertEqual(Sel.normalize_suffix('main'), '')
        self.assertIsNone(Sel.normalize_suffix('skip'))
        with self.assertRaises(ValueError):
            Sel.normalize_suffix('x')
        with self.assertRaises(ValueError):
            Sel.parse_evec_map('justaname')

    def test_evec_phonemes(self):
        self.assertEqual(Sel.parse_evec_phonemes('Soft:a,i,M,e,o;Whisper:a e'),
                         {'Soft': {'a', 'i', 'M', 'e', 'o'}, 'Whisper': {'a', 'e'}})
        with self.assertRaises(ValueError):
            Sel.parse_evec_phonemes('Soft')

    def test_extras_want(self):
        self.assertIsNone(P.parse_extras_want('all'))
        self.assertEqual(P.parse_extras_want('none'), set())
        self.assertEqual(P.parse_extras_want('vend'), {'vend'})
        with self.assertRaises(ValueError):
            P.parse_extras_want('br9')
        with self.assertRaises(ValueError):          # built in up to 1.0.5: says how to map it now
            P.parse_extras_want('br1,vend')

    def test_extra_kind(self):
        self.assertEqual(P.extra_kind(['a', 'Sil']), 'vend')

    def test_custom_extras(self):
        self.assertEqual(Sel.parse_custom_extras('cough=ch:unvoiced'),
                         {'cough': ('ch', False, 'Custom')})
        self.assertEqual(Sel.parse_custom_extras('laugh=la:voiced:Laughs'),
                         {'laugh': ('la', True, 'Laughs')})
        self.assertEqual(Sel.parse_custom_extras(['a=b:uv', 'c=d:v']),
                         {'a': ('b', False, 'Custom'), 'c': ('d', True, 'Custom')})
        with self.assertRaises(ValueError):
            Sel.parse_custom_extras('justaname')
        with self.assertRaises(ValueError):
            Sel.parse_custom_extras('cough=9bad:unvoiced')
        with self.assertRaises(ValueError):
            Sel.parse_custom_extras('cough=ch:123')

    def test_custom_phonetic_groups(self):
        # the DBTool's own groups: the group implies its voiced flag in dictionary.txt
        self.assertEqual(Sel.parse_custom_extras('cough=ch:UnvoicedPlosives'),
                         {'cough': ('ch', False, 'UnvoicedPlosives')})
        self.assertEqual(Sel.parse_custom_extras('laugh=la:Vowels'),
                         {'laugh': ('la', True, 'Vowels')})
        self.assertEqual(Sel.parse_custom_extras('sniff=sn:whisper'),  # case-insensitive
                         {'sniff': ('sn', False, 'Whisper')})
        # explicit voiced/unvoiced still wins over the group's implication
        self.assertEqual(Sel.parse_custom_extras('x=xx:UnvoicedPlosives:voiced'),
                         {'x': ('xx', True, 'UnvoicedPlosives')})
        self.assertEqual(Sel.parse_custom_extras('y=yy:voiced:UnvoicedPlosives'),
                         {'y': ('yy', True, 'UnvoicedPlosives')})
        self.assertEqual(len(Sel.PHONETIC_GROUPS), 12)

    def test_auto_evec_map(self):
        rows = [('Standard', 10, [], []), ('Soft', 8, [], []), ('Whisper', 5, [], [])]
        self.assertEqual(Sel.auto_evec_map(rows), 'Standard=main, Soft=#1, Whisper=#2')
        spec = Sel.auto_evec_map(rows)
        self.assertEqual(Sel.parse_evec_map(spec),
                         {'Standard': '', 'Soft': '#1', 'Whisper': '#2'})

    def test_suggest_extra_phoneme(self):
        self.assertEqual(Sel.suggest_extra_phoneme('cough'), 'co')
        self.assertEqual(Sel.suggest_extra_phoneme('cough', taken={'co'}), 'co2')
        self.assertEqual(Sel.suggest_extra_phoneme('123'), 'ex')
        self.assertTrue(Sel.PHONEME_RE.fullmatch(Sel.suggest_extra_phoneme('語尾息')))

    def test_evec_mode(self):
        self.assertEqual(Sel.normalize_evec_mode(None), 'full')
        self.assertEqual(Sel.normalize_evec_mode('vowels'), 'vowels')
        self.assertEqual(Sel.normalize_evec_mode('support'), 'vowels')
        with self.assertRaises(ValueError):
            Sel.normalize_evec_mode('fancy')


class Colours(unittest.TestCase):
    def test_bank_colours(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            rows, _entries = Sel.bank_colours(root)
            names = [r[0] for r in rows]
            self.assertIn('Standard', names)
            self.assertIn('Soft', names)

    def test_evec_map_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', evec_map='Soft=#2')
            self.assertEqual(conv.color_suffix.get('Soft'), '#2')
            self.assertEqual(conv.color_suffix.get('Standard'), '')

    def test_evec_map_skip(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', evec_map='Soft=skip')
            self.assertNotIn('Soft', conv.color_suffix)
            self.assertTrue(all(e.color != 'Soft' for e in conv.entries))

    def test_evec_map_implies_evec(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, evec_map='Soft=#1')
            self.assertEqual(conv.colors, 'evec')
            self.assertEqual(conv.color_suffix.get('Soft'), '#1')

    def test_evec_phonemes_filter(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', evec_phonemes='Soft:a,e')
            self.assertFalse(conv._color_allows('Soft', ['k', 'a']))
            self.assertTrue(conv._color_allows('Soft', ['a']))
            self.assertTrue(conv._color_allows('Standard', ['k', 'a']))


class CustomExtras(unittest.TestCase):
    def test_custom_mapping(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', extras='none',
                             custom_extras='breath1=ch:unvoiced')
            by = {e.raw_alias: e for e in conv.entries}
            self.assertEqual(conv._extra(by['breath1']), ['Sil', 'ch', 'Sil'])
            self.assertEqual(conv.custom_dict, {'ch': ('Custom', False)})
            self.assertEqual(conv.extras_found, ['ch'])

    def test_custom_case_insensitive(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', custom_extras='BREATH1=ch:unvoiced')
            by = {e.raw_alias: e for e in conv.entries}
            self.assertEqual(conv._extra(by['breath1']), ['Sil', 'ch', 'Sil'])

    def test_vowels_only_suffixer(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', evec_mode='vowels')
            self.assertEqual(conv.colors, 'evec')
            g = next(g for g in conv.group_color if conv.group_color[g] == 'Soft')
            sx = conv.suffixer(g)
            self.assertEqual(sx('a'), 'a#1')
            self.assertEqual(sx('k'), 'k')
            self.assertEqual(sx('Sil'), 'Sil')

    def test_vowels_only_implies_evec(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, evec_mode='vowels')
            self.assertEqual(conv.colors, 'evec')


class TetoBank(unittest.TestCase):
    """Alias shapes from the Kasane Teto OU bank (star takes, x-takes, small-kana
    offglides, sokuon endings, edge diphthongs) must map, not flood the popup."""
    def test_star_prefix(self):
        self.assertEqual(P.ja_alias('* あ'), ['a'])
        self.assertEqual(P.extra_alias('* a R'), ['a', 'Sil'])

    def test_extra_takes(self):
        self.assertEqual(P.ja_alias('xか'), ['k', 'a'])
        self.assertEqual(P.ja_alias('Ｘか'), ['k', 'a'])
        self.assertEqual(P.extra_alias('xあ R'), ['a', 'Sil'])
        self.assertIsNone(P.extra_alias('Ｘ巻'))           # a rolled r is a custom extra now

    def test_small_kana_offglides(self):
        self.assertEqual(P.ja_syllable('teu'), ['t', 'e', 'M'])  # う is devkit phoneme M
        self.assertEqual(P.ja_syllable('oa'), ['o', 'a'])
        self.assertEqual(P.ja_syllable('doo'), ['d', 'o'])      # long vowel collapses
        self.assertEqual(P.ja_syllable('kya'), ["k'", 'a'])   # table hits unchanged
        self.assertEqual(P.ja_alias('a てぅ'), ['a', 't', 'e', 'M'])
        self.assertEqual(P.ja_alias('a おぁ'), ['a', 'o', 'a'])
        self.assertEqual(P.ja_alias('うあ'), ['M', 'a'])
        self.assertEqual(P.ja_alias('- くぁ'), ['Sil', 'k', 'M', 'a'])

    def test_sokuon(self):
        self.assertEqual(P.ja_syllable('teQ'), ['t', 'e', '?'])
        self.assertEqual(P.ja_syllable('Q'), ['?'])
        self.assertEqual(P.ja_alias('e てっ'), ['e', 't', 'e', '?'])
        self.assertIsNone(P.ja_alias('かった'))  # mid-token geminate still skips

    def test_edge_tag_leak(self):
        self.assertEqual(P.ja_alias("く'"), ['k', 'M'])
        self.assertEqual(P.ja_alias("- く'"), ['Sil', 'k', 'M'])

    def test_nasal_offglide(self):
        self.assertEqual(P.ja_alias('n どぉ')[1:], ['d', 'o'])

    def test_romanized_suggestions(self):
        self.assertEqual(Sel.suggest_extra_phoneme('xか'), 'ka')
        self.assertEqual(Sel.suggest_extra_phoneme('うあ\''), 'ua')
        self.assertEqual(Sel.suggest_extra_phoneme('cough'), 'co')

    def test_bank_shapes_map(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, 'takes')
            os.makedirs(d)
            with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                for wav, alias in [('_a.wav', '* あ'), ('_xka.wav', 'xか'),
                                   ('_ete.wav', 'e てっ'), ('_aoa.wav', 'a おぁ')]:
                    f.write('%s=%s,100,50,-200,30,10\n' % (wav, alias))
                    open(os.path.join(d, wav), 'wb').close()
            self.assertEqual(Sel.find_unmapped(root, 'ja'), [])

    def test_all_aliases_fallback(self):
        # the Custom extras picker falls back to this when everything maps:
        # every alias listed with its mapping, unmapped ones as None
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            got = Sel.all_aliases(root, 'ja')
            by = {(raw, grp): note for raw, grp, note in got}
            self.assertIsNone(by[('breath1', 'extras')])    # breaths are custom extras now
            self.assertIsNone(by[('rr', 'extras')])
            self.assertEqual(by[('ka', 'Standard')], ' -> k a')
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, 'bank')
            os.makedirs(d)
            with open(os.path.join(d, 'oto.ini'), 'w', encoding='utf-8') as f:
                f.write('_q.wav=zzz_qqq,100,50,-200,30,10\n')
                open(os.path.join(d, '_q.wav'), 'wb').close()
            got = [(raw, note) for raw, _grp, note in Sel.all_aliases(root, 'ja')]
            self.assertEqual(got, [('zzz_qqq', None)])


class Extras(unittest.TestCase):
    def test_extra_selection(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            with self.assertRaises(ValueError):
                make_conv(root, colors='evec', extras='br1')
            conv = make_conv(root, colors='evec', custom_extras='breath1=br1:Breaths')
            by = {e.raw_alias: e for e in conv.entries}
            self.assertEqual(conv._extra(by['breath1']), ['Sil', 'br1', 'Sil'])
            self.assertIsNone(conv._extra(by['rr']))

    def test_alias_rows(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            d = os.path.join(root, 'Standard')
            with open(os.path.join(d, 'oto.ini'), 'a', encoding='utf-8') as f:
                f.write('c.wav=a R,100,50,-200,30,10\n')
            open(os.path.join(d, 'c.wav'), 'wb').close()
            rows = {r['key']: r for r in Sel.alias_rows(root, 'ja')}
            self.assertEqual(rows['breath1']['kind'], 'unknown')     # numbered takes stay apart
            self.assertEqual(rows['a R']['kind'], 'ending')
            self.assertEqual(rows['a R']['lead'], 'a')
            self.assertEqual(rows['ka']['kind'], 'sung')
            self.assertEqual(rows['ka']['count'], 2)               # same raw alias in two folders
            self.assertEqual(rows['ka']['groups'], {'Soft', 'Standard'})
            self.assertEqual([r['kind'] for r in Sel.alias_rows(root, 'ja')][0], 'unknown')

    def test_extras_none(self):
        with tempfile.TemporaryDirectory() as root:
            make_bank(root)
            conv = make_conv(root, colors='evec', extras='none')
            by = {e.raw_alias: e for e in conv.entries}
            self.assertIsNone(conv._extra(by['breath1']))


if __name__ == '__main__':
    unittest.main()
