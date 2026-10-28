"""ServiceArea: checks addresses against Greater Melbourne (A10).

The external mapping service assumed in A10 is not available to a student
prototype, so it is replaced by a small built-in gazetteer of suburbs and a
straight-line distance with a road factor. Only this class knows that, so a
real geocoding adapter can replace it without touching the rest of the code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.common import ValidationError
from ..domain.delivery import Address

ROAD_FACTOR = 1.3
GREATER_MELBOURNE_POSTCODES = (
    (3000, 3210), (3335, 3341), (3427, 3442), (3750, 3812),
    (3910, 3944), (3975, 3978),
)

# postcode -> (suburb, latitude, longitude); approximate suburb centres.
GAZETTEER: dict[str, tuple[str, float, float]] = {
    "3000": ("Melbourne", -37.8136, 144.9631),
    "3002": ("East Melbourne", -37.8126, 144.9850),
    "3006": ("Southbank", -37.8230, 144.9640),
    "3008": ("Docklands", -37.8170, 144.9460),
    "3011": ("Footscray", -37.8000, 144.9000),
    "3020": ("Sunshine", -37.7880, 144.8330),
    "3029": ("Hoppers Crossing", -37.8820, 144.7000),
    "3030": ("Werribee", -37.9000, 144.6610),
    "3051": ("North Melbourne", -37.7990, 144.9460),
    "3053": ("Carlton", -37.8000, 144.9670),
    "3064": ("Craigieburn", -37.6000, 144.9420),
    "3065": ("Fitzroy", -37.7990, 144.9780),
    "3070": ("Northcote", -37.7700, 145.0000),
    "3083": ("Bundoora", -37.6980, 145.0600),
    "3084": ("Heidelberg", -37.7560, 145.0670),
    "3121": ("Richmond", -37.8230, 144.9980),
    "3122": ("Hawthorn", -37.8220, 145.0350),
    "3124": ("Camberwell", -37.8380, 145.0700),
    "3128": ("Box Hill", -37.8190, 145.1220),
    "3134": ("Ringwood", -37.8150, 145.2290),
    "3141": ("South Yarra", -37.8380, 144.9930),
    "3145": ("Malvern East", -37.8740, 145.0420),
    "3150": ("Glen Waverley", -37.8780, 145.1650),
    "3168": ("Clayton", -37.9150, 145.1290),
    "3175": ("Dandenong", -37.9870, 145.2150),
    "3182": ("St Kilda", -37.8640, 144.9810),
    "3186": ("Brighton", -37.9070, 145.0000),
    "3199": ("Frankston", -38.1440, 145.1260),
    "3805": ("Narre Warren", -38.0270, 145.3030),
    "3977": ("Cranbourne", -38.0990, 145.2830),
}


@dataclass(frozen=True)
class AreaCheck:
    """Result of checking one address."""

    accepted: bool
    message: str


class ServiceArea:
    """Validates addresses and supplies distances (A2 CRC 4.4)."""

    def check(self, address: Address) -> AreaCheck:
        """Return whether ``address`` can be served, with a reason."""
        if address.state.upper() != "VIC" or \
                not self._in_greater_melbourne(int(address.postcode)):
            return AreaCheck(False, f"{address.suburb} {address.postcode} is "
                             "outside the Greater Melbourne service area.")
        entry = GAZETTEER.get(address.postcode)
        if entry is None:
            return AreaCheck(False, f"Postcode {address.postcode} could not "
                             "be verified. Please check it, or phone the "
                             "office to arrange the delivery.")
        if entry[0].lower() != address.suburb.strip().lower():
            return AreaCheck(False, f"Postcode {address.postcode} belongs to "
                             f"{entry[0]}, not {address.suburb}.")
        return AreaCheck(True, "Address verified.")

    def validate(self, address: Address, role: str) -> None:
        """Raise ``ValidationError`` naming ``role`` if not serviceable."""
        result = self.check(address)
        if not result.accepted:
            raise ValidationError(f"{role} address: {result.message}")

    def distance_km(self, origin: Address, destination: Address) -> float:
        """Return an estimated road distance between two verified addresses."""
        _, lat1, lon1 = GAZETTEER[origin.postcode]
        _, lat2, lon2 = GAZETTEER[destination.postcode]
        straight = self._haversine(lat1, lon1, lat2, lon2)
        return round(max(straight * ROAD_FACTOR, 2.0), 1)

    @staticmethod
    def _in_greater_melbourne(postcode: int) -> bool:
        return any(low <= postcode <= high
                   for low, high in GREATER_MELBOURNE_POSTCODES)

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float,
                   lon2: float) -> float:
        radius_km = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = (math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2)
             * math.sin(d_lambda / 2) ** 2)
        return 2 * radius_km * math.asin(math.sqrt(a))
