@echo off
cd /d "%~dp0"
"runtime\python.exe" -B "qa\windows_rounds.py"
if errorlevel 1 (echo FAILED) else (echo PASSED)
pause
