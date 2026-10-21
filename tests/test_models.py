"""Tests for reading UPS's records."""

from datetime import date, datetime, time

import pytest

from ups_sdk.models import Activity, DeliveryDate, DeliveryTime, Package


def test_blank_dates_and_times_are_none() -> None:
    """UPS's blank dates and times read as none."""
    activity = Activity.model_validate({"date": "", "time": " "})

    assert activity.date is None
    assert activity.time is None
    assert activity.occurred_at is None


def test_an_activity_without_an_offset_is_local_time() -> None:
    """Without an offset an activity's time is the local time UPS gave."""
    activity = Activity.model_validate({"date": "20261021", "time": "091500"})

    assert activity.occurred_at == datetime(2026, 10, 21, 9, 15)  # noqa: DTZ001 - UPS gave no offset


@pytest.mark.parametrize("offset", ["", "EST", "-5", "+05:3x"])
def test_an_unreadable_offset_is_ignored(offset: str) -> None:
    """An offset that is not a sign and four digits is ignored."""
    activity = Activity.model_validate({"date": "20261021", "gmtOffset": offset})

    assert activity.occurred_at is not None
    assert activity.occurred_at.tzinfo is None


def test_a_positive_offset_is_read() -> None:
    """An offset east of UTC is read."""
    activity = Activity.model_validate({"date": "20261021", "time": "120000", "gmtOffset": "+0530"})

    assert activity.occurred_at is not None
    assert activity.occurred_at.utcoffset() is not None
    assert activity.occurred_at.utcoffset().total_seconds() == 5.5 * 3600  # type: ignore[union-attr]


def test_dates_and_times_read_from_python_values_too() -> None:
    """Dates and times given as Python values are kept."""
    delivery = DeliveryDate(type="DEL", date=date(2026, 10, 22))
    window = DeliveryTime(type="EDW", start_time=time(9), end_time=time(12))

    assert delivery.date == date(2026, 10, 22)
    assert window.start_time == time(9)


def test_a_package_without_lists_reads_empty_lists() -> None:
    """A package UPS sends without lists reads empty ones."""
    package = Package.model_validate({"trackingNumber": "1Z999AA10123456784"})

    assert package.activity == []
    assert package.delivery_date == []
