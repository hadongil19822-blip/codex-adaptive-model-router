# Codex Adaptive Model Router

Codex Desktop에서 요청마다 **Luna·Terra·Sol과 사고 강도를 자동 선택**하는 로컬 오픈소스 라우터입니다. 분류 자체에는 모델을 호출하지 않아 토큰을 사용하지 않으며, macOS 메뉴 막대 또는 Windows 시스템 트레이에서 여러 작업을 함께 확인할 수 있습니다. UI 기본 언어는 영어입니다.

[English README](README.md)

## 주요 기능

- 목표 여부와 관계없이 모든 일반 사용자 요청을 제출 직전에 분석
- 12개 비용 구간으로 Luna → Terra → Sol 선택
- 단순 조회는 저비용 모델, 복합·고위험 작업만 강한 모델 사용
- 다음 작업이 예고되면 다음 턴 설정을 미리 예약
- 여러 Codex 작업을 작업 ID별로 독립 감시
- Codex에 표시되는 작업 제목을 사용해 작업 폴더가 바뀌어도 알아보기 쉬운 이름 유지
- 주간 Codex 사용량의 남은 퍼센트를 토큰 소모 없이 표시
- 1~100% 막대와 −/+ 버튼으로 설정한 잔여 퍼센트에서 새 작업을 막는 안전 보호 기능
- 첨부파일, 보조 에이전트, 사용자가 직접 지정한 모델은 보호
- 네이티브 macOS 메뉴 막대 상태 앱 제공
- Windows PowerShell/WinForms 트레이 앱과 바탕화면 바로가기 제공
- 모든 기준을 `router_config.json`에서 수정 가능

## 빠른 설치

```bash
git clone https://github.com/hadongil19822-blip/codex-adaptive-model-router.git
cd codex-adaptive-model-router
chmod +x install.sh macos-widget/build-widget.sh codex-auto
./install.sh
```

설치 후 Codex CLI에서 `/hooks`를 실행하고 `UserPromptSubmit` 훅을 한 번 신뢰해야 합니다.

메뉴 막대 앱은 `~/Applications/Codex Auto Router.app`에 설치됩니다.

Windows에서는 PowerShell에서 다음을 실행합니다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

설치가 끝나면 바탕화면에 `Codex Auto Router.lnk`가 생성되고 트레이 앱이 실행됩니다.

## 기본 분류

| 작업 | 모델 계열 | 사고 강도 |
| --- | --- | --- |
| 짧은 조회·설명·서식 | Luna | low–high |
| 일반 수정·구현·검증 | Terra | low–high |
| 복합 자동화·실패 누적·고위험 작업 | Sol | low–max |
| 명시적인 병렬·다중 에이전트·전수 작업 | Sol | ultra |

Ultra는 토큰 사용량이 커질 수 있어 최고 점수와 병렬 신호가 함께 있을 때만 선택됩니다.

## 설정 변경

실제 설정 파일은 `~/.codex/auto-router/router_config.json`입니다. 모델 이름, 점수 구간, 알림, 재시도 시간과 자동 재제출 여부를 자유롭게 변경할 수 있습니다. 업데이트 설치 시 기존 설정은 덮어쓰지 않습니다.

주간 사용량 보호 기능은 기본적으로 꺼져 있습니다. 켜고 임계값을 지정하면 실행 중인 턴은 안전하게 마치고, 새 요청과 자동 후속 작업을 일시 중지합니다. 사용량은 30초마다 확인하며 로컬 Codex 계정 상태만 읽으므로 모델 토큰을 사용하지 않습니다.

자세한 변경 방법은 [커스터마이징 가이드](docs/CUSTOMIZATION.md), 내부 구조는 [아키텍처 문서](docs/ARCHITECTURE.md)를 참고하세요.

## 주의사항

- macOS 13 이상과 Windows 10/11을 지원합니다.
- Codex 버전에 따라 모델 이름과 지원 사고 강도가 달라질 수 있습니다.
- 다음 턴 사전 예약은 공식 app-server 인터페이스를 우선 사용하며 macOS에서는 내부 IPC를 호환성 대안으로 사용합니다.
- OpenAI 공식 제품이 아닌 커뮤니티 프로젝트입니다.

도움이 됐다면 GitHub Star로 다른 Codex 사용자에게도 알려주세요. ⭐

라이선스는 [MIT](LICENSE)입니다.
