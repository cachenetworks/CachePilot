@echo off
REM ============================================================
REM  CachePilot setup script (Windows)
REM
REM  - Creates a virtual environment (.venv)
REM  - Installs Python dependencies from requirements.txt
REM  - Asks you which voice-model quality to download (low/medium/high)
REM  - Fetches Piper voice models into voices/
REM  - Pre-downloads the Whisper STT base model into models/whisper/
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

REM --- pick voice quality -------------------------------------------------
echo.
echo ============================================================
echo  VOICE QUALITY
echo ============================================================
echo.
echo  CachePilot ships 10 voices. Quality coverage on Piper is uneven --
echo  where the requested quality isn't available, the next-best is used.
echo.
echo    [1] LOW     ~20 MB / voice. Smallest. Fastest cold-load.
echo                LOW: alan, ryan, lessac, amy, kathleen, southern.
echo                MEDIUM fallback: joe, kusal, northern, jenny.
echo.
echo    [2] MEDIUM  ~63 MB / voice. RECOMMENDED. Balanced quality and
echo                size. Every voice has a MEDIUM variant.
echo.
echo    [3] HIGH    ~110 MB / voice. Best fidelity but only for ryan
echo                and lessac. Other 8 fall back to MEDIUM.
echo.
set "QUALITY=2"
set /p "QUALITY=Choice [1/2/3, default 2]: "
if "%QUALITY%"=="" set "QUALITY=2"

if "%QUALITY%"=="1" (
    set "Q_REQ=low"
) else if "%QUALITY%"=="3" (
    set "Q_REQ=high"
) else (
    set "Q_REQ=medium"
)
echo.
echo Selected: %Q_REQ%
echo.

REM --- download voices ----------------------------------------------------
REM
REM Each call args:
REM   %1 = local file name (alan.onnx)
REM   %2 = list of available qualities, comma-separated
REM   %3 = HF base path (without trailing slash)
REM   %4 = HF locale-slug (used to build the filename)
REM
REM HF URL layout:
REM   https://huggingface.co/rhasspy/piper-voices/resolve/main/<path>/<quality>/<locale-slug>-<quality>.onnx
REM
if not exist "voices" mkdir voices

call :get_voice alan      low,medium       en/en_GB/alan                    en_GB-alan
call :get_voice ryan      low,medium,high  en/en_US/ryan                    en_US-ryan
call :get_voice lessac    low,medium,high  en/en_US/lessac                  en_US-lessac
call :get_voice joe       medium           en/en_US/joe                     en_US-joe
call :get_voice amy       low,medium       en/en_US/amy                     en_US-amy
call :get_voice kathleen  low              en/en_US/kathleen                en_US-kathleen
call :get_voice kusal     medium           en/en_US/kusal                   en_US-kusal
call :get_voice northern  medium           en/en_GB/northern_english_male   en_GB-northern_english_male
call :get_voice southern  low              en/en_GB/southern_english_female en_GB-southern_english_female
call :get_voice jenny     medium           en/en_GB/jenny_dioco             en_GB-jenny_dioco

REM --- pre-download Whisper base model ------------------------------------
echo.
echo Pre-downloading Whisper STT base model (~74 MB, one-time)...
python -c "from faster_whisper import WhisperModel; import os; os.makedirs('models/whisper', exist_ok=True); WhisperModel('base', device='cpu', compute_type='int8', download_root='models/whisper'); print('Whisper base ready.')"
if errorlevel 1 (
    echo [WARN] Whisper model download failed. It will retry on first launch.
)

REM --- optional: install Kokoro support -----------------------------------
echo.
echo ============================================================
echo  OPTIONAL: Kokoro TTS (higher quality, ~340 MB extra)
echo ============================================================
echo.
echo  Kokoro is a more natural-sounding neural TTS but it is slower
echo  to load and synthesize than Piper. Recommended only if your
echo  PC handles Piper comfortably and you want better audio quality.
echo.
echo  Selecting Yes will:
echo    - install kokoro-onnx (~30 MB Python package)
echo    - download the Kokoro model (~310 MB) and voices (~27 MB)
echo.
set "WANT_KOKORO=N"
set /p "WANT_KOKORO=Install Kokoro support? [y/N]: "
if /i "%WANT_KOKORO%"=="y" (
    echo.
    echo Installing kokoro-onnx ...
    pip install kokoro-onnx
    if errorlevel 1 (
        echo [WARN] kokoro-onnx install failed; skipping model download.
    ) else (
        if not exist "models\kokoro" mkdir models\kokoro
        if not exist "models\kokoro\kokoro-v1.0.onnx" (
            echo Downloading Kokoro model ^(~310 MB^) ...
            curl -sL --fail -o "models\kokoro\kokoro-v1.0.onnx" ^
              "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
            if errorlevel 1 echo [WARN] Failed to download kokoro-v1.0.onnx
        )
        if not exist "models\kokoro\voices-v1.0.bin" (
            echo Downloading Kokoro voices ^(~27 MB^) ...
            curl -sL --fail -o "models\kokoro\voices-v1.0.bin" ^
              "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
            if errorlevel 1 echo [WARN] Failed to download voices-v1.0.bin
        )
        echo Kokoro support installed. Switch to it in the Settings tab.
    )
) else (
    echo Skipping Kokoro install.
)

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

REM ===================== helpers ==========================================

:get_voice
set NAME=%~1
set AVAIL=%~2
set HF_PATH=%~3
set HF_SLUG=%~4

if exist "voices\%NAME%.onnx" (
    echo Voice "%NAME%" already present, skipping.
    goto :eof
)

REM Decide which quality to fetch.
REM   1. If user-requested quality is in AVAIL, use it.
REM   2. Else fall back to "medium" if present.
REM   3. Else take the first available.
set CHOSEN=
echo ,%AVAIL%, | findstr /i /c:",%Q_REQ%," >nul && set CHOSEN=%Q_REQ%
if "!CHOSEN!"=="" echo ,%AVAIL%, | findstr /i /c:",medium," >nul && set CHOSEN=medium
if "!CHOSEN!"=="" (
    for /f "tokens=1 delims=," %%a in ("%AVAIL%") do set CHOSEN=%%a
)

set BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/%HF_PATH%/!CHOSEN!
set FILE=%HF_SLUG%-!CHOSEN!

echo.
echo Downloading %NAME% (!CHOSEN!) ...
curl -sL --fail -o "voices\%NAME%.onnx"      "%BASE%/%FILE%.onnx"
if errorlevel 1 (
    echo [WARN] Failed to download %NAME%.onnx
    goto :eof
)
curl -sL --fail -o "voices\%NAME%.onnx.json" "%BASE%/%FILE%.onnx.json"
if errorlevel 1 (
    echo [WARN] Failed to download %NAME%.onnx.json
    goto :eof
)
echo Done: %NAME% (!CHOSEN!)
goto :eof
