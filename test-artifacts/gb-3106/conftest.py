"""漂移监测测试环境：SQLite 文件库 + 假 Redis + FastAPI TestClient。

运行方式（需先安装 backend/requirements.txt 与 pytest）：
    cd test-artifacts/gb-3106 && python3 -m pytest -v
"""
import os,sys,tempfile,pathlib
BACKEND=pathlib.Path(__file__).resolve().parents[2]/'backend'
sys.path.insert(0,str(BACKEND))
TMP=tempfile.mkdtemp(prefix='drift-test-')
os.environ['DATABASE_URL']=f"sqlite:///{TMP}/test.db"
os.environ['REDIS_URL']='redis://invalid:6379/15'
os.environ['JWT_SECRET']='drift-test-secret'
os.environ['SEED_DEMO']='false'
os.environ['ARTIFACTS_DIR']=TMP
import redis
class FakeRedis:
 """进程内假队列：验证入队次数与内容，不依赖真实 Redis。"""
 _one=None
 def __init__(self):self.lists={}
 @classmethod
 def from_url(cls,url):
  if cls._one is None:cls._one=cls()
  return cls._one
 def rpush(self,key,val):self.lists.setdefault(key,[]).append(str(val))
 def blpop(self,key,timeout=0):
  lst=self.lists.get(key) or []
  return (key,lst.pop(0)) if lst else None
 def ping(self):return True
redis.Redis.from_url=FakeRedis.from_url
import pytest
from datetime import date,timedelta
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.entities import User,Claim,Training,DriftWindow,DriftRun,DriftTrigger
from src.services.business import dump
from src.utils.security import hash_password
from src.main import app
Base.metadata.create_all(engine)
with Session() as _s:
 from src.models.entities import DriftState
 if not _s.get(DriftState,1):_s.add(DriftState(id=1));_s.commit()
TODAY=date.today()
def day(off):return TODAY-timedelta(days=off)
@pytest.fixture(scope='session')
def client():
 with TestClient(app) as c:yield c
@pytest.fixture()
def clean():
 from src.models.entities import DriftState
 with Session() as s:
  for t in reversed(Base.metadata.sorted_tables):s.execute(t.delete())
  s.add(DriftState(id=1));s.commit()
 FakeRedis.from_url('').lists.clear()
 yield
@pytest.fixture()
def users(clean):
 with Session() as s:
  for role in ('admin','analyst','viewer'):s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
  s.commit()
 return True
@pytest.fixture()
def analyst(client,users):
 r=client.post('/auth/login',json={'email':'analyst@demo.local','password':'Demo123!'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}
def add_claim(code,amount,distance,holiday,claimed_on,label=None,note=''):
 with Session() as s:
  s.add(Claim(code=code,amount=amount,distance=distance,holiday=holiday,label=label,claimed_on=claimed_on,note=note));s.commit()
def populate(hist_days=33,drift_days=8,per_day=5,hist_amount=1000.0,drift_amount=5000.0):
 """历史样本截至 8 天前（带标签可训练），近 8 天为高金额漂移样本（待标注）。"""
 n=0
 with Session() as s:
  for d in range(8,8+hist_days):
   for k in range(per_day):
    n+=1;s.add(Claim(code=f'H-{n:04}',amount=hist_amount,distance=100.0,holiday=False,label=1 if n%7==0 else 0,claimed_on=day(d),note='历史'))
  for d in range(drift_days):
   for k in range(per_day):
    n+=1;s.add(Claim(code=f'D-{n:04}',amount=drift_amount,distance=100.0,holiday=True,label=None,claimed_on=day(d),note='漂移'))
  s.commit()
 return n
def make_baseline(threshold=0.5,name='测试基线'):
 with Session() as s:
  rows=[dump(r) for r in s.query(Claim).order_by(Claim.id).all()]
  t=Training(name=name,threshold=threshold,state='done',source='manual',result={'snapshot':rows})
  s.add(t);s.commit();return t.id
def run_pending():
 """模拟 worker tick：补窗口、算待算、评估触发；返回新建重训ID。"""
 from src.services.drift import ensure_windows,process_pending
 with Session() as s:
  ensure_windows(s);created=process_pending(s);s.commit();return created
def get(model,**kw):
 with Session() as s:return s.query(model).filter_by(**kw).first()
def all_(model):
 with Session() as s:return s.query(model).all()
