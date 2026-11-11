"""Bounded process-memory reclamation for AstrBot on Linux, Windows and macOS."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import gc
import json
import math
import os
from pathlib import Path
import tempfile
import time

import psutil


def memory_sample() -> dict:
    system = psutil.virtual_memory()
    return dict(system_total_bytes=system.total,system_available_bytes=system.available,
                system_used_percent=100*(system.total-system.available)/system.total,
                process_rss_bytes=psutil.Process().memory_info().rss)


def trim_free_heap() -> bool:
    if os.name!='posix':
        return False
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (OSError,AttributeError):
        return False
    trim.argtypes=[ctypes.c_size_t]
    trim.restype=ctypes.c_int
    return bool(trim(0))


@dataclass
class Policy:
    threshold: float = 60.
    cooldown: float = 300.
    periodic: float = 900.
    last_attempt: float | None = None
    started: float | None = None

    def __post_init__(self):
        if not math.isfinite(self.threshold) or not 1<=self.threshold<=99:
            raise ValueError('threshold must be 1..99')
        for value in (self.cooldown,self.periodic):
            if not math.isfinite(value) or value<120:
                raise ValueError('cleanup interval must be at least 120 seconds')

    def reason(self,sample,now):
        if self.started is None:self.started=now
        used=sample['system_used_percent']
        if not math.isfinite(used) or not 0<=used<=100:raise ValueError('invalid usage')
        since=now-(self.last_attempt if self.last_attempt is not None else self.started)
        if self.last_attempt is not None and since<self.cooldown:return 'cooldown'
        if used>self.threshold:return 'pressure'
        if since>=self.periodic:return 'periodic'
        return 'idle'


class Guard:
    def __init__(self,policy,sampler=memory_sample,collector=gc.collect,
                 trimmer=trim_free_heap,clock=time.monotonic):
        self.policy,self.sampler=policy,sampler
        self.collector,self.trimmer,self.clock=collector,trimmer,clock
        self.policy.started=self.clock()
        self.attempts=0

    def tick(self):
        before=self.sampler()
        now=self.clock()
        reason=self.policy.reason(before,now)
        row=dict(at=time.time(),pid=os.getpid(),reason=reason,before=before,attempts=self.attempts)
        if reason not in ('periodic','pressure'):return row
        self.policy.last_attempt=now
        self.attempts+=1
        begin=time.monotonic()
        row['collected_objects']=self.collector(2)
        row['allocator_trim_reported_release']=self.trimmer()
        row['duration_ms']=round((time.monotonic()-begin)*1000,3)
        row['after']=self.sampler()
        row['rss_released_bytes']=max(0,before['process_rss_bytes']-row['after']['process_rss_bytes'])
        row['attempts']=self.attempts
        return row


def write_status(path: Path,row):
    raw=(json.dumps(row,ensure_ascii=False)+'\n').encode()
    if len(raw)>16384:raise ValueError('status too large')
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.memory-')
    try:
        with os.fdopen(fd,'wb') as out:
            os.chmod(name,0o600);out.write(raw)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
