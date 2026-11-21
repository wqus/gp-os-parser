from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Source = Literal["intermark", "malina", "invest7", "s5d", "nfgroup"]


class RentStep(BaseModel):
    amount_rub: Money | None = None
    from_date: date | None = None
    from_month: int | None = Field(default=None, ge=1)
    to_month: int | None = Field(default=None, ge=1)
    raw: str


class Tenant(BaseModel):
    name: str | None = None
    status: Literal["current", "future", "unknown"] = "unknown"
    lease_start: date | None = None
    lease_end: date | None = None
    lease_term_raw: str | None = None
    indexation_raw: str | None = None
    rent_current_rub: Money | None = None
    rent_future_rub: Money | None = None
    rate_start: date | None = None
    rate_start_raw: str | None = None
    rent_steps: list[RentStep] = Field(default_factory=list)
    turnover_formula_raw: str | None = None
    source_excerpt: str


class Evidence(BaseModel):
    field: str
    text: str
    location: str


class Discrepancy(BaseModel):
    field: str
    values: list[str]
    explanation: str


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Source
    listing_id: str
    url: str
    collected_at: datetime
    last_successful_check_at: datetime
    status: Literal["active", "removed"] = "active"
    title: str
    description: str
    price_rub: Money | None = None
    area_m2: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)] | None = None
    address: str | None = None
    map_url: str | None = None
    map_url_generated: bool = False
    metro: str | None = None
    vat: str | None = None
    commission: str | None = None
    owner: str | None = None
    ownership_form: str | None = None
    photos: list[str] = Field(default_factory=list)
    tenants: list[Tenant] = Field(default_factory=list)
    rent_current_rub: Money | None = None
    rent_future_rub: Money | None = None
    payback_years: Money | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requested_category: str | None = None
    replacement_note: str | None = None
    html_file: str | None = None
    html_sha256: str

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"
