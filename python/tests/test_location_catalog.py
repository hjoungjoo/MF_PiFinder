from PiFinder import location_catalog


def test_location_catalog_loads_countries_without_north_korea():
    countries = location_catalog.countries()
    codes = {country["code"] for country in countries}

    assert "KR" in codes
    assert "US" in codes
    assert "KP" not in codes


def test_location_catalog_returns_regions_districts_and_places():
    regions = location_catalog.regions("KR")
    seoul = next(region for region in regions if region["name"] == "Seoul")

    districts = location_catalog.districts("KR", seoul["code"])
    assert districts

    places = location_catalog.places("KR", seoul["code"], districts[0]["code"])
    assert any(place["name"] == "Seoul" for place in places)


def test_location_catalog_unknown_keys_return_empty_lists():
    assert location_catalog.regions("XX") == []
    assert location_catalog.districts("XX", "01") == []
    assert location_catalog.places("XX", "01", "001") == []
