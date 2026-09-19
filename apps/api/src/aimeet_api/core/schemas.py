from pydantic import BaseModel


class ValidationDetail(BaseModel):
    path: list[str | int]
    type: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ValidationDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
