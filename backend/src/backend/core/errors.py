from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Do not reflect submitted OTPs, push tokens, or private fields in errors.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "request_id": getattr(request.state, "request_id", None),
                    "details": [
                        {"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        payload: dict[str, Any] = {
            "error": {"code": exc.code, "message": exc.message},
        }
        if request_id:
            payload["error"]["request_id"] = request_id
        return JSONResponse(status_code=exc.status_code, content=payload)
