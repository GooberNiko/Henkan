"""Choosable extras and EVEC colour mappings.

Extras: the user picks which bonus phonemes to convert (br1..br5, brE, brI,
rr, vend for vowel endings like "a R"). None means all, empty set means none.

EVEC: the user picks which voice colour (append/prefix/folder) goes into the
DB, which EVEC number (#1, #2, ...) each gets, and optionally which phonemes
each colour covers. The converter finds that colour's transitions from its own
entries; a colour with no vowels still converts, with a warning.
"""
import os
import re
from collections import defaultdict

MAIN_TOKENS = {'', 'main', 'none', '-', '(main)', '(none)'}
SKIP_TOKENS = {'skip', 'off', 'exclude', 'excluded', 'no', 'drop', 'hide'}

EVEC_MODES = ('full', 'vowels')

PHONEME_RE = re.compile(r'[A-Za-z][A-Za-z0-9_\']{0,29}')
VOICED_TOKENS = {'voiced', 'true', 'yes', 'v', 'voiz', '+v'}
UNVOICED_TOKENS = {'unvoiced', 'false', 'no', 'uv', 'unvoi', '-v', 'unvoice'}

# The DBTool's own phonetic unit groups (its "Select Phonetic Unit Group" dialog)
# with the voiced flag each one is written with into dictionary.txt. A custom
# extra lives in one of these so the devkit recognizes it.
PHONETIC_GROUPS = {'Vowels': True, 'Semivowels': True, 'Nasals': True,
                   'UnvoicedFricatives': False, 'VoicedFricatives': True, 'Whisper': False,
                   'VoicedAffricates': True, 'UnvoicedAffricates': False, 'Liquids': True,
                   'UnvoicedPlosives': False, 'VoicedPlosives': True, 'Silence': False}
_GROUP_CANON = {g.lower(): g for g in PHONETIC_GROUPS}


def normalize_suffix(spec):
    """User EVEC number -> '' (main), '#N', or None (skip this colour)."""
    if spec is None:
        return None
    t = str(spec).strip()
    if t.lower() in SKIP_TOKENS:
        return None
    if t.lower() in MAIN_TOKENS:
        return ''
    m = re.fullmatch(r'#?(\d+)', t)
    if not m:
        raise ValueError("EVEC number should look like #1 (or 'main', 'skip'): %r" % (spec,))
    n = int(m.group(1))
    if not 1 <= n <= 9:
        raise ValueError("EVEC number out of range (1-9): %r" % (spec,))
    return '#%d' % n


def parse_evec_map(spec):
    """'Soft=#1,Whisper=#2,Power=main,Old=skip' -> {colour: suffix|None}. None = automatic."""
    if spec is None:
        return None
    if isinstance(spec, dict):
        return {str(k): normalize_suffix(v) for k, v in spec.items()}
    out = {}
    for part in re.split(r'[;\n]+', str(spec)):
        for chunk in part.split(','):
            chunk = chunk.strip()
            if not chunk:
                continue
            if '=' in chunk:
                colour, suffix = chunk.split('=', 1)
            elif ':' in chunk:
                colour, suffix = chunk.split(':', 1)
            else:
                raise ValueError("EVEC map entry should look like Soft=#1: %r" % (chunk,))
            colour = colour.strip()
            if not colour:
                raise ValueError("EVEC map entry with no colour name: %r" % (chunk,))
            out[colour] = normalize_suffix(suffix.strip())
    return out


def parse_evec_phonemes(spec):
    """'Soft:a,i,M,e,o;Whisper:a,e' -> {colour: {phonemes}}. None = all phonemes."""
    if spec is None:
        return None
    if isinstance(spec, dict):
        return {str(k): set(v) for k, v in spec.items()}
    out = {}
    for part in re.split(r'[;\n]+', str(spec)):
        part = part.strip()
        if not part:
            continue
        if ':' not in part and '=' not in part:
            raise ValueError("EVEC phonemes entry should look like Soft:a,i,e: %r" % (part,))
        colour, phs = re.split(r'[:=]', part, maxsplit=1)
        colour = colour.strip()
        got = {p.strip() for tok in phs.split(',') for p in tok.split() if p.strip()}
        if not colour or not got:
            raise ValueError("EVEC phonemes entry should look like Soft:a,i,e: %r" % (part,))
        out[colour] = got
    return out


def apply_evec_map(order, default_suffix, evec_enabled, evec_map, log=None):
    # order: [main colour, ...]. Returns ({colour: suffix}, [dropped]).
    # Explicitly chosen # numbers are never reused by the automatic ones.
    resolved = {}
    if evec_map:
        lower = {c.lower(): c for c in order}
        for name, sfx in evec_map.items():
            key = lower.get(name.lower())
            if key is None:
                close = [c for c in order if name.lower() in c.lower() or c.lower() in name.lower()]
                if len(close) == 1:
                    key = close[0]
            if key is None:
                if log:
                    log('EVEC map: colour not in the bank, ignored')
                continue
            resolved[key] = sfx
    dropped = sorted(k for k, v in resolved.items() if v is None)
    if not evec_enabled:
        if resolved and log is not None:
            log('EVEC numbers need evec colours (ignored here); skipped colours still apply')
        return ({k: '' for k in order if k not in dropped}, dropped)
    taken = set()
    for v in resolved.values():
        if v and v[:1] == '#':
            taken.add(v)
    suffix, n = {}, 0
    for k in order:
        if k in resolved:
            suffix[k] = resolved[k] or ''
        elif order and k == order[0]:
            suffix[k] = ''
        else:
            n += 1
            while ('#%d' % n) in taken:
                n += 1
            suffix[k] = '#%d' % n
            taken.add(suffix[k])
    for d in dropped:
        suffix.pop(d, None)
    return suffix, dropped


def normalize_evec_mode(spec):
    if spec is None:
        return 'full'
    t = str(spec).strip().lower().replace('_', '-').replace(' ', '-')
    if t in ('full', 'all', 'consonants', 'classic'):
        return 'full'
    if t in ('vowels', 'vowel', 'vowels-only', 'vowel-only', 'support', 'easy', 'evec-support'):
        return 'vowels'
    raise ValueError("EVEC mode should be 'full' or 'vowels': %r" % (spec,))


END_TOKENS = {'end', 'ending', 'phrase-end'}


class CustomExtras(dict):
    """{alias: (PHONEME, voiced, group)}; .ends = the phonemes that only end a phrase (never lead into a
    vowel, so no [PH V] joins are made for them)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ends = set()


def parse_custom_extras(spec):
    """'cough=ch:UnvoicedPlosives, a R=hh:Breaths:end' -> {alias: (PHONEME, voiced, group)}.

    A trailing ':end' marks a phoneme that only ends a phrase (a breath out): it gets no joins into vowels.

    The type slot takes a DBTool phonetic unit group (Vowels, Semivowels, Nasals,
    UnvoicedFricatives, VoicedFricatives, Whisper, VoicedAffricates,
    UnvoicedAffricates, Liquids, UnvoicedPlosives, VoicedPlosives, Silence) —
    the group implies its voiced flag — or plain voiced/unvoiced, which keep the
    entry in the 'Custom' group.

    Any UTAU alias can become a standalone extra phoneme: the entry is cut by
    level like a breath (Sil PH Sil) and the phoneme is added to dictionary.txt
    with the TYPE you pick (voiced/unvoiced) so the DBTool accepts it.
    Repeatable on the CLI (--custom-extra ... --custom-extra ...); entries are
    ';' or newline separated inside one string. None/'' = no customs.
    """
    out = CustomExtras()
    if spec is None:
        return out
    if isinstance(spec, dict):
        items = list(spec.items())
    elif isinstance(spec, (list, tuple)):
        items = []
        for part in spec:
            items.extend(_split_custom_parts(part))
    else:
        items = _split_custom_parts(spec)
    for item in items:
        if isinstance(item, tuple):
            alias, rest = item
        else:
            if '=' not in item and ':' not in item:
                raise ValueError("custom extra should look like cough=ch:unvoiced: %r" % (item,))
            alias, rest = re.split(r'[=:]', item, maxsplit=1)
            alias, rest = alias.strip(), rest.strip()
        if not alias:
            raise ValueError("custom extra with no alias: %r" % (item,))
        bits = [b.strip() for b in rest.split(':') if b.strip()]
        if not bits:
            raise ValueError("custom extra %r needs a phoneme name: %r" % (alias, item))
        ph = bits[0]
        if not PHONEME_RE.fullmatch(ph):
            raise ValueError("bad phoneme name %r (letters/digits/_/', max 30 chars)" % ph)
        voiced, group, explicit = None, 'Custom', False
        for tok in bits[1:]:
            tl = tok.lower()
            if tl in END_TOKENS:
                out.ends.add(ph)
            elif tl in VOICED_TOKENS:
                voiced, explicit = True, True
            elif tl in UNVOICED_TOKENS:
                voiced, explicit = False, True
            elif tl in _GROUP_CANON:
                group = _GROUP_CANON[tl]
                if not explicit:  # the group implies its voiced flag unless told otherwise
                    voiced = PHONETIC_GROUPS[group]
            elif re.fullmatch(r'[A-Za-z][A-Za-z0-9 _-]{0,29}', tok):
                group = tok
            else:
                raise ValueError("custom extra %r: %r should be voiced/unvoiced or a group name "
                                 "(%s)" % (alias, tok, ', '.join(PHONETIC_GROUPS)))
        if voiced is None:
            voiced = False
        if ph in out.values() and ph not in [v[0] for k, v in out.items() if k == alias]:
            pass  # same phoneme from several aliases is fine (breath variants)
        out[alias] = (ph, voiced, group)
    # two aliases mapping to different phoneme spellings that differ only by case collide in the DB
    lower = {}
    for alias, (ph, _v, _g) in out.items():
        if ph.lower() in lower and lower[ph.lower()] != ph:
            raise ValueError("custom phonemes %r and %r differ only by case" % (lower[ph.lower()], ph))
        lower[ph.lower()] = ph
    return out


def _split_custom_parts(spec):
    if spec is None:
        return []
    if isinstance(spec, tuple) and len(spec) == 2:
        return [spec]
    return [c.strip() for p in re.split(r'[;\n]+', str(spec)) for c in p.split(',') if c.strip()]


def find_unmapped(bank_root, lang='ja'):
    """Aliases in the bank that map to nothing (neither extra nor language phonemes).

    These are the candidates for --custom-extra. Returns [(raw_alias, group)]."""
    from . import oto, phonemes as P
    entries = oto.read_bank(bank_root)
    if lang == 'auto':
        lang = P.detect_language([e.alias for e in entries])
    out, seen = [], set()
    for e in entries:
        key = (e.raw_alias, e.group)
        if key in seen:
            continue
        seen.add(key)
        ex = P.extra_alias(e.alias + (str(e.dup) if e.dup else ''))
        if ex is None and P.alias_to_phonemes(e.alias, lang) is None:
            out.append((e.raw_alias, e.group))
    return out


def all_aliases(bank_root, lang='ja'):
    """Every alias in the bank with what it maps to: [(raw_alias, group, maps_to)].

    maps_to is a short display string (extra kind or phoneme list) or None for
    unmapped aliases. The Custom extras picker falls back to this when nothing
    is unmapped, so any sound can still be forced into its own phoneme."""
    from . import oto, phonemes as P
    entries = oto.read_bank(bank_root)
    if lang == 'auto':
        lang = P.detect_language([e.alias for e in entries])
    out, seen = [], set()
    for e in entries:
        key = (e.raw_alias, e.group)
        if key in seen:
            continue
        seen.add(key)
        ex = P.extra_alias(e.alias + (str(e.dup) if e.dup else ''))
        ph = P.alias_to_phonemes(e.alias, lang)
        if ex is not None:
            maps_to = 'vowel ending -> %s' % ' '.join(ex)
        elif ph is not None:
            maps_to = ' -> %s' % ' '.join(ph)
        else:
            maps_to = None
        out.append((e.raw_alias, e.group, maps_to))
    return out


def alias_rows(bank_root, lang='ja'):
    """What the Custom extras window lists: one dict per alias a custom extra can name.

    key       how the custom extra is written (unknown aliases and vowel endings merge across pitches,
              aliases that already sing something stay one per recording)
    count     recordings it covers, groups: the folders they are in
    maps_to   what it is now: 'unknown', 'vowel ending [a Sil]' or its phonemes
    lead      the vowel it starts with ("a R" -> 'a'): a custom extra on it keeps that transition
    kind      'unknown', 'ending' or 'sung' (the window sorts and filters by it)"""
    from . import oto, phonemes as P
    entries = oto.read_bank(bank_root)
    if lang == 'auto':
        lang = P.detect_language([e.alias for e in entries])
    rows = {}
    for e in entries:
        own = P.alias_to_phonemes(e.alias, lang)
        vend = P.extra_alias(e.alias + (str(e.dup) if e.dup else ''))
        ending = vend is not None or (own is not None and len(own) == 2 and own[1] == 'Sil'
                                      and own[0] in P.SUSTAINED)
        numbered = e.alias + (str(e.dup) if e.dup else '')          # "breath1", "breath2" stay apart
        key = numbered if (own is None or ending) else e.raw_alias.strip()
        r = rows.get(key)
        if r is None:
            ex, ph = P.extra_alias(key), P.alias_to_phonemes(key, lang)
            if ex is None and ph is not None and len(ph) == 2 and ph[1] == 'Sil' and ph[0] in P.SUSTAINED:
                ex = ph
            kind = 'ending' if ex is not None else 'sung' if (ph is not None or own is not None) else 'unknown'
            maps = ('vowel ending [%s]' % ' '.join(ex) if ex is not None else
                    ' '.join(ph or own) if kind == 'sung' else 'unknown')
            r = rows[key] = {'key': key, 'count': 0, 'groups': set(), 'maps_to': maps, 'kind': kind,
                             'lead': P.lead_vowel(key) if lang == 'ja' else None}
        r['count'] += 1
        r['groups'].add(e.group)
    order = {'unknown': 0, 'ending': 1, 'sung': 2}
    return sorted(rows.values(), key=lambda r: (order[r['kind']], r['key']))


def auto_evec_map(rows):
    """[(colour, n, ...)] (main colour first) -> 'Main=main, Other=#1, ...' spec string."""
    return ', '.join('%s=%s' % (c, 'main' if i == 0 else '#%d' % min(i, 9))
                      for i, (c, _n, *_r) in enumerate(rows))


def suggest_extra_phoneme(alias, taken=()):
    """Prefill phoneme name for a custom extra: first 2 letters, numbered if taken."""
    letters = ''
    kana_bits = re.findall(r'[぀-ヿ]+', alias)
    if kana_bits:  # romanize kana so か suggests 'ka', うあ suggests 'ua'
        from . import phonemes as _P
        rom = _P.kana_to_romaji(_P.kata_to_hira(''.join(kana_bits)))
        if rom:
            letters = re.sub(r'[^A-Za-z]', '', ''.join(rom)).lower()
    if not letters:
        letters = re.sub(r'[^A-Za-z]', '', alias).lower() or 'ex'
    base = letters[:2]
    taken = {t.lower() for t in taken}
    cand, n = base, 1
    while cand.lower() in taken:
        n += 1
        cand = '%s%d' % (base, n)
    return cand


def bank_colours(bank_root):
    """Detected voice colours without touching audio: [(colour, entries, pitches, groups)]."""
    from . import oto, phonemes as P
    entries = oto.read_bank(bank_root)
    for e in entries:
        e.color = P.NUMBERING.sub(r'\1', P.METHOD_WORDS.sub('', e.color)).strip(' _-/.') or 'main'
    size = defaultdict(int)
    for e in entries:
        size[e.color] += 1
    if not size:
        return [], entries
    big = max(size.values())
    keep = [c for c in size if size[c] >= 0.1 * big]
    cp = os.path.commonprefix(keep) if len(keep) > 1 else ''

    def main_like(c):
        return c[len(cp):] == '' or bool(P.MAIN_COLOR.search(c))
    default = max(keep, key=lambda c: (main_like(c), size[c]))
    order = [default] + sorted((k for k in keep if k != default), key=lambda k: (-size[k], k))
    rows = []
    for c in order:
        pitches = sorted({e.pitch for e in entries if e.color == c and e.pitch})
        groups = sorted({e.group for e in entries if e.color == c})
        rows.append((c, size[c], pitches, groups))
    return rows, entries


def prefix_affixes(bank_root):
    """prefix.map (prefix, suffix) pairs plus alias tags seen in oto.ini files."""
    from . import oto
    pairs = sorted(oto.read_prefix_map(bank_root))
    return ['%s+%s' % (pre or '-', suf or '-') for pre, suf in pairs]
