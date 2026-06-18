from gps import (
    LOC_EXTERNAL,
    LOC_INTERNAL,
    LOC_MANUAL,
    LOC_UNSET,
    Position,
    _lat_lon_from_dict,
    _normalize_location_source,
    extract_position_from_node,
    extract_position_from_packet,
)


class TestPosition:
    def test_maps_url(self):
        p = Position(48.8566, 2.3522)
        assert p.maps_url() == "https://www.google.com/maps?q=48.8566,2.3522"

    def test_osm_url(self):
        p = Position(48.8566, 2.3522)
        url = p.osm_url()
        assert "mlat=48.8566" in url
        assert "mlon=2.3522" in url
        assert "zoom=15" in url

    def test_age_seconds_recent(self):
        p = Position(0.0, 0.0)
        assert p.age_seconds() < 1

    def test_str_with_altitude(self):
        p = Position(48.8566, 2.3522, altitude=120)
        s = str(p)
        assert "48.856600, 2.352200" in s
        assert "alt 120m" in s

    def test_str_without_altitude(self):
        p = Position(1.0, 2.0)
        assert "alt" not in str(p)

    def test_received_at_defaults_to_now(self):
        # Regression: received_at used to default to 0.0, making age_seconds()
        # return ~1.7 billion seconds. It should now default to construction time.
        p = Position(1.0, 2.0)
        assert p.age_seconds() < 1

    def test_str_uses_english_ago(self):
        # Regression: __str__ used to say "(il y a Xmin)" in French.
        p = Position(1.0, 2.0, received_at=0.0)
        assert "il y a" not in str(p)
        assert "ago" in str(p)


class TestPrecisionKm:
    def test_full_precision(self):
        p = Position(1.0, 2.0, precision_bits=32)
        assert p.precision_km() is not None
        assert p.precision_km() < 0.001  # sub-metre

    def test_20km_precision(self):
        p = Position(1.0, 2.0, precision_bits=11)
        km = p.precision_km()
        assert km is not None
        assert 19 < km < 20  # ~19.5km

    def test_unknown_returns_none(self):
        p = Position(1.0, 2.0)
        assert p.precision_km() is None


class TestIsApproximate:
    def test_low_precision_bits(self):
        p = Position(1.0, 2.0, precision_bits=11, location_source=LOC_INTERNAL)
        assert p.is_approximate() is True

    def test_high_precision_internal_gps(self):
        p = Position(1.0, 2.0, precision_bits=15, location_source=LOC_INTERNAL)
        assert p.is_approximate() is False

    def test_unset_source(self):
        p = Position(1.0, 2.0, location_source=LOC_UNSET)
        assert p.is_approximate() is True

    def test_manual_source(self):
        p = Position(1.0, 2.0, location_source=LOC_MANUAL)
        assert p.is_approximate() is True

    def test_external_gps_is_precise(self):
        p = Position(1.0, 2.0, location_source=LOC_EXTERNAL, precision_bits=17)
        assert p.is_approximate() is False

    def test_no_quality_info_defaults_to_precise(self):
        # When we have no quality metadata at all, don't cry wolf.
        p = Position(1.0, 2.0)
        assert p.is_approximate() is False

    def test_boundary_12_bits_not_approximate(self):
        # 12 bits → ~9.8km, just above the 11-bit (~20km) threshold.
        p = Position(1.0, 2.0, precision_bits=12, location_source=LOC_INTERNAL)
        assert p.is_approximate() is False


class TestQualityWarning:
    def test_good_fix_returns_none(self):
        p = Position(1.0, 2.0, precision_bits=17, location_source=LOC_INTERNAL,
                     sats_in_view=8)
        assert p.quality_warning() is None

    def test_unset_source(self):
        p = Position(1.0, 2.0, location_source=LOC_UNSET)
        w = p.quality_warning()
        assert w is not None
        assert "source unknown" in w

    def test_manual_source(self):
        p = Position(1.0, 2.0, location_source=LOC_MANUAL)
        w = p.quality_warning()
        assert w is not None
        assert "manually entered" in w

    def test_low_precision_includes_km(self):
        p = Position(1.0, 2.0, precision_bits=11, location_source=LOC_INTERNAL)
        w = p.quality_warning()
        assert w is not None
        assert "low precision" in w
        assert "km" in w

    def test_low_satellites(self):
        p = Position(1.0, 2.0, location_source=LOC_INTERNAL,
                     sats_in_view=2, precision_bits=17)
        w = p.quality_warning()
        assert w is not None
        assert "2 satellites" in w

    def test_multiple_warnings_joined(self):
        p = Position(1.0, 2.0, location_source=LOC_MANUAL, precision_bits=8)
        w = p.quality_warning()
        assert w is not None
        assert "manually entered" in w
        assert "low precision" in w
        assert ";" in w


class TestQualityDescription:
    def test_no_metadata_says_not_reported(self):
        # Common case for local node DB entry: no quality fields at all.
        p = Position(1.0, 2.0)
        desc = p.quality_description()
        assert "not reported" in desc

    def test_internal_gps_with_good_precision(self):
        p = Position(1.0, 2.0, location_source=LOC_INTERNAL,
                     precision_bits=17, sats_in_view=8)
        desc = p.quality_description()
        assert "GPS fix" in desc
        assert "internal GPS" in desc
        assert "precision" in desc
        assert "8 satellites" in desc

    def test_external_gps(self):
        p = Position(1.0, 2.0, location_source=LOC_EXTERNAL, precision_bits=17)
        desc = p.quality_description()
        assert "external GPS" in desc

    def test_manual_source_returns_warning(self):
        p = Position(1.0, 2.0, location_source=LOC_MANUAL)
        desc = p.quality_description()
        assert "manually entered" in desc

    def test_unset_source_returns_warning(self):
        p = Position(1.0, 2.0, location_source=LOC_UNSET)
        desc = p.quality_description()
        assert "source unknown" in desc

    def test_low_precision_returns_warning(self):
        p = Position(1.0, 2.0, precision_bits=11, location_source=LOC_INTERNAL)
        desc = p.quality_description()
        assert "low precision" in desc

    def test_internal_gps_without_precision_bits(self):
        # Common for remote nodes: locationSource present but no precisionBits.
        p = Position(1.0, 2.0, location_source=LOC_INTERNAL)
        desc = p.quality_description()
        assert "GPS fix" in desc
        assert "internal GPS" in desc
        # Should NOT say "not reported" since we do have the source.
        assert "not reported" not in desc

    def test_full_precision_shows_meters(self):
        p = Position(1.0, 2.0, location_source=LOC_INTERNAL, precision_bits=32)
        desc = p.quality_description()
        assert "m" in desc  # precision in metres, not km


class TestNormalizeLocationSource:
    def test_int_passthrough(self):
        assert _normalize_location_source(2) == LOC_INTERNAL

    def test_string_name(self):
        assert _normalize_location_source("LOC_EXTERNAL") == LOC_EXTERNAL

    def test_none(self):
        assert _normalize_location_source(None) is None

    def test_invalid_string(self):
        assert _normalize_location_source("LOC_FOO") is None

    def test_bool_rejected(self):
        # bool is a subclass of int but should not be accepted.
        assert _normalize_location_source(True) is None


class TestLatLonFromDict:
    def test_float_fields(self):
        assert _lat_lon_from_dict({"latitude": 1.0, "longitude": 2.0}) == (1.0, 2.0)

    def test_integer_i_fields(self):
        assert _lat_lon_from_dict(
            {"latitudeI": 10000000, "longitudeI": 20000000}
        ) == (1.0, 2.0)

    def test_zero_zero_returns_none(self):
        assert _lat_lon_from_dict({"latitude": 0.0, "longitude": 0.0}) is None

    def test_missing_returns_none(self):
        assert _lat_lon_from_dict({}) is None

    def test_partial_returns_none(self):
        assert _lat_lon_from_dict({"latitude": 1.0}) is None

    def test_partial_i_returns_none(self):
        assert _lat_lon_from_dict({"latitudeI": 10000000}) is None


class TestExtractFromPacket:
    def _packet(self, portnum="POSITION_APP", pos=None):
        decoded = {"portnum": portnum}
        if pos is not None:
            decoded["position"] = pos
        return {"decoded": decoded}

    def test_position_packet(self):
        pkt = self._packet(pos={"latitude": 10.0, "longitude": 20.0})
        p = extract_position_from_packet(pkt)
        assert p is not None
        assert p.latitude == 10.0
        assert p.longitude == 20.0

    def test_non_position_packet_returns_none(self):
        pkt = self._packet(portnum="TEXT_MESSAGE_APP")
        assert extract_position_from_packet(pkt) is None

    def test_zero_position_returns_none(self):
        pkt = self._packet(pos={"latitude": 0.0, "longitude": 0.0})
        assert extract_position_from_packet(pkt) is None

    def test_preserves_altitude_and_timestamp(self):
        pkt = self._packet(pos={"latitude": 1.0, "longitude": 2.0,
                                "altitude": 55, "time": 999})
        p = extract_position_from_packet(pkt)
        assert p is not None
        assert p.altitude == 55
        assert p.timestamp == 999

    def test_extracts_camelcase_quality_fields(self):
        pkt = self._packet(pos={
            "latitude": 1.0, "longitude": 2.0,
            "precisionBits": 11, "locationSource": "LOC_INTERNAL",
            "HDOP": 5, "satsInView": 4, "fixQuality": 1, "fixType": 2,
        })
        p = extract_position_from_packet(pkt)
        assert p is not None
        assert p.precision_bits == 11
        assert p.location_source == LOC_INTERNAL
        assert p.hdop == 5
        assert p.sats_in_view == 4
        assert p.fix_quality == 1
        assert p.fix_type == 2

    def test_extracts_snake_case_quality_fields(self):
        pkt = self._packet(pos={
            "latitude": 1.0, "longitude": 2.0,
            "precision_bits": 17, "location_source": "LOC_EXTERNAL",
            "hdop": 2, "sats_in_view": 9,
        })
        p = extract_position_from_packet(pkt)
        assert p is not None
        assert p.precision_bits == 17
        assert p.location_source == LOC_EXTERNAL
        assert p.hdop == 2
        assert p.sats_in_view == 9

    def test_missing_quality_fields_default_none(self):
        pkt = self._packet(pos={"latitude": 1.0, "longitude": 2.0})
        p = extract_position_from_packet(pkt)
        assert p is not None
        assert p.precision_bits is None
        assert p.location_source is None
        assert p.hdop is None
        assert p.sats_in_view is None
        assert p.fix_quality is None
        assert p.fix_type is None


class TestExtractFromNode:
    def test_with_position(self):
        node = {"position": {"latitude": 5.0, "longitude": 6.0,
                             "altitude": 100, "time": 123}}
        p = extract_position_from_node(node)
        assert p is not None
        assert p.latitude == 5.0
        assert p.longitude == 6.0
        assert p.altitude == 100
        assert p.timestamp == 123

    def test_without_position(self):
        assert extract_position_from_node({}) is None
        assert extract_position_from_node({"position": {}}) is None

    def test_zero_position(self):
        node = {"position": {"latitude": 0.0, "longitude": 0.0}}
        assert extract_position_from_node(node) is None

    def test_extracts_quality_fields(self):
        node = {"position": {
            "latitude": 5.0, "longitude": 6.0,
            "precisionBits": 11, "locationSource": "LOC_MANUAL",
            "satsInView": 3,
        }}
        p = extract_position_from_node(node)
        assert p is not None
        assert p.precision_bits == 11
        assert p.location_source == LOC_MANUAL
        assert p.sats_in_view == 3
        assert p.is_approximate() is True

    def test_missing_quality_fields_default_none(self):
        node = {"position": {"latitude": 5.0, "longitude": 6.0}}
        p = extract_position_from_node(node)
        assert p is not None
        assert p.precision_bits is None
        assert p.location_source is None