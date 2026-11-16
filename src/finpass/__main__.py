"""让 ``python -m finpass`` 可用：CI 里比 PATH 上的 finpass 脚本更可靠。"""

import sys

from .cli import main

sys.exit(main())
