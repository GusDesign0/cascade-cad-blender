@echo off
setlocal
set "CASCADE_BLENDER="
for /d %%D in ("%ProgramFiles%\Blender Foundation\Blender *") do if exist "%%~D\blender.exe" set "CASCADE_BLENDER=%%~D\blender.exe"
if not defined CASCADE_BLENDER (
  echo Blender nao encontrado em Program Files. Abra Abrir_Demo.py pelo Blender.
  pause
  exit /b 1
)
start "Cascade CAD" "%CASCADE_BLENDER%" --python "%~dp0Abrir_Demo.py"

