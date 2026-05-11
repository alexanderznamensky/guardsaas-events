from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntityDescription, SensorDeviceClass, SensorStateClass


@dataclass(frozen=True, kw_only=True)
class BillingSensorDescription(SensorEntityDescription):
    value_fn: callable
    attrs_fn: callable | None = None
    device_key_fn: callable | None = None
    unique_key_fn: callable | None = None
    name_fn: callable | None = None
    available_fn: callable | None = None
    native_unit_of_measurement: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
