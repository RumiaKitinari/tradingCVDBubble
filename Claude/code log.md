## 2026-07-08

### 차트 렌더링 성능 최적화 (WebGL 도입 및 메모리 누수 픽스)
- **WebGL(`Scattergl`) 기반 렌더링 도입**: `cvd/visualizer.py`의 라인 차트 및 버블 오버레이를 기존 `go.Scatter`(SVG)에서 `go.Scattergl`(WebGL)로 교체하여 수만 개의 점을 렌더링할 때 발생하는 심각한 줌/팬(Zoom/Pan) 버벅거림을 극적으로 개선.
- **WebGL Context 초과 문제 분석 및 방어**: 과거 WebGL 도입 실패 원인이 "전체 타임프레임 그래프를 동시에 렌더링하여 브라우저 WebGL Context 한도(16개)를 초과"했기 때문임을 규명. Dash 앱(`app.py`) 구조상 유저가 선택한 단일 `active_timeframe`만 브라우저로 전송하도록 분기되어 있어, 렌더링 개수가 한도를 초과하지 않음을 확인하고 안전하게 도입 완료.
- **차트 스크롤 디바운싱(Debouncing) 버그 수정**: `app.py`의 `refit_y` 클라이언트 콜백에서 `setTimeout` 타이머를 취소(`clearTimeout`)하지 않아 스크롤 시 백그라운드에서 수십 개의 렌더링 이벤트가 겹쳐서 폭탄처럼 터지던 메모리 누수 로직을 디바운스 처리로 정상화.
- 롤백 대비 백업 파일(`app_backup.py`, `visualizer_backup.py`) 생성 완료.

## 2026-07-09

### 대시보드 파이 차트 동기화 및 렌더링 로직 개편
- **파이 차트 캔들 좌표 동기화 (Panning Sync)**: 줌/팬(Zoom/Pan) 조작 시 파이 차트가 캔들과 따로 놀던 현상을 수정. 단순 배열 등분(`np.array_split`) 방식을 버리고 캔들의 `x_idx`를 기반으로 x축 좌표를 역산해 정확한 위치에 파이 차트를 매핑하는 청킹(Chunking) 로직으로 교체.
- **파이 차트 크기 비선형 스케일링 (Square Root Scale)**: 장 초반/종반의 거대 거래량(Volume Spike) 때문에 평상시 캔들의 파이 차트가 점처럼 작아져 보이지 않던 문제를 해결. 전체 볼륨 대비 `sqrt(buy+sell) / sqrt(max_vol)` 비례식을 적용하여 다이나믹 레인지를 압축, 극단적인 거래량 차이에서도 시각적 왜곡 없이 직관적인 비교가 가능하도록 개선.
- **파이 차트 UI/UX 위치 조정**: 차트 하단의 "Indicator Panel A/B" 서브플롯 타이틀을 삭제해 수직 여백을 확보하고, 파이 차트의 y축 도메인을 캔들 차트 최하단(`y=0.52` 중심)으로 끌어올려 볼륨 바와의 겹침을 방지하고 반투명(`opacity=0.7`) 처리를 통해 캔들 시인성을 높임.
- **백그라운드 캐시 에러 수정**: 대시보드 콜백에서 DataFrame에 `x_idx`가 없을 때 발생하는 `KeyError` 방어 코드 및 스마트 패닝(리프레시 시 기존 패닝 상태 유지) 로직 완비.

### FinViz 실시간 데이터 파이프라인 무인화 (Auto-renewal)
- **API 토큰 만료(HTTP 401) 예외 처리 연동**: 실시간 FinViz 데이터 수집 중 토큰이 만료되어 9시 35분경 수집이 중단되던 현상 파악.
- **curl_cffi 자동 갱신 모듈 통합**: `finviz/new_finviz.py`의 `fetch_and_save` 로직에 `FinvizTokenError` 발생 시 즉각적으로 `finviz_curl.py`를 백그라운드 호출하여 브라우저 Impersonate로 새 토큰을 발급받아 재시도하도록 통합. 수동 개입 없이 12시간 주기 무인화 달성.
- 불필요한 테스트 파일(`MOCK*.html`, `test_*.py`, 에러 로그 등) 깔끔하게 정리 완료.


## 2026-07-11

### 3x3 CVD 상관관계 백테스트 구현 및 리포트 자동화
- **3x3 매트릭스 분석 스크립트 작성**: `scripts/backtest_correlation.py`를 대대적으로 리팩토링하여, 교수님의 가설 검증을 위해 3개의 Market Cap(Mega, Micro, Nano)과 3개의 세션(프리마켓, 정규장, 애프터마켓)별로 CVD와 가격 간의 상관관계를 분할 계산하도록 로직 고도화.
- **분석 결과 마크다운 자동 생성**: 분석된 결과가 3x3 요약표(Summary)와 상세표(Detailed Results) 형태의 `CVD_3x3_Correlation_Report.md` 파일로 깔끔하게 자동 출력되도록 구현.

### IBKR 9개 티커 실시간 및 과거 데이터 수집 파이프라인
- **Master Collection 스크립트(`collect_9_tickers.py`) 추가**: NVDA, AAPL, MSFT(Mega) / GME, AMC, PLTR(Micro) / PENN, CHWY, RUM(Nano) 9개 티커의 데이터를 한 번에 관리.
- **백필 및 라이브 연동 자동화**: 실행 시 9개 티커에 대해 3일 치 과거 데이터(1초봉)를 먼저 병렬 백필(Backfill)로 채워 넣고, 완료 직후 곧바로 `TickCollector` 9개를 백그라운드에 띄워서 실시간 틱 수집으로 자연스럽게 넘어가도록 월요일 장 준비 완료.

### 대시보드(Dash) UI 및 사용성 개선
- **새 티커 검색 시 Auto-Backfill 구현**: 대시보드(`app.py`)에서 기존에 수집하지 않던 새로운 티커 검색 시 DB에 데이터가 없으면, 백그라운드에서 `ibkr.backfill` 서브프로세스를 즉각 1일 치 실행시키고, 그동안 화면은 FinViz 1분봉 데이터로 자동 우회(Fallback) 렌더링되도록 처리.
- **파이 차트 가시성 스케일업**: 사용자가 50개, 100개의 파이 차트 모드를 선택하여 화면에 밀도 있게 뿌려질 때 크기가 너무 작아지는 문제를 해결하기 위해, 50개 이상 선택 시 파이 차트 렌더링 기본 반경(`factor`)에 1.5배의 가중치를 주어 시인성 대폭 강화.

### CVD 방법론 핵심 오류 및 노이즈 규명
- **Wick vs Tick CVD 문제 발견**: 과거에 사용하던 캔들 꼬리 기반(Wick Decomposition) CVD는 상관관계가 0.99에 달하지만 이는 가격 변동 결과로 델타를 역산하는 "순환 논리"임을 리포팅. 반면 실제 틱 기반 CVD는 현재 상관관계가 극히 낮은데, 이는 호가 지연(Quote-lag)과 블록딜 아웃라이어(Noise) 때문임을 파악하고 차주 Lee-Ready 알고리즘 도입 근거 마련.

## 2026-07-14

### History Pipeline (Resolution Ladder) 전면 구현 — 계획: `{CVD Trading} History Pipeline Plan.md`
- **버그 규명**: FinViz 일봉(`d`) 데이터가 DB에 있었는데도(NVDA 2,522봉, 2016~2026) 웹앱에서 안 보이던 원인 = ① UI가 `d`를 아예 요청하지 않음(fallback 분기 전용), ② date가 문자열 `"01/02/2018"`인데 `add_cvd_columns`가 `%m/%d/%Y %H:%M`(시간 포함)으로 파싱 → 전부 NaT → `IndexError` 크래시.
- **`history/` 패키지 신설**: `schema.py`(tier·quality·서빙맵), `bvc.py`(BVC 추정, erf 근사 벡터화), `store.py`(quality-guard upsert + 워터마크), `migrate.py`, `backfill_finviz_daily.py`, `rollup.py`(증분 롤업), `session_grid.py`(빈 캔들), `serve.py`(tier-direct 서빙 + 증분 캐시), `prune.py`(보존정책 + Parquet 아카이브).
- **Resolution Ladder**: `1sec`(30d) → `1min`(180d) → `30min`(2y) → `1day`(영구) 계층을 `candles`에 구체화. 차트 TF는 매핑된 tier에서만 로드(일봉 차트가 틱을 만질 일 없음). 1day 8년 로드+집계 ≈ 285ms.
- **백필 실행 완료**: FinViz `d` 9티커(NVDA/AAPL/MSFT/GME/AMC/PLTR/PENN/CHWY/RUM) 각 최대 2,522봉(~10년) + 전체 30티커 i1→1min→30min→1day 롤업(1회 47s, 이후 증분). FinViz 429 대응 딜레이/재시도 포함.
- **CVD 추정**: 틱 없는 구간은 윅 대신 **BVC**(V×Φ(Δp/σ)) write-time 계산, `quality: tick|mixed|bvc|wick` 필드로 등급화. quality 가드로 실측(tick) 봉은 추정치로 절대 덮어쓰기 불가. 차트 음영도 quality 기반으로 전환.
- **빈 캔들(Empty Candle)**: 세션(프리 4:00~애프터 20:00) 그리드를 생성해 거래 없는 슬롯을 NaN 캔들(갭)으로 표시 — 시간축 왜곡 제거 (WKHS 1min: 3,254 실봉 → 10,560 그리드 슬롯). 음영 annotation이 갭마다 쪼개져 수천 shape 생기던 문제는 ffill+상한 100개로 해결.
- **app.py**: tier 서빙 경로 연결(raw_tick 라디오 = Tiered 모드), 새 티커 검색 시 FinViz 일봉+i1 즉시 백필 → 롤업 → 재서빙 자동화, 일봉 커버리지(backfill_meta) 체크, 기회적(60s 쿨다운) 증분 롤업.
- **ibkr/backfill.py**: `--barsize 1sec|1min|30min|1day` 파라미터화(1분+ 봉은 IBKR lookback 무제한), BVC write-time 추정, backfill_meta 기반 중단-재개.
- **남은 실행 항목 (IB Gateway 켠 뒤)**:
  `python -m ibkr.backfill --barsize 30min --days 730 --ticker NVDA AAPL MSFT GME AMC PLTR PENN CHWY RUM` (~40분)
  `python -m ibkr.backfill --barsize 1min --days 180 --ticker ...` (~5시간, 재개 가능)
  cron 권장: `python -m history.rollup --loop 60` (수집기와 병행), `python -m history.prune` (일 1회)

### BVC 추정 CVD 별도 트레이스 + 파이차트/성능 버그 픽스 (같은 날 오후)
- **CVD (BVC est.) 범례 추가**: `add_cvd_columns`에 기존 윅분해/CVD를 건드리지 않는 **독립 계산** 컬럼 `delta_bvc`/`cvd_bvc` 추가(`history.bvc.bvc_split` 재사용, 종가 기반 BVC, 동일한 클로징 옥션 중립화). `aggregate_pressure`가 `cvd_bvc_end`/`delta_bvc_sum`으로 캐리, 세션 그리드 ffill 목록에 포함, `_add_indicator_panel`에 청록 점선 "CVD (BVC est.)" 트레이스로 두 패널 모두 추가(기본 legendonly). 정적 HTML 버튼용 trace count 21→23 갱신.
- **FinViz vs IBKR 1분봉 불일치 원인 규명** (NVDA 7/13 실측): ① 분당 거래량 IB ≈ FinViz의 **52%**(중앙값; IB TRADES 피드는 odd-lot·다크풀 제외, FinViz는 통합테이프) — ×100 단위 문제 아님. ② 종가 차이 중앙값 2센트(최대 35센트), IB 고저폭이 더 좁음(필터된 피드). ③ 라이브 중 tiered 모드는 FinViz i1을 **가져오지 않아** 당일 분봉이 수집기(부분적, tick 분봉 vol ratio 0.05)에만 의존 → 희소/저볼륨. → **수정**: tiered 서브데일리 차트도 60초 쿨다운 i1 자동 페치 추가(롤업이 IBKR 없는 분에만 병합; quality 가드로 실측 우선 유지).
- **파이차트 불일치 근본 원인**: `DATA_CACHE`를 `build_chart` **호출 전** 프레임으로 저장 → build_chart가 MAX_CANDLES(6,000) 절단 후 x_idx를 재배열하므로, 프레임이 6,000봉을 넘는 순간(예: 세션그리드 1min 30일 ≈ 31,676 슬롯) 파이 patch가 **완전히 다른 봉**의 buy/sell을 집계. → 캐시를 build_chart 이후의 절단된 프레임으로 이동 + 파이 trace 인덱스도 캐시. 검증: 팬 후 interval 갱신 거쳐도 파이 값 == 해당 구간 막대 합 (정확 일치).
- **미래 빈 슬롯 버그**: 세션 그리드가 장중에 당일 20:00까지 미래 슬롯을 생성 → auto-tail(최근 100봉)이 전부 빈 미래 분에 걸려 화면이 텅 빔. `reindex_to_session_grid`에서 grid를 현재 ET 시각까지로 컷.
- **성능 최적화 (stretch-then-rescale 제거)**:
  - `update_graph`·파이 콜백에서 `State('main-chart','figure')` 제거 — 매 10초/매 팬 이벤트마다 수 MB figure JSON을 브라우저→서버로 업로드하던 것이 체감 느림의 주범. 파이 인덱스는 서버 캐시로 대체, 팬 좌표는 `relayoutData`(초소형)로 수신.
  - 서버가 **보이는 창 기준으로 y축(y/y2/y4)을 미리 fit**해서 figure 전달(클라이언트 refit 10% 패딩과 동일 공식) → 갱신 직후 세로로 쭉 늘어났다 다시 줄어드는 현상 제거. 축별 uirevision을 데이터 좌표공간(len+oldest) 키로 걸어 데이터 변화 시에만 서버 range 적용, 그 외엔 사용자 줌 유지.
  - refit JS: 절대 임계값 0.05(수백만 단위 CVD에선 사실상 매번 재렌더) → **상대 2%**로 변경, 디바운스 150→60ms.
  - refit 코어를 `window.__requestRefit`으로 분리하고 `plotly_restyle`에 바인딩 — **legend 토글 시에도 y축 자동 재조정**(기존엔 팬/갱신에서만 동작; BVC CVD처럼 스케일이 다른 트레이스 토글 시 필수).
  - 팬 중 interval 리빌드가 pan-state 커밋 전에 도착하면 tail로 스냅백하던 레이스: `relayoutData` 좌표를 우선 사용해 해소.
- **검증**(포트 8051, 실시간 틱 수집 중): 1min/raw_tick 뷰 렌더, 팬 → 10초 interval 후 x-range 그대로 유지 + 파이=막대 합 일치 + y축 즉시 fit, legend 토글 → y4 범위가 CVD(44k)와 BVC CVD(-74k)를 모두 포함하게 자동 확장. **8050의 사용자 앱은 재시작 필요.**

### 타임프레임 전환 속도 최적화 (프로파일링 → 픽스)
- **프로파일 결과 (전환 1회당)**: `aggregate_pressure` 1min 1.16s(전체 지배) + build_chart 1.33s + figure 5.4MB 전송/렌더 + (쿨다운 만료 시) FinViz 동기 fetch 1~3s + rollup 0.3s; raw_tick 전환은 12개 프레임 전부 집계로 1.7s; 팬으로 커진 days-to-load(최대 180일)가 모든 TF 전환에 전이.
- **① quality 집계 벡터화**: `aggregate_pressure`의 버킷당 Python reducer(`_agg_quality`, 19k 버킷 × 람다 호출)를 rank min/max resample로 대체 → **1.16s → 0.03s (35×)**. 시맨틱 동일(단일→자신, tick+추정 혼합→mixed, 그 외→최저 rank).
- **② 서빙 윈도우 현실화**: 차트가 어차피 MAX_CANDLES=6,000봉으로 절단하므로 `SERVE_WINDOW_DAYS` 1min 30→10, 3min 60→30, 5min 90→45, 15min 180→130 (6,000봉 ≈ 1min 기준 6거래일). 좌측 팬 시 days-to-load로 여전히 확장 가능.
- **③ FinViz fetch + rollup 백그라운드화**: `_spawn_data_refresh(ticker, fetch=)` 데몬 스레드(티커당 in-flight 가드) — 전환/interval이 HTTP 왕복을 기다리지 않음. Manual Refresh 버튼만 동기 유지(즉시성 기대). `_maybe_rollup`도 스레드로.
- **④ days-to-load를 TF별로 스코프**: store를 `{'days': N, 'tf': tf}`로 — 한 TF에서 깊게 팬해도 다른 TF 전환은 기본 윈도우 사용.
- **⑤ raw_tick/legacy 경로 `only=[active_tf]`**: `run_pipeline`에 `only` 파라미터 추가, 표시 중인 1개 프레임만 집계 (12→1; 정적 HTML 경로는 기존대로 전부 생성). raw_tick 1.7s→0.73s(남은 건 190k 틱 로드 자체).
- **결과 (브라우저 실측, fetch 훅으로 콜백 왕복 측정)**: tiered 1min COLD 2.14s→**0.13s**, WARM 1.48s→**0.05s**; 실제 전환 왕복 1min 122~373ms, 1hr 85~533ms, 1day ~740ms, raw_tick ~0.9s. 남은 체감 지연은 6,000봉 SVG 클라이언트 렌더(~0.5-1.5s)가 하한.

### 기타 최적화 라운드 — figure 페이로드 45% 감축 + raw_tick 폴링 + UX
- **figure JSON 5.43MB → 3.01MB** (매 10초 리프레시마다 전송/파싱되는 양). 분석 결과 customdata(동일 타임스탬프 문자열을 23개 트레이스에 중복) 2.51MB가 최대 낭비:
  - **customdata 축소**: 호버 타임스탬프를 패널당 기본 표시 트레이스(Buy/Sell Volume, CVD all-time)와 캔들에만 유지, 나머지 트레이스는 값만 표시 (2.51→0.96MB).
  - **x0/dx 전환**: 모든 지표/틱 트레이스가 동일한 정수 그리드이므로 6,000개 x 배열 대신 `x0=..., dx=1` (0.37→0.07MB). refit JS 두 벌(app.py·visualizer REFIT_JS)이 x0/dx 폼도 처리하도록 수정.
  - **막대 색상**: rgba 문자열 리스트 → 짧은 hex + `marker.opacity`, 옥션 지배 봉이 화면에 없으면 스칼라 색 (0.59→0.27MB).
  - **strip `base` 배열 제거**: `barmode="relative"`가 같은 offsetgroup의 동부호 막대를 자동 스택 (0.24MB↓). 브라우저에서 Buy% 하단/Sell% 상단 100% 스택 시각 확인.
  - **float 라운딩**: BVC 분해값의 12자리+ 소수를 직렬화 직전 1자리(비율류 4자리)로.
- **raw_tick 10초 폴링 3일치(19만 틱) 재로드 → 0.5일 기본** (해당 TF에서 팬으로 늘린 경우만 days-to-load 존중): 0.73s → **0.11s**/폴링.
- **TF/티커/소스 전환 시 팬 상태 리셋**: 이전 차트의 x_idx 좌표는 새 차트에서 무의미 — 전환하면 항상 최신 봉에서 열림 (이전엔 이전 TF의 팬 창이 그대로 적용돼 과거 구간이 뜸).
- Mongo 인덱스 점검: candles(ticker,timeframe,date)·raw_ticks(ticker,date) 모두 존재 확인 (조치 불요).
- build_chart 0.90s→0.71s (절단 후 복사 + 위 항목들).
- 검증: x0/dx 기반 y-refit 정상(팬 후 자동 fit), 파이=막대 일치 유지, strip 스택, TF 전환 즉시 tail 오픈. **8050 앱 재시작 필요.**

## 2026-07-15

### 온디맨드 틱 수집 + 볼륨 스케일링 + 뷰 유지/렉 개선 (5개 이슈 일괄)
- **① 온디맨드 동적 틱 수집기 신설 (`ibkr/dynamic_collector.py`)**: 앱에서 티커를 조회하면(tiered 모드) `collector_requests` 컬렉션에 요청이 업서트되고, 별도 프로세스인 동적 수집기(단일 IB 연결, clientId 60/백필용 61)가 5초 폴링으로 감지해 **그 순간부터 tick-by-tick 구독** 시작. 최대 3티커 LRU(최근 조회 순) 슬롯, 초과 시 가장 오래 안 본 티커 해지. 연구용 수집기(NVDA/SOFI/RUM, clientId 40~42)는 `--exclude`로 완전 격리 — 중복 수집/raw_ticks 오염 없음. 첫 구독 시 마지막 1sec 봉 이후 공백(최대 24h)을 히스토리컬 1sec 백필로 캐치업(`resume=False`). 이 과정에서 `ibkr/backfill.py` 버그 2개 발견·수정: backfill_meta coverage가 start/end union이라 갭을 표현 못 해 "커버됨"으로 오판 + `--no-resume`인데도 루프 안에서 coverage를 다시 로드해 첫 청크 후 나머지를 전부 스킵(→ resume일 때만 재로드하도록 수정). AAPL 오늘 09:38~13:44 갭은 일회성 재백필로 메움. 수집기 내 워커가 10분마다 수집 대상(동적+연구용)의 FinViz i1 갱신 + rollup 실행. 실행: `nohup python -m ibkr.dynamic_collector &` (현재 가동 중). 검증: TSLA/AAPL 검색 → 수초 내 구독 + 라이브 1sec 스트리밍 + AAPL은 아침 백필이 끊긴 09:38:33부터 정확히 캐치업.
- **② NVDA "볼륨 10%" 근본 해결 — consolidated 볼륨 스케일링**: 실측 결과 09:30~10:00 NVDA 1min tier(전부 quality=tick) 볼륨이 FinViz 대비 **9.7%** — tick-by-tick(AllLast) 스트림은 odd-lot·오프익스체인지 프린트가 빠지는데, quality=tick 가드가 FinViz 병합을 (올바르게) 차단해 얇은 볼륨이 그대로 노출되던 구조. `history/rollup.py`에 `scale_tick_volume` 단계 추가(1sec→1min과 1min→30min 사이): **실측 매수/매도 비율은 유지**하고 volume/buy/sell/delta를 FinViz i1 consolidated 볼륨 기준으로 스케일(계수 상한 200). 원본 틱 값은 `*_tick` 필드에 보존(무손실·가역), `vol_scaled`/`scale_factor` 마커 + `store.upsert_bars`가 봉 재작성 시 마커 자동 제거 → 멱등. 진행 중 분봉은 90초 가드(부분 i1 스냅샷에 고정 방지). 과거 스케일 시 상위 티어 워터마크 자동 되감기(30min/1day 재집계). 일회성 `python -m history.rollup --rescale` 실행 완료: NVDA 803/846·SOFI 1104/1239·RUM 463/493 버킷 스케일, 스케일 후 tier/FinViz 비율 중앙값 **1.000**. 1sec 티어·raw_ticks는 절대 불변(연구 데이터 무손상).
- **③ 소스 라벨 구성비 표시**: 타이틀의 `mode()` 단일 소스(오늘 봉이 IBKR인데 "finviz"로 표시되던 문제) → `finviz 45% + ibkr_hist 37% + ...` 구성비로. AAPL 시가 볼륨 2배 차이의 정체: tiered 09:30봉은 IBKR 백필(599k, 개장 옥션 포함), 레거시는 FinViz(232k) — 서로 다른 소스였고 라벨이 그걸 숨기고 있었음. AAPL 1초봉 09:38 중단은 구독 문제가 아니라 그 시각에 실행한 수동 백필의 종료 지점(라이브 수집기 부재) — ①로 해소.
- **④ 자동 갱신 시 뷰/스케일 리셋 제거**: 60초 유휴 시 tail로 스냅백하던 로직 삭제 — 팬한 뷰는 TF/티커 전환 또는 더블클릭(autorange) 전까지 무기한 유지. 팬 중에는 relayoutData의 명시적 y-range(사용자 y줌)가 서버 y-fit보다 우선. 검증: 팬 후 99초(자동갱신 ~10회) x-range 완전 유지, 더블클릭 시 라이브 추적 복귀.
- **⑤ 렉 개선**:
  - **off-tail interval 스킵**: 팬으로 tail에서 벗어나 있으면 interval 틱에서 파이프라인 로드+full figure 재전송+SVG 재렌더를 통째로 스킵(과거 봉은 불변이므로 보일 게 없음; 백그라운드 fetch/rollup은 계속). 판정은 클라이언트별 Store(`last-data-state`의 `n_active`+`pan-state`) 기반이라 다른 탭이 열려 있어도 오염 없음. 검증: 팬 상태 61초(6틱) 동안 재빌드 0회.
  - **`handle_panning` 고질 버그 수정**: x축이 선형 인덱스(x_idx)인데 `pd.to_datetime(2847)`→1970년으로 해석돼 **모든 팬 제스처마다 로드 일수 2배**(7→15→…→180일) 증식하던 문제. build_chart가 최신 MAX_CANDLES로 절단하므로 그 로드는 전부 낭비였음 → 좌측 끝(x0≤10) 도달 + 프레임이 캡 미만일 때만 증가.
  - **MAX_CANDLES 6000→3000** (visualizer 모듈 상수로 승격, app이 import): SVG는 화면 밖 봉도 전부 렌더하므로 이 상수가 곧 렌더 비용 — 리렌더 ~절반. 대신 차트 내 스크롤백도 3,000봉으로 감소(되돌리려면 상수 1개). `SERVE_WINDOW_DAYS`도 상응 축소(1min 10→5 등).
- **주의/운영**: 8050 사용자 앱 **재시작 필요**. 동적 수집기는 별도 프로세스로 계속 가동(`logs_dynamic_collector.log`). 틱 데이터 삭제 작업(prune 등)은 수집 기간 동안 실행하지 않음(요청 사항 — 이번 작업도 원본 보존 방식만 사용).

### 프로그레시브 스크롤백 — 기본 1,000봉 + 팬/줌아웃 시 자동 확장 로딩
- **요구**: "기본 1,000봉이면 충분, 그 이상 보려고 하면 그때 데이터를 더 불러오게" → 구현.
- **구조**: `MAX_CANDLES=1000`(visualizer 기본 렌더 캡) + per-TF `bars-to-show` 스토어. 뷰가 **왼쪽 끝(x0≤10)에 닿거나 렌더된 봉의 95% 이상이 보이도록 줌아웃**하면 캡이 2배(1000→2000→…→12,000 하드캡). 캡까지 렌더 중인데 데이터가 부족하면(`n_active < bars`) 서버가 `days-to-load`를 2배로(최대 365일) 늘려 Mongo에서 더 깊은 윈도우를 로드. `SERVE_WINDOW_DAYS`는 ~3,000봉치로 유지 — 로드(0.1s)는 싸고 렌더가 비싸므로, 처음 두 번의 확장은 재조회 없이 즉시.
- **확장 시 뷰 앵커링**: 캡이 커지면 x_idx 좌표 전체가 리매핑되므로, 보던 창을 (추가된 봉 수만큼) 시프트해 **같은 봉들이 그대로 화면에 유지**. 검증: 1000→2000 확장 후 앵커 봉이 정확히 index 1000에서 발견, 뷰 [1000,1100].
- **디버깅으로 잡은 3중 레이스/버그** (확장이 자꾸 tail로 튀거나 유실되던 원인):
  1. **마스터 x축**: 서브플롯 공유 x축의 마스터가 `xaxis3`(xaxis/xaxis2는 matches='x3')인데 서버가 `xaxis`(슬레이브)에만 범위를 써서 build_chart가 마스터에 남긴 tail이 승리 → `fig.update_xaxes(range=…)`로 모든 x축에 기록 + uirevision도 전체 x축에 적용(bars_req를 uirevision 키에 포함 — 캡 변경 = 좌표공간 변경).
  2. **relayoutData 덮임**: 팬 직후 y-refit JS의 프로그램적 relayout이 relayoutData를 y-only 이벤트로 교체 → 앵커 좌표를 성장 스토어 페이로드(`{'bars','tf','x0','x1'}`)에 실어 레이스 원천 차단.
  3. **요청 abort**: dash-renderer는 같은 콜백에 새 트리거가 오면 진행 중 요청을 abort — (a) 성장 판정 콜백 자체를 **클라이언트사이드**(`grow_scrollback` JS)로 이동(HTTP 없음 → abort 불가; 서버 `handle_panning`은 days 성장만 담당), (b) abort된 성장 재빌드는 다음 interval이 대신 배달하도록 off-tail 스킵에 `bars 일치` 조건 추가 + `bars_grew` 상태 델타 감지로 어느 트리거로 와도 앵커/리맵 적용. interval 배달 경로 실측 확인(3,558봉 도착, 뷰 시프트 정확).
- 검증 시 겪은 함정 메모: 동일 좌표로 두 번 relayout하면 prop이 안 변해 콜백이 안 뜸(실사용 드래그는 무관); 서버 재시작 후 브라우저 탭은 반드시 하드 리로드해야 콜백 스펙 불일치 500이 안 남.
- **8050 앱 재시작 + 브라우저 새로고침 필요.**

## 2026-07-16

### 외부 AI 수정분 검수 — 종가경매 오탐 수정 + 잠재 버그/데드 코드 정리
- **배경**: 다른 AI 세션이 넣은 변경(Z-score 버블, 품질 음영/경계선 개편, 조기종료 감지 "수정", assets/clientside.js 등)을 전수 검수. 버블 토글이 figure State 없이 통합된 점, Z-score 벡터화, 음영 run 병합, raw tick quality NaN→'tick' 마스킹 등은 건전 — 유지.
- **🔴 종가경매(조기종료) 감지 재작성 (`cvd/calculator.py _flag_auction_1min`)**: 외부 AI의 "그날 마지막 데이터 시각 ≤14시면 반장" 판정은 재현 결과 **라이브로 보는 매일 13:00~14:59 사이에 그날을 반장으로 오판** → 13:00 블록딜을 종가경매로 중화(사용자가 항의한 원 증상이 오후 3시까지 매일 재발), 반대로 진짜 반장은 애프터마켓 데이터(≤17시)가 있어 감지 실패(미탐). 재작성: 반장 여부를 시각이 아닌 **증거**로 판정 — 13:10~15:55 봉이 5개 이상 있고 그 중앙값이 오전(09:30~13:00) 중앙값의 10% 미만이면 반장(→ 12:59~13:01 창), 아니면 정규(→ 15:59~16:01 창); 판정 불가(라이브 오후 초반)면 플래그 없음. 앵커는 창 내 최대 볼륨 봉이 오전 중앙값의 `mult`(10)배 초과일 때만. docstring에만 남아있던 **spill 로직 복원**(앵커 이후 `spill_mult`(3)배 초과 지속 봉 전방 플래그 — 16:00 오버플로 프린트 다시 중화, 외부 AI 버전은 앵커 1개만 플래그해 회귀 상태였음). 하드코딩 5배 대신 `mult`/`spill_mult` 파라미터 사용, 미사용 `reg_med` 데드 코드 제거. 6개 시나리오 테스트 전부 통과(라이브 13:30+블록딜=0플래그, 진짜 반장=12:59+13:00, 정규일=15:59+16:00, 크로스 없는 피드=0, 오전 라이브=0, 장 마감 후 13:00 블록딜=15:59만).
- **🟡 버블 trace 가변 개수 → TF 버튼 visibility 어긋남 수정 (`cvd/visualizer.py`)**: Z-score 버블 trace가 "임계 초과 봉이 있을 때만" 추가돼 tf당 trace 수가 23/24로 흔들림 — updatemenus 버튼(정적 HTML/레거시 경로)의 고정 배열과 misalign. 버블 없는 tf에도 빈 placeholder trace를 추가해 개수 고정, visibility/showlegend 배열을 `show_bubbles` 반영(`head=2`)으로 수정. 실데이터 검증: tiered 경로 버블 on/off = trace 49/48(정확히 ±1), 레거시 9TF×24=216 trace = visibility 216 정렬 일치.
- **🧹 정리**: `assets/clientside.js` 삭제(index_string 인라인 스크립트가 {%scripts%} 뒤에 실행되며 동명 `refit_y`를 덮어써 실행 자체가 안 되던 죽은 파일; 내용도 x0/dx 미지원·존재하지 않는 yaxis3 참조의 구버전), 루트 스크래치 `test_zscore.py` 삭제, 경계선 텍스트 제거 후 남은 `y_levels`/`y_idx`/`last_text_x` 데드 변수 제거.
- **8050 앱 재시작 필요** (calculator/visualizer 변경 반영).
