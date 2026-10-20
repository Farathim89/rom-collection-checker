@echo off
rem Builds dist\ROMCollectionChecker.exe (one file, no console). Needs a Python with requirements + pyinstaller
rem (the .venv; ROMCHECK_PY overrides it).
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
rem quoted: a path with & in it (AudioBooks & EBook Sorter) would end the line
if defined ROMCHECK_PY set "PY=%ROMCHECK_PY%"
"%PY%" -m pip install -q pyinstaller || goto :error
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name ROMCollectionChecker ^
    --icon assets\icon.ico --add-data "assets\icon.png;assets" --add-data "assets\flags;assets\flags" --add-data "assets\consoles;assets\consoles" ^
    --exclude-module tkinter --exclude-module pytest ^
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtPdf ^
    checker.pyw || goto :error
if not exist dist\RomChecker-data mkdir dist\RomChecker-data
echo.
echo Built: "%~dp0dist\ROMCollectionChecker.exe"
exit /b 0
:error
echo Build failed.
exit /b 1
