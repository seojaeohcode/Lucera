# Lucera

영암군 태양광 민원을 입력하면 주소를 좌표로 확정하고, 관련 근거와 규칙을 묶어 설명한 뒤 같은 데이터로 계속 대화하는 사전점검 데모입니다.

## 완성된 사용자 흐름

1. **민원 접수** — 영암군 주소와 민원 원문을 입력합니다. 좌표를 직접 주거나 주소 검색으로 지오코딩합니다.
2. **AI 분석·저장** — `complaint_submission`에 원문·주소·좌표·쟁점을 저장하고, 검색 근거를 `complaint_evidence`로 연결합니다.
3. **영암 전용 지도** — `/v1/map/pins`가 공식 허가 원장에서 고른 실제 대표 핀과 사용자가 방금 접수한 민원을 표시합니다. 화면의 지도는 영암군 범위만 노출합니다.
4. **다음** — 저장된 주소·좌표를 유지한 채 대화 단계로 진입합니다.
5. **연속·멀티모달 대화** — `chat_conversation`과 `chat_message`에 문맥을 누적하며, 추가 질문과 PNG/JPEG/WEBP/GIF 현장 이미지를 함께 받을 수 있습니다. Claude 키가 없으면 동일한 근거팩을 결정론적 답변기로 처리합니다.
6. **Solverton F 기능 연결** — F1 영암 조례 기준, F3 리별 누적 허가, F4 읍면 계통 신호를 영암 전용 API와 지도 카드로 제공합니다.

화면에서 1·2·3단계를 항상 확인할 수 있고, 잠긴 단계는 앞 단계가 끝나면 열립니다. 공식 허가 원장 전체는 DB에 보존하되, 지도는 리별 실제 대표 사례를 5~7건 정도 표시해 발표 화면의 밀도를 유지합니다. 회의록은 영암군 실제 문서 12건과 136개 문단을 사용합니다.

## 빠른 실행

Windows PowerShell에서 프로젝트 루트에서 실행합니다.

```powershell
$py = 'C:\Python313\python.exe'
& $py scripts\rebuild_yeongam_real_db.py --db data\db\lucera_minutes.sqlite3 --replace --map-sample-target 180 --geocode-workers 12
& $py -m lucera.cli serve --host 127.0.0.1 --port 8000
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다. UI의 **예시 다시 채우기**와 **민원 분석하고 좌표 저장**으로 첫 흐름을 재현할 수 있습니다.

테스트 fixture가 필요한 경우에만 `scripts\rebuild_demo_db.py`를 별도로 사용합니다. 발표용 실행은 위의 실데이터 DB 재구축 명령을 기준으로 합니다.

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

## Solverton F 기능 API

`GET /v1/features/f1`은 법제처 조례 스냅샷에서 영암군의 도로·주거 이격거리와 조문을 반환합니다. `GET /v1/features/f3`은 영암군 리별 누적 허가 건수·면적과 지도 좌표 표본을 반환하고, `GET /v1/features/f4`는 변전소명을 노출하지 않고 읍면별 공개 허가 건수 기준의 계통 부담 신호(`여유`·`혼잡`·`포화`)를 반환합니다. 이 신호는 실제 접속 가능 용량을 판정하지 않으며, 최종 접속 여부는 한전 확인이 필요합니다. 원본 파일은 `data/reference/solverton/`에 provenance와 함께 보관하며, 다른 시군 데이터는 API 응답에 노출하지 않습니다.

## HTTP API

### 민원과 지도

`POST /v1/complaints`

```json
{
  "address": "전라남도 영암군 도포면 영호리 572-5",
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

실제 화면용 DB는 [scripts/rebuild_yeongam_real_db.py](scripts/rebuild_yeongam_real_db.py)가 새 SQLite 파일에 다음을 적재합니다.

- 공공데이터포털 영암군 태양광 허가정보 원장 1,549건
- 원장 지번 주소에서 보정한 지도용 실제 좌표 표본(리별 최소 1건, 허가 건수 비례로 총 180건)
- 영암군 실제 회의록 12건·136개 문단·쟁점·처리 과정
- 허가 사례와 회의록 문단의 읍·면·리·태양광 맥락 연결

지도 표본은 합성 데이터가 아니다. 원장 전체를 DB에 보존하고, 실제 주소를 지오코딩한 리별 대표 사례만 지도에 표시한다. 좌표에는 지오코더, 매칭 점수, 원 주소, 처리 상태를 함께 저장한다.

```powershell
& $py scripts\rebuild_yeongam_real_db.py --db data\db\lucera_minutes.sqlite3 --replace --map-sample-target 180 --geocode-workers 12
```

`--replace`는 기존 파일과 `-wal`, `-shm`을 `data/db/backups/`에 남긴 뒤 새 파일을 원자적으로 교체합니다. 서버도 이 방식으로 재생성하므로 라이브 WAL을 압축파일로 덮어쓰지 않습니다. 기본값은 리별 최소 1건과 허가 건수 비례 배분으로 지도 표본 180건을 지오코딩하며, 이전 방식이 필요하면 `--map-sample-per-ri`를 지정합니다.

## 구조

```text
lucera/
  complaints.py   # 민원 저장, 대화, 영암 핀
  real_data.py    # 영암 공식 허가 원장·회의록 재구축
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

배포는 코드·스키마·실제 원장·재생성 스크립트를 업로드하고, 서버에서 서비스를 멈춘 뒤 `rebuild_yeongam_real_db.py --map-sample-target 180`으로 실제 영암 DB를 재생성하고 `systemctl restart lucera` 후 `/health`를 확인합니다. 기존 DB는 서버의 `/opt/lucera/data/db/backups/`에 보존됩니다.
