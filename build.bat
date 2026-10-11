@echo off
rem Hakari.exe'yi derler: dist\Hakari.exe
rem Gerekenler: py -3 -m pip install -r requirements.txt pyinstaller pillow
cd /d "%~dp0"
if not exist assets\hakari.ico py -3 tools\make_icon.py || exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --uac-admin ^
    --name Hakari --icon assets\hakari.ico --version-file tools\version_info.txt ^
    --exclude-module PIL --exclude-module unittest main.py
