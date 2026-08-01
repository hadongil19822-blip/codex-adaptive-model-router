Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Continue"
$RuntimeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateFile = Join-Path $RuntimeRoot "runtime\state.json"
$ConfigFile = Join-Path $RuntimeRoot "router_config.json"
$RouterScript = Join-Path $RuntimeRoot "codex_router.py"
$PythonPathFile = Join-Path $RuntimeRoot "python.path"
$Python = if (Test-Path $PythonPathFile) { (Get-Content $PythonPathFile -Raw).Trim() } else { "python.exe" }

function New-Label([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height, [float]$Size = 9, [bool]$Bold = $false) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size($Width, $Height)
    $style = if ($Bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    $label.Font = New-Object System.Drawing.Font("Segoe UI", $Size, $style)
    $label.ForeColor = [System.Drawing.Color]::FromArgb(225, 235, 245)
    $label.BackColor = [System.Drawing.Color]::Transparent
    return $label
}

$Form = New-Object System.Windows.Forms.Form
$Form.Text = "Codex Adaptive Model Router"
$Form.Size = New-Object System.Drawing.Size(470, 650)
$Form.MinimumSize = New-Object System.Drawing.Size(470, 650)
$Form.StartPosition = "CenterScreen"
$Form.BackColor = [System.Drawing.Color]::FromArgb(15, 20, 32)
$Form.ForeColor = [System.Drawing.Color]::White
$Form.Icon = [System.Drawing.SystemIcons]::Application

$Title = New-Label "Codex Adaptive Model Router" 20 18 330 28 14 $true
$Subtitle = New-Label "Local, zero-token routing for every task" 20 48 360 20 9 $false
$Subtitle.ForeColor = [System.Drawing.Color]::FromArgb(70, 205, 220)
$Status = New-Label "CHECKING STATUS" 345 22 105 22 8 $true
$Status.TextAlign = "MiddleRight"

$UsageTitle = New-Label "WEEKLY CODEX USAGE" 20 88 260 18 9 $true
$UsageValue = New-Label "Usage unavailable" 20 108 300 35 18 $true
$UsageReset = New-Label "Reset time unavailable" 20 143 410 18 8 $false
$UsageReset.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)
$UsageBar = New-Object System.Windows.Forms.ProgressBar
$UsageBar.Location = New-Object System.Drawing.Point(20, 168)
$UsageBar.Size = New-Object System.Drawing.Size(410, 10)
$UsageBar.Minimum = 0
$UsageBar.Maximum = 100

$GuardCheck = New-Object System.Windows.Forms.CheckBox
$GuardCheck.Text = "Pause new work when weekly usage is low"
$GuardCheck.Location = New-Object System.Drawing.Point(20, 195)
$GuardCheck.Size = New-Object System.Drawing.Size(320, 24)
$GuardCheck.ForeColor = [System.Drawing.Color]::White
$GuardCheck.BackColor = [System.Drawing.Color]::Transparent

$ThresholdLabel = New-Label "Pause at" 42 225 65 24 9 $false
$Threshold = New-Object System.Windows.Forms.NumericUpDown
$Threshold.Location = New-Object System.Drawing.Point(110, 224)
$Threshold.Size = New-Object System.Drawing.Size(65, 24)
$Threshold.Minimum = 1
$Threshold.Maximum = 50
$Threshold.Value = 10
$Threshold.DecimalPlaces = 0
$PercentLabel = New-Label "% remaining" 180 225 100 24 9 $false
$SaveGuard = New-Object System.Windows.Forms.Button
$SaveGuard.Text = "Save settings"
$SaveGuard.Location = New-Object System.Drawing.Point(320, 221)
$SaveGuard.Size = New-Object System.Drawing.Size(110, 30)

$GuardNote = New-Label "Safe mode: active turns finish; new prompts and automatic follow-ups pause." 20 258 420 32 8 $false
$GuardNote.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)

$TasksTitle = New-Label "ACTIVE TASKS" 20 302 200 20 9 $true
$Tasks = New-Object System.Windows.Forms.TextBox
$Tasks.Location = New-Object System.Drawing.Point(20, 326)
$Tasks.Size = New-Object System.Drawing.Size(410, 205)
$Tasks.Multiline = $true
$Tasks.ReadOnly = $true
$Tasks.ScrollBars = "Vertical"
$Tasks.BackColor = [System.Drawing.Color]::FromArgb(25, 31, 46)
$Tasks.ForeColor = [System.Drawing.Color]::FromArgb(220, 230, 240)
$Tasks.BorderStyle = "FixedSingle"
$Tasks.Font = New-Object System.Drawing.Font("Consolas", 9)

$ToggleRouter = New-Object System.Windows.Forms.Button
$ToggleRouter.Text = "Start routing"
$ToggleRouter.Location = New-Object System.Drawing.Point(20, 550)
$ToggleRouter.Size = New-Object System.Drawing.Size(200, 36)
$OpenFolder = New-Object System.Windows.Forms.Button
$OpenFolder.Text = "Open router folder"
$OpenFolder.Location = New-Object System.Drawing.Point(230, 550)
$OpenFolder.Size = New-Object System.Drawing.Size(200, 36)
$Message = New-Label "Ready" 20 594 410 22 8 $false
$Message.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)

$Form.Controls.AddRange(@(
    $Title, $Subtitle, $Status, $UsageTitle, $UsageValue, $UsageReset, $UsageBar,
    $GuardCheck, $ThresholdLabel, $Threshold, $PercentLabel, $SaveGuard, $GuardNote,
    $TasksTitle, $Tasks, $ToggleRouter, $OpenFolder, $Message
))

$script:WatcherAlive = $false
$script:GuardControlsLoaded = $false

function Test-ProcessAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Invoke-Router([string[]]$Arguments) {
    try {
        $quotedScript = '"' + $RouterScript + '"'
        $process = Start-Process -FilePath $Python -ArgumentList (@($quotedScript) + $Arguments) -WindowStyle Hidden -PassThru -Wait
        $Message.Text = if ($process.ExitCode -eq 0) { "Command completed." } else { "Command failed with exit code $($process.ExitCode)." }
    } catch {
        $Message.Text = "Router command failed: $($_.Exception.Message)"
    }
}

function Set-ObjectProperty($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Save-GuardSettings {
    try {
        $config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if (-not $config.usage_guard) {
            $config | Add-Member -NotePropertyName usage_guard -NotePropertyValue ([PSCustomObject]@{})
        }
        Set-ObjectProperty $config.usage_guard "enabled" ([bool]$GuardCheck.Checked)
        Set-ObjectProperty $config.usage_guard "pause_at_remaining_percent" ([int]$Threshold.Value)
        Set-ObjectProperty $config.usage_guard "mode" "safe_turn_boundary"
        $temporary = "$ConfigFile.tmp"
        $json = $config | ConvertTo-Json -Depth 20
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $json, $utf8NoBom)
        Move-Item -Force $temporary $ConfigFile
        $Message.Text = "Usage guard settings saved."
    } catch {
        $Message.Text = "Could not save settings: $($_.Exception.Message)"
    }
}

function Refresh-Dashboard {
    try {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        $pidValue = [int]($state.watcher_pid)
        $script:WatcherAlive = [bool]$state.running -and (Test-ProcessAlive $pidValue)
        $Status.Text = if ($script:WatcherAlive) { "ROUTING ACTIVE" } else { "STOPPED" }
        $Status.ForeColor = if ($script:WatcherAlive) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Salmon }
        $ToggleRouter.Text = if ($script:WatcherAlive) { "Stop routing" } else { "Start routing" }

        if ($state.usage -and $state.usage.available) {
            $remaining = [Math]::Max(0, [Math]::Min(100, [double]$state.usage.remaining_percent))
            $UsageValue.Text = "$([Math]::Round($remaining))% remaining"
            $UsageBar.Value = [int][Math]::Round($remaining)
            if ([int64]$state.usage.resets_at -gt 0) {
                $resetDate = [DateTimeOffset]::FromUnixTimeSeconds([int64]$state.usage.resets_at).LocalDateTime
                $UsageReset.Text = "Resets $($resetDate.ToString('g'))"
            }
        } else {
            $UsageValue.Text = "Usage unavailable"
            $UsageBar.Value = 0
        }

        if ($state.usage_guard -and -not $script:GuardControlsLoaded) {
            $GuardCheck.Checked = [bool]$state.usage_guard.enabled
            $value = [Math]::Max(1, [Math]::Min(50, [int]$state.usage_guard.pause_at_remaining_percent))
            $Threshold.Value = $value
            $script:GuardControlsLoaded = $true
        }
        if ($state.usage_guard -and $state.usage_guard.paused) { $UsageValue.ForeColor = [System.Drawing.Color]::Orange }
        else { $UsageValue.ForeColor = [System.Drawing.Color]::White }

        $lines = New-Object System.Collections.Generic.List[string]
        foreach ($task in @($state.tasks)) {
            $project = if ($task.cwd) { Split-Path $task.cwd -Leaf } else { "Unknown task" }
            $current = "$($task.current_model) / $($task.current_effort)"
            $next = if ($task.decision) { "$($task.decision.model) / $($task.decision.effort)" } else { "Analyzing" }
            $lines.Add("$project`r`n  Current: $current`r`n  Next:    $next`r`n  Status:  $($task.auto_apply_status)`r`n")
        }
        $Tasks.Text = if ($lines.Count) { $lines -join "`r`n" } else { "Looking for active user tasks..." }
        $Tray.Text = if ($script:WatcherAlive) { "Codex Router - active" } else { "Codex Router - stopped" }
    } catch {
        $script:WatcherAlive = $false
        $Status.Text = "STATE UNAVAILABLE"
        $Status.ForeColor = [System.Drawing.Color]::Orange
        $Message.Text = "Waiting for router state..."
    }
}

$Tray = New-Object System.Windows.Forms.NotifyIcon
$Tray.Icon = [System.Drawing.SystemIcons]::Application
$Tray.Text = "Codex Adaptive Model Router"
$Tray.Visible = $true
$Menu = New-Object System.Windows.Forms.ContextMenuStrip
$OpenItem = $Menu.Items.Add("Open dashboard")
$ToggleItem = $Menu.Items.Add("Start or stop routing")
$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$ExitItem = $Menu.Items.Add("Exit")
$Tray.ContextMenuStrip = $Menu

$OpenItem.Add_Click({ $Form.Show(); $Form.WindowState = "Normal"; $Form.Activate() })
$Tray.Add_DoubleClick({ $Form.Show(); $Form.WindowState = "Normal"; $Form.Activate() })
$ToggleItem.Add_Click({
    if ($script:WatcherAlive) { Invoke-Router @("stop") }
    else { Invoke-Router @("watch", "--all", "--daemon") }
    Refresh-Dashboard
})
$ToggleRouter.Add_Click({
    if ($script:WatcherAlive) { Invoke-Router @("stop") }
    else { Invoke-Router @("watch", "--all", "--daemon") }
    Refresh-Dashboard
})
$SaveGuard.Add_Click({ Save-GuardSettings; Refresh-Dashboard })
$OpenFolder.Add_Click({ Start-Process explorer.exe $RuntimeRoot })
$ExitItem.Add_Click({ $Tray.Visible = $false; $Tray.Dispose(); $Form.Dispose(); [System.Windows.Forms.Application]::Exit() })
$Form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($eventArgs.CloseReason -eq [System.Windows.Forms.CloseReason]::UserClosing) {
        $eventArgs.Cancel = $true
        $Form.Hide()
    }
})

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 2000
$Timer.Add_Tick({ Refresh-Dashboard })
$Timer.Start()
Refresh-Dashboard
$Form.Show()
[System.Windows.Forms.Application]::Run($Form)
$Tray.Visible = $false
$Tray.Dispose()
