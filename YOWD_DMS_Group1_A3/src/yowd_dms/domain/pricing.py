"""Pricing policies (Strategy pattern, A9).

All rates are assumed values for the prototype and must be confirmed with
the business owners (see assumption A3-2 in the report).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta

from .delivery import DeliveryRequest, ParcelSize, ServiceLevel

SIZE_SURCHARGE = {ParcelSize.SMALL: 0.0, ParcelSize.MEDIUM: 5.0,
                  ParcelSize.LARGE: 12.0}
AVERAGE_SPEED_KMH = 30.0


class PricingStrategy(ABC):
    """Suggests a fee and a delivery window for a request."""

    service_level: ServiceLevel

    def base_fee(self, request: DeliveryRequest, distance_km: float) -> float:
        """Return the fee shared by every level, before the level's rule."""
        weight_extra = max(0.0, request.total_weight_kg - 5.0) * 0.50
        extra_parcels = (len(request.parcels) - 1) * 3.0
        return (12.0 + 1.20 * distance_km + SIZE_SURCHARGE[
            request.largest_size] + weight_extra + extra_parcels)

    @abstractmethod
    def suggest_fee(self, request: DeliveryRequest,
                    distance_km: float) -> float:
        """Return the suggested fee in dollars, rounded to cents."""

    @abstractmethod
    def delivery_window(self, distance_km: float) -> timedelta:
        """Return the time promised from pickup to delivery."""


class StandardPricing(PricingStrategy):
    """Same-day standard service."""

    service_level = ServiceLevel.STANDARD

    def suggest_fee(self, request: DeliveryRequest,
                    distance_km: float) -> float:
        return round(self.base_fee(request, distance_km), 2)

    def delivery_window(self, distance_km: float) -> timedelta:
        hours = distance_km / AVERAGE_SPEED_KMH
        return timedelta(minutes=round(hours * 60) + 120)


class ExpressPricing(PricingStrategy):
    """Priority service: 50% dearer, delivered with less slack."""

    service_level = ServiceLevel.EXPRESS

    def suggest_fee(self, request: DeliveryRequest,
                    distance_km: float) -> float:
        return round(self.base_fee(request, distance_km) * 1.5, 2)

    def delivery_window(self, distance_km: float) -> timedelta:
        hours = distance_km / AVERAGE_SPEED_KMH
        return timedelta(minutes=round(hours * 60) + 30)
