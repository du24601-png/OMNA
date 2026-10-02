@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
echo OMNA x OpenCode test round. Uses temporary data, does not touch your library or OpenCode config.
server\.venv\Scripts\python.exe tests\agent_extract\run_opencode.py %*
echo.
echo Finished. Results are in tests\agent_extract\results_opencode
pause
