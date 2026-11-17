from datetime import date,datetime,timedelta,timezone
from sqlalchemy import func
from src.models.entities import Claim,Training,DriftWindow,DriftRun,DriftTrigger,DriftState
from src.services.business import dump
# 漂移判定口径（可解释、可复算）：
# 基线为窗口首次计算时锁定的最新已完成训练快照；窗口为截至 window_end 的连续 7 天。
# 金额/里程比较均值相对变化 |(窗口均值-基线均值)/基线均值|，节假日比较占比差绝对值。
# 任一指标越限即告警；窗口内报销不足 MIN_WINDOW_SAMPLES 笔时不判定漂移，避免小样本噪声。
WINDOW_DAYS=7
MIN_WINDOW_SAMPLES=5
THRESHOLDS={'amount_change':0.35,'distance_change':0.35,'holiday_change':0.20}
def now():return datetime.now(timezone.utc).isoformat()
def distribution(rows):
 n=len(rows)
 return {'count':n,'amount_mean':round(sum(r['amount'] for r in rows)/n,4),'distance_mean':round(sum(r['distance'] for r in rows)/n,4),'holiday_rate':round(sum(1 for r in rows if r['holiday'])/n,4)}
def compare(baseline,current):
 def rel(c,b):return round((c-b)/b,4) if b else 0.0
 changes={'amount_change':rel(current['amount_mean'],baseline['amount_mean']),'distance_change':rel(current['distance_mean'],baseline['distance_mean']),'holiday_change':round(current['holiday_rate']-baseline['holiday_rate'],4)}
 breaches=[k for k,v in changes.items() if abs(v)>=THRESHOLDS[k]]
 return changes,breaches
def lock_state(s):
 # 单行互斥锁：串行化“评估触发”与“发布重训”，避免并发双触发或取消/发布竞争。
 st=s.query(DriftState).filter(DriftState.id==1).with_for_update().first()
 if not st:st=DriftState(id=1);s.add(st);s.flush()
 return st
def compute_window(s,window,reason):
 # 窗口行加锁，串行化同一窗口的并发重算（MySQL 生效；SQLite 单进程测试忽略）。
 w=s.query(DriftWindow).filter(DriftWindow.id==window.id).with_for_update().first()
 start=w.window_end-timedelta(days=WINDOW_DAYS-1)
 rows=[dump(r) for r in s.query(Claim).filter(Claim.claimed_on>=start,Claim.claimed_on<=w.window_end).order_by(Claim.id).all()]
 ts=now()
 try:
  if w.baseline_training_id:baseline=s.get(Training,w.baseline_training_id)
  else:baseline=s.query(Training).filter(Training.state=='done').order_by(Training.id.desc()).first()
  snapshot=(baseline.result or {}).get('snapshot') if baseline else None
  base={'window':{'start':start.isoformat(),'end':w.window_end.isoformat()},'thresholds':dict(THRESHOLDS),'min_samples':MIN_WINDOW_SAMPLES,'method':'金额/里程为窗口均值相对基线均值的变化率，节假日为占比差；任一指标绝对值达到门槛即告警'}
  if not baseline or not snapshot:
   metrics={**base,'baseline':None,'current':distribution(rows) if rows else None,'alert':False,'note':'暂无已完成的训练版本作为基线，仅记录窗口分布'}
   alert=False;baseline_id=None
  else:
   bd=distribution(snapshot)
   if len(rows)<MIN_WINDOW_SAMPLES:
    metrics={**base,'baseline':{'training_id':baseline.id,'training_name':baseline.name,**bd},'current':distribution(rows) if rows else None,'alert':False,'note':f'窗口内报销不足{MIN_WINDOW_SAMPLES}笔，样本不足不判定漂移'}
    alert=False
   else:
    cd=distribution(rows);changes,breaches=compare(bd,cd)
    alert=bool(breaches)
    metrics={**base,'baseline':{'training_id':baseline.id,'training_name':baseline.name,**bd},'current':cd,'changes':changes,'breaches':breaches,'alert':alert,'note':'任一指标越限即告警' if alert else '各指标均在门槛内'}
   baseline_id=baseline.id
  w.revision+=1
  s.add(DriftRun(window_id=w.id,revision=w.revision,reason=reason,baseline_training_id=baseline_id,alert=alert,metrics=metrics,created=ts))
  w.state='done';w.alert=alert;w.metrics=metrics;w.error=''
  if baseline_id and not w.baseline_training_id:w.baseline_training_id=baseline_id
 except Exception as e:
  w.revision+=1
  s.add(DriftRun(window_id=w.id,revision=w.revision,reason=reason,baseline_training_id=w.baseline_training_id,alert=False,metrics=None,error=str(e),created=ts))
  w.state='failed';w.error=str(e)
 w.updated=ts
 return w
def evaluate(s):
 """窗口重算后统一评估：失效取消→恢复闭环→连续三窗触发。返回新建重训ID（调用方提交后入队）。"""
 lock_state(s)
 windows=s.query(DriftWindow).order_by(DriftWindow.window_end).all()
 by_id={w.id:w for w in windows}
 ts=now();created=[]
 # 1) 触发依据失效：任一依据窗口不再处于告警，取消未发布的重训；已完成的历史训练不变。
 for t in s.query(DriftTrigger).filter(DriftTrigger.state=='fired').all():
  if any(not(by_id.get(wid)and by_id[wid].state=='done'and by_id[wid].alert) for wid in (t.basis_window_ids or [])):
   t.state='cancelled';t.round_open=False;t.cancel_reason='依据窗口经更正或重算后不再全部告警';t.updated=ts
   s.query(Training).filter(Training.id==t.training_id,Training.state.in_(['queued','running'])).update({'state':'cancelled','error':'漂移触发依据失效，停止发布结果'},synchronize_session=False)
 # 2) 最新已完成窗口恢复正常：关闭开放轮次，之后才可开启新一轮。
 done=[w for w in windows if w.state=='done']
 streak=[]
 if done:
  latest=done[-1]
  if not latest.alert:
   for t in s.query(DriftTrigger).filter(DriftTrigger.round_open==True).all():t.round_open=False;t.updated=ts
  else:
   by_date={w.window_end:w for w in windows};d=latest.window_end
   while True:
    w=by_date.get(d)
    if not w or w.state!='done' or not w.alert:break
    streak.append(w);d-=timedelta(days=1)
 # 3) 连续三窗告警且无开放轮次：触发一次自动重训；持续告警不重复触发。
 open_round=any(t.round_open for t in s.query(DriftTrigger).all())
 if len(streak)>=3 and not open_round:
  basis=streak[:3];ts=now()
  trigger=DriftTrigger(state='fired',round_open=True,basis_window_ids=[w.id for w in basis],streak_start=streak[-1].window_end,baseline_training_id=basis[0].baseline_training_id,created=ts,updated=ts)
  s.add(trigger);s.flush()
  base=s.get(Training,trigger.baseline_training_id) if trigger.baseline_training_id else None
  rows=[dump(r) for r in s.query(Claim).order_by(Claim.id).all()]
  training=Training(name=f'漂移自动重训 · 触发#{trigger.id}',threshold=base.threshold if base else .5,state='queued',source='auto',trigger_id=trigger.id,result={'snapshot':rows})
  s.add(training);s.flush();trigger.training_id=training.id;created.append(training.id)
 return created
def ensure_windows(s,today=None):
 today=today or date.today()
 first=s.query(func.min(Claim.claimed_on)).scalar()
 if not first:return []
 existing={w.window_end for w in s.query(DriftWindow).all()}
 created=[];d=first
 while d<=today:
  if d not in existing:
   w=DriftWindow(window_end=d,state='pending',pending_reason='scheduled',updated=now());s.add(w);created.append(w)
  d+=timedelta(days=1)
 if created:s.flush()
 return created
def process_pending(s):
 pending=s.query(DriftWindow).filter(DriftWindow.state=='pending').order_by(DriftWindow.window_end).all()
 for w in pending:compute_window(s,w,w.pending_reason or 'scheduled')
 return evaluate(s) if pending else []
def recompute_for_dates(s,dates,reason):
 """迟到记录或更正：受影响窗口（覆盖该日期的全部七日窗口）同步重算并评估，保证取消先于重训发布生效。"""
 dates={d for d in dates if d}
 if not dates:return []
 ensure_windows(s)
 affected={}
 for d in dates:
  for w in s.query(DriftWindow).filter(DriftWindow.window_end>=d,DriftWindow.window_end<=d+timedelta(days=WINDOW_DAYS-1)).all():
   w.state='pending';w.pending_reason=reason;affected[w.id]=w
 s.flush()
 for w in affected.values():compute_window(s,w,reason)
 return evaluate(s)
def baseline_ready(s):
 # 新训练完成后，此前无基线而跳过的窗口重新待算，以新基线比对。
 return s.query(DriftWindow).filter(DriftWindow.baseline_training_id==None,DriftWindow.state=='done').update({'state':'pending','pending_reason':'baseline-ready'},synchronize_session=False)
