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
    var updatedAt: String?
    var mode: String?
    var strategy: String?
    var tasks: [RouterState]?
}

@MainActor
final class RouterViewModel: ObservableObject {
    @Published var state: RouterState?
    @Published var tasks: [RouterState] = []
    @Published var controllerPid = 0
    @Published var isWatcherAlive = false
    @Published var isCommandRunning = false
    @Published var message = "상태를 확인하고 있습니다…"
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
            } else {
                let decoded = try decoder.decode(RouterState.self, from: data)
                state = decoded
                tasks = [decoded]
                controllerPid = decoded.watcherPid ?? 0
                isWatcherAlive = processIsAlive(controllerPid)
            }
            lastRefresh = Date()
            if !isCommandRunning {
                message = isWatcherAlive ? "자동 모델 변경이 작동 중입니다." : "자동 모델 변경이 중지되어 있습니다."
            }
        } catch {
            state = nil
            tasks = []
            controllerPid = 0
            isWatcherAlive = false
            if !isCommandRunning {
                message = "상태 파일을 읽을 수 없습니다: \(error.localizedDescription)"
            }
        }
    }

    func startWatching() {
        let arguments = [routerScript.path, "watch", "--all", "--daemon"]
        runPython(arguments: arguments, action: "자동 변경 시작")
    }

    func stopWatching() {
        runPython(arguments: [routerScript.path, "stop"], action: "자동 변경 중지")
    }

    func openRouterFolder() {
        NSWorkspace.shared.open(runtimeRoot)
    }

    private func runPython(arguments: [String], action: String) {
        guard !isCommandRunning else { return }
        isCommandRunning = true
        message = "\(action) 요청을 처리하고 있습니다…"

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
                    ? (output.isEmpty ? "\(action) 완료" : output)
                    : "\(action) 실패: \(output)"
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
            Text(alive ? "자동 변경 작동 중" : "중지됨")
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
        return preview.isEmpty ? "후속 작업을 기다리는 중입니다." : preview
    }

    private var routingLabel: String {
        switch task.autoApplyStatus ?? "" {
        case "switching", "running": return "자동 변경 중"
        case "prearming": return "다음 턴 예약 중"
        case "next_turn_prearmed": return "다음 턴 예약 완료"
        case "prearm_applied": return "실제 적용 확인"
        case "prearm_missed": return "예약 불일치"
        case "waiting_next_step": return "다음 작업 분석 중"
        case "non_goal_complete": return "일반 요청 완료"
        case "prearm_failed": return "사전 예약 실패"
        case "already_optimal": return "이미 최적 모델"
        case "waiting_turn_boundary": return "작업 종료 후 변경"
        case "goal_blocked": return "목표 중지됨"
        case "goal_missing": return "활성 목표 없음"
        case "failed": return "변경 실패"
        case "cooldown": return "재시도 대기"
        default: return "변경 준비됨"
        }
    }

    private var tierLabel: String {
        let tier = task.decision?.tier ?? ""
        if tier.hasPrefix("luna_") { return "절약형 · Luna" }
        if tier.hasPrefix("terra_") { return "균형형 · Terra" }
        if tier.hasPrefix("sol_") { return "고성능 · Sol" }
        return "분석 중"
    }

    private func effortLabel(_ effort: String?) -> String {
        switch effort ?? "" {
        case "low": return "낮음"
        case "medium": return "보통"
        case "high": return "높음"
        case "xhigh": return "매우 높음"
        case "max": return "최대"
        case "ultra": return "최고"
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
        case "goal_blocked", "goal_missing", "failed", "prearm_failed", "prearm_missed": return .red
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
                Text("문맥 \(Int(contextRatio * 100))%")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(contextRatio >= 0.8 ? Color.orange : Color.cyan)
            }

            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("현재 사용 중")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.38))
                    Text(task.currentModel ?? "—")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.white)
                    Text("사고 강도: \(effortLabel(task.currentEffort))")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.cyan)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Image(systemName: "arrow.right")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Color.white.opacity(0.25))

                VStack(alignment: .leading, spacing: 3) {
                    Text("다음 턴에 자동 적용")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Color.white.opacity(0.38))
                    Text(task.decision?.model ?? "—")
                        .font(.system(size: 11, weight: .semibold, design: .monospaced))
                        .foregroundStyle(.white)
                    Text("사고 강도: \(effortLabel(task.decision?.effort))")
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

struct ContentView: View {
    @EnvironmentObject var model: RouterViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("코덱스 자동 모델 전환기")
                        .font(.system(size: 17, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    Text("모든 요청을 제출 전에 분석하고 알맞은 모델로 자동 변경")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundStyle(Color.cyan.opacity(0.72))
                }
                Spacer()
                Text("작업 \(model.tasks.count)개")
                    .font(.system(size: 9, weight: .bold, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.4))
                StatusPill(alive: model.isWatcherAlive)
            }

            VStack(spacing: 10) {
                ForEach(Array(model.tasks.prefix(4).enumerated()), id: \.offset) { _, task in
                    TaskCard(task: task)
                }
                if model.tasks.isEmpty {
                    Text("활성 사용자 작업을 찾고 있습니다…")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.white.opacity(0.45))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 28)
                }
            }

            HStack(spacing: 9) {
                Button {
                    model.isWatcherAlive ? model.stopWatching() : model.startWatching()
                } label: {
                    Label(
                        model.isWatcherAlive ? "자동 변경 중지" : "자동 변경 시작",
                        systemImage: model.isWatcherAlive ? "stop.fill" : "play.fill"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(model.isWatcherAlive ? .red.opacity(0.78) : .blue)
                .disabled(model.isCommandRunning)

                Button {
                    model.refresh()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .help("상태 새로고침")

                Button {
                    model.openRouterFolder()
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.bordered)
                .help("라우터 폴더 열기")
            }

            HStack(spacing: 6) {
                Image(systemName: "shield.lefthalf.filled")
                    .foregroundStyle(Color.green.opacity(0.8))
                Text("목표·일반 요청 모두 적용 · Luna → Terra → Sol 자동 최적화")
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
                contentRect: NSRect(x: 0, y: 0, width: 430, height: 520),
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
            Text(appDelegate.viewModel.isWatcherAlive ? "자동 모델 변경 작동 중" : "자동 모델 변경 중지됨")
            Divider()
            Button("위젯 열기") {
                appDelegate.showMainWindow()
            }
            Button(appDelegate.viewModel.isWatcherAlive ? "자동 변경 중지" : "자동 변경 시작") {
                appDelegate.viewModel.isWatcherAlive
                    ? appDelegate.viewModel.stopWatching()
                    : appDelegate.viewModel.startWatching()
            }
            Divider()
            Button("종료") {
                NSApp.terminate(nil)
            }
        }

        Settings {
            EmptyView()
        }
    }
}
