"""Optional companion/RVC admission helper; no model, network, or config mutation."""
import json
import time
from pathlib import Path

STATUS = Path('/opt/whale-astrbot/data/plugin_data/astrbot_plugin_whale_memory_guard/pressure.json')


def should_defer_background(path=STATUS, now=None):
    try:
        if path.stat().st_size > 4096:return False
        row=json.loads(path.read_bytes())
        age=(time.time() if now is None else now)-float(row['at'])
        return 0 <= age <= 30 and row.get('defer_background') is True
    except (OSError,ValueError,TypeError,KeyError):
        # An observer outage must not stop ordinary chat or leave an eternal gate.
        return False
