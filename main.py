from __future__ import annotations

import asyncio
import subprocess
import time
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
from .guard import Guard, Policy, ResourcePressure, memory_sample, write_status
from .network import NetworkObserver


class WhaleMemoryGuard(Star):
    def __init__(self,context: Context,config: dict):
        super().__init__(context)
        self.context=context
        self.config=config
        self.interval=max(5,min(300,int(config.get('interval_seconds',5))))
        self.pressure=ResourcePressure(float(config.get('cpu_soft_percent',50)),
            float(config.get('cpu_critical_percent',70)),
            float(config.get('threshold_percent',60)),
            float(config.get('memory_critical_percent',80)))
        self._last_level=None
        self.guard=Guard(Policy(float(config.get('threshold_percent',60)),
                               float(config.get('cooldown_seconds',300)),
                               float(config.get('periodic_seconds',900))))
        self._task=None
        self._stop=asyncio.Event()
        self._last_reclaim=None
        self.path=StarTools.get_data_dir('astrbot_plugin_whale_memory_guard')/'status.json'
        self.network=NetworkObserver(self.path.with_name('network.sqlite'))
        self._last_network=0.

    async def initialize(self):
        if self.config.get('enabled',True) and (self._task is None or self._task.done()):
            self._stop.clear()
            self._task=asyncio.create_task(self._run(),name='memory-guard')

    async def _run(self):
        while not self._stop.is_set():
            try:
                sample=await asyncio.to_thread(memory_sample)
                pressure=self.pressure.update(sample)
                # Publish admission protection before any optional reclamation.
                await asyncio.to_thread(write_status,self.path.with_name('pressure.json'),
                    dict(at=time.time(),**pressure))
                if self.config.get('network_monitor_enabled',True) and time.monotonic()-self._last_network>=30:
                    manager=getattr(self.context,'platform_manager',None)
                    get_stats=getattr(manager,'get_all_stats',None)
                    stats=get_stats() if callable(get_stats) else {'platforms':[]}
                    network_row=await asyncio.to_thread(self.network.sample,stats)
                    await asyncio.to_thread(self.network.persist,network_row)
                    await asyncio.to_thread(write_status,self.path.with_name('network-status.json'),network_row)
                    self._last_network=time.monotonic()
                if pressure['level'] != self._last_level:
                    helper=str(self.config.get('linux_pressure_helper','')).strip()
                    if helper:
                        if helper != '/usr/local/libexec/whale-resource-pressure':
                            raise ValueError('unrecognized pressure helper')
                        result_helper=await asyncio.to_thread(subprocess.run,
                            ['sudo','-n',helper,pressure['level']],
                            capture_output=True,timeout=5,check=False)
                        if result_helper.returncode:
                            raise RuntimeError('pressure helper failed')
                    self._last_level=pressure['level']
                    logger.info('资源保护级别: %s',pressure['level'])
                # CPU-only pressure must not trigger another costly GC sweep.
                if pressure['level'] != 'normal' and not pressure['memory_reclaim']:
                    result=dict(at=time.time(),reason='cpu_backpressure',before=sample)
                else:
                    result=await asyncio.to_thread(self.guard.tick)
                if result['reason'] in ('periodic','pressure'):
                    self._last_reclaim=result
                    logger.info('内存回收完成，释放 %s 字节，耗时 %s 毫秒',
                                result['rss_released_bytes'],result['duration_ms'])
                await asyncio.to_thread(write_status,self.path,
                                        dict(latest=result,last_reclaim=self._last_reclaim,
                                             pressure=pressure))
            except Exception as error:
                logger.warning('内存守护本轮跳过，%s',type(error).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(),timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def terminate(self):
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task=None
