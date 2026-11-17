from datetime import datetime,date,timezone
from src.models.entities import Audit
def audit(s,u,action):
 s.add(Audit(actor=u.email,action=action,created=datetime.now(timezone.utc).isoformat()));s.commit()
def dump(obj):
 out={}
 for c in obj.__table__.columns:
  if c.name=='password':continue
  v=getattr(obj,c.name)
  if isinstance(v,(date,datetime)):v=v.isoformat()
  out[c.name]=v
 return out
