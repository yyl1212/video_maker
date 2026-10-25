"""对外错误不包含数据库语句、文件路径或用户剧本。"""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class BackendError(Exception):
    """客户端只提供可公开错误；未知执行状态不能当作普通失败解锁。"""

    def __init__(self, code: str, message: str, execution_uncertain: bool):
        super().__init__(message)
        self.code, self.message = code, message
        self.execution_uncertain = execution_uncertain
