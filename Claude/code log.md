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
