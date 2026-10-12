#!/usr/bin/env python3
"""Henkan 変換 GUI: pick an UTAU bank, convert it, build the singer DB with the DBTool, test it in the
VOCALOID4 Editor for Developer, pack and install it, or backport a V3/V4 bank to VOCALOID2. Every step
runs the command line tools, so the log shows exactly what the CLI would print.

    python gui.py
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
DEVKIT = os.path.dirname(ROOT)
CLI = os.path.join(ROOT, 'henkan.py')
_APPDATA = os.environ.get('APPDATA', os.path.expanduser('~'))
SETTINGS = os.path.join(_APPDATA, 'Henkan', 'gui.json')
OLD_SETTINGS = os.path.join(_APPDATA, 'utau2vocaloid', 'gui.json')     # read once if SETTINGS isn't there yet
sys.path.insert(0, ROOT)


SAVED = ('bank', 'out', 'dict', 'lang', 'colors', 'keep', 'splice', 'fill', 'normalize', 'derive', 'extend_dict',
         'extras', 'evec_map', 'evec_phonemes', 'custom_extras', 'evec_vowels', 'v_mode',
         'merge_groups', 'brighten', 'avoid', 'db_name', 'db_path', 'db_lang', 'dbtool', 'dbtool_hidden',
         'pkg_db', 'pkg_name', 'pkg_compid', 'pkg_out', 'pkg_editor', 'pkg_dest', 'pkg_devini',
         'v3v2_name', 'v3v2_lang', 'sections', 'geometry', 'tab')

V3V2_BROWSE_FOLDER = 'Browse for a voicebank folder…'
V3V2_BROWSE_TREE = 'Browse for a developer-kit .tree…'
V3V2_PACK_DB = "Use the Pack tab's built DB (.tree)…"

# ヴ choices: the label's first word is the --v-mode value
V_MODE_LABELS = ('auto — v when the bank has ヴ', "v — own phonemes v, v'", "b — sing it as b, b'")

UI_FONT, UI_FONT_BOLD = 'Segoe UI', 'Segoe UI Semibold'
INK, INK_SOFT = '#1d2230', '#a7adbd'          # header band
ACCENT = '#d6336c'
HINT, INFO = '#6b7280', '#374151'


class ScrollFrame(ttk.Frame):
    """A frame whose .body scrolls vertically (wheel included) once it is taller than the space it gets."""

    def __init__(self, parent):
        super().__init__(parent)
        bg = ttk.Style(self).lookup('TFrame', 'background') or 'SystemButtonFace'
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=bg)
        self.vsb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', self._fit)
        self.canvas.bind('<Configure>', self._fit)
        self.bind('<Enter>', lambda _e: self.bind_all('<MouseWheel>', self._wheel))
        self.bind('<Leave>', lambda _e: self.unbind_all('<MouseWheel>'))

    def _fit(self, _e=None):
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), self.body.winfo_reqheight()))
        if self.body.winfo_reqheight() > self.canvas.winfo_height() + 1:
            if not self.vsb.winfo_ismapped():
                self.vsb.pack(side='right', fill='y', before=self.canvas)
        elif self.vsb.winfo_ismapped():
            self.vsb.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, ev):
        if self.vsb.winfo_ismapped() and not isinstance(ev.widget, (ttk.Combobox, ttk.Spinbox)):
            self.canvas.yview_scroll(-1 if ev.delta > 0 else 1, 'units')


class Section(ttk.Frame):
    """A titled block that folds open and shut. summary (a StringVar) shows next to the title."""

    def __init__(self, parent, title, opened=False, on_toggle=None):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.opened, self.on_toggle = opened, on_toggle
        self.summary = tk.StringVar()
        head = ttk.Frame(self, cursor='hand2')
        head.grid(row=0, column=0, sticky='ew')
        self.arrow = ttk.Label(head, style='Sec.TLabel', width=2)
        self.arrow.pack(side='left')
        title_lb = ttk.Label(head, text=title, style='Sec.TLabel')
        title_lb.pack(side='left')
        sum_lb = ttk.Label(head, textvariable=self.summary, style='Hint.TLabel')
        sum_lb.pack(side='left', padx=12)
        for w in (head, self.arrow, title_lb, sum_lb):
            w.bind('<Button-1>', lambda _e: self.toggle())
        ttk.Separator(self).grid(row=1, column=0, sticky='ew', pady=(3, 0))
        self.body = ttk.Frame(self, padding=(20, 6, 0, 2))
        self._show()

    def toggle(self):
        self.opened = not self.opened
        self._show()
        if self.on_toggle:
            self.on_toggle()

    def _show(self):
        self.arrow.configure(text='▾' if self.opened else '▸')
        if self.opened:
            self.body.grid(row=2, column=0, sticky='ew')
        else:
            self.body.grid_remove()


class Runner:
    """Runs one command at a time in the background and streams its output into the log."""

    def __init__(self, app):
        self.app, self.proc, self.q = app, None, queue.Queue()

    @property
    def busy(self):
        return self.proc is not None

    def start(self, cmd, title, done=None):
        if self.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish (or press Stop).')
            return
        self.app.log('\n$ %s\n' % ' '.join('"%s"' % c if ' ' in c else c for c in cmd[1:]), 'cmd')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding='utf-8',
                                     errors='replace', bufsize=1, creationflags=flags)
        self.done, self.title = done, title
        self.app.set_busy(title)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self.app.after(100, self._poll)

    def _read(self, proc):
        for line in proc.stdout:
            if 'warnings.warn(' in line or '32-bit application should be automated' in line:
                continue                                    # pywinauto noise
            self.q.put(line)
        proc.wait()
        self.q.put(None)

    def _poll(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line is None:
                    code = self.proc.returncode
                    self.proc = None
                    self.app.set_busy(None)
                    self.app.log('%s %s\n' % (self.title, 'finished' if code == 0 else 'failed (exit %s)' % code),
                                 'ok' if code == 0 else 'err')
                    if self.done:
                        self.done(code)
                    return
                tag = 'err' if re.search(r'Traceback|Error|failed|problems', line) else None
                self.app.log(line, tag)
        except queue.Empty:
            pass
        self.app.after(100, self._poll)

    def stop(self):
        if not self.proc:
            return
        # the build drives the DBTool and the render the dev editor: end the whole process tree
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.app.log('stopped\n', 'err')


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        from u2v import __version__
        self.title('変換 Henkan ' + __version__)
        self.geometry('960x900')
        self.minsize(820, 640)
        self.runner = Runner(self)
        self.v = {}
        self.groups = []          # [(group, entries)]
        self.detected_lang = None
        self._vars()
        self._load()
        self._ui()
        if re.fullmatch(r'\d+x\d+([+-]-?\d+){2}', self.v['geometry'].get()):
            self.geometry(self.v['geometry'].get())
        self.protocol('WM_DELETE_WINDOW', self._close)
        if self.v['bank'].get() and os.path.isdir(self.v['bank'].get()):
            self.after(200, self.scan)

    # ------------------------------------------------------------------ state
    def _vars(self):
        S, B, I = tk.StringVar, tk.BooleanVar, tk.IntVar
        self.v.update(bank=S(), out=S(), dict=S(), lang=S(value='auto'), colors=S(value='main'), keep=I(value=1),
                      splice=B(value=True), fill=B(value=True), normalize=B(value=True), derive=B(value=True),
                      extend_dict=B(value=True), merge_groups=B(value=False), extras=B(value=True),
                      brighten=S(), avoid=S(), evec_map=S(), evec_phonemes=S(),
                      custom_extras=S(), evec_vowels=B(value=False), v_mode=S(value=V_MODE_LABELS[0]),
                      sections=S(value='extras'), geometry=S(), tab=S(value='0'),
                      db_name=S(), db_path=S(), db_lang=S(value='Japanese'),
                      pkg_db=S(), pkg_name=S(), pkg_compid=S(), pkg_out=S(), pkg_editor=S(),
                      pkg_dest=S(), pkg_devini=B(value=False),
                      v3v2_src=S(), v3v2_name=S(), v3v2_lang=S(value='Auto'),
                      dbtool=S(value=os.path.join(DEVKIT, 'VocaloidDBTool3.exe')), dbtool_hidden=B(value=True))
        self.status = S(value='Ready')
        self.info = S(value='Choose an UTAU voicebank folder.')
        self.evec_info = S(value='')
        self.evec_pick, self.evec_pick_order, self.color_rows = {}, [], []

    def _load(self):
        for path in (SETTINGS, OLD_SETTINGS):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            for k in SAVED:
                if k in data:
                    try:
                        self.v[k].set(data[k])
                    except tk.TclError:
                        pass
            break
        if self.v['v_mode'].get().split()[0] not in ('auto', 'v', 'b'):
            self.v['v_mode'].set(V_MODE_LABELS[0])

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            self.v['geometry'].set(self.geometry())
            if hasattr(self, 'nb'):
                self.v['tab'].set(str(self.nb.index('current')))
            with open(SETTINGS, 'w', encoding='utf-8') as f:
                json.dump({k: self.v[k].get() for k in SAVED}, f, indent=1)
        except OSError:
            pass

    def _close(self):
        if (self.runner.busy or getattr(self, '_v3v2_busy', False)) \
                and not messagebox.askyesno('Quit', 'A step is still running. Stop it and quit?'):
            return
        self.runner.stop()
        self._save()
        self.destroy()

    # --------------------------------------------------------------------- UI
    def _ui(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Head.TLabel', font=(UI_FONT_BOLD, 10))
        style.configure('Sec.TLabel', font=(UI_FONT_BOLD, 10), foreground=INK)
        style.configure('Hint.TLabel', foreground=HINT)
        style.configure('Info.TLabel', foreground=INFO)
        style.configure('Rec.TLabel', foreground='#15803d')
        style.configure('Splice.TLabel', foreground='#b45309')
        style.configure('Accent.TButton', font=(UI_FONT_BOLD, 9), padding=(16, 5))
        style.configure('Big.TButton', padding=(12, 4))
        style.configure('TNotebook.Tab', padding=(12, 4))

        self._header()

        src = ttk.Frame(self, padding=(12, 10, 12, 0))
        src.pack(fill='x')
        src.columnconfigure(1, weight=1)
        ttk.Label(src, text='UTAU bank', style='Head.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Entry(src, textvariable=self.v['bank']).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(src, text='Browse…', command=self.pick_bank).grid(row=0, column=2)
        ttk.Button(src, text='Rescan', command=self.scan).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(src, textvariable=self.info, style='Info.TLabel').grid(row=1, column=1, columnspan=3,
                                                                         sticky='w', padx=6, pady=(2, 0))
        ttk.Label(src, text='Phonetic dictionary', style='Head.TLabel').grid(row=2, column=0, sticky='w',
                                                                            pady=(6, 0))
        ttk.Entry(src, textvariable=self.v['dict']).grid(row=2, column=1, sticky='ew', padx=6, pady=(6, 0))
        ttk.Button(src, text='Browse…', command=self.pick_dict).grid(row=2, column=2, pady=(6, 0))
        ttk.Label(src, text='From the devkit: Japanese Dictionary\\Japanese_Dictionary.txt (English: English '
                            'Dictionary\\english_phonetic_dictionary_20061220.txt). Found automatically when it can be.',
                  style='Hint.TLabel', wraplength=760).grid(row=3, column=1, columnspan=3, sticky='w', padx=6)

        bar = ttk.Frame(self, padding=(12, 4, 12, 8))
        bar.pack(side='bottom', fill='x')
        self.prog = ttk.Progressbar(bar, mode='determinate', length=160)
        self.prog.pack(side='left')
        ttk.Label(bar, textvariable=self.status).pack(side='left', padx=8)
        self.stop_btn = ttk.Button(bar, text='Stop', command=self.runner.stop, state='disabled')
        self.stop_btn.pack(side='right')
        ttk.Button(bar, text='Clear log', command=self.clear_log).pack(side='right', padx=6)

        pw = ttk.PanedWindow(self, orient='vertical')
        pw.pack(fill='both', expand=True, padx=12, pady=(8, 0))
        self.nb = nb = ttk.Notebook(pw)
        nb.add(self._tab_convert(nb), text='①  Convert')
        nb.add(self._tab_build(nb), text='②  Build singer DB')
        nb.add(self._tab_pack(nb), text='③  Pack & Install')
        nb.add(self._tab_v3v2(nb), text='④  V3 → V2 backport')
        try:
            nb.select(int(self.v['tab'].get()))
        except (ValueError, tk.TclError):
            pass
        pw.add(nb, weight=4)

        lf = ttk.Frame(pw, padding=(0, 6, 0, 0))
        pw.add(lf, weight=1)
        self.text = tk.Text(lf, wrap='word', font=('Consolas', 9), height=8, relief='flat', padx=8, pady=6,
                            background='#1e1e1e', foreground='#d4d4d4', insertbackground='#d4d4d4')
        sb = ttk.Scrollbar(lf, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('cmd', foreground='#9cdcfe')
        self.text.tag_configure('ok', foreground='#6a9955')
        self.text.tag_configure('warn', foreground='#dcdcaa')
        self.text.tag_configure('err', foreground='#f48771')
        self.bind_all('<Control-l>', lambda _e: self.clear_log())
        self.bind_all('<F5>', lambda _e: self.scan())

    def _header(self):
        from u2v import __version__
        h = tk.Frame(self, background=INK)
        h.pack(fill='x')
        tk.Label(h, text='変換', background=INK, foreground=ACCENT,
                 font=('Yu Gothic UI', 22, 'bold')).pack(side='left', padx=(16, 8), pady=6)
        t = tk.Frame(h, background=INK)
        t.pack(side='left')
        tk.Label(t, text='Henkan', background=INK, foreground='#ffffff',
                 font=(UI_FONT_BOLD, 14)).pack(anchor='w')
        tk.Label(t, text='UTAU bank → VOCALOID singer DB → installed voicebank   ·   V3/V4 → VOCALOID2',
                 background=INK, foreground=INK_SOFT, font=(UI_FONT, 9)).pack(anchor='w')
        tk.Label(h, text='v' + __version__, background=INK, foreground=INK_SOFT,
                 font=(UI_FONT, 9)).pack(side='right', padx=16)

    def _row(self, parent, r, label, var, browse=None, width=None, browse_text='Browse…'):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=3)
        e = ttk.Entry(parent, textvariable=var, width=width or 60)
        e.grid(row=r, column=1, sticky='ew', padx=6, pady=3)
        if browse:
            ttk.Button(parent, text=browse_text, command=browse).grid(row=r, column=2, pady=3)
        return e

    @staticmethod
    def _hint(parent, r, text, column=1, columnspan=1, **kw):
        lb = ttk.Label(parent, text=text, style='Hint.TLabel', justify='left', **kw)
        lb.grid(row=r, column=column, columnspan=columnspan, sticky='w', padx=6)
        return lb

    def _section(self, parent, row, key, title):
        opened = key in self.v['sections'].get().split(',')
        s = Section(parent, title, opened, on_toggle=self._sections_changed)
        s.key = key
        s.grid(row=row, column=0, sticky='ew', pady=(10, 0))
        self.sections[key] = s
        return s

    def _sections_changed(self):
        self.v['sections'].set(','.join(k for k, s in self.sections.items() if s.opened))

    def _tab_convert(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)

        left = ttk.Frame(f)
        left.grid(row=0, column=0, sticky='nsw')
        ttk.Label(left, text='Folders / suffixes', style='Sec.TLabel').pack(anchor='w')
        ttk.Label(left, text='none selected = all', style='Hint.TLabel').pack(anchor='w')
        box = ttk.Frame(left)
        box.pack(fill='both', expand=True, pady=4)
        self.glist = tk.Listbox(box, selectmode='extended', height=9, width=30, exportselection=False,
                                activestyle='none', relief='solid', borderwidth=1, highlightthickness=0,
                                selectbackground=ACCENT, selectforeground='#ffffff')
        gsb = ttk.Scrollbar(box, command=self.glist.yview)
        self.glist.configure(yscrollcommand=gsb.set)
        self.glist.pack(side='left', fill='both', expand=True)
        gsb.pack(side='right', fill='y')
        pick = ttk.Frame(left)
        pick.pack(fill='x')
        ttk.Button(pick, text='All', width=6, command=lambda: self.glist.selection_set(0, 'end')).pack(side='left')
        ttk.Button(pick, text='None', width=6,
                   command=lambda: self.glist.selection_clear(0, 'end')).pack(side='left', padx=6)

        sf = ScrollFrame(f)
        sf.grid(row=0, column=1, sticky='nsew', padx=(16, 0))
        r = sf.body
        r.columnconfigure(0, weight=1)

        out = ttk.Frame(r)
        out.grid(row=0, column=0, sticky='ew')
        out.columnconfigure(1, weight=1)
        self._row(out, 0, 'Output folder', self.v['out'], self.pick_out, width=40)

        o = ttk.Frame(r)
        o.grid(row=1, column=0, sticky='w', pady=(4, 0))
        ttk.Label(o, text='Language').grid(row=0, column=0, sticky='w', pady=2)
        ttk.Combobox(o, textvariable=self.v['lang'], values=('auto', 'ja', 'en'), width=7,
                     state='readonly').grid(row=0, column=1, sticky='w', padx=(6, 24))
        ttk.Label(o, text='Voice colours').grid(row=0, column=2, sticky='w')
        ttk.Combobox(o, textvariable=self.v['colors'], values=('main', 'evec', 'merge'), width=7,
                     state='readonly').grid(row=0, column=3, sticky='w', padx=6)
        ttk.Label(o, text='Samples per unit').grid(row=1, column=0, sticky='w', pady=2)
        ttk.Spinbox(o, textvariable=self.v['keep'], from_=1, to=5, width=6).grid(row=1, column=1, sticky='w',
                                                                                 padx=(6, 24))
        ttk.Label(o, text='ヴ sings as').grid(row=1, column=2, sticky='w')
        ttk.Combobox(o, textvariable=self.v['v_mode'], values=V_MODE_LABELS, width=30,
                     state='readonly').grid(row=1, column=3, sticky='w', padx=6)

        self.sections = {}
        s = self._section(r, 2, 'processing', 'Processing').body
        checks = [('splice', 'Fake missing transitions (CV legato)'), ('fill', 'Fill missing units'),
                  ('normalize', 'Even out loudness'), ('derive', 'Units from silence'),
                  ('extend_dict', "Add b, b', p\\' to dictionary"), ('merge_groups', 'Treat folders as one set')]
        for i, (k, t) in enumerate(checks):
            ttk.Checkbutton(s, text=t, variable=self.v[k]).grid(row=i // 2, column=i % 2, sticky='w',
                                                                padx=(0, 24), pady=1)

        s = self._section(r, 3, 'extras', 'Extras & transitions').body
        s.columnconfigure(1, weight=1)
        ttk.Checkbutton(s, text='Vowel endings  ("a R" → [a Sil])', variable=self.v['extras']).grid(
            row=0, column=0, columnspan=3, sticky='w')
        self._row(s, 1, 'Custom extras', self.v['custom_extras'], self._edit_custom, width=30,
                  browse_text='Edit…')
        self._hint(s, 2, 'Any sound in the bank as a phoneme of your own: breaths, a rolled r, a laugh …\n'
                         'An alias that starts with a vowel keeps its recorded transition, so a R, i R … → hh\n'
                         'gives every vowel its own way in. Edit… shows which transitions are recorded.')

        s = self._section(r, 4, 'colours', 'Voice colours (EVEC)').body
        s.columnconfigure(1, weight=1)
        ttk.Label(s, textvariable=self.evec_info, style='Info.TLabel', wraplength=560,
                  justify='left').grid(row=0, column=0, columnspan=3, sticky='w')
        self.evec_box = ttk.Frame(s)
        self.evec_box.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(s, text='Auto', command=self._auto_evec).grid(row=1, column=2, sticky='ne', pady=(4, 0))
        ttk.Label(s, textvariable=self.v['evec_map'], style='Hint.TLabel', wraplength=560).grid(
            row=2, column=0, columnspan=3, sticky='w', pady=(2, 0))
        self._hint(s, 3, 'The first colour is the main voice (no suffix). skip drops a colour; '
                         'a # number switches Voice colours to evec.', column=0, columnspan=3)
        ttk.Checkbutton(s, text='EVEC support mode: only vowels get #numbers (consonants shared, easy crossfades)',
                        variable=self.v['evec_vowels']).grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self._row(s, 5, 'EVEC phonemes', self.v['evec_phonemes'], width=30)
        self._hint(s, 6, 'e.g.  Soft:a,i,M,e,o; Whisper:a,e   (empty = all; a colour with no vowels '
                         'warns but still converts)')

        s = self._section(r, 5, 'tuning', 'Fine-tuning').body
        s.columnconfigure(1, weight=1)
        self._row(s, 0, 'Brighten', self.v['brighten'], width=30)
        self._hint(s, 1, 'e.g.  o=3   (dB of treble for a dull vowel; several: o=3, a=1)')
        self._row(s, 2, 'Avoid units', self.v['avoid'], width=30)
        self._hint(s, 3, "e.g.  g' i; k o   (rebuild these from other recordings)")

        b = ttk.Frame(f)
        b.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        ttk.Button(b, text='Convert', style='Accent.TButton', command=self.convert).pack(side='left')
        ttk.Button(b, text='Validate', command=self.validate).pack(side='left', padx=6)
        ttk.Button(b, text='Open report', command=lambda: self.open_file(os.path.join(self.v['out'].get(),
                                                                                       'report.txt'))).pack(side='left')
        ttk.Button(b, text='Open folder', command=lambda: self.open_file(self.v['out'].get())).pack(side='left', padx=6)

        for k in ('splice', 'fill', 'normalize', 'derive', 'extend_dict', 'merge_groups', 'extras', 'custom_extras', 'evec_map', 'evec_vowels', 'evec_phonemes', 'colors', 'brighten', 'avoid'):
            self.v[k].trace_add('write', lambda *_a: self._summaries())
        self._summaries()
        return f

    def _summaries(self):
        """One-line state of each Convert section, shown next to its title (so closed sections still say
        what they will do)."""
        if not getattr(self, 'sections', None):
            return
        v = self.v
        names = {'splice': 'legato', 'fill': 'fill', 'normalize': 'loudness', 'derive': 'silence units',
                 'extend_dict': "b/b'/p\\'", 'merge_groups': 'one set'}
        off = [n for k, n in names.items() if k != 'merge_groups' and not v[k].get()]
        bits = ['all on' if not off else 'off: ' + ', '.join(off)]
        if v['merge_groups'].get():
            bits.append('folders merged')
        self.sections['processing'].summary.set(' · '.join(bits))
        try:
            from u2v import select as _SelS
            phs = sorted({ph for ph, _v, _g in _SelS.parse_custom_extras(v['custom_extras'].get()).values()})
        except ValueError:
            phs = ['?']
        self.sections['extras'].summary.set('vowel endings %s · %s' % (
            'on' if v['extras'].get() else 'off', ', '.join(phs) if phs else 'no custom extras'))
        n_col = len(getattr(self, 'color_rows', []) or [])
        col = '%s' % v['colors'].get()
        if v['evec_vowels'].get():
            col += ', vowels only'
        self.sections['colours'].summary.set('%s · %d colour%s found' % (col, n_col, '' if n_col == 1 else 's')
                                             if n_col else col)
        tune = []
        if v['brighten'].get().strip():
            tune.append('brighten ' + v['brighten'].get().strip())
        n_av = len([u for u in v['avoid'].get().split(';') if u.strip()])
        if n_av:
            tune.append('avoid %d unit%s' % (n_av, '' if n_av == 1 else 's'))
        self.sections['tuning'].summary.set(' · '.join(tune) or 'none')

    def _intro(self, f, text):
        ttk.Label(f, text=text, style='Info.TLabel', wraplength=820, justify='left').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))

    @staticmethod
    def _group(f, row, title):
        g = ttk.LabelFrame(f, labelwidget=ttk.Label(f, text=' %s ' % title, style='Sec.TLabel'), padding=(10, 6))
        g.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(0, 10))
        g.columnconfigure(1, weight=1)
        return g

    def _tab_build(self, nb):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        self._intro(f, 'Builds the singer DB from the converted folder with VocaloidDBTool3. It takes a few '
                       'minutes. The DBTool is driven automatically, out of sight on a hidden desktop, so you can '
                       'keep using the PC; if it gets stuck there, it runs again where you can see it.')
        g = self._group(f, 1, 'Singer DB')
        self._row(g, 0, 'Singer name', self.v['db_name'], width=30)
        self._row(g, 1, 'New DB (folder\\Name)', self.v['db_path'], self.pick_db)
        ttk.Label(g, text='Language').grid(row=2, column=0, sticky='w', pady=3)
        ttk.Combobox(g, textvariable=self.v['db_lang'], values=('Japanese', 'English', 'Spanish'), width=12,
                     state='readonly').grid(row=2, column=1, sticky='w', padx=6)
        g = self._group(f, 2, 'Devkit')
        self._row(g, 0, 'VocaloidDBTool3.exe', self.v['dbtool'], self.pick_dbtool)
        ttk.Checkbutton(g, text='Run the DBTool in the background (hidden)', variable=self.v['dbtool_hidden']).grid(
            row=1, column=1, sticky='w', padx=6, pady=(2, 0))
        self._hint(g, 2, 'Untick to watch it work. Leave it alone then: it clicks itself.')
        b = ttk.Frame(f)
        b.grid(row=3, column=0, sticky='w')
        ttk.Button(b, text='Build DB', style='Accent.TButton', command=self.build).pack(side='left')
        ttk.Button(b, text='Convert + Build', command=lambda: self.convert(then=self.build)).pack(side='left', padx=6)
        return f

    def _tab_pack(self, nb):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        self._intro(f, 'Packs the built DB into a voicebank (.ddb + .ddi + .vvd) and installs it into any '
                       'VOCALOID editor on this PC.')
        g = self._group(f, 1, 'Package')
        self._row(g, 0, 'Built DB (folder\\Name)', self.v['pkg_db'], self.pick_built)
        self._row(g, 1, 'Voice name', self.v['pkg_name'], width=30)
        self._row(g, 2, 'CompID', self.v['pkg_compid'], self.gen_compid, browse_text='Generate…')
        self._hint(g, 3, '16 capital letters/digits, unique per voicebank (checked against this PC)')
        self._row(g, 4, 'Package folder', self.v['pkg_out'], self.pick_pkg_out)
        b = ttk.Frame(g)
        b.grid(row=5, column=1, sticky='w', pady=(8, 2), padx=6)
        ttk.Button(b, text='Pack voicebank', style='Accent.TButton', command=self.pack).pack(side='left')
        ttk.Button(b, text='Open package', command=lambda: self.open_file(self.package_dir())).pack(
            side='left', padx=6)
        g = self._group(f, 2, 'Install')
        ttk.Label(g, text='Install into').grid(row=0, column=0, sticky='w', pady=3)
        self.editor_box = ttk.Combobox(g, textvariable=self.v['pkg_editor'], width=57, state='readonly')
        self.editor_box.grid(row=0, column=1, sticky='ew', padx=6)
        self.editor_box['values'] = self._editors()
        ttk.Button(g, text='Browse…', command=self.pick_editor).grid(row=0, column=2, pady=3)
        self._row(g, 1, 'Bank folder', self.v['pkg_dest'], self.pick_pkg_dest)
        self._hint(g, 2, 'Where the .ddb/.ddi/.vvd live (VOCALOID4 banks: VoiceDB\\<name>)')
        ttk.Checkbutton(g, text='Dev editor: register a DB_Dev.ini voice slot instead (VOCALOID4FE)',
                        variable=self.v['pkg_devini']).grid(row=3, column=1, sticky='w', padx=6, pady=3)
        b2 = ttk.Frame(g)
        b2.grid(row=4, column=1, sticky='w', pady=(6, 2), padx=6)
        ttk.Button(b2, text='Install', style='Accent.TButton', command=self.install).pack(side='left')
        ttk.Button(b2, text='Uninstall', command=self.uninstall).pack(side='left', padx=6)
        self._hint(g, 5, 'Install/Uninstall touch Program Files and the registry, so Windows asks for '
                         'permission (UAC) first.')
        return f

    def _tab_v3v2(self, nb):
        """V3→V2 backport: convert VOCALOID3/4 banks (or a Pack-tab .tree) for VOCALOID2."""
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        self._intro(f, 'VOCALOID2 cannot read V3/V4 banks: the index (.ddi) is rewritten the way its engine '
                       'reads it. Converted singers live in Documents\\VOCALOID2 Singers.')
        g = self._group(f, 1, 'Convert')
        ttk.Label(g, text='Voicebank').grid(row=0, column=0, sticky='w', pady=3)
        self.v3v2_box = ttk.Combobox(g, textvariable=self.v['v3v2_src'], width=57, state='readonly')
        self.v3v2_box.grid(row=0, column=1, sticky='ew', padx=6, pady=3)
        self.v3v2_box.bind('<<ComboboxSelected>>', lambda _e: self._v3v2_picked_source())
        ttk.Button(g, text='Browse…', command=self._v3v2_browse).grid(row=0, column=2, pady=3)
        self._row(g, 1, 'Name in VOCALOID2', self.v['v3v2_name'])
        ttk.Button(g, text='Convert', style='Accent.TButton', command=self._v3v2_convert).grid(
            row=2, column=1, sticky='w', padx=6, pady=(6, 2))
        g = self._group(f, 2, 'Converted singers')
        g.grid_configure(sticky='nsew')
        g.rowconfigure(0, weight=1)
        g.columnconfigure(0, weight=1)
        g.columnconfigure(1, weight=0)
        self.v3v2_lib = tk.Listbox(g, height=6, exportselection=False, activestyle='none', relief='solid',
                                   borderwidth=1, highlightthickness=0, selectbackground=ACCENT,
                                   selectforeground='#ffffff')
        self.v3v2_lib.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        b = ttk.Frame(g)
        b.grid(row=0, column=1, sticky='n')
        ttk.Label(b, text='Language').pack(anchor='w')
        ttk.Combobox(b, textvariable=self.v['v3v2_lang'], values=('Auto', 'Japanese', 'English'),
                     width=20, state='readonly').pack(fill='x', pady=(0, 6))
        ttk.Button(b, text='Install in VOCALOID2', command=self._v3v2_install).pack(fill='x', pady=2)
        ttk.Button(b, text='Uninstall', command=self._v3v2_uninstall).pack(fill='x', pady=2)
        ttk.Button(b, text='Delete singer', command=self._v3v2_delete).pack(fill='x', pady=2)
        ttk.Button(b, text='Refresh', command=self._v3v2_refresh_singers).pack(fill='x', pady=2)
        self._v3v2_sources, self._v3v2_picked, self._v3v2_library = [], None, []
        self._v3v2_busy = False
        self._v3v2_refresh_sources()
        self._v3v2_refresh_singers()
        return f

    @staticmethod
    def _v3v2_api():
        """The vendored V3V2 engine (needs Windows for the registry parts)."""
        try:
            from v3v2 import convert, install, sources, tree
        except ImportError as ex:
            messagebox.showwarning('V3→V2', 'The backport engine needs Windows: %s' % ex)
            return None
        return convert, install, sources, tree

    def _v3v2_refresh_sources(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, _install, sources, _tree = api
        try:
            self._v3v2_sources = sources.installed()
        except OSError:
            self._v3v2_sources = []
        self.v3v2_box['values'] = (['%s  (installed)' % s.name for s in self._v3v2_sources]
                                   + [V3V2_BROWSE_FOLDER, V3V2_BROWSE_TREE, V3V2_PACK_DB])

    def _v3v2_refresh_singers(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, sources, _tree = api
        self._v3v2_library = sources.library()
        try:
            done = install.installed()
        except OSError:
            done = {}
        self._v3v2_installed = done
        self.v3v2_lib.delete(0, 'end')
        for name, folder, _info in self._v3v2_library:
            e = done.get(os.path.abspath(folder))
            self.v3v2_lib.insert('end', '%s   [installed, %s]' % (name, install.LANGUAGES[e['language']])
                                 if e else name)

    def _v3v2_take_source(self, src):
        api = self._v3v2_api()
        _convert, _install, sources, _tree = api
        self._v3v2_picked = src
        self.v['v3v2_name'].set(sources.safe_name(src.name))

    def _v3v2_picked_source(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, _install, sources, _tree = api
        i = self.v3v2_box.current()
        if i < 0:
            return
        if i < len(self._v3v2_sources):
            self._v3v2_take_source(self._v3v2_sources[i])
        elif self.v['v3v2_src'].get() == V3V2_BROWSE_TREE:
            path = filedialog.askopenfilename(title='Developer-kit singer', filetypes=[('Singer tree', '*.tree')])
            if path:
                self._v3v2_classify(path)
        elif self.v['v3v2_src'].get() == V3V2_PACK_DB:
            db = self.v['pkg_db'].get().rstrip('\\/')
            tree_path = db + '.tree'
            if not db or not os.path.isfile(tree_path):
                messagebox.showinfo('V3→V2', 'The Pack tab has no built DB yet — build one first.')
                return
            self._v3v2_classify(tree_path)
        else:
            path = filedialog.askdirectory(title='Voicebank folder (holds a .ddi and a .ddb)')
            if path:
                self._v3v2_classify(path)

    def _v3v2_classify(self, path):
        from v3v2 import sources
        try:
            src = sources.classify(path)
        except ValueError as ex:
            messagebox.showwarning('Cannot use this', str(ex))
            return
        self.v['v3v2_src'].set('%s  (%s)' % (src.name, src.folder))
        self._v3v2_take_source(src)

    def _v3v2_browse(self):
        path = filedialog.askdirectory(title='Voicebank folder (holds a .ddi and a .ddb)')
        if path:
            self._v3v2_classify(path)

    def _v3v2_convert(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, sources, _tree = api
        if self._v3v2_busy or self.runner.busy:
            messagebox.showinfo('Busy', 'Wait for the current step to finish.')
            return
        src = self._v3v2_picked
        if src is None:
            messagebox.showinfo('V3→V2', 'Pick a voicebank first.')
            return
        name = sources.safe_name(self.v['v3v2_name'].get() or src.name)
        out = os.path.join(sources.LIBRARY, name)
        if os.path.exists(out):
            if os.path.abspath(out) in getattr(self, '_v3v2_installed', {}):
                messagebox.showwarning('V3→V2', '%s is installed in VOCALOID2. Uninstall it first, '
                                                'or pick another name.' % name)
                return
            if not messagebox.askyesno('V3→V2', '%s already exists. Replace it?' % name):
                return
        self._v3v2_busy = True
        self.set_busy('Converting voicebank')
        self.log('\n$ v3v2 convert %s -> %s\n' % (src.name, out), 'cmd')
        threading.Thread(target=self._v3v2_job, args=(api, src, out, name), daemon=True).start()

    def _v3v2_job(self, api, src, out, name):
        convert, _install, sources, tree = api
        vlog = lambda msg, tag=None: self.after(0, self.log, msg + '\n', tag)  # noqa: E731
        try:
            os.makedirs(out, exist_ok=True)
            for fn in os.listdir(out):
                if fn.lower().endswith(('.ddi', '.ddb', '.vvd')):
                    os.remove(os.path.join(out, fn))
            if src.kind == 'tree':
                root = tree.pack(src.folder, os.path.join(out, name + '.ddb'), vlog)
                data, rep = convert.root_to_v2(root)
                with open(os.path.join(out, name + '.ddi'), 'wb') as fh:
                    fh.write(data)
                for line in rep.lines():
                    vlog(line)
            else:
                rep = convert.convert_bank(src.folder, out, name, vlog)
            sources.write_info(out, name, src, rep.lines())
            vlog('%s is ready. Install it in VOCALOID2 below.' % name, 'ok')
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            vlog('Failed: %s: %s' % (type(ex).__name__, ex), 'err')
        self.after(0, self._v3v2_done)

    def _v3v2_done(self):
        self._v3v2_busy = False
        self.set_busy(None)
        self._v3v2_refresh_singers()

    def _v3v2_selected(self):
        sel = self.v3v2_lib.curselection()
        if not sel or sel[0] >= len(self._v3v2_library):
            messagebox.showinfo('V3→V2', 'Select a converted singer first.')
            return None
        return self._v3v2_library[sel[0]]

    def _v3v2_language(self, folder):
        from v3v2 import install
        choice = self.v['v3v2_lang'].get()
        if choice == 'Japanese':
            return 0
        if choice == 'English':
            return 1
        ddis = [f for f in os.listdir(folder) if f.lower().endswith('.ddi')]
        try:
            return install.detect_language(os.path.join(folder, ddis[0]))
        except Exception:                                          # noqa: BLE001 - default Japanese
            return 0

    def _v3v2_install(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, _sources, _tree = api
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) in getattr(self, '_v3v2_installed', {}):
            messagebox.showinfo('V3→V2', '%s is already installed.' % name)
            return
        language = self._v3v2_language(folder)
        self.log('Installing %s as a %s singer (Windows will ask for administrator rights)...'
                 % (name, install.LANGUAGES[language]))
        self.update()
        try:
            install.install(folder, name, language)
        except install.InstallError as ex:
            self.log('Install failed: %s' % ex, 'err')
            messagebox.showwarning('Install', str(ex))
            return
        self.log('%s is installed. Open VOCALOID2 and pick "%s" as the singer.' % (name, name), 'ok')
        self._v3v2_refresh_singers()

    def _v3v2_uninstall(self):
        api = self._v3v2_api()
        if api is None:
            return
        _convert, install, _sources, _tree = api
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) not in getattr(self, '_v3v2_installed', {}):
            messagebox.showinfo('V3→V2', '%s is not installed.' % name)
            return
        self.update()
        try:
            install.uninstall(folder)
        except install.InstallError as ex:
            self.log('Uninstall failed: %s' % ex, 'err')
            messagebox.showwarning('Uninstall', str(ex))
            return
        self.log('%s is removed from VOCALOID2 (the converted files are still here).' % name, 'ok')
        self._v3v2_refresh_singers()

    def _v3v2_delete(self):
        bank = self._v3v2_selected()
        if not bank:
            return
        name, folder, _info = bank
        if os.path.abspath(folder) in getattr(self, '_v3v2_installed', {}):
            messagebox.showwarning('V3→V2', '%s is installed in VOCALOID2. Uninstall it first.' % name)
            return
        if not messagebox.askyesno('V3→V2', 'Delete the converted singer %s?\n(The original voicebank is '
                                             'not touched.)' % name):
            return
        for fn in os.listdir(folder):
            os.remove(os.path.join(folder, fn))
        os.rmdir(folder)
        self.log('Deleted %s.\n' % name)
        self._v3v2_refresh_singers()

    # ---------------------------------------------------------------- helpers
    def log(self, s, tag=None):
        if tag is None and re.search(r'WARNING|warning:', s):
            tag = 'warn'
        self.text.insert('end', s, tag)
        self.text.see('end')

    def clear_log(self):
        self.text.delete('1.0', 'end')

    def set_busy(self, title):
        if title:
            self._busy = (title, time.monotonic())
            self.prog.configure(mode='indeterminate')
            self.prog.start(12)
            self.stop_btn.configure(state='normal')
            self._tick()
        else:
            took = time.monotonic() - self._busy[1] if getattr(self, '_busy', None) else 0
            self._busy = None
            self.status.set('Ready' + ('  (last step took %s)' % self._clock(took) if took >= 1 else ''))
            self.prog.stop()
            self.prog.configure(mode='determinate', value=0)
            self.stop_btn.configure(state='disabled')

    @staticmethod
    def _clock(sec):
        return '%d:%02d' % divmod(int(sec), 60)

    def _tick(self):
        """Status line with the running step's elapsed time, updated every second."""
        if not getattr(self, '_busy', None):
            return
        title, t0 = self._busy
        self.status.set('%s…  %s' % (title, self._clock(time.monotonic() - t0)))
        if getattr(self, '_tick_id', None):
            self.after_cancel(self._tick_id)
        self._tick_id = self.after(1000, self._tick)

    @staticmethod
    def open_file(path):
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showinfo('Not found', '%s does not exist yet.' % (path or 'That'))

    def _py(self, script, *args):
        return [sys.executable, '-I', '-u', script] + [str(a) for a in args]

    # ------------------------------------------------------------------ pick
    def pick_bank(self):
        d = filedialog.askdirectory(title='UTAU voicebank folder', initialdir=self.v['bank'].get() or None)
        if d:
            self.v['bank'].set(os.path.normpath(d))
            name = os.path.basename(os.path.normpath(d))
            safe = re.sub(r'[^\w\- ]+', '_', name).strip() or 'bank'
            self.v['out'].set(os.path.join(DEVKIT, safe + '_seg'))
            ascii_name = re.sub(r'[^A-Za-z0-9]+', '', name)[:20] or 'Singer'
            self.v['db_name'].set(ascii_name)
            self.v['db_path'].set(os.path.join(DEVKIT, ascii_name, ascii_name))
            self.scan()

    def pick_out(self):
        d = filedialog.askdirectory(title='Output folder for the DBTool files')
        if d:
            self.v['out'].set(os.path.normpath(d))

    def pick_db(self):
        d = filedialog.askdirectory(title='Folder to create the singer DB in')
        if d:
            name = self.v['db_name'].get() or 'Singer'
            self.v['db_path'].set(os.path.join(os.path.normpath(d), name, name))

    def pick_dict(self):
        p = filedialog.askopenfilename(title='VOCALOID phonetic dictionary (.txt from the devkit)',
                                       filetypes=[('Phonetic dictionary', '*.txt'), ('All files', '*.*')])
        if p:
            self.v['dict'].set(os.path.normpath(p))

    def _find_dict(self, lang):
        """Fill in the devkit dictionary for lang unless the user chose one for that language."""
        from u2v import dbtool_io
        cur = self.v['dict'].get()
        if cur and os.path.exists(cur) and dbtool_io.dictionary_language(cur) in (lang, None):
            return
        roots = [DEVKIT, os.path.dirname(self.v['dbtool'].get()), os.path.dirname(DEVKIT)]
        found = dbtool_io.find_dictionary(lang, roots)
        self.v['dict'].set(found or '')

    def pick_dbtool(self):
        p = filedialog.askopenfilename(title='VocaloidDBTool3.exe', filetypes=[('DBTool', '*.exe')])
        if p:
            self.v['dbtool'].set(os.path.normpath(p))

    def scan(self):
        """List the bank's folders/suffixes and guess its language (reads oto.ini files only)."""
        bank = self.v['bank'].get()
        self.glist.delete(0, 'end')
        if not os.path.isdir(bank):
            return
        self.info.set('Reading oto.ini files…')
        self.update_idletasks()
        try:
            from u2v import oto, phonemes as P
            entries = oto.read_bank(bank)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            self.info.set('Could not read the bank: %s' % ex)
            return
        if not entries:
            self.info.set('No oto.ini entries found in this folder.')
            return
        count = {}
        for e in entries:
            count[e.group] = count.get(e.group, 0) + 1
        self.groups = sorted(count.items())
        for g, n in self.groups:
            self.glist.insert('end', '%s   (%d)' % (g or '(main folder)', n))
        self.detected_lang = P.detect_language([e.alias for e in entries])
        lang = {'ja': 'Japanese', 'en': 'English'}.get(self.detected_lang, self.detected_lang)
        n_v = len(P.v_aliases([e.alias for e in entries])) if self.detected_lang == 'ja' else 0
        self.info.set('%d oto entries in %d folder/suffix group(s), looks %s%s' % (
            len(entries), len(count), lang, ', %d ヴ recordings' % n_v if n_v else ''))
        if self.detected_lang in ('ja', 'en'):
            self._find_dict(self.detected_lang)
        if self.detected_lang == 'en':
            self.v['db_lang'].set('English')
        elif self.detected_lang == 'ja':
            self.v['db_lang'].set('Japanese')
        try:
            from u2v import select as _Sel, phonemes as _P
            _rows, _entries = _Sel.bank_colours(bank)
            _lang = 'en' if self.detected_lang == 'en' else 'ja'
            _vv = {}
            for _e in _entries:
                _ph = _P.extra_alias(_e.alias) or _P.alias_to_phonemes(_e.alias, _lang)
                if _ph:
                    for _p in _ph:
                        if _p in _P.VOWELS:
                            _vv.setdefault(_e.color, set()).add(_p)
            _bits = []
            for _c, _n, _pitches, _groups in _rows:
                _v = ''.join(sorted(_vv.get(_c, ()))) or 'no vowels!'
                _bits.append('%s (%d, vowels %s)' % (_c, _n, _v))
            _aff = _Sel.prefix_affixes(bank)
            self.color_rows = _rows
            self._build_evec_rows(_rows)
            self._summaries()
            self.evec_info.set('Colours: ' + '; '.join(_bits)
                               + (' | prefix.map: ' + ', '.join(_aff) if _aff else ''))
        except Exception:
            self.evec_info.set('')

    # ---------------------------------------------------------------- actions
    def _build_evec_rows(self, rows):
        """One label + number dropdown per colour. Preserves any previous picks."""
        if not hasattr(self, 'evec_box'):
            return
        for w in self.evec_box.winfo_children():
            w.destroy()
        prev = {}
        try:
            from u2v import select as _SelB
            if self.v['evec_map'].get().strip():
                prev = _SelB.parse_evec_map(self.v['evec_map'].get())
        except ValueError:
            prev = {}
        self.evec_pick, self.evec_pick_order = {}, []
        nums = ['main'] + ['#%d' % i for i in range(1, 10)] + ['skip']
        for i, (c, n, _p, _g) in enumerate(rows):
            if c in prev:
                v = prev[c]
            else:
                hit = [vv for k, vv in prev.items() if k.lower() in c.lower() or c.lower() in k.lower()]
                v = hit[0] if hit else ('' if i == 0 else '#%d' % min(i, 9))
            var = tk.StringVar(value='skip' if v is None else (v or 'main'))
            ttk.Label(self.evec_box, text='%s (%d)' % (c, n)).grid(row=i, column=0, sticky='w')
            ttk.OptionMenu(self.evec_box, var, var.get(), *nums,
                           command=self._sync_evec_map).grid(row=i, column=1, sticky='w', padx=6)
            self.evec_pick[c], self.evec_pick_order = var, self.evec_pick_order + [c]
        self._sync_evec_map()

    def _sync_evec_map(self, *_a):
        self.v['evec_map'].set(', '.join('%s=%s' % (c, self.evec_pick[c].get())
                                         for c in self.evec_pick_order))

    def _auto_evec(self):
        rows = getattr(self, 'color_rows', None)
        if not rows:
            from u2v import select as _SelA
            bank = self.v['bank'].get()
            if not os.path.isdir(bank):
                return messagebox.showwarning('EVEC', 'Choose a bank first.')
            try:
                rows, _e = _SelA.bank_colours(bank)
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                return messagebox.showwarning('EVEC', 'Could not read colours: %s' % ex)
            if rows:
                self.color_rows = rows
        if not rows:
            return messagebox.showinfo('EVEC', 'No colours detected.')
        self.v['evec_map'].set('')
        self._build_evec_rows(rows)
        if self.v['colors'].get() == 'main':
            self.v['colors'].set('evec')
        self.log('EVEC map: %s\n' % self.v['evec_map'].get())

    def _edit_custom(self):
        """Custom extras & transitions: tick aliases, name their phoneme (several may share one), pick its
        group, and see per phoneme which vowel transitions come from recordings and which get spliced."""
        bank = self.v['bank'].get()
        if not os.path.isdir(bank):
            return messagebox.showwarning('Custom extras', 'Choose a bank first.')
        from u2v import phonemes as _P, select as _S
        lang = 'en' if (self.v['lang'].get() == 'en' or self.detected_lang == 'en') else 'ja'
        try:
            rows = _S.alias_rows(bank, lang)
            cur = _S.parse_custom_extras(self.v['custom_extras'].get())
        except ValueError as ex:
            return messagebox.showwarning('Custom extras', 'The custom extras field: %s' % ex)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Custom extras', 'Could not read the bank: %s' % ex)
        known = {r['key'] for r in rows}
        for alias in cur:                                          # keep entries this bank doesn't have
            if alias not in known:
                rows.append({'key': alias, 'count': 0, 'groups': set(), 'maps_to': 'not in this bank',
                             'kind': 'unknown', 'lead': _P.lead_vowel(alias) if lang == 'ja' else None})
        groups = ['Breaths'] + list(_S.PHONETIC_GROUPS)
        sung = set().union(_P.VOWELS, _P.UNV_PLOSIVE, _P.UNV_FRIC, _P.UNV_AFFR, _P.V_PLOSIVE, _P.V_FRIC,
                           _P.NASAL, _P.LIQUID, _P.SEMI, _P.SIL, _P.V_PHONEMES)
        vowels = ['a', 'i', 'M', 'e', 'o', 'N\\'] if lang == 'ja' else []

        # prefilled names: "a R", "i R" ... share one ("hh"), so ticking them all just works
        # (vowel endings "a R" / "a -" all suggest hh; names already in the field win)
        by_rest, taken = {'R': 'hh', '-': 'hh'}, {ph for ph, _v, _g in cur.values()} | sung | {'hh'}
        for alias, (ph, _v, _g) in cur.items():
            if lang == 'ja' and _P.lead_vowel(alias):
                by_rest[alias.split(None, 1)[1].strip()] = ph

        def suggest(r):
            if r['lead']:
                rest = r['key'].split(None, 1)[1].strip()
                if rest not in by_rest:
                    by_rest[rest] = _S.suggest_extra_phoneme(rest, taken)
                    taken.add(by_rest[rest])
                return by_rest[rest]
            ph = _S.suggest_extra_phoneme(r['key'], taken)
            taken.add(ph)
            return ph

        items = []
        for r in rows:
            ph0, v0, g0 = cur.get(r['key'], (None, None, None))
            if ph0 is None:
                ph0 = suggest(r)
                breathy = ph0 == 'hh' or re.search(r'息|吸|breath|br', r['key'], re.I)
                g0 = 'Breaths' if breathy else 'Whisper'
            items.append(dict(r, inc=tk.BooleanVar(value=r['key'] in cur), ph=tk.StringVar(value=ph0),
                              grp=tk.StringVar(value=g0 if g0 in groups else
                                               ('Vowels' if v0 else 'Breaths'))))
        ends = {ph: tk.BooleanVar(value=ph in cur.ends) for ph in {ph for ph, _v, _g in cur.values()}}
        for it in items:
            it['trans'] = tk.StringVar()
            show = lambda *_a, x=it: x['trans'].set(  # noqa: E731
                '%s → %s' % (x['lead'], x['ph'].get().strip() or '?') if x['lead'] else 'on its own')
            it['ph'].trace_add('write', show)
            show()

        top = tk.Toplevel(self)
        top.title('Custom extras & transitions')
        top.transient(self)
        top.grab_set()
        top.geometry('980x720')
        top.minsize(760, 520)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(3, weight=1)
        ttk.Label(top, padding=(12, 10, 12, 0), style='Info.TLabel', wraplength=940, justify='left',
                  text='Tick the sounds to port and name their phoneme. An alias that starts with a vowel '
                       '(a R, a laugh) keeps the transition it was recorded with; give several the same name '
                       '(a R, i R … → hh) and each of those vowels gets its own recorded way in. Below: '
                       'which transitions come from recordings and which get spliced.').grid(
            row=0, column=0, sticky='ew')
        bar = ttk.Frame(top, padding=(12, 8, 12, 0))
        bar.grid(row=1, column=0, sticky='ew')
        ttk.Label(bar, text='Search').pack(side='left')
        q = tk.StringVar()
        search = ttk.Entry(bar, textvariable=q, width=28)
        search.pack(side='left', padx=6)
        show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='Show every alias (also the ones that already sing)',
                        variable=show_all).pack(side='left', padx=(12, 0))
        count = ttk.Label(bar, style='Hint.TLabel')
        count.pack(side='right')
        sf = ScrollFrame(top)
        sf.grid(row=3, column=0, sticky='nsew', padx=12, pady=(6, 0))
        body = sf.body
        SHOW = 200                                    # widgets per view; search always covers every alias

        cov_box = ttk.LabelFrame(top, labelwidget=ttk.Label(top, text=' Transitions ', style='Sec.TLabel'),
                                 padding=(10, 4))
        cov_box.grid(row=4, column=0, sticky='ew', padx=12, pady=(8, 0))
        cov_sf = ScrollFrame(cov_box)
        cov_sf.configure(height=150)
        cov_sf.pack(fill='both', expand=True)
        cov_sf.pack_propagate(False)
        cov = cov_sf.body
        pending = []

        def coverage(*_a):
            if pending:
                return
            pending.append(top.after_idle(_coverage))

        def _coverage():
            pending.clear()
            for w in cov.winfo_children():
                w.destroy()
            chosen = {}
            for it in items:
                ph = it['ph'].get().strip()
                if it['inc'].get() and ph:
                    chosen.setdefault(ph, []).append(it)
            if not chosen:
                ttk.Label(cov, text='Nothing ticked yet.', style='Hint.TLabel').grid(row=0, column=0, sticky='w')
                return
            r = 0
            for ph in sorted(chosen):
                its = chosen[ph]
                if ph not in ends:                    # endings ("a R" only) default to phrase end
                    ends[ph] = tk.BooleanVar(value=all(it['lead'] for it in its))
                ttk.Label(cov, text=ph, style='Sec.TLabel').grid(row=r, column=0, sticky='nw', padx=(0, 10))
                ttk.Label(cov, style='Hint.TLabel', wraplength=760, justify='left',
                          text='%s · from %s' % (its[0]['grp'].get(), ', '.join(
                              '%s ×%d' % (it['key'], it['count']) for it in its))).grid(
                    row=r, column=1, columnspan=8, sticky='w')
                r += 1
                rec = {}
                for it in its:
                    if it['lead']:
                        rec[it['lead']] = rec.get(it['lead'], 0) + it['count']
                line = ttk.Frame(cov)
                line.grid(row=r, column=1, columnspan=8, sticky='w')
                for v in vowels:
                    n = rec.get(v, 0)
                    ttk.Label(line, text='%s→%s %s' % (v, ph, '✔ %d' % n if n else '~ splice'),
                              style='Rec.TLabel' if n else 'Splice.TLabel').pack(side='left', padx=(0, 12))
                alone = sum(it['count'] for it in its if not it['lead'])
                ttk.Label(line, text='Sil→%s %s' % (ph, '✔ %d' % alone if alone else '—'),
                          style='Rec.TLabel' if alone else 'Hint.TLabel').pack(side='left', padx=(0, 12))
                ttk.Label(line, text='%s→Sil ✔' % ph, style='Rec.TLabel').pack(side='left', padx=(0, 12))
                r += 1
                into = ttk.Frame(cov)
                into.grid(row=r, column=1, columnspan=8, sticky='w', pady=(0, 6))
                lead_in = tk.BooleanVar(value=not ends[ph].get())
                lead_in.trace_add('write', lambda *_a, p=ph, b=lead_in: ends[p].set(not b.get()))
                ttk.Checkbutton(into, variable=lead_in,
                                text='%s leads into singing (%s→a, %s→i …, spliced). Off = it only ends a phrase, '
                                     'like a breath out.' % (ph, ph, ph)).pack(side='left')
                r += 1

        def sync_group(it):
            ph = it['ph'].get().strip()
            for other in items:
                if other is not it and other['ph'].get().strip() == ph:
                    other['grp'].set(it['grp'].get())
            coverage()

        def render(vis):
            for w in body.winfo_children():
                w.destroy()
            for col, txt in enumerate(('', 'Alias', 'Now', 'Phoneme', 'Group', 'Transition')):
                ttk.Label(body, text=txt, style='Sec.TLabel').grid(row=0, column=col, sticky='w', padx=2,
                                                                   pady=(0, 2))
            for i, it in enumerate(vis[:SHOW], start=1):
                ttk.Checkbutton(body, variable=it['inc'], width=2).grid(row=i, column=0, sticky='w', padx=2)
                ttk.Label(body, text='%s  ×%d' % (it['key'], it['count']) if it['count'] else it['key'],
                          width=24).grid(row=i, column=1, sticky='w', padx=2)
                ttk.Label(body, text=it['maps_to'], width=22, style='Hint.TLabel').grid(row=i, column=2,
                                                                                        sticky='w', padx=2)
                ttk.Entry(body, textvariable=it['ph'], width=10).grid(row=i, column=3, sticky='w', padx=2)
                cb = ttk.Combobox(body, textvariable=it['grp'], values=groups, width=17, state='readonly')
                cb.grid(row=i, column=4, sticky='w', padx=2)
                cb.bind('<<ComboboxSelected>>', lambda _e, x=it: sync_group(x))
                ttk.Label(body, textvariable=it['trans'], width=16, style='Info.TLabel').grid(row=i, column=5, sticky='w', padx=2)
            sf.canvas.yview_moveto(0)

        def refilter(*_a):
            needle = q.get().strip().lower()
            vis = [it for it in items if (show_all.get() or it['kind'] != 'sung' or it['inc'].get())
                   and (not needle or needle in it['key'].lower() or needle in it['maps_to'].lower())]
            vis.sort(key=lambda it: not it['inc'].get())             # ticked ones first
            render(vis)
            n_sung = sum(1 for it in items if it['kind'] == 'sung')
            count.configure(text='showing %d of %d%s' % (
                min(len(vis), SHOW), len(vis),
                '' if show_all.get() else ' (+%d that already sing)' % n_sung if n_sung else ''))

        q.trace_add('write', refilter)
        show_all.trace_add('write', refilter)
        for it in items:
            it['inc'].trace_add('write', coverage)
            it['ph'].trace_add('write', coverage)
        refilter()
        _coverage()
        search.focus_set()

        def wheel_off():
            try:
                top.unbind_all('<MouseWheel>')
            except tk.TclError:
                pass

        def close():
            wheel_off()
            top.destroy()

        def ok():
            parts, seen = [], {}
            for it in items:
                if not it['inc'].get():
                    continue
                ph, alias = it['ph'].get().strip(), it['key']
                if any(sep in alias for sep in ',;:=\n'):
                    return messagebox.showwarning('Custom extras', '%r has , ; : or = in it, so it cannot be '
                                                                   'written as a custom extra.' % alias, parent=top)
                if not ph:
                    return messagebox.showwarning('Custom extras', 'Give %r a phoneme name.' % alias, parent=top)
                if ph in sung:
                    return messagebox.showwarning('Custom extras', '%r is already a singing phoneme: pick another '
                                                                   'name for %r.' % (ph, alias), parent=top)
                if seen.setdefault(ph, it['grp'].get()) != it['grp'].get():
                    return messagebox.showwarning('Custom extras', '%r has two groups (%s, %s): pick one.'
                                                  % (ph, seen[ph], it['grp'].get()), parent=top)
                end = ends.get(ph)
                parts.append('%s=%s:%s%s' % (alias, ph, it['grp'].get(), ':end' if end and end.get() else ''))
            spec = '; '.join(parts)
            try:
                _S.parse_custom_extras(spec)
            except ValueError as ex:
                return messagebox.showwarning('Custom extras', str(ex), parent=top)
            self.v['custom_extras'].set(spec)
            self.log('Custom extras: %s\n' % (spec or 'none'))
            close()

        btns = ttk.Frame(top, padding=(12, 10))
        btns.grid(row=5, column=0, sticky='ew')
        ttk.Button(btns, text='OK', style='Accent.TButton', command=ok).pack(side='left')
        ttk.Button(btns, text='Cancel', command=close).pack(side='left', padx=6)
        ttk.Button(btns, text='Untick all', command=lambda: [it['inc'].set(False) for it in items]).pack(
            side='right')
        top.protocol('WM_DELETE_WINDOW', close)

    def _v_mode(self):
        return (self.v['v_mode'].get().split() or ['auto'])[0]

    def _convert_args(self):
        v = self.v
        _colors = v['colors'].get()
        if _colors == 'main' and re.search(r'#\d', v['evec_map'].get()):
            _colors = 'evec'
        if v['evec_vowels'].get():
            _colors = 'evec'
        args = ['convert', v['bank'].get(), v['out'].get(), '--lang', v['lang'].get(), '--colors', _colors,
                '--keep', v['keep'].get()]
        if v['dict'].get():
            if not os.path.exists(v['dict'].get()):
                raise ValueError('The phonetic dictionary file does not exist:\n%s' % v['dict'].get())
            args += ['--dict', v['dict'].get()]
        sel = [self.groups[i][0] for i in self.glist.curselection()]
        if sel and len(sel) < len(self.groups):
            args += ['--groups', '^(%s)$' % '|'.join(re.escape(g) for g in sel)]
        for k, flag in (('splice', '--no-splice'), ('fill', '--no-fill'), ('normalize', '--no-normalize'),
                        ('derive', '--no-derive'), ('extend_dict', '--no-extend-dict')):
            if not v[k].get():
                args.append(flag)
        if v['merge_groups'].get():
            args.append('--merge-groups')
        _em = v['evec_map'].get().strip()
        _ep = v['evec_phonemes'].get().strip()
        if _em or _ep:
            from u2v import select as _Sel2
            try:
                if _em:
                    _Sel2.parse_evec_map(_em)
                if _ep:
                    _Sel2.parse_evec_phonemes(_ep)
            except ValueError as _ex:
                raise ValueError('EVEC: %s' % _ex)
        if not v['extras'].get():
            args += ['--extras', 'none']
        if _em:
            args += ['--evec-map', _em]
        if _ep:
            args += ['--evec-phonemes', _ep]
        _cx = v['custom_extras'].get().strip()
        if _cx:
            try:
                from u2v import select as _SelC2
                _SelC2.parse_custom_extras(_cx)
            except ValueError as _ex:
                raise ValueError('Custom extras: %s' % _ex)
            args += ['--custom-extra', _cx]
        if v['evec_vowels'].get():
            args += ['--evec-mode', 'vowels']
        if self._v_mode() != 'auto':
            args += ['--v-mode', self._v_mode()]
        for b in re.split(r'[,;\s]+', v['brighten'].get().strip()):
            if b:
                if not re.fullmatch(r"[^=]+=-?\d+(\.\d+)?", b):
                    raise ValueError('Brighten: "%s" should look like o=3' % b)
                args += ['--brighten', b]
        for u in v['avoid'].get().split(';'):
            if u.strip():
                if len(u.split()) != 2:
                    raise ValueError('Avoid units: "%s" should be two phonemes, e.g. g\' i' % u.strip())
                args += ['--avoid', ' '.join(u.split())]
        return args

    def _convert_warnings(self):
        sel = [self.groups[i][0] for i in self.glist.curselection()]
        names = sel or [g for g, _n in self.groups]
        em = self.v['evec_map'].get().strip()
        if em and self.v['colors'].get() == 'evec':
            try:
                from u2v import select as _SelW
                want = _SelW.parse_evec_map(em)
            except ValueError:
                return True
            rows = getattr(self, 'color_rows', []) or []
            colours = [c for c, _n, _p, _g in rows]
            bad = [w for w in want
                   if not any(w.lower() in n.lower() or n.lower() in w.lower() for n in names)
                   and not any(w.lower() in c.lower() or c.lower() in w.lower() for c in colours)]
            if bad:
                return messagebox.askokcancel(
                    'EVEC map',
                    'These EVEC names match nothing selected: %s.\n\n'
                    'Selected folders: %s.\n'
                    'That colour will convert with an automatic number instead. Proceed anyway?'
                    % (', '.join(bad), ', '.join(names[:6]) + (' ...' if len(names) > 6 else '')))
        if not self.v['splice'].get() and any('単独音' in n for n in names):
            return messagebox.askokcancel(
                'CV legato is off',
                '“Fake missing transitions” is off but single-note folders are selected.\n'
                'CV parts will not sing legato. Proceed anyway?')
        return True

    def convert(self, then=None):
        if not os.path.isdir(self.v['bank'].get()):
            return messagebox.showwarning('Convert', 'Choose an UTAU voicebank folder first.')
        if not self.v['out'].get():
            return messagebox.showwarning('Convert', 'Choose an output folder.')
        if not self.v['dict'].get():
            return messagebox.showwarning(
                'Convert', 'No phonetic dictionary found.\n\nIt comes with the VOCALOID3 devkit: '
                '"Japanese Dictionary\\Japanese_Dictionary.txt" (English banks: "English Dictionary\\'
                'english_phonetic_dictionary_20061220.txt").\n\nPress Browse… next to "Phonetic dictionary" and pick it.')
        try:
            args = self._convert_args()
        except ValueError as ex:
            return messagebox.showwarning('Convert', str(ex))
        if not self._convert_warnings():
            return
        self._save()
        self.runner.start(self._py(CLI, *args), 'Converting',
                          done=lambda code: then() if (then and code == 0) else None)

    def validate(self):
        out = self.v['out'].get()
        if not os.path.isdir(out):
            return messagebox.showwarning('Validate', 'Convert the bank first.')
        lang = 'en' if (self.v['lang'].get() == 'en' or self.detected_lang == 'en') else 'ja'
        self.runner.start(self._py(CLI, 'validate', out, '--lang', lang), 'Validating')

    def build(self):
        out, db = self.v['out'].get(), self.v['db_path'].get()
        if not os.path.exists(os.path.join(out, 'dictionary.txt')):
            return messagebox.showwarning('Build', 'The output folder has no dictionary.txt, so it was not converted '
                                          '(or the conversion could not find the phonetic dictionary).\n\n'
                                          'Set "Phonetic dictionary" at the top and press Convert again.')
        if not db:
            return messagebox.showwarning('Build', 'Enter where to create the DB (folder\\Name).')
        if os.path.exists(db + '.tree'):
            return messagebox.showwarning('Build', '%s already exists. Pick a new name or folder.' % db)
        if not os.path.exists(self.v['dbtool'].get()):
            return messagebox.showwarning('Build', 'VocaloidDBTool3.exe not found. Set its path.')
        self._save()
        args = ['build', out, db, '--name', self.v['db_name'].get() or 'Singer',
                '--language', self.v['db_lang'].get(), '--dbtool', self.v['dbtool'].get()]
        if not self.v['dbtool_hidden'].get():
            args.append('--show-dbtool')
        self.runner.start(self._py(CLI, *args), 'Building the singer DB', done=self._built)

    def _built(self, code):
        """A built DB is what the Pack tab packs next: point it there."""
        if code != 0:
            return
        db = self.v['db_path'].get()
        self.v['pkg_db'].set(db)
        if not self.v['pkg_name'].get():
            self.v['pkg_name'].set(self.v['db_name'].get() or os.path.basename(db))
        if not self.v['pkg_out'].get():
            self.v['pkg_out'].set(os.path.join(os.path.dirname(db), 'package'))
        self.log('Next: ③ Pack & Install packs %s into a voicebank.\n' % db, 'ok')

    # ------------------------------------------------------------ pack & install
    def pick_built(self):
        d = filedialog.askdirectory(title='Folder holding the built singer DB (<Name>.tree + <Name>/voice/)')
        if not d:
            return
        d = os.path.normpath(d)
        trees = sorted(f for f in os.listdir(d) if f.endswith('.tree'))
        if len(trees) == 1:
            self.v['pkg_db'].set(os.path.join(d, trees[0][:-5]))
        elif not trees:
            parent_tree = os.path.join(os.path.dirname(d), os.path.basename(d) + '.tree')
            if os.path.exists(parent_tree):
                self.v['pkg_db'].set(parent_tree[:-5])  # pointed at <Name>/ itself
            else:
                self.v['pkg_db'].set(d)
                messagebox.showinfo('Built DB', 'No .tree in %s: pick the folder holding <Name>.tree.' % d)
        else:
            self.v['pkg_db'].set(os.path.join(d, trees[0][:-5]))
        db = self.v['pkg_db'].get()
        if not self.v['pkg_name'].get():
            self.v['pkg_name'].set(os.path.basename(db.rstrip('\\/')) or 'Singer')
        if not self.v['pkg_out'].get():
            self.v['pkg_out'].set(os.path.join(os.path.dirname(db.rstrip('\\/')), 'package'))

    def gen_compid(self):
        """CompID Studio: pick a language, take a pool ID or roll a random one, check it, use it."""
        from u2v import compid as _C
        top = tk.Toplevel(self)
        top.title('CompID Studio — your bank\'s ID card')
        top.transient(self)
        top.grab_set()
        top.minsize(520, 460)
        ttk.Label(top, text=_C.WHAT_IS_IT, wraplength=500, padding=(10, 8, 10, 0),
                  style='Info.TLabel', justify='left').pack(anchor='w')
        row = ttk.Frame(top, padding=(10, 8, 10, 0))
        row.pack(fill='x')
        ttk.Label(row, text='Bank language').pack(side='left')
        langs = ('Japanese', 'Chinese', 'English', 'Korean', 'Spanish', 'Other')
        cur_lang = (self.v['db_lang'].get() or 'Japanese').capitalize()
        lang = tk.StringVar(value=cur_lang if cur_lang in langs else 'Japanese')
        ttk.Combobox(row, textvariable=lang, values=langs, width=12,
                     state='readonly').pack(side='left', padx=6)
        cand = tk.StringVar()
        show = ttk.Label(top, textvariable=cand, font=('Consolas', 16, 'bold'), padding=(10, 6, 10, 0))
        show.pack(anchor='w')
        status = tk.StringVar()
        status_lb = ttk.Label(top, textvariable=status, wraplength=500, padding=(10, 0, 10, 0))
        status_lb.pack(anchor='w')
        info = tk.StringVar()
        ttk.Label(top, textvariable=info, wraplength=500, padding=(10, 0, 10, 0),
                  style='Info.TLabel').pack(anchor='w')

        def _show(cid):
            cand.set((cid or '').strip().upper())
            ok, msg = _C.status_of(cid)
            status.set(msg)
            try:
                status_lb.configure(foreground='#107010' if ok else '#c00000')
            except Exception:                                      # noqa: BLE001 - cosmetic only
                pass
            parts = _C.describe_compid(cand.get())
            if parts:
                info.set('raw %s — vendor %s, %s, product %s, version %s' % (
                    parts['raw'], parts['vendor'], parts['lang_name'],
                    parts['product'], parts['version']))
            elif cand.get():
                info.set('decodes to no keygen raw string — normal for real bank IDs, '
                         'the editor never checks the checksum.')
            else:
                info.set('')

        def _pool():
            try:
                _show(_C.pick_from_pool(lang.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)
                try:
                    status_lb.configure(foreground='#c00000')
                except Exception:                                  # noqa: BLE001 - cosmetic only
                    pass

        def _random():
            try:
                _show(_C.random_compid())
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)

        def _craft():
            try:
                _show(_C.generate(lang.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)

        def _encode_raw():
            try:
                _show(_C.encode_raw(raw.get()))
            except Exception as ex:                                # noqa: BLE001 - show, don't crash
                status.set('%s' % ex)
                try:
                    status_lb.configure(foreground='#c00000')
                except Exception:                                  # noqa: BLE001 - cosmetic only
                    pass

        btns = ttk.Frame(top, padding=(10, 6, 10, 0))
        btns.pack(fill='x')
        ttk.Button(btns, text='Pick from pool', command=_pool).pack(side='left')
        ttk.Button(btns, text='Craft for language', command=_craft).pack(side='left', padx=6)
        ttk.Button(btns, text='Random', command=_random).pack(side='left')
        ttk.Label(top, text='Pool: known-valid V4 Japanese/Chinese/English IDs. Craft: fresh ID with '
                            'the right language digit (the Korean/Spanish move — no pools exist).',
                  wraplength=500, padding=(10, 2, 10, 0), style='Hint.TLabel',
                  justify='left').pack(anchor='w')
        crow = ttk.Frame(top, padding=(10, 6, 10, 0))
        crow.pack(fill='x')
        ttk.Label(crow, text='Or paste one:').pack(side='left')
        custom = tk.StringVar()
        ttk.Entry(crow, textvariable=custom, width=20, font=('Consolas', 11)).pack(side='left', padx=6)
        ttk.Button(crow, text='Check', command=lambda: _show(custom.get())).pack(side='left')
        rrow = ttk.Frame(top, padding=(10, 6, 10, 0))
        rrow.pack(fill='x')
        ttk.Label(rrow, text='Or raw (14 chars):').pack(side='left')
        raw = tk.StringVar()
        ttk.Entry(rrow, textvariable=raw, width=20, font=('Consolas', 11)).pack(side='left', padx=6)
        ttk.Button(rrow, text='Encode', command=_encode_raw).pack(side='left')
        end = ttk.Frame(top, padding=(10, 10))
        end.pack(fill='x')

        def _use():
            if not cand.get():
                return messagebox.showwarning('CompID', 'Pick or generate an ID first.')
            ok, msg = _C.status_of(cand.get())
            if not ok and not messagebox.askokcancel('CompID', '%s.\n\nUse it anyway?' % msg):
                return
            self.v['pkg_compid'].set(cand.get().strip().upper())
            self.log('CompID: %s\n' % cand.get().strip().upper())
            top.destroy()

        ttk.Button(end, text='Use this CompID', command=_use).pack(side='left')
        ttk.Button(end, text='Cancel', command=top.destroy).pack(side='left', padx=6)
        _pool() if _C.POOLS.get(lang.get()) else _craft()

    def pick_pkg_out(self):
        d = filedialog.askdirectory(title='Folder for the voicebank package')
        if d:
            self.v['pkg_out'].set(os.path.normpath(d))

    def package_dir(self):
        out, cid = self.v['pkg_out'].get(), self.v['pkg_compid'].get().strip().upper()
        return os.path.join(out, cid) if out and cid else (out or '')

    def _editors(self):
        try:
            from u2v import pack as _pack
            eds = _pack.find_editors()
            self._editor_list = eds
            return ['%s  (%s)' % (e['label'], e['root']) for e in eds] or ['(no editor detected — Browse…)']
        except Exception:                                          # noqa: BLE001 - editor not installed
            self._editor_list = []
            return ['(no editor detected — Browse…)']

    def _editor_pick(self):
        eds = getattr(self, '_editor_list', [])
        txt = self.v['pkg_editor'].get()
        for e in eds:
            if txt.startswith(e['label']):
                return e
        if os.path.isdir(txt):
            try:
                from u2v import pack as _pack
                return _pack.guess_editor(txt)
            except Exception:                                      # noqa: BLE001 - warned below
                pass
        return None

    def pick_editor(self):
        d = filedialog.askdirectory(title='Point at the VOCALOID editor install folder')
        if not d:
            return
        try:
            from u2v import pack as _pack
            e = _pack.guess_editor(os.path.normpath(d))
            self._editor_list = getattr(self, '_editor_list', []) + [e]
            self.editor_box['values'] = self._editors_refresh(e)
            self.v['pkg_editor'].set('%s  (%s)' % (e['label'], e['root']))
            self._fill_pkg_dest(e)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            messagebox.showwarning('Editor', '%s' % ex)

    def _editors_refresh(self, extra):
        eds = getattr(self, '_editor_list', [])
        if all(x['root'] != extra['root'] for x in eds):
            eds.append(extra)
        self._editor_list = eds
        return ['%s  (%s)' % (e['label'], e['root']) for e in eds]

    def _fill_pkg_dest(self, editor):
        if self.v['pkg_dest'].get():
            return
        try:
            from u2v import pack as _pack
            name = self.v['pkg_name'].get() or 'Singer'
            self.v['pkg_dest'].set(_pack.default_bank_dir(editor, name))
        except Exception:                                          # noqa: BLE001 - leave it empty
            pass

    def pick_pkg_dest(self):
        d = filedialog.askdirectory(title='Bank folder for the .ddb/.ddi/.vvd')
        if d:
            self.v['pkg_dest'].set(os.path.normpath(d))

    def pack(self):
        db = self.v['pkg_db'].get()
        if not db:
            db = self.v['db_path'].get()
            self.v['pkg_db'].set(db)
        if not db or not os.path.exists(db + '.tree'):
            return messagebox.showwarning('Pack', 'Build the singer DB first (tab 2), then point at it.')
        args = [db]
        if self.v['pkg_out'].get():
            args += ['--out', self.v['pkg_out'].get()]
        if self.v['pkg_name'].get():
            args += ['--name', self.v['pkg_name'].get()]
        if self.v['pkg_compid'].get().strip():
            args += ['--compid', self.v['pkg_compid'].get().strip().upper()]
        self._save()

        def done(code):
            if code == 0:
                pkg = self.package_dir()
                if self.v['pkg_compid'].get().strip():
                    self.log('package: %s\n' % pkg)
                else:
                    try:
                        import glob as _glob
                        subs = sorted(_glob.glob(os.path.join(self.v['pkg_out'].get(), 'B*')))
                        if subs:
                            self.v['pkg_compid'].set(os.path.basename(subs[-1]))
                            self.log('package: %s\n' % self.package_dir())
                    except Exception:                              # noqa: BLE001 - log only
                        pass
        self.runner.start(self._py(CLI, 'pack', *args), 'Packing the voicebank', done=done)

    @staticmethod
    def _run_as_admin(bat):
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', bat, None,
                                                 os.path.dirname(bat), 1)
        if rc <= 32:
            raise OSError('Windows refused to launch %s (code %s)' % (bat, rc))
        return rc

    def _stage_install(self):
        """Write install.bat/uninstall.bat (+ dev slot) without admin rights. Returns the .bat to elevate."""
        from u2v import pack as _pack
        pkg = self.package_dir()
        if not pkg or not os.path.isdir(pkg):
            raise ValueError('Pack the voicebank first: no package at %s.' % (pkg or 'that folder'))
        editor = self._editor_pick()
        if editor is None:
            raise ValueError('Choose the editor to install into (or Browse… to point at it).')
        if self.v['pkg_devini'].get():
            if editor['kind'] != 'V4FE-dev':
                raise ValueError('A DB_Dev.ini slot is for the VOCALOID4FE developer editor.')
            db = self.v['pkg_db'].get() or self.v['db_path'].get()
            return _pack.write_dev_slot(editor['root'], db, self.v['pkg_name'].get() or None,
                                        log=self.log)
        _pack.write_install_scripts(pkg, editor,
                                    dest=self.v['pkg_dest'].get() or None,
                                    compid=self.v['pkg_compid'].get().strip().upper() or None,
                                    name=self.v['pkg_name'].get() or None, log=self.log)
        return os.path.join(pkg, 'install.bat')

    def install(self):
        try:
            bat = self._stage_install()
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Install', '%s' % ex)
        self._save()
        self.log('staged: %s\nrequesting administrator rights…\n' % bat)
        try:
            self._run_as_admin(bat)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Install', '%s' % ex)
        self.log('launched %s: press Yes on the Windows prompt, then watch its window.\n' % bat)

    def uninstall(self):
        pkg = self.package_dir()
        bat = os.path.join(pkg, 'uninstall.bat') if pkg else ''
        if not bat or not os.path.exists(bat):
            return messagebox.showwarning('Uninstall', 'No uninstall.bat in %s: pack + stage the install first.'
                                          % (pkg or 'that folder'))
        try:
            self._run_as_admin(bat)
        except Exception as ex:                                    # noqa: BLE001 - show, don't crash
            return messagebox.showwarning('Uninstall', '%s' % ex)
        self.log('launched %s: press Yes on the Windows prompt.\n' % bat)


def main():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)         # sharp text on scaled displays
        except Exception:                                          # noqa: BLE001
            pass
    App().mainloop()


if __name__ == '__main__':
    main()
