"""Find voicebanks to convert and keep the library of converted ones.

Official VOCALOID3/4 libraries are only converted from this PC's own installation (the
VOCALOID3/VOCALOID4 registry lists them); developer-kit singers (.tree) and other unsigned
banks can come from anywhere.
"""
import datetime
import json
import os
import re
import winreg

from . import ddi

LIBRARY = os.path.join(os.path.expanduser('~'), 'Documents', 'VOCALOID2 Singers')
V3_KEYS = [r'SOFTWARE\VOCALOID3\DATABASE\VOICE3']
V4_ROOT = r'SOFTWARE\VOCALOID4'


def _open(path):
    return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)


def _value(key, name, default=None):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _subkeys(key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


class Source:
    def __init__(self, name, folder, vid=None, kind='installed'):
        self.name = name
        self.folder = folder
        self.id = vid
        self.kind = kind          # installed | tree | folder

    def __repr__(self):
        return '<%s %s %s>' % (self.kind, self.name, self.folder)


def _db_keys():
    keys = list(V3_KEYS)
    try:
        with _open(V4_ROOT) as k:
            keys += [V4_ROOT + '\\' + s for s in _subkeys(k) if re.match(r'DATABASE\d*$', s)]
    except OSError:
        pass
    return keys


def installed():
    """Official V3/V4 voicebanks installed on this PC."""
    found = {}
    for base in _db_keys():
        try:
            root = _open(base)
        except OSError:
            continue
        with root:
            for vid in _subkeys(root):
                try:
                    with _open(base + '\\' + vid) as k:
                        path, name, ok = _value(k, 'PATH'), _value(k, 'NAME'), _value(k, 'INSTALLED', 0)
                except OSError:
                    continue
                folder = os.path.join(path, vid) if path else None
                if not (ok and folder and os.path.isdir(folder)):
                    continue
                if not any(f.lower().endswith('.ddi') for f in os.listdir(folder)):
                    continue
                m = re.search(r'\((.*)\)', name or '')
                found[vid] = Source(m.group(1) if m else (name or vid), folder, vid)
    return sorted(found.values(), key=lambda s: s.name.lower())


def installed_ids():
    return {s.id: s for s in installed()}


def classify(path):
    """Turn a picked file/folder into a Source, or raise ValueError with a reason."""
    path = os.path.abspath(path)
    if os.path.isfile(path) and path.lower().endswith('.tree'):
        return Source(os.path.splitext(os.path.basename(path))[0], path, kind='tree')
    if os.path.isfile(path) and path.lower().endswith(('.ddi', '.ddb')):
        path = os.path.dirname(path)
    if os.path.isdir(path):
        trees = [f for f in os.listdir(path) if f.lower().endswith('.tree')]
        if trees and not any(f.lower().endswith('.ddi') for f in os.listdir(path)):
            return Source(os.path.splitext(trees[0])[0], os.path.join(path, trees[0]), kind='tree')
        ddis = [f for f in os.listdir(path) if f.lower().endswith('.ddi')]
        if ddis:
            head = open(os.path.join(path, ddis[0]), 'rb').read(12)[8:12]
            vid = os.path.basename(path)
            if head == b'DBSe':
                if vid not in installed_ids():
                    raise ValueError('This is an official VOCALOID3/4 library that is not installed on '
                                     'this PC. Install (and activate) it first, then pick it from the '
                                     'installed list.')
                return Source(os.path.splitext(ddis[0])[0], path, vid)
            if head == b'ARR2':
                raise ValueError('This voicebank already is a VOCALOID2 voicebank.')
            if head not in (b'DBS ', ddi.DBS0):
                raise ValueError('Not a VOCALOID voicebank index (%r).' % head)
            return Source(os.path.splitext(ddis[0])[0], path, kind='folder')
    raise ValueError('Pick a voicebank folder (with a .ddi and .ddb) or a developer-kit .tree file.')


# ---------------------------------------------------------------- library
def safe_name(name):
    name = re.sub(r'[^A-Za-z0-9_\-]+', '_', name).strip('_')
    return name[:40] or 'Singer'


def library(root=LIBRARY):
    """Converted banks: [(name, folder, info dict)]."""
    out = []
    if not os.path.isdir(root):
        return out
    for d in sorted(os.listdir(root), key=str.lower):
        folder = os.path.join(root, d)
        if not os.path.isdir(folder):
            continue
        files = [f.lower() for f in os.listdir(folder)]
        if not any(f.endswith('.ddi') for f in files):
            continue
        try:
            info = json.load(open(os.path.join(folder, 'info.json'), encoding='utf-8'))
        except (OSError, ValueError):
            info = {}
        out.append((info.get('name', d), folder, info))
    return out


def write_info(folder, name, source, report):
    info = {'name': name, 'source': source.folder, 'kind': source.kind,
            'converted': datetime.datetime.now().isoformat(timespec='seconds'), 'report': report}
    with open(os.path.join(folder, 'info.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=1)
