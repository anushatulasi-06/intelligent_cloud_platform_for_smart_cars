"""Service-layer errors. main.py turns each into an HTTP response {"detail": "<message>"}
with the class's status code, so services never import FastAPI."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class Unauthorized(ServiceError):
    status_code = 401


class Forbidden(ServiceError):
    status_code = 403


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class Unprocessable(ServiceError):
    status_code = 422


class ServiceFailure(ServiceError):
    status_code = 500


class Unavailable(ServiceError):
    status_code = 503
