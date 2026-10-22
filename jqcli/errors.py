from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JqcliError(Exception):
    message: str
    code: str = "api_error"
    exit_code: int = 4
    details: dict[str, Any] = field(default_factory=dict)


class NotAuthenticatedError(JqcliError):
    def __init__(self, message: str = "未登录，请先提供 JQCLI_TOKEN/JQCLI_COOKIE 或执行认证命令", *, details: dict[str, Any] | None = None):
        super().__init__(message=message, code="not_authenticated", exit_code=1, details=details or {})


class NotFoundError(JqcliError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message=message, code="not_found", exit_code=2, details=details or {})


class UsageError(JqcliError):
    def __init__(self, message: str):
        super().__init__(message=message, code="usage_error", exit_code=3)


class ApiError(JqcliError):
    def __init__(self, message: str, status_code: int | None = None, details: dict[str, Any] | None = None):
        merged = details or {}
        if status_code is not None:
            merged = {**merged, "status_code": status_code}
        super().__init__(message=message, code="api_error", exit_code=4, details=merged)


class NetworkError(JqcliError):
    def __init__(self, message: str = "无法连接到聚宽服务器，请检查网络", *, details: dict[str, Any] | None = None):
        super().__init__(message=message, code="network_error", exit_code=5, details=details or {})


class FileError(JqcliError):
    def __init__(self, message: str):
        super().__init__(message=message, code="file_error", exit_code=6)


class ConfirmationRequiredError(JqcliError):
    def __init__(self, message: str = "该操作需要确认；非交互模式请显式传入 --yes"):
        super().__init__(message=message, code="confirmation_required", exit_code=7)


class TimeoutError(JqcliError):
    def __init__(self, message: str = "等待回测完成超时，请稍后查询结果"):
        super().__init__(message=message, code="timeout", exit_code=8)


def error_payload(error: JqcliError) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    }

