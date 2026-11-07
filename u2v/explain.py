"""Plain words for a failed step: the Easy tab shows these instead of asking people to read the log.

explain(log_text) -> (what went wrong, what to do), from the first pattern that matches the log.
"""
import re

# (pattern, what went wrong, what to do); first match wins, so the specific ones go first
KNOWN = [
    (r'side-by-side',
     'The DBTool could not start.',
     'It needs the "Microsoft Visual C++ 2008 Redistributable", x86 version (also on 64-bit Windows). '
     'Install it, restart the PC and press Make my voicebank again.'),
    (r"No module named '?(pywinauto|win32\w*|pywintypes|numpy)",
     'A Python package Henkan needs is missing ({0}).',
     'Close Henkan and double-click "Henkan GUI.bat": it installs what is missing. Or open a command prompt '
     'in the Henkan folder and run:  pip install -r requirements.txt'),
    (r'no STA block|no stationaries',
     'The DBTool made a DB without stationaries (its vowel sounds).',
     'Press Make my voicebank again with "Show the DBTool while it works" ticked and watch the Automatic '
     'Segmentation window: the Stationaries column should fill in. If it stays empty, post the log on '
     'r/VocaloidPort so it can be fixed.'),
    (r'phonetic dictionary|no dictionary\.txt',
     'Henkan could not find the phonetic dictionary.',
     'It comes with the VOCALOID3 devkit (Japanese Dictionary\\Japanese_Dictionary.txt). Press "Find it…" '
     'next to Dictionary in step 2.'),
    (r'already exists',
     'A singer with that name was already built.',
     'Change the singer name in step 3 and try again.'),
    (r'cannot handle the DB name',
     'The DBTool cannot use that singer name.',
     'Use plain letters and digits only (A-Z, 0-9) in step 3.'),
    (r'VocaloidDBTool3\.exe not found|cannot find the DBTool|DBTool.*not found',
     'Henkan could not find VocaloidDBTool3.exe.',
     'Press "Find it…" next to DBTool in step 2 and pick VocaloidDBTool3.exe in your devkit folder.'),
    (r'Permission denied|PermissionError',
     'Windows would not let Henkan open a file.',
     'Close anything that has the voicebank open (the DBTool, an editor, a folder window), then try again. '
     'If it names a folder, move that folder out of the output folder.'),
    (r'lists no files',
     'The DBTool opened the wrong folder, so it had no recordings to build from.',
     'Press Make my voicebank again. If it happens again, post the log on r/VocaloidPort so it can be fixed.'),
    (r'timed out|got stuck|exited \(crashed\?\)',
     'The DBTool got stuck.',
     'Tick "Show the DBTool while it works" and press Make my voicebank again. Don\'t touch the mouse or '
     'keyboard while it clicks itself.'),
]

_ERRORISH = re.compile(r'(error|Error|failed|denied|cannot|could not)')


def explain(log_text):
    for pat, what, fix in KNOWN:
        m = re.search(pat, log_text or '')
        if m:
            return what.format(*m.groups()), fix
    lines = [ln.strip() for ln in (log_text or '').splitlines() if ln.strip()]
    bad = [ln for ln in lines if _ERRORISH.search(ln)]
    last = bad[-1] if bad else (lines[-1] if lines else '')
    fix = 'The details are in the log at the bottom of the window.'
    if last:
        fix = 'Henkan said:\n\n    %s\n\n%s' % (last, fix)
    return 'This step did not finish.', fix
