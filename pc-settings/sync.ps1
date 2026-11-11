# Sync per-PC settings from this repo into the current user's profile.
#   1) ~/.claude/CLAUDE.md : block between markers <- pc-settings/claude-global.md
#   2) ~/.codex/config.toml: top-level model + model_reasoning_effort = "ultra"
#   3) ~/.codex/AGENTS.md  : block between markers <- pc-settings/codex-global.md
# Runs at Claude Code session start (.claude/settings.json) and from 9_*.bat.
# Writes only when something differs. Keeps a one-time .bak before the first change.
# This file is ASCII only (Windows PowerShell 5.1 reads BOM-less files as ANSI).

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$changes = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Normalize([string]$s) { return ($s -replace "`r`n", "`n").Trim() }

function Backup-Once([string]$path) {
    $bak = "$path.bak"
    if ((Test-Path -LiteralPath $path) -and -not (Test-Path -LiteralPath $bak)) {
        Copy-Item -LiteralPath $path -Destination $bak
    }
}

# ---------- shared: copy a repo file into a marked block of a profile file ----------
function Sync-Block([string]$srcName, [string]$dir, [string]$fileName, [string]$label) {
    try {
        $src = Join-Path $PSScriptRoot $srcName
        $block = Normalize ([IO.File]::ReadAllText($src, $utf8))
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

        # Model: gpt-6.1-sol, unless this account cannot use it (then gpt-6-astra).
        $wantModel = 'gpt-6.1-sol'
        $cache = Join-Path $codexDir 'models_cache.json'
        if ($curModel -eq 'gpt-6-astra') {
            $wantModel = 'gpt-6-astra'
        } elseif (Test-Path -LiteralPath $cache) {
            $c = [IO.File]::ReadAllText($cache, $utf8)
            if (-not $c.Contains('"gpt-6.1-sol"') -and $c.Contains('"gpt-6-astra"')) { $wantModel = 'gpt-6-astra' }
        }

        $modelLine = "model = `"$wantModel`""
        $effortLine = 'model_reasoning_effort = "ultra"'
        $before = ($lines -join "`n")

        if ($effortIdx -ge 0) { $lines[$effortIdx] = $effortLine } else { $lines.Insert(0, $effortLine); if ($modelIdx -ge 0) { $modelIdx++ } }
        if ($modelIdx -ge 0) { $lines[$modelIdx] = $modelLine } else { $lines.Insert(0, $modelLine) }

        if (($lines -join "`n") -ne $before) {
            Backup-Once $cfg
            $out = ($lines -join $nl)
            if (-not $out.EndsWith($nl)) { $out += $nl }
            [IO.File]::WriteAllText($cfg, $out, $utf8)
            $changes.Add("~/.codex/config.toml set to $wantModel + ultra")
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
