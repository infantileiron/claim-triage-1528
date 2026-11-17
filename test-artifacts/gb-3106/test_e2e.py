"""端到端：种子数据 → 手动基线训练 → worker 滚动计算 → 漂移触发 → 自动重训发布 → 新基线生效。"""
import pytest
from conftest import clean,users,analyst,day,run_pending,get,all_,Session,FakeRedis
from src.models.entities import Claim,Training,DriftWindow,DriftTrigger
def pump():
 """模拟 worker 主循环：tick 计算窗口并入队新重训，消费队列直至无新任务。"""
 import src.worker as worker
 fake=FakeRedis.from_url('')
 for _ in range(10):
  worker.tick()
  q=fake.lists.setdefault('claim:trainings',[])
  if not q:break
  while q:worker.execute_training(int(q.pop(0)))
def test_full_demo_day(client,analyst):
 from scripts.seed_demo import seed
 seed()
 # 09:20 创建“11月差旅风险基线”
 r=client.post('/trainings',json={'name':'11月差旅风险基线','threshold':0.5},headers=analyst)
 assert r.status_code==200 and r.json()['state']=='queued'
 pump()
 base=[t for t in all_(Training) if t.source=='manual'][0]
 assert base.state=='done' and 'metrics' in base.result,'手动基线应训练完成'
 # 漂移窗口全部计算，近七日窗口连续告警并触发一次自动重训
 ws=all_(DriftWindow)
 assert ws and all(w.state=='done' for w in ws),'窗口从待计算到完成'
 alerts=[w for w in ws if w.alert]
 assert len(alerts)>=3,'近七日窗口应连续告警'
 triggers=all_(DriftTrigger)
 assert len(triggers)==1 and triggers[0].state=='published'
 auto=get(Training,id=triggers[0].training_id)
 assert auto.source=='auto' and auto.state=='done' and 'metrics' in auto.result,'自动重训发布结果'
 # 自动重训成为后续窗口的新基线；历史窗口仍锁定旧基线
 old=[w for w in ws if w.baseline_training_id==base.id]
 assert old,'历史窗口保留原基线，可追溯'
 # 页面数据齐备
 r=client.get('/drift-triggers',headers=analyst)
 assert r.json()[0]['training']['state']=='done'
 r=client.get('/drift-windows',headers=analyst)
 assert all(w['state']=='done' for w in r.json())
 # 再次 pump 不重复触发
 pump()
 assert len(all_(DriftTrigger))==1,'持续告警不重复触发'
