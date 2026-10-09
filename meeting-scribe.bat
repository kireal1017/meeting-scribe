@echo off
rem meeting-scribe launcher: double-click to install (first run only) and open the GUI.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"
"%UV%" --version >nul 2>nul
if errorlevel 1 (
    echo [meeting-scribe] uv 가 설치되어 있지 않습니다.
    echo PowerShell 에서 아래 명령으로 설치한 뒤 다시 실행해 주세요:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
)

if not exist ".venv\Scripts\scribe-gui.exe" (
    echo [meeting-scribe] 처음 실행: 필요한 패키지를 설치합니다. 약 2.5GB, 몇 분 걸립니다...
)
rem fast no-op when everything is already installed; picks up changes after a git pull
"%UV%" sync --quiet
if errorlevel 1 (
    echo [meeting-scribe] 설치에 실패했습니다. 위 메시지를 확인해 주세요.
    pause
    exit /b 1
)

start "" ".venv\Scripts\scribe-gui.exe" %*
exit /b 0
