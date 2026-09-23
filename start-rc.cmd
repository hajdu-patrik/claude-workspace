@echo off
cd /d "%~dp0"
:loop
claude remote-control --name "Otthoni gep"
timeout /t 30 /nobreak >nul
goto loop
