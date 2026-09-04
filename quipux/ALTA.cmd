@echo off
rem ------------------------------------------------------------
rem  Da de alta la credencial de CuencaDOC. Doble clic y listo.
rem
rem  Existe para no tener que acordarse de dos cosas que fallan siempre:
rem  situarse en la carpeta del proyecto, y usar el Python del entorno del
rem  proyecto en vez del de Windows —que no tiene instalado lo que hace falta—.
rem ------------------------------------------------------------
cd /d "%~dp0.."

set PY=%CD%\venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo.
"%PY%" -m quipux alta
echo.
pause
