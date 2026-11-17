@echo off
rem place2mc: double-click this file to start the app.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo then double-click this file again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Checking the two things place2mc needs (numpy and pillow)...
%PY% -m pip install --user --quiet --disable-pip-version-check numpy pillow
%PY% place2mc_app.py
pause
