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
$Form.Size = New-Object System.Drawing.Size(470, 760)
$Form.MinimumSize = New-Object System.Drawing.Size(470, 760)
$Form.StartPosition = "CenterScreen"
$Form.BackColor = [System.Drawing.Color]::FromArgb(15, 20, 32)
$Form.ForeColor = [System.Drawing.Color]::White
$Form.Icon = [System.Drawing.SystemIcons]::Application

$Title = New-Label "Codex Adaptive Model Router" 20 18 330 28 14 $true
$Subtitle = New-Label "Local, zero-token routing for every task" 20 48 360 20 9 $false
$Subtitle.ForeColor = [System.Drawing.Color]::FromArgb(70, 205, 220)
$Status = New-Label "CHECKING STATUS" 345 22 105 22 8 $true
$Status.TextAlign = "MiddleRight"

$ToggleRouter = New-Object System.Windows.Forms.Button
$ToggleRouter.Text = "TURN ROUTING ON"
$ToggleRouter.Location = New-Object System.Drawing.Point(20, 78)
$ToggleRouter.Size = New-Object System.Drawing.Size(410, 40)
$ToggleRouter.FlatStyle = "Flat"
$ToggleRouter.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)

$UsageTitle = New-Label "WEEKLY CODEX USAGE" 20 136 260 18 9 $true
$UsageValue = New-Label "Usage unavailable" 20 156 300 35 18 $true
$UsageReset = New-Label "Reset time unavailable" 20 191 410 18 8 $false
$UsageReset.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)
$UsageBar = New-Object System.Windows.Forms.ProgressBar
$UsageBar.Location = New-Object System.Drawing.Point(20, 216)
$UsageBar.Size = New-Object System.Drawing.Size(410, 10)
$UsageBar.Minimum = 0
$UsageBar.Maximum = 100

$GuardCheck = New-Object System.Windows.Forms.CheckBox
$GuardCheck.Text = "Pause new work when weekly usage is low"
$GuardCheck.Location = New-Object System.Drawing.Point(20, 243)
$GuardCheck.Size = New-Object System.Drawing.Size(320, 24)
$GuardCheck.ForeColor = [System.Drawing.Color]::White
$GuardCheck.BackColor = [System.Drawing.Color]::Transparent

$ThresholdLabel = New-Label "Stop at" 20 273 58 24 9 $false
$ThresholdMinus = New-Object System.Windows.Forms.Button
$ThresholdMinus.Text = "-"
$ThresholdMinus.Location = New-Object System.Drawing.Point(80, 268)
$ThresholdMinus.Size = New-Object System.Drawing.Size(32, 30)
$ThresholdBar = New-Object System.Windows.Forms.TrackBar
$ThresholdBar.Location = New-Object System.Drawing.Point(116, 266)
$ThresholdBar.Size = New-Object System.Drawing.Size(220, 38)
$ThresholdBar.Minimum = 1
$ThresholdBar.Maximum = 100
$ThresholdBar.TickFrequency = 10
$ThresholdBar.SmallChange = 1
$ThresholdBar.LargeChange = 5
$ThresholdBar.Value = 10
$ThresholdValue = New-Label "10%" 338 273 54 24 10 $true
$ThresholdValue.TextAlign = "MiddleCenter"
$ThresholdPlus = New-Object System.Windows.Forms.Button
$ThresholdPlus.Text = "+"
$ThresholdPlus.Location = New-Object System.Drawing.Point(398, 268)
$ThresholdPlus.Size = New-Object System.Drawing.Size(32, 30)

$GuardNote = New-Label "Safe stop: active turns finish; new prompts and automatic follow-ups wait." 20 306 420 32 8 $false
$GuardNote.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)

$IdleLabel = New-Label "Hide tasks idle for" 20 348 210 24 9 $false
$IdleMinutes = New-Object System.Windows.Forms.NumericUpDown
$IdleMinutes.Location = New-Object System.Drawing.Point(286, 344)
$IdleMinutes.Size = New-Object System.Drawing.Size(78, 28)
$IdleMinutes.Minimum = 1
$IdleMinutes.Maximum = 120
$IdleMinutes.Increment = 1
$IdleMinutes.Value = 10
$IdleMinutes.TextAlign = "Right"
$IdleSuffix = New-Label "minutes" 370 348 60 24 9 $false
$IdleSuffix.TextAlign = "MiddleLeft"

$TasksTitle = New-Label "ACTIVE TASKS" 20 388 200 20 9 $true
$Tasks = New-Object System.Windows.Forms.TextBox
$Tasks.Location = New-Object System.Drawing.Point(20, 412)
$Tasks.Size = New-Object System.Drawing.Size(410, 205)
$Tasks.Multiline = $true
$Tasks.ReadOnly = $true
$Tasks.ScrollBars = "Vertical"
$Tasks.BackColor = [System.Drawing.Color]::FromArgb(25, 31, 46)
$Tasks.ForeColor = [System.Drawing.Color]::FromArgb(220, 230, 240)
$Tasks.BorderStyle = "FixedSingle"
$Tasks.Font = New-Object System.Drawing.Font("Consolas", 9)

$OpenFolder = New-Object System.Windows.Forms.Button
$OpenFolder.Text = "Open router folder"
$OpenFolder.Location = New-Object System.Drawing.Point(20, 634)
$OpenFolder.Size = New-Object System.Drawing.Size(410, 34)
$Message = New-Label "Ready" 20 676 410 22 8 $false
$Message.ForeColor = [System.Drawing.Color]::FromArgb(145, 155, 170)

$Form.Controls.AddRange(@(
    $Title, $Subtitle, $Status, $ToggleRouter, $UsageTitle, $UsageValue, $UsageReset, $UsageBar,
    $GuardCheck, $ThresholdLabel, $ThresholdMinus, $ThresholdBar, $ThresholdValue, $ThresholdPlus, $GuardNote,
    $IdleLabel, $IdleMinutes, $IdleSuffix, $TasksTitle, $Tasks, $OpenFolder, $Message
))

$script:WatcherAlive = $false
$script:GuardControlsLoaded = $false
$script:SuppressGuardSave = $false
$script:IdleControlLoaded = $false
$script:SuppressIdleSave = $false

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
        Set-ObjectProperty $config.usage_guard "pause_at_remaining_percent" ([int]$ThresholdBar.Value)
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

function Save-IdleSettings {
    try {
        $config = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        $minutes = [Math]::Max(1, [Math]::Min(120, [int]$IdleMinutes.Value))
        Set-ObjectProperty $config "activity_window_seconds" ($minutes * 60)
        $temporary = "$ConfigFile.tmp"
        $json = $config | ConvertTo-Json -Depth 20
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temporary, $json, $utf8NoBom)
        Move-Item -Force $temporary $ConfigFile
        $Message.Text = "Idle task filter saved."
    } catch {
        $Message.Text = "Could not save idle task filter: $($_.Exception.Message)"
    }
}

function Refresh-Dashboard {
    try {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        $pidValue = [int]($state.watcher_pid)
        $script:WatcherAlive = [bool]$state.running -and (Test-ProcessAlive $pidValue)
        $Status.Text = if ($script:WatcherAlive) { "ROUTING ACTIVE" } else { "STOPPED" }
        $Status.ForeColor = if ($script:WatcherAlive) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Salmon }
        $ToggleRouter.Text = if ($script:WatcherAlive) { "TURN ROUTING OFF" } else { "TURN ROUTING ON" }
        $ToggleRouter.BackColor = if ($script:WatcherAlive) { [System.Drawing.Color]::FromArgb(92, 38, 45) } else { [System.Drawing.Color]::FromArgb(28, 86, 65) }
        $ToggleRouter.ForeColor = [System.Drawing.Color]::White

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
            $script:SuppressGuardSave = $true
            $GuardCheck.Checked = [bool]$state.usage_guard.enabled
            $value = [Math]::Max(1, [Math]::Min(100, [int]$state.usage_guard.pause_at_remaining_percent))
            $ThresholdBar.Value = $value
            $ThresholdValue.Text = "$value%"
            $ThresholdBar.Enabled = $GuardCheck.Checked
            $ThresholdMinus.Enabled = $GuardCheck.Checked
            $ThresholdPlus.Enabled = $GuardCheck.Checked
            $script:SuppressGuardSave = $false
            $script:GuardControlsLoaded = $true
        }
        if ($state.usage_guard -and $state.usage_guard.paused) { $UsageValue.ForeColor = [System.Drawing.Color]::Orange }
        else { $UsageValue.ForeColor = [System.Drawing.Color]::White }

        if ($state.activity_window_seconds -and -not $script:IdleControlLoaded) {
            $script:SuppressIdleSave = $true
            $minutes = [Math]::Max(1, [Math]::Min(120, [int][Math]::Round([double]$state.activity_window_seconds / 60)))
            $IdleMinutes.Value = $minutes
            $script:SuppressIdleSave = $false
            $script:IdleControlLoaded = $true
        }

        $lines = New-Object System.Collections.Generic.List[string]
        foreach ($task in @($state.tasks)) {
            $project = if ($task.task_name) { [string]$task.task_name } elseif ($task.cwd) { Split-Path $task.cwd -Leaf } else { "Unknown task" }
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
$GuardCheck.Add_CheckedChanged({
    $ThresholdBar.Enabled = $GuardCheck.Checked
    $ThresholdMinus.Enabled = $GuardCheck.Checked
    $ThresholdPlus.Enabled = $GuardCheck.Checked
    if ($script:GuardControlsLoaded -and -not $script:SuppressGuardSave) { Save-GuardSettings }
})
$ThresholdBar.Add_ValueChanged({
    $ThresholdValue.Text = "$($ThresholdBar.Value)%"
    if ($script:GuardControlsLoaded -and -not $script:SuppressGuardSave) { Save-GuardSettings }
})
$ThresholdMinus.Add_Click({
    if ($ThresholdBar.Value -gt $ThresholdBar.Minimum) { $ThresholdBar.Value -= 1 }
})
$ThresholdPlus.Add_Click({
    if ($ThresholdBar.Value -lt $ThresholdBar.Maximum) { $ThresholdBar.Value += 1 }
})
$IdleMinutes.Add_ValueChanged({
    if ($script:IdleControlLoaded -and -not $script:SuppressIdleSave) { Save-IdleSettings }
})
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
