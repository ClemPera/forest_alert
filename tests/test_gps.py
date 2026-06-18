from gps import (
    Position,
    _lat_lon_from_dict,
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