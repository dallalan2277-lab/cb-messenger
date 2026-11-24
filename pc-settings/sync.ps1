# Sync per-PC settings from this repo into the current user's profile.
#   1) ~/.claude/CLAUDE.md : block between markers <- pc-settings/claude-global.md
#   2) ~/.codex/config.toml: top-level model (gpt-6.2-sol) + model_reasoning_effort = "max"
#   3) ~/.codex/AGENTS.md  : block between markers <- pc-settings/codex-global.md
#   5) ~/.claude/skills/  : skill folders from this repo and a sibling ai-bridge checkout
# Runs at Claude Code session start (.claude/settings.json) and from 9_*.bat.
# Uses origin/main for rules and updates itself from there (see section 0).
# Writes only when something differs. Keeps a one-time .bak before the first change.
# This file is ASCII only (Windows PowerShell 5.1 reads BOM-less files as ANSI).

param(
    [switch]$Local,
    [string]$RepoRoot,
    # Internal: the downloaded script uses the same revisions and skips the bootstrap.
    [ValidatePattern('^[0-9a-f]{40}([0-9a-f]{24})?$')]
    [string]$OriginMainRevision,
    [ValidatePattern('^[0-9a-f]{40}([0-9a-f]{24})?$')]
    [string]$BridgeMainRevision
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)

# The two startup hooks can overlap. Keep the lock only while a sync is running,
# so an explicit second run can pick up new commits or switch back from -Local.
$syncLock = $null
if (-not $OriginMainRevision) {
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $created = $false
    $syncLock = [Threading.Mutex]::new($false, "Local\cb-messenger-pc-settings-sync-$userSid", [ref]$created)
    if (-not $created) { $syncLock.Dispose(); exit 0 }
}
$changes = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Normalize([string]$s) { return ($s -replace "`r`n", "`n").Trim() }

# ---------- 0) Select the sources and run a newer script from origin/main ----------
if ($repoRoot) { $repoRoot = (Resolve-Path -LiteralPath $repoRoot).ProviderPath }
else { $repoRoot = Split-Path -Parent $PSScriptRoot }
$repoSettingsDir = Join-Path $repoRoot 'pc-settings'
$bridgeRoot = Join-Path (Split-Path -Parent $repoRoot) 'ai-bridge'
$bridgeUsesGit = Test-Path -LiteralPath (Join-Path $bridgeRoot '.git')

function Start-Git([string]$gitRoot, [string[]]$gitArgs, [string]$outputPath) {
    $p = [Diagnostics.Process]::new()
    $p.StartInfo.FileName = 'git'
    $p.StartInfo.WorkingDirectory = $gitRoot
    # Only fixed options, tree paths and verified Git object IDs are passed here.
    $p.StartInfo.Arguments = $gitArgs -join ' '
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    $output = [IO.Stream]::Null
    try {
        if ($outputPath) { $output = [IO.File]::Create($outputPath) }
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $null = $p.Start()
        $stdout = $p.StandardOutput.BaseStream.CopyToAsync($output)
        $null = $p.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        return @{ Process = $p; Timer = $timer; Output = $output; Stdout = $stdout }
    } catch {
        $output.Dispose()
        $p.Dispose()
    }
}

function Wait-Git([hashtable]$run, [int]$timeoutMs) {
    # Returns the exit code, or -1 on timeout / git missing.
    if (-not $run) { return -1 }
    $p = $run.Process
    try {
        $remainingMs = [Math]::Max(0, $timeoutMs - $run.Timer.ElapsedMilliseconds)
        if (-not $p.WaitForExit([int]$remainingMs)) {
            try { $p.Kill() } catch {}
            return -1
        }
        # A child process may keep a pipe open. Bound the drain by the same deadline.
        $remainingMs = [Math]::Max(0, $timeoutMs - $run.Timer.ElapsedMilliseconds)
        if (-not $run.Stdout.Wait([int]$remainingMs)) { return -1 }
        return $p.ExitCode
    } catch { return -1 }
    finally {
        $p.Dispose()
        $run.Output.Dispose()
    }
}

function Invoke-Git([string]$gitRoot, [string[]]$gitArgs, [string]$outputPath) {
    return (Wait-Git (Start-Git $gitRoot $gitArgs $outputPath) 5000)
}

function Get-OriginMainRevision([string]$gitRoot) {
    $tmp = [IO.Path]::GetTempFileName()
    try {
        if ((Invoke-Git $gitRoot @('show-ref', '--verify', '--hash', 'refs/remotes/origin/main') $tmp) -eq 0) {
            return [IO.File]::ReadAllText($tmp, $utf8).Trim()
        }
    } finally { Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
}

$remoteRevision = $null
$bridgeRevision = $null
if (-not $Local) {
    $env:GIT_TERMINAL_PROMPT = '0'
    $remoteRevision = $OriginMainRevision
    $bridgeRevision = $BridgeMainRevision
    if (-not $remoteRevision) {
        # Start both fetches before waiting so slow remotes share the hook's time budget.
        $fetchArgs = @('fetch', '-q', '--no-tags', '--refmap=', 'origin', '+refs/heads/main:refs/remotes/origin/main')
        $repoFetch = Start-Git $repoRoot $fetchArgs
        $bridgeFetch = if ($bridgeUsesGit) { Start-Git $bridgeRoot $fetchArgs }
        $null = Wait-Git $repoFetch 15000
        $bridgeFetchExit = Wait-Git $bridgeFetch 15000

        # The main repo can use its cached ref offline. ai-bridge falls back to its local files.
        $remoteRevision = Get-OriginMainRevision $repoRoot
        if ($bridgeFetchExit -eq 0) { $bridgeRevision = Get-OriginMainRevision $bridgeRoot }

        if ($remoteRevision) {
            $remoteScript = Join-Path ([IO.Path]::GetTempPath()) "cb-messenger-sync-$([Guid]::NewGuid()).ps1"
            try {
                if ((Invoke-Git $repoRoot @('cat-file', 'blob', "${remoteRevision}:pc-settings/sync.ps1") $remoteScript) -eq 0) {
                    # The checkout normally has CRLF while the Git blob has LF.
                    $running = [IO.File]::ReadAllText($PSCommandPath, $utf8).Replace("`r`n", "`n")
                    $downloaded = [IO.File]::ReadAllText($remoteScript, $utf8).Replace("`r`n", "`n")
                    if (-not $running.Equals($downloaded, [StringComparison]::Ordinal)) {
                        $scriptArgs = @('-RepoRoot', $repoRoot, '-OriginMainRevision', $remoteRevision)
                        if ($bridgeRevision) { $scriptArgs += @('-BridgeMainRevision', $bridgeRevision) }
                        try {
                            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $remoteScript @scriptArgs
                            exit $LASTEXITCODE
                        } finally { if ($syncLock) { $syncLock.Dispose() } }
                    }
                }
            } finally { Remove-Item -LiteralPath $remoteScript -ErrorAction SilentlyContinue }
        }
    }
}

function Get-SourceText([string]$srcName) {
    if (-not $remoteRevision) { return [IO.File]::ReadAllText((Join-Path $repoSettingsDir $srcName), $utf8) }

    $tmp = [IO.Path]::GetTempFileName()
    try {
        if ((Invoke-Git $repoRoot @('cat-file', 'blob', "${remoteRevision}:pc-settings/$srcName") $tmp) -ne 0) {
            throw "Could not read pc-settings/$srcName from origin/main."
        }
        return [IO.File]::ReadAllText($tmp, $utf8)
    } finally { Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
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

        if ((Normalize $new) -cne $old) {
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

# ---------- 4) Global Claude hook: run this script at every Claude Code start (2026-11-20) ----------
# The project hook only fires in cb-messenger sessions. Register this script (at its current path)
# as a SessionStart hook in ~/.claude/settings.json so sessions in ANY folder get the newest rules.
try {
    $claudeDir = Join-Path $env:USERPROFILE '.claude'
    $settingsPath = Join-Path $claudeDir 'settings.json'
    $myPath = Join-Path $repoSettingsDir 'sync.ps1'
    $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$myPath`""

    $raw = ''
    if (Test-Path -LiteralPath $settingsPath) { $raw = [IO.File]::ReadAllText($settingsPath, $utf8) }
    if ($raw.Trim() -eq '') { $s = New-Object psobject } else { $s = $raw | ConvertFrom-Json }

    # Collect SessionStart entries, dropping any older registration of this script (e.g. repo moved).
    $entries = New-Object System.Collections.Generic.List[object]
    $already = $false
    if ($s.PSObject.Properties['hooks'] -and $s.hooks.PSObject.Properties['SessionStart']) {
        foreach ($en in @($s.hooks.SessionStart)) {
            if ($null -eq $en) { continue }
            $mine = $false
            foreach ($h in @($en.hooks)) {
                if ($h -and $h.command -and ($h.command -like '*pc-settings*sync.ps1*')) {
                    $mine = $true
                    if ($h.command -eq $cmd) { $already = $true }
                }
            }
            if (-not $mine) { $entries.Add($en) }
        }
    }

    if (-not $already) {
        $hook = [pscustomobject]@{ type = 'command'; command = $cmd; timeout = 30 }
        $entries.Add([pscustomobject]@{ matcher = 'startup'; hooks = @($hook) })
        if (-not $s.PSObject.Properties['hooks']) { $s | Add-Member -NotePropertyName hooks -NotePropertyValue (New-Object psobject) }
        if ($s.hooks.PSObject.Properties['SessionStart']) { $s.hooks.SessionStart = $entries.ToArray() }
        else { $s.hooks | Add-Member -NotePropertyName SessionStart -NotePropertyValue $entries.ToArray() }

        $json = $s | ConvertTo-Json -Depth 32
        $null = $json | ConvertFrom-Json   # must still parse before we write
        if (-not (Test-Path -LiteralPath $claudeDir)) { New-Item -ItemType Directory -Path $claudeDir | Out-Null }
        Backup-Once $settingsPath
        [IO.File]::WriteAllText($settingsPath, $json, $utf8)
        $changes.Add('~/.claude/settings.json: rules sync now runs at every Claude Code start (any folder)')
    }
} catch {
    $warnings.Add("Global hook setup skipped (settings.json left as is): $($_.Exception.Message)")
}

# ---------- 5) Claude personal skills ----------
function Copy-SkillFile([string]$source, [string]$target) {
    $exists = [IO.File]::Exists($target)
    if ([IO.Path]::GetExtension($target) -in @('.md', '.txt', '.py', '.ps1', '.js', '.json', '.yml', '.yaml', '.toml')) {
        $new = [IO.File]::ReadAllText($source, $utf8).Replace("`r`n", "`n")
        if ($exists) {
            $old = [IO.File]::ReadAllText($target, $utf8).Replace("`r`n", "`n")
            if ($new.Equals($old, [StringComparison]::Ordinal)) { return $false }
        }
        $bytes = $utf8.GetBytes($new.Replace("`n", "`r`n"))
    } else {
        $bytes = [IO.File]::ReadAllBytes($source)
        if ($exists -and [Collections.StructuralComparisons]::StructuralEqualityComparer.Equals(
            $bytes, [IO.File]::ReadAllBytes($target))) { return $false }
    }

    $null = [IO.Directory]::CreateDirectory((Split-Path -Parent $target))
    Backup-Once $target
    [IO.File]::WriteAllBytes($target, $bytes)
    return $true
}

function Add-LocalSkillFiles([string]$dir, [string]$prefix, [hashtable]$files) {
    foreach ($item in (Get-ChildItem -LiteralPath $dir -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $relative = $prefix + $item.Name
        if ($item.PSIsContainer) { Add-LocalSkillFiles $item.FullName "$relative/" $files }
        else { $files[$relative] = $item.FullName }
    }
}

function Get-RemoteSkills([string]$gitRoot, [string]$skillPath, [string]$revision, [string]$sourceLabel) {
    $skills = @{}
    if ((Invoke-Git $gitRoot @('ls-tree', '-r', '-z', $revision, '--', $skillPath) $skillGitOutput) -ne 0) {
        $warnings.Add("Could not list Claude skills from $sourceLabel origin/main.")
        return $skills
    }

    # NUL separators preserve spaces and Unicode. Only regular Git blobs are copied.
    $pathPattern = [regex]::Escape($skillPath)
    $entryPattern = "^100[0-7]{3} blob ([0-9a-f]+)`t$pathPattern/([^/]+)/(.+)$"
    $invalidNameChars = [IO.Path]::GetInvalidFileNameChars()
    foreach ($entry in [IO.File]::ReadAllText($skillGitOutput, $utf8).Split([char]0)) {
        if ($entry -cnotmatch $entryPattern) { continue }
        $objectId = $Matches[1]
        $name = $Matches[2]
        $relative = $Matches[3]
        $invalidParts = @((@($name) + $relative.Split('/')) | Where-Object {
            $_ -eq '' -or $_.IndexOfAny($invalidNameChars) -ge 0 -or $_.EndsWith('.') -or $_.EndsWith(' ')
        })
        if ($invalidParts.Count -gt 0) {
            $warnings.Add("Skill file has an invalid Windows path. Skipped: $skillPath/$name/$relative")
            continue
        }
        if (-not $skills.ContainsKey($name)) { $skills[$name] = @{} }
        $skills[$name][$relative] = $objectId
    }
    return $skills
}

function Sync-Skill([string]$name, [string]$sourceRoot, [string]$sourceLabel, [hashtable]$files, [string]$gitRoot) {
    $label = "~/.claude/skills/$name"
    $targetDir = Join-Path $claudeSkillsDir $name
    $updated = $false
    try {
        $item = Get-Item -LiteralPath $targetDir -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $warnings.Add("$label is a symlink or junction. Skipped.")
            return
        }

        if (-not $gitRoot) {
            $localDir = Get-Item -LiteralPath (Join-Path $sourceRoot $name) -Force -ErrorAction SilentlyContinue
            if (-not $localDir -or ($localDir.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
            Add-LocalSkillFiles $localDir.FullName '' $files
        }

        :skillFiles foreach ($relative in ($files.Keys | Sort-Object)) {
            # A nested link can also redirect a write outside the skill.
            $target = $targetDir
            foreach ($part in $relative.Split('/')) {
                $target = Join-Path $target $part
                $item = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
                if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    $warnings.Add("$label/$relative uses a symlink or junction. Skipped.")
                    continue skillFiles
                }
            }

            $source = $files[$relative]
            if ($gitRoot) {
                if ((Invoke-Git $gitRoot @('cat-file', 'blob', $source) $skillGitOutput) -ne 0) {
                    $warnings.Add("Could not read $label/$relative from $sourceLabel origin/main.")
                    continue
                }
                $source = $skillGitOutput
            }
            if (-not [IO.File]::Exists($source)) { continue }
            if (Copy-SkillFile $source $target) { $updated = $true }
        }
    } catch {
        $warnings.Add("$label sync failed: $($_.Exception.Message)")
    } finally {
        if ($updated) { $changes.Add("$label updated from $sourceLabel") }
    }
}

$skillGitOutput = $null
try {
    $claudeSkillsDir = Join-Path $env:USERPROFILE '.claude\skills'
    $skillsDirItem = Get-Item -LiteralPath $claudeSkillsDir -Force -ErrorAction SilentlyContinue
    if ($skillsDirItem -and ($skillsDirItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw '~/.claude/skills is a symlink or junction. Skipped.'
    }

    if ($remoteRevision -or $bridgeRevision) { $skillGitOutput = [IO.Path]::GetTempFileName() }
    $sources = @(
        @{ Root = $repoRoot; Path = '.claude/skills'; Label = 'cb-messenger'; Revision = $remoteRevision }
        @{ Root = $bridgeRoot; Path = 'tools/claude/skills'; Label = 'ai-bridge'; Revision = $bridgeRevision }
    )
    $usedNames = @{}
    foreach ($source in $sources) {
        $sourceDir = Join-Path $source.Root $source.Path
        $gitRoot = $null
        if ($source.Revision) {
            $gitRoot = $source.Root
            $skills = Get-RemoteSkills $gitRoot $source.Path $source.Revision $source.Label
        } else {
            $skills = @{}
            $item = Get-Item -LiteralPath $sourceDir -Force -ErrorAction SilentlyContinue
            if ($item -and $item.PSIsContainer -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                foreach ($dir in (Get-ChildItem -LiteralPath $sourceDir -Directory -Force)) {
                    if (-not ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $skills[$dir.Name] = @{} }
                }
            }
        }

        foreach ($name in ($skills.Keys | Sort-Object)) {
            if ($usedNames.ContainsKey($name)) {
                $warnings.Add("Claude skill '$name' is in both repositories. Using cb-messenger.")
            } else {
                $usedNames[$name] = $true
                Sync-Skill $name $sourceDir $source.Label $skills[$name] $gitRoot
            }
        }
    }
} catch {
    $warnings.Add("Claude skills sync failed: $($_.Exception.Message)")
} finally {
    if ($skillGitOutput) { Remove-Item -LiteralPath $skillGitOutput -ErrorAction SilentlyContinue }
}

# ---------- report ----------
# Report local fallbacks only when settings were applied, so offline starts stay quiet.
if ($changes.Count -gt 0 -and -not $Local) {
    if (-not $remoteRevision) { $warnings.Add('No origin/main ref for cb-messenger. Used the files on this PC.') }
    if ($bridgeUsesGit -and -not $bridgeRevision) {
        $warnings.Add('Could not get origin/main for ai-bridge. Used the files on this PC.')
    }
}
foreach ($c in $changes) { Write-Output "[pc-settings] $c" }
foreach ($w in $warnings) { Write-Output "[pc-settings] WARNING: $w" }
if ($changes.Count -gt 0) { Write-Output '[pc-settings] Restart Claude Code / Codex for the new settings to take effect.' }
if ($syncLock) { $syncLock.Dispose() }
exit 0
