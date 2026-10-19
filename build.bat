@echo off
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%~dp0build" mkdir "%~dp0build"
"%PY%" "%~dp0build_version_info.py" "%~dp0build\version_info.txt" || exit /b 1

"%PY%" -m PyInstaller --name "AD-Multitool" --windowed --noconfirm ^
    --version-file "%~dp0build\version_info.txt" "%~dp0main.py" || exit /b 1

rem Optional code signing. Set SIGN_CERT_THUMBPRINT to the SHA1 thumbprint of a
rem code-signing certificate in the current user's store to sign the exe.
if not defined SIGN_CERT_THUMBPRINT (
    echo SIGN_CERT_THUMBPRINT not set, exe left unsigned.
    exit /b 0
)
if not defined SIGN_TIMESTAMP_URL set "SIGN_TIMESTAMP_URL=http://timestamp.digicert.com"
signtool sign /sha1 %SIGN_CERT_THUMBPRINT% /fd sha256 /tr "%SIGN_TIMESTAMP_URL%" /td sha256 ^
    "%~dp0dist\AD-Multitool\AD-Multitool.exe" || exit /b 1
signtool verify /pa "%~dp0dist\AD-Multitool\AD-Multitool.exe" || exit /b 1
