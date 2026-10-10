@echo off
rem  Trae lo nuevo de las nueve cuentas y abre la pagina con lo relacionado.
cd /d "%~dp0.."
echo Trayendo el correo de las nueve cuentas...
py -m correo
if errorlevel 1 goto problema
py -m correo informe
if errorlevel 1 goto problema
exit /b 0
:problema
echo.
echo Hubo un problema. La ventana queda abierta para poder leerlo.
pause
