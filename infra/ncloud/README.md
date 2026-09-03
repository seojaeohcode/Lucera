# NCloud Terraform deployment

이 디렉터리는 Lucera 회의록 검색 서비스를 NCloud VPC Compute 1대에 배포하기 위한 Terraform 루트입니다.

인증키는 Terraform 파일이나 tfvars에 넣지 않습니다. `scripts/terraform_ncloud.ps1`가 로컬 `.env.ncloud`를 프로세스 환경변수로만 주입하고 Terraform을 실행합니다.

## 실행

PowerShell에서 저장소 루트 기준으로 실행합니다.

```powershell
./scripts/terraform_ncloud.ps1 init
./scripts/terraform_ncloud.ps1 validate
./scripts/terraform_ncloud.ps1 plan
./scripts/terraform_ncloud.ps1 apply
```

기본 구성은 VPC, 공개 서브넷, Terraform이 생성한 로그인 키, Ubuntu 22.04 KVM Compute 서버, Public IP를 만듭니다. 서버 사양·가용영역·이미지는 `terraform.tfvars`에서 계정에 맞게 조정할 수 있습니다. 예시는 `terraform.tfvars.example`에 있습니다.

## 기존 리소스 정리 정책

Terraform은 state에 관리 대상으로 등록된 리소스만 삭제합니다. 따라서 기존 계정의 모든 리소스를 무조건 `destroy`하는 것은 안전하지 않으며, 먼저 계정 리소스를 Terraform으로 조회하고 명시적으로 import한 뒤 삭제해야 합니다. 이 루트에서 새로 만든 리소스는 `apply` 후 `destroy`로 함께 제거할 수 있습니다.

운영 DB와 원문 데이터는 서버와 별도로 보존해야 합니다. 배포 파일은 `deploy/`를 사용하고, 데이터베이스는 `data/db/lucera_minutes.sqlite3`를 서버의 `/opt/lucera/data/db/`에 배치합니다.

Terraform apply가 완료된 후 저장소 루트에서 `./scripts/deploy_ncloud.ps1`를 실행하면 Terraform state의 Public IP와 민감 출력인 로그인키를 일시적으로 사용해 원문 DB·애플리케이션·systemd·nginx를 Compute 서버에 설치하고 `/health`를 확인합니다. 로그인키 파일은 작업 후 삭제됩니다.

## 기존 리소스 정리

`inventory/`는 계정에 있는 Terraform Provider 조회 결과를 state에 저장하고, `cleanup/`은 그 결과 중 삭제 대상으로 확정한 리소스만 import하는 별도 루트입니다. cleanup state에 import된 리소스만 destroy하므로, 신규 배포 state와 섞이지 않습니다.

```powershell
./scripts/terraform_ncloud_inventory.ps1 apply -auto-approve
./scripts/terraform_ncloud_cleanup.ps1 init
./scripts/terraform_ncloud_cleanup.ps1 import ncloud_vpc.existing 145704
./scripts/terraform_ncloud_cleanup.ps1 import ncloud_subnet.existing_public 317672
./scripts/terraform_ncloud_cleanup.ps1 import ncloud_access_control_group.existing_custom 384242
./scripts/terraform_ncloud_cleanup.ps1 import ncloud_login_key.existing life-rpg-demo-login
./scripts/terraform_ncloud_cleanup.ps1 import ncloud_public_ip.existing_unassociated 144407359
./scripts/terraform_ncloud_cleanup.ps1 plan -destroy
./scripts/terraform_ncloud_cleanup.ps1 destroy -auto-approve
```

삭제 대상 ID는 실제 inventory 결과와 대조한 뒤 실행합니다. VPC를 삭제하면 그 VPC의 기본 ACL·라우팅 테이블·기본 ACG도 함께 정리됩니다.
