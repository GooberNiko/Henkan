@echo off
rem Double-click to open the Henkan window.
setlocal
cd /d "%~dp0"

rem Python on PATH, else the py launcher (a python.org install without "Add to PATH").
set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if defined PYW set "PY=%PYW:pythonw.exe=python.exe%"
if not defined PYW (
    where pyw >nul 2>nul && set "PYW=pyw -3" && set "PY=py -3"
)
if not defined PYW goto nopython
rem the Microsoft Store's "python" stand-in is on PATH even with no Python installed
%PY% -c "import sys" >nul 2>nul || goto nopython

rem Henkan's packages; installed here on first run, so nobody has to type pip.
%PY% -c "import numpy, tkinter" >nul 2>nul && goto run
echo Henkan's Python packages are missing: installing them now (one time only)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto nopip

:run
start "" %PYW% gui.py
exit /b 0

:nopython
echo Henkan needs Python 3, and Windows can't find it.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH"
echo in the installer, then double-click "Henkan GUI.bat" again.
pause
exit /b 1

:nopip
echo.
echo Installing the packages failed (see above). Open a command prompt in this folder and run:
echo     %PY% -m pip install -r requirements.txt
pause
exit /b 1
