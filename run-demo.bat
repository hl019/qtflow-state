@echo off
REM qtflow-state demo runner
REM ASCII-only to avoid CMD codepage issues on Windows.

cd /d "%~dp0"

echo ============================================
echo    qtflow-state - Demo Runner
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Please install Python 3.10 or newer.
    pause
    exit /b 1
)

echo [1/2] Parsing sample emails...
python project\parser.py
if errorlevel 1 goto fail

echo.
echo [2/2] Running delivery self-check...
python scripts\verify_delivery.py
if errorlevel 1 goto fail

echo.
echo ============================================
echo    All checks passed.
echo    Output: project\data\parsed_states.json
echo ============================================
pause
exit /b 0

:fail
echo.
echo [FAILED] See errors above.
pause
exit /b 1
