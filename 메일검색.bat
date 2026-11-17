@echo off
rem Mail backup search launcher (ASCII only on purpose: works with any console code page)
cd /d "%~dp0"
title Mail Backup Search
set "PY="
rem 1) bundled Python in .\python (no install needed)
if exist "%~dp0python\python.exe" set PY="%~dp0python\python.exe"
rem 2) installed Python
if not defined PY (py -3 -c "import sys" >nul 2>nul && set "PY=py -3")
if not defined PY (python -c "import sys" >nul 2>nul && set "PY=python")
if not defined PY goto nopython
%PY% -I "%~dp0mail_search.py"
if errorlevel 1 pause
exit /b 0

:nopython
echo Required files not found. Re-extract the zip and keep the python folder.
powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand CgBBAGQAZAAtAFQAeQBwAGUAIAAtAEEAcwBzAGUAbQBiAGwAeQBOAGEAbQBlACAAUwB5AHMAdABlAG0ALgBXAGkAbgBkAG8AdwBzAC4ARgBvAHIAbQBzAAoAJABtACAAPQAgACIABNVcuPitqLcgAOTCidXQxSAARNWUxlzVIAAM03zHRMcgAD7MRMcgABjCIADGxbXCyLLksi4AYABuAGAAbgB0xyAA9NNUsyAASMVYxyAAcAB5AHQAaABvAG4AIAD001SzAKwgAMDJzMZMyHCsmLAgAC7GqKzEySAAg6wgABmstcLIsuSyLgBgAG4AMLzs0yAAegBpAHAAIABExyAA5LLcwiAAgNS0xRzBIAD001Sz+MkgAKzAqcZ01SAA/Mg4wZTGLgAiAAoAWwB2AG8AaQBkAF0AWwBTAHkAcwB0AGUAbQAuAFcAaQBuAGQAbwB3AHMALgBGAG8AcgBtAHMALgBNAGUAcwBzAGEAZwBlAEIAbwB4AF0AOgA6AFMAaABvAHcAKAAkAG0ALAAgACcAVLp8xyAAMbzFxSAAgKzJwDCuJwAsACAAJwBPAEsAJwAsACAAJwBXAGEAcgBuAGkAbgBnACcAKQAKAA==
exit /b 1
