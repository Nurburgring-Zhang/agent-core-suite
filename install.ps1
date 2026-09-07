<#
Agent Core Suite v2.0.0 - Windows installer (PowerShell)

NOTE: This script is intentionally ASCII-only. Windows PowerShell 5.1 parses .ps1
files as ANSI when there is no BOM, and some multi-byte characters end with byte
0x5C which breaks string termination. Keeping this file ASCII avoids that failure.

Usage (run from the suite directory):
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target C:\path\to\workspace
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target . -Mode generic
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target . -DryRun

Parameters:
    -Target     target workspace directory (default: current directory)
    -Mode       auto (default; acs_doctor detects the terminal from filesystem markers)
                or one of the ids in spec/terminals.json (qoder / qoderwork /
                claude / cursor / windsurf / codex / generic). Install targets
                live in that spec only.
    -SkillHome  override the skill install directory
    -Python     python executable name (default: auto-detect python / python3 / py)
    -Node       node executable name (fallback runtime when Python is absent; 4/5 gates)
    -DryRun     print the plan only, write nothing
    -Force      overwrite existing files
    -WithHook   also install hooks/pre-commit into <target>\.git\hooks (target must be a repo)

Exit codes: 0 = success, 1 = failure (incomplete inventory or post-install gate failure),
2 = usage error (unknown -Mode / unusable -Python / -Node).
Integrity: inventory existence + non-empty only. No hash verification.
#>
[CmdletBinding()]
param(
    [string]$Target = ".",
    # No ValidateSet on purpose: the legal set lives in spec/terminals.json. A second
    # copy here would be a second drift path; acs_doctor rejects unknown ids with exit 2.
    [string]$Mode = "auto",
    [string]$SkillHome = "",
    [string]$Python = "",
    [string]$Node = "",
    [switch]$DryRun,
    [switch]$Force,
    [switch]$WithHook
)

$ErrorActionPreference = "Stop"
$Suite = $PSScriptRoot
$FailCount = 0
$Runtime = "python"

function Say($msg) { Write-Host $msg }
function Fail($msg) { Write-Host "FAIL  $msg"; $script:FailCount = $script:FailCount + 1 }

# ---- Step 0: python runtime probe ----
# Do NOT hardcode a single interpreter name. Plenty of machines only ship `python3`
# (Linux/macOS, some CI images) and others only `py` (Windows launcher). A wrong
# default makes the whole install fail for a reason that looks unrelated.
function Test-PyWorks($exe) {
    if (-not $exe) { return $false }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        & $exe -c "import sys;sys.exit(0 if sys.version_info[0]==3 else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function Test-NodeWorks($exe) {
    if (-not $exe) { return $false }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        & $exe -e "process.exit(0)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
if ($Python -ne "") {
    if (-not (Test-PyWorks $Python)) {
        Write-Host "USAGE_ERROR -Python $Python is unavailable or not Python 3"
        exit 2
    }
} else {
    foreach ($cand in @("python", "python3", "py")) {
        if (Test-PyWorks $cand) { $Python = $cand; break }
    }
}
if ($Python -eq "") {
    # Missing Python is not an instant death sentence: Node still buys 4/5 gates with
    # real exit codes. The gap (reality_scan needs Python AST) must be stated out loud,
    # otherwise people believe they installed full enforcement.
    if ($Node -ne "") {
        if (-not (Test-NodeWorks $Node)) {
            Write-Host "USAGE_ERROR -Node $Node is unavailable"
            exit 2
        }
    } else {
        foreach ($cand in @("node", "nodejs")) {
            if (Test-NodeWorks $cand) { $Node = $cand; break }
        }
    }
    if ($Node -eq "") {
        Write-Host "FAIL  no Python 3 found (tried python / python3 / py) and no usable Node."
        Write-Host "      Hard gates run on one of those two runtimes; with neither, only soft"
        Write-Host "      constraints remain -- that is an EXPLICIT failure, not a silent downgrade."
        Write-Host "      Install Python 3 (preferred) or Node >=16, or pass -Python / -Node explicitly."
        exit 1
    }
    $Runtime = "node"
}

Say "=== Agent Core Suite installer ==="
Say "  suite     : $Suite"
if ($Runtime -eq "python") {
    Say "  runtime   : python -> $Python"
} else {
    Say "  runtime   : node -> $Node"
    Say "  WARN      : no Python 3 found, falling back to Node -- hard gates cover 4/5"
    Say "              (reality_scan needs Python AST). Explicit partial coverage."
}

if (-not (Test-Path -LiteralPath $Target)) {
    if ($DryRun) { Say "  [dry-run] would create target directory $Target" }
    else { New-Item -ItemType Directory -Path $Target -Force | Out-Null }
}
# -DryRun must not require the target to exist: Resolve-Path throws on a missing path,
# which used to make "preview before installing into a new workspace" impossible -- i.e.
# the one scenario -DryRun exists for.
#
# Cmdlets only, no [System.IO.Path]:: calls. Locked-down hosts run PowerShell in
# ConstrainedLanguage mode where .NET static method invocation is blocked outright,
# so a "cleaner" one-liner would crash exactly on the machines that need this script.
if (Test-Path -LiteralPath $Target) {
    $TargetFull = (Resolve-Path -LiteralPath $Target).Path
} elseif (($Target -match '^[A-Za-z]:[\\/]') -or ($Target -match '^\\\\')) {
    $TargetFull = $Target
} else {
    $TargetFull = Join-Path (Get-Location).Path $Target
}
Say "  workspace : $TargetFull"
Say "  mode      : $Mode"

# ---- Step 1: pre-install self check (inventory completeness) ----
Say ""
Say "[1/4] pre-install check"
$nodeGate = Join-Path $Suite "scripts\node\acs_gates.mjs"
if ($Runtime -eq "python") {
    $checkScript = Join-Path $Suite "scripts\install_check.py"
    if (-not (Test-Path -LiteralPath $checkScript)) {
        Fail "missing scripts\install_check.py - suite is incomplete, aborting"
        exit 1
    }
    $env:PYTHONUTF8 = "1"
    & $Python -X utf8 $checkScript --root $Suite
    if ($LASTEXITCODE -ne 0) {
        Fail "install_check failed (exit=$LASTEXITCODE), aborting"
        exit 1
    }
} else {
    if (-not (Test-Path -LiteralPath $nodeGate)) {
        Fail "missing scripts\node\acs_gates.mjs - Node fallback path is incomplete, aborting"
        exit 1
    }
    & $Node $nodeGate check --root $Suite
    if ($LASTEXITCODE -ne 0) {
        Fail "inventory check failed (exit=$LASTEXITCODE), aborting"
        exit 1
    }
}

# ---- Step 2: resolve install paths ----
# Skill dir and rule target are asked from acs_doctor. The definition lives only in
# spec/terminals.json; the installer is just an interpreter. Copying the mapping here
# would open a second drift path.
function Get-DoctorPaths {
    if ($Runtime -eq "python") {
        & $Python -X utf8 (Join-Path $Suite "scripts\acs_doctor.py") --paths --mode $Mode --target $TargetFull
    } else {
        & $Node $nodeGate doctor --paths --mode $Mode --target $TargetFull
    }
}
$pathsOut = Get-DoctorPaths
if ($LASTEXITCODE -ne 0) {
    Write-Host "USAGE_ERROR -Mode $Mode was rejected by acs_doctor (legal ids: spec/terminals.json)"
    exit 2
}
$Terminal = ""
$ModeLabel = ""
$RulesSrc = ""
$RulesDst = ""
$ModeNote = ""
foreach ($line in @($pathsOut)) {
    $text = "$line"
    $idx = $text.IndexOf("=")
    if ($idx -lt 1) { continue }
    $key = $text.Substring(0, $idx)
    $val = $text.Substring($idx + 1)
    switch ($key) {
        "TERMINAL"     { $Terminal = $val }
        "LABEL"        { $ModeLabel = $val }
        "SKILLS_DIR"   { if ($SkillHome -eq "") { $SkillHome = $val } }
        "RULES_SOURCE" { $RulesSrc = $val }
        "RULES_TARGET" { $RulesDst = $val }
        "NOTE"         { $ModeNote = $val }
    }
}
if (($SkillHome -eq "") -or ($RulesSrc -eq "") -or ($RulesDst -eq "")) {
    Fail "acs_doctor returned incomplete paths (mode=$Mode) - aborting instead of installing somewhere wrong"
    exit 1
}
$Mode = $Terminal
$AcsDir = Join-Path $TargetFull ".acs"
$AcsScripts = Join-Path $AcsDir "scripts"
$AcsTemplates = Join-Path $AcsDir "templates"
$AcsSpec = Join-Path $AcsDir "spec"

Say ""
Say "[2/4] install paths"
Say "  terminal  : $Mode ($ModeLabel)"
Say "  skills    -> $SkillHome"
Say "  rules     -> $RulesDst (source: $RulesSrc)"
Say "  gates     -> $AcsScripts"
Say "  templates -> $AcsTemplates"
if ($ModeNote -ne "") { Say "  note      : $ModeNote" }

function Ensure-Dir($path) {
    if ($DryRun) { return }
    if (-not (Test-Path -LiteralPath $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}

function Copy-One($src, $dst) {
    if (-not (Test-Path -LiteralPath $src)) { Fail "source missing: $src"; return }
    if ($DryRun) { Say "  [dry-run] $src -> $dst"; return }
    if (Test-Path -LiteralPath $dst) {
        # identical content -> nothing to do; DIFFERENT content without -Force is a real upgrade failure,
        # not a harmless skip. Staying silent here would let a stale version pass post-install checks.
        $same = $false
        try {
            $h1 = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
            $h2 = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
            $same = ($h1 -eq $h2)
        } catch {
            $same = $false
        }
        if ($same) { Say "  same  already up to date: $dst"; return }
        if (-not $Force) { Fail "outdated and NOT overwritten (content differs): $dst -- rerun with -Force to upgrade"; return }
    }
    Ensure-Dir (Split-Path -Parent $dst)
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Say "  ok    $dst"
}

# ---- Step 3: copy ----
Say ""
Say "[3/4] copy files"

$skills = @("universal-task-code", "loop-engineering", "graph-engineering", "self-verify-scaling", "token-thrift")
foreach ($name in $skills) {
    $srcDir = Join-Path $Suite "skills\$name"
    if (-not (Test-Path -LiteralPath $srcDir)) { Fail "missing skill directory: $srcDir"; continue }
    $dstDir = Join-Path $SkillHome $name
    Ensure-Dir $dstDir
    foreach ($item in Get-ChildItem -LiteralPath $srcDir -File) {
        Copy-One $item.FullName (Join-Path $dstDir $item.Name)
    }
}

# Rule target comes from spec/terminals.json. The single intentional exception: when the
# source is AGENTS.md the destination is a shared convention file the owner likely wrote
# into already -- do not clobber it, ask for a manual merge instead.
if (($RulesSrc -eq "AGENTS.md") -and (Test-Path -LiteralPath $RulesDst) -and (-not $Force)) {
    Say "  skip  $RulesDst exists - merge $Suite\AGENTS.md manually (or use -Force)"
    Say "        NOTE: this is the only intentional skip; it may leave an outdated file in place."
} else {
    Copy-One (Join-Path $Suite ($RulesSrc -replace "/", "\")) $RulesDst
}

$gateFiles = @("_acs_common.py", "gate_state_validate.py", "gate_loop_guard.py", "gate_verify_rank.py",
               "gate_reality_scan.py", "gate_checklist.py", "run_gates.py", "install_check.py",
               "acs_doctor.py")
foreach ($f in $gateFiles) { Copy-One (Join-Path $Suite "scripts\$f") (Join-Path $AcsScripts $f) }

$tplFiles = @("task-state.schema.json", "task-state.example.json", "verify-record.schema.json",
              "verify-record.example.json", "handoff-summary.md")
foreach ($f in $tplFiles) { Copy-One (Join-Path $Suite "templates\$f") (Join-Path $AcsTemplates $f) }

Copy-One (Join-Path $Suite "spec\thresholds.json") (Join-Path $AcsSpec "thresholds.json")
Copy-One (Join-Path $Suite "spec\terminals.json") (Join-Path $AcsSpec "terminals.json")

# Node gates: keeps a hard-enforcement path for "Node available, Python missing" environments
# (covers 4/5 gates; the reality_scan gap is declared explicitly, never silently downgraded).
Copy-One (Join-Path $Suite "scripts\node\acs_gates.mjs") (Join-Path $AcsScripts "node\acs_gates.mjs")

Copy-One (Join-Path $Suite "VERSION") (Join-Path $AcsDir "VERSION")
Copy-One (Join-Path $Suite "manifest.json") (Join-Path $AcsDir "manifest.json")

# External enforcement (L3), opt-in only. Dropping a hook into someone's repository
# without being asked is taking over, not augmenting. The escape hatch stays git-native:
# git commit --no-verify.
if ($WithHook) {
    $gitDir = Join-Path $TargetFull ".git"
    if (-not (Test-Path -LiteralPath $gitDir)) {
        Fail "-WithHook but target is not a git repository: $TargetFull (no .git, nothing to hook)"
    } else {
        # Windows does not carry a POSIX exec bit; Git for Windows runs hooks through its
        # bundled sh regardless, so no chmod step is possible or needed here.
        Copy-One (Join-Path $Suite "hooks\pre-commit") (Join-Path $gitDir "hooks\pre-commit")
    }
}

# ---- Step 4: post-install verification (really run the gates) ----
Say ""
Say "[4/4] post-install verification"
if ($DryRun) {
    Say "  [dry-run] verification skipped"
} else {
    foreach ($name in $skills) {
        $probe = Join-Path (Join-Path $SkillHome $name) "SKILL.md"
        if (Test-Path -LiteralPath $probe) { Say "  ok    skill readable: $probe" } else { Fail "skill not on disk: $probe" }
    }
    $sample = Join-Path $AcsTemplates "task-state.example.json"
    if ($Runtime -eq "python") {
        $runner = Join-Path $AcsScripts "run_gates.py"
        if ((Test-Path -LiteralPath $runner) -and (Test-Path -LiteralPath $sample)) {
            & $Python -X utf8 $runner --state $sample --root $AcsScripts --tier T2
            if ($LASTEXITCODE -eq 0) { Say "  ok    gates PASS on positive sample (exit=0) - hard constraints are live" }
            else { Fail "gates failed on positive sample (exit=$LASTEXITCODE) - install NOT effective" }
        } else {
            Fail "runner or positive sample missing - cannot verify"
        }
    } else {
        $runnerJs = Join-Path $AcsScripts "node\acs_gates.mjs"
        if ((Test-Path -LiteralPath $runnerJs) -and (Test-Path -LiteralPath $sample)) {
            & $Node $runnerJs run --state $sample --tier T2
            if ($LASTEXITCODE -eq 0) {
                Say "  ok    Node gates PASS on positive sample (exit=0) - hard constraints are live"
                Say "  PARTIAL 4/5 gates: reality_scan needs Python AST; the Node side does not fake it."
            } else {
                Fail "Node gates failed on positive sample (exit=$LASTEXITCODE) - install NOT effective"
            }
        } else {
            Fail "Node runner or positive sample missing - cannot verify"
        }
    }
}

Say ""
if ($FailCount -eq 0) {
    Say "RESULT: install complete (0 failures)"
    if ($ModeNote -ne "") { Say "HINT: $ModeNote" }
    Say "NEXT: copy $AcsTemplates\task-state.example.json to $AcsDir\task-state.json and rewrite it for the real task."
    exit 0
} else {
    Say "RESULT: install FAILED ($FailCount failures) - fix and rerun"
    exit 1
}
