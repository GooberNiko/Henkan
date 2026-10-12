"""Install converted singers into VOCALOID2 as singers of their own.

How VOCALOID2 (dbm2.dll) finds voices:

* HKLM\\SOFTWARE\\VOCALOID2\\DATABASE\\VOICE\\<voice id>: InstallDir, Name, Installed, and a
  "vvoice" subkey listing the singer files (vvoice<N>) of that voice;
* <InstallDir>\\<voice id>\\ holds <name>.ddi, .ddb and a .vvd with FORMAT "2.0.0.0";
* voicedbdir\\vvoice<N>.vvd is the singer as the editor lists it, voice.map puts it at
  (bank, program). The bank is the lyrics language: 0 Japanese, 1 English.

A voice id is 16 characters that dbm2 decodes with a substitution cipher (FUN_100211e0) into
"00 <vendor> <language> <product>..."; the language digit is the bank. Each converted singer
gets an id of its own with vendor R (no real product uses it) and the language you pick.

Only the registry part needs administrator rights; it is done with one Windows prompt via
`reg import`. Everything else is in voicedbdir (writable by users) and the singers folder.
"""
import hashlib
import json
import os
import stat
import struct
import subprocess
import tempfile
import winreg

from . import ddi

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'V3V2', 'installed.json')
VOICE_KEY = r'SOFTWARE\VOCALOID2\DATABASE\VOICE'
REG_PATH = r'HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\VOCALOID2\DATABASE\VOICE'
LANGUAGES = {0: 'Japanese', 1: 'English'}
AL = '23456789ABCDEFGHKLMNPRSTWXYZ'
NOFLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
CRLF = chr(13) + chr(10)


class InstallError(Exception):
    pass


# ---------------------------------------------------------------- voice ids
_T = json.load(open(os.path.join(HERE, 'idtables.json')))


def _sel(c):
    c = {'K': '0', 'L': '1'}.get(c, c)
    return ord(c) - (0x30 if c < ':' else 0x37)


def decode_id(vid):
    s = list(vid[:14])
    a, b = _T['A'][_sel(vid[15])], _T['B'][_sel(vid[14])]
    for i in range(1, 14):
        s[i] = AL[(AL.index(s[i]) - a[i - 1]) % 28]
    for i in range(1, 14):
        s[i] = AL[(AL.index(s[i]) - b[i - 1]) % 28]
    for i in range(14):
        t = _T['P'][i]
        if s[i] in t:
            k = t.index(s[i])
            s[i] = chr(k + 0x30) if k < 10 else chr(k + 0x37)
    return ''.join(s)


def encode_id(dec, c6='C', c5='B'):
    s = list(dec)
    for i in range(14):
        s[i] = _T['P'][i][int(s[i], 36)]
    a, b = _T['A'][_sel(c5)], _T['B'][_sel(c6)]
    for i in range(1, 14):
        s[i] = AL[(AL.index(s[i]) + b[i - 1]) % 28]
    for i in range(1, 14):
        s[i] = AL[(AL.index(s[i]) + a[i - 1]) % 28]
    return ''.join(s) + c6 + c5


def new_id(name, language, taken):
    digits = '0123456789ABCDEFGHIJKLMNOPQR'          # base 28, as the decoder reads them
    h = int(hashlib.sha1(name.encode('utf-8')).hexdigest(), 16)
    for k in range(28 * 28):
        n = (h + k) % (28 * 28)
        vid = encode_id('00R%d%s%s00000000' % (language, digits[n // 28], digits[n % 28]))
        if vid not in taken:
            return vid
    raise InstallError('no free voice id left')


# ---------------------------------------------------------------- language
def detect_language(ddi_path):
    """1 (English) if the phoneme set has English-only symbols, else 0 (Japanese)."""
    root, _ = ddi.parse_auto(open(ddi_path, 'rb').read(), engine=2)
    p = root.phdc
    count = struct.unpack_from('<I', p, 12)[0]
    names = {p[16 + 31 * i:16 + 31 * i + 18].split(b'\0')[0] for i in range(count)}
    return 1 if names & {b'@', b'{', b'Q', b'V', b'I', b'U', b'O@', b'aI', b'th'} else 0


# ---------------------------------------------------------------- vvd / voice.map
def _xor(data):
    return bytes(b if b in (10, 13) else b ^ 0x1a for b in data)


def make_vvd(vid, name):
    keys = [('ID', 'VOCALOID:VIRTUAL:VOICE'), ('FORMAT', '2.0.0.0'), ('VOICEIDSTR', vid), ('VOICENAME', name),
            ('Breathiness', '0'), ('Brightness', '0'), ('Clearness', '0'), ('Opening', '0'),
            ('Gender:Factor', '0')]
    return _xor(''.join('"%s":=:"%s"\r\n' % kv for kv in keys).encode('latin1'))


def _write(path, data):
    """Write a file VOCALOID marked read-only and keep it read-only."""
    if os.path.exists(path):
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWRITE)
        with open(path, 'r+b') as f:                  # create-always is refused in Program Files
            f.write(data)
            f.truncate()
        os.chmod(path, mode)
    else:
        with open(path, 'wb') as f:
            f.write(data)


def _map_with(voicedir, bank, program, number):
    """(path, new bytes) of voice.map with (bank, program) set to singer file number."""
    path = os.path.join(voicedir, 'voice.map')
    data = bytearray(open(path, 'rb').read())
    if not data.startswith(b'VOCALOIDVOICEMAP') or len(data) != 32 + 128 * 128 * 8:
        raise InstallError('voice.map has an unexpected layout')
    struct.pack_into('<II', data, 32 + (bank * 128 + program) * 8, number, 0)
    return path, bytes(data)


def _map_free(voicedir, bank):
    data = open(os.path.join(voicedir, 'voice.map'), 'rb').read()
    for prog in range(128):
        if struct.unpack_from('<I', data, 32 + (bank * 128 + prog) * 8)[0] == 0:
            return prog
    raise InstallError('no free singer place in voice.map for this language')


# ---------------------------------------------------------------- registry
def _open(path):
    return winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY)


def voice_dir():
    try:
        with _open(VOICE_KEY) as k:
            return winreg.QueryValueEx(k, 'VOICEDIR')[0]
    except OSError:
        raise InstallError('VOCALOID2 is not installed (no voice folder in the registry).')


def registered_ids():
    ids = set()
    try:
        with _open(VOICE_KEY) as k:
            i = 0
            while True:
                try:
                    ids.add(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return ids


def _orphans():
    """Voices this tool registered that it no longer tracks (e.g. an install that failed half way)."""
    known = set(_load())
    out = []
    for vid in registered_ids():
        try:
            with _open(os.path.join(VOICE_KEY, vid)) as k:
                name = winreg.QueryValueEx(k, 'NAME')[0]
        except OSError:
            continue
        if name.startswith('V3V2 Voice DB') and vid not in known:
            out.append(vid)
    return out


def _elevated(reg_text, voice_map=None, admin=True):
    """Apply a .reg file and optionally replace voice.map, behind one administrator prompt."""
    tmp = tempfile.mkdtemp(prefix='v3v2_')
    reg = os.path.join(tmp, 'singer.reg')
    with open(reg, 'w', encoding='utf-16') as f:
        f.write('Windows Registry Editor Version 5.00' + CRLF * 2 + reg_text)
    log = os.path.join(tmp, 'log.txt')
    ok = os.path.join(tmp, 'ok')
    lines = ["$ErrorActionPreference = 'Stop'",
             "try {",
             "  $r = Start-Process -FilePath reg.exe -ArgumentList 'import','\"%s\"' -Wait -PassThru "
             "-WindowStyle Hidden" % reg,
             "  if ($r.ExitCode -ne 0) { throw ('reg import failed with code ' + $r.ExitCode) }"]
    if voice_map:
        path, data = voice_map
        new = os.path.join(tmp, 'voice.map')
        with open(new, 'wb') as f:
            f.write(data)
        lines += ["  [IO.File]::SetAttributes('%s', 'Normal')" % path,
                  "  [IO.File]::Copy('%s', '%s', $true)" % (new, path),
                  "  [IO.File]::SetAttributes('%s', 'ReadOnly')" % path]
    lines += ["  Set-Content -LiteralPath '%s' -Value ok" % ok,
              "} catch { $_ | Out-String | Set-Content -LiteralPath '%s' }" % log]
    script = os.path.join(tmp, 'apply.ps1')
    with open(script, 'w', encoding='utf-8-sig') as f:
        f.write(CRLF.join(lines + ['']))
    try:
        ps = ("Start-Process -FilePath powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',"
              "'-File','\"%s\"' %s-Wait -WindowStyle Hidden" % (script, '-Verb RunAs ' if admin else ''))
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
                       creationflags=NOFLAGS)
        if not os.path.exists(ok):
            why = open(log, encoding='utf-8', errors='replace').read().strip() if os.path.exists(log) else \
                'the administrator prompt was cancelled'
            raise InstallError('Windows did not allow the change: %s' % why)
    finally:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)


def _esc(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')


# ---------------------------------------------------------------- state
def _load():
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=1)
    os.replace(STATE + '.tmp', STATE)


def installed():
    """{singer folder: entry} for singers installed by this tool and still registered."""
    ids = registered_ids()
    return {e['folder']: e for vid, e in _load().items() if vid in ids}


def vocaloid2_running():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq VOCALOID2.exe', '/NH'], capture_output=True, text=True,
                         creationflags=NOFLAGS).stdout
    return 'vocaloid2.exe' in out.lower()


# ---------------------------------------------------------------- install / uninstall
def install(folder, name, language):
    if vocaloid2_running():
        raise InstallError('Close VOCALOID2 first.')
    folder = os.path.abspath(folder)
    if folder in installed():
        raise InstallError('%s is already installed.' % name)
    files = os.listdir(folder)
    ddis = [f for f in files if f.lower().endswith('.ddi')]
    if len(ddis) != 1 or not any(f.lower() == ddis[0][:-4].lower() + '.ddb' for f in files):
        raise InstallError('%s must hold exactly one .ddi with its .ddb' % folder)
    base = ddis[0][:-4]
    vdir = voice_dir()
    orphans = _orphans()
    vid = new_id(name, language, registered_ids() - set(orphans))
    link = os.path.join(vdir, vid)
    numbers = [int(f[6:-4]) for f in os.listdir(vdir)
               if f.lower().startswith('vvoice') and f.lower().endswith('.vvd') and f[6:-4].isdigit()]
    number = max(numbers + [0]) + 1
    program = _map_free(vdir, language)
    vvd = make_vvd(vid, name)

    done = []
    try:
        for f in files:                                   # the voice folder's own .vvd
            if f.lower().endswith('.vvd'):
                os.remove(os.path.join(folder, f))
        with open(os.path.join(folder, base + '.vvd'), 'wb') as f:
            f.write(vvd)
        r = subprocess.run(['cmd', '/c', 'mklink', '/J', link, folder], capture_output=True, text=True,
                           creationflags=NOFLAGS)
        if r.returncode != 0:
            raise InstallError('could not link the singer into %s: %s' % (vdir, (r.stderr or r.stdout).strip()))
        done.append(lambda: os.rmdir(link))
        vpath = os.path.join(vdir, 'vvoice%d.vvd' % number)
        _write(vpath, vvd)
        done.append(lambda: os.remove(vpath))
        _elevated(''.join('[-%s\\%s]\r\n\r\n' % (REG_PATH, o) for o in orphans) +
                  '[%s\\%s]\r\n"INSTALLDIR"="%s"\r\n"NAME"="%s"\r\n"INSTALLED"=dword:00000001\r\n\r\n'
                  '[%s\\%s\\vvoice]\r\n"vvoice%d"=""\r\n'
                  % (REG_PATH, vid, _esc(vdir), _esc('V3V2 Voice DB (%s)' % name), REG_PATH, vid, number),
                  _map_with(vdir, language, program, number))
    except Exception:
        for undo in reversed(done):
            try:
                undo()
            except OSError:
                pass
        raise
    state = _load()
    state[vid] = {'name': name, 'folder': folder, 'language': language, 'vvoice': number, 'program': program}
    _save(state)
    return vid


def uninstall(folder):
    if vocaloid2_running():
        raise InstallError('Close VOCALOID2 first.')
    folder = os.path.abspath(folder)
    state = _load()
    hits = [vid for vid, e in state.items() if e['folder'] == folder]
    if not hits:
        raise InstallError('This singer is not installed.')
    vdir = voice_dir()
    for vid in hits:
        e = state[vid]
        data = open(os.path.join(vdir, 'voice.map'), 'rb').read()
        vmap = None
        if struct.unpack_from('<I', data, 32 + (e['language'] * 128 + e['program']) * 8)[0] == e['vvoice']:
            vmap = _map_with(vdir, e['language'], e['program'], 0)
        if vid in registered_ids() or vmap:
            _elevated('[-%s\\%s]\r\n' % (REG_PATH, vid), vmap)
        link = os.path.join(vdir, vid)
        if os.path.isjunction(link):
            os.rmdir(link)
        vpath = os.path.join(vdir, 'vvoice%d.vvd' % e['vvoice'])
        if os.path.exists(vpath):
            os.chmod(vpath, stat.S_IWRITE | stat.S_IREAD)
            os.remove(vpath)
        del state[vid]
    _save(state)
