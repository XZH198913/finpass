"""FinPass：承诺决策可追溯，不承诺自动得到正确答案。

公开 API 只有 open / start / next / answer / export，另加 open 时设置的 profile_info；
规则与列名全部来自 profiles/*.yaml，引擎层不认识具体业务。
"""

from .session import answer, export, next, open, start

__version__ = "0.0.3"

__all__ = [
    "__version__",
    "open",
    "start",
    "next",
    "answer",
    "export",
]
