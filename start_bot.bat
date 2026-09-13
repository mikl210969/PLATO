@echo off
title PLATO Watchdog
setlocal enabledelayedexpansion

set "PYTHON_EXE=C:\Users\ongul\AppData\Local\Programs\Python\Python311\python.exe"
set "SCRIPT_PATH=%~dp0main.py"
set "STATE_FILE=%~dp0restart_state.txt"
set "MAX_RESTARTS=2"
set "COOLDOWN=15"

echo.
echo ========================================
echo   PLATO Watchdog - Safe Auto-Restart
echo ========================================
echo.

:LOOP
    echo [%date% %time%] Starting platform...
    
    rem Запускаем платформу и ждем завершения
    "%PYTHON_EXE%" "%SCRIPT_PATH%"
    set "EXIT_CODE=%errorlevel%"
    
    echo.
    echo [%date% %time%] Platform exited with code: %EXIT_CODE%
    echo.
    
    if %EXIT_CODE% equ 0 (
        echo [INFO] Normal exit. Watchdog stopping.
        goto :END
    )
    
    if %EXIT_CODE% equ 42 (
        echo [INFO] Code 42: Blind mode detected. Safe restart in %COOLDOWN% seconds...
        timeout /t %COOLDOWN% /nobreak
        
        rem Простая проверка лимита перезапусков (без JSON)
        set /a RESTART_COUNT=0
        if exist "%STATE_FILE%" (
            set /p RESTART_COUNT=<"%STATE_FILE%"
        )
        
        set /a RESTART_COUNT+=1
        echo %RESTART_COUNT% > "%STATE_FILE%"
        
        if !RESTART_COUNT! geq %MAX_RESTARTS% (
            echo.
            echo ========================================
            echo [CRITICAL] Max restarts reached (!RESTART_COUNT!).
            echo [CRITICAL] Manual intervention required!
            echo ========================================
            echo.
            echo Press any key to exit...
            pause >nul
            goto :END
        )
        
        echo [INFO] Restart attempt !RESTART_COUNT! of %MAX_RESTARTS%
        echo.
        goto :LOOP
    )
    
    rem Любой другой код ошибки
    echo [ERROR] Unexpected error code %EXIT_CODE%. Watchdog stopping.
    echo [ERROR] Check platform logs.
    echo.
    pause
    goto :END

:END
echo Watchdog stopped.
pause