#!/usr/bin/env python3
"""Henkan 変換: port an UTAU voicebank (VCV, CVVC or CV; Japanese, or English Arpasing) to a VOCALOID
singer DB, pack it, install it.

    python henkan.py convert "<UTAU bank folder>" "<output folder>" [options]
    python henkan.py validate "<output folder>" [--dict ...]
    python henkan.py build "<output folder>" "<DB folder>/<Name>" [--name "Singer"]
    python henkan.py pack "<DB folder>/<Name>" [--out <dir>] [--name Voice] [--compid ID]
    python henkan.py install <package> --editor "<editor folder>"

The output folder holds <name>.wav/.trans/.seg/.as<N> files. Point the DBTool's
"Segmentate Folder" (WavefilePath) at it, then run the DB build steps.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from u2v import convert, dbtool_io, phonemes as P  # noqa: E402

DEVKIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_DICT = '''could not find the VOCALOID3 phonetic dictionary for %s.
It comes with the DBTool devkit: "Japanese Dictionary\\Japanese_Dictionary.txt" (English:
"English Dictionary\\english_phonetic_dictionary_20061220.txt"). Either put the Henkan folder inside
the devkit folder (next to VocaloidDBTool3.exe), or point at the file with --dict "...\\Japanese_Dictionary.txt".'''


def default_dict(lang):
    """The devkit dictionary for lang: next to this folder, in the devkit around it, or in the working folder."""
    return dbtool_io.find_dictionary(lang, [DEVKIT, os.getcwd(), os.path.dirname(DEVKIT)])


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    from u2v import __version__
    ap.add_argument('--version', action='version', version='Henkan ' + __version__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('convert', help='convert an UTAU bank')
    c.add_argument('bank', help='UTAU voicebank folder (searched recursively for oto.ini)')
    c.add_argument('out', help='output folder for the DBTool (will be created)')
    c.add_argument('--lang', choices=['auto', 'ja', 'en'], default='auto')
    c.add_argument('--dict', help='DB phonetic dictionary .txt (or a DB .dat). '
                                  'Default: the devkit dictionary for the language')
    c.add_argument('--groups', help='regex: only use these pitch/colour groups (sub folder / suffix)')
    c.add_argument('--merge-groups', action='store_true',
                   help='treat all selected groups as one sample set (e.g. CV + VCV folders of one pitch)')
    c.add_argument('--keep', type=int, default=1, help='samples kept per unit and group (default 1)')
    c.add_argument('--no-derive', action='store_true',
                   help="don't create [Sil X]/[X Sil] units from silence found around CV/V samples")
    c.add_argument('--no-fill', action='store_true',
                   help="don't fill missing units by relabelling similar ones or muting onsets")
    c.add_argument('--no-splice', action='store_true',
                   help="don't fake missing [V C] / [V V] / [V N] transitions by joining two samples "
                        "(this is what lets a CV bank sing legato)")
    c.add_argument('--colors', choices=['main', 'evec', 'merge'], default='main',
                   help="banks with several voice colours (soft, whisper ...): 'main' converts only the "
                        "main one (default), 'evec' puts them all in one DB, the others with EVEC-style "
                        "phonemes (a#1, k#1 ...), 'merge' treats all colours as one")
    c.add_argument('--no-normalize', action='store_true',
                   help="keep each recording's own loudness (default: match every unit's vowels to the bank's "
                        "usual level so joins don't jump)")
    c.add_argument('--extras', default='all', metavar='LIST',
                   help='which extras to convert: comma list from br1..br5, brE (breath out), '
                        'brI (breath in), rr (rolled r), vend (vowel endings); '
                        "all (default), none, or br for br1..br5")
    c.add_argument('--no-extras', action='store_true',
                   help='same as --extras none')
    c.add_argument('--evec-map', default=None, metavar='"C=#N,..."',
                   help='choose EVEC colours and their numbers, e.g. Soft=#1,Whisper=#2,Power=main,Old=skip '
                        '(implies --colors evec when a # number is given)')
    c.add_argument('--evec-phonemes', default=None, metavar='"C:ph,..."',
                   help='choose phonemes per colour, e.g. Soft:a,i,M,e,o;Whisper:a,e. '
                        'The converter each colour keeps its own entries; the converter finds their transitions (vowels and such) from those; '
                        'a colour with no vowels warns but still converts')
    c.add_argument('--custom-extra', action='append', default=[], metavar='"ALIAS=PH:TYPE"',
                   help='custom extra: port ANY bank sound as its own phoneme, e.g. '
                        'cough=ch:UnvoicedPlosives, laugh=la:Vowels. Repeatable. The phoneme is added '
                        'to dictionary.txt in the phonetic group you pick (Vowels, Semivowels, Nasals, '
                        'UnvoicedFricatives, VoicedFricatives, Whisper, VoicedAffricates, '
                        'UnvoicedAffricates, Liquids, UnvoicedPlosives, VoicedPlosives, Silence; '
                        'plain voiced/unvoiced also works).')
    c.add_argument('--evec-mode', default='full', choices=['full', 'vowels'],
                   help="EVEC support mode: 'full' suffixes every phoneme (type k#1), 'vowels' suffixes "
                        "ONLY vowels (type a#1) so consonants stay shared and joins crossfade")
    c.add_argument('--v-mode', default='auto', choices=list(P.V_MODES),
                   help="ヴ (va, vi, vu ...): 'v' gives it its own phonemes v and v' (before i), 'b' sings it "
                        "as b / b' like the stock Japanese dictionary, 'auto' (default) = v when the bank "
                        "recorded any ヴ")
    c.add_argument('--list-unknown', action='store_true',
                   help='list bank aliases that map to nothing (candidates for --custom-extra) and exit')
    c.add_argument('--avoid', action='append', default=[], metavar='"C V"',
                   help="don't take this unit from its recording; relabel or splice it from others, "
                        "e.g. --avoid \"g' i\" when one recorded unit renders badly")
    c.add_argument('--brighten', action='append', default=[], metavar='PH=DB',
                   help="lift the treble (above 2 kHz) of a phoneme by DB, e.g. --brighten o=3 for a dull o")
    c.add_argument('--no-extend-dict', action='store_true',
                   help="use the dictionary as is (don't add b, b' and p\' to it)")
    v = sub.add_parser('validate', help='check an output folder the way the DBTool parses it')
    v.add_argument('out')
    v.add_argument('--dict')
    v.add_argument('--lang', choices=['ja', 'en'], default='ja')
    b = sub.add_parser('build', help='drive the DBTool: new singer DB from <out>/dictionary.txt + all build steps')
    b.add_argument('out', help='a converted folder (with dictionary.txt)')
    b.add_argument('db', help='new DB path as folder/Name, e.g. "VOCALOID Developer/Goober/Goober"')
    b.add_argument('--name', help='singer name written to singer.inf')
    b.add_argument('--dbtool', default=os.path.join(DEVKIT, 'VocaloidDBTool3.exe'))
    b.add_argument('--language', default='Japanese', choices=['Japanese', 'English', 'Spanish'])
    b.add_argument('--show-dbtool', action='store_true',
                   help='run the DBTool where you can see it (default: in the background, on a hidden desktop)')
    p = sub.add_parser('pack', help='pack a built singer DB into a voicebank package (.ddb + .ddi + .vvd)')
    p.add_argument('db', help='built DB path as folder/Name, e.g. "VOCALOID Developer/Goober/Goober"')
    p.add_argument('--out', help='package folder (default: <db folder>/package)')
    p.add_argument('--name', help='voicebank name (default: the DB name)')
    p.add_argument('--compid', help='16-char component ID (default: a fresh unique one)')
    p.add_argument('--db-version', default='4', choices=['2', '3', '4'],
                   help='editor generation to pack for (default 4; 2/3 drop the Growl line)')
    n = sub.add_parser('install', help='install a packed voicebank into a VOCALOID editor')
    n.add_argument('package', help='package folder holding <CompID>/<name>.ddb/.ddi/.vvd')
    n.add_argument('--editor', help='editor folder to install into (default: auto-detect)')
    n.add_argument('--dest', help='bank folder (default picked from the editor)')
    n.add_argument('--db-version', default='4', choices=['2', '3', '4'],
                   help='editor generation (default 4)')
    n.add_argument('--compid', help='override the package CompID')
    n.add_argument('--name', help='override the voicebank name')
    n.add_argument('--dev-ini', action='store_true',
                   help='VOCALOID4FE dev editor: register a DB_Dev.ini voice slot instead of files+registry')
    n.add_argument('--db', help='with --dev-ini: the loose built DB path (folder/Name) the slot points at')
    d = sub.add_parser('compid', help="suggest or check a voicebank CompID (the bank's ID card)")
    d.add_argument('--lang', default='Japanese',
                   choices=['Japanese', 'Chinese', 'English', 'Korean', 'Spanish', 'Other'],
                   help='bank language: Japanese/Chinese take from pools of known-valid V4 IDs')
    d.add_argument('--check', metavar='ID', help='check this ID instead of suggesting one')
    d.add_argument('--random', action='store_true',
                   help='fresh random ID even when a language pool exists')
    d.add_argument('--craft', action='store_true',
                   help='fresh ID with the right language digit even when a pool exists '
                        '(the Korean/Spanish move — no pools exist for them)')
    d.add_argument('--encode', metavar='RAW',
                   help='encode a 14-char raw string (e.g. 00C11100040000) to its CompID')
    d.add_argument('--decode', metavar='ID',
                   help='decode a CompID to its raw string (vendor/language/product/version)')
    a = ap.parse_args()

    if a.cmd == 'compid':
        from u2v import compid as _compid
        if a.decode:
            parts = _compid.describe_compid(a.decode)
            if parts is None:
                print('%s has no valid keygen checksum (normal for real bank IDs; '
                      'the editor never checks it)' % (a.decode or '').strip().upper())
                return 1
            print(parts['raw'])
            print('vendor %s, %s, product %s, version %s' % (
                parts['vendor'], parts['lang_name'], parts['product'], parts['version']))
            return 0
        if a.encode:
            try:
                cid = _compid.encode_raw(a.encode)
            except _compid.CompIDError as ex:
                print('error: %s' % ex)
                return 1
            ok, msg = _compid.status_of(cid)
            print(cid)
            print(msg)
            return 0 if ok else 1
        if a.check:
            ok, msg = _compid.status_of(a.check)
            print(msg)
            parts = _compid.describe_compid(a.check)
            if parts is not None:
                print('raw %s: vendor %s, %s, product %s, version %s' % (
                    parts['raw'], parts['vendor'], parts['lang_name'],
                    parts['product'], parts['version']))
            return 0 if ok else 1
        try:
            if a.random:
                cid = _compid.random_compid()
            elif a.craft:
                cid = _compid.generate(a.lang)
            else:
                cid = _compid.suggest(a.lang)
        except _compid.CompIDError as ex:
            print('error: %s' % ex)
            return 1
        ok, msg = _compid.status_of(cid)
        print(cid)
        print(msg)
        return 0 if ok else 1

    if a.cmd == 'pack':
        from u2v import pack as _pack
        try:
            package = _pack.make_package(a.db, a.out, name=a.name, compid=a.compid,
                                         db_version=a.db_version, log=print)
        except _pack.PackError as ex:
            print('error: %s' % ex)
            return 1
        print('install it with: python -I henkan.py install "%s" --editor "<editor folder>"'
              % package)
        return 0

    if a.cmd == 'install':
        from u2v import pack as _pack
        try:
            editors = _pack.find_editors()
            ed = _pack.guess_editor(a.editor) if a.editor else (editors[0] if editors else None)
            if ed is None:
                print('error: no VOCALOID editor found; point at one with --editor "<editor folder>"')
                return 2
            if a.dev_ini:
                if ed['kind'] != 'V4FE-dev':
                    print('error: --dev-ini is for the VOCALOID4FE developer editor')
                    return 2
                if not a.db:
                    print('error: --dev-ini needs --db "<built DB folder/Name>"')
                    return 2
                _pack.write_dev_slot(ed['root'], a.db, a.name)
                print('dev slot added: restart the dev editor to see it')
                return 0
            _pack.install_package(a.package, ed, dest=a.dest, compid=a.compid, name=a.name,
                                  db_version=a.db_version, log=print)
        except _pack.PackError as ex:
            print('error: %s' % ex)
            return 1
        print('run install.bat inside the package as administrator to finish registration')
        return 0

    if a.cmd == 'build':
        from u2v import dbtool_build
        if not os.path.exists(os.path.join(a.out, 'dictionary.txt')):
            print('error: %s has no dictionary.txt. Convert the bank (again) first; the convert step writes it '
                  'from the devkit\'s phonetic dictionary.' % a.out)
            return 2
        dpath = os.path.join(a.out, 'dictionary.txt')
        d = dbtool_io.load_dictionary(dpath)
        used = set()
        for fn in os.listdir(a.out):
            if fn.endswith('.trans'):
                used.update(dbtool_io.read_trans(os.path.join(a.out, fn))[0])
        extra = {p: v for p, v in {**P.JA_EXTRA, **P.V_DICT, **P.EXTRA_DICT}.items() if p in used and p not in d}
        if extra:
            # e.g. the devkit's own dictionary copied in by hand: it lacks b, b', p\', v, v' (and extras)
            print('dictionary.txt lacks %s: adding them' % '  '.join(sorted(extra)))
            d = dbtool_io.extend_dictionary(d, extra)
            dbtool_io.write_dictionary(dpath, d)
            d = dbtool_io.load_dictionary(dpath)
        errs = dbtool_io.validate_folder(a.out, d)
        if errs:
            print('the folder has %d problems:' % len(errs))
            for e in errs[:15]:
                print('  ' + e)
            print('convert the bank again (with the devkit dictionary) and build again')
            return 1
        try:
            failed = dbtool_build.build(a.dbtool, a.out, a.db, a.name, language=a.language,
                                        hidden=not a.show_dbtool)
        except dbtool_build.DBToolError as ex:
            print('error: %s' % ex)
            return 1
        print('DB built: %s%s' % (a.db, '' if not failed else ' (%d files not added)' % len(failed)))
        return 1 if failed else 0

    if a.cmd == 'convert':
        conv = convert.Converter(a.bank, a.out, a.lang, None, a.groups, a.keep, not a.no_derive,
                                 a.merge_groups, not a.no_fill, colors=a.colors,
                                 splice=not a.no_splice, normalize=not a.no_normalize,
                                 brighten={k: float(v) for k, v in (b.split('=') for b in a.brighten)},
                                 avoid=a.avoid, extras='none' if a.no_extras else a.extras,
                                 evec_map=a.evec_map, evec_phonemes=a.evec_phonemes,
                                 custom_extras=a.custom_extra, evec_mode=a.evec_mode, v_mode=a.v_mode)
        if a.list_unknown:
            from u2v import select as _Sel
            for raw, grp in _Sel.find_unmapped(a.bank, conv.lang):
                print('  %-30s (%s)' % (raw, grp))
            print('(use --custom-extra ALIAS=PH:Group (e.g. cough=ch:UnvoicedPlosives), repeatable)')
            return 0
        dpath = a.dict or default_dict(conv.lang)
        if a.dict and not os.path.exists(a.dict):
            print('error: --dict %s does not exist' % a.dict)
            return 2
        if not dpath:
            # without it no dictionary.txt is written and the DB can't be built: stop here, loudly
            print('error: ' + NO_DICT % {'ja': 'Japanese', 'en': 'English'}.get(conv.lang, conv.lang))
            return 2
        # b, b', p\', v / v', breaths, customs: phonemes the stock dictionaries lack are added
        conv.use_dictionary(dbtool_io.load_dictionary(dpath), extend=not a.no_extend_dict)
        print('dictionary: %s (%d phonemes)' % (dpath, len(conv.dict.voiced)))
        conv.run()
        errs = dbtool_io.validate_folder(a.out, conv.dict_out)
        print('validation: %s' % ('OK' if not errs else '%d problems' % len(errs)))
        for e in errs[:30]:
            print('  ' + e)
        print('report: %s' % os.path.join(a.out, 'report.txt'))
        return 1 if errs else 0

    # the folder's own dictionary.txt is what the DB gets built from (it has b, b', p' and EVEC phonemes)
    own = os.path.join(a.out, 'dictionary.txt')
    dpath = a.dict or (own if os.path.exists(own) else default_dict(a.lang))
    d = dbtool_io.load_dictionary(dpath) if dpath and os.path.exists(dpath) else None
    errs = dbtool_io.validate_folder(a.out, d)
    if d is None:
        errs.append('no dictionary.txt and no devkit dictionary found: phonemes not checked, and the DB '
                    "can't be built from this folder. Convert again (see the convert step's error).")
    elif not os.path.exists(own):
        errs.append('no dictionary.txt in the folder: convert again before building')
    for e in errs:
        print(e)
    print('OK' if not errs else '%d problems' % len(errs))
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
