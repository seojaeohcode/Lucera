# Lucera

영암군 태양광 민원을 입력하면 주소를 좌표로 확정하고, 관련 근거와 규칙을 묶어 설명한 뒤 같은 데이터로 계속 대화하는 사전점검 데모입니다.

## 완성된 사용자 흐름

1. **민원 접수** — 영암군 주소와 민원 원문을 입력합니다. 좌표를 직접 주거나 주소 검색으로 지오코딩합니다.
2. **AI 분석·저장** — `complaint_submission`에 원문·주소·좌표·쟁점을 저장하고, 검색 근거를 `complaint_evidence`로 연결합니다.
3. **영암 전용 지도** — `/v1/map/pins`가 합성 참고 사업과 사용자가 방금 접수한 민원을 핀으로 표시합니다. 화면의 지도는 영암군 범위만 노출합니다.
4. **다음** — 저장된 주소·좌표를 유지한 채 대화 단계로 진입합니다.
5. **연속·멀티모달 대화** — `chat_conversation`과 `chat_message`에 문맥을 누적하며, 추가 질문과 PNG/JPEG/WEBP/GIF 현장 이미지를 함께 받을 수 있습니다. Claude 키가 없으면 동일한 근거팩을 결정론적 답변기로 처리합니다.

화면에서 1·2·3단계를 항상 확인할 수 있고, 잠긴 단계는 앞 단계가 끝나면 열립니다. 합성 데이터는 실제 민원이나 허가 사실이 아닙니다.

## 빠른 실행

Windows PowerShell에서 프로젝트 루트에서 실행합니다.

```powershell
$py = 'C:\Python313\python.exe'
& $py scripts\rebuild_demo_db.py --db data\db\lucera_minutes.sqlite3 --replace
& $py -m lucera.cli serve --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. UI의 **예시 다시 채우기**와 **민원 분석하고 좌표 저장**으로 첫 흐름을 재현할 수 있습니다.

샘플 CLI 입력은 [chat-input.synthetic.json](chat-input.synthetic.json)입니다.
발표 시나리오 3종은 [demo-scenarios.synthetic.json](demo-scenarios.synthetic.json)에서 확인할 수 있습니다.

```powershell
& $py -m lucera.cli chat --json-file .\chat-input.synthetic.json
```

## 환경변수

`config.py`에는 비밀키를 넣지 않습니다. 로컬은 `.env`, NCloud는 `/etc/lucera/lucera.env`로 주입합니다. 배포 파일을 만들 때는 [deploy/lucera.env.example](deploy/lucera.env.example)을 복사하세요.

```text
LUCERA_ANSWER_MODE=local
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-5
VWORLD_API_KEY=
VWORLD_DOMAIN=http://YOUR_SERVER_IP
ROAD_ADDRESS_API_KEY=
CLIK_API_KEY=
```

`LUCERA_ANSWER_MODE=local`은 외부 AI 호출 없이 근거 기반 템플릿으로 실행합니다. `ANTHROPIC_API_KEY`가 있으면 Claude가 최종 문장을 작성하지만, 규칙 계산·거리·인용 검증은 로컬에서 먼저 수행됩니다. VWorld 키가 있으면 선택한 지도 영상이 AI 이미지 입력으로 추가됩니다.

## HTTP API

### 민원과 지도

`POST /v1/complaints`

```json
{
  "address": "전라남도 영암군 삼호읍 가상리 45-2",
  "text": "집중호우 때 배수로와 토사 유출이 걱정됩니다.",
  "latitude": 34.8,
  "longitude": 126.42,
  "resolve_address": false,
  "include_map_context": false
}
```

응답에는 `complaint_id`, `conversation_id`, `complaint`, `analysis`, `evidence_links`가 포함됩니다. 좌표가 없고 주소 검색을 끄면 지도에 저장하지 않고 오류를 반환합니다. 영암군 이외의 주소는 거부합니다.

`GET /v1/map/pins`는 영암군 전체 핀과 읍·면별 요약을 반환합니다. `GET /v1/map/areas/{eup_myeon}`은 선택한 지역의 핀·용량·쟁점·상태 상세를 반환합니다. `GET /v1/conversations/{conversation_id}`는 대화 전체를 반환합니다.

### 연속 대화

`POST /v1/conversations/{conversation_id}/messages`

```json
{
  "message": "현장 점검과 주민 설명회에서 다음으로 준비할 자료는 무엇인가요?",
  "image": {
    "media_type": "image/png",
    "data": "base64..."
  }
}
```

`image`는 선택 입력이며 서버 DB에는 이미지 원문을 저장하지 않고 첨부 여부만 기록합니다. AI 외부 전송 여부는 Claude 키를 설정한 경우에만 발생합니다.

기존 저수준 검색·일회성 분석 API도 유지합니다.

- `POST /v1/meeting-evidence/search` — `scope: "yeongam"`을 사용하면 영암군 근거만 검색합니다.
- `POST /v1/chat` — 주소 기반 일회성 RAG 분석입니다. 새 화면은 대화 API를 사용합니다.
- `GET /health` — 서비스 및 테이블별 건수를 확인합니다.

## 데이터 재구축 원칙

기존에 섞여 있던 더미·대용량 운영 DB를 배포하지 않습니다. [scripts/rebuild_demo_db.py](scripts/rebuild_demo_db.py)가 새 SQLite 파일을 만들고 다음만 적재합니다.

- 영암군 합성 회의록 6개와 연결된 문단·쟁점·처리 과정
- 영암군 합성 이격거리 규칙 2개
- 읍·면 10곳에 분산된 영암군 합성 참고 사업 16개
- 빈 민원·대화 테이블

`--replace`는 기존 파일과 `-wal`, `-shm`을 `data/db/backups/`에 남긴 뒤 새 파일을 원자적으로 교체합니다. 서버도 이 방식으로 재생성하므로 라이브 WAL을 압축파일로 덮어쓰지 않습니다.

## 구조

```text
lucera/
  complaints.py   # 민원 저장, 대화, 영암 핀
  rag.py           # 검색·규칙·근거팩·답변 연결
  search.py        # 위치/근거 검색
  answer.py        # Claude + 결정론적 fallback + 가드
  synthetic.py     # 영암 합성 fixture
  server.py        # HTTP API와 정적 화면
web/index.html     # 3단계 영암 사용자 흐름
db/schema.sql      # SQLite 스키마
scripts/rebuild_demo_db.py
scripts/deploy_ncloud.ps1
deploy/            # systemd, nginx, 환경변수 예시
tests/             # 회귀·플로우 테스트
```

## 테스트

```powershell
$env:LUCERA_ANSWER_MODE = 'local'
& $py -m pytest -q tests\test_rag.py tests\test_lucera.py tests\test_flow.py
```

## NCloud 배포

Terraform 인프라는 `infra/ncloud/`에 있고, 배포 스크립트는 Terraform output의 공인 IP·root 비밀번호를 사용합니다.

```powershell
& .\scripts\deploy_ncloud.ps1
```

배포는 코드·스키마·재생성 스크립트만 업로드하고, 서버에서 서비스를 멈춘 뒤 영암 합성 DB를 새로 생성하고 `systemctl restart lucera` 후 `/health`를 확인합니다. 기존 DB는 서버의 `/opt/lucera/data/db/backups/`에 보존됩니다.
