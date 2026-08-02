import AppKit
import SwiftUI

private let runtimeRoot = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".codex/auto-router", isDirectory: true)
private let routerScript = Bundle.main.url(
    forResource: "codex_router",
    withExtension: "py"
) ?? runtimeRoot.appendingPathComponent("codex_router.py")
private let routerConfig = Bundle.main.url(
    forResource: "router_config",
    withExtension: "json"
) ?? runtimeRoot.appendingPathComponent("router_config.json")
private let stateFile = runtimeRoot.appendingPathComponent("runtime/state.json")
private let pidFile = runtimeRoot.appendingPathComponent("runtime/watcher.pid")
private let liveConfigFile = runtimeRoot.appendingPathComponent("router_config.json")
private let pythonExecutable = URL(fileURLWithPath: "/usr/bin/python3")

struct TelemetryState: Codable {
    var failuresThisTurn: Int?
    var toolOutputBytesThisTurn: Int?
    var contextRatio: Double?
    var cumulativeInputTokens: Int?
    var cumulativeCachedInputTokens: Int?
    var cumulativeOutputTokens: Int?
    var contextWindow: Int?
}

struct DecisionState: Codable {
    var tier: String?
    var model: String?
    var effort: String?
    var score: Int?
    var reasons: [String]?
    var source: String?
    var taskPreview: String?
    var recommendCompact: Bool?
    var shouldInterrupt: Bool?
}

struct RouterState: Codable {
    var threadId: String?
    var taskName: String?
    var sessionPath: String?
    var cwd: String?
    var currentModel: String?
    var currentEffort: String?
    var latestUserPrompt: String?
    var plannedNextStep: String?
    var latestAssistantMessage: String?
    var telemetry: TelemetryState?
    var decision: DecisionState?
    var mode: String?
    var strategy: String?
    var watcherPid: Int?
    var running: Bool?
    var updatedAt: String?
    var turnActive: Bool?
    var lastTurnId: String?
    var lastTurnStatus: String?
    var goalStatus: String?
    var autoApplyStatus: String?
    var autoApplyModel: String?
    var autoApplyEffort: String?
    var autoApplyPid: Int?
    var note: String?
}

struct RouterEnvelope: Codable {
    var version: Int?
    var multi: Bool?
    var running: Bool?
    var watcherPid: Int?
    var activeTaskCount: Int?
    var activityWindowSeconds: Int?
    var updatedAt: String?
    var mode: String?
    var strategy: String?
    var usage: UsageState?
    var usageGuard: UsageGuardState?
    var tasks: [RouterState]?
}

struct UsageState: Codable {
    var available: Bool?
    var limitId: String?
    var usedPercent: Double?
    var remainingPercent: Double?
    var windowDurationMins: Int?
    var resetsAt: Int?
    var planType: String?
    var resetCredits: Int?
    var updatedAt: String?
    var error: String?
}

struct UsageGuardState: Codable {
    var enabled: Bool?
    var pauseAtRemainingPercent: Double?
    var paused: Bool?
    var mode: String?
    var note: String?
}

@MainActor
final class RouterViewModel: ObservableObject {
    @Published var state: RouterState?
    @Published var tasks: [RouterState] = []
    @Published var controllerPid = 0
    @Published var isWatcherAlive = false
    @Published var isCommandRunning = false
    @Published var message = "Checking router status…"
    @Published var usage: UsageState?
    @Published var usageGuard: UsageGuardState?
    @Published var guardEnabled = false
    @Published var guardThreshold = 10.0
    @Published var guardSettingsDirty = false
    @Published var idleTimeoutMinutes = 10.0
    @Published var idleSettingsDirty = false
    @Published var lastRefresh = Date()

    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    deinit {
        timer?.invalidate()
    }

    func refresh() {
        do {
            let data = try Data(contentsOf: stateFile)
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            if let envelope = try? decoder.decode(RouterEnvelope.self, from: data), envelope.multi == true {
                tasks = envelope.tasks ?? []
                state = tasks.first
                controllerPid = envelope.watcherPid ?? 0
                isWatcherAlive = processIsAlive(controllerPid)
                usage = envelope.usage
                usageGuard = envelope.usageGuard
                if guardSettingsDirty {
                    let savedEnabled = envelope.usageGuard?.enabled ?? false
                    let savedThreshold = envelope.usageGuard?.pauseAtRemainingPercent ?? 10
                    if savedEnabled == guardEnabled && abs(savedThreshold - guardThreshold) < 0.5 {
                        guardSettingsDirty = false
                    }
                } else {
                    guardEnabled = envelope.usageGuard?.enabled ?? guardEnabled
                    guardThreshold = envelope.usageGuard?.pauseAtRemainingPercent ?? guardThreshold
                }
                if let activityWindowSeconds = envelope.activityWindowSeconds {
                    let savedMinutes = Double(max(60, activityWindowSeconds)) / 60.0
                    if idleSettingsDirty {
                        if abs(savedMinutes - idleTimeoutMinutes) < 0.5 {
                            idleSettingsDirty = false
                        }
                    } else {
                        idleTimeoutMinutes = savedMinutes
                    }
                }
            } else {
                let decoded = try decoder.decode(RouterState.self, from: data)
                state = decoded
                tasks = [decoded]
                controllerPid = decoded.watcherPid ?? 0
                isWatcherAlive = processIsAlive(controllerPid)
            }
            lastRefresh = Date()
            if !isCommandRunning {
                message = isWatcherAlive ? "Automatic model routing is active." : "Automatic model routing is stopped."
            }
        } catch {
            state = nil
            tasks = []
            controllerPid = 0
            isWatcherAlive = false
            if !isCommandRunning {
                message = "Could not read router state: \(error.localizedDescription)"
            }
        }
    }

    func startWatching() {
        let arguments = [routerScript.path, "watch", "--all", "--daemon"]
        runPython(arguments: arguments, action: "Start automatic routing")
    }

    func stopWatching() {
        runPython(arguments: [routerScript.path, "stop"], action: "Stop automatic routing")
    }

    func openRouterFolder() {
        NSWorkspace.shared.open(runtimeRoot)
    }

    func saveUsageGuard() {
        do {
            let data = try Data(contentsOf: liveConfigFile)
            guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            var guardConfig = root["usage_guard"] as? [String: Any] ?? [:]
            guardConfig["enabled"] = guardEnabled
            guardConfig["pause_at_remaining_percent"] = Int(guardThreshold.rounded())
            guardConfig["mode"] = "safe_turn_boundary"
            root["usage_guard"] = guardConfig
            let updated = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
            try updated.write(to: liveConfigFile, options: .atomic)
            message = "Usage guard settings saved. The watcher reloads them automatically."
        } catch {
            message = "Could not save usage guard settings: \(error.localizedDescription)"
        }
    }

    func saveIdleTimeout() {
        do {
            let data = try Data(contentsOf: liveConfigFile)
            guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let minutes = min(max(Int(idleTimeoutMinutes.rounded()), 1), 120)
            idleTimeoutMinutes = Double(minutes)
            root["activity_window_seconds"] = minutes * 60
            let updated = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
            try updated.write(to: liveConfigFile, options: .atomic)
            message = "Idle task filter saved. The list updates automatically."
        } catch {
            message = "Could not save idle task filter: \(error.localizedDescription)"
        }
    }

    private func runPython(arguments: [String], action: String) {
        guard !isCommandRunning else { return }
        isCommandRunning = true
        message = "\(action)…"

        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            let pipe = Pipe()
            process.executableURL = pythonExecutable
            process.currentDirectoryURL = runtimeRoot
            var environment = ProcessInfo.processInfo.environment
            environment["CODEX_ROUTER_ROOT"] = runtimeRoot.path
            process.environment = environment
            process.standardOutput = pipe
            process.standardError = pipe

            var output = ""
            var exitCode: Int32 = -1
            do {
                try FileManager.default.createDirectory(
                    at: runtimeRoot,
                    withIntermediateDirectories: true
                )
                let installedScript = runtimeRoot.appendingPathComponent("codex_router.py")
                let scriptData = try Data(contentsOf: routerScript)
                try scriptData.write(to: installedScript, options: .atomic)
                let installedConfig = runtimeRoot.appendingPathComponent("router_config.json")
                if !FileManager.default.fileExists(atPath: installedConfig.path) {
                    let configData = try Data(contentsOf: routerConfig)
                    try configData.write(to: installedConfig, options: .atomic)
                }
                process.arguments = [installedScript.path] + arguments.dropFirst()
                try process.run()
                process.waitUntilExit()
                exitCode = process.terminationStatus
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            } catch {
                output = error.localizedDescription
            }

            DispatchQueue.main.async {
                self.isCommandRunning = false
                self.message = exitCode == 0
                    ? (output.isEmpty ? "\(action) complete" : output)
                    : "\(action) failed: \(output)"
                self.refresh()
            }
        }
    }

    private func processIsAlive(_ pid: Int?) -> Bool {
        guard let pid, pid > 0 else { return false }
        guard let savedPid = try? String(contentsOf: pidFile, encoding: .utf8),
              Int(savedPid.trimmingCharacters(in: .whitespacesAndNewlines)) == pid else {
            return false
        }
        return Darwin.kill(Int32(pid), 0) == 0
    }
}

struct StatusPill: View {
    let alive: Bool

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(alive ? Color.green : Color.red)
                .frame(width: 9, height: 9)
                .shadow(color: (alive ? Color.green : Color.red).opacity(0.8), radius: 5)
            Text(alive ? "ROUTING ACTIVE" : "STOPPED")
                .font(.system(size: 11, weight: .bold, design: .monospaced))
                .foregroundStyle(alive ? Color.green : Color.red)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.white.opacity(0.07), in: Capsule())
    }
}

struct ModelCard: View {
    let title: String
    let model: String
    let effort: String
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundStyle(Color.white.opacity(0.48))
            Text(model.isEmpty ? "—" : model)
                .font(.system(size: 14, weight: .semibold, design: .monospaced))
                .foregroundStyle(.white)
                .lineLimit(1)
            Text(effort.isEmpty ? "—" : effort)
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(accent)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color.white.opacity(0.065), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(accent.opacity(0.22), lineWidth: 1)
        }
    }
}

struct TaskCard: View {
    let task: RouterState

    private var projectName: String {
        let taskName = task.taskName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !taskName.isEmpty { return taskName }
        guard let cwd = task.cwd, !cwd.isEmpty else { return "Unknown task" }
        return URL(fileURLWithPath: cwd).lastPathComponent
    }

    private var contextRatio: Double {
        min(max(task.telemetry?.contextRatio ?? 0, 0), 1)
    }

    private var nextStep: String {
        let value = task.plannedNextStep?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !value.isEmpty { return value }
        let preview = task.latestUserPrompt?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return preview.isEmpty ? "Waiting for follow-up work." : preview
    }

    private var routingLabel: String {
        switch task.autoApplyStatus ?? "" {
        case "switching", "running": return "SWITCHING"
        case "prearming": return "PRE-ARMING"
        case "next_turn_prearmed": return "NEXT TURN READY"
        case "prearm_applied": return "ROUTE VERIFIED"
        case "prearm_missed": return "ROUTE MISMATCH"
        case "waiting_next_step": return "ANALYZING NEXT STEP"
        case "non_goal_complete": return "REQUEST COMPLETE"
        case "prearm_failed": return "PRE-ARM FAILED"
        case "already_optimal": return "OPTIMAL ROUTE"
        case "waiting_turn_boundary": return "WAITING FOR BOUNDARY"
        case "goal_blocked": return "GOAL BLOCKED"
        case "goal_missing": return "NO ACTIVE GOAL"
        case "usage_paused": return "USAGE PAUSED"
        case "failed": return "SWITCH FAILED"
        case "cooldown": return "COOLDOWN"
        default: return "ROUTE READY"
        }
    }

    private var tierLabel: String {
        let tier = task.decision?.tier ?? ""
        if tier.hasPrefix("luna_") { return "ECONOMY · LUNA" }
        if tier.hasPrefix("terra_") { return "BALANCED · TERRA" }
        if tier.hasPrefix("sol_") { return "POWER · SOL" }
        return "ANALYZING"
    }

    private func effortLabel(_ effort: String?) -> String {
        switch effort ?? "" {
        case "low": return "Low"
        case "medium": return "Medium"
        case "high": return "High"
        case "xhigh": return "Extra high"
        case "max": return "Maximum"
        case "ultra": return "Ultra"
        default: return "—"
        }
    }

    private var routingColor: Color {
        switch task.autoApplyStatus ?? "" {
        case "switching", "running": return .orange
        case "prearming": return .orange
        case "next_turn_prearmed": return .green
        case "prearm_applied": return .green
        case "already_optimal": return .green
        case "goal_blocked", "goal_missing", "usage_paused", "failed", "prearm_failed", "prearm_missed": return .red
        default: return .cyan
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack {
                HStack(spacing: 7) {
                    Circle()
                        .fill(Color.green)
                        .frame(width: 7, height: 7)
                    Text(projectName)
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                }
                Spacer()
                Text(routingLabel)
                    .font(.system(size: 8, weight: .bold, design: .monospaced))
                    .foregroundStyle(routingColor)
                Text(tierLabel)
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.purple)
                Text("CONTEXT \(Int(contextRatio * 100))%")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(contextRatio >= 0.8 ? Color.orange : Color.cyan)
            }

            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("CURRENT ROUTE")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.38))
                    Text(task.currentModel ?? "—")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.white)
                    Text("Reasoning: \(effortLabel(task.currentEffort))")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.cyan)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "arrow.right")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Color.white.opacity(0.25))

                VStack(alignment: .leading, spacing: 3) {
                    Text("NEXT TURN")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.38))
                    Text(task.decision?.model ?? "—")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.white)
                    Text("Reasoning: \(effortLabel(task.decision?.effort))")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.purple)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.08))
                    Capsule()
                        .fill(contextRatio >= 0.8 ? Color.orange : Color.cyan)
                        .frame(width: geometry.size.width * contextRatio)
                }
            }
            .frame(height: 5)

            Text(nextStep)
                .font(.system(size: 10))
                .foregroundStyle(Color.white.opacity(0.55))
                .lineLimit(2)
        }
        .padding(13)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(Color.white.opacity(0.08), lineWidth: 1)
        }
    }
}

struct RoutingControlCard: View {
    @EnvironmentObject var model: RouterViewModel

    private var routingBinding: Binding<Bool> {
        Binding(
            get: { model.isWatcherAlive },
            set: { shouldRun in
                guard shouldRun != model.isWatcherAlive else { return }
                shouldRun ? model.startWatching() : model.stopWatching()
            }
        )
    }

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill((model.isWatcherAlive ? Color.green : Color.red).opacity(0.14))
                    .frame(width: 38, height: 38)
                Image(systemName: model.isWatcherAlive ? "bolt.fill" : "pause.fill")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(model.isWatcherAlive ? Color.green : Color.red)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text("AUTOMATIC ROUTING")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.48))
                Text(model.isWatcherAlive ? "On · monitoring every active task" : "Off · no model changes will be made")
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                Text("Turning this off does not stop work already running in Codex.")
                    .font(.system(size: 9))
                    .foregroundStyle(Color.white.opacity(0.38))
            }

            Spacer()

            Toggle("Automatic routing", isOn: routingBinding)
                .labelsHidden()
                .toggleStyle(.switch)
                .tint(.green)
                .disabled(model.isCommandRunning)
                .help(model.isWatcherAlive ? "Turn automatic routing off" : "Turn automatic routing on")
        }
        .padding(13)
        .background(
            (model.isWatcherAlive ? Color.green : Color.red).opacity(0.075),
            in: RoundedRectangle(cornerRadius: 13, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke((model.isWatcherAlive ? Color.green : Color.red).opacity(0.24), lineWidth: 1)
        }
    }
}

struct UsageDial: View {
    let remaining: Double
    let warning: Bool

    var body: some View {
        Gauge(value: remaining, in: 0...100) {
            Text("Weekly usage remaining")
        } currentValueLabel: {
            Text("\(Int(remaining.rounded()))%")
                .font(.system(size: 15, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
        }
        .gaugeStyle(.accessoryCircularCapacity)
        .tint(warning ? Color.orange : Color.green)
        .frame(width: 74, height: 74)
        .accessibilityValue("\(Int(remaining.rounded())) percent remaining")
    }
}

struct UsageCard: View {
    @EnvironmentObject var model: RouterViewModel

    private var remaining: Double {
        min(max(model.usage?.remainingPercent ?? 0, 0), 100)
    }

    private var resetText: String {
        guard let timestamp = model.usage?.resetsAt, timestamp > 0 else { return "Reset time unavailable" }
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return "Resets \(formatter.string(from: Date(timeIntervalSince1970: TimeInterval(timestamp))))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 13) {
                UsageDial(
                    remaining: remaining,
                    warning: remaining <= model.guardThreshold && model.guardEnabled
                )

                VStack(alignment: .leading, spacing: 2) {
                    Text("WEEKLY CODEX USAGE")
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.48))
                    Text(model.usage?.available == true ? "\(Int(remaining.rounded()))% remaining" : "Usage unavailable")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                        .foregroundStyle(remaining <= model.guardThreshold && model.guardEnabled ? Color.orange : Color.white)
                    Text(resetText)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.42))
                    if (model.usage?.resetCredits ?? 0) > 0 {
                        Text("\(model.usage?.resetCredits ?? 0) reset credit")
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundStyle(Color.white.opacity(0.42))
                    }
                }
                Spacer()
                if model.usageGuard?.paused == true {
                    Text("PAUSED")
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(Color.orange.opacity(0.12), in: Capsule())
                }
            }

            Divider().overlay(Color.white.opacity(0.08))

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("USAGE SAFETY STOP")
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.48))
                    Text("Pause new prompts when weekly usage is low")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.white)
                }
                Spacer()
                Toggle("Usage safety stop", isOn: Binding(
                    get: { model.guardEnabled },
                    set: {
                        model.guardEnabled = $0
                        model.guardSettingsDirty = true
                        model.saveUsageGuard()
                    }
                ))
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .tint(.orange)
            }

            HStack {
                Text("STOP NEW WORK AT")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.48))
                Spacer()
                Text("\(Int(model.guardThreshold))% remaining")
                    .font(.system(size: 12, weight: .bold, design: .monospaced))
                    .foregroundStyle(model.guardEnabled ? Color.orange : Color.white.opacity(0.35))
            }

            HStack(spacing: 8) {
                Button {
                    model.guardThreshold = max(1, model.guardThreshold - 1)
                    model.guardSettingsDirty = true
                    model.saveUsageGuard()
                } label: {
                    Image(systemName: "minus")
                        .frame(width: 18, height: 18)
                }
                .buttonStyle(.bordered)
                .help("Decrease stop percentage")

                Slider(
                    value: Binding(
                        get: { model.guardThreshold },
                        set: {
                            model.guardThreshold = min(max($0, 1), 100)
                            model.guardSettingsDirty = true
                        }
                    ),
                    in: 1...100,
                    step: 1,
                    onEditingChanged: { editing in
                        if !editing { model.saveUsageGuard() }
                    }
                )
                .tint(.orange)
                .accessibilityLabel("Stop new work percentage")
                .accessibilityValue("\(Int(model.guardThreshold)) percent remaining")

                Button {
                    model.guardThreshold = min(100, model.guardThreshold + 1)
                    model.guardSettingsDirty = true
                    model.saveUsageGuard()
                } label: {
                    Image(systemName: "plus")
                        .frame(width: 18, height: 18)
                }
                .buttonStyle(.bordered)
                .help("Increase stop percentage")
            }
            .disabled(!model.guardEnabled)

            Text("Safe stop: active turns finish; new prompts and automatic follow-ups wait.")
                .font(.system(size: 9))
                .foregroundStyle(Color.white.opacity(0.38))
        }
        .padding(13)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 13, style: .continuous)
                .stroke(Color.green.opacity(0.16), lineWidth: 1)
        }
    }
}

struct TaskVisibilityControl: View {
    @EnvironmentObject var model: RouterViewModel

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "clock.badge.xmark")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.cyan)
                .frame(width: 30, height: 30)
                .background(Color.cyan.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))

            VStack(alignment: .leading, spacing: 2) {
                Text("HIDE IDLE TASKS")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.52))
                Text("Show again automatically when activity resumes")
                    .font(.system(size: 9))
                    .foregroundStyle(Color.white.opacity(0.34))
            }

            Spacer(minLength: 8)

            Stepper(
                value: Binding(
                    get: { model.idleTimeoutMinutes },
                    set: {
                        model.idleTimeoutMinutes = min(max($0, 1), 120)
                        model.idleSettingsDirty = true
                        model.saveIdleTimeout()
                    }
                ),
                in: 1...120,
                step: 1
            ) {
                Text("\(Int(model.idleTimeoutMinutes)) min")
                    .font(.system(size: 11, weight: .bold, design: .monospaced))
                    .foregroundStyle(.white)
                    .frame(minWidth: 50, alignment: .trailing)
            }
            .accessibilityLabel("Hide tasks after idle minutes")
            .accessibilityValue("\(Int(model.idleTimeoutMinutes)) minutes")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .stroke(Color.cyan.opacity(0.12), lineWidth: 1)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var model: RouterViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Codex Adaptive Model Router")
                        .font(.system(size: 17, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    Text("Local, zero-token routing for every task")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.cyan.opacity(0.72))
                }
                Spacer()
                Text("\(model.tasks.count) TASKS")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.4))
                StatusPill(alive: model.isWatcherAlive)
            }

            RoutingControlCard()
                .environmentObject(model)

            UsageCard()
                .environmentObject(model)

            TaskVisibilityControl()
                .environmentObject(model)

            VStack(spacing: 10) {
                ForEach(Array(model.tasks.prefix(4).enumerated()), id: \.offset) { _, task in
                    TaskCard(task: task)
                }
                if model.tasks.isEmpty {
                    Text("Looking for active user tasks…")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.white.opacity(0.45))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 28)
                }
            }

            HStack(spacing: 9) {
                Button {
                    model.refresh()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    model.openRouterFolder()
                } label: {
                    Label("Router folder", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }

            HStack(spacing: 6) {
                Image(systemName: "shield.lefthalf.filled")
                    .foregroundStyle(Color.green.opacity(0.8))
                Text("All tasks · Luna → Terra → Sol · local classification")
                    .foregroundStyle(Color.white.opacity(0.5))
                Spacer()
                Text("PID \(model.controllerPid)")
                    .foregroundStyle(Color.white.opacity(0.32))
            }
            .font(.system(size: 9, weight: .medium, design: .monospaced))

            Text(model.message)
                .font(.system(size: 10))
                .foregroundStyle(Color.white.opacity(0.42))
                .lineLimit(2)
        }
        .padding(20)
        .frame(width: 430)
        .background {
            ZStack {
                LinearGradient(
                    colors: [Color(red: 0.045, green: 0.06, blue: 0.10), Color(red: 0.025, green: 0.03, blue: 0.055)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                RadialGradient(
                    colors: [Color.cyan.opacity(0.13), .clear],
                    center: .topLeading,
                    startRadius: 0,
                    endRadius: 300
                )
            }
            .ignoresSafeArea()
        }
        .preferredColorScheme(.dark)
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let viewModel = RouterViewModel()
    private var mainWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        DispatchQueue.main.async { [weak self] in
            self?.showMainWindow()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func showMainWindow() {
        let window: NSWindow
        if let existing = mainWindow {
            window = existing
        } else {
            let created = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 430, height: 760),
                styleMask: [.titled, .closable, .fullSizeContentView],
                backing: .buffered,
                defer: false
            )
            created.title = "Codex Auto Router"
            created.isReleasedWhenClosed = false
            created.contentView = NSHostingView(
                rootView: ContentView().environmentObject(viewModel)
            )
            mainWindow = created
            window = created
        }
        configure(window)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }

    private func configure(_ window: NSWindow) {
        window.delegate = self
        window.level = .floating
        window.isMovableByWindowBackground = true
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.standardWindowButton(.zoomButton)?.isHidden = true
        window.standardWindowButton(.miniaturizeButton)?.isHidden = true
        window.titlebarAppearsTransparent = true
        if let screen = NSScreen.main {
            let frame = window.frame
            let visible = screen.visibleFrame
            let origin = NSPoint(
                x: visible.maxX - frame.width - 24,
                y: visible.maxY - frame.height - 24
            )
            window.setFrameOrigin(origin)
        }
    }
}

@main
struct CodexAutoRouterWidgetApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra("Codex Auto Router", systemImage: "point.3.connected.trianglepath.dotted") {
            Text(appDelegate.viewModel.isWatcherAlive ? "Automatic routing is active" : "Automatic routing is stopped")
            Divider()
            Button("Open dashboard") {
                appDelegate.showMainWindow()
            }
            Button(appDelegate.viewModel.isWatcherAlive ? "Stop routing" : "Start routing") {
                appDelegate.viewModel.isWatcherAlive
                    ? appDelegate.viewModel.stopWatching()
                    : appDelegate.viewModel.startWatching()
            }
            Divider()
            Button("Quit") {
                NSApp.terminate(nil)
            }
        }

        Settings {
            EmptyView()
        }
    }
}
