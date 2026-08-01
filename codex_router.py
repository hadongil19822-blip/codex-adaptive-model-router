#!/usr/bin/env python3
"""Adaptive model router for local Codex sessions.

The watcher observes rollout JSONL files and pre-arms announced follow-up work.
The optional UserPromptSubmit hook classifies every textual request before a
model call and safely resubmits it when a different route is required.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(os.environ.get("CODEX_ROUTER_ROOT") or Path(__file__).resolve().parent).resolve()
RUNTIME_DIR = ROOT / "runtime"
CONFIG_PATH = ROOT / "router_config.json"
STATE_PATH = RUNTIME_DIR / "state.json"
USAGE_PATH = RUNTIME_DIR / "usage.json"
PID_PATH = RUNTIME_DIR / "watcher.pid"
LOG_PATH = RUNTIME_DIR / "watcher.log"
AUTO_RUN_DIR = RUNTIME_DIR / "auto-runs"
PROMPT_ROUTE_DIR = RUNTIME_DIR / "prompt-routes"
SESSION_ROOT = Path.home() / ".codex" / "sessions"
_codex_candidate = os.environ.get("CODEX_BIN") or shutil.which("codex")
if not _codex_candidate:
    macos_codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    _codex_candidate = str(macos_codex) if macos_codex.exists() else "codex"
CODEX_BIN = Path(_codex_candidate)
OSASCRIPT_BIN = Path("/usr/bin/osascript")
CODEX_IPC_SOCKET = Path.home() / ".codex" / "ipc" / "ipc.sock"
IPC_METHOD_VERSIONS = {
    "thread-follower-update-thread-settings": 1,
}


SIMPLE_PATTERNS = (
    r"\b(explain|status|show|list|find|locate|summari[sz]e|format|rename)\b",
    r"(설명|상태|목록|찾아|어디|알려줘|요약|정리|이름\s*변경)",
)
NORMAL_PATTERNS = (
    r"\b(edit|change|fix|test|debug|implement|build|create|refactor)\b",
    r"(수정|변경|고쳐|테스트|디버그|구현|만들|실행|리팩터링|확인|검증|감사|분석|점검|보완)",
)
COMPLEX_PATTERNS = (
    r"\b(after effects|premiere|ffmpeg|migration|architecture|multi[- ]file|router|daemon|realtime|real-time|bulk|all characters)\b",
    r"(에프터\s*이펙트|애프터\s*이펙트|영상\s*편집|마이그레이션|아키텍처|다중\s*파일|라우터|백그라운드|실시간|자동\s*전환|완성도|전수\s*(?:검사|확인|보완)|끝까지|다\s*해서|비주얼|비쥬얼|밸런스|디벨롭|경제\s*루프|게임\s*경제|\d+\s*명(?:의|을|를)?|캐릭터별|날짜별|대량|공용\s*문장)",
)
CRITICAL_PATTERNS = (
    r"\b(security|vulnerability|exploit|production outage|data loss|destructive|deep scan)\b",
    r"(보안|취약점|익스플로잇|운영\s*장애|데이터\s*손실|파괴적|딥\s*스캔)",
)
PARALLEL_PATTERNS = (
    r"\b(parallel|subagents?|multi[- ]agent|exhaustive|independent workstreams?|ultra)\b",
    r"(병렬|서브\s*에이전트|하위\s*에이전트|다중\s*에이전트|전수\s*조사|누락\s*없이|울트라)",
)
FAILURE_PATTERNS = (
    r'"exit_code"\s*:\s*[1-9]\d*',
    r"process exited with code [1-9]",
    r"traceback \(most recent call last\)",
    r'"isError"\s*:\s*true',
    r"FAILED \(failures=",
    r"\bcommand failed\b",
)
NEXT_STEP_PATTERNS = (
    r"(?:^|[\n.!?]\s*)(?:다음\s*(?:작업|단계))\s*(?:은|는|으로|에는|:)\s*([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)(?:다음은|다음에는)\s+([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)(?:다음으로(?:는)?|이어서(?:는)?)\s+([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)그\s*다음\s+([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)이제\s+([^\n.!?]{5,350}?(?:하겠습니다|진행하겠습니다|보완하겠습니다|확인하겠습니다))",
    r"(?:^|[\n.!?]\s*)(?:next\s+(?:task|step))\s*(?:is|:|-)\s*([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)i(?:'|’)ll\s+now\s+([^\n]{5,500})",
    r"(?:^|[\n.!?]\s*)([^\n.!?]{5,350}?(?:부터|먼저|이어서)[^\n.!?]{2,350}?(?:하겠습니다|진행하겠습니다|보완하겠습니다|확인하겠습니다))",
    r"(?:^|[\n.!?]\s*)i(?:'|’)ll\s+(?:next\s+|continue\s+(?:by|with)\s+)?([^\n]{5,500})",
)
ACTION_PLAN_PATTERNS = (
    r"\b(check|verify|test|edit|fix|implement|build|create|review|analy[sz]e|inspect|update|continue|add|remove|refactor|run)\b",
    r"(확인|검증|테스트|수정|구현|보완|점검|분석|정리|반영|추가|제거|찾|살펴|이어|진행|만들|고치|검사|실행|적용|리팩터링|디버깅)",
)


@dataclass
class Telemetry:
    failures_this_turn: int = 0
    tool_output_bytes_this_turn: int = 0
    context_ratio: float = 0.0
    cumulative_input_tokens: int = 0
    cumulative_cached_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    context_window: int = 0


@dataclass
class Decision:
    tier: str
    model: str
    effort: str
    score: int
    reasons: List[str]
    source: str
    task_preview: str
    recommend_compact: bool = False
    should_interrupt: bool = False


@dataclass
class RouterState:
    thread_id: str = ""
    session_path: str = ""
    cwd: str = ""
    current_model: str = ""
    current_effort: str = ""
    latest_user_prompt: str = ""
    planned_next_step: str = ""
    latest_assistant_message: str = ""
    telemetry: Telemetry = field(default_factory=Telemetry)
    decision: Optional[Decision] = None
    mode: str = "observe"
    strategy: str = "turn_boundary"
    watcher_pid: int = 0
    running: bool = False
    updated_at: str = ""
    activity_at: float = 0.0
    turn_active: bool = False
    last_turn_id: str = ""
    last_turn_status: str = "unknown"
    goal_status: str = "unknown"
    auto_apply_status: str = "waiting"
    auto_apply_model: str = ""
    auto_apply_effort: str = ""
    auto_apply_pid: int = 0
    prearmed_model: str = ""
    prearmed_effort: str = ""
    prearmed_turn_id: str = ""
    prearm_verification: str = "none"
    note: str = (
        "Active turns are preserved; the selected route is applied at a safe turn boundary."
    )


@dataclass
class UsageSnapshot:
    available: bool = False
    limit_id: str = "codex"
    used_percent: float = 0.0
    remaining_percent: float = 0.0
    window_duration_mins: int = 0
    resets_at: int = 0
    plan_type: str = ""
    reset_credits: int = 0
    updated_at: str = ""
    error: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(str(tmp), str(path))


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ipc_frame(message: Dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: List[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("Codex IPC connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_ipc_message(connection: socket.socket) -> Dict[str, Any]:
    length = struct.unpack("<I", _recv_exact(connection, 4))[0]
    if length <= 0 or length > 16 * 1024 * 1024:
        raise ValueError(f"Invalid Codex IPC frame length: {length}")
    return json.loads(_recv_exact(connection, length).decode("utf-8"))


def _wait_ipc_response(
    connection: socket.socket,
    request_id: str,
) -> Dict[str, Any]:
    while True:
        message = _recv_ipc_message(connection)
        if message.get("type") == "response" and message.get("requestId") == request_id:
            if message.get("resultType") == "error":
                raise RuntimeError(str(message.get("error") or "Codex IPC request failed"))
            return message


def codex_ipc_request(
    method: str,
    params: Dict[str, Any],
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Send a versioned request through Codex Desktop's local IPC router."""
    version = IPC_METHOD_VERSIONS.get(method)
    if version is None:
        raise ValueError(f"Unsupported Codex IPC method: {method}")
    if not CODEX_IPC_SOCKET.exists():
        raise FileNotFoundError(f"Codex IPC socket not found: {CODEX_IPC_SOCKET}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(CODEX_IPC_SOCKET))
        initialize_id = str(uuid.uuid4())
        connection.sendall(
            _ipc_frame(
                {
                    "type": "request",
                    "requestId": initialize_id,
                    "sourceClientId": "initializing-client",
                    "version": 0,
                    "method": "initialize",
                    "params": {"clientType": "codex-auto-router"},
                    "timeoutMs": int(timeout * 1000),
                }
            )
        )
        initialized = _wait_ipc_response(connection, initialize_id)
        client_id = str((initialized.get("result") or {}).get("clientId") or "")
        if not client_id:
            raise RuntimeError("Codex IPC initialization returned no client id")
        request_id = str(uuid.uuid4())
        connection.sendall(
            _ipc_frame(
                {
                    "type": "request",
                    "requestId": request_id,
                    "sourceClientId": client_id,
                    "version": version,
                    "method": method,
                    "params": params,
                    "timeoutMs": int(timeout * 1000),
                }
            )
        )
        return _wait_ipc_response(connection, request_id)


def prearm_next_turn(
    thread_id: str,
    model: str,
    effort: str,
    timeout: float = 3.0,
) -> bool:
    try:
        _app_server_request(
            "thread/settings/update",
            {"threadId": thread_id, "model": model, "effort": effort},
            timeout=timeout,
        )
        return True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        if os.name == "nt":
            raise
    response = codex_ipc_request(
        "thread-follower-update-thread-settings",
        {
            "conversationId": thread_id,
            "threadSettings": {"model": model, "effort": effort},
        },
        timeout=timeout,
    )
    return bool((response.get("result") or {}).get("ok", True))


def effort_label(effort: str) -> str:
    return {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "xhigh": "Extra high",
        "max": "Maximum",
        "ultra": "Ultra",
    }.get(effort, effort)


def send_recommendation_notification(
    model: str,
    effort: str,
    reason: str,
) -> bool:
    """Show a best-effort native notification without interpolating user text."""
    if not model:
        return False
    message = f"Use {model} with {effort_label(effort)} reasoning."
    if reason:
        message = f"{reason}\n{message}"
    try:
        if OSASCRIPT_BIN.exists():
            script = (
                "on run argv\n"
                "display notification (item 1 of argv) with title (item 2 of argv) "
                "subtitle (item 3 of argv)\n"
                "end run"
            )
            command = [
                str(OSASCRIPT_BIN), "-e", script, message,
                "Codex model recommendation", "Automatic routing needs attention",
            ]
        else:
            powershell = shutil.which("powershell.exe") or shutil.which("powershell")
            if not powershell:
                return False
            script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                "$n.BalloonTipTitle=$args[0];$n.BalloonTipText=$args[1];"
                "$n.Visible=$true;$n.ShowBalloonTip(5000);Start-Sleep -Seconds 6;$n.Dispose()"
            )
            command = [
                powershell, "-NoProfile", "-WindowStyle", "Hidden", "-Command", script,
                "Codex model recommendation", message,
            ]
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError as exc:
        append_log(f"notification failed: {exc}")
        return False


def read_turn_settings(transcript_path: str, turn_id: str = "") -> Tuple[str, str]:
    """Read the latest matching turn settings from the local rollout tail."""
    if not transcript_path:
        return "", ""
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 2 * 1024 * 1024))
            if handle.tell() > 0:
                handle.readline()
            lines = handle.read().splitlines()
    except OSError:
        return "", ""
    fallback: Tuple[str, str] = ("", "")
    for raw_line in reversed(lines):
        try:
            event = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn_context":
            continue
        payload = event.get("payload") or {}
        model = str(payload.get("model") or "")
        effort = str(payload.get("effort") or payload.get("reasoning_effort") or "")
        if not fallback[0]:
            fallback = (model, effort)
        if not turn_id or str(payload.get("turn_id") or "") == turn_id:
            return model, effort
    return fallback


def transcript_is_root_user_session(transcript_path: str) -> bool:
    if not transcript_path:
        return True
    try:
        with Path(transcript_path).open("rb") as handle:
            for _ in range(8):
                raw_line = handle.readline()
                if not raw_line:
                    break
                event = json.loads(raw_line.decode("utf-8", errors="replace"))
                if event.get("type") != "session_meta":
                    continue
                payload = event.get("payload") or {}
                if payload.get("parent_thread_id"):
                    return False
                source = payload.get("source") or {}
                if isinstance(source, dict) and source.get("subagent"):
                    return False
                return str(payload.get("thread_source") or "user") == "user"
    except (OSError, json.JSONDecodeError):
        return True
    return True


def transcript_turn_has_attachments(transcript_path: str) -> bool:
    if not transcript_path:
        return False
    path = Path(transcript_path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - 2 * 1024 * 1024))
            if handle.tell() > 0:
                handle.readline()
            lines = handle.read().splitlines()
    except OSError:
        return False
    for raw_line in reversed(lines):
        try:
            event = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if event.get("type") == "event_msg" and payload.get("type") == "user_message":
            return any(
                bool(payload.get(field))
                for field in ("images", "local_images", "audio", "local_audio")
            )
        if event.get("type") == "response_item" and payload.get("type") == "message":
            if payload.get("role") != "user":
                continue
            return any(
                isinstance(item, dict)
                and str(item.get("type") or "") in {"input_image", "input_audio", "image", "audio"}
                for item in (payload.get("content") or [])
            )
    return False


def prompt_has_attachment_marker(prompt: str) -> bool:
    return bool(
        re.search(
            r"<(?:image|video|audio|attachment)\b|\[(?:attached|attachment|image|video|audio)\b",
            prompt,
            flags=re.IGNORECASE,
        )
    )


def prompt_has_explicit_model_choice(prompt: str) -> bool:
    return bool(
        re.search(
            r"gpt-5\.6-(?:luna|terra|sol)|(?:루나|테라|솔)\s*(?:모델)?(?:로|을|를|사용)",
            prompt,
            flags=re.IGNORECASE,
        )
    )


def _write_prompt_route_request(payload: Dict[str, Any]) -> Optional[Path]:
    PROMPT_ROUTE_DIR.mkdir(parents=True, exist_ok=True)
    safe_turn_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("turn_id") or uuid.uuid4()))
    path = PROMPT_ROUTE_DIR / f"{safe_turn_id}.json"
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def run_prompt_submit_hook() -> int:
    """Route every textual user request before it spends model tokens."""
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if hook_input.get("hook_event_name") != "UserPromptSubmit":
        return 0
    if os.environ.get("CODEX_AUTO_ROUTER_RESUBMIT") == "1":
        return 0

    config = load_config()
    prompt = normalize_user_prompt(str(hook_input.get("prompt") or "")).strip()
    if not prompt:
        return 0
    guard = usage_guard_config(config)
    if guard.get("enabled", False):
        max_age = float(guard.get("max_cache_age_seconds", 300))
        usage = cached_weekly_usage(max_age)
        if not usage.available:
            usage = query_weekly_usage(
                timeout=float(guard.get("query_timeout_seconds", 3))
            )
        if usage_guard_is_paused(config, usage):
            threshold = float(guard.get("pause_at_remaining_percent", 10))
            append_log(
                f"usage guard blocked prompt remaining={usage.remaining_percent:.1f}% "
                f"threshold={threshold:.1f}%"
            )
            print(json.dumps({
                "decision": "block",
                "reason": (
                    f"Weekly usage guard is paused at {usage.remaining_percent:.0f}% remaining "
                    f"(threshold: {threshold:.0f}%). Disable the guard or wait for reset."
                ),
            }))
            return 0
    if prompt_has_explicit_model_choice(prompt):
        return 0
    decision = classify_task(prompt, config, source="prompt_submit")
    turn_id = str(hook_input.get("turn_id") or "")
    transcript_path = str(hook_input.get("transcript_path") or "")
    if not transcript_is_root_user_session(transcript_path):
        return 0
    current_model = str(hook_input.get("model") or "")
    transcript_model, current_effort = read_turn_settings(transcript_path, turn_id)
    current_model = current_model or transcript_model
    same_route = current_model == decision.model and (
        not current_effort or current_effort == decision.effort
    )
    if same_route:
        append_log(
            f"prompt route optimal thread={hook_input.get('session_id') or '-'} "
            f"turn={turn_id or '-'} model={decision.model} effort={decision.effort}"
        )
        return 0

    if config.get("prompt_submit_notification", True):
        send_recommendation_notification(
            decision.model,
            decision.effort,
            "This request was analyzed before submission.",
        )
    if not config.get("prompt_submit_reroute", True):
        return 0
    has_attachments = prompt_has_attachment_marker(prompt) or transcript_turn_has_attachments(
        transcript_path
    )
    if config.get("prompt_submit_skip_attachments", True) and has_attachments:
        append_log(f"prompt reroute skipped attachment turn={turn_id or '-'}")
        return 0

    thread_id = str(hook_input.get("session_id") or "")
    if not thread_id:
        append_log(f"prompt reroute skipped missing-session turn={turn_id or '-'}")
        return 0
    request_path = _write_prompt_route_request(
        {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "prompt": prompt,
            "cwd": str(hook_input.get("cwd") or ROOT),
            "model": decision.model,
            "effort": decision.effort,
            "delay": float(config.get("prompt_submit_delay_seconds", 0.8)),
        }
    )
    if request_path is None:
        append_log(f"prompt reroute deduplicated thread={thread_id} turn={turn_id or '-'}")
        print(json.dumps({"decision": "block", "reason": "Automatic rerouting is in progress."}))
        return 0
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_resume-routed", "--request", str(request_path)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        request_path.unlink(missing_ok=True)
        append_log(f"prompt reroute spawn failed thread={thread_id}: {exc}")
        return 0
    append_log(
        f"prompt reroute scheduled thread={thread_id} turn={turn_id or '-'} "
        f"{current_model or '-'}/{current_effort or '-'} -> {decision.model}/{decision.effort}"
    )
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    f"The router selected {decision.model}/{decision.effort} and is "
                    "resubmitting the same request."
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


def resume_routed_request(request_path: Path) -> int:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        time.sleep(max(0.2, min(float(request.get("delay") or 0.8), 3.0)))
        env = os.environ.copy()
        env["CODEX_AUTO_ROUTER_RESUBMIT"] = "1"
        command = [
            str(CODEX_BIN),
            "exec",
            "resume",
            "-m",
            str(request["model"]),
            "-c",
            f'model_reasoning_effort="{request["effort"]}"',
            str(request["thread_id"]),
            str(request["prompt"]),
        ]
        AUTO_RUN_DIR.mkdir(parents=True, exist_ok=True)
        log_path = AUTO_RUN_DIR / f"prompt-{request['thread_id']}.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            result = subprocess.call(
                command,
                cwd=str(request.get("cwd") or ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
            )
        append_log(
            f"prompt reroute finished thread={request['thread_id']} "
            f"model={request['model']} effort={request['effort']} exit={result}"
        )
        if result != 0:
            send_recommendation_notification(
                str(request["model"]),
                str(request["effort"]),
                "Automatic reroute failed. Retry this prompt with the recommended model.",
            )
        return int(result)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        append_log(f"prompt reroute helper failed request={request_path}: {exc}")
        return 1
    finally:
        request_path.unlink(missing_ok=True)


def discover_session(thread_id: Optional[str] = None) -> Path:
    if not SESSION_ROOT.exists():
        raise FileNotFoundError(f"Codex session directory not found: {SESSION_ROOT}")
    if thread_id:
        matches = list(SESSION_ROOT.rglob(f"*{thread_id}*.jsonl"))
    else:
        matches = list(SESSION_ROOT.rglob("rollout-*.jsonl"))
    if not matches:
        target = thread_id or "latest"
        raise FileNotFoundError(f"No Codex rollout found for {target}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def read_session_meta(path: Path) -> Dict[str, Any]:
    try:
        with path.open("rb") as handle:
            for _ in range(5):
                raw_line = handle.readline()
                if not raw_line:
                    break
                event = json.loads(raw_line.decode("utf-8", errors="replace"))
                if event.get("type") == "session_meta":
                    return event.get("payload") or {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def discover_active_root_sessions(config: Dict[str, Any]) -> Dict[str, Tuple[Path, float]]:
    cutoff = time.time() - int(config.get("activity_window_seconds", 1800))
    excluded_thread_ids = {
        str(value) for value in config.get("excluded_thread_ids", []) if value
    }
    recent: List[Tuple[Path, Dict[str, Any], float]] = []
    for path in SESSION_ROOT.rglob("rollout-*.jsonl"):
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if modified < cutoff:
            continue
        meta = read_session_meta(path)
        if meta:
            recent.append((path, meta, modified))

    active_roots: Dict[str, Tuple[Path, float]] = {}
    for path, meta, modified in recent:
        thread_id = str(meta.get("id") or meta.get("session_id") or "")
        parent_id = str(meta.get("parent_thread_id") or "")
        thread_source = meta.get("thread_source")
        if parent_id:
            root_id = parent_id
            try:
                root_path = discover_session(root_id)
            except FileNotFoundError:
                continue
        elif thread_source == "user" or (thread_source is None and thread_id):
            root_id = thread_id
            root_path = path
        else:
            continue
        if root_id in excluded_thread_ids:
            continue
        previous = active_roots.get(root_id)
        if previous is None or modified > previous[1]:
            active_roots[root_id] = (root_path, modified)
    return active_roots


def extract_message_text(payload: Dict[str, Any]) -> str:
    texts: List[str] = []
    for item in payload.get("content") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("text") or item.get("input_text") or item.get("output_text")
        if isinstance(value, str):
            texts.append(value)
    return "\n".join(texts).strip()


def normalize_user_prompt(text: str) -> str:
    if '<codex_internal_context source="goal">' not in text:
        return text
    match = re.search(r"<objective>\s*(.*?)\s*</objective>", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def extract_next_step(text: str) -> str:
    if not text:
        return ""
    for pattern in NEXT_STEP_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            candidate = re.split(r"(?<=[.!?。])\s+", candidate, maxsplit=1)[0]
            candidate = candidate[:500].strip()
            if matches_any(ACTION_PLAN_PATTERNS, candidate):
                return candidate
    return ""


def matches_any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_task(
    text: str,
    config: Dict[str, Any],
    telemetry: Optional[Telemetry] = None,
    source: str = "user_prompt",
) -> Decision:
    telemetry = telemetry or Telemetry()
    normalized = " ".join((text or "").split())
    score = 0
    reasons: List[str] = []

    if matches_any(SIMPLE_PATTERNS, normalized):
        score -= 1
        reasons.append("simple lookup or explanation signal")
    if matches_any(NORMAL_PATTERNS, normalized):
        score += 2
        reasons.append("implementation or editing signal")
    if matches_any(COMPLEX_PATTERNS, normalized):
        score += 4
        reasons.append("complex tooling or automation signal")
    if matches_any(CRITICAL_PATTERNS, normalized):
        score += 8
        reasons.append("high-risk or high-accuracy signal")

    length = len(normalized)
    if length >= 1200:
        score += 2
        reasons.append("long task description")
    elif length >= 500:
        score += 1
        reasons.append("medium-length task description")

    thresholds = config["thresholds"]
    if telemetry.failures_this_turn >= thresholds["failure_escalation_count"]:
        score += 3
        reasons.append(f"{telemetry.failures_this_turn} failures in the current turn")
    if telemetry.tool_output_bytes_this_turn >= thresholds["large_tool_output_bytes"]:
        score += 1
        reasons.append("large tool output")
    recommend_compact = telemetry.context_ratio >= thresholds["context_warn_ratio"]
    if recommend_compact:
        score += 1
        reasons.append(f"context usage {telemetry.context_ratio:.0%}")

    if not reasons:
        reasons.append("default general-work route")

    routes = list(config.get("routes") or [])
    if not routes:
        raise ValueError("router_config.json has no routes")
    parallel_signal = matches_any(PARALLEL_PATTERNS, normalized)
    selected: Optional[Dict[str, Any]] = None
    for route in routes:
        if score > int(route.get("max_score", 999)):
            continue
        if route.get("requires_parallel_signal") and not parallel_signal:
            continue
        selected = route
        break
    if selected is None:
        eligible = [route for route in routes if not route.get("requires_parallel_signal")]
        selected = eligible[-1] if eligible else routes[-1]

    tier = str(selected["tier"])
    model_name = str(selected["model"])
    effort = str(selected["effort"])
    supported = list((config.get("model_capabilities") or {}).get(model_name) or [])
    if supported and effort not in supported:
        effort = supported[-1]
        reasons.append(f"reasoning effort adjusted to installed model support: {effort}")
    if parallel_signal and effort == "ultra":
        reasons.append("explicit parallel or exhaustive-work signal")
    should_interrupt = bool(
        config.get("auto_interrupt", False)
        and telemetry.failures_this_turn >= thresholds["failure_escalation_count"] + 1
        and model_name == "gpt-5.6-sol"
    )
    return Decision(
        tier=tier,
        model=model_name,
        effort=effort,
        score=score,
        reasons=reasons,
        source=source,
        task_preview=normalized[:220],
        recommend_compact=recommend_compact,
        should_interrupt=should_interrupt,
    )


def stabilize_model_switch(
    decision: Decision,
    current_model: str,
    config: Dict[str, Any],
) -> Decision:
    """Return the task route directly so a cheaper next task downgrades promptly."""
    return decision


class RolloutObserver:
    def __init__(self, session_path: Path, config: Dict[str, Any]) -> None:
        self.session_path = session_path
        self.config = config
        self.state = RouterState(
            session_path=str(session_path),
            strategy=config.get("strategy", "turn_boundary"),
            watcher_pid=os.getpid(),
            running=True,
            updated_at=utc_now(),
        )

    def process_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        payload = event.get("payload") or {}
        event_type = event.get("type")
        payload_type = payload.get("type")

        if event_type == "session_meta":
            self.state.thread_id = str(payload.get("id") or self.state.thread_id)
            self.state.cwd = str(payload.get("cwd") or self.state.cwd)

        if event_type == "turn_context":
            turn_id = str(payload.get("turn_id") or "")
            next_model = str(payload.get("model") or self.state.current_model)
            next_effort = str(
                payload.get("effort")
                or payload.get("reasoning_effort")
                or self.state.current_effort
            )
            if (
                self.state.prearm_verification == "pending"
                and self.state.prearmed_turn_id
                and turn_id
                and turn_id != self.state.prearmed_turn_id
            ):
                applied = (
                    next_model == self.state.prearmed_model
                    and next_effort == self.state.prearmed_effort
                )
                self.state.prearm_verification = "applied" if applied else "missed"
                self.state.auto_apply_status = "prearm_applied" if applied else "prearm_missed"
                self.state.note = (
                    f"Verified route on the new turn: {next_model}/{next_effort}"
                    if applied
                    else (
                        f"Pre-arm mismatch: expected {self.state.prearmed_model}/"
                        f"{self.state.prearmed_effort}, actual {next_model}/{next_effort}"
                    )
                )
                append_log(
                    f"prearm {'applied' if applied else 'missed'} "
                    f"thread={self.state.thread_id} turn={turn_id} "
                    f"model={next_model} effort={next_effort}"
                )
            if turn_id:
                self.state.last_turn_id = turn_id
            self.state.turn_active = True
            self.state.last_turn_status = "in_progress"
            self.state.current_model = next_model
            self.state.current_effort = next_effort

        if event_type == "event_msg" and payload_type == "task_started":
            self.state.turn_active = True
            self.state.last_turn_status = "in_progress"
            self.state.last_turn_id = str(payload.get("turn_id") or self.state.last_turn_id)

        if event_type == "event_msg" and payload_type == "task_complete":
            self.state.turn_active = False
            self.state.last_turn_status = "completed"
            self.state.last_turn_id = str(payload.get("turn_id") or self.state.last_turn_id)

        if event_type == "event_msg" and payload_type in {"turn_aborted", "task_aborted"}:
            self.state.turn_active = False
            self.state.last_turn_status = "interrupted"
            self.state.last_turn_id = str(payload.get("turn_id") or self.state.last_turn_id)

        if event_type == "response_item" and payload_type == "message":
            role = payload.get("role")
            text = extract_message_text(payload)
            if role == "user" and text:
                text = normalize_user_prompt(text)
            if role == "user" and text:
                self.state.latest_user_prompt = text
                self.state.planned_next_step = ""
                self.state.telemetry.failures_this_turn = 0
                self.state.telemetry.tool_output_bytes_this_turn = 0
                self.state.decision = classify_task(
                    text, self.config, self.state.telemetry, source="user_prompt"
                )
                self.state.decision = stabilize_model_switch(
                    self.state.decision, self.state.current_model, self.config
                )
            elif role == "assistant" and text:
                self.state.latest_assistant_message = text[-2000:]
                next_step = extract_next_step(text)
                if next_step:
                    self.state.planned_next_step = next_step
                    self.state.decision = classify_task(
                        next_step,
                        self.config,
                        self.state.telemetry,
                        source="announced_next_step",
                    )
                    self.state.decision = stabilize_model_switch(
                        self.state.decision, self.state.current_model, self.config
                    )

        if event_type == "response_item" and payload_type == "custom_tool_call_output":
            size = len(line.encode("utf-8"))
            self.state.telemetry.tool_output_bytes_this_turn += size
            output = str(payload.get("output") or "")
            if matches_any(FAILURE_PATTERNS, output):
                self.state.telemetry.failures_this_turn += 1

        if event_type == "event_msg" and payload_type == "mcp_tool_call_end":
            size = len(line.encode("utf-8"))
            self.state.telemetry.tool_output_bytes_this_turn += size
            result_text = json.dumps(payload.get("result"), ensure_ascii=False)
            if '"isError": true' in result_text or matches_any(FAILURE_PATTERNS, result_text):
                self.state.telemetry.failures_this_turn += 1

        if event_type == "event_msg" and payload_type == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            last = info.get("last_token_usage") or {}
            context_window = int(info.get("model_context_window") or 0)
            last_input = int(last.get("input_tokens") or 0)
            self.state.telemetry.cumulative_input_tokens = int(total.get("input_tokens") or 0)
            self.state.telemetry.cumulative_cached_input_tokens = int(
                total.get("cached_input_tokens") or 0
            )
            self.state.telemetry.cumulative_output_tokens = int(total.get("output_tokens") or 0)
            self.state.telemetry.context_window = context_window
            if context_window > 0 and last_input > 0:
                self.state.telemetry.context_ratio = min(last_input / context_window, 1.0)

        task_text = self.state.latest_user_prompt
        if task_text and not self.state.planned_next_step:
            self.state.decision = classify_task(
                task_text, self.config, self.state.telemetry, source="user_prompt"
            )
            self.state.decision = stabilize_model_switch(
                self.state.decision, self.state.current_model, self.config
            )
        self.state.updated_at = utc_now()

    def snapshot(self) -> Dict[str, Any]:
        self.state.watcher_pid = os.getpid()
        self.state.running = True
        self.state.updated_at = utc_now()
        return asdict(self.state)


@dataclass
class WatchedRollout:
    path: Path
    observer: RolloutObserver
    offset: int
    activity_at: float


@dataclass
class AutoRun:
    process: subprocess.Popen[Any]
    log_handle: Any
    key: str
    model: str
    effort: str
    started_at: float


def _app_server_request(
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Call a local Codex app-server method. This does not invoke a model."""
    process = subprocess.Popen(
        [str(CODEX_BIN), "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        messages = [
            {
                "method": "initialize",
                "id": 0,
                "params": {
                    "clientInfo": {
                        "name": "codex_auto_router",
                        "title": "Codex Auto Router",
                        "version": "0.3.0",
                    }
                },
            },
            {"method": "initialized", "params": {}},
            {"method": method, "id": 1, **({"params": params} if params is not None else {})},
        ]
        for message in messages:
            process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            events = selector.select(max(0.05, deadline - time.monotonic()))
            if not events:
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == 1:
                if response.get("error"):
                    raise RuntimeError(str(response["error"]))
                return response.get("result") or {}
        raise TimeoutError(f"Codex app-server request timed out: {method}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()


def query_thread_goal(thread_id: str, timeout: float = 5.0) -> Dict[str, Any]:
    try:
        return (_app_server_request(
            "thread/goal/get", {"threadId": thread_id}, timeout=timeout
        ).get("goal") or {})
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
        return {}


def parse_weekly_usage(result: Dict[str, Any]) -> UsageSnapshot:
    """Normalize the Codex quota bucket with the longest rolling window."""
    buckets = result.get("rateLimitsByLimitId") or {}
    snapshot = buckets.get("codex") or result.get("rateLimits") or {}
    windows = [
        value for value in (snapshot.get("primary"), snapshot.get("secondary"))
        if isinstance(value, dict)
    ]
    if not windows:
        return UsageSnapshot(updated_at=utc_now(), error="No Codex quota window returned")
    window = max(windows, key=lambda value: int(value.get("windowDurationMins") or 0))
    used = min(max(float(window.get("usedPercent") or 0), 0.0), 100.0)
    reset_summary = result.get("rateLimitResetCredits") or {}
    return UsageSnapshot(
        available=True,
        limit_id=str(snapshot.get("limitId") or "codex"),
        used_percent=used,
        remaining_percent=100.0 - used,
        window_duration_mins=int(window.get("windowDurationMins") or 0),
        resets_at=int(window.get("resetsAt") or 0),
        plan_type=str(snapshot.get("planType") or ""),
        reset_credits=int(reset_summary.get("availableCount") or 0),
        updated_at=utc_now(),
    )


def query_weekly_usage(timeout: float = 5.0) -> UsageSnapshot:
    try:
        snapshot = parse_weekly_usage(
            _app_server_request("account/rateLimits/read", timeout=timeout)
        )
    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
        snapshot = UsageSnapshot(updated_at=utc_now(), error=str(exc))
    if snapshot.available:
        atomic_write_json(USAGE_PATH, asdict(snapshot))
    return snapshot


def cached_weekly_usage(max_age_seconds: float = 300.0) -> UsageSnapshot:
    try:
        value = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        modified_age = max(0.0, time.time() - USAGE_PATH.stat().st_mtime)
        if modified_age > max_age_seconds:
            return UsageSnapshot(error="Cached usage is stale")
        return UsageSnapshot(**{key: value[key] for key in UsageSnapshot.__dataclass_fields__ if key in value})
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return UsageSnapshot(error="No cached usage")


def usage_guard_config(config: Dict[str, Any]) -> Dict[str, Any]:
    value = config.get("usage_guard") or {}
    return value if isinstance(value, dict) else {}


def usage_guard_is_paused(config: Dict[str, Any], usage: UsageSnapshot) -> bool:
    guard = usage_guard_config(config)
    return bool(
        guard.get("enabled", False)
        and usage.available
        and usage.remaining_percent <= float(guard.get("pause_at_remaining_percent", 10))
    )


def usage_guard_state(config: Dict[str, Any], usage: UsageSnapshot) -> Dict[str, Any]:
    guard = usage_guard_config(config)
    return {
        "enabled": bool(guard.get("enabled", False)),
        "pause_at_remaining_percent": float(guard.get("pause_at_remaining_percent", 10)),
        "paused": usage_guard_is_paused(config, usage),
        "mode": "safe_turn_boundary",
        "note": "Active turns finish safely; new prompts and automatic follow-ups are paused.",
    }


def auto_apply_key(state: RouterState) -> str:
    decision = state.decision
    if decision is None:
        return ""
    return f"{state.last_turn_id}:{decision.model}:{decision.effort}"


def auto_apply_eligible(state: RouterState, config: Dict[str, Any]) -> bool:
    if not config.get("auto_apply", False) or state.decision is None:
        return False
    if state.turn_active:
        return False
    allowed_statuses = {"completed"}
    if config.get("resume_interrupted_goals", False):
        allowed_statuses.add("interrupted")
    if state.last_turn_status not in allowed_statuses:
        return False
    return (
        state.current_model != state.decision.model
        or state.current_effort != state.decision.effort
    )


class MultiRolloutObserver:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.tasks: Dict[str, WatchedRollout] = {}
        self.last_discovery = 0.0
        self.auto_runs: Dict[str, AutoRun] = {}
        self.applied_keys: set[str] = set()
        self.last_auto_apply: Dict[str, float] = {}
        self.last_notification: Dict[str, float] = {}
        self.prearmed_keys: set[str] = set()
        self.last_prearm_attempt: Dict[str, float] = {}
        self.usage = UsageSnapshot(updated_at=utc_now())
        self.last_usage_refresh = 0.0
        self.last_config_check = 0.0
        try:
            self.config_mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            self.config_mtime = 0.0

    def _reload_config(self) -> None:
        now = time.monotonic()
        if now - self.last_config_check < 2:
            return
        self.last_config_check = now
        try:
            modified = CONFIG_PATH.stat().st_mtime
            if modified == self.config_mtime:
                return
            updated = load_config()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            append_log(f"config reload failed: {exc}")
            return
        self.config.clear()
        self.config.update(updated)
        self.config_mtime = modified
        append_log("configuration reloaded")

    def _refresh_usage(self, force: bool = False) -> None:
        interval = float(self.config.get("usage_poll_interval_seconds", 300))
        now = time.monotonic()
        if not force and now - self.last_usage_refresh < interval:
            return
        self.last_usage_refresh = now
        updated = query_weekly_usage(
            timeout=float(usage_guard_config(self.config).get("query_timeout_seconds", 3))
        )
        if updated.available or not self.usage.available:
            self.usage = updated

    def _usage_paused(self) -> bool:
        return usage_guard_is_paused(self.config, self.usage)

    def _notify_manual_action(
        self,
        thread_id: str,
        state: RouterState,
        reason: str,
        status: str,
    ) -> None:
        decision = state.decision
        if decision is None or not self.config.get("fallback_notifications", True):
            return
        cooldown = float(self.config.get("notification_cooldown_seconds", 300))
        key = f"{thread_id}:{status}:{decision.model}:{decision.effort}"
        now = time.monotonic()
        last = self.last_notification.get(key)
        if last is not None and now - last < cooldown:
            return
        if send_recommendation_notification(decision.model, decision.effort, reason):
            self.last_notification[key] = now
            append_log(
                f"manual recommendation notified thread={thread_id} "
                f"status={status} model={decision.model} effort={decision.effort}"
            )

    def refresh_discovery(self, force: bool = False) -> None:
        now = time.monotonic()
        interval = float(self.config.get("discovery_interval_seconds", 5))
        if not force and now - self.last_discovery < interval:
            return
        self.last_discovery = now
        active = discover_active_root_sessions(self.config)
        for thread_id, (path, activity_at) in active.items():
            watched = self.tasks.get(thread_id)
            if watched is None or watched.path != path:
                self.tasks[thread_id] = self._initialize(path, activity_at)
            else:
                watched.activity_at = max(watched.activity_at, activity_at)
        for thread_id in list(self.tasks):
            if thread_id not in active:
                del self.tasks[thread_id]

    def _initialize(self, path: Path, activity_at: float) -> WatchedRollout:
        observer = RolloutObserver(path, self.config)
        tail_bytes = int(self.config.get("initial_tail_bytes", 33554432))
        with path.open("rb") as handle:
            first_line = handle.readline()
            if first_line:
                observer.process_line(first_line.decode("utf-8", errors="replace"))
            size = path.stat().st_size
            start = max(0, size - tail_bytes)
            if start > len(first_line):
                handle.seek(start)
                handle.readline()
            else:
                handle.seek(len(first_line))
            while True:
                position = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    handle.seek(position)
                    break
                observer.process_line(raw_line.decode("utf-8", errors="replace"))
            offset = handle.tell()
        observer.state.activity_at = activity_at
        return WatchedRollout(path, observer, offset, activity_at)

    def poll(self) -> None:
        self._reload_config()
        self._refresh_usage()
        self.refresh_discovery()
        self._reap_auto_runs()
        for thread_id, watched in list(self.tasks.items()):
            try:
                size = watched.path.stat().st_size
            except OSError:
                continue
            if size < watched.offset:
                self.tasks[thread_id] = self._initialize(watched.path, watched.activity_at)
                continue
            if size == watched.offset:
                continue
            with watched.path.open("rb") as handle:
                handle.seek(watched.offset)
                while True:
                    position = handle.tell()
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    if not raw_line.endswith(b"\n"):
                        handle.seek(position)
                        break
                    watched.observer.process_line(raw_line.decode("utf-8", errors="replace"))
                    watched.activity_at = time.time()
                watched.offset = handle.tell()
            watched.observer.state.activity_at = watched.activity_at
        self._schedule_prearm()
        self._schedule_auto_apply()

    def _schedule_prearm(self) -> None:
        if self._usage_paused():
            for watched in self.tasks.values():
                if not watched.observer.state.turn_active:
                    watched.observer.state.auto_apply_status = "usage_paused"
            return
        if not self.config.get("prearm_next_turn", True):
            return
        timeout = float(self.config.get("desktop_ipc_timeout_seconds", 3))
        cooldown = float(self.config.get("prearm_retry_cooldown_seconds", 10))
        now = time.monotonic()
        for thread_id, watched in self.tasks.items():
            state = watched.observer.state
            decision = state.decision
            if (
                not state.turn_active
                or decision is None
                or decision.source != "announced_next_step"
                or not state.planned_next_step
            ):
                continue
            key = (
                f"{state.last_turn_id}:{decision.model}:{decision.effort}:"
                f"{state.planned_next_step}"
            )
            if key in self.prearmed_keys:
                state.auto_apply_status = "next_turn_prearmed"
                continue
            last_attempt = self.last_prearm_attempt.get(thread_id)
            if last_attempt is not None and now - last_attempt < cooldown:
                continue
            self.last_prearm_attempt[thread_id] = now
            state.auto_apply_status = "prearming"
            try:
                if not prearm_next_turn(
                    thread_id,
                    decision.model,
                    decision.effort,
                    timeout=timeout,
                ):
                    raise RuntimeError("Codex Desktop rejected next-turn settings")
            except (OSError, RuntimeError, ValueError, ConnectionError) as exc:
                state.auto_apply_status = "prearm_failed"
                state.note = f"Could not pre-arm the next-turn route: {exc}"
                append_log(f"prearm failed thread={thread_id}: {exc}")
                self._notify_manual_action(
                    thread_id,
                    state,
                    "The next-turn route could not be pre-armed.",
                    "prearm_failed",
                )
                continue
            self.prearmed_keys.add(key)
            state.prearmed_model = decision.model
            state.prearmed_effort = decision.effort
            state.prearmed_turn_id = state.last_turn_id
            state.prearm_verification = "pending"
            state.auto_apply_status = "next_turn_prearmed"
            state.note = (
                f"Next turn pre-armed: {decision.model}/{decision.effort}. "
                f"The active turn was not interrupted."
            )
            append_log(
                f"next-turn prearmed thread={thread_id} "
                f"model={decision.model} effort={decision.effort}"
            )

    def _reap_auto_runs(self) -> None:
        for thread_id, run in list(self.auto_runs.items()):
            watched = self.tasks.get(thread_id)
            return_code = run.process.poll()
            if return_code is None:
                if watched:
                    state = watched.observer.state
                    state.auto_apply_status = "running"
                    state.auto_apply_pid = run.process.pid
                continue
            run.log_handle.close()
            del self.auto_runs[thread_id]
            if watched:
                state = watched.observer.state
                state.auto_apply_status = "completed" if return_code == 0 else "failed"
                state.auto_apply_pid = 0
                state.note = (
                    f"Automatic reroute completed: {run.model}/{run.effort}"
                    if return_code == 0
                    else f"Automatic reroute failed (exit={return_code})"
                )
            if return_code != 0:
                self.applied_keys.discard(run.key)
                self.last_auto_apply[thread_id] = time.monotonic()
                if watched:
                    self._notify_manual_action(
                        thread_id,
                        watched.observer.state,
                        "Automatic model routing failed.",
                        "failed",
                    )
            append_log(f"auto-apply exited thread={thread_id} code={return_code}")

    def _schedule_auto_apply(self) -> None:
        if self._usage_paused():
            for watched in self.tasks.values():
                state = watched.observer.state
                if not state.turn_active:
                    state.auto_apply_status = "usage_paused"
                    state.note = (
                        f"Weekly usage guard paused automatic follow-ups at "
                        f"{self.usage.remaining_percent:.0f}% remaining."
                    )
            return
        if not self.config.get("auto_apply", False):
            for thread_id, watched in self.tasks.items():
                state = watched.observer.state
                if auto_apply_eligible(state, {**self.config, "auto_apply": True}):
                    self._notify_manual_action(
                        thread_id,
                        state,
                        "Automatic model routing is disabled.",
                        "disabled",
                    )
            return
        cooldown = float(self.config.get("auto_apply_cooldown_seconds", 15))
        goal_timeout = float(self.config.get("goal_query_timeout_seconds", 5))
        now = time.monotonic()
        for thread_id, watched in self.tasks.items():
            state = watched.observer.state
            state.mode = "auto_apply"
            if thread_id in self.auto_runs:
                continue
            if not auto_apply_eligible(state, self.config):
                if state.turn_active:
                    if (
                        state.prearmed_turn_id == state.last_turn_id
                        and state.decision is not None
                        and state.prearmed_model == state.decision.model
                        and state.prearmed_effort == state.decision.effort
                    ):
                        state.auto_apply_status = "next_turn_prearmed"
                    elif state.auto_apply_status != "prearm_failed":
                        state.auto_apply_status = (
                            "prearm_applied"
                            if state.prearm_verification == "applied"
                            else "prearm_missed"
                            if state.prearm_verification == "missed"
                            else "waiting_next_step"
                        )
                elif state.decision and (
                    state.current_model == state.decision.model
                    and state.current_effort == state.decision.effort
                ):
                    state.auto_apply_status = "already_optimal"
                continue
            key = auto_apply_key(state)
            if not key or key in self.applied_keys:
                continue
            last_attempt = self.last_auto_apply.get(thread_id)
            if last_attempt is not None and now - last_attempt < cooldown:
                if not state.auto_apply_status.startswith("goal_"):
                    state.auto_apply_status = "cooldown"
                continue
            goal = query_thread_goal(thread_id, timeout=goal_timeout)
            goal_status = str(goal.get("status") or "missing")
            state.goal_status = goal_status
            has_announced_follow_up = bool(
                state.planned_next_step
                and state.decision
                and state.decision.source == "announced_next_step"
            )
            if self.config.get("require_active_goal", False) and goal_status != "active":
                state.auto_apply_status = f"goal_{goal_status}"
                state.note = "Automatic continuation skipped because the goal is not active."
                self.last_auto_apply[thread_id] = now
                reason = (
                    "The goal is blocked, so automatic routing cannot continue it."
                    if goal_status == "blocked"
                    else "There is no active goal to continue automatically."
                )
                self._notify_manual_action(
                    thread_id,
                    state,
                    reason,
                    f"goal_{goal_status}",
                )
                continue
            if goal_status != "active" and not has_announced_follow_up:
                state.auto_apply_status = "non_goal_complete"
                state.note = (
                    "The non-goal request is complete, so it will not be resumed. "
                    "The next request will be classified by the submit hook."
                )
                self.last_auto_apply[thread_id] = now
                continue
            self._launch_auto_apply(thread_id, watched, key)
            self.last_auto_apply[thread_id] = now
            self.applied_keys.add(key)

    def _launch_auto_apply(
        self,
        thread_id: str,
        watched: WatchedRollout,
        key: str,
    ) -> None:
        state = watched.observer.state
        decision = state.decision
        if decision is None:
            return
        prompt = state.planned_next_step or str(
            self.config.get(
                "continuation_prompt",
                "Continue the active goal and prioritize the previously announced next step.",
            )
        )
        command = [
            str(CODEX_BIN),
            "exec",
            "resume",
            "-m",
            decision.model,
            "-c",
            f'model_reasoning_effort="{decision.effort}"',
            thread_id,
            prompt,
        ]
        AUTO_RUN_DIR.mkdir(parents=True, exist_ok=True)
        log_path = AUTO_RUN_DIR / f"{thread_id}.log"
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(
            f"\n{utc_now()} auto-apply {state.current_model}/{state.current_effort} "
            f"-> {decision.model}/{decision.effort}\n"
        )
        log_handle.flush()
        try:
            process = subprocess.Popen(
                command,
                cwd=state.cwd or str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
        except OSError as exc:
            log_handle.write(f"launch failed: {exc}\n")
            log_handle.close()
            state.auto_apply_status = "failed"
            state.note = f"Could not launch automatic rerouting: {exc}"
            self._notify_manual_action(
                thread_id,
                state,
                "Automatic model routing could not be started.",
                "launch_failed",
            )
            append_log(f"auto-apply launch failed thread={thread_id}: {exc}")
            return
        self.auto_runs[thread_id] = AutoRun(
            process=process,
            log_handle=log_handle,
            key=key,
            model=decision.model,
            effort=decision.effort,
            started_at=time.time(),
        )
        state.auto_apply_status = "switching"
        state.auto_apply_model = decision.model
        state.auto_apply_effort = decision.effort
        state.auto_apply_pid = process.pid
        state.note = f"Automatic reroute started: {decision.model}/{decision.effort}"
        append_log(
            f"auto-apply started thread={thread_id} pid={process.pid} "
            f"model={decision.model} effort={decision.effort}"
        )

    def snapshot(self, running: bool = True) -> Dict[str, Any]:
        ordered = sorted(self.tasks.values(), key=lambda item: item.activity_at, reverse=True)
        task_states: List[Dict[str, Any]] = []
        for watched in ordered:
            watched.observer.state.watcher_pid = os.getpid()
            watched.observer.state.running = running
            watched.observer.state.updated_at = utc_now()
            watched.observer.state.activity_at = watched.activity_at
            task_states.append(asdict(watched.observer.state))
        return {
            "version": 3,
            "multi": True,
            "running": running,
            "watcher_pid": os.getpid(),
            "active_task_count": len(task_states),
            "updated_at": utc_now(),
            "mode": "multi_auto_apply" if self.config.get("auto_apply") else "multi_observe",
            "strategy": self.config.get("strategy", "turn_boundary"),
            "usage": asdict(self.usage),
            "usage_guard": usage_guard_state(self.config, self.usage),
            "tasks": task_states,
        }


def append_log(message: str) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {message}\n")


def watch_worker(session_path: Path, config: Dict[str, Any]) -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    observer = RolloutObserver(session_path, config)
    stopping = False

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    append_log(f"watch started pid={os.getpid()} session={session_path}")

    try:
        initial_size = session_path.stat().st_size
        with session_path.open("rb") as handle:
            initial_data = handle.read(initial_size)
            for index, raw_line in enumerate(initial_data.splitlines(), start=1):
                observer.process_line(raw_line.decode("utf-8", errors="replace"))
                if index % 250 == 0:
                    atomic_write_json(STATE_PATH, observer.snapshot())
            atomic_write_json(STATE_PATH, observer.snapshot())
            last_heartbeat = time.monotonic()
            while not stopping:
                position = handle.tell()
                raw_line = handle.readline()
                if raw_line:
                    if not raw_line.endswith(b"\n"):
                        handle.seek(position)
                        time.sleep(config.get("poll_interval_seconds", 0.75))
                        continue
                    observer.process_line(raw_line.decode("utf-8", errors="replace"))
                    atomic_write_json(STATE_PATH, observer.snapshot())
                    continue
                handle.seek(position)
                if time.monotonic() - last_heartbeat >= 5:
                    atomic_write_json(STATE_PATH, observer.snapshot())
                    last_heartbeat = time.monotonic()
                time.sleep(config.get("poll_interval_seconds", 0.75))
    finally:
        observer.state.running = False
        observer.state.updated_at = utc_now()
        atomic_write_json(STATE_PATH, asdict(observer.state))
        try:
            recorded_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
            if recorded_pid == os.getpid():
                PID_PATH.unlink()
        except (FileNotFoundError, ValueError):
            pass
        append_log(f"watch stopped pid={os.getpid()}")
    return 0


def watch_all_worker(config: Dict[str, Any]) -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    observer = MultiRolloutObserver(config)
    stopping = False

    def stop_handler(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    append_log(f"multi-watch started pid={os.getpid()}")
    try:
        observer._refresh_usage(force=True)
        observer.refresh_discovery(force=True)
        atomic_write_json(STATE_PATH, observer.snapshot())
        while not stopping:
            observer.poll()
            atomic_write_json(STATE_PATH, observer.snapshot())
            time.sleep(config.get("poll_interval_seconds", 0.75))
    finally:
        atomic_write_json(STATE_PATH, observer.snapshot(running=False))
        try:
            recorded_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
            if recorded_pid == os.getpid():
                PID_PATH.unlink()
        except (FileNotFoundError, ValueError):
            pass
        append_log(f"multi-watch stopped pid={os.getpid()}")
    return 0


def start_daemon(session_path: Optional[Path], watch_all: bool = False) -> int:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        try:
            saved_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            saved_pid = int(saved_state.get("watcher_pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            saved_pid = 0
        if process_is_running(saved_pid):
            print(f"Watcher is already running. pid={saved_pid}")
            return 0
    if PID_PATH.exists():
        try:
            old_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if process_is_running(old_pid):
            print(f"Watcher is already running. pid={old_pid}")
            return 0

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_worker",
    ]
    if watch_all:
        command.append("--all")
    elif session_path is not None:
        command += ["--session", str(session_path)]
    else:
        raise ValueError("session_path is required unless watch_all is enabled")
    with LOG_PATH.open("a", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if STATE_PATH.exists() and process_is_running(process.pid):
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if state.get("watcher_pid") == process.pid and state.get("running"):
                    print(f"Started real-time monitoring. pid={process.pid}")
                    print("session=all-active" if watch_all else f"session={session_path}")
                    return 0
            except (OSError, json.JSONDecodeError):
                pass
        if process.poll() is not None:
            print(f"Watcher failed to start: exit={process.returncode}", file=sys.stderr)
            return 1
        time.sleep(0.1)
    print("The watcher started but state verification timed out.", file=sys.stderr)
    return 1


def stop_daemon() -> int:
    pid = 0
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            PID_PATH.unlink(missing_ok=True)
    if pid <= 0 and STATE_PATH.exists():
        try:
            saved_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            pid = int(saved_state.get("watcher_pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pid = 0
    if pid <= 0:
        print("No watcher process is running.")
        return 0
    if not process_is_running(pid):
        PID_PATH.unlink(missing_ok=True)
        print("Removed the stale watcher PID file.")
        return 0
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process_is_running(pid):
        time.sleep(0.1)
    print(f"Sent the watcher stop request. pid={pid}")
    return 0


def read_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        raise FileNotFoundError("No state file exists. Run watch --daemon first.")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def print_status(as_json: bool = False) -> int:
    try:
        state = read_state()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    if state.get("multi"):
        tasks = state.get("tasks") or []
        print(f"Watcher: {'running' if state.get('running') else 'stopped'}")
        print(f"Active user tasks: {len(tasks)}")
        usage = state.get("usage") or {}
        if usage.get("available"):
            print(f"Weekly usage remaining: {float(usage.get('remaining_percent', 0)):.0f}%")
        for index, task in enumerate(tasks, start=1):
            decision = task.get("decision") or {}
            telemetry = task.get("telemetry") or {}
            project = Path(task.get("cwd") or "-").name or "-"
            print(f"[{index}] {project} · {task.get('thread_id') or '-'}")
            print(
                f"    {task.get('current_model') or '-'} / {task.get('current_effort') or '-'}"
                f" → {decision.get('model') or '-'} / {decision.get('effort') or '-'}"
            )
            print(f"    context {float(telemetry.get('context_ratio', 0)):.1%} · follow-up: {task.get('planned_next_step') or '-'}")
        print("Subagent and guardian sessions are counted under their root user task.")
        return 0
    decision = state.get("decision") or {}
    telemetry = state.get("telemetry") or {}
    print(f"Watcher: {'running' if state.get('running') else 'stopped'}")
    print(f"Task ID: {state.get('thread_id') or '-'}")
    print(f"Current model: {state.get('current_model') or '-'} / {state.get('current_effort') or '-'}")
    print(
        f"Recommended model: {decision.get('model') or '-'} / {decision.get('effort') or '-'} "
        f"(tier={decision.get('tier') or '-'})"
    )
    print(f"Decision source: {decision.get('source') or '-'}")
    print(f"Next task: {state.get('planned_next_step') or '(not announced yet)'}")
    print(f"Failures: {telemetry.get('failures_this_turn', 0)}")
    print(f"Context usage: {float(telemetry.get('context_ratio', 0)):.1%}")
    if decision.get("reasons"):
        print("Reasons: " + ", ".join(decision["reasons"]))
    if decision.get("recommend_compact"):
        print("Recommendation: consider compacting context before the next model change.")
    print("Mode: observe only — active Desktop turns are never overwritten externally.")
    return 0


def session_looks_active(session_path: Path, seconds: int = 30) -> bool:
    try:
        age = time.time() - session_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < seconds


def submit_prompt(
    thread_id: str,
    prompt: str,
    execute: bool,
    force_active: bool,
) -> int:
    config = load_config()
    session_path = discover_session(thread_id)
    telemetry = Telemetry()
    if STATE_PATH.exists():
        try:
            saved = read_state()
            candidates = saved.get("tasks") or [saved]
            for candidate in candidates:
                if candidate.get("thread_id") == thread_id:
                    telemetry = Telemetry(**(candidate.get("telemetry") or {}))
                    break
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    decision = classify_task(prompt, config, telemetry, source="submitted_prompt")
    command = [
        str(CODEX_BIN),
        "exec",
        "resume",
        thread_id,
        "-m",
        decision.model,
        "-c",
        f'model_reasoning_effort="{decision.effort}"',
        prompt,
    ]
    print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))
    print("Planned command: codex exec resume <thread> -m " + decision.model)
    if not execute:
        print("Dry run. Add --execute to run it.")
        return 0
    if session_looks_active(session_path) and not force_active:
        print(
            "Codex Desktop appears to be updating this task. It was not resumed to avoid a "
            "session conflict. Stop or close the task, then try again.",
            file=sys.stderr,
        )
        return 2
    return subprocess.call(command, cwd=str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex goal-aware automatic model router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    watch = subparsers.add_parser("watch", help="Monitor Codex rollouts in real time")
    watch.add_argument("--thread-id", help="Codex task ID to monitor")
    watch.add_argument("--session", type=Path, help="Rollout JSONL path to monitor")
    watch.add_argument("--all", action="store_true", help="Monitor all active user tasks")
    watch.add_argument("--daemon", action="store_true", help="Run in the background")

    worker = subparsers.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--session", type=Path)
    worker.add_argument("--all", action="store_true")

    subparsers.add_parser("prompt-hook", help=argparse.SUPPRESS)
    routed = subparsers.add_parser("_resume-routed", help=argparse.SUPPRESS)
    routed.add_argument("--request", type=Path, required=True)

    status = subparsers.add_parser("status", help="Show current routing status")
    status.add_argument("--json", action="store_true")

    usage = subparsers.add_parser("usage", help="Refresh and show weekly Codex usage")
    usage.add_argument("--json", action="store_true")

    subparsers.add_parser("stop", help="Stop background monitoring")

    decide = subparsers.add_parser("decide", help="Test a model decision from text")
    decide.add_argument("text")

    submit = subparsers.add_parser("submit", help="Resume a task with the selected model")
    submit.add_argument("--thread-id", required=True)
    submit.add_argument("--prompt", required=True)
    submit.add_argument("--execute", action="store_true")
    submit.add_argument(
        "--force-active",
        action="store_true",
        help="Ignore active Desktop session conflict warnings (not recommended)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    if args.command == "watch":
        session_path = None if args.all else (args.session or discover_session(args.thread_id))
        if args.daemon:
            return start_daemon(session_path, watch_all=args.all)
        if args.all:
            return watch_all_worker(config)
        assert session_path is not None
        return watch_worker(session_path, config)
    if args.command == "_worker":
        if args.all:
            return watch_all_worker(config)
        if args.session is None:
            raise SystemExit("_worker requires --session or --all")
        return watch_worker(args.session, config)
    if args.command == "prompt-hook":
        return run_prompt_submit_hook()
    if args.command == "_resume-routed":
        return resume_routed_request(args.request)
    if args.command == "status":
        return print_status(args.json)
    if args.command == "usage":
        usage = query_weekly_usage()
        if args.json:
            print(json.dumps(asdict(usage), ensure_ascii=False, indent=2))
        elif usage.available:
            print(f"Weekly Codex usage remaining: {usage.remaining_percent:.0f}%")
            if usage.resets_at:
                reset = datetime.fromtimestamp(usage.resets_at).astimezone()
                print(f"Resets: {reset.isoformat(timespec='minutes')}")
        else:
            print(f"Weekly usage unavailable: {usage.error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "stop":
        return stop_daemon()
    if args.command == "decide":
        decision = classify_task(args.text, config)
        print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))
        return 0
    if args.command == "submit":
        return submit_prompt(args.thread_id, args.prompt, args.execute, args.force_active)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
