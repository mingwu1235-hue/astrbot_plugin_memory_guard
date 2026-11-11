import unittest
from unittest.mock import Mock
from pathlib import Path
import tempfile
import json
from guard import Guard,Policy,memory_sample,write_status

def sample(used=30,rss=1000):
 return dict(system_used_percent=used,process_rss_bytes=rss)

class Cases(unittest.TestCase):
 def test_real_platform_sample(self):
  s=memory_sample();self.assertGreater(s['system_total_bytes'],0)
  self.assertGreater(s['process_rss_bytes'],0);self.assertTrue(0<=s['system_used_percent']<=100)
 def test_periodic_below_threshold(self):
  clock=Mock(side_effect=[0,901]);collect=Mock(return_value=4)
  g=Guard(Policy(),sampler=Mock(side_effect=[sample(),sample(rss=800)]),collector=collect,trimmer=lambda:False,clock=clock)
  row=g.tick();self.assertEqual(row['reason'],'periodic');self.assertEqual(row['rss_released_bytes'],200)
  collect.assert_called_once_with(2)
 def test_pressure_immediate_and_cooldown(self):
  clock=Mock(side_effect=[0,1,5,305])
  g=Guard(Policy(),sampler=lambda:sample(70),collector=Mock(return_value=0),trimmer=lambda:False,clock=clock)
  self.assertEqual(g.tick()['reason'],'pressure')
  self.assertEqual(g.tick()['reason'],'cooldown')
  self.assertEqual(g.tick()['reason'],'pressure')
 def test_failure_reserves_cooldown(self):
  g=Guard(Policy(),sampler=lambda:sample(90),collector=Mock(side_effect=RuntimeError),clock=Mock(side_effect=[0,1,3]))
  with self.assertRaises(RuntimeError):g.tick()
  self.assertEqual(g.tick()['reason'],'cooldown')
 def test_no_fake_released_bytes(self):
  g=Guard(Policy(),sampler=Mock(side_effect=[sample(90),sample(90,1100)]),collector=lambda n:0,trimmer=lambda:True,clock=Mock(side_effect=[0,1]))
  self.assertEqual(g.tick()['rss_released_bytes'],0)
 def test_atomic_bounded_status(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'status.json';write_status(p,{'reason':'periodic'})
   self.assertEqual(json.loads(p.read_text()),{'reason':'periodic'})
   with self.assertRaises(ValueError):write_status(p,{'value':'x'*20000})
   self.assertEqual(len(list(Path(d).iterdir())),1)
 def test_invalid_intervals(self):
  for kwargs in ({'periodic':1},{'cooldown':0},{'threshold':float('nan')}):
   with self.assertRaises(ValueError):Policy(**kwargs)
if __name__=='__main__':unittest.main()
