param([string]$RuntimePath = (Join-Path $env:LOCALAPPDATA 'CascadeCAD\runtime'))
$ErrorActionPreference = 'Stop'
$runtimeTarget = [IO.Path]::GetFullPath($RuntimePath)
$pythonExe = Join-Path $runtimeTarget 'python.exe'
New-Item -ItemType Directory -Force -Path $runtimeTarget | Out-Null
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'Baixando Python 3.12.10 isolado para o motor CAD...'
    $archivePath = Join-Path $runtimeTarget 'python-3.12.10-embed-amd64.zip'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeTarget -Force
    Remove-Item -LiteralPath $archivePath
}
@('python312.zip', '.', 'Lib\site-packages', 'import site') | Set-Content -LiteralPath (Join-Path $runtimeTarget 'python312._pth') -Encoding ascii
& $pythonExe -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    $bootstrapPath = Join-Path $runtimeTarget 'get-pip.py'
    Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $bootstrapPath
    & $pythonExe $bootstrapPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar pip' }
}
Write-Host 'Instalando CadQuery e Open CASCADE. O primeiro download pode demorar.'
$requirementsPath = Join-Path $PSScriptRoot 'runtime-requirements.txt'
if (Test-Path -LiteralPath $requirementsPath) {
    & $pythonExe -m pip install --disable-pip-version-check --no-warn-script-location -r $requirementsPath
} else {
    & $pythonExe -m pip install --disable-pip-version-check --no-warn-script-location 'cadquery==2.6.1' 'cadquery-ocp==7.8.1.1.post1'
}
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar o motor CAD' }
& $pythonExe -c "import cadquery, OCP; print('CAD OK:', cadquery.__version__, OCP.__version__)"
if ($LASTEXITCODE -ne 0) { throw 'O teste de importacao do motor falhou' }
Write-Host "Motor instalado: $pythonExe"
Write-Host 'O plugin detecta automaticamente este caminho padrao. Reinicie o Blender se necessario.'

