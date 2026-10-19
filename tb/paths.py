"""所有路径的唯一来源。

本工具数据在 DATA（默认仓库下的 data/）；仓库之外可选写客户端 hook 条目和一个 macOS 登录启动项。
测试用 TASKBOARD_HOME 把用户主目录整个换成临时目录，这时忽略 CODEX_HOME / CLAUDE_CONFIG_DIR，保证不碰真实配置。
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SANDBOX = os.environ.get('TASKBOARD_HOME')
HOME = Path(_SANDBOX) if _SANDBOX else Path.home()
DATA = Path(os.environ.get('TASKBOARD_DATA') or REPO / 'data')


def _env_dir(name, default):
    value = None if _SANDBOX else os.environ.get(name)
    return Path(value) if value else default


CODEX_HOME = _env_dir('CODEX_HOME', HOME / '.codex')
CLAUDE_HOME = _env_dir('CLAUDE_CONFIG_DIR', HOME / '.claude')

if sys.platform == 'darwin':
    CLAUDE_DESKTOP = HOME / 'Library' / 'Application Support' / 'Claude'
elif os.name == 'nt':
    CLAUDE_DESKTOP = (HOME / 'AppData' / 'Roaming' if _SANDBOX else Path(os.environ.get('APPDATA') or HOME / 'AppData' / 'Roaming')) / 'Claude'
else:
    CLAUDE_DESKTOP = HOME / '.config' / 'Claude'

CODEX_HOOKS = CODEX_HOME / 'hooks.json'
CLAUDE_SETTINGS = CLAUDE_HOME / 'settings.json'


def data_dir():
    """用到时才创建数据目录，导入本模块不产生任何写入。"""
    DATA.mkdir(parents=True, exist_ok=True)
    return DATA
