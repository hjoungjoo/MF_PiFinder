# MF_PiFinder — 대형 카탈로그 지연/선택 로드 설계

작성일: 2026-07-20 · 상태: **설계안 (구현 전)**

## 1. 배경과 목표

메인 UI 프로세스의 실측 메모리(PSS)는 548MB이고, 그 중 **~350-400MB가 카탈로그
인메모리 로드**로 추정된다. 전체 149,329개 천체 중 **WDS가 131,303개(88%)**로
지배적이다. 최종 제품이 2GB RAM이므로:

- **목표**: WDS(및 대형 카탈로그)를 쓰지 않는 세션에서 메인 UI를 ~200-250MB로.
- **제약**: `catalogs.py`는 upstream 파일 — 변경 최소화. 기존 UX(LCD 카탈로그
  탐색, push-to, 검색)는 유지하되, 미로드 카탈로그는 "진입하면 그때 로드"로.

## 2. 현재 구조 (실조사 결과)

| 단계 | 동작 | 근거 |
|---|---|---|
| 첫 부팅 (캐시 없음) | M/NGC/IC(~13K)만 동기 로드 → 나머지(WDS 포함 ~136K)는 `CatalogBackgroundLoader` 스레드가 100개/50ms 배치로 로드 → 완료 시 전체를 pickle 캐시로 저장 | catalogs.py:1011 (`priority_catalogs = {"NGC","IC","M"}`), 703-, 855- |
| 재부팅 (캐시 있음) | **45MB pickle 하나를 통째로 동기 로드** → 전부 상주 | catalog_cache.py (`composite_objects.pkl`, 실측 45MB), catalogs.py:866- |
| UI "로딩 중" 처리 | 빈(미로드) 카탈로그는 필터를 건너뛰는 분기가 **이미 존재** | catalogs.py:310-316 |
| `filter.selected_catalogs` | UI 표시 필터일 뿐 — 메모리와 무관 (전부 로드됨) | catalogs.py:129, 298 |

즉 "지연 로드 골격 + 로딩중 UI 인지 + 캐시"가 모두 있으므로, 부족한 것은
**(a) 캐시가 카탈로그별로 분리되어 있지 않다**, **(b) 지연 대상을 영구히
로드하지 않고 버틸 방법이 없다** 두 가지다.

## 3. 대안 비교

| 안 | 내용 | 절감 | 변경량 | 판정 |
|---|---|---|---|---|
| **A. 선택+지연 로드 (권장)** | 설정으로 지정한 대형 카탈로그는 부팅 시 로드 제외. LCD에서 그 카탈로그에 **진입하는 순간** 카탈로그별 캐시(pkl)를 로드 | WDS 제외 시 ~300-350MB | 중간 (기존 골격 재사용) | ✅ 채택 |
| B. 온디맨드 DB 조회 (비상주) | 웹 카탈로그처럼 SQLite를 페이지 단위 직조회, 메모리 상주 없음 | 최대 | UI 필터/정렬/nearby 파이프라인이 "전체 리스트 상주" 전제라 **대규모 리팩터** | 장기 옵션 |
| C. CompositeObject 슬림화 (`__slots__`, name interning) | 객체당 오버헤드 축소 | 20-30% | upstream 데이터클래스 변경, 파급 큼 | 보조 수단, 보류 |

## 4. 설계 (A안)

### 4.1 설정

```json
"catalogs.deferred_load": ["WDS"]        // 기본값. 2GB 프로파일 후보: ["WDS","SaM","SaR"]
```

- 빈 리스트 = 현재 동작과 동일(전부 로드). upstream 동작이 기본으로 보존되도록
  **default_config.json에는 빈 리스트**, MF 제품 config에서 `["WDS"]`.

### 4.2 카탈로그별 캐시 분할 (`catalog_cache.py`)

- `composite_objects.pkl`(45MB 단일) → `catalog_<CODE>.pkl` 분할 저장.
  `CACHE_VERSION` bump로 기존 캐시 자동 무효화.
- 부팅 로드: deferred 목록에 없는 카탈로그의 pkl만 로드.
- 진입 시 로드: 해당 카탈로그 pkl 하나만 로드 — **pickle 로드는 수 초**
  (background loader 재빌드는 131K 기준 65초+라 캐시 필수).
  캐시가 없는 첫 부팅만 background loader가 만들고 저장.

### 4.3 로드 흐름

```
부팅:
  캐시 있음 → non-deferred pkl들 로드 (WDS 제외 시 ~18K 객체)
  캐시 없음 → 기존 우선순위/백그라운드 로더 그대로 전체 빌드
              → 완료 시 카탈로그별 pkl 저장 (deferred 대상은 저장 후 메모리에서 해제)

LCD에서 deferred 카탈로그 진입:
  Catalog.get_objects()가 빈 상태 감지 → 로드 요청 (기존 310행 "빈 카탈로그"
  분기가 이미 이 상태를 안전하게 처리)
  → CatalogBackgroundLoader 또는 pkl 로더가 백그라운드 스레드에서 채움
  → 기존 "로딩 중" UI 상태 재사용, 완료 시 목록 갱신
  → 세션 동안 상주 유지 (언로드는 하지 않음 — 복잡도 대비 실익 없음)
```

### 4.4 터치 포인트 (원소스 최소 변경 관점)

| 파일 | 변경 | 성격 |
|---|---|---|
| `catalog_cache.py` | save/load를 카탈로그별 분할로 확장 | upstream 파일, 함수 시그니처 유지 |
| `catalogs.py` | build()에서 deferred 목록 분기 + Catalog에 `ensure_loaded()` 추가 | upstream 파일, 기존 background 골격 재사용으로 삽입 위주 |
| UI 카탈로그 진입 지점 (object_list 등) | `ensure_loaded()` 호출 1곳 | 삽입 |
| `default_config.json` / menu_structure | 설정 항목 (+ 선택: 설정 메뉴 노출) | 삽입 |

### 4.5 영향/위험 분석

- **전역 검색·nearby**: 미로드 카탈로그의 천체는 결과에서 빠진다 — 명시적
  트레이드오프. (WDS 이중성이 nearby에 안 뜨는 것은 2GB 절약의 대가로 수용;
  로드 후에는 정상 포함)
- **push-to recent / observed 체크**: observed는 (catalog, sequence) 키 기반
  DB 조회라 무영향. 웹 카탈로그는 SQLite 직조회라 **완전 무영향**.
- **캐시 정합성**: observed 상태는 로드 시점에 obs_db로 재주입(기존 로직 그대로).
- **첫 부팅 시간**: 변화 없음(전체 빌드는 동일, 저장만 분할).
- **재부팅 시간**: 오히려 개선 (45MB → ~8MB 로드).

### 4.6 예상 효과 (실측 기반 추정)

| 구성 | 메인 UI PSS |
|---|---|
| 현재 (전체 로드) | 548MB |
| WDS 지연 (기본 제안) | **~220-250MB** |
| WDS+SaM+SaR 지연 (2GB 공격적 프로파일) | ~200MB |

2GB 제품 수지: PiFinder 전체 ~650MB + INDI 50MB + OS 200MB ≈ 0.9GB → 여유 1.1GB.

## 5. 구현 단계

| 단계 | 내용 | 검증 |
|---|---|---|
| P1 | 캐시 분할 (동작 변화 없음, 버전 bump) | 재부팅 시간·객체 수 동일 확인 |
| P2 | `catalogs.deferred_load` 설정 + 부팅 제외 + 메모리 해제 | PSS 실측 (목표 250MB 이하) |
| P3 | 진입 시 로드 (`ensure_loaded` + 로딩중 UI 재사용) | LCD에서 WDS 진입 → 수 초 내 목록 표시 |
| P4 | (선택) 설정 메뉴 노출 + 2GB 프로파일 기본값 | 2GB 실기기 검증 |

## 6. 미결 질문 (구현 전 확인)

1. 진입 시 로드의 UX 허용치 — pkl 로드 수 초를 "Loading…" 화면으로 수용?
2. deferred 기본값에 WDS만 넣을지, SaM(2,162)·SaR(333)은 작아서 실익 없음 →
   **WDS 단독 권장**.
3. Hipparcos(`hip_main.pkl` 8.2MB, 차트용 별)는 별도 경로 — 이번 범위 제외.
