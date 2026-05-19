@echo off
setlocal

cd /d "%~dp0"

if not exist .venv (
    py -3.12 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-windows.txt
python -m PyInstaller --clean --noconfirm EMR_DHIS2_Tracker_Interoperability_App.spec

echo.
echo Build finished. Your Windows executable is:
echo dist\EMR_DHIS2_Tracker_Interoperability_App.exe
pause
