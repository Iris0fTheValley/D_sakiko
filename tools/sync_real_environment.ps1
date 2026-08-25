[CmdletBinding()]
param(
    [string]$TargetRoot = 'J:\AI friend\sairi\DSakiko3.10',
    [switch]$WhatIf,
    [switch]$InstallRuntimeCompatibilityDependencies,
    [switch]$InstallElectronDependencies,
    [switch]$BuildElectron
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$targetPath = [System.IO.Path]::GetFullPath($TargetRoot)

if ([string]::Equals($repoRoot, $targetPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'TargetRoot must be a separate runtime directory, not the source repository.'
}

if (-not (Test-Path -LiteralPath $targetPath -PathType Container)) {
    throw "Target environment does not exist: $targetPath"
}

function Sync-Directory {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string[]]$ExcludedDirectories = @()
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Source directory does not exist: $Source"
    }

    if ($WhatIf) {
        Write-Host "[WhatIf] $Source -> $Destination"
        return
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $robocopyArgs = @(
        $Source,
        $Destination,
        '/E',
        '/R:1',
        '/W:1',
        '/XJ',
        '/NFL',
        '/NDL',
        '/NP'
    )

    foreach ($excludedDirectory in $ExcludedDirectories) {
        $robocopyArgs += @('/XD', (Join-Path $Source $excludedDirectory))
    }

    & robocopy @robocopyArgs | Out-Host
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed ($LASTEXITCODE): $Source -> $Destination"
    }
}

function Sync-File {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Source file does not exist: $Source"
    }

    if ($WhatIf) {
        Write-Host "[WhatIf] $Source -> $Destination"
        return
    }

    $destinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

Write-Host "Source:  $repoRoot"
Write-Host "Target:  $targetPath"
Write-Host "Baseline: current branch (already based on latest upstream/master)"

$startupConfigName = (-join @([char]0x542F, [char]0x52A8, [char]0x53C2, [char]0x6570, [char]0x914D, [char]0x7F6E)) + '.bat'

$runtimePython = Join-Path $targetPath 'runtime\python.exe'
$runtimePythonVersion = $null
if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
    $runtimePythonVersion = (& $runtimePython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    Write-Host "Runtime:  Python $runtimePythonVersion"
    if ([version]$runtimePythonVersion -lt [version]'3.11') {
        Write-Warning 'The current upstream project officially requires Python 3.11; the packaged runtime is older and uses compatibility paths.'
    }
}

# This is intentionally additive. It updates source files from the current branch
# without deleting packaged models, runtime libraries, user data, or local settings.
Sync-File `
    -Source (Join-Path $repoRoot 'run.bat') `
    -Destination (Join-Path $targetPath 'run.bat')

Sync-File `
    -Source (Join-Path $repoRoot 'startup_config.bat') `
    -Destination (Join-Path $targetPath 'startup_config.bat')

Sync-File `
    -Source (Join-Path $repoRoot 'startup_config.bat') `
    -Destination (Join-Path $targetPath $startupConfigName)

Sync-File `
    -Source (Join-Path $repoRoot 'tools\launch_runtime.py') `
    -Destination (Join-Path $targetPath 'tools\launch_runtime.py')

Sync-Directory `
    -Source (Join-Path $repoRoot 'GPT_SoVITS') `
    -Destination (Join-Path $targetPath 'GPT_SoVITS')

Sync-Directory `
    -Source (Join-Path $repoRoot 'bridge') `
    -Destination (Join-Path $targetPath 'bridge')

Sync-Directory `
    -Source (Join-Path $repoRoot 'electron_frontend') `
    -Destination (Join-Path $targetPath 'electron_frontend') `
    -ExcludedDirectories @('node_modules', 'dist', 'out')

if (-not $WhatIf) {
    $checks = @(
        'run.bat',
        'startup_config.bat',
        'tools\launch_runtime.py',
        'GPT_SoVITS\main2.py',
        'GPT_SoVITS\live2d_controller.py',
        'GPT_SoVITS\qconfig.py',
        'GPT_SoVITS\ui\components\custom_setting_area.py',
        'bridge\protocol.py',
        'bridge\saki_bridge.py',
        'electron_frontend\package.json',
        'electron_frontend\src\renderer\renderer-controller\Live2DRendererController.ts'
    )

    Write-Host ''
    Write-Host 'Verification:'
    foreach ($relativePath in $checks) {
        $sourceFile = Join-Path $repoRoot $relativePath
        $targetFile = Join-Path $targetPath $relativePath
        if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf)) {
            throw "Sync verification failed; target file is missing: $targetFile"
        }

        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceFile).Hash
        $targetHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $targetFile).Hash
        $status = if ($sourceHash -eq $targetHash) { 'OK' } else { 'MISMATCH' }
        Write-Host ("{0}: {1}" -f $status, $relativePath)
        if ($status -eq 'MISMATCH') {
            throw "Sync verification failed; hash mismatch: $relativePath"
        }
    }
}

if ($InstallRuntimeCompatibilityDependencies) {
    if (-not $runtimePythonVersion) {
        throw "Bundled runtime Python was not found: $runtimePython"
    }

    if ($WhatIf) {
        Write-Host "[WhatIf] install runtime compatibility dependencies with $runtimePython"
    } else {
        $colorVersion = if ([version]$runtimePythonVersion -lt [version]'3.10') { 'coloraide==8.6' } else { 'coloraide>=8.11.1,<9' }
        & $runtimePython -m pip install $colorVersion
        if ($LASTEXITCODE -ne 0) {
            throw "Runtime dependency installation failed ($LASTEXITCODE): $colorVersion"
        }
    }
}

$electronRoot = Join-Path $targetPath 'electron_frontend'
if ($InstallElectronDependencies) {
    if ($WhatIf) {
        Write-Host "[WhatIf] npm ci in $electronRoot"
    } else {
        Push-Location $electronRoot
        try {
            npm ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci failed ($LASTEXITCODE)"
            }
        } finally {
            Pop-Location
        }
    }
}

if ($BuildElectron) {
    if ($WhatIf) {
        Write-Host "[WhatIf] npm run build in $electronRoot"
    } else {
        if (-not (Test-Path -LiteralPath (Join-Path $electronRoot 'node_modules') -PathType Container)) {
            throw 'Electron dependencies are missing; run with -InstallElectronDependencies first.'
        }

        Push-Location $electronRoot
        try {
            npm run build
            if ($LASTEXITCODE -ne 0) {
                throw "npm run build failed ($LASTEXITCODE)"
            }
        } finally {
            Pop-Location
        }
    }
}

Write-Host ''
Write-Host 'Real environment sync completed.'
