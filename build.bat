@echo off
"%~dp0.venv\Scripts\python.exe" -m PyInstaller --name "AD-Multitool" --windowed --noconfirm "%~dp0main.py"
