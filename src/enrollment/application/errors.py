"""Errors of the application layer; the HTTP adapter translates them (403, 404, 503) in one place."""


class Forbidden(Exception):
    def __init__(self, message: str = "this role may not perform the operation") -> None:
        super().__init__(message)
        self.message = message


class NotFound(Exception):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(message)
        self.message = message


class DependencyUnavailable(Exception):
    """A dependency the service needs (the database) did not answer: alive, but not ready (503)."""

    def __init__(self, dependency: str) -> None:
        self.dependency = dependency
        self.message = f"{dependency} is not reachable"
        super().__init__(self.message)
