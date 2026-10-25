"""对外错误不包含数据库语句、文件路径或用户剧本。"""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
