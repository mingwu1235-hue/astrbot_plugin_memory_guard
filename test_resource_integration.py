import json
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from network import NetworkObserver
from resource_pressure import should_defer_background
import linux_pressure_helper

class Integration(unittest.TestCase):
    def test_gate_expiry_and_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'pressure.json'
            self.assertFalse(should_defer_background(p,100))
            p.write_text(json.dumps({'at':95,'defer_background':True}))
            self.assertTrue(should_defer_background(p,100))
            self.assertFalse(should_defer_background(p,140))
            self.assertFalse(should_defer_background(p,90))
            p.write_text('invalid');self.assertFalse(should_defer_background(p,100))
    def test_helper_only_scoped_quota(self):
        for level,quota in [('normal','160%'),('soft','100%'),('critical','50%')]:
            with tempfile.TemporaryDirectory() as d, patch('sys.argv',['helper',level]),patch('subprocess.run') as run, patch.object(linux_pressure_helper,'Path',lambda _:Path(d)):
                linux_pressure_helper.main()
                command=run.call_args.args[0]
                self.assertEqual(command[-2:],[ 'whale-rvc-fast.service','CPUQuota='+quota])
                self.assertIn('--runtime',command)
        with patch('sys.argv',['helper','reboot']),patch('subprocess.run') as run:
            with self.assertRaises(SystemExit):linux_pressure_helper.main()
            run.assert_not_called()
    def test_passive_network_redaction_counter_reset_and_bound(self):
        with tempfile.TemporaryDirectory() as d:
            observer=NetworkObserver(Path(d)/'net.sqlite',clock=iter([100,130,160]).__next__)
            stats={'platforms':[dict(type='qq_official',status='running',id='PRIVATE',last_error='PRIVATE',received_count=4)]}
            with patch('psutil.net_io_counters',side_effect=[SimpleNamespace(bytes_recv=100,bytes_sent=200),SimpleNamespace(bytes_recv=400,bytes_sent=800),SimpleNamespace(bytes_recv=0,bytes_sent=0)]):
                first=observer.sample(stats);second=observer.sample(stats);third=observer.sample(stats)
            self.assertIsNone(first['host_rx_bytes_per_second'])
            self.assertEqual(second['host_rx_bytes_per_second'],10)
            self.assertEqual(third['host_rx_bytes_per_second'],0)
            self.assertNotIn('PRIVATE',json.dumps(second))
            self.assertIsNone(second['qq_rtt_ms'])
            observer.persist(first);observer.persist(second)
            import sqlite3
            with closing(sqlite3.connect(observer.path)) as db:self.assertEqual(db.execute('select count(*) from samples').fetchone()[0],2)

if __name__=='__main__':unittest.main()
