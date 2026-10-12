"""Reader/writer for VOCALOID voicebank index files (.ddi) and devkit trees (.tree).

A .ddi is one serialized object tree. Every object starts with a header

    u64 pos | char[4] tag | u32 size

followed by its fields. "Containers" (everything derived from the ARR class) then have
u32 named, u32 stubs, their own fields, u32 count and the children. Every object ends
with a length-prefixed name. Layouts below were read out of DSE2.dll (VOCALOID2) and
DSE4.dll (VOCALOID3/4); see FORMAT.md.

The parser keeps every byte of the input, so a tree can be written back unchanged or with
only the parts the VOCALOID2 engine does not understand rewritten.
"""
import struct

SMS2 = b'SMS2'
DBS0 = b'DBS' + bytes(1)   # tag written by some hand-converted V2 banks

# containers: tag -> extra body reader name
CONTAINERS = {
    b'ARR ', b'DBV ', b'TDB ', b'NAT ', b'NRL ', b'NTN ', b'STA ', b'VIB ', b'ART ', b'TMM ', b'TMF ',
    b'ARTu', b'STAu', b'NATu', b'NRLu', b'NTNu', b'VIBu', b'VQMu',
    b'ARTp', b'ARTx', b'STAp', b'NATp', b'NRLp', b'NTNp', b'VIBp', b'VQMp',
    b'VQM ', b'DBS ', b'DBSe', DBS0, b'ARR2', b'ARR3', b'ARR4',
}
LEAVES = {b'EMPT', b'CODB', b'VIs ', b'ENV ', b'HMM ', b'PHDC', b'PHG2', b'PHG ', b'RESO', b'FRM2',
          b'FRM ', b'SMS2', b'SMS ', b'GEN ', b'GTRK', b'RGN '}
TOPS = {b'DBS ', b'DBSe', DBS0, b'ARR2', b'ARR3', b'ARR4'}
PARTS = {b'ARTp', b'ARTx', b'STAp', b'NATp', b'NRLp', b'NTNp', b'VIBp', b'VQMp'}
UNITS = {b'ARTu', b'STAu', b'NATu', b'NRLu', b'NTNu', b'VIBu', b'VQMu'}


class FormatError(Exception):
    pass


class Reader:
    def __init__(self, data, version=2, compact=0, engine=4):
        self.d = data
        self.engine = engine        # 2: read like DSE2 (no version after DBS), 4: like DSE4
        self.p = 0
        self.version = version      # DSE4 stream[0xc]: 2 for V2 files, value after DBS/DBSe for V3+
        self.compact = compact      # DSE2 stream[0xc] / DSE4 stream[0xd]
        self.sms = SMS2             # stream[0xb]

    def take(self, n):
        if n < 0 or self.p + n > len(self.d):
            raise FormatError('read past end at 0x%x (+%d)' % (self.p, n))
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def u64(self):
        return struct.unpack('<Q', self.take(8))[0]

    def string(self):
        n = self.i32()
        return self.take(n) if n > 0 else b''


class Node:
    """One object. raw_* fields are byte slices of the source, kept for writing back."""

    def __init__(self):
        self.tag = None
        self.hdr = b''          # pos + tag + size
        self.flags = b''        # containers: named + stubs (8 bytes)
        self.stubs = False
        self.body = b''         # class fields (may include nested objects)
        self.count = 0
        self.children = []
        self.post = b''         # packed-mode tail of PhU parts
        self.name = b''         # u32 len + chars
        self.stub = False       # EMPT placeholder child
        self.version = None     # top only
        self.sig = None         # top only: 0x104-byte signature block
        self.phdc = None        # top only: raw PHDC object

    @property
    def label(self):
        return self.name[4:].decode('latin1')

    def walk(self, depth=0):
        yield depth, self
        for c in self.children:
            yield from c.walk(depth + 1)


def _header(r):
    s = r.p
    r.take(8)
    tag = r.take(4)
    r.take(4)
    return tag, r.d[s:r.p]


def read_object(r, top=False):
    """Read one full object (header, fields, children, tail, name)."""
    start = r.p
    tag, hdr = _header(r)
    n = Node()
    n.tag, n.hdr = tag, hdr
    if tag in CONTAINERS:
        fs = r.p
        named, stubs = r.u32(), r.u32()
        n.flags = r.d[fs:r.p]
        n.stubs = (named == 0 and stubs != 0)
        bs = r.p
        if top or tag in TOPS:
            _top_body(r, n)
        else:
            _container_body(r, tag)
        n.body = r.d[bs:r.p]
        n.count = r.u32()
        for _ in range(n.count):
            n.children.append(_read_stub(r) if n.stubs else read_object(r))
    elif tag in LEAVES:
        bs = r.p
        _leaf_body(r, tag)
        n.body = r.d[bs:r.p]
    else:
        raise FormatError('unknown object %r at 0x%x' % (tag, start))
    ps = r.p
    if tag in PARTS:
        _part_post(r, tag)
    n.post = r.d[ps:r.p]
    ns = r.p
    if not (top or tag in TOPS):                 # the singer itself has no name record
        r.string()
    n.name = r.d[ns:r.p]
    return n


def _read_stub(r):
    tag, hdr = _header(r)
    n = Node()
    n.tag, n.hdr, n.stub = tag, hdr, True
    ns = r.p
    r.string()
    n.name = r.d[ns:r.p]
    return n


def _top_body(r, n):
    if n.tag in (b'DBS ', b'DBSe') and r.engine >= 3:
        n.version = r.u32()
        r.version = n.version
    # The engines read the phonetic dictionary in "file mode": no pos, no name, and the
    # object's extent is its size field (counted from the tag).
    ps = r.p
    tag = r.take(4)
    size = r.u32()
    if tag != b'PHDC' or size < 8:
        raise FormatError('expected PHDC at 0x%x, found %r' % (ps, tag))
    r.p = ps
    r.take(size)
    n.phdc = r.d[ps:r.p]
    if n.tag == b'ARR2' or (r.engine >= 3 and n.tag in (b'ARR3', b'ARR4', b'DBSe')):
        n.sig = r.take(0x104)


def _pbase(r):
    r.take(8 + 2 + 4)
    r.take(8 if r.compact else 16)


def _container_body(r, tag):
    if tag in (b'ART ', b'TMM '):
        r.take(4)
    elif tag == b'TMF ':
        r.take(8)
        read_object(r)                           # inline FRM2
    elif tag in UNITS:
        r.take(4)
        if tag == b'ARTu':
            r.take(16)
        elif tag == b'STAu':
            r.take(8)
    elif tag in PARTS:
        _pbase(r)
        if tag == b'STAp':
            r.take(4)
        elif tag == b'NTNp':
            r.take(12)
        elif tag == b'VIBp':
            f = r.u32()
            for bit in (1, 2, 4, 8):
                if f & bit:
                    r.take(4)
            read_object(r)                       # embedded vibrato section
    elif tag == b'VQM ':
        pass


def _part_post(r, tag):
    if tag in (b'ARTp', b'ARTx') and r.version > 2:
        n = r.u32()
        r.take(8 * n)
        r.take(4 + 2 + 4 + 8 + 8)
        m = r.u32()
        r.take(16 * m)
        return
    r.take(4)
    n = r.u32()
    r.take(8 * n)
    r.take(4 + 2 + 4 + 8 + 16)
    if tag == b'ARTx':
        r.take(8)


def _flags_struct(r):
    f = r.u32()
    sizes = [(1, 4), (2, 4), (4, 4), (8, 4), (0x10, 4), (0x20, 4), (0x40, 4), (0x80, 4), (0x100, 4),
             (0x200, 4), (0x400, 0xa0), (0x800, 0xa0), (0x1000, 0x50), (0x2000, 0x50), (0x4000, 4),
             (0x8000, 4), (0x10000, 4), (0x20000, 4), (0x40000, 4), (0x80000, 4)]
    for bit, size in sizes:
        if f & bit:
            r.take(size)


def _frame(r, tag):
    r.take(4 + 8)
    if tag == b'FRM ':
        lo, hi = r.u32(), 0
    else:
        lo, hi = r.u32(), r.u32()
    if lo & 7:
        n = r.i32()
        if n >= 1:
            each = 1 if lo & 0x40000000 else 4
            for bit in (2, 1, 4):
                if lo & bit:
                    r.take(n * each)
    if lo & 0x30:
        m = r.i32()
        each = 1 if lo & 0x20000000 else 4
        if not lo & 0x20:
            r.take(m * 4)
        else:
            if lo & 0x10:
                r.take(m * each)
            r.take(m * each)
    if lo & 0x40:
        k = r.i32()
        if k > 0:
            r.take(k * 2)
    for bit in (0x200, 0x100000, 0x400, 0x800, 0x1000):
        if lo & bit:
            r.take(4)
    if lo & 0x2000:
        _flags_struct(r)
        r.take(1)
    if hi & 8:
        r.take(0x34)
    if hi & 0x20:
        read_object(r)
    if lo & 0x8000000:
        r.take(0x174 + 0x4c + 0x4c)
    if lo & 0x4000:
        r.take(0x1c + 7)
    if hi & 1:
        r.take(4 * r.i32())
    if hi & 2:
        k = r.i32()
        r.take(8 * k + 0x60 + 8)
    if hi & 4:
        r.take(4 * r.i32())
    if lo & 0x10000:
        k = r.i32()
        if k > 0:
            r.take(4 * k)
    if lo & 0x40000:
        r.take(1)
    if lo & 0x80000:
        r.take(4)
    if lo & 0x200000:
        k = r.i32()
        if k > 0:
            r.take(0x18 * k)
    if lo & 0x400000:
        r.take(4)
        if r.sms == SMS2:
            r.take(4)
        r.take(4)
        k = r.i32()
        if k > 0:
            r.take(0x18 * k)
    for bit in (0x80, 0x100, 0x20000, 0x800000):
        if lo & bit:
            read_object(r)


def _leaf_body(r, tag):
    if tag in (b'EMPT', b'RESO'):
        return
    if tag in (b'FRM2', b'FRM '):
        _frame(r, tag)
    elif tag == b'VIs ':
        f = r.u32()
        for bit, size in ((1, 4), (2, 1), (4, 8), (8, 8), (0x10, 4), (0x20, 4), (0x40, 4), (0x80, 4),
                          (0x100, 4), (0x200, 4), (0x400, 4), (0x800, 4), (0x1000, 4)):
            if f & bit:
                r.take(size)
        if f & 0x2000:
            read_object(r)
    elif tag == b'ENV ':
        r.take(4)
        n = r.i32()
        r.take(12)
        r.take(max(n, 0))
    elif tag == b'HMM ':
        n, m = r.i32(), r.i32()
        counts = struct.unpack('<%di' % m, r.take(4 * m)) if m > 0 else ()
        if n > 0:
            r.take(n * n * 8)
            for c in counts:
                if c > 0:
                    r.take(n * c * 8)
            r.take(n * 8)
    elif tag == b'CODB':
        for _ in range(5):
            n = r.i32()
            if n >= 1:
                r.take(n * 8)
            m = r.i32()
            if m > 0:
                r.take(m * max(n, 0) * 8)
    elif tag == b'PHDC':
        flags, count = r.u32(), r.i32()
        envs = []
        for _ in range(count):
            r.take(18 + 4)
            a, b = r.u32(), r.u32()
            r.take(1)
            envs.append((a, b))
        if flags & 2:
            for a, b in envs:
                read_object(r)                   # HMM
                if a:
                    read_object(r)
                if b:
                    read_object(r)
        if flags & 1:
            read_object(r)                       # CODB
        if flags & 4:
            read_object(r)                       # PHG2
        if not r.compact:
            for _ in range(r.i32()):
                r.take(0x20)
                r.take(16 * r.i32())
    elif tag in (b'PHG2', b'PHG '):
        for _ in range(r.i32()):
            r.string()
            for _ in range(r.i32()):
                r.take(4)
                r.string()
            if tag == b'PHG2':
                f = r.u32()
                if f & 1:
                    read_object(r)
    else:
        raise FormatError('no reader for %r at 0x%x' % (tag, r.p))


def parse(data, compact=0, engine=4):
    """Parse a whole .ddi/.tree the way DSE<engine> would. Returns (root, reader)."""
    r = Reader(data, version=2, compact=compact, engine=engine)
    root = read_object(r, top=True)
    if r.p != len(data):
        raise FormatError('%d trailing bytes after the tree (stopped at 0x%x)' % (len(data) - r.p, r.p))
    return root, r


def parse_auto(data, engine=4):
    """Try the release layout first, then the compact one."""
    err = None
    for compact in (0, 1):
        try:
            return parse(data, compact, engine)
        except FormatError as e:
            err = err or e
    raise err
