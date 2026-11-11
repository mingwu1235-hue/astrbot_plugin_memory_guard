from __future__ import annotations

import asyncio
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
from .guard import Guard, Policy, write_status


class WhaleMemoryGuard(Star):
    def __init__(self,context: Context,config: dict):
        super().__init__(context)
        self.config=config
        self.interval=max(15,min(300,int(config.get('interval_seconds',30))))
        self.guard=Guard(Policy(float(config.get('threshold_percent',60)),
                               float(config.get('cooldown_seconds',300)),
                               float(config.get('periodic_seconds',900))))
        self._task=None
        self._stop=asyncio.Event()
        self._last_reclaim=None
        self.path=StarTools.get_data_dir('astrbot_plugin_whale_memory_guard')/'status.json'

    async def initialize(self):
        if self.config.get('enabled',True) and (self._task is None or self._task.done()):
            self._stop.clear()
            self._task=asyncio.create_task(self._run(),name='memory-guard')

    async def _run(self):
        while not self._stop.is_set():
            try:
                result=await asyncio.to_thread(self.guard.tick)
                if result['reason'] in ('periodic','pressure'):
                    self._last_reclaim=result
                    logger.info('内存回收完成，释放 %s 字节，耗时 %s 毫秒',
                                result['rss_released_bytes'],result['duration_ms'])
                await asyncio.to_thread(write_status,self.path,
                                        dict(latest=result,last_reclaim=self._last_reclaim))
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
