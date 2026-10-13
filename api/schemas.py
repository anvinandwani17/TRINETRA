from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from pydantic import BaseModel
from typing import Optional, List, Literal

class RestoreEdgeRequest(BaseModel):
    edge_id: str

class JobState(str, Enum):
    QUEUED = "QUEUED"
    DISCOVERING = "DISCOVERING"
    PLANNING = "PLANNING"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    PREPROCESSING = "PREPROCESSING"
    AI_PREPROCESSING = "AI_PREPROCESSING"
    DETECTING = "DETECTING"
    OSM_QUERYING = "OSM_QUERYING"
    INFERENCE = "INFERENCE"
    NETWORK_ANALYSIS = "NETWORK_ANALYSIS"
    REPORTING = "REPORTING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class AOI(BaseModel):
    type: str = Field(default="Polygon")
    coordinates: list[list[list[float]]]


class AnalyzeRequest(BaseModel):
    aoi: AOI
    flood_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    config: str = "default"


class PipelineRequest(AnalyzeRequest):
    config: Literal['baseline', 'experimental_ai'] = 'baseline'
    allow_downloads: bool = Field(default=False, strict=True)
    transport: Literal['s3', 'odata'] = 's3'
    max_product_gib: float = Field(default=3.0, gt=0, le=3.0, allow_inf_nan=False)


class JobResponse(BaseModel):
    id: str
    state: JobState
    created_at: datetime
    flood_date: str
    message: str


class LayerResponse(BaseModel):
    name: str
    type: str
    data: dict[str, Any]
    status: str = "available"


class SummaryResponse(BaseModel):
    id: str
    state: JobState
    flood_date: str
    layers: list[str]
    message: str
