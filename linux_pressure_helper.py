#!/usr/bin/python3 -I
"""Root-owned optional deployment helper: only reduce the dedicated RVC quota."""
import subprocess
import sys
import json
import os
from pathlib import Path
import time

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('normal','soft','critical'):
        raise SystemExit(2)
    quota={'normal':'160%','soft':'100%','critical':'50%'}[sys.argv[1]]
    subprocess.run(['/usr/bin/systemctl','set-property','--runtime',
                    'whale-rvc-fast.service','CPUQuota='+quota],check=True,timeout=3)
    folder=Path('/run/whale-resource-guard')
    folder.mkdir(exist_ok=True,mode=0o755)
    temp=folder/'.pressure.tmp'
    with temp.open('w') as out:
        json.dump({'level':sys.argv[1],'at':time.time()},out)
    temp.chmod(0o644);os.replace(temp,folder/'pressure.json')

if __name__=='__main__':main()
