"""本地测试运行器。

用法（在仓库根目录执行）:
  py -3 test/_run_tests.py              # 跑 test/ 下全部用例
  py -3 test/_run_tests.py test_db ...  # 只跑指定模块

两点环境适配：
1. 源码都在 src/ 下、模块之间用裸名互相 import，所以先把 src/ 与 test/ 加进 sys.path。
2. `tempfile.mkdtemp` 固定以 0o700 建目录，受限沙箱的令牌无法写入这类目录，
   会让 setUp/tearDown 报 PermissionError；这里换成默认权限版本，
   临时目录统一落在仓库根目录的 .runtmp 下。
"""

import os
import random
import sys
import tempfile
import unittest

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_TEST_DIR)

for _path in (os.path.join(_BASE_DIR, "src"), _TEST_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_TMPDIR = os.path.join(_BASE_DIR, ".runtmp")
os.makedirs(_TMPDIR, exist_ok=True)


def _mkdtemp(suffix=None, prefix=None, dir=None):
    suffix = suffix or ""
    prefix = prefix or "tmp"
    base = dir or _TMPDIR
    while True:
        name = os.path.join(base, f"{prefix}{random.randrange(1 << 30):08x}{suffix}")
        if os.path.exists(name):
            continue
        os.mkdir(name)  # 默认权限，沙箱内可读写
        return name


tempfile.mkdtemp = _mkdtemp
tempfile.tempdir = _TMPDIR


if __name__ == "__main__":
    loader = unittest.TestLoader()
    if sys.argv[1:]:
        suite = loader.loadTestsFromNames(sys.argv[1:])
    else:
        suite = loader.discover(_TEST_DIR, pattern="test_*.py",
                                top_level_dir=_TEST_DIR)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
