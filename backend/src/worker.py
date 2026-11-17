import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Training,DriftTrigger
from src.services.analysis import train
from src.services.drift import ensure_windows,process_pending,baseline_ready,lock_state
r=redis.Redis.from_url(os.environ['REDIS_URL'])
ARTIFACTS=os.environ.get('ARTIFACTS_DIR','/artifacts')
def enqueue(tid):
 try:r.rpush('claim:trainings',tid)
 except redis.RedisError:
  with Session() as s:
   t=s.get(Training,tid)
   if t and t.state=='queued':t.state='failed';t.error='队列暂不可用，请稍后重新提交';s.commit()
def recover():
 # 启动恢复：仅 queued/running 重新入队；已取消的自动重训不再恢复。
 with Session() as s:
  for pending in s.query(Training).filter(Training.state.in_(['queued','running'])).all():
   pending.state='queued';r.rpush('claim:trainings',pending.id)
  s.commit()
def tick():
 # 按日滚动：补齐截至今日的窗口，计算待算窗口并评估触发；新建重训提交后入队。
 with Session() as s:
  ensure_windows(s)
  created=process_pending(s)
  s.commit()
 for tid in created:enqueue(tid)
def execute_training(tid):
 # 条件更新保证并发安全：取消优先时放弃发布，历史训练不受影响。
 with Session() as s:
  claimed=s.query(Training).filter(Training.id==tid,Training.state=='queued').update({'state':'running'},synchronize_session=False)
  s.commit()
  if not claimed:return
  row=s.get(Training,tid)
  snapshot=row.result['snapshot'];threshold=row.threshold
  try:result=train(snapshot,threshold)
  except Exception as e:
   s.query(Training).filter(Training.id==tid,Training.state=='running').update({'state':'failed','error':str(e)},synchronize_session=False)
   s.commit();return
  lock_state(s)
  published=s.query(Training).filter(Training.id==tid,Training.state=='running').update({'state':'done','result':{**result,'snapshot':snapshot},'error':''},synchronize_session=False)
  if not published:
   s.rollback();logging.info('训练 %s 已被取消，放弃发布结果',tid);return
  s.query(DriftTrigger).filter(DriftTrigger.training_id==tid,DriftTrigger.state=='fired').update({'state':'published'},synchronize_session=False)
  s.commit()
  Path(ARTIFACTS+'/'+str(tid)+'.json').write_text(json.dumps({**result,'snapshot':snapshot},ensure_ascii=False))
  with Session() as s2:
   baseline_ready(s2);s2.commit()
def main():
 recover()
 while True:
  Path('/tmp/heartbeat').touch()
  try:tick()
  except Exception:logging.exception('drift tick');time.sleep(2)
  try:
   item=r.blpop('claim:trainings',timeout=3)
   if not item:continue
   execute_training(int(item[1]))
  except Exception:logging.exception('worker');time.sleep(2)
if __name__=='__main__':main()
