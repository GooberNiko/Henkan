"""Pack a VOCALOID3 developer-kit singer (<Name>.tree + <Name>\\ folder) into .ddi/.ddb.

The .tree file already is a complete V3 index. Its sample references (frame offsets, wave
positions) point into the per-unit files of the folder next to it:

    <Name>\\voice\\articulation\\<ART>\\<ARTu>           one file per diphone
    <Name>\\voice\\stationary\\normal\\<STAu>\\<STAp>     one file per held vowel

A container with the "named" flag is a folder (plus <name>.dat holding its header), anything
else is a file. File names keep a-z and write every other character as %<decimal code>%.

A unit file holds the whole recording (SND) and, region by region, the analysis frames (FRM2,
256 samples each, times relative to their region) covering it back to back. A unit uses the
regions from the one holding its first frame on, so its audio starts at (frames of the regions
before) x 256 samples. The devkit's own wave reference is only right for stationaries; the
devkit editor recomputes it for articulations at load time.
Release voicebanks store per unit only the frames it uses, followed by the recording trimmed to
those frames plus 1024 samples on each side; the wave reference points at the first frame's
sample. Packing rebuilds exactly that.
"""
import os
import struct

from . import ddi

PARTS = {b'ARTp', b'ARTx', b'STAp', b'NATp', b'NRLp', b'NTNp', b'VIBp'}
PAD = 1024          # samples kept before the first and after the last frame
HOP = 256           # samples per analysis frame


def escape(name):
    return ''.join(c if 'a' <= c <= 'z' else '%%%d%%' % ord(c) for c in name)


def _named(node):
    return bool(node.flags) and struct.unpack_from('<I', node.flags, 0)[0] != 0


def _layout(node, version):
    """(offsets position, frame count, tail position, has two wave fields) of a part's tail."""
    p = node.post
    if node.tag in (b'ARTp', b'ARTx') and version > 2:
        n = struct.unpack_from('<I', p, 0)[0]
        return 4, n, 4 + 8 * n, True
    n = struct.unpack_from('<I', p, 4)[0]
    return 8, n, 8 + 8 * n, False


def _frame_lists(unit, after):
    """(offset of first frame, frame count) of every region's frame list after byte `after`, in
    file order. A list is u32 count followed by that many FRM2 objects."""
    out = []
    i = unit.find(b'FRM2', after + 4)
    while i >= 0:
        count = struct.unpack_from('<I', unit, i - 4)[0]
        j, k = i, 0
        while k < count and unit[j:j + 4] == b'FRM2':
            size = struct.unpack_from('<I', unit, j + 4)[0]
            if size < 8:
                break
            j += size
            k += 1
        if count and k == count:
            out.append((i, count))
            i = unit.find(b'FRM2', j)
        else:
            i = unit.find(b'FRM2', i + 1)          # four bytes that only look like a tag
    return out


def _pack_part(node, unit, out, version):
    o, n, tail, two = _layout(node, version)
    offs = struct.unpack_from('<%dQ' % n, node.post, o)
    snd = [c for c in node.children if c.stub and c.label == 'SND']
    if not snd or n < 1:
        raise ddi.FormatError('unit %r has no recording or no frames' % node.label)
    sp = struct.unpack_from('<Q', snd[0].hdr, 0)[0]
    if unit[sp:sp + 4] != b'SND ':
        raise ddi.FormatError('unit %r: recording not found at 0x%x' % (node.label, sp))
    rate, chans, total = struct.unpack_from('<IHI', unit, sp + 8)
    samples = unit[sp + 18:sp + 18 + 2 * total]

    # The recording covers every region of the unit back to back, HOP samples per frame; the
    # unit uses the regions from the one holding its first frame on. Frame times are already
    # relative to their region, as in release voicebanks, so frames are copied unchanged.
    first = None
    pos = 0
    for start_off, count_ in _frame_lists(unit, sp + 18 + 2 * total):
        if start_off == offs[0]:
            first = pos
            break
        pos += count_ * HOP
    if first is None or pos > total:
        raise ddi.FormatError('unit %r: cannot place its frames in the recording' % node.label)
    new_offs = []
    for off in offs:
        if unit[off:off + 4] != b'FRM2':
            raise ddi.FormatError('unit %r: frame not found at 0x%x' % (node.label, off))
        size = struct.unpack_from('<I', unit, off + 4)[0]
        new_offs.append(out.tell())
        out.write(unit[off:off + size])

    count = n * HOP + 2 * PAD
    start = first - PAD
    cut = bytearray(2 * count)
    a, b = max(start, 0), min(start + count, total)
    if b > a:
        cut[2 * (a - start):2 * (b - start)] = samples[2 * a:2 * b]
    snd_pos = out.tell()
    out.write(struct.pack('<4sIIHI', b'SND ', 18 + len(cut), rate, chans, count))
    out.write(cut)
    data = snd_pos + 18

    p = bytearray(node.post)
    struct.pack_into('<%dQ' % n, p, o, *new_offs)
    struct.pack_into('<I', p, tail + 6, count)
    if two:
        struct.pack_into('<QQ', p, tail + 10, data, data + 2 * PAD)
    else:
        struct.pack_into('<Q', p, tail + 10, data + 2 * PAD)
    node.post = bytes(p)
    for c in node.children:
        if c.stub:
            pos = snd_pos if c.label == 'SND' else new_offs[0]
            c.hdr = struct.pack('<Q', pos) + c.hdr[8:]


def _pack_subtree(node, unit, out, version, counts):
    if node.tag in PARTS:
        _pack_part(node, unit, out, version)
        counts[0] += 1
    for c in node.children:
        if not c.stub:
            _pack_subtree(c, unit, out, version, counts)


def unit_files(root, folder):
    """Yield (node, path) for every file-backed object that holds sample references."""
    def holds_parts(n):
        return any(x.tag in PARTS for _, x in n.walk())

    def rec(node, path):
        for c in node.children:
            if c.stub or not holds_parts(c):
                continue
            p = os.path.join(path, escape(c.label))
            if _named(c):
                yield from rec(c, p)
            else:
                yield c, p

    yield from rec(root, folder)


def pack(tree_path, ddb_out, log=print, progress=None):
    """Read <Name>.tree, write the sample file ddb_out, return the parsed root with every
    reference pointing into ddb_out."""
    data = open(tree_path, 'rb').read()
    root, _ = ddi.parse_auto(data, engine=4)
    folder = os.path.splitext(tree_path)[0]
    if not os.path.isdir(folder):
        raise FileNotFoundError('the singer folder %s is missing next to the .tree' % folder)
    files = list(unit_files(root, folder))
    missing = [p for _, p in files if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError('%d unit files are missing, e.g. %s' % (len(missing), missing[0]))
    log('packing %d unit files' % len(files))
    counts = [0]
    with open(ddb_out, 'wb') as out:
        for i, (node, path) in enumerate(files):
            with open(path, 'rb') as f:
                unit = f.read()
            _pack_subtree(node, unit, out, root.version or 2, counts)
            if progress:
                progress(i + 1, len(files))
    log('packed %d units, %.0f MB of samples' % (counts[0], os.path.getsize(ddb_out) / 1e6))
    return root
