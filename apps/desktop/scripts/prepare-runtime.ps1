# Builds apps/desktop/runtime: bundled CPython, locked server dependencies,
# the zhiwo package, the Owner UI build, one embedding model and the icon.
# Nothing here reads a user memory library.
param(
    [string]$ModelSource = ""
)

$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $PSScriptRoot
$repo = Resolve-Path (Join-Path $desktop "..\..")
$server = Join-Path $repo "server"
$web = Join-Path $repo "apps\web"
$runtime = Join-Path $desktop "runtime"
$python = Join-Path $runtime "python"
$site = Join-Path $python "Lib\site-packages"
$version = "3.12.13"
if (-not $ModelSource) {
    $ModelSource = Join-Path $repo "experiments\kernel_spike\runs\p0_3\fastembed-cache\models--Qdrant--bge-small-zh-v1.5"
}

function Step($text) { Write-Host "== $text" }
function Check($label) { if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" } }

if (-not (Test-Path (Join-Path $ModelSource "snapshots"))) { throw "embedding model not found: $ModelSource" }

Step "clean runtime"
if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

Step "copy CPython $version"
$base = (& uv python find $version).Trim()
Check "uv python find"
$baseDir = Split-Path -Parent $base
& $base -c "import sys; assert sys.version.split()[0] == '$version', sys.version"
Check "python version"
Copy-Item $baseDir $python -Recurse
foreach ($drop in "include", "libs", "tcl", "Scripts", "Lib\test", "Lib\idlelib", "Lib\turtledemo", "Lib\tkinter", "Lib\ensurepip") {
    $target = Join-Path $python $drop
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
}
Remove-Item (Join-Path $python "Lib\EXTERNALLY-MANAGED") -ErrorAction SilentlyContinue

# CPython ships vcruntime140*.dll, but onnxruntime also imports the C++ runtime.
# A fresh Windows may not have the VC++ Redistributable, so ship it app-local.
foreach ($dll in "msvcp140.dll", "msvcp140_1.dll") {
    $source = Join-Path $env:SystemRoot "System32\$dll"
    if (-not (Test-Path $source)) { throw "$dll not found; install the x64 Visual C++ Redistributable on the build machine" }
    $version = [version](Get-Item $source).VersionInfo.FileVersion.Split(" ")[0]
    if ($version -lt [version]"14.40") { throw "$dll is $version; onnxruntime needs 14.40 or newer" }
    Copy-Item $source (Join-Path $python $dll)
    Write-Host "   $dll $version"
}
Get-ChildItem $site -Force | Remove-Item -Recurse -Force

Step "install locked server dependencies"
$requirements = Join-Path $runtime "requirements.txt"
Push-Location $server
try {
    & uv export --frozen --no-dev --no-emit-project --format requirements-txt -o $requirements --quiet
    Check "uv export"
} finally { Pop-Location }
& uv pip install --python (Join-Path $python "python.exe") --target $site --no-deps --require-hashes -r $requirements --link-mode copy --quiet
Check "uv pip install"
Remove-Item $requirements
# Console-script launchers point at the build machine's interpreter.
Remove-Item (Join-Path $site "bin") -Recurse -Force -ErrorAction SilentlyContinue

Step "add zhiwo package"
Copy-Item (Join-Path $server "zhiwo") (Join-Path $site "zhiwo") -Recurse
Get-ChildItem $site -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# The ._pth file pins sys.path to this directory and ignores PYTHONPATH. `import site`
# stays because pywin32 needs its .pth bootstrap; callers pass -I so the user's
# site-packages is not added.
Set-Content -Path (Join-Path $python "python312._pth") -Encoding ascii -Value @(
    "Lib",
    "DLLs",
    "Lib\site-packages",
    "import site"
)

Step "precompile"
& (Join-Path $python "python.exe") -X utf8 -m compileall -q -j 0 (Join-Path $python "Lib") | Out-Null
Check "compileall"

Step "smoke import"
$env:PYTHONPATH = $server
try {
    & (Join-Path $python "python.exe") -I -X utf8 -c "import os, sys, pywintypes, win32api, zhiwo.api.app, zhiwo.gateway.stdio_bridge, fastembed, mcp, uvicorn; root = os.path.dirname(sys.executable).lower(); outside = [p for p in sys.path if p and not p.lower().startswith(root)]; assert not outside, outside; assert zhiwo.api.app.__file__.lower().startswith(root); print(sys.version.split()[0], 'path ok')"
    Check "smoke import"
} finally { Remove-Item Env:PYTHONPATH }

Step "build Owner UI"
Push-Location $web
try {
    & npm run build --silent
    Check "web build"
} finally { Pop-Location }
Copy-Item (Join-Path $web "dist") (Join-Path $runtime "web") -Recurse

Step "copy embedding model"
New-Item -ItemType Directory -Force -Path (Join-Path $runtime "model") | Out-Null
Copy-Item $ModelSource (Join-Path $runtime "model\models--Qdrant--bge-small-zh-v1.5") -Recurse

Step "icon"
Copy-Item (Join-Path $repo "brand\OMNA_AppIcon_Light_1024.png") (Join-Path $runtime "icon.png")

$size = (Get-ChildItem $runtime -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("runtime ready: {0:N1} MB at {1}" -f $size, $runtime)
