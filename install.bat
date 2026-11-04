@echo off
setlocal DisableDelayedExpansion
set "UPOOL_INSTALL_BAT=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "try { $body = (Get-Content -LiteralPath $env:UPOOL_INSTALL_BAT -Raw) -split '(?m)^# POWERSHELL\r?$', 2; & ([scriptblock]::Create($body[1])) } catch { Write-Host $_ -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }"
exit /b %errorlevel%
# POWERSHELL
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$script:Root = Split-Path -Parent $env:UPOOL_INSTALL_BAT
$script:Downloads = Join-Path ([IO.Path]::GetTempPath()) ('upool-install-' + [guid]::NewGuid())

function Write-Step([string]$Message) {
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Invoke-Tool {
    param([string]$File, [string[]]$Arguments, [int[]]$SuccessCodes = @(0))
    # Native stderr is useful diagnostic output, not a PowerShell exception.
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($code -notin $SuccessCodes) {
        throw "$File failed (exit $code)."
    }
}

function Update-ProcessPath {
    # Retain custom session entries while picking up newly installed tools.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:Path
}

function Find-Python {
    $candidates = @()
    $existing = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($existing) { $candidates += $existing.Source }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidates += @(& $launcher.Source -0p 2>$null | ForEach-Object {
            if ("$_" -match '([A-Za-z]:\\.*python.exe)\s*$') { $Matches[1].Trim() }
        })
    }
    $candidates += @(Get-ChildItem "$env:LocalAppData\Programs\Python\Python*\python.exe",
        "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName)
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -like '*\Microsoft\WindowsApps\*') { continue }
        $result = & $candidate -c "import sys, struct; print(sys.executable) if sys.version_info >= (3,11) and struct.calcsize('P') == 8 else sys.exit(1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $result) { return "$result".Trim() }
    }
    return $null
}

function Find-Node {
    $candidates = @()
    $existing = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($existing) { $candidates += $existing.Source }
    $candidates += "$env:ProgramFiles\nodejs\node.exe", "$env:LocalAppData\Programs\nodejs\node.exe"
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (!(Test-Path -LiteralPath $candidate)) { continue }
        $result = & $candidate -p 'process.versions.node' 2>$null
        if ($LASTEXITCODE -eq 0 -and "$result" -match '^\d+\.\d+\.\d+$' -and
            [version]"$result" -ge [version]'20.9.0') { return $candidate }
    }
    return $null
}

function Save-Download([string]$Url, [string]$Name) {
    New-Item -ItemType Directory -Path $script:Downloads -Force | Out-Null
    $destination = Join-Path $script:Downloads $Name
    Write-Host "Downloading $Url"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $destination -TimeoutSec 300
    return $destination
}

function Install-SignedFile([string]$File, [string]$Arguments) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if ($signature.Status -ne 'Valid') { throw "Installer signature is invalid: $File" }
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010, 1641)) {
        throw "Installer failed (exit $($process.ExitCode)): $File"
    }
    if ($process.ExitCode -ne 0) { Write-Host 'Windows reports that a restart may be needed.' }
}

function Install-Prerequisite([string]$Kind) {
    $ids = @{ Python = 'Python.Python.3.13'; Node = 'OpenJS.NodeJS.LTS' }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            Invoke-Tool $winget.Source @('install', '--id', $ids[$Kind], '--exact', '--source', 'winget',
                '--scope', 'machine', '--architecture', 'x64', '--silent', '--disable-interactivity',
                '--accept-package-agreements', '--accept-source-agreements')
            Update-ProcessPath
            $found = if ($Kind -eq 'Python') { Find-Python } else { Find-Node }
            if ($found) { return }
        } catch {
            Write-Host "WinGet could not finish: $_"
        }
    }
    Write-Host 'Using the official installer.'
    if ($Kind -eq 'Python') {
        $file = Save-Download 'https://www.python.org/ftp/python/3.13.12/python-3.13.12-amd64.exe' 'python.exe'
        Install-SignedFile $file '/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0'
    } else {
        $releases = Invoke-RestMethod 'https://nodejs.org/dist/index.json' -TimeoutSec 60
        $release = $releases | Where-Object { $_.lts -and $_.files -contains 'win-x64-msi' } | Select-Object -First 1
        if (!$release) { throw 'No Windows x64 Node.js LTS installer was found.' }
        $name = "node-$($release.version)-x64.msi"
        $base = "https://nodejs.org/dist/$($release.version)"
        $file = Save-Download "$base/$name" $name
        $sums = Save-Download "$base/SHASUMS256.txt" 'SHASUMS256.txt'
        $line = Get-Content -LiteralPath $sums | Where-Object { $_ -match ('\s+' + [regex]::Escape($name) + '$') }
        if (@($line).Count -ne 1 -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne ($line -split '\s+')[0]) {
            throw 'Node.js installer SHA256 verification failed.'
        }
        # Verify the MSI before handing it to Windows Installer.
        if ((Get-AuthenticodeSignature -LiteralPath $file).Status -ne 'Valid') {
            throw 'Node.js installer signature is invalid.'
        }
        $process = Start-Process msiexec.exe -ArgumentList "/i `"$file`" /qn /norestart" -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { throw "Node.js installation failed (exit $($process.ExitCode))." }
    }
    Update-ProcessPath
}

function Update-Npm([string]$Node, [string]$Npm) {
    # npm bundles semver; use its engine rules rather than guessing a compatible major.
    $semver = Join-Path (Split-Path $Npm) 'node_modules\npm\node_modules\semver'
    if (!(Test-Path -LiteralPath $semver)) {
        $globalRoot = & $Npm root --global
        if ($LASTEXITCODE -ne 0) { throw 'Cannot locate npm modules.' }
        $semver = Join-Path "$globalRoot" 'npm\node_modules\semver'
    }
    $selector = @'
const https = require('https');
const semver = require(process.argv[1]);
const request = https.get('https://registry.npmjs.org/npm', {headers: {Accept: 'application/vnd.npm.install-v1+json'}}, response => {
  if (response.statusCode !== 200) { response.resume(); console.error('npm registry HTTP ' + response.statusCode); process.exitCode = 1; return; }
  let data = '';
  response.setEncoding('utf8');
  response.on('data', chunk => data += chunk);
  response.on('error', error => { console.error(error.message); process.exitCode = 1; });
  response.on('end', () => {
    try {
      const versions = JSON.parse(data).versions;
      const version = Object.keys(versions).filter(v => semver.valid(v) && !semver.prerelease(v) &&
        !versions[v].deprecated && semver.satisfies(process.versions.node, versions[v].engines?.node || '*'))
        .sort(semver.rcompare)[0];
      if (!version) throw new Error('No compatible npm release found');
      console.log(version);
    } catch (error) { console.error(error.message); process.exitCode = 1; }
  });
});
request.setTimeout(60000, () => request.destroy(new Error('npm registry timed out')));
request.on('error', error => { console.error(error.message); process.exitCode = 1; });
'@
    $target = & $Node -e $selector $semver
    if ($LASTEXITCODE -ne 0 -or "$target" -notmatch '^\d+\.\d+\.\d+$') { throw 'Could not select a compatible npm release.' }
    Invoke-Tool $Npm @('install', '--global', "npm@$target", '--engine-strict')
    Update-ProcessPath
}

function Invoke-Setup {
    Set-Location -LiteralPath $script:Root
    foreach ($required in @('pyproject.toml', 'src\upool\__main__.py', 'ui\package-lock.json')) {
        if (!(Test-Path -LiteralPath (Join-Path $script:Root $required))) {
            throw "Run install.bat inside the complete U-Pool source folder. Missing: $required"
        }
    }
    if (![Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or
        $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw 'Windows x64 is required.' }

    Write-Step '1/7 Checking Python and Node.js'
    $python = Find-Python
    if (!$python) { Install-Prerequisite 'Python'; $python = Find-Python }
    if (!$python) { throw 'Python 3.11+ x64 is still unavailable. Restart Windows and run install.bat again.' }
    $node = Find-Node
    if (!$node) { Install-Prerequisite 'Node'; $node = Find-Node }
    if (!$node) { throw 'Node.js 20.9+ is still unavailable. Restart Windows and run install.bat again.' }
    $env:Path = (Split-Path $node) + ';' + $env:Path
    $npm = Join-Path (Split-Path $node) 'npm.cmd'
    if (!(Test-Path -LiteralPath $npm)) {
        Install-Prerequisite 'Node'
        $node = Find-Node
        if (!$node) { throw 'Node.js installation could not be repaired.' }
        $env:Path = (Split-Path $node) + ';' + $env:Path
        $npm = Join-Path (Split-Path $node) 'npm.cmd'
    }
    if (!(Test-Path -LiteralPath $npm)) { throw 'npm.cmd is missing after Node.js installation.' }
    Write-Host "Python: $python"
    Invoke-Tool $node @('--version')

    Write-Step '2/7 Updating npm'
    Update-Npm $node $npm
    $env:Path = (Split-Path $node) + ';' + $env:Path
    # npm's global shim may be placed in AppData rather than beside node.exe.
    $npmPrefix = & $npm prefix --global
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate updated npm.' }
    $updatedNpm = Join-Path "$npmPrefix" 'npm.cmd'
    if (Test-Path -LiteralPath $updatedNpm) { $npm = $updatedNpm }
    Invoke-Tool $npm @('--version')

    Write-Step '3/7 Installing Python dependencies in .venv'
    $venv = Join-Path $script:Root '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $venv)) { Invoke-Tool $python @('-m', 'venv', '.venv') }
    Invoke-Tool $venv @('-c', "import sys, struct; assert sys.version_info >= (3,11) and struct.calcsize('P') == 8, 'Remove or rename the incompatible .venv and run install.bat again'")
    Invoke-Tool $venv @('-m', 'pip', 'install', '--upgrade', 'pip')
    Invoke-Tool $venv @('-m', 'pip', 'install', '-e', '.')

    Write-Step '4/7 Installing UI dependencies'
    Invoke-Tool $npm @('--prefix', 'ui', 'ci')
    Write-Step '5/7 Building UI'
    Invoke-Tool $npm @('--prefix', 'ui', 'run', 'build')

    Write-Step '6/7 Verifying installation'
    Invoke-Tool $venv @('-m', 'upool', '--version')
    Invoke-Tool $venv @('-c', "import webview; import webview.platforms.winforms; print('Windows webview OK')")
    if (!(Test-Path -LiteralPath (Join-Path $script:Root 'ui\out\index.html'))) { throw 'Build did not create ui/out/index.html.' }

    Write-Step '7/7 Starting U-Pool'
    Write-Host 'Installation complete. To start later: .venv\Scripts\python.exe -m upool'
    Invoke-Tool $venv @('-m', 'upool')
}

function Start-Installer {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host 'Requesting administrator access...'
        $escapedPath = $env:UPOOL_INSTALL_BAT.Replace("'", "''")
        $bootstrap = "`$env:UPOOL_INSTALL_BAT = '$escapedPath'; try { & ([scriptblock]::Create(((Get-Content -LiteralPath `$env:UPOOL_INSTALL_BAT -Raw) -split '(?m)^# POWERSHELL\r?$', 2)[1])) } catch { Write-Host `$_ -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
        $process = Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoLogo -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded" -Wait -PassThru
        return $process.ExitCode
    }
    $logDirectory = Join-Path $script:Root 'build\install-logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $log = Join-Path $logDirectory ("install-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
    Start-Transcript -LiteralPath $log | Out-Null
    $code = 0
    try {
        Write-Host "U-Pool installer - log: $log"
        Invoke-Setup
    } catch {
        $code = 1
        Write-Host "`nINSTALLATION / STARTUP FAILED: $_" -ForegroundColor Red
        Write-Host "Log: $log"
        Read-Host 'Press Enter to close' | Out-Null
    } finally {
        Stop-Transcript | Out-Null
        if (Test-Path -LiteralPath $script:Downloads) {
            Get-ChildItem -LiteralPath $script:Downloads -File | Remove-Item -Force
            Remove-Item -LiteralPath $script:Downloads -Force
        }
    }
    return $code
}

exit (Start-Installer)
