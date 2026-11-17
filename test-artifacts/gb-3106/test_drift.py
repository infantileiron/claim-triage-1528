"""漂移监测与自动重训的功能、状态机与并发竞争测试。

口径约定（与 src/services/drift.py 一致）：
- 历史样本 165 笔（5 笔/天，8 天前起共 33 天），金额 1000、非节假日、带标签；
- 漂移样本 40 笔（5 笔/天，最近 8 天），金额 5000、节假日、待标注；
- 基线均值 = (165*1000+40*5000)/205 ≈ 1780.49，节假日占比 = 40/205 ≈ 0.195；
- 窗口结束日 k 天前覆盖偏移 [k, k+6]：k=0..5 告警，k=6 起恢复正常。
"""
import time,threading
from datetime import timedelta
import pytest
from conftest import (TODAY,day,clean,users,analyst,add_claim,populate,make_baseline,run_pending,get,all_,Session,FakeRedis)
from src.models.entities import Claim,Training,DriftWindow,DriftRun,DriftTrigger
from src.services.drift import THRESHOLDS,recompute_for_dates,evaluate
BASE_MEAN=(165*1000+40*5000)/205
def setup_trigger():
 """构造连续告警并触发一次自动重训，返回 (基线ID, 触发ID, 重训ID)。"""
 populate();base=make_baseline();created=run_pending()
 assert len(created)==1,'连续三窗告警应恰好触发一次自动重训'
 t=all_(DriftTrigger);assert len(t)==1 and t[0].state=='fired' and t[0].round_open
 tr=get(Training,id=created[0])
 assert tr.source=='auto' and tr.state=='queued' and tr.threshold==0.5 and tr.trigger_id==t[0].id
 return base,t[0].id,tr.id
def correct_drift_to_baseline():
 """把全部漂移样本更正为基线均值水平，使触发依据失效。"""
 with Session() as s:
  for c in s.query(Claim).filter(Claim.code.like('D-%')).all():c.amount=BASE_MEAN;c.holiday=False
  created=recompute_for_dates(s,{day(o) for o in range(8)},'correction');s.commit();return created
# ---------- 补录报销日期 ----------
def test_claim_requires_claimed_on(client,analyst):
 body={'code':'T-NO-DATE','amount':100,'distance':10,'holiday':False,'label':None,'note':'缺日期'}
 r=client.post('/claims',json=body,headers=analyst);assert r.status_code==422,'缺少报销日期应 422'
 body['claimed_on']=(TODAY+timedelta(days=1)).isoformat()
 r=client.post('/claims',json=body,headers=analyst);assert r.status_code==422 and '不能晚于今天' in str(r.json()),'未来日期应 422'
 body['claimed_on']=TODAY.isoformat()
 r=client.post('/claims',json=body,headers=analyst);assert r.status_code==200 and r.json()['claimed_on']==TODAY.isoformat()
def test_claim_window_recompute_on_create(client,analyst):
 populate();make_baseline();run_pending()
 w=get(DriftWindow,window_end=day(0));rev=w.revision
 r=client.post('/claims',json={'code':'T-TODAY','amount':1000,'distance':10,'holiday':False,'label':None,'note':'','claimed_on':day(0).isoformat()},headers=analyst)
 assert r.status_code==200
 w=get(DriftWindow,window_end=day(0));assert w.revision==rev+1,'新记录落入窗口应重算'
# ---------- 窗口计算可复算 ----------
def test_window_metrics_recomputable(clean):
 populate();base=make_baseline();run_pending()
 w=get(DriftWindow,window_end=day(0))
 assert w.state=='done' and w.alert and w.baseline_training_id==base
 m=w.metrics
 # 独立复算：窗口内样本与基线快照的分布与变化率
 with Session() as s:
  rows=[dict(amount=c.amount,distance=c.distance,holiday=c.holiday) for c in s.query(Claim).filter(Claim.claimed_on>=day(6),Claim.claimed_on<=day(0)).all()]
  snap=get(Training,id=base).result['snapshot']
 b_mean=sum(r['amount'] for r in snap)/len(snap);w_mean=sum(r['amount'] for r in rows)/len(rows)
 assert m['current']['count']==len(rows)==35
 assert abs(m['baseline']['amount_mean']-b_mean)<1e-3 and abs(m['current']['amount_mean']-w_mean)<1e-3
 assert abs(m['changes']['amount_change']-round((w_mean-b_mean)/b_mean,4))<1e-6
 assert m['changes']['amount_change']>THRESHOLDS['amount_change'],'金额变化应越限'
 assert m['thresholds']==THRESHOLDS and m['min_samples']==5,'门槛随结果保存，可复算'
 assert set(m['breaches'])>={'amount_change','holiday_change'} and m['alert']
 # 正常窗口不告警
 w6=get(DriftWindow,window_end=day(6));assert w6.state=='done' and not w6.alert
def test_window_without_baseline_then_ready(clean):
 populate();run_pending()
 w=get(DriftWindow,window_end=day(0))
 assert w.state=='done' and not w.alert and w.baseline_training_id is None and '暂无已完成的训练版本' in w.metrics['note']
 from src.services.drift import baseline_ready
 make_baseline()
 with Session() as s:baseline_ready(s);s.commit()
 run_pending()
 w=get(DriftWindow,window_end=day(0))
 assert w.baseline_training_id is not None and w.alert,'基线就绪后应以新基线重算并告警'
# ---------- 触发状态机 ----------
def test_three_consecutive_alerts_trigger_once(clean):
 base,tid,trid=setup_trigger()
 t=get(DriftTrigger,id=tid)
 assert len(t.basis_window_ids)==3,'依据为连续三个告警窗口'
 ends=[get(DriftWindow,id=i).window_end for i in t.basis_window_ids]
 assert (ends[0]-ends[1]).days==1 and (ends[1]-ends[2]).days==1,'依据窗口须为连续三日'
 assert run_pending()==[],'持续告警不重复触发'
 assert len(all_(DriftTrigger))==1
def test_recovery_then_new_round(clean):
 setup_trigger()
 # 恢复正常：最新已完成窗口不告警，轮次闭环
 with Session() as s:
  s.add(DriftWindow(window_end=TODAY+timedelta(days=1),state='done',alert=False,revision=1,pending_reason='scheduled',metrics={'note':'人工置正常'},updated=''));s.commit()
  assert evaluate(s)==[];s.commit()
 assert not get(DriftTrigger,id=1).round_open,'恢复正常后轮次闭环'
 # 新一轮连续三窗告警
 with Session() as s:
  for k in (2,3,4):s.add(DriftWindow(window_end=TODAY+timedelta(days=k),state='done',alert=True,revision=1,pending_reason='scheduled',metrics={},updated=''))
  s.commit();created=evaluate(s);s.commit()
 assert len(created)==1,'恢复后可开启新一轮并再次触发'
 assert len(all_(DriftTrigger))==2
# ---------- 更正使触发依据失效 ----------
def test_correction_invalidates_queued_retrain(clean):
 base,tid,trid=setup_trigger()
 correct_drift_to_baseline()
 t=get(DriftTrigger,id=tid);tr=get(Training,id=trid)
 assert t.state=='cancelled' and not t.round_open and t.cancel_reason
 assert tr.state=='cancelled' and '停止发布' in tr.error,'已排队的重训应停止发布'
 w=get(DriftWindow,window_end=day(0))
 assert not w.alert and w.revision>=2,'更正后窗口重算为正常'
 runs=[r for r in all_(DriftRun) if r.window_id==w.id]
 assert any(r.alert for r in runs) and any(not r.alert for r in runs),'旧告警记录仍可追溯'
def test_correction_invalidates_running_retrain(clean):
 base,tid,trid=setup_trigger()
 with Session() as s:
  s.query(Training).filter(Training.id==trid).update({'state':'running'});s.commit()
 correct_drift_to_baseline()
 tr=get(Training,id=trid)
 assert tr.state=='cancelled','运行中的重训也应停止发布'
 # 模拟 worker 完成计算后的条件发布：不得生效
 with Session() as s:
  published=s.query(Training).filter(Training.id==trid,Training.state=='running').update({'state':'done','result':{'metrics':{}},'error':''},synchronize_session=False)
  s.commit()
 assert published==0 and get(Training,id=trid).state=='cancelled'
def test_race_execute_training_vs_correction(clean,monkeypatch):
 """跨窗更正与重训完成的并发竞争：更正先提交时，重训不得发布。"""
 import src.worker as worker
 base,tid,trid=setup_trigger()
 real_train=worker.train
 def slow_train(snapshot,threshold):time.sleep(0.6);return real_train(snapshot,threshold)
 monkeypatch.setattr(worker,'train',slow_train)
 done=[]
 th=threading.Thread(target=lambda:(worker.execute_training(trid),done.append(1)))
 th.start()
 deadline=time.time()+5
 while get(Training,id=trid).state!='running':
  assert time.time()<deadline,'worker 应进入 running'
  time.sleep(0.05)
 correct_drift_to_baseline()
 th.join(timeout=30);assert done,'worker 应执行完毕'
 tr=get(Training,id=trid);t=get(DriftTrigger,id=tid)
 assert tr.state=='cancelled' and t.state=='cancelled','更正先生效时重训必须放弃发布'
 assert 'metrics' not in (tr.result or {}),'不得发布训练结果'
def test_publish_first_then_correction_keeps_history(clean):
 """重训先发布时，更正不再回滚历史训练。"""
 import src.worker as worker
 base,tid,trid=setup_trigger()
 worker.execute_training(trid)
 tr=get(Training,id=trid)
 assert tr.state=='done' and 'metrics' in tr.result,'重训正常发布'
 assert get(DriftTrigger,id=tid).state=='published'
 correct_drift_to_baseline()
 tr=get(Training,id=trid);t=get(DriftTrigger,id=tid)
 assert tr.state=='done' and t.state=='published','历史训练与已发布触发不受更正影响'
# ---------- 失败重试 ----------
def test_failed_window_retry_no_duplicate_trigger(client,analyst):
 setup_trigger()
 w=get(DriftWindow,window_end=day(0))
 with Session() as s:
  s.query(DriftWindow).filter(DriftWindow.id==w.id).update({'state':'failed','error':'模拟计算失败'});s.commit()
 r=client.post(f'/drift-windows/{w.id}/retry',headers=analyst)
 assert r.status_code==200 and r.json()['state']=='pending'
 run_pending()
 w=get(DriftWindow,window_end=day(0))
 assert w.state=='done' and w.alert,'重试后窗口恢复计算'
 assert len(all_(DriftTrigger))==1,'失败重试不重复触发'
 r=client.post(f'/drift-windows/{w.id}/retry',headers=analyst)
 assert r.status_code==409,'非失败窗口不可重试'
# ---------- 迟到记录与更正 ----------
def test_late_record_recomputes_affected_windows(client,analyst):
 populate();make_baseline();run_pending()
 before={get(DriftWindow,window_end=day(k)).revision for k in range(4)}
 r=client.post('/claims',json={'code':'LATE-1','amount':1000,'distance':100,'holiday':False,'label':None,'note':'迟到','claimed_on':day(3).isoformat()},headers=analyst)
 assert r.status_code==200
 for k in range(4):
  w=get(DriftWindow,window_end=day(k))
  assert w.revision==2,f'覆盖迟到日期的窗口 k={k} 应重算一次'
  runs=sorted([r for r in all_(DriftRun) if r.window_id==w.id],key=lambda r:r.revision)
  assert runs[-1].reason=='late-record' and len(runs)==2,'迟到重算留有历史'
 w4=get(DriftWindow,window_end=day(4));assert w4.revision==1,'未覆盖的窗口不受影响'
 assert len(all_(DriftTrigger))==1,'小额迟到记录不改变告警，不重复触发'
def test_label_only_edit_skips_recompute(client,analyst):
 populate();make_baseline();run_pending()
 c=get(Claim,code='D-0166')
 w=get(DriftWindow,window_end=day(0));rev=w.revision
 r=client.put(f'/claims/{c.id}',json={'code':c.code,'amount':c.amount,'distance':c.distance,'holiday':c.holiday,'label':0,'note':c.note,'claimed_on':c.claimed_on.isoformat()},headers=analyst)
 assert r.status_code==200
 assert get(DriftWindow,window_end=day(0)).revision==rev,'仅改标签不重算窗口'
def test_amount_correction_recomputes(client,analyst):
 populate();make_baseline();run_pending()
 c=get(Claim,code='D-0166')
 w=get(DriftWindow,window_end=day(0));rev=w.revision
 r=client.put(f'/claims/{c.id}',json={'code':c.code,'amount':9000,'distance':c.distance,'holiday':c.holiday,'label':None,'note':'更正金额','claimed_on':c.claimed_on.isoformat()},headers=analyst)
 assert r.status_code==200
 w=get(DriftWindow,window_end=day(0))
 assert w.revision==rev+1,'金额更正应重算受影响窗口'
 runs=sorted([r for r in all_(DriftRun) if r.window_id==w.id],key=lambda r:r.revision)
 assert runs[-1].reason=='correction'
# ---------- API 展示 ----------
def test_drift_api_surface(client,analyst,users):
 base,tid,trid=setup_trigger()
 r=client.get('/drift-windows',headers=analyst);assert r.status_code==200
 ws=r.json();assert ws and ws[0]['window_end']>=ws[-1]['window_end'],'按结束日倒序'
 wid=[w for w in ws if w['alert']][0]['id']
 r=client.get(f'/drift-windows/{wid}',headers=analyst)
 assert r.status_code==200 and set(r.json())=={'window','runs','triggers'}
 assert r.json()['runs'] and r.json()['triggers'][0]['training']['id']==trid,'页面可展示窗口依据及关联重训状态'
 r=client.get('/drift-triggers',headers=analyst)
 assert r.status_code==200 and r.json()[0]['training']['state']=='queued'
 r=client.post('/auth/login',json={'email':'viewer@demo.local','password':'Demo123!'})
 viewer={'Authorization':'Bearer '+r.json()['token']}
 assert client.get('/drift-windows',headers=viewer).status_code==200
 assert client.post(f'/drift-windows/{wid}/retry',headers=viewer).status_code==403,'观察员只读'
# ---------- 种子数据 ----------
def test_seed_demo_dated_and_idempotent(clean):
 from scripts.seed_demo import seed
 seed();seed()
 claims=all_(Claim)
 assert len(claims)==318 and all(c.claimed_on for c in claims),'种子样本全部带报销日期且幂等'
 assert sum(1 for c in claims if c.code.startswith('HX-DRIFT-'))==18
 assert sum(1 for c in claims if c.label is not None)==260
