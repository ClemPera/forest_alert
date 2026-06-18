import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    timestamp: Optional[int] = None   # unix epoch from the node
    received_at: float = 0.0          # local time we got this position

    def age_seconds(self) -> float:
        return time.time() - self.received_at

    def maps_url(self) -> str:
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"

    def osm_url(self) -> str:
        return (
            f"https://www.openstreetmap.org/"
            f"?mlat={self.latitude}&mlon={self.longitude}&zoom=15"
        )

    def __str__(self) -> str:
        parts = [f"{self.latitude:.6f}, {self.longitude:.6f}"]
        if self.altitude is not None:
            parts.append(f"alt {self.altitude:.0f}m")
        age = self.age_seconds()
        if age > 60:
            parts.append(f"(il y a {age / 60:.0f}min)")
        return " | ".join(parts)


def _lat_lon_from_dict(d: dict) -> Optional[tuple[float, float]]:
    """Extract (lat, lon) from a Meshtastic position dict, handling both
    float fields and integer *I fields (degrees × 1e7)."""
    lat = d.get("latitude")
    lon = d.get("longitude")

    if lat is None or lon is None:
        lat_i = d.get("latitudeI")
        lon_i = d.get("longitudeI")
        if lat_i is not None and lon_i is not None:
            lat, lon = lat_i / 1e7, lon_i / 1e7

    if lat is None or lon is None:
        return None
    if float(lat) == 0.0 and float(lon) == 0.0:
        return None  # unset default position

    return float(lat), float(lon)


def extract_position_from_node(node_info: dict) -> Optional[Position]:
    """Extract position from a Meshtastic node DB entry (interface.nodes[id])."""
    pos_dict = node_info.get("position")
    if not pos_dict:
        return None

    coords = _lat_lon_from_dict(pos_dict)
    if not coords:
        return None

    lat, lon = coords
    return Position(
        latitude=lat,
        longitude=lon,
        altitude=pos_dict.get("altitude"),
        timestamp=pos_dict.get("time"),
        received_at=time.time(),
    )


def extract_position_from_packet(packet: dict) -> Optional[Position]:
    """Extract position from a POSITION_APP packet."""
    decoded = packet.get("decoded", {})
    if decoded.get("portnum") != "POSITION_APP":
        return None

    pos_dict = decoded.get("position", {})
    coords = _lat_lon_from_dict(pos_dict)
    if not coords:
        return None

    lat, lon = coords
    return Position(
        latitude=lat,
        longitude=lon,
        altitude=pos_dict.get("altitude"),
        timestamp=pos_dict.get("time"),
        received_at=time.time(),
    )
