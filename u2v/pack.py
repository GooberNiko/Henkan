"""Pack a built singer DB into a voicebank package (.ddb + .ddi + .vvd) and install it
into a VOCALOID editor of your choice.

Pipeline (all verified against real installs on a VOCALOID machine):

1. **Pack**: the DBTool writes ``<Name>.tree`` (which IS the DDI: PHDC/STA/ART/VQM blocks)
   plus per-unit sample files under ``<Name>/voice/articulation/...`` and
   ``<Name>/voice/stationary/normal/...``. Packing copies every unit's FRM2/SND chunks
   into one ``<Name>.ddb`` and rebases the offsets inside ``<Name>.ddi``.
   (Same algorithm as ddb-tools' pack_ddb.py, ported here so there is no dependency.)
2. **VVD**: ``<Name>.vvd`` is substitution-cipher encoded text metadata (voicebank name +
   CompID). Same cipher and template as VVD Editor Plus.
3. **Install**: the package folder ``<CompID>/`` (with same-basename .ddb/.ddi/.vvd) is
   copied to the editor's bank folder and registered, e.g. for VOCALOID4::

       HKLM\\SOFTWARE\\VOCALOID4\\DATABASE\\<CompID>  (+ the same under POCALOID4)
           PATH, DRP=0B0032, INSTALLED=1, NAME="VOCALOID4 Library (<name>)", TIME, KEYS\\default

   Registry writes + Program Files copies need admin rights, so install/uninstall run
   through generated .bat scripts (same shape as VVD Editor Plus') launched elevated.
   The VOCALOID4FE developer editor instead takes a ``[VVoice N]`` slot in DB_Dev.ini
   pointing at the loose built DB (no packing needed there).

    python -I henkan.py pack "<DB folder>/<Name>" --out <dir> --name <voice> [--compid X]
    python -I henkan.py install <package dir> --editor "<editor folder>"
"""
import io
import os
import random
import re
import shutil
import struct
import sys

# ------------------------------------------------------------------ filename escaping

# sample-file names escape every non-lowercase-ASCII char as %<code>% (dbtool_build._decode
# reverses it): 'R' -> %82%, '#' -> %35%, "'" -> %39%
def escape_filename(name):
    return ''.join(c if 'a' <= c <= 'z' else '%%%d%%' % ord(c) for c in name)


def _decode(name):
    return re.sub(r'%(\\d+)%', lambda m: chr(int(m.group(1))), name)


# ------------------------------------------------------------------ DDI model (.tree reader)

# Port of ddb-tools' utils/ddi_utils.py read path (no yaml/temp features, so no new
# dependency). Parses the PHDC/TDB/DBV/STA/ART/VQM blocks of a DBTool .tree and records,
# for every unit, the file position of each sample offset (as "pos=offset" hex strings)
# so the packer can rebase them into the new .ddb.


def _read_str(data):
    size = int.from_bytes(data.read(4), byteorder='little')
    return data.read(size).decode('latin-1')  # the DBTool writes latin-1, not utf-8


def _read_arr(data):
    assert data.read(4).decode() == 'ARR '
    int.from_bytes(data.read(4), byteorder='little')
    assert int.from_bytes(data.read(8), byteorder='little') == 1
    return data.read(4)


def _reverse_search(data, search, offset, limit=-1):
    if limit == -1:
        limit = offset - len(search)
    offset -= len(search)
    for i in range(offset, 0, -1):
        if data[i:i + len(search)] == search:
            return i
        if offset - i > limit:
            break
    return -1


class DDIModel:
    def __init__(self, ddi_bytes):
        self.ddi_bytes = ddi_bytes
        self.ddi_data = None
        self.sta_data = {}
        self.art_data = {}
        self.vqm_data = {}
        self.ddi_data_dict = {}

    def read(self):
        self.ddi_data = io.BytesIO(self.ddi_bytes)
        phdc_offset = self.ddi_bytes.find(b'PHDC')
        if phdc_offset >= 0:
            self.ddi_data.seek(phdc_offset)
            self.read_phdc()
        tdb_offset = self.ddi_bytes.find(b'\xff' * 8 + b'TDB ')
        if tdb_offset >= 0:
            try:
                self.ddi_data.seek(tdb_offset)
                self.read_tdb()
            except AssertionError:
                pass  # TDB timbre map is not needed for packing; some DBs vary here
        dbv_offset = self.ddi_bytes.find(b'\x00' * 8 + b'DBV ')
        if dbv_offset < 0:
            raise ValueError('no DBV block: not a singer database .tree?')
        self.ddi_data.seek(dbv_offset)
        self.read_dbv()
        sta_offset = self.ddi_bytes.find(b'\x00' * 8 + b'STA ')
        if sta_offset < 0:
            raise ValueError('no STA block: the DB has no stationaries?')
        sta_offset = _reverse_search(self.ddi_bytes, b'ARR ', sta_offset) - 8
        self.ddi_data.seek(sta_offset)
        self.sta_data = self.read_sta()
        art_offset = self.ddi_bytes.find(b'\x00' * 8 + b'ART ')
        if art_offset < 0:
            raise ValueError('no ART block: the DB has no articulations?')
        art_offset = _reverse_search(self.ddi_bytes, b'ARR ', art_offset) - 8
        self.ddi_data.seek(art_offset)
        self.art_data = self.read_art()
        vqm_offset = self.ddi_bytes.find(b'\xff' * 8 + b'VQM ')
        self.vqm_data = None
        if vqm_offset != -1:
            self.ddi_data.seek(vqm_offset)
            self.vqm_data = self.read_vqm()
        self.ddi_data_dict = {'sta': {}, 'art': {}}
        if self.vqm_data is not None:
            self.ddi_data_dict = {'vqm': {}, 'sta': {}, 'art': {}}
            vqm_dict = []
            for _idx, vqmp in self.vqm_data.items():
                vqm_dict.append({'snd': vqmp['snd'], 'epr': vqmp['epr'], 'pitch': vqmp['pitch1']})
            self.ddi_data_dict['vqm'] = vqm_dict
        sta_dict = {}
        for stau in self.sta_data.values():
            stau_dict = []
            for _idx, stap in stau['stap'].items():
                stau_dict.append({'snd': stap['snd'], 'epr': stap['epr'], 'pitch': stap['pitch1']})
            sta_dict[stau['phoneme']] = stau_dict
        self.ddi_data_dict['sta'] = {key: sta_dict[key] for key in sorted(sta_dict)}
        art_dict = {}
        for art in self.art_data.values():
            if 'artu' in art:
                for artu in art['artu'].values():
                    key = art['phoneme'] + ' ' + artu['phoneme']
                    art_dict[key] = []
                    for artp in artu['artp'].values():
                        art_dict[key].append({'snd': artp['snd'], 'snd_start': artp['snd_start'],
                                              'epr': artp['epr'], 'pitch': artp['pitch1']})
            if 'art' in art:
                for sub_art in art['art'].values():
                    if 'artu' in sub_art:
                        for artu in sub_art['artu'].values():
                            key = art['phoneme'] + ' ' + sub_art['phoneme'] + ' ' + artu['phoneme']
                            art_dict[key] = []
                            for artp in artu['artp'].values():
                                art_dict[key].append({'snd': artp['snd'],
                                                      'snd_start': artp['snd_start'],
                                                      'epr': artp['epr'], 'pitch': artp['pitch1']})
        self.ddi_data_dict['art'] = {key: art_dict[key] for key in sorted(art_dict)}

    def read_phdc(self):
        assert self.ddi_data.read(4).decode() == 'PHDC'
        phdc_size = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 4
        phoneme_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(phoneme_num):
            bytes_str = self.ddi_data.read(0x1F)
            assert bytes_str[-1] in [0, 1]
        assert self.ddi_data.read(4).decode() == 'PHG2'
        phg2_size = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        phg2_epr_guide_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(phg2_epr_guide_num):
            _read_str(self.ddi_data)
            temp_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(temp_num):
                self.ddi_data.read(4)
                _read_str(self.ddi_data)
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        epr_guide_size = phdc_size - phg2_size - 0x10 - 0x1F * phoneme_num - 4
        epr_guide_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        epr_guide_bytes = self.ddi_data.read(epr_guide_size)
        offset = 0
        for _i in range(epr_guide_num):
            offset += 0x24
            while offset < len(epr_guide_bytes) and epr_guide_bytes[offset] == 0:
                offset += 8
        assert offset == len(epr_guide_bytes)

    def read_tdb(self):
        assert self.ddi_data.read(8) == b'\xff' * 8
        assert self.ddi_data.read(4).decode() == 'TDB '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        tmm_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(tmm_num):
            assert self.ddi_data.read(8) == b'\xff' * 8
            assert self.ddi_data.read(4).decode() == 'TMM '
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
            self.ddi_data.read(4)
            str_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert str_num == 3
            for _j in range(str_num):
                assert self.ddi_data.read(8) == b'\xff' * 8
                assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') == 0
                _read_str(self.ddi_data)
                _read_str(self.ddi_data)
            assert _read_str(self.ddi_data) == 'timbre'

    def read_dbv(self):
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert self.ddi_data.read(4).decode() == 'DBV '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        self.ddi_data.read(4)

    def read_sta(self):
        sta_data = {}
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
        assert self.ddi_data.read(4).decode() == 'STA '
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 1
        stau_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(stau_num):
            stau_data = {'phoneme': '', 'stap': {}}
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
            assert self.ddi_data.read(4).decode() == 'STAu'
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            stau_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert self.ddi_data.read(8) == b'\xff' * 8
            stap_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(stap_num):
                stap_data = {'snd': '', 'snd_length': '', 'epr': []}
                assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'STAp'
                self.ddi_data.read(4)
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
                stap_data['duration'] = struct.unpack('<d', self.ddi_data.read(8))[0]
                assert int.from_bytes(self.ddi_data.read(2), byteorder='little') == 1
                stap_data['pitch1'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['pitch2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['unknown2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['dynamics'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                stap_data['tempo'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 2
                assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0x3D
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'SND'
                stap_data['snd_length'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'EpR'
                self.ddi_data.read(4)
                epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                epr_list = []
                for _k in range(epr_num):
                    epr_offset_pos = self.ddi_data.tell()
                    epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                    epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                stap_data['epr'] = epr_list
                stap_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                assert self.ddi_data.read(2) == b'\x01\x00'
                snd_identifier = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                snd_offset_pos = self.ddi_data.tell()
                snd_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                stap_data['snd'] = f'{snd_offset_pos:0>8x}={snd_offset:016x}_{snd_identifier:08x}'
                self.ddi_data.read(0x10)
                stap_idx = _read_str(self.ddi_data)
                assert stap_idx not in stau_data['stap']
                stau_data['stap'][stap_idx] = stap_data
                stau_data['stap'] = {k: stau_data['stap'][k] for k in sorted(stau_data['stap'])}
            stau_data['phoneme'] = _read_str(self.ddi_data)
            sta_data[stau_idx] = stau_data
        sta_data = {k: sta_data[k] for k in sorted(sta_data)}
        assert _read_str(self.ddi_data) == 'normal'
        assert _read_str(self.ddi_data) == 'stationary'
        return sta_data

    def read_art(self):
        total_art_data = {}
        int.from_bytes(self.ddi_data.read(8), byteorder='little')
        assert int.from_bytes(_read_arr(self.ddi_data), byteorder='little') != 0
        while True:
            start = self.ddi_data.read(8)
            if start not in [b'\x00' * 8, b'\xff' * 8]:
                offset = self.ddi_data.tell() - 8
                self.ddi_data.seek(offset)
                assert _read_str(self.ddi_data) == 'articulation'
                break
            assert self.ddi_data.read(4).decode() == 'ART '
            art_idx, art_data = self.read_art_block()
            total_art_data[art_idx] = art_data
        return {key: total_art_data[key] for key in sorted(total_art_data)}

    def read_art_block(self):
        art_data = {'phoneme': '', 'artu': {}, 'art': {}}
        self.ddi_data.read(4)
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        art_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        artu_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        for _i in range(artu_num):
            artu_data = {'phoneme': '', 'artp': {}}
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') == 0
            block_type = self.ddi_data.read(4).decode()
            if block_type == 'ART ':
                sub_art_idx, sub_art_data = self.read_art_block()
                art_data['art'][sub_art_idx] = sub_art_data
                continue
            assert block_type == 'ARTu'
            self.ddi_data.read(4)
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            artu_idx = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert int.from_bytes(self.ddi_data.read(8), byteorder='little') in [0, 1]
            self.ddi_data.read(4)
            assert self.ddi_data.read(4) == b'\xff' * 4
            artp_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            for _j in range(artp_num):
                artp_data = {'snd': '', 'snd_unknown': '', 'epr': []}
                dev_artp_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['dev_artp'] = f'{dev_artp_offset:0>8x}'
                assert self.ddi_data.read(4).decode() == 'ARTp'
                self.ddi_data.read(4)
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
                artp_data['duration'] = struct.unpack('<d', self.ddi_data.read(8))[0]
                assert int.from_bytes(self.ddi_data.read(2), byteorder='little') == 1
                artp_data['pitch1'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['pitch2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['unknown2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['dynamics'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                artp_data['tempo'] = struct.unpack('<f', self.ddi_data.read(4))[0]
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 2
                artp_idx = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'SND'
                snd_len_phoneme = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                artp_data['snd_len_phoneme'] = f'{snd_len_phoneme:08x}'
                assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
                assert self.ddi_data.read(4).decode() == 'EMPT'
                self.ddi_data.read(4)
                assert _read_str(self.ddi_data) == 'EpR'
                loc = self.ddi_data.tell()
                try:
                    epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    epr_list = []
                    for _k in range(epr_num):
                        epr_offset_pos = self.ddi_data.tell()
                        epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                        epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                    artp_data['epr'] = epr_list
                    artp_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    assert self.ddi_data.read(2) == b'\x01\x00'
                except AssertionError:
                    self.ddi_data.seek(loc)
                    self.ddi_data.read(4)
                    epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    epr_list = []
                    for _k in range(epr_num):
                        epr_offset_pos = self.ddi_data.tell()
                        epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                        epr_list.append(f'{epr_offset_pos:0>8x}={epr_offset:0>8x}')
                    artp_data['epr'] = epr_list
                    artp_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                    assert self.ddi_data.read(2) == b'\x01\x00'
                snd_identifier = int.from_bytes(self.ddi_data.read(4), byteorder='little')
                snd_offset_pos = self.ddi_data.tell()
                snd_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['snd'] = f'{snd_offset_pos:08x}={snd_offset - 0x12:016x}_{snd_identifier:08x}'
                snd_offset2_pos = self.ddi_data.tell()
                snd_offset2 = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                artp_data['snd_start'] = \
                    f'{snd_offset2_pos:08x}={snd_offset2 - 0x12:016x}_{snd_identifier:08x}'
                ddi_bytes = self.ddi_bytes[self.ddi_data.tell():self.ddi_data.tell() + 1024]
                align_length = ddi_bytes.find(b'default') - 4
                align_bytes = self.ddi_data.read(align_length)
                frame_align = []
                if align_length > 4:
                    align_group_num = int.from_bytes(align_bytes[0:4], byteorder='little')
                    align_io = io.BytesIO(align_bytes[4:])
                    for _ in range(align_group_num):
                        frame_align.append({
                            'start': int.from_bytes(align_io.read(4), byteorder='little'),
                            'end': int.from_bytes(align_io.read(4), byteorder='little'),
                            'start2': int.from_bytes(align_io.read(4), byteorder='little'),
                            'end2': int.from_bytes(align_io.read(4), byteorder='little'),
                        })
                else:
                    frame_align_group = []
                    for i in range(0, len(align_bytes), 4):
                        frame_align_group.append(int.from_bytes(align_bytes[i:i + 4], byteorder='little'))
                    frame_align.append(frame_align_group)
                artp_data['frame_align'] = frame_align
                assert _read_str(self.ddi_data) == 'default'
                assert artp_idx not in artu_data['artp']
                artu_data['artp'][artp_idx] = artp_data
                artu_data['artp'] = {k: artu_data['artp'][k] for k in sorted(artu_data['artp'])}
            artu_data['phoneme'] = _read_str(self.ddi_data)
            art_data['artu'][artu_idx] = artu_data
            art_data['artu'] = {k: art_data['artu'][k] for k in sorted(art_data['artu'])}
        art_data['art'] = {k: art_data['art'][k] for k in sorted(art_data['art'])}
        art_data['phoneme'] = _read_str(self.ddi_data)
        if not art_data['art']:
            del art_data['art']
        if not art_data.get('artu'):
            del art_data['artu']
        return art_idx, art_data

    def read_vqm(self):
        vqm_data = {}
        assert self.ddi_data.read(8) == b'\xff' * 8
        assert self.ddi_data.read(4).decode() == 'VQM '
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
        assert self.ddi_data.read(8) == b'\xff' * 8
        assert self.ddi_data.read(4).decode() == 'VQMu'
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
        vqmp_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
        assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == vqmp_num
        for _i in range(vqmp_num):
            vqmp_data = {'snd': '', 'epr': []}
            assert self.ddi_data.read(8) == b'\xff' * 8
            assert self.ddi_data.read(4).decode() == 'VQMp'
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 1
            vqmp_data['duration'] = struct.unpack('<d', self.ddi_data.read(8))[0]
            assert int.from_bytes(self.ddi_data.read(2), byteorder='little') == 1
            vqmp_data['pitch1'] = struct.unpack('<f', self.ddi_data.read(4))[0]
            vqmp_data['pitch2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
            vqmp_data['unknown2'] = struct.unpack('<f', self.ddi_data.read(4))[0]
            vqmp_data['dynamics'] = struct.unpack('<f', self.ddi_data.read(4))[0]
            vqmp_data['tempo'] = struct.unpack('<f', self.ddi_data.read(4))[0]
            assert int.from_bytes(self.ddi_data.read(4), byteorder='little') == 0
            assert self.ddi_data.read(4) == b'\xff' * 4
            epr_num = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            epr_list = []
            for _k in range(epr_num):
                ddi_epr_offset = self.ddi_data.tell()
                epr_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
                epr_list.append(f'{ddi_epr_offset:0>8x}={epr_offset:0>8x}')
            vqmp_data['epr'] = epr_list
            vqmp_data['fs'] = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            assert self.ddi_data.read(2) == b'\x01\x00'
            snd_identifier = int.from_bytes(self.ddi_data.read(4), byteorder='little')
            ddi_snd_offset = self.ddi_data.tell()
            snd_offset = int.from_bytes(self.ddi_data.read(8), byteorder='little')
            vqmp_data['snd'] = f'{ddi_snd_offset:0>8x}={snd_offset:016x}_{snd_identifier:08x}'
            assert self.ddi_data.read(0x10) == b'\xff' * 0x10
            vqmp_idx = int(_read_str(self.ddi_data))
            vqm_data[vqmp_idx] = vqmp_data
            assert _read_str(self.ddi_data) == 'GROWL'
            assert _read_str(self.ddi_data) == 'vqm'
        return vqm_data

class PackError(RuntimeError):
    pass


def pack_db(db_path, dst_dir=None, log=print, progress=None):
    """Pack a built singer DB (folder/Name: Name.tree + Name/voice/...) into Name.ddb + Name.ddi.

    Returns (ddb_path, ddi_path). progress(done_units, total_units) is called per unit."""
    db_path = os.path.normpath(os.path.abspath(db_path))
    singer_path, singer_name = db_path, os.path.basename(db_path)
    tree_path = singer_path + '.tree'
    if not os.path.exists(tree_path):
        raise PackError('%s.tree does not exist: build the singer DB first' % singer_path)
    if dst_dir is None:
        dst_dir = os.path.join(os.path.dirname(singer_path), 'package')
    os.makedirs(dst_dir, exist_ok=True)
    ddi_path = os.path.join(dst_dir, singer_name + '.ddi')
    ddb_path = os.path.join(dst_dir, singer_name + '.ddb')
    for p in (ddi_path, ddb_path):
        if os.path.exists(p):
            os.remove(p)
    with open(tree_path, 'rb') as f:
        ddi_bytes = f.read()
    ddi_data = io.BytesIO(ddi_bytes)
    log('reading the .tree (DDI)...')
    try:
        model = DDIModel(ddi_bytes)
        model.read()
    except (AssertionError, ValueError) as ex:
        raise PackError('could not read %s as a singer database: %s' % (tree_path, ex))
    art = model.ddi_data_dict['art']
    n_units = sum(len(v) for v in art.values()) + sum(len(v) for v in model.ddi_data_dict['sta'].values())
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(done[0], n_units)

    log('packing %d units...' % n_units)
    with open(ddb_path, 'wb') as ddb_f:
        for cvvc, art_items in art.items():
            phonemes = cvvc.split(' ')
            if len(phonemes) == 3:
                art_file = os.path.join(singer_path, 'voice', 'articulation', escape_filename(phonemes[0]),
                                        escape_filename(phonemes[1]), escape_filename(phonemes[2]))
            elif len(phonemes) == 2:
                art_file = os.path.join(singer_path, 'voice', 'articulation', escape_filename(phonemes[0]),
                                        escape_filename(phonemes[1]))
            else:
                raise PackError('odd articulation key %r in the .tree' % cvvc)
            if not os.path.exists(art_file):
                raise PackError('articulation file missing: %s (the DB build left units out?)' % art_file)
            with open(art_file, 'rb') as art_f:
                art_bytes = art_f.read()
            with io.BytesIO(art_bytes) as art_data:
                for art_item in art_items:
                    for epr_info in art_item['epr']:
                        ddi_epr_pos, epr_offset = epr_info.split('=')
                        ddb_epr_offset = ddb_f.tell()
                        ddi_epr_pos, epr_offset = int(ddi_epr_pos, 16), int(epr_offset, 16)
                        art_data.seek(epr_offset)
                        if art_data.read(4) != b'FRM2':
                            raise PackError('articulation file is broken: %s' % art_file)
                        frm_len = int.from_bytes(art_data.read(4), byteorder='little')
                        ddb_f.write(art_bytes[epr_offset:epr_offset + frm_len])
                        ddi_data.seek(ddi_epr_pos)
                        ddi_data.write(ddb_epr_offset.to_bytes(8, byteorder='little'))
                    ddi_snd_pos, t = art_item['snd'].split('=')
                    snd_offset, _ = t.split('_')
                    ddi_snd_pos2, t = art_item['snd_start'].split('=')
                    snd_offset2, _ = t.split('_')
                    ddi_snd_pos, snd_offset = int(ddi_snd_pos, 16), int(snd_offset, 16)
                    ddi_snd_pos2, snd_offset2 = int(ddi_snd_pos2, 16), int(snd_offset2, 16)
                    offset2_delta = snd_offset2 - snd_offset
                    art_data.seek(snd_offset)
                    if art_data.read(4) != b'SND ':
                        raise PackError('articulation file is broken: %s' % art_file)
                    snd_len = int.from_bytes(art_data.read(4), byteorder='little')
                    snd_start = snd_offset + snd_len
                    ddb_snd_offset = ddb_f.tell() + 0x12
                    snd_bytes = art_bytes[snd_offset:snd_start]
                    if snd_bytes[0:4] != b'SND ':
                        raise PackError('articulation file is broken: %s' % art_file)
                    ddb_f.write(snd_bytes)
                    ddi_data.seek(ddi_snd_pos)
                    ddi_data.write(ddb_snd_offset.to_bytes(8, byteorder='little'))
                    ddi_data.write((ddb_snd_offset + offset2_delta).to_bytes(8, byteorder='little'))
                    tick()
        for sta_info in model.sta_data.values():
            phoneme = sta_info['phoneme']
            for sta_idx, sta_item in sta_info['stap'].items():
                sta_file = os.path.join(singer_path, 'voice', 'stationary', 'normal',
                                        escape_filename(phoneme), escape_filename(str(sta_idx)))
                if not os.path.exists(sta_file):
                    raise PackError('stationary file missing: %s' % sta_file)
                with open(sta_file, 'rb') as sta_f:
                    sta_bytes = sta_f.read()
                with io.BytesIO(sta_bytes) as sta_data:
                    for epr_info in sta_item['epr']:
                        ddi_epr_pos, epr_offset = epr_info.split('=')
                        ddb_epr_offset = ddb_f.tell()
                        ddi_epr_pos, epr_offset = int(ddi_epr_pos, 16), int(epr_offset, 16)
                        sta_data.seek(epr_offset)
                        if sta_data.read(4) != b'FRM2':
                            raise PackError('stationary file is broken: %s' % sta_file)
                        frm_len = int.from_bytes(sta_data.read(4), byteorder='little')
                        ddb_f.write(sta_bytes[epr_offset:epr_offset + frm_len])
                        ddi_data.seek(ddi_epr_pos)
                        ddi_data.write(ddb_epr_offset.to_bytes(8, byteorder='little'))
                    ddi_snd_pos, snd_name = sta_item['snd'].split('=')
                    snd_offset, _snd_id = snd_name.split('_')
                    ddi_snd_pos, snd_offset = int(ddi_snd_pos, 16), int(snd_offset, 16)
                    real_snd_offset = _reverse_search(sta_bytes, b'SND ', snd_offset)
                    sta_data.seek(real_snd_offset)
                    if sta_data.read(4) != b'SND ':
                        raise PackError('stationary file is broken: %s' % sta_file)
                    snd_len = int.from_bytes(sta_data.read(4), byteorder='little')
                    snd_start = real_snd_offset + snd_len
                    delta_snd_offset = snd_offset - real_snd_offset
                    ddb_snd_offset = ddb_f.tell() + delta_snd_offset
                    ddb_f.write(sta_bytes[real_snd_offset:snd_start])
                    ddi_data.seek(ddi_snd_pos)
                    ddi_data.write(ddb_snd_offset.to_bytes(8, byteorder='little'))
                    tick()
    with open(ddi_path, 'wb') as f:
        f.write(ddi_data.getbuffer())
    log('packed: %s + %s' % (ddb_path, ddi_path))
    return ddb_path, ddi_path

# ------------------------------------------------------------------ VVD files

# Substitution cipher from VVD Editor Plus (atonyxu/vvdeditor): plaintext char -> file char.
# Chars outside the map pass through unchanged. Verified: decoding the real KAITO.vvd from
# a VOCALOID install gives '"ID" = "VOCALOID VIRTUAL VOICE"' etc.
_VVD_ENC = {
    '[': 'A', 'A': '[', 'E': '_', '_': 'E', 'R': 'H', 'I': 'S', 'U': 'O', 'P': 'J',
    'J': 'P', 'Q': 'K', 'K': 'Q', 'M': 'W', 'W': 'M', 'S': 'I', 'D': '^', '^': 'D',
    'F': '\\', 'G': ']', ']': 'G', '\\': 'F', 'H': 'R', 'O': 'U', 'Z': '@', '@': 'Z',
    'X': 'B', 'B': 'X', 'Y': 'C', 'C': 'Y', 'V': 'L', 'L': 'V', 'N': 'T', 'T': 'N',
    '1': '+', '2': '(', '3': ')', '4': '.', '5': '/', '6': ',', '7': '-', '8': '"',
    '9': '#', '0': '*', '+': '1', '(': '2', ')': '3', '.': '4', '/': '5', ',': '6',
    '-': '7', '"': '8', '#': '9', '*': '0', '=': "'", "'": '=', 'a': '{', '{': 'a',
    'r': 'h', 'h': 'r', 't': 'n', 'n': 't', 'u': 'o', 'o': 'u', '}': 'g', 'g': '}',
    'p': 'j', 'j': 'p', 'q': 'k', 'k': 'q', 's': 'i', 'i': 's', '\x7f': 'e', 'e': '\x7f',
    'd': '~', '~': 'd', 'x': 'b', 'b': 'x', 'y': 'c', 'c': 'y', 'v': 'l', 'l': 'v',
    'z': '`', '`': 'z', 'w': 'm', 'm': 'w', '|': 'f', 'f': '|',
}
_VVD_DEC = {v: k for k, v in _VVD_ENC.items()}

VVD_TEMPLATE = ('"ID" = "VOCALOID VIRTUAL VOICE"\n'
                '"FORMAT" = "{fmt}"\n'
                '"VOICEIDSTR" = "{compid}"\n'
                '"VOICENAME" = "{name}"\n'
                '"Breathiness" = "0"\n'
                '"Brightness" = "0"\n'
                '"Clearness" = "0"\n'
                '"Opening" = "0"\n'
                '"Gender Factor" = "0"\n'
                '{growl}')


def encode_vvd(text):
    """Plaintext VVD metadata -> encoded file text."""
    return ''.join(_VVD_ENC.get(c, c) for c in text)


def decode_vvd(data):
    """Encoded .vvd file text/bytes -> plaintext metadata."""
    if isinstance(data, bytes):
        data = data.decode('latin-1')
    return ''.join(_VVD_DEC.get(c, c) for c in data)


def make_vvd(compid, name, fmt='4.0.0.0', growl=True):
    """Encoded .vvd content for a voicebank. V2/V3 databases drop the Growl line."""
    text = VVD_TEMPLATE.format(fmt=fmt, compid=compid, name=name,
                               growl='"Growl" = "1"\n' if growl else '')
    return encode_vvd(text).replace('\n', '\r\n')


def vvd_info(path):
    """{'compid': ..., 'name': ...} from a .vvd file."""
    plain = decode_vvd(open(path, 'rb').read())
    info = {}
    m = re.search(r'"VOICEIDSTR"\s*=\s*"([^"]*)"', plain)
    if m:
        info['compid'] = m.group(1)
    m = re.search(r'"VOICENAME"\s*=\s*"([^"]*)"', plain)
    if m:
        info['name'] = m.group(1)
    return info

# ------------------------------------------------------------------ CompIDs and editors

from .compid import (COMPID_RE, COMPID_ALPHABET, CompIDError, compids_in_use,  # noqa: F401
                     normalize as _normalize_compid, random_compid, status_of)


def new_compid(rng=None):
    """A fresh unique 16-char CompID: 'B' + 15 unambiguous chars, checked against the registry."""
    try:
        return random_compid(rng)
    except CompIDError as ex:
        raise PackError(str(ex)) from ex


def check_compid(compid):
    ok, msg = status_of(compid)
    if not ok:
        raise PackError(msg)
    return (compid or '').strip().upper()


# editor kinds: reg roots are (files branch, mirror branch or None)
EDITOR_KINDS = {
    'V4': {'label': 'VOCALOID4 editor', 'exes': ('VOCALOID4.exe',),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE', 'HKLM\\SOFTWARE\\POCALOID4\\DATABASE')},
    'V4FE-dev': {'label': 'VOCALOID4FE developer editor', 'exes': ('VoiceLoader.exe', 'VOCALOID4_Dev.exe'),
                 'reg': ('HKLM\\SOFTWARE\\VOCALOID4\\DATABASE', 'HKLM\\SOFTWARE\\POCALOID4\\DATABASE')},
    'V3': {'label': 'VOCALOID3 editor', 'exes': ('VOCALOID3.exe',),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID3\\DATABASE\\VOICE3', None)},
    'V2': {'label': 'VOCALOID2 editor', 'exes': ('VOCALOID2.exe', 'SingerEditor2.exe'),
           'reg': ('HKLM\\SOFTWARE\\VOCALOID2\\DATABASE\\VOICE', None)},
}

KNOWN_EDITOR_ROOTS = [
    os.path.join(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'), d)
    for d in ('VOCALOID4\\Editor', 'VOCALOID4FE', 'VOCALOID3', 'VOCALOID2', 'VOCALOID')
]


def guess_editor(path):
    """{'kind', 'root', 'label'} for an editor folder you point at."""
    path = os.path.normpath(os.path.abspath(path))
    if not os.path.isdir(path):
        raise PackError('editor folder does not exist: %s' % path)
    try:
        files = {f.lower() for f in os.listdir(path)}
    except OSError:
        files = set()
    for kind, spec in EDITOR_KINDS.items():
        if any(e.lower() in files for e in spec['exes']):
            return {'kind': kind, 'root': path, 'label': spec['label']}
    low = path.lower()
    if 'vocaloid4' in low:
        kind = 'V4FE-dev' if 'voice' in files and 'loader' in low else 'V4'
    elif 'vocaloid3' in low:
        kind = 'V3'
    elif 'vocaloid2' in low or low.rstrip('\\/').endswith('vocaloid'):
        kind = 'V2'
    else:
        raise PackError('no VOCALOID editor recognized in %s (point at its install folder)' % path)
    return {'kind': kind, 'root': path, 'label': EDITOR_KINDS[kind]['label']}


def find_editors():
    """Detected editor installs, dev editor first (it is this tool's home turf)."""
    found, seen = [], set()
    for root in KNOWN_EDITOR_ROOTS:
        if os.path.isdir(root) and root not in seen:
            try:
                found.append(guess_editor(root))
                seen.add(root)
            except PackError:
                pass
    fe = [e for e in found if e['kind'] == 'V4FE-dev']
    return fe + [e for e in found if e not in fe]


def default_bank_dir(editor, name):
    """Where a new bank folder goes for this editor (real V4 banks live in VoiceDB\\<name>)."""
    pf86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
    if editor['kind'] in ('V4', 'V4FE-dev'):
        return os.path.join(pf86, 'VoiceDB', name)
    return os.path.join(editor['root'], 'voicedbdir', name)

# ------------------------------------------------------------------ packaging and install

INSTALL_BAT = ('set DB_COMPID={compid}\r\n'
               'set DB_NAME={name}\r\n'
               'set DB_VERSION={version}\r\n'
               '\r\n'
               'cd /d %~dp0\r\n'
               'set PKG=%cd%\r\n'
               'set DB_PATH={dest}\r\n'
               'echo installing to %DB_PATH%\\%DB_COMPID%\r\n'
               'if not exist "%DB_PATH%\\%DB_COMPID%" mkdir "%DB_PATH%\\%DB_COMPID%"\r\n'
               'copy /Y "%PKG%\\{base}.ddb" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               'copy /Y "%PKG%\\{base}.ddi" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               'copy /Y "%PKG%\\{base}.vvd" "%DB_PATH%\\%DB_COMPID%\\"\r\n'
               '{slot_lines}'
               '\r\n'
               'if %PROCESSOR_ARCHITECTURE%==x86 (set reg3264= ) else (set reg3264=/reg:32)\r\n'
               '{reg_lines}'
               '{cleanup_lines}'
               'echo done.\r\n')

UNINSTALL_BAT = ('set DB_COMPID={compid}\r\n'
                 '\r\n'
                 'if %PROCESSOR_ARCHITECTURE%==x86 (set reg3264= ) else (set reg3264=/reg:32)\r\n'
                 '{reg_lines}'
                 'echo registration removed. The bank folder was left in place.\r\n')

DEV_SLOT_APPEND_BAT = ('@echo off\r\n'
                       'set INI={ini}\r\n'
                       'type "{slot_file}" >> "%INI%"\r\n'
                       'echo dev slot added to %INI%\r\n')


def _reg_add_lines(branches, compid, values, delete=False):
    lines = []
    for branch in branches:
        key = '%s\\%s' % (branch, compid)
        if delete:
            lines.append('reg DELETE "%s" /F %%reg3264%%\r\n' % key)
            continue
        for value, typ, data in values:
            if value is None:
                lines.append('reg ADD "%s" /F /ve /T %s /D "%s" %%reg3264%%\r\n' % (key, typ, data))
            else:
                lines.append('reg ADD "%s" /F /V %s /T %s /D "%s" %%reg3264%%\r\n'
                             % (key, value, typ, data))
    return ''.join(lines)


def install_script(compid, name, dest, files_base, kind, db_version='4', uninstall=False):
    """The elevated .bat that copies the bank and writes the registry (same shape as VVD Editor's)."""
    if kind == 'V2':
        branches = (EDITOR_KINDS['V2']['reg'][0],)
        values = [('INSTALLDIR', 'REG_SZ', dest), ('NAME', 'REG_SZ', 'VOCALOID2 Voice DB (%s)' % name),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'), ('INSTALLED', 'REG_DWORD', '1'),
                  ('DRP', 'REG_SZ', '050013'), ('KEYS\\default', 'REG_SZ', '1145141919810')]
        slot_lines = ''
    elif kind == 'V3':
        branches = (EDITOR_KINDS['V3']['reg'][0],)
        values = [('PATH', 'REG_SZ', dest), ('NAME', 'REG_SZ', 'VOCALOID3 Library (%s)' % name),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'), ('INSTALLED', 'REG_DWORD', '1'),
                  ('DRP', 'REG_SZ', '0B0032'), ('KEYS\\default', 'REG_SZ', '1145141919810')]
        slot_lines = ''
    else:
        # mirror a working commercial install (MIKU_V4X): DBVERSION dword, DRP 0B0A51, no KEYS
        branches = EDITOR_KINDS['V4']['reg']
        values = [('PATH', 'REG_SZ', dest), ('DRP', 'REG_SZ', '0B0A51'),
                  ('INSTALLED', 'REG_DWORD', '1'), ('DBVERSION', 'REG_DWORD', '4'),
                  ('NAME', 'REG_SZ', 'VOCALOID%s Library (%s)' % (db_version, name)),
                  ('TIME', 'REG_SZ', 'BHMN74F9ED86FKAB'),
                  ('BankName', 'REG_SZ', name)]
        slot_lines = ''
    if uninstall:
        return UNINSTALL_BAT.format(compid=compid,
                                    reg_lines=_reg_add_lines(branches, compid, values, delete=True))
    cleanup_lines = ''
    if kind not in ('V2', 'V3'):
        # self-heal the first-generation layout: files sat in the bank folder root (the editor
        # only looks in the CompID subfolder) and a placeholder KEYS value was written
        cleanup_lines = ('del "%DB_PATH%\\{base}.ddb" "%DB_PATH%\\{base}.ddi" '
                         '"%DB_PATH%\\{base}.vvd" 2>nul\r\n').format(DB_PATH=dest, base=files_base)
        cleanup_lines += ''.join('reg DELETE "%s\\%s\\KEYS" /F %%reg3264%% 2>nul\r\n'
                                 % (b, compid) for b in branches)
    return INSTALL_BAT.format(compid=compid, name=name, version=db_version, dest=dest,
                              base=files_base, slot_lines=slot_lines, cleanup_lines=cleanup_lines,
                              reg_lines=_reg_add_lines(branches, compid, values))


def make_package(db_path, out_dir=None, name=None, compid=None, db_version='4', log=print):
    """Pack the DB and write the voicebank package: <out>/<compid>/<name>.{ddb,ddi,vvd}.

    Returns the package folder. Also writes install.bat/uninstall.bat next to the files."""
    db_path = os.path.normpath(os.path.abspath(db_path))
    name = name or os.path.basename(db_path.rstrip('\\/')) or 'Singer'
    name = re.sub(r'[^A-Za-z0-9 _-]+', '_', name).strip() or 'Singer'
    compid = check_compid(compid) if compid else new_compid()
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(db_path), 'package')
    package = os.path.join(os.path.abspath(out_dir), compid)
    if os.path.exists(package):
        raise PackError('package folder already exists: %s' % package)
    os.makedirs(package)
    ddb_src, ddi_src = pack_db(db_path, package, log=log)
    base = os.path.splitext(os.path.basename(ddb_src))[0]
    for ext in ('.ddb', '.ddi'):
        os.rename(os.path.join(package, base + ext), os.path.join(package, name + ext))
    with open(os.path.join(package, name + '.vvd'), 'w', encoding='latin-1', newline='') as f:
        f.write(make_vvd(compid, name, fmt='%s.0.0.0' % db_version, growl=db_version == '4'))
    log('voicebank package: %s' % package)
    return package


def _package_files(package):
    """(compid, base, {ext: path}) for a package folder."""
    compid = os.path.basename(os.path.normpath(os.path.abspath(package)))
    if not COMPID_RE.match(compid):
        raise PackError('%s is not a package folder (it should be named by its CompID)' % package)
    found = {}
    for fn in os.listdir(package):
        _base, ext = os.path.splitext(fn)
        if ext.lower() in ('.ddb', '.ddi', '.vvd'):
            found.setdefault(_base, {})[ext.lower()] = os.path.join(package, fn)
    if not found:
        raise PackError('no .ddb/.ddi/.vvd in %s' % package)
    base = sorted(found)[0]
    got = found[base]
    if '.ddb' not in got or '.vvd' not in got:
        raise PackError('%s has no %s.ddb/.vvd pair' % (package, base))
    info = vvd_info(got['.vvd'])
    return compid, base, got, info


def write_install_scripts(package, editor, dest=None, compid=None, name=None, db_version='4', log=print):
    """Write install.bat/uninstall.bat into the package. Returns (install_bat, dest)."""
    compid0, base, _got, info = _package_files(package)
    compid = check_compid(compid) if compid else compid0
    if compid != compid0:
        log('warning: installing under a different CompID than the package folder name')
    name = name or info.get('name') or base
    if dest is None:
        dest = default_bank_dir(editor, name)
    dest = os.path.normpath(os.path.abspath(dest))
    install_bat = os.path.join(package, 'install.bat')
    uninstall_bat = os.path.join(package, 'uninstall.bat')
    with open(install_bat, 'w', encoding='ascii', newline='') as f:
        f.write(install_script(compid, name, dest, base, editor['kind'], db_version))
    with open(uninstall_bat, 'w', encoding='ascii', newline='') as f:
        f.write(install_script(compid, name, dest, base, editor['kind'], db_version, uninstall=True))
    return install_bat, dest


def install_package(package, editor, dest=None, compid=None, name=None, db_version='4', log=print):
    """Stage install.bat/uninstall.bat and report what the elevated install will do."""
    install_bat, dest = write_install_scripts(package, editor, dest, compid, name, db_version, log)
    compid0, base, _got, _info = _package_files(package)
    log('bank folder: %s' % dest)
    log('registry: %s\\%s (+ mirror)' % (EDITOR_KINDS[editor['kind']]['reg'][0], compid or compid0))
    log('run as administrator: %s' % install_bat)
    return install_bat, dest


def dev_ini_path(editor_root):
    return os.path.join(editor_root, 'DB_Dev.ini')


def read_dev_slots(ini_path):
    """[(n, voice_name, tree_path)] from a dev editor DB_Dev.ini."""
    slots = []
    try:
        text = open(ini_path, encoding='latin-1').read()
    except OSError:
        return slots
    for m in re.finditer(r'\[VVoice (\d+)\]([^\[]*)', text):
        body = m.group(2)
        gv = lambda k: re.search(r'^%s\s*=\s*"?([^"]*)"?\s*$' % k, body, re.M)
        vv = gv('VVoiceName')
        dp, dn = gv('DBPath'), gv('DBName')
        tree = os.path.join(dp.group(1), dn.group(1)) if dp and dn else ''
        slots.append((int(m.group(1)), vv.group(1) if vv else '', tree))
    return slots


def write_dev_slot(editor_root, db_path, name=None, log=print):
    """Register a loose built DB as a new [VVoice N] slot in the dev editor's DB_Dev.ini.

    The ini lives in Program Files, so this writes a snippet + an elevated .bat that appends it."""
    ini = dev_ini_path(editor_root)
    if not os.path.exists(ini):
        raise PackError('no DB_Dev.ini in %s: not a developer editor?' % editor_root)
    db_path = os.path.normpath(os.path.abspath(db_path))
    if not os.path.exists(db_path + '.tree'):
        raise PackError('%s.tree does not exist: build the singer DB first' % db_path)
    name = name or os.path.basename(db_path.rstrip('\\/')) or 'Singer'
    slots = read_dev_slots(ini)
    n = max([s[0] for s in slots] + [-1]) + 1
    pcs = set()
    for m in re.finditer(r'ProgramChange\s*=\s*(\d+)', open(ini, encoding='latin-1').read()):
        pcs.add(int(m.group(1)))
    pc = max(pcs) + 1 if pcs else 0
    folder, tree = os.path.dirname(db_path), os.path.basename(db_path)
    snippet = ('\r\n[VVoice %d]\r\nBankSelect = 1\r\nProgramChange = %d\r\nComponentID = "%016d"\r\n'
               'DBPath = "%s"\r\nDBName = "%s.tree"\r\nVVoiceName = "%s"\r\n'
               'Breathiness = 0\r\nBrightness = 0\r\nClearness = 0\r\nGenderFactor = 0\r\nOpening = 0\r\n'
               % (n, pc, n + 1, folder, tree, name))
    work = os.path.join(os.path.dirname(db_path), 'devslot_%d' % n)
    os.makedirs(work, exist_ok=True)
    slot_file = os.path.join(work, 'slot.txt')
    with open(slot_file, 'w', encoding='latin-1', newline='') as f:
        f.write(snippet)
    bat = os.path.join(work, 'add_dev_slot.bat')
    with open(bat, 'w', encoding='ascii', newline='') as f:
        f.write(DEV_SLOT_APPEND_BAT.format(ini=ini, slot_file=slot_file))
    log('dev slot staged: run as administrator: %s' % bat)
    return bat