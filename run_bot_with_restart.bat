@echo off
echo Starting PLATO with auto-restart on Exit Code 42...
:loop
echo.
echo ========================================
echo [%date% %time%] Starting bot...
echo ========================================
python main.py
set exit_code=%errorlevel%
if %exit_code%==42 (
    echo.
    echo ⚠️  Bot requested SAFE RESTART (Exit Code 42)
    echo Waiting 10 seconds before restart...
    timeout /t 10 /nobreak
    goto loop
)
echo.
echo ========================================
echo Bot exited with code %exit_code%
echo ========================================
pause