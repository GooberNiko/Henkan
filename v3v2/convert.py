"""VOCALOID3/4 voicebank (.ddi + .ddb) -> VOCALOID2 voicebank.

The sample data (.ddb) is the same format in both versions, so it is linked or copied as is.
Only the index (.ddi) is rewritten:

* the header becomes the plain VOCALOID2 "DBS " singer header (no format version field),
* the VOCALOID4 growl data (VQM) is dropped, VOCALOID2 has no growl,
* VOCALOID3 triphones (an articulation nested in an articulation) are dropped,
* every diphone's segment table is written back in the VOCALOID2 layout.
"""
import os
import shutil
import struct

from . import ddi

DROP = {b'VQM ', b'VQMu', b'VQMp', b'ARTx'}


class Report:
    def __init__(self):
        self.diphones = 0
        self.stationaries = 0
        self.triphones = 0
        self.growl = 0
        self.skipped = 0
        self.notes = []

    def lines(self):
        out = ['diphones kept: %d' % self.diphones, 'stationaries kept: %d' % self.stationaries]
        if self.triphones:
            out.append('triphones dropped (VOCALOID2 has none): %d' % self.triphones)
        if self.growl:
            out.append('growl data dropped (VOCALOID2 has no growl): %d' % self.growl)
        if self.skipped:
            out.append('units skipped (unexpected layout): %d' % self.skipped)
        return out + self.notes


def _v2_stationary_post(post):
    """VOCALOID2 stationaries carry L = frame count and four -1 marks."""
    n = struct.unpack_from('<I', post, 4)[0]
    o = 8 + 8 * n + struct.calcsize('<IHIQ')
    return struct.pack('<I', n) + post[4:o] + struct.pack('<4i', -1, -1, -1, -1)


def _v2_post(post):
    """Rewrite an articulation part's tail from the V3+ layout to the V2 one, or None."""
    n = struct.unpack_from('<I', post, 0)[0]
    o = 4 + 8 * n
    frames = post[4:o]
    a1, h, a2, _wave_start, wave_end, m = struct.unpack_from('<IHIQQI', post, o)
    o += struct.calcsize('<IHIQQI')
    if m != 2 or len(post) != o + 32:
        return None
    s0, e0, f0, g0, s1, e1, f1, g1 = struct.unpack_from('<8i', post, o)
    if s0 != 0 or s1 != e0 or e1 != n:
        return None
    # DSE4 rebuilds [0, L, a, b+1] and [L, n, d, c+1] from the V2 fields L, a, b, c, d.
    return (struct.pack('<II', e0, n) + frames + struct.pack('<IHIQ', a1, h, a2, wave_end)
            + struct.pack('<4i', f0, g0 - 1, g1 - 1, f1))


def _convert(node, rep, parent_tag=None):
    keep = []
    for c in node.children:
        if c.tag in DROP:
            rep.growl += c.tag.startswith(b'VQM')
            continue
        if node.tag == b'ART ' and c.tag == b'ART ':
            rep.triphones += sum(1 for _, x in c.walk() if x.tag == b'ARTp')
            continue
        if c.tag == b'ARTp' and not c.stub:
            post = _v2_post(c.post)
            if post is None:
                rep.skipped += 1
                continue
            c.post = post
            rep.diphones += 1
        elif c.tag == b'STAp' and not c.stub:
            c.post = _v2_stationary_post(c.post)
            rep.stationaries += 1
        if not c.stub:
            _convert(c, rep, node.tag)
        if c.tag in (b'ARTu', b'STAu') and not any(x.tag in (b'ARTp', b'STAp') for x in c.children):
            continue                                 # every part of this unit was dropped
        keep.append(c)
    node.children = keep
    node.count = len(keep)


MIN_HALF = 2          # frames the middle phoneme must keep in a diphone cut from a triphone
MAX_TAKEN = 4         # cut parts kept per recovered diphone (longest first)


def _name(label):
    b = label.encode('latin1')
    return struct.pack('<I', len(b)) + b


def _clone(node, label=None):
    n = ddi.Node()
    n.__dict__.update(node.__dict__)
    n.children = list(node.children)
    if label is not None:
        n.name = _name(label)
    return n


def _cut_part(part, first, last):
    """A V3 diphone part made of records first..last (2 records) of a triphone part, or None."""
    p = part.post
    n = struct.unpack_from('<I', p, 0)[0]
    o = 4 + 8 * n
    a1, h, _cnt, _u1, u2, m = struct.unpack_from('<IHIQQI', p, o)
    if m != 4:
        return None
    recs = [struct.unpack_from('<4i', p, o + 30 + 16 * i) for i in range(4)]
    (s0, e0, f0, g0), (s1, e1, f1, g1) = recs[first], recs[last]
    if s1 != e0 or e1 <= s0:
        return None
    mid_frames = (e0 - s0) if first == 1 else (e1 - s1)
    if mid_frames < MIN_HALF:
        return None
    frames = p[4 + 8 * s0:4 + 8 * e1]
    k = e1 - s0
    start = u2 + 2 * 256 * s0                        # recording sample of the first kept frame
    post = (struct.pack('<I', k) + frames
            + struct.pack('<IHIQQI', a1, h, k * 256 + 2048, start - 2048, start, 2)
            + struct.pack('<8i', 0, e0 - s0, f0 - s0, g0 - s0, e0 - s0, k, f1 - s0, g1 - s0))
    c = _clone(part)
    c.post = post
    return c, mid_frames


def recover_from_triphones(root):
    """Add the diphones VOCALOID2 would otherwise miss, cut out of VOCALOID3 triphones.

    A triphone is stored as ART <a> / ART ^<b> / ARTu <c> / ARTp with four records:
    a, first half of b, second half of b, c. Records 1-2 are the diphone [a b] and records
    3-4 the diphone [b c]; frame times already restart at every record. Returns how many
    diphones were added."""
    dbv = [c for c in root.children if c.tag == b'DBV ']
    arts = [c for c in dbv[0].children if c.label == 'articulation'] if dbv else []
    if not arts:
        return 0
    art = arts[0]
    by_first = {a.label: a for a in art.children if a.tag == b'ART '}
    have = {(a.label, u.label) for a in by_first.values() for u in a.children
            if u.tag == b'ARTu' and any(x.tag == b'ARTp' for x in u.children)}
    ids = {}                                          # phoneme -> id, from ART bodies and unit bodies
    for a in by_first.values():
        ids[a.label] = a.body[:4]
        for u in a.children:
            if u.tag == b'ARTu':
                ids.setdefault(u.label, u.body[:4])
            elif u.tag == b'ART ':
                ids.setdefault(u.label.lstrip('^'), u.body[:4])
    found = {}                                        # (x, y) -> [(frames, part, unit template)]
    for a in by_first.values():
        for mid in a.children:
            if mid.tag != b'ART ':
                continue
            b = mid.label.lstrip('^')
            for u in mid.children:
                if u.tag != b'ARTu':
                    continue
                for part in u.children:
                    if part.tag != b'ARTp' or part.stub:
                        continue
                    for key, rng in (((a.label, b), (0, 1)), ((b, u.label), (2, 3))):
                        if key in have:
                            continue
                        cut = _cut_part(part, *rng)
                        if cut:
                            found.setdefault(key, []).append((cut[1], cut[0], u))
    added = 0
    for (x, y), cands in sorted(found.items()):
        if x not in ids or y not in ids:
            continue
        cands.sort(key=lambda c: -c[0])
        parent = by_first.get(x)
        if parent is None:
            parent = _clone(next(iter(by_first.values())), x)
            parent.body = ids[x] + parent.body[4:]
            parent.children = []
            art.children.append(parent)
            by_first[x] = parent
        template = cands[0][2]
        unit = _clone(template, y)
        unit.body = ids[y] + template.body[4:]
        unit.children = [c[1] for c in cands[:MAX_TAKEN]]
        unit.count = len(unit.children)
        parent.children.append(unit)
        parent.count = len(parent.children)
        added += 1
    art.count = len(art.children)
    return added


_V2_JA = None
# closest sounding stand-ins, best first
SUBSTITUTES = {
    'N': ['N\\', 'n', "N'"], "N'": ['N', 'J', 'N\\'], 'n': ['N\\', 'N'], 'm': ['N\\', 'n'], "m'": ['m', 'N\\'],
    'J': ['n', "N'"], 'N\\': ['N', 'n', "N'", 'J'],'dZ': ['Z', 'dz', 'tS'], 'dz': ['z', 'dZ', 'ts'], 'Z': ['dZ', 'S'],
    'z': ['dz', 's'], 'h\\': ['h'], "k'": ['k'], "g'": ['g'], "b'": ['b'], "p'": ['p'], "t'": ['t'],
    "d'": ['d'], "4'": ['4'], "p\\'": ['p\\'], 'p\\': ['h'], 'C': ['S', 'h'],
}


def _v2_japanese():
    global _V2_JA
    if _V2_JA is None:
        import json
        with open(os.path.join(os.path.dirname(__file__), 'v2_japanese_units.json'), encoding='utf-8') as f:
            d = json.load(f)
        _V2_JA = ({tuple(p) for p in d['diphones']}, set(d['stationaries']))
    return _V2_JA


def _phonemes(root):
    p = root.phdc
    n = struct.unpack_from('<I', p, 12)[0]
    return [p[16 + 31 * i:16 + 31 * i + 18].split(b'\0')[0].decode('latin1') for i in range(n)]


def _deep(node, label=None):
    """Copy a unit with its own parts, so each copy's tail is converted on its own."""
    c = _clone(node, label)
    c.children = [_deep(x) if not x.stub else x for x in node.children]
    return c


def fill_gaps(root):
    """Give a Japanese bank every transition and held sound VOCALOID2's Japanese lyrics can ask
    for, borrowing the closest-sounding unit it has where a recording is missing. Returns
    (diphones filled, stationaries filled)."""
    names = _phonemes(root)
    if not {'M', 'N\\', 'a'} <= set(names):
        return 0, 0
    ids = {nm: struct.pack('<I', i) for i, nm in enumerate(names)}
    want_di, want_st = _v2_japanese()
    dbv = [c for c in root.children if c.tag == b'DBV ']
    if not dbv:
        return 0, 0
    groups = {c.label: c for c in dbv[0].children}
    art, sta = groups.get('articulation'), groups.get('stationary')
    filled_di = filled_st = 0
    if art is not None:
        by_first = {a.label: a for a in art.children if a.tag == b'ART '}
        units = {(a.label, u.label): u for a in by_first.values() for u in a.children
                 if u.tag == b'ARTu' and any(x.tag == b'ARTp' for x in u.children)}
        for x, y in sorted(want_di, key=lambda p: (p[0] in SUBSTITUTES) + (p[1] in SUBSTITUTES)):
            if (x, y) in units or x not in ids or y not in ids:
                continue
            tries = [(x, s) for s in SUBSTITUTES.get(y, [])] + [(s, y) for s in SUBSTITUTES.get(x, [])]
            tries += [(s, t) for s in SUBSTITUTES.get(x, []) for t in SUBSTITUTES.get(y, [])]
            src = next((units[t] for t in tries if t in units), None)
            if src is None:
                continue
            parent = by_first.get(x)
            if parent is None:
                parent = _clone(next(iter(by_first.values())), x)
                parent.body = ids[x] + parent.body[4:]
                parent.children = []
                art.children.append(parent)
                by_first[x] = parent
            unit = _deep(src, y)
            unit.body = ids[y] + src.body[4:]
            parent.children.append(unit)
            parent.count = len(parent.children)
            units[(x, y)] = unit
            filled_di += 1
        art.count = len(art.children)
    if sta is not None:
        for group in sta.children:
            have = {u.label: u for u in group.children if u.tag == b'STAu'}
            for v in sorted(want_st, key=lambda v: v in SUBSTITUTES) * 2:   # twice: stand-ins can chain
                if v in have or v not in ids:
                    continue
                src = next((have[s] for s in SUBSTITUTES.get(v, []) if s in have), None)
                if src is None:
                    continue
                unit = _deep(src, v)
                unit.body = ids[v] + src.body[4:]
                group.children.append(unit)
                have[v] = unit
                filled_st += 1
            group.count = len(group.children)
    return filled_di, filled_st


def _serialize(node, out):
    out += node.hdr
    if node.stub:
        out += node.name
        return
    if node.flags:
        out += node.flags
    out += node.body
    if node.flags:
        out += struct.pack('<I', len(node.children))
        for c in node.children:
            _serialize(c, out)
    out += node.post
    out += node.name


def to_v2(data):
    """Convert V3/V4 .ddi bytes to V2 .ddi bytes. Returns (bytes, Report)."""
    root, _ = ddi.parse_auto(data, engine=4)
    if root.tag == b'ARR2':
        rep = Report()
        rep.notes.append('already a VOCALOID2 index, copied unchanged')
        return data, rep
    return root_to_v2(root)


def root_to_v2(root):
    """Convert a parsed V3/V4 singer tree to V2 .ddi bytes. Returns (bytes, Report)."""
    rep = Report()
    if root.tag not in (b'DBS ', b'DBSe'):
        raise ddi.FormatError('not a VOCALOID3/4 voicebank index (starts with %r)' % root.tag)
    recovered = recover_from_triphones(root)
    if recovered:
        rep.notes.append('missing diphones recovered from triphones: %d' % recovered)
    di, st = fill_gaps(root)
    if di or st:
        rep.notes.append('gaps filled with the closest-sounding unit: %d transitions, %d held sounds' % (di, st))
    _convert(root, rep)
    out = bytearray()
    phdc_size = struct.unpack_from('<I', root.phdc, 4)[0]
    out += struct.pack('<Q4sI', 0, b'DBS ', phdc_size + 0x10)
    out += root.flags
    out += root.phdc                                 # no version, no signature block
    out += struct.pack('<I', len(root.children))
    for c in root.children:
        _serialize(c, out)
    out = bytes(out)
    ddi.parse(out, 0, engine=2)                       # must read back the way VOCALOID2 reads it
    return out, rep


def find_bank(folder):
    """(ddi, ddb, vvd or None) in a voicebank folder."""
    files = {f.lower(): f for f in os.listdir(folder)}
    for low, real in sorted(files.items()):
        if low.endswith('.ddi'):
            base = low[:-4]
            if base + '.ddb' in files:
                vvd = files.get(base + '.vvd')
                return (os.path.join(folder, real), os.path.join(folder, files[base + '.ddb']),
                        os.path.join(folder, vvd) if vvd else None)
    raise FileNotFoundError('no .ddi/.ddb pair in %s' % folder)


def link_or_copy(src, dst, progress=None):
    """Point at the big sample file instead of copying it when Windows allows it: a hard link
    (same drive, writable source) or a file symlink (Developer Mode). Otherwise copy."""
    if os.path.lexists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
        return 'linked (no copy needed)'
    except OSError:
        pass
    try:
        os.symlink(src, dst)
        return 'linked to the original (no copy needed; keep the original voicebank installed)'
    except OSError:
        pass
    total = os.path.getsize(src)
    done = 0
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        while True:
            buf = fi.read(16 << 20)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if progress:
                progress(done, total)
    shutil.copystat(src, dst)
    return 'copied'


def convert_bank(src_folder, out_folder, name, log=print, progress=None):
    """Convert one voicebank folder. Writes <out>/<name>.ddi, .ddb and .vvd."""
    ddi_path, ddb_path, _ = find_bank(src_folder)
    log('reading %s' % ddi_path)
    data = open(ddi_path, 'rb').read()
    out, rep = to_v2(data)
    os.makedirs(out_folder, exist_ok=True)
    with open(os.path.join(out_folder, name + '.ddi'), 'wb') as f:
        f.write(out)
    log('sample data: %s' % link_or_copy(ddb_path, os.path.join(out_folder, name + '.ddb'), progress))
    for line in rep.lines():
        log(line)
    return rep
