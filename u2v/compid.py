"""VOCALOID Component IDs: what they are, and how to get a safe one for your bank.

A CompID is the voicebank's opaque 16-character ID (capital letters + digits,
e.g. BKGHF5N5PSTN4KD6). The editor uses it to tell banks apart: it goes in the
.vvd metadata (VOICEIDSTR), names the package folder, and keys the registry
entries. Two banks on one machine must not share an ID; repackaging the SAME
bank reuses its ID.

What the characters mean (from the VVD Editor Plus help page and your decoded
diagram): a CompID is a 14-character raw string in disguise::

    [0] [DRP 6 chars] [00] [version 5 digits]      (14 chars, e.g. 00C11100040000)
           | | |_ product code (last 3 of the DRP)
           | |_ language code: 0-JPN 1-ENG 2-KOR 3-ESP 4-CHN
           |_ vendor code (first 2 of the DRP)

The raw string is scrambled into the 16-char CompID with a substitution table
plus two checksum-dependent shifts; the last 2 CompID chars are the checksum
(0->K, 1->L). ``encode_raw``/``decode`` below are a straight port of the VVD
Editor Plus page script (slidingwall/VVD help.html).

Honest limits: the editor treats the CompID as an opaque string — it does NOT
check the checksum, so real commercial IDs sometimes don't decode (one of the
published English pool IDs doesn't). What DOES matter per language is the
language digit: install under an ID whose language matches your bank (the V4
editor checks this). For V4 Japanese/Chinese/English banks the
community-standard move is reusing an ID from the pools of known-valid ones
below; for Korean/Spanish (Yamaha never shipped V4 banks for them, so no pools
exist) you craft a fresh ID with the right language digit via ``generate``.
"""

import random
import re
import sys

COMPID_RE = re.compile(r'^[A-Z0-9]{16}$')
COMPID_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ0123456789'  # no I/L/O: unambiguous on sight

# Known-valid V4 pools published with VVD Editor Plus (slidingwall/VVD help page).
# These are real commercial-bank IDs the community reuses for custom banks: pick one
# that is NOT already registered on your machine.
JPN_POOL = [
    'BPAMGAZ8RNZS5PE9', 'BMADD8MZMLZH2MDE', 'BX3LFFY7PWPR4XF3', 'BE9H82K5FDYNTEDD',
    'BE8DC2AZLDXHZECF', 'BM9KD8N6MLYP2MEK', 'BD7EEZK2NCWK3DDF', 'BNBCB9CYKM2GYNCE',
    'BN6GD9W4MMTM2NEA', 'BT4GND44YSRMATF6', 'BT8ELDP2WSXK8TE2', 'BX6LMFX7XWTR9XEC',
    'BX4PEFHANWRW3XEL', 'BY9DKG3ZTXYH7YEE', 'BL8HE745NKXN3LFK', 'BH9EE5E2NGYK3HD2',
    'BN6EL9Z2WMTK8NEF', 'BM5M98B8GLSSWMD3', 'BCBFCYK3LB2LZCD4',
]
CHN_POOL = [
    'BX49FFZ2PWRK4XE7', 'BL5H97EAGKSWWLDL', 'BN6E7987EMTRSNC3', 'BCBACYR3LB2LZCD4',
    'BM5F98G8GLSSWMD3', 'BKA8C6BZLHZHZKC2', 'BMA8D82ZMLZH2MDE', 'BN6BD964MMTM2NEA',
    'BK6CF6M5PHTN4KD6', 'BM9DD826MLYP2MEK', 'BC6ADYL3MBTL2CD5', 'BT89LD22WSXK8TE2',
    'BMAAH863SLZL6ME4', 'BL7BD7C4MKWM2LC6', 'BY4FEGX8NXRS3YE3', 'BM6888CZFLTHTMC7',
    'BKCEC6L7LH3RZKC8', 'BK8H79SAEHXWSKDB', 'BKCEC9G7LH3RZKC8', 'BMA8DBXZMLZH2MDE',
    'BD7B84P4FCWMTDDA', 'BX6EMK57XWTR9XEC', 'BW7HCH5ALTWWZWEB', 'BD79E4T2NCWK3DDF',
    'BM688B9ZFLTHTMC7', 'BM9DDBY6MLYP2MEK', 'BC6AD3G3MBTL2CD5', 'BN6BDC34MMTM2NEA',
    'BCBAC3M3LB2LZCD4', 'BL7BDA94MKWM2LC6',
]
ENG_POOL = [
    'BXI5WRTWNYF9CSG6', 'BLLR2PRJLTVT7VA0', 'BAIOIIQUZET7K7TZ', 'BAOGOJOS7JV24L3A',
    'BTT44A75Z6D0A5FA', 'BIXIAD9ONT2XK45H', 'BP1HG461N9ZKPPLX', 'BSCYZZIGTPLT1ZGH',
    'BVT0H52GWP0X38UI', 'BMBYE2CCHLCL16N6', 'BGK5C1CWQ3XLZ1YU', 'BXF9LMPMWPQ8YUIB',
    'BB1M9132ZR8GT601', 'BZV0EWHRW5HKOH07', 'BPVF1TRUKA4C7TU3', 'BGJYU3KQ9ZHYZNYJ',
    'BU1V7ROSXNGYUS38', 'BXO5Q2H7B2V2XAGX', 'BE1VO1T2JVRLQ8ZV', 'BAIOW91Q7N1RHYW1',
    'BUX9X4GS1P33VAXW', 'BO2LE8MD6RM6JB4T', 'BNSHEXH5U91KGPJA', 'BHG08OHURSXGBTRX',
    'BM6AW0CLPJIWZSOV',
]

# language -> pool of known-valid V4 IDs, or None (no public pool: craft one with generate)
POOLS = {'Japanese': JPN_POOL, 'Chinese': CHN_POOL, 'English': ENG_POOL,
         'Korean': None, 'Spanish': None, 'Other': None}

# language digit (3rd char of the DRP) <-> language name
LANGS = {'0': 'Japanese', '1': 'English', '2': 'Korean', '3': 'Spanish', '4': 'Chinese'}
LANG_CODES = {v: k for k, v in LANGS.items()}

WHAT_IS_IT = (
    'A CompID is your voicebank\u2019s ID card: 16 capital letters/digits. It goes in the '
    '.vvd file, names the package folder, and keys the registry entries, so the editor '
    'can tell your bank apart from every other bank.\n'
    'Rules: one bank = one ID, no two banks on the same PC may share one, the ID\u2019s '
    'language must match your bank (the V4 editor checks this), and repackaging the same '
    'bank reuses its ID. For V4 Japanese/Chinese/English banks the community reuses IDs '
    'from pools of known-valid ones; for Korean/Spanish (no V4 pools exist) you craft a '
    'fresh ID with the right language digit instead.'
)


class CompIDError(RuntimeError):
    pass


# ------------------------------------------------------------------ raw <-> CompID codec
# Straight port of the VVD Editor Plus help-page script (slidingwall/VVD).

_CHAR = ['BAEB2T9XAGMFMA', 'EB8K37HWD62S7T', 'LCA54S5K2DD2E5', 'KDFC54KTEPHNBW',
         'CE266WGLK9EGX8', 'FFBH7ZYHCR7C3B', 'HG9D8BXRH4A6CD', 'DH329YLS5S8PYH',
         'GKDAAR4BGHKKG4', 'AL47BPMM6NW9ZP', '2M6FCXBA ', '7NK4DNW2 ', 'SP73EMAD ',
         'ZRHEFF3C ', 'TS58G8DY ', '8TC9HLC3 ', 'MWGLK92Z ', '9XYZLANN ', 'WYPGMDZP ',
         '4ZNYN2FG ', '32WMPEP8 ', 'N3ZNRKE6 ', '64RXSCR5 ', 'X5TRTHTF ', 'R6LWW564 ',
         '57XPXGS7 ', 'Y8MSY67E ', 'P9STZ389 ']
_CSET = '0123456789ABCDEFGHIJKLMNOPQR'
_ALP = '23456789ABCDEFGHKLMNPRSTWXYZ'
_HIGH = [[1, 8, 8, 7, 1, 8, 8, 7, 1, 8, 8, 7, 1], [2, 3, 0, 4, 2, 3, 0, 4, 2, 3, 0, 4, 2],
         [1, 5, 0, 8, 1, 5, 0, 8, 1, 5, 0, 8, 1], [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
         [1, 1, 0, 2, 1, 1, 0, 2, 1, 1, 0, 2, 1], [2, 9, 0, 4, 2, 9, 0, 4, 2, 9, 0, 4, 2],
         [5, 0, 3, 0, 5, 0, 3, 0, 5, 0, 3, 0, 5], [0, 5, 0, 5, 0, 5, 0, 5, 0, 5, 0, 5, 0],
         [7, 0, 2, 1, 7, 0, 2, 1, 7, 0, 2, 1, 7], [9, 0, 1, 5, 9, 0, 1, 5, 9, 0, 1, 5, 9],
         [0, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0], [1, 1, 0, 3, 1, 1, 0, 3, 1, 1, 0, 3, 1],
         [2, 3, 1, 1, 2, 3, 1, 1, 2, 3, 1, 1, 2], [1, 2, 2, 3, 1, 2, 2, 3, 1, 2, 2, 3, 1],
         [9, 1, 2, 8, 9, 1, 2, 8, 9, 1, 2, 8, 9], [8, 0, 1, 9, 8, 0, 1, 9, 8, 0, 1, 9, 8]]
_LOW = [[1, 7, 5, 2, 1, 7, 5, 2, 1, 7, 5, 2, 1], [8, 2, 9, 3, 8, 2, 9, 3, 8, 2, 9, 3, 8],
        [6, 6, 1, 8, 6, 6, 1, 8, 6, 6, 1, 8, 6], [9, 2, 7, 3, 9, 2, 7, 3, 9, 2, 7, 3, 9],
        [1, 8, 2, 6, 1, 8, 2, 6, 1, 8, 2, 6, 1], [1, 3, 2, 7, 1, 3, 2, 7, 1, 3, 2, 7, 1],
        [7, 3, 4, 9, 7, 3, 4, 9, 7, 3, 4, 9, 7], [8, 2, 1, 4, 8, 2, 1, 4, 8, 2, 1, 4, 8],
        [6, 8, 7, 8, 6, 8, 7, 8, 6, 8, 7, 8, 6], [3, 8, 7, 5, 3, 8, 7, 5, 3, 8, 7, 5, 3],
        [2, 4, 3, 2, 2, 4, 3, 2, 2, 4, 3, 2, 2], [7, 5, 9, 1, 7, 5, 9, 1, 7, 5, 9, 1, 7],
        [8, 4, 6, 9, 8, 4, 6, 9, 8, 4, 6, 9, 8], [3, 6, 4, 2, 3, 6, 4, 2, 3, 6, 4, 2, 3],
        [9, 7, 0, 7, 9, 7, 0, 7, 9, 7, 0, 7, 9], [2, 4, 1, 8, 2, 4, 1, 8, 2, 4, 1, 8, 2]]

_RAW_RE = re.compile(r'^[0-9A-R]{7}[0-9]{7}$')  # first 7 in 0-R, last 7 digits


def _find(table, ch):
    """table.index, but -1 when missing (the page script uses indexOf semantics)."""
    try:
        return table.index(ch)
    except ValueError:
        return -1


def _tail_val(ch):
    if ch == 'K':
        return 0
    if ch == 'L':
        return 1
    return _CSET.find(ch)


def encode_raw(raw):
    """A 14-char raw string ([0][DRP 6][00][version 5]) -> its 16-char CompID."""
    raw = (raw or '').strip().upper()
    if not _RAW_RE.match(raw):
        raise CompIDError('a raw string is 14 chars: first 7 are 0-9/A-R '
                          '(0 + 6-char DRP), last 7 are digits (00 + 5-char version), '
                          'e.g. 00C11100040000: %r' % raw)
    o = []
    for i in range(0, 14, 2):
        o.append(_CHAR[_CSET.index(raw[i])][i])
        o.append(_CHAR[_CSET.index(raw[i + 1])][i + 1])
    cs = sum(c.encode('latin-1')[0] for c in o) & 0xFF

    def _shift(table, k):
        for i in range(1, 14):
            o[i] = _ALP[(_find(_ALP, o[i]) + table[k][i - 1]) % 28]

    _shift(_HIGH, cs >> 4)
    _shift(_LOW, cs & 15)

    def _h(n):
        c = format(n, 'X')
        return c.replace('0', 'K').replace('1', 'L')

    return ''.join(o) + _h(cs >> 4) + _h(cs & 15)


def decode(compid):
    """A CompID -> its 14-char raw string, or None if it has no valid checksum.

    None is normal for real commercial-bank IDs: the editor treats the CompID
    as an opaque string and never checks the checksum (one published English
    pool ID doesn't decode). Only shape + local uniqueness gate an install.
    """
    v = (compid or '').strip().upper()
    if len(v) != 16:
        return None
    o = list(v[:14])
    h, lo = _tail_val(v[14]), _tail_val(v[15])
    if h < 0 or lo < 0 or h >= 16 or lo >= 16:
        return None

    def _unshift(table, k):
        for i in range(1, 14):
            o[i] = _ALP[(_find(_ALP, o[i]) - table[k][i - 1] + 28) % 28]

    _unshift(_LOW, lo)
    _unshift(_HIGH, h)
    cs = sum(c.encode('latin-1')[0] for c in o) & 0xFF
    if (cs >> 4) != h or (cs & 15) != lo:
        return None
    out = []
    for i, n in enumerate(o):
        for k, row in enumerate(_CHAR):
            if row[i] == n:
                out.append(_CSET[k])
                break
        else:
            return None
    return ''.join(out)


def describe(raw):
    """A 14-char raw string -> {'vendor', 'lang', 'product', 'version', ...}."""
    raw = (raw or '').strip().upper()
    if not _RAW_RE.match(raw):
        raise CompIDError('not a 14-char raw string: %r' % raw)
    return {'lead': raw[0], 'vendor': raw[1:3], 'lang': raw[3],
            'lang_name': LANGS.get(raw[3], 'unknown(%s)' % raw[3]),
            'product': raw[4:7], 'drp': raw[1:7], 'middle': raw[7:9],
            'version': raw[9:14], 'raw': raw}


def describe_compid(compid):
    """A CompID -> its decoded parts, or None if it has no valid checksum."""
    raw = decode(compid)
    return describe(raw) if raw is not None else None


def random_raw(language='Japanese', rng=None, version='40000'):
    """A fresh 14-char raw string with the right language digit for a V4 bank."""
    rng = rng or random.SystemRandom()
    code = LANG_CODES.get(language)
    if code is None:  # 'Other': any language digit, like the page's random button
        code = rng.choice('01234')
    if not re.match(r'^[0-9]{5}$', version or ''):
        raise CompIDError('a version is 5 digits, e.g. 40000: %r' % version)
    vendor = ''.join(rng.choice(_CSET) for _ in range(2))
    product = ''.join(rng.choice(_CSET) for _ in range(3))
    return '0' + vendor + code + product + '00' + version


def generate(language='Japanese', rng=None, version='40000'):
    """A fresh properly-encoded CompID with the right language digit.

    This is the move for Korean/Spanish banks (no V4 pools exist) and the
    fallback when every pool ID is taken: unlike random_compid it decodes to a
    raw string whose language digit matches your bank. Checked against the
    registry like everything else.
    """
    rng = rng or random.SystemRandom()
    used = compids_in_use()
    for _ in range(100):
        cid = encode_raw(random_raw(language, rng, version))
        if cid not in used:
            return cid
    raise CompIDError('could not dream up a free CompID')


def _reg_open(key_path):
    import winreg
    parts = key_path.split('\\', 1)
    hive = {'HKLM': winreg.HKEY_LOCAL_MACHINE, 'HKCU': winreg.HKEY_CURRENT_USER}[parts[0]]
    return winreg.OpenKey(hive, parts[1], 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)


def compids_in_use():
    """Every registered voicebank CompID on this machine (all editor generations)."""
    found = set()
    if sys.platform != 'win32':
        return found
    for root in ('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE', 'HKLM\\SOFTWARE\\POCALOID4\\DATABASE',
                 'HKLM\\SOFTWARE\\VOCALOID2\\DATABASE\\VOICE',
                 'HKLM\\SOFTWARE\\VOCALOID3\\DATABASE\\VOICE2',
                 'HKLM\\SOFTWARE\\VOCALOID3\\DATABASE\\VOICE3'):
        try:
            with _reg_open(root) as k:
                import winreg
                i = 0
                while True:
                    try:
                        found.add(winreg.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
    return found


def normalize(compid):
    """Uppercase + validate the shape; raises CompIDError if malformed."""
    compid = (compid or '').strip().upper()
    if not COMPID_RE.match(compid):
        raise CompIDError('a CompID is 16 capital letters/digits, e.g. BKGHF5N5PSTN4KD6: %r' % compid)
    return compid


def status_of(compid):
    """(ok, message): is this ID well-formed and free on this machine? Never raises."""
    try:
        cid = normalize(compid)
    except CompIDError as ex:
        return False, str(ex)
    if cid in compids_in_use():
        return False, '%s is already registered on this machine — pick another' % cid
    return True, '%s looks good and is free on this machine' % cid


def random_compid(rng=None):
    """A fresh random ID ('B' + 15 unambiguous chars), checked against this machine."""
    rng = rng or random.SystemRandom()
    used = compids_in_use()
    for _ in range(100):
        cid = 'B' + ''.join(rng.choice(COMPID_ALPHABET) for _ in range(15))
        if COMPID_RE.match(cid) and cid not in used:
            return cid
    raise CompIDError('could not dream up a free CompID')


def pick_from_pool(language='Japanese', rng=None):
    """A random ID from the language pool that is free on this machine.

    Raises CompIDError for languages without a pool (use generate) or when
    every pool ID is taken locally (use generate).
    """
    pool = POOLS.get(language)
    if not pool:
        raise CompIDError('no ID pool for %s: craft one with the right language digit instead' % language)
    rng = rng or random.SystemRandom()
    used = compids_in_use()
    free = [c for c in pool if c not in used]
    if not free:
        raise CompIDError('every %s pool ID is taken on this machine: craft a fresh one' % language)
    return rng.choice(free)


def suggest(language='Japanese', rng=None):
    """Best ID for a new bank of this language: pool pick, else a crafted one."""
    try:
        return pick_from_pool(language, rng)
    except CompIDError:
        return generate(language, rng)
