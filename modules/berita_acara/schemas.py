from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class LineIn(BaseModel):
    product_id: Optional[str] = ""
    nama_item: str = Field(..., min_length=1)
    kode_item: str = ""
    uom: str = ""
    qty: float = Field(..., gt=0)


class HeaderIn(BaseModel):
    no_form: str = Field(..., min_length=1)
    purpose_id: str = Field(..., min_length=1)
    purpose_name: str = Field(..., min_length=1)
    outlet_id: str = Field(..., min_length=1)
    outlet_name: str = Field(..., min_length=1)
    dibuat_oleh: List[str]
    disetujui_oleh: List[str]
    mengetahui_oleh: List[str]

    @model_validator(mode="after")
    def _validate_names(self):
        if not self.dibuat_oleh:
            raise ValueError("dibuat_oleh harus diisi")
        if not self.disetujui_oleh:
            raise ValueError("disetujui_oleh harus diisi")
        if not self.mengetahui_oleh:
            raise ValueError("mengetahui_oleh harus diisi")
        return self


class SubmitIn(BaseModel):
    header: HeaderIn
    items: List[LineIn]

    @model_validator(mode="after")
    def _validate_items(self):
        if not self.items:
            raise ValueError("items harus diisi")
        return self
