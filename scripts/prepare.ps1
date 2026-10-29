param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
$backend = Split-Path $PSScriptRoot -Parent
$environmentPython = Join-Path $backend '.venv\Scripts\python.exe'
Push-Location $backend
try {
    if (-not (Test-Path -LiteralPath $environmentPython -PathType Leaf)) {
        if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
            throw 'No se encontro Python. Instalalo o indica su ruta con -Python.'
        }
        $launcherArgs = @()
        if ((Split-Path -Leaf $Python) -in @('py', 'py.exe')) { $launcherArgs = @('-3') }
        & $Python @launcherArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
        if ($LASTEXITCODE -ne 0) { throw 'Se necesita Python 3.12 o superior para crear el entorno.' }
        & $Python @launcherArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno virtual.' }
    } else {
        Write-Host 'Se conserva el entorno virtual existente.'
    }
    & $environmentPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'El entorno virtual requiere Python 3.12 o superior.' }
    & $environmentPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
    & $environmentPython scripts\init_env.py
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar .env.' }
    Write-Host 'Siguiente paso: configura MySQL en .env, crea la base y ejecuta las migraciones; consulta README.md.'
} finally {
    Pop-Location
}
