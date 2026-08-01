import importlib.util
import io
import json
import socket
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "codex_router.py"
SPEC = importlib.util.spec_from_file_location("codex_router", MODULE_PATH)
router = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = router
SPEC.loader.exec_module(router)

HOOKS_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_hooks.py"
HOOKS_SPEC = importlib.util.spec_from_file_location("manage_hooks", HOOKS_MODULE_PATH)
manage_hooks = importlib.util.module_from_spec(HOOKS_SPEC)
sys.modules[HOOKS_SPEC.name] = manage_hooks
HOOKS_SPEC.loader.exec_module(manage_hooks)


class RouterTests(unittest.TestCase):
    def test_goal_context_uses_only_user_objective(self):
        text = '''<codex_internal_context source="goal">
Continue working toward the active thread goal.
<objective>
그래 다 해서 완성도를 높혀줘
</objective>
</codex_internal_context>'''
        self.assertEqual(
            router.normalize_user_prompt(text),
            "그래 다 해서 완성도를 높혀줘",
        )

    @classmethod
    def setUpClass(cls):
        cls.config = router.load_config()

    def test_simple_question_uses_luna_low(self):
        decision = router.classify_task("현재 토큰 상태만 알려줘", self.config)
        self.assertEqual(decision.tier, "luna_low")
        self.assertEqual(decision.model, "gpt-5.6-luna")
        self.assertEqual(decision.effort, "low")

    def test_complex_automation_uses_sol_medium(self):
        decision = router.classify_task(
            "After Effects를 실시간 감시하는 자동 라우터를 구현하고 실행해줘",
            self.config,
        )
        self.assertEqual(decision.tier, "sol_medium")
        self.assertEqual(decision.model, "gpt-5.6-sol")
        self.assertEqual(decision.effort, "medium")

    def test_broad_quality_goal_uses_terra_high(self):
        decision = router.classify_task("그래 다 해서 완성도를 높혀줘", self.config)
        self.assertEqual(decision.tier, "terra_high")
        self.assertEqual(decision.model, "gpt-5.6-terra")

    def test_technical_validation_uses_terra_medium(self):
        decision = router.classify_task(
            "클라이언트 복구 코드가 실제 Firebase 권한과 맞는지 확인하겠습니다.",
            self.config,
        )
        self.assertEqual(decision.tier, "terra_low")
        self.assertEqual(decision.model, "gpt-5.6-terra")
        self.assertEqual(decision.effort, "low")

    def test_game_visual_balance_goal_uses_terra_high(self):
        decision = router.classify_task(
            "다음 단계로 가고 게임의 재미와 비주얼, 밸런스를 디벨롭해줘",
            self.config,
        )
        self.assertEqual(decision.tier, "terra_high")
        self.assertEqual(decision.model, "gpt-5.6-terra")

    def test_bulk_character_dialogue_revision_uses_sol_low(self):
        decision = router.classify_task(
            "40명의 날짜별 만남 대사에서 공용 문장을 찾아 캐릭터별로 보완하겠습니다",
            self.config,
        )
        self.assertEqual(decision.tier, "sol_low")
        self.assertEqual(decision.model, "gpt-5.6-sol")
        self.assertEqual(decision.effort, "low")

    def test_turn_boundary_becomes_auto_apply_eligible(self):
        observer = router.RolloutObserver(Path("/tmp/test.jsonl"), self.config)
        observer.process_line(
            '{"type":"session_meta","payload":{"id":"thread-1","cwd":"/tmp"}}'
        )
        observer.process_line(
            '{"type":"turn_context","payload":{"turn_id":"turn-1",'
            '"model":"gpt-5.6-sol","effort":"high"}}'
        )
        observer.process_line(
            '{"type":"response_item","payload":{"type":"message","role":"user",'
            '"content":[{"type":"input_text","text":"Firebase 권한을 확인해줘"}]}}'
        )
        self.assertTrue(observer.state.turn_active)
        observer.process_line(
            '{"type":"event_msg","payload":{"type":"task_complete",'
            '"turn_id":"turn-1"}}'
        )
        self.assertFalse(observer.state.turn_active)
        self.assertEqual(observer.state.last_turn_status, "completed")
        self.assertTrue(router.auto_apply_eligible(observer.state, self.config))

    def test_active_turn_is_not_auto_applied(self):
        state = router.RouterState(
            current_model="gpt-5.6-terra",
            current_effort="low",
            turn_active=True,
            last_turn_status="in_progress",
            decision=router.Decision(
                tier="complex",
                model="gpt-5.6-sol",
                effort="high",
                score=4,
                reasons=["test"],
                source="test",
                task_preview="test",
            ),
        )
        self.assertFalse(router.auto_apply_eligible(state, self.config))

    def test_scheduler_launches_only_for_active_goal(self):
        state = router.RouterState(
            thread_id="thread-1",
            cwd="/tmp",
            current_model="gpt-5.6-sol",
            current_effort="high",
            last_turn_id="turn-1",
            last_turn_status="completed",
            decision=router.Decision(
                tier="normal",
                model="gpt-5.6-terra",
                effort="medium",
                score=2,
                reasons=["test"],
                source="test",
                task_preview="test",
            ),
        )
        watched = router.WatchedRollout(
            Path("/tmp/test.jsonl"),
            type("Observer", (), {"state": state})(),
            0,
            0,
        )
        strict_config = {**self.config, "require_active_goal": True}
        multi = router.MultiRolloutObserver(strict_config)
        multi.tasks = {"thread-1": watched}
        launched = []
        with patch.object(router, "query_thread_goal", return_value={"status": "active"}):
            with patch.object(
                multi,
                "_launch_auto_apply",
                side_effect=lambda thread_id, _watched, key: launched.append((thread_id, key)),
            ):
                multi._schedule_auto_apply()
        self.assertEqual(len(launched), 1)
        self.assertEqual(state.goal_status, "active")

    def test_security_task_uses_critical_tier(self):
        decision = router.classify_task(
            "운영 보안 취약점을 분석하고 데이터 손실 가능성을 검증해줘",
            self.config,
        )
        self.assertEqual(decision.tier, "sol_max")
        self.assertEqual(decision.effort, "max")

    def test_failures_escalate_normal_work(self):
        telemetry = router.Telemetry(failures_this_turn=3)
        decision = router.classify_task("이 파일을 수정해줘", self.config, telemetry)
        self.assertEqual(decision.tier, "sol_low")

    def test_extracts_announced_next_step(self):
        text = "현재 분석이 끝났습니다. 다음 작업은 렌더링 오류를 디버깅하고 테스트합니다."
        self.assertEqual(
            router.extract_next_step(text),
            "렌더링 오류를 디버깅하고 테스트합니다.",
        )

    def test_extracts_future_step_without_literal_next_task(self):
        text = (
            "현재 대사 정리를 마쳤습니다. "
            "서버가 가진 캐릭터 역할 정보부터 확인해 이어서 보완하겠습니다."
        )
        self.assertEqual(
            router.extract_next_step(text),
            "서버가 가진 캐릭터 역할 정보부터 확인해 이어서 보완하겠습니다",
        )

    def test_extracts_next_step_from_next_direction_phrase(self):
        text = "다음으로는 네트워크 복구 대사를 점검하겠습니다. 이후 테스트합니다."
        self.assertEqual(
            router.extract_next_step(text),
            "네트워크 복구 대사를 점검하겠습니다.",
        )

    def test_extracts_next_step_from_next_is_phrase(self):
        text = "다음은 플레이 지속성의 핵심인 경제 루프를 점검하겠습니다."
        next_step = router.extract_next_step(text)
        self.assertEqual(next_step, "플레이 지속성의 핵심인 경제 루프를 점검하겠습니다.")
        decision = router.classify_task(next_step, self.config)
        self.assertEqual(decision.model, "gpt-5.6-sol")

    def test_prearmed_route_is_verified_on_new_turn_context(self):
        observer = router.RolloutObserver(Path("/tmp/test.jsonl"), self.config)
        observer.state.thread_id = "thread-1"
        observer.state.prearmed_model = "gpt-5.6-terra"
        observer.state.prearmed_effort = "medium"
        observer.state.prearmed_turn_id = "turn-1"
        observer.state.prearm_verification = "pending"
        observer.process_line(
            '{"type":"turn_context","payload":{"turn_id":"turn-2",'
            '"model":"gpt-5.6-terra","effort":"medium"}}'
        )
        self.assertEqual(observer.state.prearm_verification, "applied")
        self.assertEqual(observer.state.auto_apply_status, "prearm_applied")

    def test_cheaper_next_task_downgrades_without_hysteresis(self):
        decision = router.Decision(
            tier="terra_medium",
            model="gpt-5.6-terra",
            effort="medium",
            score=3,
            reasons=["test"],
            source="announced_next_step",
            task_preview="test",
        )
        stable = router.stabilize_model_switch(decision, "gpt-5.6-sol", self.config)
        self.assertEqual(stable.model, "gpt-5.6-terra")

    def test_clear_normal_step_downgrades_from_sol(self):
        decision = router.classify_task("이 파일을 수정하겠습니다", self.config)
        self.assertEqual(decision.score, 2)
        stable = router.stabilize_model_switch(decision, "gpt-5.6-sol", self.config)
        self.assertEqual(stable.model, "gpt-5.6-terra")

    def test_ultra_requires_explicit_parallel_signal(self):
        non_parallel = router.classify_task(
            "운영 보안 취약점과 데이터 손실을 실시간 자동 전환으로 분석하고 구현해줘",
            self.config,
        )
        self.assertEqual(non_parallel.effort, "max")
        parallel = router.classify_task(
            "운영 보안 취약점과 데이터 손실을 다중 에이전트로 병렬 전수 조사하고 "
            "실시간 자동 전환 라우터를 구현해줘",
            self.config,
        )
        self.assertEqual(parallel.tier, "sol_ultra")
        self.assertEqual(parallel.effort, "ultra")

    def test_luna_never_selects_unsupported_ultra(self):
        decision = router.classify_task("짧게 목록만 알려줘", self.config)
        self.assertEqual(decision.model, "gpt-5.6-luna")
        self.assertIn(decision.effort, self.config["model_capabilities"][decision.model])

    def test_prompt_hook_blocks_then_schedules_same_prompt_on_better_route(self):
        hook_input = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "thread-hook-test",
            "turn_id": "turn-hook-test",
            "model": "gpt-5.6-sol",
            "prompt": "현재 상태만 알려줘",
            "cwd": "/tmp",
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(router, "PROMPT_ROUTE_DIR", Path(temporary)):
                with patch.object(router.sys, "stdin", io.StringIO(json.dumps(hook_input))):
                    with patch.object(router, "send_recommendation_notification"):
                        with patch.object(router.subprocess, "Popen") as popen:
                            output = io.StringIO()
                            with redirect_stdout(output):
                                result = router.run_prompt_submit_hook()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["decision"], "block")
        popen.assert_called_once()

    def test_subagent_transcript_is_not_rerouted(self):
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "subagent.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "child",
                            "parent_thread_id": "root",
                            "thread_source": "subagent",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(router.transcript_is_root_user_session(str(transcript)))

    def test_attachment_in_latest_user_event_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            transcript = Path(temporary) / "attached.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "이 이미지를 수정해줘",
                            "local_images": ["/tmp/image.png"],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(router.transcript_turn_has_attachments(str(transcript)))

    def test_hook_installer_preserves_existing_handlers(self):
        with tempfile.TemporaryDirectory() as temporary:
            hooks_path = Path(temporary) / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {"hooks": [{"type": "command", "command": "echo existing"}]}
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(manage_hooks, "HOOK_PATH", hooks_path):
                manage_hooks.install()
            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
        groups = installed["hooks"]["UserPromptSubmit"]
        commands = [handler["command"] for group in groups for handler in group["hooks"]]
        self.assertIn("echo existing", commands)
        self.assertTrue(any(manage_hooks.ROUTER_MARKER in command for command in commands))

    def test_ipc_frame_is_little_endian_length_prefixed_json(self):
        message = {"type": "request", "method": "initialize", "params": {"한글": True}}
        frame = router._ipc_frame(message)
        self.assertEqual(struct.unpack("<I", frame[:4])[0], len(frame) - 4)
        self.assertEqual(json.loads(frame[4:].decode("utf-8")), message)

    def test_announced_next_step_is_prearmed_once(self):
        state = router.RouterState(
            thread_id="thread-1",
            current_model="gpt-5.6-terra",
            current_effort="medium",
            last_turn_id="turn-1",
            last_turn_status="in_progress",
            turn_active=True,
            planned_next_step="복잡한 자동 전환을 구현하겠습니다.",
            decision=router.Decision(
                tier="complex",
                model="gpt-5.6-sol",
                effort="high",
                score=4,
                reasons=["test"],
                source="announced_next_step",
                task_preview="test",
            ),
        )
        watched = router.WatchedRollout(
            Path("/tmp/test.jsonl"),
            type("Observer", (), {"state": state})(),
            0,
            0,
        )
        multi = router.MultiRolloutObserver(self.config)
        multi.tasks = {"thread-1": watched}
        with patch.object(router, "prearm_next_turn", return_value=True) as prearm:
            multi._schedule_prearm()
            multi._schedule_prearm()
        prearm.assert_called_once_with(
            "thread-1",
            "gpt-5.6-sol",
            "high",
            timeout=float(self.config["desktop_ipc_timeout_seconds"]),
        )
        self.assertEqual(state.auto_apply_status, "next_turn_prearmed")
        self.assertEqual(state.prearmed_model, "gpt-5.6-sol")

    def test_quoted_next_task_is_not_a_plan(self):
        text = "'다음 작업'이 선언된 직후의 턴 경계에서 모델을 바꿉니다."
        self.assertEqual(router.extract_next_step(text), "")

    def test_explanatory_sentence_after_now_is_not_a_plan(self):
        text = (
            "이제 단순히 화면에서 숨기는 수준이 아니라, 저장 복구가 들어와도 "
            "실제로 만난 적 없는 인물에게 관계 수치가 생기지 않습니다."
        )
        self.assertEqual(router.extract_next_step(text), "")

    def test_successful_mcp_result_is_not_failure(self):
        self.assertFalse(router.matches_any(router.FAILURE_PATTERNS, '{"isError": false}'))

    def test_failed_mcp_result_is_failure(self):
        self.assertTrue(router.matches_any(router.FAILURE_PATTERNS, '{"isError": true}'))

    def test_manual_notification_contains_recommended_model_and_korean_effort(self):
        with patch.object(router, "OSASCRIPT_BIN", Path("/usr/bin/true")):
            with patch.object(router.subprocess, "Popen") as popen:
                self.assertTrue(
                    router.send_recommendation_notification(
                        "gpt-5.6-sol",
                        "high",
                        "자동 모델 변경이 실패했습니다.",
                    )
                )
        command = popen.call_args.args[0]
        rendered_command = "\n".join(command)
        self.assertIn("gpt-5.6-sol · 사고 강도 높음을 사용하세요.", rendered_command)
        self.assertIn("코덱스 모델 추천", rendered_command)

    def test_goal_blocked_sends_fallback_notification_once(self):
        state = router.RouterState(
            thread_id="thread-1",
            current_model="gpt-5.6-terra",
            current_effort="medium",
            last_turn_id="turn-1",
            last_turn_status="completed",
            decision=router.Decision(
                tier="complex",
                model="gpt-5.6-sol",
                effort="high",
                score=4,
                reasons=["test"],
                source="test",
                task_preview="test",
            ),
        )
        watched = router.WatchedRollout(
            Path("/tmp/test.jsonl"),
            type("Observer", (), {"state": state})(),
            0,
            0,
        )
        multi = router.MultiRolloutObserver({**self.config, "require_active_goal": True})
        multi.tasks = {"thread-1": watched}
        with patch.object(router, "query_thread_goal", return_value={"status": "blocked"}):
            with patch.object(router, "send_recommendation_notification", return_value=True) as notify:
                multi._schedule_auto_apply()
                multi._schedule_auto_apply()
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(state.auto_apply_status, "goal_blocked")

    def test_completed_non_goal_is_not_restarted_without_follow_up(self):
        state = router.RouterState(
            thread_id="thread-1",
            current_model="gpt-5.6-sol",
            current_effort="high",
            last_turn_id="turn-1",
            last_turn_status="completed",
            decision=router.Decision(
                tier="luna_low",
                model="gpt-5.6-luna",
                effort="low",
                score=-1,
                reasons=["test"],
                source="user_prompt",
                task_preview="상태를 알려줘",
            ),
        )
        watched = router.WatchedRollout(
            Path("/tmp/test.jsonl"),
            type("Observer", (), {"state": state})(),
            0,
            0,
        )
        multi = router.MultiRolloutObserver(self.config)
        multi.tasks = {"thread-1": watched}
        with patch.object(router, "query_thread_goal", return_value={}):
            with patch.object(multi, "_launch_auto_apply") as launch:
                multi._schedule_auto_apply()
        launch.assert_not_called()
        self.assertEqual(state.auto_apply_status, "non_goal_complete")


if __name__ == "__main__":
    unittest.main()
