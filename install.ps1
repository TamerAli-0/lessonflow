<#
    LessonFlow installer for Windows.

    Paste this one line into Command Prompt or PowerShell:

      powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; irm https://raw.githubusercontent.com/TamerAli-0/lessonflow/main/install.ps1 | iex"

    It looks at what the computer already has, installs only what is missing or
    too old, and asks before changing anything. Administrator rights are not
    needed. Running the same line again later updates an existing install and
    keeps everything saved in runtime\.

    This file is deliberately plain ASCII. Windows PowerShell 5.1 reads .ps1
    files as ANSI when they have no byte-order mark, so any accented or box
    character written directly in the file would arrive as garbage. The nicer
    symbols are built from character codes at run time instead.
#>

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$NoLaunch,
    [string]$InstallDir,
    [string]$Source
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------- settings --

$RepoOwner    = 'TamerAli-0'
$RepoName     = 'lessonflow'
$RepoBranch   = 'main'
$AppName      = 'LessonFlow'
$AppPort      = 5050
$MinPython    = [version]'3.10'
$TargetPython = '3.13'
$WingetId     = 'Python.Python.3.13'
$PythonFallbackUrls = @(
    'https://www.python.org/ftp/python/3.13.9/python-3.13.9-amd64.exe',
    'https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe',
    'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
)

# Parameters are not passed through "irm ... | iex", so environment variables
# can set the same three things.
if (-not $InstallDir) { $InstallDir = $env:LESSONFLOW_DIR }
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA $AppName }
if (-not $Source)     { $Source     = $env:LESSONFLOW_SOURCE }
$script:AutoYes = $false
if ($Yes) { $script:AutoYes = $true }
if ($env:LESSONFLOW_YES -eq '1') { $script:AutoYes = $true }

# ------------------------------------------------------------------ output --

$script:Fancy = $false
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $script:Fancy = $true
} catch {
    $script:Fancy = $false
}

function New-Glyphs([bool]$Unicode) {
    if ($Unicode) {
        return @{
            Ok    = [string][char]0x2714
            No    = [string][char]0x2716
            Dot   = [string][char]0x00B7
            Arrow = [string][char]0x2192
            H     = [string][char]0x2500
            V     = [string][char]0x2502
            TL    = [string][char]0x256D
            TR    = [string][char]0x256E
            BL    = [string][char]0x2570
            BR    = [string][char]0x256F
        }
    }
    return @{ Ok='+'; No='x'; Dot='.'; Arrow='>'; H='-'; V='|'; TL='+'; TR='+'; BL='+'; BR='+' }
}

$G = New-Glyphs $script:Fancy
$Width = 68

function Write-Rule([string]$Left, [string]$Right) {
    Write-Host ($Left + ($G.H * ($Width - 2)) + $Right) -ForegroundColor DarkCyan
}

function Write-Line([string]$Text, [string]$Color = 'Gray') {
    $room = $Width - 4
    if ($Text.Length -gt $room) {
        # Keep the end of a long path, which is the part that identifies it.
        $Text = '...' + $Text.Substring($Text.Length - ($room - 3))
    }
    $pad = $room - $Text.Length
    if ($pad -lt 0) { $pad = 0 }
    Write-Host ($G.V + ' ') -NoNewline -ForegroundColor DarkCyan
    Write-Host $Text -NoNewline -ForegroundColor $Color
    Write-Host ((' ' * $pad) + ' ' + $G.V) -ForegroundColor DarkCyan
}

function Write-Banner {
    Write-Host ''
    Write-Host '    __                            ______            ' -ForegroundColor Cyan
    Write-Host '   / /   ___  ___ ___ ___  ___   / __/ /__ _    __  ' -ForegroundColor Cyan
    Write-Host '  / /__ / -_)(_-<(_-</ _ \/ _ \ / _// / _ \ |/|/ /  ' -ForegroundColor Cyan
    Write-Host ' /____/ \__//___/___/\___/_//_//_/ /_/\___/|__,__/  ' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Turn your own teaching material into a lesson plan.' -ForegroundColor DarkGray
    Write-Host ("  One-step setup {0} nothing is installed without asking." -f $G.Dot) -ForegroundColor DarkGray
    Write-Host ''
}

function Write-Section([string]$Title) {
    Write-Host ''
    Write-Host ('  ' + $G.Arrow + ' ') -NoNewline -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor White
    Write-Host ('  ' + ($G.H * ($Width - 4))) -ForegroundColor DarkGray
}

function Write-Row([string]$Name, [string]$Status, [string]$Detail, [string]$Color) {
    Write-Host ('    {0,-16}' -f $Name) -NoNewline -ForegroundColor Gray
    Write-Host ('{0,-11}' -f $Status) -NoNewline -ForegroundColor $Color
    Write-Host $Detail -ForegroundColor DarkGray
}

function Write-Note([string]$Text, [string]$Color = 'DarkGray') {
    Write-Host ('    ' + $Text) -ForegroundColor $Color
}

function Write-Plan([string]$Text) {
    Write-Host ('    ' + $G.Arrow + ' ') -NoNewline -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Gray
}

function Write-Fail([string]$Text) {
    Write-Host ''
    Write-Host ('  ' + $G.No + ' ' + $Text) -ForegroundColor Red
    Write-Host ''
}

function Confirm-Action([string]$Question, [bool]$DefaultYes) {
    if ($script:AutoYes) {
        Write-Host ('    ' + $G.Ok + ' ') -NoNewline -ForegroundColor Green
        Write-Host ($Question + ' ') -NoNewline -ForegroundColor White
        Write-Host 'yes (automatic)' -ForegroundColor DarkGray
        return $true
    }
    if ($DefaultYes) { $hint = '[Y/n]' } else { $hint = '[y/N]' }
    while ($true) {
        Write-Host '    ? ' -NoNewline -ForegroundColor Yellow
        Write-Host ($Question + ' ') -NoNewline -ForegroundColor White
        Write-Host ($hint + ' ') -NoNewline -ForegroundColor DarkGray
        $answer = (Read-Host).Trim().ToLower()
        if ($answer -eq '') { return $DefaultYes }
        if ($answer -eq 'y' -or $answer -eq 'yes') { return $true }
        if ($answer -eq 'n' -or $answer -eq 'no') { return $false }
        Write-Note 'Please type y or n.' 'DarkYellow'
    }
}

function Invoke-Step([string]$Label, [scriptblock]$Action) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $blank = ' ' * ($Label.Length + 24)
    Write-Host ('    ' + $G.Dot + ' ' + $Label) -NoNewline -ForegroundColor DarkGray
    try {
        $result = & $Action
        $watch.Stop()
        Write-Host ("`r" + $blank + "`r") -NoNewline
        Write-Host ('    ' + $G.Ok + ' ') -NoNewline -ForegroundColor Green
        Write-Host ('{0,-52} ' -f $Label) -NoNewline -ForegroundColor Gray
        Write-Host ('{0:n1}s' -f $watch.Elapsed.TotalSeconds) -ForegroundColor DarkGray
        return $result
    } catch {
        $watch.Stop()
        Write-Host ("`r" + $blank + "`r") -NoNewline
        Write-Host ('    ' + $G.No + ' ') -NoNewline -ForegroundColor Red
        Write-Host $Label -ForegroundColor Gray
        throw
    }
}

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    # A native program writing to stderr would become a terminating error while
    # $ErrorActionPreference is Stop, and pip and winget both do that for
    # ordinary warnings. Only the exit code decides whether this failed.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        $tail = ($output | Select-Object -Last 12 | Out-String).Trim()
        throw ("{0} failed (exit code {1}).`r`n{2}" -f (Split-Path $Exe -Leaf), $code, $tail)
    }
    return $output
}

# ------------------------------------------------------------- inspection --

function Update-SessionPath {
    # winget and the Python installer write PATH into the registry; this window
    # keeps its old copy until it is told to re-read it.
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($machine, $user) | Where-Object { $_ }
    if ($parts.Count -gt 0) { $env:Path = $parts -join ';' }
}

function Get-PythonInfo([string]$Exe) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $Exe -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        $code = $LASTEXITCODE
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0 -or -not $raw) { return $null }
    try {
        return [pscustomobject]@{ Path = $Exe; Version = [version]("$raw".Trim()) }
    } catch {
        return $null
    }
}

function Find-Pythons {
    $paths = New-Object 'System.Collections.Generic.List[string]'

    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($cmd in (Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($cmd.Source) { $paths.Add($cmd.Source) }
        }
    }

    if (Get-Command 'py.exe' -ErrorAction SilentlyContinue) {
        try {
            foreach ($line in (& py.exe -0p 2>$null)) {
                if ("$line" -match '([A-Za-z]:\\[^\r\n]*?python\.exe)') { $paths.Add($Matches[1]) }
            }
        } catch { }
    }

    $globs = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
        (Join-Path $env:ProgramFiles 'Python3*\python.exe'),
        'C:\Python3*\python.exe'
    )
    foreach ($glob in $globs) {
        foreach ($item in (Get-ChildItem $glob -ErrorAction SilentlyContinue)) { $paths.Add($item.FullName) }
    }

    $seen = @{}
    $found = @()
    foreach ($path in $paths) {
        # The Microsoft Store stub is not a real interpreter; running it opens the Store.
        if ($path -like '*\Microsoft\WindowsApps\*') { continue }
        $key = $path.ToLower()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $info = Get-PythonInfo $path
        if ($info) { $found += $info }
    }
    return @($found | Sort-Object -Property Version -Descending)
}

function Test-WordInstalled {
    foreach ($key in @('HKLM:\SOFTWARE\Classes\Word.Application', 'HKCU:\SOFTWARE\Classes\Word.Application')) {
        if (Test-Path $key) { return $true }
    }
    return $false
}

function Find-DownloadedCopy {
    # The teacher may already have the project in Downloads or on the Desktop.
    $roots = @((Join-Path $env:USERPROFILE 'Downloads'), [Environment]::GetFolderPath('Desktop'))
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        foreach ($dir in (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)) {
            if ((Test-Path (Join-Path $dir.FullName 'app.py')) -and (Test-Path (Join-Path $dir.FullName 'lesson_planner'))) {
                return [pscustomobject]@{ Kind = 'folder'; Path = $dir.FullName }
            }
        }
        foreach ($zip in (Get-ChildItem $root -Filter '*.zip' -File -ErrorAction SilentlyContinue)) {
            if ($zip.Name -match 'lessonflow|lesson-flow|lesson_plan') {
                return [pscustomobject]@{ Kind = 'zip'; Path = $zip.FullName }
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------- actions --

function Install-Python {
    Update-SessionPath
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            Invoke-Step ('Installing Python ' + $TargetPython + ' with winget') {
                Invoke-Native $winget.Source @(
                    'install', '--id', $WingetId, '--exact', '--source', 'winget',
                    '--accept-package-agreements', '--accept-source-agreements', '--silent'
                )
            } | Out-Null
            Update-SessionPath
            $best = Find-Pythons | Select-Object -First 1
            if ($best -and $best.Version -ge $MinPython) { return $best }
        } catch {
            Write-Note 'winget could not do it, using the official installer instead.' 'DarkYellow'
        }
    }

    $temp = Join-Path $env:TEMP ('python-setup-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.exe')
    $downloaded = $false
    foreach ($url in $PythonFallbackUrls) {
        try {
            Invoke-Step ('Downloading ' + (Split-Path $url -Leaf)) {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
            } | Out-Null
            $downloaded = $true
            break
        } catch {
            continue
        }
    }
    if (-not $downloaded) { throw 'Python could not be downloaded. Check the internet connection and run the command again.' }

    Invoke-Step 'Running the Python installer (about a minute)' {
        $proc = Start-Process -FilePath $temp -Wait -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_pip=1', 'Include_launcher=1', 'Include_test=0'
        )
        # 1638 means a newer build of the same Python is already there.
        if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 1638) {
            throw ('The Python installer exited with code ' + $proc.ExitCode + '.')
        }
    } | Out-Null

    Remove-Item $temp -Force -ErrorAction SilentlyContinue
    Update-SessionPath
    $best = Find-Pythons | Select-Object -First 1
    if (-not $best -or $best.Version -lt $MinPython) {
        throw 'Python is installed but this window cannot see it yet. Close this window, open a new one, and run the command again.'
    }
    return $best
}

function Get-ProjectFiles([string]$Destination) {
    $stage = Join-Path $env:TEMP ('lessonflow-src-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $root = $null

    if ($script:SourceFolder) {
        $root = $script:SourceFolder
    } elseif ($script:SourceZip) {
        Invoke-Step 'Unpacking the copy already on this computer' {
            Expand-Archive -Path $script:SourceZip -DestinationPath $stage -Force
        } | Out-Null
        $root = $stage
    } else {
        $zip = Join-Path $stage 'source.zip'
        $url = 'https://github.com/' + $RepoOwner + '/' + $RepoName + '/archive/refs/heads/' + $RepoBranch + '.zip'
        Invoke-Step 'Downloading the latest version' {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        } | Out-Null
        Invoke-Step 'Unpacking it' {
            Expand-Archive -Path $zip -DestinationPath $stage -Force
        } | Out-Null
        $root = $stage
    }

    if (-not (Test-Path (Join-Path $root 'app.py'))) {
        $inner = Get-ChildItem $root -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'app.py') } | Select-Object -First 1
        if (-not $inner) { throw ('The files that arrived do not look like ' + $AppName + '.') }
        $root = $inner.FullName
    }

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $sameFolder = ((Resolve-Path $root).Path.TrimEnd('\') -eq (Resolve-Path $Destination).Path.TrimEnd('\'))

    if (-not $sameFolder) {
        Invoke-Step 'Copying the program files into place' {
            # runtime\ holds uploads and finished plans, so an update never touches it.
            $skip = @('runtime', '.venv', '.git', '__pycache__', '.pytest_cache', '.planning', '.coverage')
            foreach ($item in (Get-ChildItem $root -Force)) {
                if ($skip -contains $item.Name) { continue }
                $target = Join-Path $Destination $item.Name
                # Copy-Item nests a folder inside itself when the name already
                # exists, so the old copy goes first.
                if (Test-Path $target) { Remove-Item $target -Recurse -Force }
                Copy-Item -Path $item.FullName -Destination $target -Recurse -Force
            }
        } | Out-Null
    }

    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
}

function Initialize-Venv([string]$PythonExe) {
    $venv = Join-Path $InstallDir '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $config = Join-Path $venv 'pyvenv.cfg'
    $wanted = (Get-PythonInfo $PythonExe).Version
    $rebuild = $true

    if ((Test-Path $config) -and (Test-Path $venvPython)) {
        $rebuild = $false
        $found = Select-String -Path $config -Pattern '^\s*version\s*=\s*([0-9][0-9.]*)' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) {
            try {
                $have = [version]$found.Matches[0].Groups[1].Value
                # A folder built by a different Python stops working after an upgrade.
                if ($have.Major -ne $wanted.Major -or $have.Minor -ne $wanted.Minor) { $rebuild = $true }
            } catch {
                $rebuild = $true
            }
        } else {
            $rebuild = $true
        }
    }

    if ($rebuild) {
        Invoke-Step 'Setting up a private Python folder for the app' {
            if (Test-Path $venv) { Remove-Item $venv -Recurse -Force }
            Invoke-Native $PythonExe @('-m', 'venv', $venv)
        } | Out-Null
    }
    if (-not (Test-Path $venvPython)) { throw 'The private Python folder could not be created.' }

    Invoke-Step 'Updating the installer tools' {
        Invoke-Native $venvPython @('-m', 'pip', 'install', '--upgrade', '--quiet', '--disable-pip-version-check', 'pip', 'setuptools', 'wheel')
    } | Out-Null

    Invoke-Step 'Installing the program libraries' {
        Invoke-Native $venvPython @(
            '-m', 'pip', 'install', '--upgrade', '--quiet', '--disable-pip-version-check', '--prefer-binary',
            '-r', (Join-Path $InstallDir 'requirements.txt')
        )
    } | Out-Null

    return $venvPython
}

function New-Launcher {
    $bat = Join-Path $InstallDir ('Start ' + $AppName + '.bat')
    $address = 'http://127.0.0.1:' + $AppPort
    # One level of quoting only: cmd sees the empty window title, then hands the
    # rest to PowerShell, which waits and opens the browser.
    $opener = 'start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process ''ADDRESS''"'
    $opener = $opener.Replace('ADDRESS', $address)

    $lines = @(
        '@echo off',
        ('title ' + $AppName),
        'cd /d "%~dp0"',
        'if not exist ".venv\Scripts\python.exe" goto missing',
        $opener,
        'echo.',
        ('echo   ' + $AppName + ' is running at ' + $address),
        'echo   Keep this window open while you work. Close it to stop.',
        'echo.',
        '".venv\Scripts\python.exe" app.py',
        'echo.',
        'echo   The program stopped. Press any key to close this window.',
        'pause >nul',
        'exit /b 0',
        ':missing',
        'echo.',
        ('echo   ' + $AppName + ' is not set up in this folder.'),
        'echo   Run the setup command again.',
        'echo.',
        'pause >nul'
    )
    # A .bat file needs CRLF line endings or cmd can mis-read the labels, and
    # Set-Content follows the host's default, so the bytes are written directly.
    $text = ($lines -join "`r`n") + "`r`n"
    [IO.File]::WriteAllText($bat, $text, [Text.Encoding]::ASCII)
    return $bat
}

function New-Shortcuts([string]$Target) {
    $made = @()
    try {
        $shell = New-Object -ComObject WScript.Shell
        # The LF monogram ships in assets\. Fall back to a stock icon rather than
        # letting a missing file stop the shortcut from being made at all.
        $icon = Join-Path $InstallDir 'assets\lessonflow.ico'
        if (-not (Test-Path $icon)) { $icon = $env:SystemRoot + '\System32\shell32.dll,21' }
        $places = @(
            (Join-Path ([Environment]::GetFolderPath('Desktop')) ($AppName + '.lnk')),
            (Join-Path $env:APPDATA ('Microsoft\Windows\Start Menu\Programs\' + $AppName + '.lnk'))
        )
        foreach ($place in $places) {
            $link = $shell.CreateShortcut($place)
            $link.TargetPath = $Target
            $link.WorkingDirectory = $InstallDir
            $link.Description = $AppName + ' - build a lesson plan from your teaching material'
            $link.IconLocation = $icon
            $link.Save()
            $made += $place
        }
    } catch { }
    return ,$made
}

# ------------------------------------------------------------------- main --

if (Get-Command Clear-Host -ErrorAction SilentlyContinue) { try { Clear-Host } catch { } }
Write-Banner

if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    Write-Fail 'This installer is for Windows.'
    return
}

Write-Section 'Checking this computer'

$os = 'Windows'
try { $os = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption.Trim() } catch { }
Write-Row 'Windows' 'OK' $os 'Green'

$python = Find-Pythons | Select-Object -First 1
$needPython = $false
$offerUpgrade = $false

if (-not $python) {
    Write-Row 'Python' 'MISSING' ('will install Python ' + $TargetPython) 'Red'
    $needPython = $true
} elseif ($python.Version -lt $MinPython) {
    Write-Row 'Python' 'TOO OLD' ('found ' + $python.Version + ', needs ' + $MinPython + ' or newer') 'Red'
    $needPython = $true
} elseif ($python.Version -lt [version]$TargetPython) {
    Write-Row 'Python' 'OK' ($python.Version.ToString() + ' works ' + $G.Dot + ' ' + $TargetPython + ' is newer') 'Green'
    $offerUpgrade = $true
} else {
    Write-Row 'Python' 'OK' ($python.Version.ToString() + ' at ' + $python.Path) 'Green'
}

$winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
if ($winget) {
    Write-Row 'App installer' 'OK' 'winget is available' 'Green'
} else {
    Write-Row 'App installer' 'MISSING' 'not a problem, the official installer is used' 'DarkYellow'
}

if (Test-WordInstalled) {
    Write-Row 'Microsoft Word' 'OK' 'draws the preview and opens finished plans' 'Green'
} else {
    Write-Row 'Microsoft Word' 'NOT FOUND' 'plans still download, the preview cannot be drawn' 'DarkYellow'
}

$isUpdate = Test-Path (Join-Path $InstallDir 'app.py')
if ($isUpdate) {
    Write-Row $AppName 'INSTALLED' ('will be updated in ' + $InstallDir) 'Cyan'
} else {
    Write-Row $AppName 'NEW' ('will be installed in ' + $InstallDir) 'Cyan'
}

$script:SourceFolder = $null
$script:SourceZip = $null
if ($Source) {
    if (Test-Path $Source -PathType Container) { $script:SourceFolder = (Resolve-Path $Source).Path }
    elseif (Test-Path $Source -PathType Leaf) { $script:SourceZip = (Resolve-Path $Source).Path }
}

$here = $null
if ($PSScriptRoot) { $here = $PSScriptRoot }
elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path $MyInvocation.MyCommand.Path -Parent }

if (-not $script:SourceFolder -and -not $script:SourceZip -and $here -and (Test-Path (Join-Path $here 'app.py'))) {
    $script:SourceFolder = $here
    Write-Row 'Program files' 'LOCAL' 'using the folder this script sits in' 'Green'
}

# Re-running the command on a computer that already has LessonFlow means "give me the
# current version". An older folder sitting in Downloads would quietly undo that, so an
# update always takes the fresh copy and the offer is only made on a first install.
if (-not $isUpdate -and -not $script:SourceFolder -and -not $script:SourceZip) {
    $local = Find-DownloadedCopy
    if ($local) {
        Write-Row 'Program files' 'FOUND' ('already on this computer: ' + $local.Path) 'Green'
        if (Confirm-Action 'Use that copy instead of downloading the latest?' $false) {
            if ($local.Kind -eq 'folder') { $script:SourceFolder = $local.Path } else { $script:SourceZip = $local.Path }
        }
    }
}
if (-not $script:SourceFolder -and -not $script:SourceZip) {
    Write-Row 'Program files' 'DOWNLOAD' ('from github.com/' + $RepoOwner + '/' + $RepoName) 'Cyan'
}

Write-Section 'What will happen'

if ($needPython) {
    Write-Plan ('Install Python ' + $TargetPython + ', about 30 MB. No administrator rights needed.')
    if (-not (Confirm-Action ('Install Python ' + $TargetPython + ' now?') $true)) {
        Write-Fail ('Python ' + $MinPython + ' or newer is required. Nothing was changed.')
        return
    }
} elseif ($offerUpgrade) {
    Write-Plan ('Your Python ' + $python.Version + ' already works, so this is optional.')
    if (Confirm-Action ('Install the newer Python ' + $TargetPython + ' as well?') $false) { $needPython = $true }
}

if ($isUpdate) {
    Write-Plan ('Update ' + $AppName + ' in ' + $InstallDir + '.')
    Write-Plan 'Keep everything already saved in the runtime folder.'
} else {
    Write-Plan ('Install ' + $AppName + ' in ' + $InstallDir + '.')
}
Write-Plan ('Put a ' + $AppName + ' icon on the Desktop and in the Start menu.')

if (-not (Confirm-Action 'Go ahead?' $true)) {
    Write-Fail 'Cancelled. Nothing was changed.'
    return
}

Write-Section 'Working'

$launcher = $null
$shortcuts = @()
try {
    if ($needPython) {
        $python = Install-Python
        Write-Note ('Python ' + $python.Version + ' is ready.')
    }
    if (-not $python) { throw 'No usable Python was found.' }

    Get-ProjectFiles -Destination $InstallDir
    $venvPython = Initialize-Venv -PythonExe $python.Path
    $launcher = Invoke-Step 'Creating the launcher' { New-Launcher }
    $shortcuts = Invoke-Step 'Adding the shortcuts' { New-Shortcuts -Target $launcher }
    Invoke-Step 'Checking that everything loads' {
        Invoke-Native $venvPython @('-c', 'import flask, fitz, docx, requests')
    } | Out-Null
} catch {
    Write-Fail $_.Exception.Message
    Write-Note 'Nothing else was changed. Fix what is shown above and run the command again.'
    Write-Host ''
    return
}

Write-Host ''
Write-Rule $G.TL $G.TR
Write-Line ($G.Ok + ' ' + $AppName + ' is ready.') 'Green'
Write-Line ''
Write-Line ('Start it      ' + $AppName + ' icon on the Desktop') 'Gray'
Write-Line ('It opens at   http://127.0.0.1:' + $AppPort) 'Gray'
Write-Line ('Installed in  ' + $InstallDir) 'DarkGray'
Write-Line ''
Write-Line 'The first screen asks for a free API key from Google' 'DarkGray'
Write-Line 'Gemini, Groq, Mistral or OpenRouter. Paste it there.' 'DarkGray'
Write-Rule $G.BL $G.BR

if (@($shortcuts).Count -eq 0) {
    Write-Note ('The shortcuts could not be made. Start it from: ' + $launcher) 'DarkYellow'
}

Write-Host ''
if (-not $NoLaunch) {
    if (Confirm-Action ('Start ' + $AppName + ' now?') $true) {
        Start-Process -FilePath $launcher -WorkingDirectory $InstallDir
        Write-Note 'A new window opens, and the page follows a few seconds later.'
    }
}
Write-Host ''
