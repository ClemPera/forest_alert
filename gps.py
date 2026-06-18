import time
from dataclasses import dataclass, field
from typing import Optional


# Location-source enum values (from meshtastic protobuf Position.location_source).
LOC_UNSET = 0      # no location source — coordinates may be meaningless
LOC_MANUAL = 1     # manually entered by the user — may be approximate
LOC_INTERNAL = 2   # internal GPS module — real fix
LOC_EXTERNAL = 3   # external GPS source — real fix

# When precision_bits is at or below this threshold the effective horizontal
# resolution is ~20km or worse (40000km / 2^11 ≈ 19.5km).  Such a position
# should not be trusted for search/navigation.
APPROX_PRECISION_BITS = 11

# Accepts either the protobuf enum string ("LOC_INTERNAL") or the raw int.
_LOCATION_SOURCE_NAMES = {
    "LOC_UNSET": LOC_UNSET,
    "LOC_MANUAL": LOC_MANUAL,
    "LOC_INTERNAL": LOC_INTERNAL,
    "LOC_EXTERNAL": LOC_EXTERNAL,
}


def _normalize_location_source(val) -> Optional[int]:
    """Normalise location_source to an int (0-3), accepting either the raw
    protobuf integer or the enum string name produced by MessageToDict."""
    if val is None:
        return None
    if isinstance(val, bool):  # bool is a subclass of int — reject it
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return _LOCATION_SOURCE_NAMES.get(val)
    return None


@dataclass
class Position:
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    timestamp: Optional[int] = None   # unix epoch from the node
    received_at: float = field(default_factory=time.time)  # local time we got this position
    # Position-quality fields — all optional, older firmware may omit them.
    precision_bits: Optional[int] = None
    location_source: Optional[int] = None  # one of the LOC_* constants
    hdop: Optional[float] = None            # horizontal dilution of precision
    sats_in_view: Optional[int] = None
    fix_quality: Optional[int] = None       # 0=invalid, 1=GPS, 2=DGPS
    fix_type: Optional[int] = None          # 0=none, 1=2D, 2=3D

    def age_seconds(self) -> float:
        return time.time() - self.received_at

    def maps_url(self) -> str:
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"

    def osm_url(self) -> str:
        return (
            f"https://www.openstreetmap.org/"
            f"?mlat={self.latitude}&mlon={self.longitude}&zoom=15"
        )

    def precision_km(self) -> Optional[float]:
        """Approximate horizontal resolution in kilometres, derived from
        precision_bits (40000km / 2^bits).  None when precision_bits is unknown."""
        if self.precision_bits is None:
            return None
        return 40000.0 / (2 ** self.precision_bits)

    def is_approximate(self) -> bool:
        """True when the position should not be trusted for precise navigation:
        the source is unset/manual, or precision_bits indicates ~20km+ resolution."""
        if self.location_source is not None and self.location_source <= LOC_MANUAL:
            return True
        if self.precision_bits is not None and self.precision_bits <= APPROX_PRECISION_BITS:
            return True
        return False

    def quality_warning(self) -> Optional[str]:
        """Human-readable warning about position unreliability, or None when
        the position looks trustworthy."""
        warnings: list[str] = []
        if self.location_source == LOC_UNSET:
            warnings.append("position source unknown — coordinates may be invalid")
        elif self.location_source == LOC_MANUAL:
            warnings.append("position is manually entered — may be approximate")
        km = self.precision_km()
        if km is not None and self.precision_bits <= APPROX_PRECISION_BITS:
            warnings.append(f"low precision (~{km:.0f}km resolution, not a precise GPS fix)")
        if self.sats_in_view is not None and self.sats_in_view < 4:
            warnings.append(f"only {self.sats_in_view} satellites in view")
        if not warnings:
            return None
        return "; ".join(warnings)

    def __str__(self) -> str:
        parts = [f"{self.latitude:.6f}, {self.longitude:.6f}"]
        if self.altitude is not None:
            parts.append(f"alt {self.altitude:.0f}m")
        age = self.age_seconds()
        if age > 60:
            parts.append(f"({age / 60:.0f}min ago)")
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


def _extract_quality_fields(pos_dict: dict) -> dict:
    """Pull position-quality fields from a Meshtastic position dict.

    The meshtastic Python library serialises protobuf via MessageToDict, so
    field names are camelCase (precisionBits, locationSource, satsInView,
    HDOP, fixQuality, fixType).  We also accept snake_case variants in case
    the dict comes from a different source.
    """
    def _pick(*keys):
        for k in keys:
            if k in pos_dict and pos_dict[k] is not None:
                return pos_dict[k]
        return None

    return {
        "precision_bits": _pick("precisionBits", "precision_bits"),
        "location_source": _normalize_location_source(
            _pick("locationSource", "location_source")
        ),
        "hdop": _pick("HDOP", "hdop"),
        "sats_in_view": _pick("satsInView", "sats_in_view"),
        "fix_quality": _pick("fixQuality", "fix_quality"),
        "fix_type": _pick("fixType", "fix_type"),
    }


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
        **_extract_quality_fields(pos_dict),
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
        **_extract_quality_fields(pos_dict),
    )
