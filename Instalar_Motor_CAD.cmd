@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Instalar_Motor_CAD.ps1"
if errorlevel 1 (
  echo A instalacao falhou. Confira a mensagem acima.
) else (
  echo Motor pronto. Instale a extensao no Blender ou abra Abrir_Demo.cmd.
)
pause

