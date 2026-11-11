"""Passive adapter health and host throughput, without sending test traffic."""
import hashlib
from contextlib import closing
import json
import math
import sqlite3
import time
import psutil


class NetworkObserver:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        self.previous = None

    def sample(self, platform_stats):
        now = self.clock()
        net = psutil.net_io_counters()
        row = dict(at=now, qq=[], host_rx_bytes_per_second=None,
                   host_tx_bytes_per_second=None, qq_rtt_ms=None,
                   qq_rtt_status='NOT_EXPOSED_BY_ADAPTER',
                   throughput_scope='host_all_interfaces_not_qq_link_bandwidth')
        if self.previous and now > self.previous[0]:
            elapsed = now-self.previous[0]
            row['host_rx_bytes_per_second'] = max(0, net.bytes_recv-self.previous[1])/elapsed
            row['host_tx_bytes_per_second'] = max(0, net.bytes_sent-self.previous[2])/elapsed
        self.previous = now, net.bytes_recv, net.bytes_sent
        for stat in platform_stats.get('platforms', []):
            if stat.get('type') not in ('qq_official', 'qqofficial'):
                continue
            item = dict(adapter=stat['type'], status=str(stat.get('status','unknown')),
                        error_count=stat.get('error_count',0))
            # Session ids, messages, credentials and free-form errors are excluded.
            for key in ('received_count','sent_count','received_messages','sent_messages',
                        'message_received','message_sent','reconnect_count'):
                if type(stat.get(key)) in (int,float):item[key]=stat[key]
            row['qq'].append(item)
        row['qq_state_verified'] = bool(row['qq'])
        return row

    def persist(self, row):
        raw=json.dumps(row,separators=(',',':'))
        if len(raw)>8192:raise ValueError('network sample too large')
        with closing(sqlite3.connect(self.path,timeout=1)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (at REAL PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO samples VALUES (?,?)',(row['at'],raw))
            db.execute('DELETE FROM samples WHERE at < ?', (row['at']-48*3600,))
            db.execute('DELETE FROM samples WHERE at NOT IN (SELECT at FROM samples ORDER BY at DESC LIMIT 5760)')
