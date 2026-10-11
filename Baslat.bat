@echo off
rem Aion2 DPS meter'i yonetici olarak baslatir (WinDivert icin gerekli).
rem Kayit almak icin:  Baslat.bat --dump kayit.bin
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)
py -3 main.py %*
if %errorlevel% neq 0 pause
