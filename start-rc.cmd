@echo off
cd /d "%~dp0"
:loop
claude remote-control --name "Razer Blade-16"
timeout /t 30 /nobreak >nul
goto loop
