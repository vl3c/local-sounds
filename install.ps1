<#
.SYNOPSIS
    Set up local-sounds: a fully local sound-effects generator (Stable Audio 3 Small-SFX and
    MOSS-SoundEffect v2) with automatic checks, on this machine's NVIDIA GPU.

.DESCRIPTION
    Clones the two engines' inference code at pinned commits into engine\, builds three
    venvs (their dependencies clash), installs CUDA 12.8 builds of PyTorch, and fetches the
    weights with checksum verification into models\. Nothing here uploads anything.

    Re-running is safe: every finished step is skipped and downloads resume.

    Stable Audio 3 is gated on Hugging Face: before installing, accept its licence at
    https://huggingface.co/stabilityai/stable-audio-3-small-sfx and run `hf auth login`
    (or `huggingface-cli login`) yourself. This script never touches your token.

    Works on Windows PowerShell 5.1 and PowerShell 7+.

.PARAMETER PythonVersion
    Python version for the venvs, passed to the 'py' launcher (3.12 or newer: MOSS needs it).

.PARAMETER ReuseModels
    The models\ folder of another local-sounds install. Its weights are hard-linked (or
    copied, across drives) instead of downloaded, then checksum-verified. Files already in
    your Hugging Face cache are reused the same way, automatically.

.PARAMETER WithCaptioner
    Also download the optional audio captioner (MiDashengLM 0.6B, 2.8 GB).

.PARAMETER SkipModels
    Build the environments only; fetch the weights later with scripts\download_models.py.

.PARAMETER NoSkillLink
    Don't link this folder into ~\.claude\skills\local-sounds (the Claude Code skill).

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -ReuseModels D:\old\local-sounds\models -WithCaptioner
#>
[CmdletBinding()]
param(
    [string] $PythonVersion = '3.13',
    [string] $CudaIndex     = 'cu128',
    [string] $ReuseModels   = '',
    [switch] $WithCaptioner,
    [switch] $SkipModels,
    [switch] $NoSkillLink,
    [switch] $NoPipCache
)

$ErrorActionPreference = 'Stop'

$RepoRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }

# The engines' inference code, at the commits this repo was built and tested against.
$SA3_REPO    = 'https://github.com/Stability-AI/stable-audio-3.git'
$SA3_COMMIT  = '3a82c807b69cf4b7c5c05270011a5d5e47abac18'
$SA3_TORCH   = '2.7.1'   # what the stable-audio-3 library pins (with cu128 wheels instead of cu126)
$MOSS_REPO   = 'https://github.com/OpenMOSS/MOSS-TTS.git'
$MOSS_COMMIT = '934d6826b084c46a0d033402174d5f8ac4ed2519'
$MAIN_TORCH  = '2.9.1'   # the main venv and MOSS (which asks for 2.9.0)

function Step ($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "    $m"   -ForegroundColor Green }
function Note ($m) { Write-Host "    $m"   -ForegroundColor DarkGray }
function Warn ($m) { Write-Host "    $m"   -ForegroundColor Yellow }
function Fail ($m) { Write-Host "`nERROR: $m" -ForegroundColor Red; exit 1 }

# Windows PowerShell 5.1 turns any stderr output from a native command into a
# terminating error while $ErrorActionPreference is 'Stop'; probes go through here.
function Probe {
    param([Parameter(Mandatory)][string] $Exe, [string[]] $Arguments = @())
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $Exe @Arguments 2>&1 | Out-String)
        [pscustomobject]@{ Code = $LASTEXITCODE; Out = $out.Trim() }
    } catch {
        [pscustomobject]@{ Code = -1; Out = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $prev
    }
}

function LastLine ($s) { (($s -split "`r?`n") | Select-Object -Last 1).Trim() }

$pipFlags = @('--disable-pip-version-check')
if ($NoPipCache) { $pipFlags += '--no-cache-dir' }

# --- prerequisites --------------------------------------------------------
Step 'Checking prerequisites'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail 'git is not on PATH. Install Git for Windows: https://git-scm.com/download/win'
}
Ok "git $((git --version) -replace 'git version ')"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Fail "The 'py' launcher is not on PATH. Install Python from python.org with the launcher option ticked."
}
$probe = Probe 'py' @("-$PythonVersion", '-c', 'import sys; sys.stdout.write(sys.executable)')
if ($probe.Code -ne 0) {
    Fail "Python $PythonVersion not found. Installed:`n$((Probe 'py' @('--list')).Out)`n       Pass -PythonVersion with one of those (3.12 or newer)."
}
Ok "Python $PythonVersion at $($probe.Out)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Ok "GPU: $((Probe 'nvidia-smi' @('--query-gpu=name,memory.total', '--format=csv,noheader')).Out)"
} else {
    Warn 'nvidia-smi not found. The engines need an NVIDIA GPU.'
}
$drive = Get-PSDrive -Name ($RepoRoot.Substring(0, 1)) -ErrorAction SilentlyContinue
if ($drive) {
    $freeGB = [math]::Round($drive.Free / 1GB, 1)
    Ok "$freeGB GB free"
    if ($freeGB -lt 40) { Warn 'Under 40 GB free. Weights are ~16 GB, the three venvs ~17 GB, plus pip cache.' }
}

# --- engine code ----------------------------------------------------------
function Clone-Pinned ($url, $dest, $commit, $sparse) {
    if (Test-Path (Join-Path $dest '.git')) {
        $head = (Probe 'git' @('-C', $dest, 'rev-parse', 'HEAD')).Out
        if ($head -eq $commit) { Ok "Already at $($commit.Substring(0, 10))"; return }
        Note "At $head; checking out the pinned commit"
        git -C $dest fetch --quiet origin
        git -C $dest checkout --quiet $commit
        if ($LASTEXITCODE -ne 0) { Fail "could not check out $commit in $dest (local changes? move it aside)" }
        return
    }
    if (Test-Path $dest) { Fail "$dest exists but is not a git checkout. Move it aside." }
    New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
    if ($sparse) {
        git clone --quiet --filter=blob:none --sparse $url $dest
        if ($LASTEXITCODE -ne 0) { Fail "git clone $url failed" }
        git -C $dest sparse-checkout set $sparse
    } else {
        git clone --quiet $url $dest
        if ($LASTEXITCODE -ne 0) { Fail "git clone $url failed" }
    }
    git -C $dest checkout --quiet $commit
    if ($LASTEXITCODE -ne 0) { Fail "could not check out $commit in $dest" }
    Ok 'Cloned'
}

$sa3Code  = Join-Path $RepoRoot 'engine\stable-audio-3'
$mossCode = Join-Path $RepoRoot 'engine\MOSS-TTS'
Step "Stable Audio 3 inference code (engine\stable-audio-3)"
Clone-Pinned $SA3_REPO $sa3Code $SA3_COMMIT $null
Step "MOSS-SoundEffect v2 inference code (engine\MOSS-TTS, just moss_soundeffect_v2)"
Clone-Pinned $MOSS_REPO $mossCode $MOSS_COMMIT 'moss_soundeffect_v2'

# --- venvs ------------------------------------------------------------------
function Ensure-Venv ($name, $torch) {
    $venv = Join-Path $RepoRoot $name
    $py = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $py)) {
        & py "-$PythonVersion" -m venv $venv
        if (-not (Test-Path $py)) { Fail "$name creation failed" }
        Ok "$name created"
    }
    $have = Probe $py @('-c', 'import torch;print(torch.__version__)')
    if ($have.Code -eq 0 -and (LastLine $have.Out) -eq "$torch+$CudaIndex") {
        Ok "torch $(LastLine $have.Out) already installed"
    } else {
        Note "Installing torch $torch from the $CudaIndex index (~3 GB download unless cached)..."
        & $py -m pip install --quiet @pipFlags --upgrade pip
        & $py -m pip install @pipFlags --index-url "https://download.pytorch.org/whl/$CudaIndex" "torch==$torch" "torchaudio==$torch"
        if ($LASTEXITCODE -ne 0) { Fail "torch install into $name failed" }
    }
    # A constraints file keeps every later install from swapping the CUDA build for a CPU one.
    $c = Join-Path $env:TEMP "local-sounds-$name-constraints.txt"
    Set-Content -Path $c -Value @("torch==$torch+$CudaIndex", "torchaudio==$torch+$CudaIndex") -Encoding ascii
    return @{ Py = $py; Constraints = $c }
}

function Check-Torch ($py, $name) {
    $t = Probe $py @('-W', 'ignore', '-c', 'import torch;print(torch.__version__, torch.cuda.is_available())')
    $last = LastLine $t.Out
    if ($last -notmatch '\+cu\S* True$') { Note $t.Out; Fail "$name has no working CUDA torch ($last)" }
    Ok "$name ready (torch $last)"
}

Step 'Main environment (.venv): server, checks, post-processing'
$main = Ensure-Venv '.venv' $MAIN_TORCH
& $main.Py -m pip install --quiet @pipFlags -c $main.Constraints -r (Join-Path $RepoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'main dependency install failed' }
Check-Torch $main.Py '.venv'

Step 'Stable Audio 3 environment (.venv-sa3)'
$sa3 = Ensure-Venv '.venv-sa3' $SA3_TORCH
# --no-deps: the library pins torch==2.7.1 without a CUDA index on Windows.
& $sa3.Py -m pip install --quiet @pipFlags --no-deps -e $sa3Code
if ($LASTEXITCODE -ne 0) { Fail 'stable-audio-3 install failed' }
& $sa3.Py -m pip install --quiet @pipFlags -c $sa3.Constraints -r (Join-Path $RepoRoot 'requirements-sa3.txt')
if ($LASTEXITCODE -ne 0) { Fail 'stable-audio-3 dependency install failed' }
if ((Probe $sa3.Py @('-W', 'ignore', '-c', 'import stable_audio_3')).Code -ne 0) { Fail 'stable_audio_3 does not import' }
Check-Torch $sa3.Py '.venv-sa3'

Step 'MOSS-SoundEffect environment (.venv-moss)'
$moss = Ensure-Venv '.venv-moss' $MAIN_TORCH
# --no-deps: its metadata pins torch 2.9.0 and numpy 1.26 (no wheels for Python 3.13).
& $moss.Py -m pip install --quiet @pipFlags --no-deps -e (Join-Path $mossCode 'moss_soundeffect_v2')
if ($LASTEXITCODE -ne 0) { Fail 'moss-soundeffect-v2 install failed' }
# --no-warn-conflicts: pip would otherwise print a harmless 'ERROR' about the demo-only
# packages (gradio, imageio...) and exact pins this repo deliberately doesn't install.
& $moss.Py -m pip install --quiet --no-warn-conflicts @pipFlags -c $moss.Constraints -r (Join-Path $RepoRoot 'requirements-moss.txt')
if ($LASTEXITCODE -ne 0) { Fail 'MOSS dependency install failed' }
if ((Probe $moss.Py @('-W', 'ignore', '-c', 'import moss_soundeffect_v2')).Code -ne 0) { Fail 'moss_soundeffect_v2 does not import' }
Check-Torch $moss.Py '.venv-moss'

# --- weights --------------------------------------------------------------
$modelsOk = $true
if ($SkipModels) {
    Step 'Skipping model download (-SkipModels)'
    Note "Fetch them later with:  & `"$($main.Py)`" scripts\download_models.py"
} else {
    $gb = if ($WithCaptioner) { '~18.7' } else { '~16' }
    Step "Model weights ($gb GB, checksum-verified)"
    Note 'Resumable - if this is interrupted, just re-run install.ps1.'
    Note 'Stable Audio 3 is gated: accept its licence on Hugging Face and run `hf auth login` first.'
    $dlArgs = @((Join-Path $RepoRoot 'scripts\download_models.py'))
    if ($WithCaptioner) { $dlArgs += '--with-optional' }
    if ($ReuseModels) {
        if (-not (Test-Path $ReuseModels)) { Fail "-ReuseModels: $ReuseModels does not exist" }
        $dlArgs += @('--reuse', (Resolve-Path $ReuseModels).Path)
    }
    & $main.Py @dlArgs
    if ($LASTEXITCODE -ne 0) {
        $modelsOk = $false
        Warn 'Some weights are missing (see above). Fix that and re-run install.ps1; the rest is done.'
    }
}

# --- Claude Code skill ------------------------------------------------------
if (-not $NoSkillLink) {
    Step 'Claude Code skill (~\.claude\skills\local-sounds)'
    $skills = Join-Path $env:USERPROFILE '.claude\skills'
    $link = Join-Path $skills 'local-sounds'
    if (Test-Path $link) {
        $target = (Get-Item -LiteralPath $link).Target
        if ($target -and ((@($target)[0].TrimEnd('\')) -ne $RepoRoot.TrimEnd('\'))) {
            Warn "$link points to $(@($target)[0]), not here. Leaving it; remove it to link this copy."
        } else {
            Ok 'Already there'
        }
    } else {
        New-Item -ItemType Directory -Path $skills -Force | Out-Null
        New-Item -ItemType Junction -Path $link -Target $RepoRoot | Out-Null
        Ok "Linked $link -> $RepoRoot"
    }
}

Step 'Done'
Write-Host @"

  Start it with:      .\start.cmd
  Then open:          http://127.0.0.1:8191/
  Agents use:         python scripts\sfx.py "sword hit" --out <folder>

  Stable Audio 3 is under the Stability AI Community License; MOSS-SoundEffect is
  Apache-2.0. See LICENSE-MODELS.md.

"@ -ForegroundColor Green
if (-not $modelsOk) { exit 1 }
