@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Criando ambiente Python...
    python -m venv .venv || goto :erro
    .venv\Scripts\python -m pip install -q -r requirements.txt || goto :erro
)
start "" .venv\Scripts\pythonw.exe -m multidesk %*
exit /b 0

:erro
echo Falha ao preparar o ambiente. Confira se o Python 3.10+ esta instalado.
pause
