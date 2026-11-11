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


class ResourcePressure:
    """Host-wide two-tier admission control; never kills live business tasks."""
    def __init__(self, cpu_soft=50., cpu_critical=70., memory_soft=60.,
                 memory_critical=80., recovery_samples=3):
        values = (cpu_soft, cpu_critical, memory_soft, memory_critical)
        if any(not math.isfinite(v) or not 0 < v <= 100 for v in values):
            raise ValueError('invalid resource thresholds')
        if cpu_soft >= cpu_critical or memory_soft >= memory_critical:
            raise ValueError('thresholds must be ordered')
        self.cpu_soft, self.cpu_critical = cpu_soft, cpu_critical
        self.memory_soft, self.memory_critical = memory_soft, memory_critical
        self.level, self.healthy = 'normal', 0
        self.recovery_samples = max(1, int(recovery_samples))

    def update(self, sample):
        cpu, memory = sample['system_cpu_percent'], sample['system_used_percent']
        if any(not math.isfinite(v) or not 0 <= v <= 100 for v in (cpu, memory)):
            raise ValueError('invalid resource sample')
        level = ('critical' if cpu >= self.cpu_critical or memory >= self.memory_critical
                 else 'soft' if cpu > self.cpu_soft or memory > self.memory_soft else 'normal')
        if level != 'normal':
            self.level, self.healthy = level, 0
        elif cpu <= self.cpu_soft - 5 and memory <= self.memory_soft - 5:
            self.healthy += 1
            if self.healthy >= self.recovery_samples:
                self.level = 'normal'
        else:
            self.healthy = 0
        return dict(level=self.level, defer_background=self.level != 'normal',
                    cpu_percent=cpu, memory_percent=memory,
                    memory_reclaim=memory > self.memory_soft,
                    generation=2 if memory >= self.memory_critical else 1)


def memory_sample() -> dict:
    system = psutil.virtual_memory()
    return dict(system_total_bytes=system.total,system_available_bytes=system.available,
                system_used_percent=100*(system.total-system.available)/system.total,
                process_rss_bytes=psutil.Process().memory_info().rss,
                system_cpu_percent=psutil.cpu_percent(interval=.1))


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
        self._critical_memory=False

    def tick(self):
        before=self.sampler()
        now=self.clock()
        reason=self.policy.reason(before,now)
        critical_memory=before['system_used_percent'] >= 80
        if critical_memory and not self._critical_memory:
            reason='pressure'
        self._critical_memory=critical_memory
        row=dict(at=time.time(),pid=os.getpid(),reason=reason,before=before,attempts=self.attempts)
        if reason not in ('periodic','pressure'):return row
        self.policy.last_attempt=now
        self.attempts+=1
        begin=time.monotonic()
        # Full collection is reserved for critical memory pressure. On busy
        # servers routine generation-2 sweeps can stall the interpreter.
        generation = 2 if before['system_used_percent'] >= 80 else 1
        row['collected_objects']=self.collector(generation)
        row['generation']=generation
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
