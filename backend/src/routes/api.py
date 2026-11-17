import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Claim,Training,Review,Audit,DriftWindow,DriftRun,DriftTrigger
from src.schemas.business import Login,ClaimInput,TrainingInput,ReviewInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.drift import recompute_for_dates,now
from src.controllers.business import ensure_training
router=APIRouter()
def enqueue(tid):
 redis.Redis.from_url(os.environ['REDIS_URL']).rpush('claim:trainings',tid)
def enqueue_created(s,ids):
 # 自动重训在事务提交后入队；入队失败标记失败，不重复触发。
 for tid in ids:
  try:enqueue(tid)
  except redis.RedisError:
   t=s.get(Training,tid)
   if t and t.state=='queued':t.state='failed';t.error='队列暂不可用，请稍后重新提交';s.commit()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/claims')
def claims(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Claim).order_by(Claim.id).all()]
@router.post('/claims')
def create_claim(data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=Claim(**data.model_dump());s.add(r)
 try:
  s.flush()
  created=recompute_for_dates(s,[r.claimed_on],'late-record')
  s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 enqueue_created(s,created);audit(s,u,'录入报销 '+r.code);return dump(r)
@router.put('/claims/{i}')
def edit_claim(i:int,data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Claim,i)
 if not r:raise HTTPException(404,'报销样本不存在')
 old=(r.claimed_on,r.amount,r.distance,r.holiday)
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:
  s.flush()
  created=[]
  if old!=(r.claimed_on,r.amount,r.distance,r.holiday):created=recompute_for_dates(s,{old[0],r.claimed_on},'correction')
  s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 enqueue_created(s,created);audit(s,u,'修订报销 '+str(i));return dump(r)
@router.get('/trainings')
def trainings(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Training).order_by(Training.id.desc()).all()]
@router.post('/trainings')
def train(data:TrainingInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Claim).order_by(Claim.id).all()];r=Training(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:enqueue(r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交训练 '+str(r.id));return dump(r)
@router.get('/trainings/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_training(s,i))
@router.get('/trainings/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功训练可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="claim-triage-{i}.json"'})
@router.post('/trainings/{i}/reviews')
def generate(i:int,u=Depends(editor),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'训练完成后才能生成复核任务')
 created=0
 for p in r.result['pending']:
  if p['score']>=r.threshold and not s.query(Review).filter_by(training_id=i,claim_id=p['id']).first():s.add(Review(training_id=i,claim_id=p['id'],score=p['score']));created+=1
 s.commit();audit(s,u,'生成风险复核 '+str(i));return {'created':created}
@router.get('/reviews')
def reviews(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Review).order_by(Review.id.desc()).all()]
@router.put('/reviews/{i}')
def review(i:int,data:ReviewInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核任务不存在')
 if r.status=='approved':raise HTTPException(409,'已签核复核不可修改')
 r.decision=data.decision;r.note=data.note;s.commit();audit(s,u,'人工复核 '+str(i));return dump(r)
@router.post('/reviews/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Review,i)
 if not r:raise HTTPException(404,'复核任务不存在')
 if r.decision=='pending' or r.status!='draft':raise HTTPException(409,'先填写人工判断，且只签核一次')
 r.status='approved';s.commit();audit(s,u,'签核复核 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
@router.get('/drift-windows')
def drift_windows(u=Depends(current),s=Depends(db)):return [dump(w) for w in s.query(DriftWindow).order_by(DriftWindow.window_end.desc()).all()]
@router.get('/drift-windows/{i}')
def drift_window(i:int,u=Depends(current),s=Depends(db)):
 w=s.get(DriftWindow,i)
 if not w:raise HTTPException(404,'监测窗口不存在')
 runs=[dump(r) for r in s.query(DriftRun).filter_by(window_id=i).order_by(DriftRun.revision.desc()).all()]
 triggers=[]
 for t in s.query(DriftTrigger).order_by(DriftTrigger.id.desc()).all():
  if i in (t.basis_window_ids or []):
   d=dump(t);tr=s.get(Training,t.training_id) if t.training_id else None
   d['training']=dump(tr) if tr else None;triggers.append(d)
 return {'window':dump(w),'runs':runs,'triggers':triggers}
@router.post('/drift-windows/{i}/retry')
def retry_window(i:int,u=Depends(editor),s=Depends(db)):
 w=s.get(DriftWindow,i)
 if not w:raise HTTPException(404,'监测窗口不存在')
 if w.state!='failed':raise HTTPException(409,'仅失败的窗口可以重试')
 w.state='pending';w.pending_reason='retry';w.error='';w.updated=now();s.commit()
 audit(s,u,'重试漂移窗口 '+str(i));return dump(w)
@router.get('/drift-triggers')
def drift_triggers(u=Depends(current),s=Depends(db)):
 out=[]
 for t in s.query(DriftTrigger).order_by(DriftTrigger.id.desc()).all():
  d=dump(t);tr=s.get(Training,t.training_id) if t.training_id else None
  d['training']=dump(tr) if tr else None;out.append(d)
 return out
