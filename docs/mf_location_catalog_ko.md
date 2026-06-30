# MF PiFinder 위치 카탈로그

웹 `Locations > Add New Location` 화면에서 국가, 지역, 군/구, 도시/장소를 선택해 기본 좌표를 입력할 수 있도록 오프라인 위치 카탈로그를 추가했다.

## 데이터 출처

초기 데이터는 GeoNames export dump를 사용한다.

```text
https://download.geonames.org/export/dump/
```

사용 파일:

```text
cities5000.zip
countryInfo.txt
admin1CodesASCII.txt
admin2Codes.txt
KR.zip
```

GeoNames 데이터는 CC BY 4.0 라이선스이며, PiFinder 문서와 데이터 metadata에 출처를 남긴다. 북한은 요청에 따라 국가 코드 `KP`를 제외하고 생성한다. 한국은 전세계 공통 `cities5000` 데이터가 서울/구/동 단위에서 너무 성기기 때문에, GeoNames 국가별 전체 덤프인 `KR.zip`을 추가로 섞어 비교적 자세한 행정구역과 동/장소를 선택할 수 있게 했다.

## 포함 파일

```text
python/PiFinder/data/location_catalog.json
python/PiFinder/location_catalog.py
scripts/build_location_catalog.py
python/tests/test_location_catalog.py
```

`location_catalog.json`은 앱 실행 중 인터넷 연결 없이 사용할 수 있는 가공 데이터다. 서버는 전체 JSON을 브라우저로 한 번에 보내지 않고, 선택 단계별 API로 필요한 목록만 반환한다.

## 웹 동작

`Add New Location` form에서 다음 순서로 선택한다.

```text
Country > State / Province > County / District > City / Place
```

`City / Place`를 선택하면 기존 수동 입력 필드에 기본값을 채운다.

- Location Name: 비어 있을 때만 장소 이름을 입력한다.
- Latitude / Longitude: GeoNames 좌표를 입력한다.
- Altitude: GeoNames elevation 또는 DEM 값을 입력한다.
- Error: 기본 `1000m`로 입력한다. 실제 관측지는 필요에 따라 사용자가 수정한다.
- Source: `GeoNames: country / region / district / place` 형식으로 기록한다.

수동 입력과 DMS 입력 기능은 그대로 유지된다.

## 재생성 방법

```bash
mkdir -p /tmp/pifinder_geonames
curl -L -o /tmp/pifinder_geonames/cities5000.zip https://download.geonames.org/export/dump/cities5000.zip
curl -L -o /tmp/pifinder_geonames/countryInfo.txt https://download.geonames.org/export/dump/countryInfo.txt
curl -L -o /tmp/pifinder_geonames/admin1CodesASCII.txt https://download.geonames.org/export/dump/admin1CodesASCII.txt
curl -L -o /tmp/pifinder_geonames/admin2Codes.txt https://download.geonames.org/export/dump/admin2Codes.txt
curl -L -o /tmp/pifinder_geonames/KR.zip https://download.geonames.org/export/dump/KR.zip

python3 scripts/build_location_catalog.py \
  --source-dir /tmp/pifinder_geonames \
  --output python/PiFinder/data/location_catalog.json
```

재생성 뒤에는 다음을 확인한다.

```bash
python3 -m pytest python/tests/test_location_catalog.py -q
```
