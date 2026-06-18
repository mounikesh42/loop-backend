@echo off
REM Quick Excel export batch file for BaseStation
REM Usage: export.bat 19thmay

setlocal enabledelayedexpansion

if "%1"=="" (
    echo.
    echo ======================================================
    echo  BaseStation Results to Excel Exporter
    echo ======================================================
    echo.
    echo Usage: export.bat ^<name^>
    echo.
    echo Example:
    echo   export.bat 19thmay
    echo   - Creates outputs/19thmay.xlsx
    echo.
    echo   export.bat results_final
    echo   - Creates outputs/results_final.xlsx
    echo.
    exit /b 1
)

set OUTPUT_NAME=%1

pushd "%~dp0"

echo.
echo ======================================================
echo  Exporting to: %OUTPUT_NAME%.xlsx
echo ======================================================
echo.

REM Activate venv and get Python path
call .venv\Scripts\activate.bat

REM Use full path to Python from venv
".venv\Scripts\python.exe" scripts\export_to_excel.py paths.json %OUTPUT_NAME%

if %errorlevel% equ 0 (
    echo.
    echo ======================================================
    echo  Success! File created: outputs\%OUTPUT_NAME%.xlsx
    echo ======================================================
    echo.
    popd
) else (
    echo.
    echo ERROR: Export failed
    echo.
    popd
    exit /b 1
)
