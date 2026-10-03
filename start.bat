@echo off
rem Start Video Saver on Windows: double-click this file.
rem The first start creates a private Python environment in .venv and asks
rem for a password. Stop the app by closing this window (or Ctrl+C).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First start: creating a Python environment in .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Video Saver needs Python 3.11 or newer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

".venv\Scripts\python.exe" main.py
pause
