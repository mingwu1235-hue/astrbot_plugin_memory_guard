#!/usr/bin/python3 -I
"""Optional root companion to the unprivileged plugin; one fixed RVC cgroup."""
import json
from pathlib import Path
import subprocess
import time

STATUS=Path('/opt/whale-astrbot/data/plugin_data/astrbot_plugin_whale_memory_guard/pressure.json')

def main():
    previous=None
    while True:
        try:
            if STATUS.stat().st_size>4096:raise ValueError('status limit')
            row=json.loads(STATUS.read_bytes())
            level=row.get('level')
            if level not in ('normal','soft','critical'):raise ValueError('level')
            if not 0<=time.time()-float(row['at'])<=30:
                # Keep the last safe quota on observer failure, never reboot.
                time.sleep(5);continue
            if level!=previous:
                subprocess.run(['/usr/local/libexec/whale-resource-pressure',level],check=True,timeout=5)
                previous=level
        except (OSError,ValueError,subprocess.SubprocessError):
            pass
        time.sleep(5)

if __name__=='__main__':main()
