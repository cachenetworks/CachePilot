@echo off
REM ============================================================
REM  CachePilot setup script (Windows)
REM
REM  - Creates a virtual environment (.venv)
REM  - Installs Python dependencies from requirements.txt
REM  - Downloads all 10 Piper voice models into voices/
REM
REM  Run from an elevated Command Prompt for best results:
REM    right-click cmd -> "Run as administrator"
REM    cd into the project folder
REM    setup.bat
REM ============================================================

setlocal enabledelayedexpansion
echo.
echo ============================================================
echo  CachePilot setup
echo ============================================================

REM --- check Python -------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/ and
    echo make sure "Add Python to PATH" is ticked.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Using Python !PYVER!

REM --- create venv --------------------------------------------------------
if not exist ".venv" (
    echo.
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

echo.
echo Activating venv ...
call .venv\Scripts\activate.bat

REM --- install requirements -----------------------------------------------
echo.
echo Installing Python packages (this can take a minute) ...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. See output above.
    pause
    exit /b 1
)

REM --- download Piper voices ----------------------------------------------
if not exist "voices" mkdir voices

set BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main

call :get_voice alan       en/en_GB/alan/medium/en_GB-alan-medium
call :get_voice ryan       en/en_US/ryan/medium/en_US-ryan-medium
call :get_voice lessac     en/en_US/lessac/medium/en_US-lessac-medium
call :get_voice joe        en/en_US/joe/medium/en_US-joe-medium
call :get_voice amy        en/en_US/amy/medium/en_US-amy-medium
call :get_voice kathleen   en/en_US/kathleen/low/en_US-kathleen-low
call :get_voice kusal      en/en_US/kusal/medium/en_US-kusal-medium
call :get_voice northern   en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium
call :get_voice southern   en/en_GB/southern_english_female/low/en_GB-southern_english_female-low
call :get_voice jenny      en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  To launch CachePilot:
echo    1. Right-click Command Prompt -^> Run as administrator
echo    2. cd into this folder
echo    3. .venv\Scripts\activate
echo    4. python main.py
echo.
echo  Admin is required so synthesized keystrokes reach Star Citizen.
echo ============================================================
echo.
pause
exit /b 0

REM --- helper: download one voice ----------------------------------------
:get_voice
set NAME=%~1
set PATH_=%~2
if exist "voices\%NAME%.onnx" (
    echo Voice "%NAME%" already present, skipping.
    goto :eof
)
echo.
echo Downloading voice: %NAME% ...
curl -sL --fail -o "voices\%NAME%.onnx"      "%BASE%/%PATH_%.onnx"
if errorlevel 1 (
    echo [WARN] Failed to download %NAME%.onnx
    goto :eof
)
curl -sL --fail -o "voices\%NAME%.onnx.json" "%BASE%/%PATH_%.onnx.json"
if errorlevel 1 (
    echo [WARN] Failed to download %NAME%.onnx.json
    goto :eof
)
echo Done: %NAME%
goto :eof
