"""Models for UPS's APIs, named and shaped as UPS documents them."""

import datetime as dt

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.alias_generators import to_camel


class UPSModel(BaseModel):
    """A UPS record: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True, validate_by_alias=True)


def _date(value: object) -> object:
    """Read UPS's ``YYYYMMDD`` dates; a blank one is no date."""
    if isinstance(value, str):
        return dt.datetime.strptime(value, "%Y%m%d").date() if value.strip() else None  # noqa: DTZ007 - a calendar day
    return value


def _time(value: object) -> object:
    """Read UPS's ``HHMMSS`` times; a blank one is no time."""
    if isinstance(value, str):
        return dt.datetime.strptime(value, "%H%M%S").time() if value.strip() else None  # noqa: DTZ007 - a wall-clock time
    return value


class Token(BaseModel):
    """An OAuth access token from the client credentials grant."""

    access_token: str
    token_type: str | None = None
    #: Seconds until the token expires. UPS sends it as a string.
    expires_in: float
    issued_at: str | None = None
    client_id: str | None = None
    scope: str | None = None
    status: str | None = None


class Address(UPSModel):
    """A place a package was, or is going."""

    address_line1: str | None = None
    address_line2: str | None = None
    address_line3: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country: str | None = None
    country_code: str | None = None


class Location(UPSModel):
    """Where an activity happened."""

    address: Address | None = None
    #: Site Location Indicator Code, UPS's code for the facility.
    slic: str | None = None


class Status(UPSModel):
    """A package's status, current or at one activity.

    ``type`` is UPS's status type: ``M`` billing information received, ``P`` pickup, ``I`` in
    transit, ``O`` out for delivery, ``D`` delivered, ``X`` exception, ``RS`` returned to shipper,
    and others UPS lists under its API codes.
    """

    type: str | None = None
    description: str | None = None
    code: str | None = None
    status_code: str | None = None
    simplified_text_description: str | None = None


class Activity(UPSModel):
    """One scan or event in a package's history, at the local time where it happened."""

    location: Location | None = None
    status: Status | None = None
    date: dt.date | None = None
    time: dt.time | None = None
    gmt_date: str | None = None
    gmt_time: str | None = None
    #: The local time's offset from UTC, such as ``-05:00``.
    gmt_offset: str | None = None
    logical_scan: bool | None = None

    _read_date = field_validator("date", mode="before")(_date)
    _read_time = field_validator("time", mode="before")(_time)

    @property
    def occurred_at(self) -> dt.datetime | None:
        """Answer when the activity happened, with its UTC offset when UPS gave one."""
        if self.date is None:
            return None
        moment = dt.datetime.combine(self.date, self.time or dt.time())
        offset = _offset(self.gmt_offset)
        return moment if offset is None else moment.replace(tzinfo=offset)


def _offset(value: str | None) -> dt.timezone | None:
    """Read an offset such as ``-05:00`` or ``+0530``; anything else is no offset."""
    if not value:
        return None
    text = value.strip().replace(":", "")
    if len(text) != 5 or text[0] not in "+-" or not text[1:].isdigit():  # noqa: PLR2004 - sign and four digits
        return None
    minutes = int(text[1:3]) * 60 + int(text[3:])
    return dt.timezone(dt.timedelta(minutes=minutes if text[0] == "+" else -minutes))


class DeliveryDate(UPSModel):
    """A delivery date of a package.

    ``type`` is ``SDD`` scheduled, ``RDD`` rescheduled or ``DEL`` delivered.
    """

    type: str | None = None
    date: dt.date | None = None

    _read_date = field_validator("date", mode="before")(_date)


class DeliveryTime(UPSModel):
    """The time, or window, of a delivery.

    ``type`` is ``EOD`` end of day, ``CMT`` commit time, ``EDW``/``CDW``/``IDW`` an estimated,
    confirmed or imminent delivery window, or ``DEL`` the delivered time.
    """

    type: str | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None

    _read_times = field_validator("start_time", "end_time", mode="before")(_time)


class DeliveryInformation(UPSModel):
    """Who took a delivered package, and where it was left."""

    location: str | None = None
    received_by: str | None = None


class Weight(UPSModel):
    """A package's weight; UPS sends the number as a string."""

    unit_of_measurement: str | None = None
    weight: str | None = None


class Dimension(UPSModel):
    """A package's dimensions; UPS sends the numbers as strings."""

    length: str | None = None
    width: str | None = None
    height: str | None = None
    unit_of_dimension: str | None = None


class Service(UPSModel):
    """The UPS service a package ships with."""

    code: str | None = None
    description: str | None = None
    level_code: str | None = None


class ReferenceNumber(UPSModel):
    """A reference number on a package or its shipment."""

    type: str | None = None
    number: str | None = None


class AlternateTrackingNumber(UPSModel):
    """Another number the package is known by."""

    type: str | None = None
    number: str | None = None


class Milestone(UPSModel):
    """One milestone of a package's journey, when milestones were asked for."""

    category: str | None = None
    code: str | None = None
    current: bool | None = None
    description: str | None = None
    linked_activity: str | None = None
    state: str | None = None


class Package(UPSModel):
    """One package's tracking."""

    tracking_number: str | None = None
    current_status: Status | None = None
    status_code: str | None = None
    status_description: str | None = None
    activity: list[Activity] = []
    delivery_date: list[DeliveryDate] = []
    delivery_time: DeliveryTime | None = None
    delivery_information: DeliveryInformation | None = None
    weight: Weight | None = None
    dimension: Dimension | None = None
    service: Service | None = None
    reference_number: list[ReferenceNumber] = []
    alternate_tracking_number: list[AlternateTrackingNumber] = []
    milestones: list[Milestone] = []
    package_count: int | None = None


class Warning(UPSModel):  # noqa: A001 - UPS's own name for it
    """A warning UPS answered with, such as a number it has no information for."""

    code: str | None = None
    message: str | None = None


class Shipment(UPSModel):
    """The shipment a tracked number belongs to."""

    inquiry_number: str | None = None
    package: list[Package] = []
    warnings: list[Warning] = []


class TrackResponse(UPSModel):
    """What the Tracking API answered."""

    shipment: list[Shipment] = []


class TrackApiResponse(UPSModel):
    """The Tracking API's response envelope."""

    track_response: TrackResponse
