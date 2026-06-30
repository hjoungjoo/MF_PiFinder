# MF PiFinder Location Catalog

The web `Locations > Add New Location` page can now pre-fill coordinates after selecting a country, region, county/district, and city/place from an offline catalog.

## Data Source

The initial data is generated from the GeoNames export dump.

```text
https://download.geonames.org/export/dump/
```

Source files:

```text
cities5000.zip
countryInfo.txt
admin1CodesASCII.txt
admin2Codes.txt
KR.zip
```

GeoNames data is licensed under CC BY 4.0, and attribution is retained in PiFinder documentation and catalog metadata. North Korea is excluded by country code `KP`. Korea is augmented with the country-specific GeoNames `KR.zip` extract because the global `cities5000` file is too sparse for Seoul district/neighborhood selection.

## Included Files

```text
python/PiFinder/data/location_catalog.json
python/PiFinder/location_catalog.py
scripts/build_location_catalog.py
python/tests/test_location_catalog.py
```

`location_catalog.json` is a processed offline data file used without internet access at runtime. The server does not send the entire JSON file to the browser; it exposes small step-by-step APIs for the selected country/region/district.

## Web Behavior

The `Add New Location` form uses this selection flow:

```text
Country > State / Province > County / District > City / Place
```

Selecting `City / Place` fills the existing manual-entry fields.

- Location Name: filled only when the field is empty.
- Latitude / Longitude: filled from GeoNames coordinates.
- Altitude: filled from GeoNames elevation or DEM data.
- Error: filled with `1000m` by default. Users should adjust this for the actual observing site.
- Source: stored as `GeoNames: country / region / district / place`.

Manual coordinate entry and DMS entry continue to work.

## Regenerating The Catalog

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

After regenerating the catalog, run:

```bash
python3 -m pytest python/tests/test_location_catalog.py -q
```
