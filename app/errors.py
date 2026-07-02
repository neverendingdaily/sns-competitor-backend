class ApiError(Exception):
    status_code = 500

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class BadRequestError(ApiError):
    status_code = 400


class UnauthorizedError(ApiError):
    status_code = 401


class AccountNotFoundError(ApiError):
    status_code = 404


class PlatformNotImplementedError(ApiError):
    status_code = 501


class UpstreamUnavailableError(ApiError):
    status_code = 502
