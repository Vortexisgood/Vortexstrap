@echo off
:: Yönetici haklarını kontrol et
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :run
) else (
    echo Yonetici izinleri isteniyor...
    powershell -Command "Start-Process -FilePath '%0' -Verb RunAs"
    exit /b
)

:run
:: Scriptin bulundugu klasore git
cd /d "%~dp0"
echo Vortex Launcher Baslatiliyor...
python launcher\main.py
