# Sync per-PC settings from this repo into the current user's profile.
#   1) ~/.claude/CLAUDE.md : block between markers <- pc-settings/claude-global.md
#   2) ~/.codex/config.toml: top-level model (gpt-6.2-sol) + model_reasoning_effort = "max"
#   3) ~/.codex/AGENTS.md  : block between markers <- pc-settings/codex-global.md
# Runs at Claude Code session start (.claude/settings.json) and from 9_*.bat.
# Rule files are read from GitHub origin/main when possible (see section 0), so no pull is needed.
# Writes only when something differs. Keeps a one-time .bak before the first change.
# This file is ASCII only (Windows PowerShell 5.1 reads BOM-less files as ANSI).

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$changes = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Normalize([string]$s) { return ($s -replace "`r`n", "`n").Trim() }

# ---------- 0) Get the newest rules from GitHub (2026-11-20) ----------
# Fetch origin/main (never touches the working tree or the current branch) and read the
# pc-settings sources from there, so every PC gets new rules without a manual pull.
# Uses the local file instead when: offline / fetch fails or is slow, the local file has
# uncommitted edits, or this checkout has commits origin/main does not have yet.
$repoRoot = Split-Path -Parent $PSScriptRoot
$useRemote = $false

function Invoke-Git([string[]]$gitArgs, [int]$timeoutMs) {
    # Returns the exit code, or -1 on timeout / git missing.
    $o = [IO.Path]::GetTempFileName(); $e = [IO.Path]::GetTempFileName()
    try {
        $quoted = @('-C', "`"$repoRoot`"") + $gitArgs
        $p = Start-Process -FilePath 'git' -ArgumentList $quoted -NoNewWindow -PassThru `
            -RedirectStandardOutput $o -RedirectStandardError $e
        $null = $p.Handle  # PS 5.1 quirk: without this, ExitCode stays empty
        if (-not $p.WaitForExit($timeoutMs)) { try { $p.Kill() } catch {}; return -1 }
        $p.WaitForExit()  # make ExitCode available
        return $p.ExitCode
    } catch { return -1 }
    finally { Remove-Item -LiteralPath $o, $e -ErrorAction SilentlyContinue }
}

$env:GIT_TERMINAL_PROMPT = '0'
if ((Invoke-Git @('fetch', '-q', 'origin', 'main') 15000) -eq 0) {
    # Use GitHub only when this checkout is not ahead of it (HEAD is already inside origin/main).
    if ((Invoke-Git @('merge-base', '--is-ancestor', 'HEAD', 'origin/main') 5000) -eq 0) { $useRemote = $true }
} else {
    $warnings.Add('Could not get the newest rules from GitHub (offline?). Used the files on this PC.')
}

function Get-SourceText([string]$srcName) {
    $local = Join-Path $PSScriptRoot $srcName
    if ($useRemote) {
        # Local uncommitted edits win (exit 1 = file differs from HEAD).
        if ((Invoke-Git @('diff', '--quiet', 'HEAD', '--', "pc-settings/$srcName") 5000) -eq 0) {
            $tmp = [IO.Path]::GetTempFileName()
            try {
                # cmd redirection keeps the UTF-8 bytes exactly (PowerShell pipes would re-encode).
                & cmd.exe /d /c "git -C `"$repoRoot`" show origin/main:pc-settings/$srcName > `"$tmp`" 2>nul"
                if ($LASTEXITCODE -eq 0 -and (Get-Item -LiteralPath $tmp).Length -gt 0) {
                    return [IO.File]::ReadAllText($tmp, $utf8)
                }
            } catch {
            } finally { Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
        }
    }
    return [IO.File]::ReadAllText($local, $utf8)
}

function Backup-Once([string]$path) {
    $bak = "$path.bak"
    if ((Test-Path -LiteralPath $path) -and -not (Test-Path -LiteralPath $bak)) {
        Copy-Item -LiteralPath $path -Destination $bak
    }
}

# ---------- shared: copy a repo file into a marked block of a profile file ----------
function Sync-Block([string]$srcName, [string]$dir, [string]$fileName, [string]$label) {
    try {
        $block = Normalize (Get-SourceText $srcName)
        $begin = "<!-- cb-messenger-ai pc-settings BEGIN: auto-synced from the repo file pc-settings/$srcName. Edit that file, not this block. -->"
        $end = '<!-- cb-messenger-ai pc-settings END -->'
        $managed = "$begin`n$block`n$end"

        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        $target = Join-Path $dir $fileName

        $old = ''
        if (Test-Path -LiteralPath $target) { $old = Normalize ([IO.File]::ReadAllText($target, $utf8)) }

        $bi = $old.IndexOf($begin); $ei = $old.IndexOf($end)
        if ($bi -ge 0 -and $ei -gt $bi) {
            # Replace only the managed block; keep anything the user wrote around it.
            $new = $old.Substring(0, $bi) + $managed + $old.Substring($ei + $end.Length)
        } elseif ($old -eq '' -or $old -eq $block) {
            $new = $managed
        } else {
            # Unmarked file with different content: keep it below, after a backup.
            $new = $managed + "`n`n" + $old
            $warnings.Add("$label had other content; kept it below the synced block (backup: $fileName.bak).")
        }
        $new = (Normalize $new) + "`n"

        if ((Normalize $new) -ne $old) {
            Backup-Once $target
            [IO.File]::WriteAllText($target, ($new -replace "`n", "`r`n"), $utf8)
            $changes.Add("$label updated from pc-settings/$srcName")
        }
    } catch {
        $warnings.Add("$label sync failed: $($_.Exception.Message)")
    }
}

# ---------- 1) Claude global CLAUDE.md ----------
Sync-Block 'claude-global.md' (Join-Path $env:USERPROFILE '.claude') 'CLAUDE.md' '~/.claude/CLAUDE.md'

# ---------- 2) Codex config.toml ----------
try {
    $codexDir = Join-Path $env:USERPROFILE '.codex'
    if (-not (Test-Path -LiteralPath $codexDir)) {
        $warnings.Add('Codex has not been run on this PC yet (~/.codex missing). Run Codex once, then run 9_PC settings bat again.')
    } else {
        $cfg = Join-Path $codexDir 'config.toml'
        $text = ''
        if (Test-Path -LiteralPath $cfg) { $text = [IO.File]::ReadAllText($cfg, $utf8) }
        $nl = "`n"; if ($text.Contains("`r`n")) { $nl = "`r`n" }
        $lines = New-Object System.Collections.Generic.List[string]
        if ($text -ne '') { foreach ($l in ($text -split "`r?`n")) { $lines.Add($l) } }

        # Top-level keys live before the first [table] header.
        $top = $lines.Count
        for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match '^\s*\[') { $top = $i; break } }

        $modelIdx = -1; $effortIdx = -1; $curModel = $null
        for ($i = 0; $i -lt $top; $i++) {
            if ($lines[$i] -match '^\s*model\s*=\s*"([^"]*)"') { $modelIdx = $i; $curModel = $Matches[1] }
            elseif ($lines[$i] -match '^\s*model_reasoning_effort\s*=') { $effortIdx = $i }
        }

        # Model: gpt-6.2-sol + max on every PC (2026-11-18 user decision).
        # Hard problems use gpt-6.2-astra + xhigh, chosen per call with --model/--effort (not here).
        # Fallback to gpt-5.6-terra only when Codex's model list has no gpt-6.2-sol at all.
        $wantModel = 'gpt-6.2-sol'
        $cache = Join-Path $codexDir 'models_cache.json'
        if (Test-Path -LiteralPath $cache) {
            $c = [IO.File]::ReadAllText($cache, $utf8)
            if (-not $c.Contains('"gpt-6.2-sol"') -and $c.Contains('"gpt-5.6-terra"')) { $wantModel = 'gpt-5.6-terra' }
        }

        # gpt-6.2 needs a recent Codex CLI (0.167+ worked) (0.154 is rejected by the server). Warn only; never auto-update.
        try {
            $verOut = (& codex --version 2>$null | Out-String)
            if ($verOut -match '(\d+)\.(\d+)\.(\d+)') {
                if ([int]$Matches[1] -eq 0 -and [int]$Matches[2] -lt 167) {
                    $warnings.Add("Codex CLI is too old for gpt-6.2 ($($verOut.Trim())). Run 'codex update' in PowerShell.")
                }
            }
        } catch {}

        $modelLine = "model = `"$wantModel`""
        $effortLine = 'model_reasoning_effort = "max"'
        $before = ($lines -join "`n")

        if ($effortIdx -ge 0) { $lines[$effortIdx] = $effortLine } else { $lines.Insert(0, $effortLine); if ($modelIdx -ge 0) { $modelIdx++ } }
        if ($modelIdx -ge 0) { $lines[$modelIdx] = $modelLine } else { $lines.Insert(0, $modelLine) }

        if (($lines -join "`n") -ne $before) {
            Backup-Once $cfg
            $out = ($lines -join $nl)
            if (-not $out.EndsWith($nl)) { $out += $nl }
            [IO.File]::WriteAllText($cfg, $out, $utf8)
            $changes.Add("~/.codex/config.toml set to $wantModel + max")
        }
    }
} catch {
    $warnings.Add("Codex config sync failed: $($_.Exception.Message)")
}

# ---------- 3) Codex global AGENTS.md ----------
# Codex reads ~/.codex/AGENTS.md for every session (Desktop, CLI, and calls from Claude Code).
if (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.codex')) {
    Sync-Block 'codex-global.md' (Join-Path $env:USERPROFILE '.codex') 'AGENTS.md' '~/.codex/AGENTS.md'
}

# ---------- report ----------
# Silent when nothing changed, so session start stays quiet.
foreach ($c in $changes) { Write-Output "[pc-settings] $c" }
foreach ($w in $warnings) { Write-Output "[pc-settings] WARNING: $w" }
if ($changes.Count -gt 0) { Write-Output '[pc-settings] Restart Claude Code / Codex for the new settings to take effect.' }
exit 0
