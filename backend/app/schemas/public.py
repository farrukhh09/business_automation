"""Public map-pin page ``/l/{token}`` (04-api.md §13, 03-business-rules.md §7).

No authentication and no personal data beyond what the customer wrote themselves: the page shows
the business name, the address the customer gave, the point already known (if any) and the city
centre so the map can open at the right place.
"""

from pydantic import BaseModel, ConfigDict, Field


class LatLng(BaseModel):
    lat: float
    lng: float


class PublicLocationOut(BaseModel):
    business_name: str
    address_raw: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    city_center: LatLng
    #: The geocoder's best guess (street / microdistrict centroid) to centre the map on; no pin is set.
    suggested: LatLng | None = None
    expired: bool = False
    used: bool = False


class PublicLocationIn(BaseModel):
    """``POST /public/location/{token}``: the point the customer marked on the map."""

    model_config = ConfigDict(extra="ignore")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PublicLocationSubmitOut(BaseModel):
    ok: bool = True
