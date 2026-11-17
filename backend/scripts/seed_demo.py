import random,math
from datetime import date,timedelta
from src.config.database import Base,engine,Session
from src.models.entities import User,Claim
def seed():
 from src.utils.security import hash_password
 Base.metadata.create_all(engine);rng=random.Random(3106);today=date.today()
 with Session() as s:
  for role in ('admin','analyst','viewer'):
   email=role+'@demo.local'
   if not s.query(User).filter_by(email=email).first():s.add(User(email=email,role=role,password=hash_password('Demo123!')))
  for i in range(300):
   code=f'HX-1115-{i+1:03}';distance=round(rng.uniform(5,800),1);holiday=rng.random()<.25;amount=round(distance*rng.uniform(1.2,5.5)+rng.uniform(50,900),2)
   risk=-4.7+amount/700+1.1*holiday+amount/(distance+1)/8;label=int(rng.random()<1/(1+math.exp(-risk))) if i<260 else None
   claimed_on=today-timedelta(days=8+int(rng.uniform(0,61)))
   if not s.query(Claim).filter_by(code=code).first():s.add(Claim(code=code,amount=amount,distance=distance,holiday=holiday,label=label,claimed_on=claimed_on,note='历史人工复核标签；仅用于模拟训练，不连接真实报销'))
  # 近7日漂移样本：金额、里程与节假日占比显著高于历史，全部待标注，用于演示漂移监测与自动重训。
  for j,off in enumerate([0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,7,7]):
   code=f'HX-DRIFT-{j+1:03}';distance=round(rng.uniform(50,1200),1);holiday=rng.random()<.75;amount=round(distance*rng.uniform(6,10)+rng.uniform(800,2500),2)
   if not s.query(Claim).filter_by(code=code).first():s.add(Claim(code=code,amount=amount,distance=distance,holiday=holiday,label=None,claimed_on=today-timedelta(days=off),note='近期待复核票据；分布偏离历史基线，用于演示漂移监测'))
  s.commit();print({'users':s.query(User).count(),'claims':s.query(Claim).count()})
if __name__=='__main__':seed()
