from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,Boolean,UniqueConstraint,Date
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class Claim(Base):
 __tablename__='claims'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);amount=Column(Float);distance=Column(Float);holiday=Column(Boolean);label=Column(Integer,nullable=True);note=Column(Text,default='');claimed_on=Column(Date,nullable=True)
class Training(Base):
 __tablename__='trainings'
 id=Column(Integer,primary_key=True);name=Column(String(100));threshold=Column(Float);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='');source=Column(String(10),default='manual');trigger_id=Column(Integer,nullable=True)
class Review(Base):
 __tablename__='reviews'
 __table_args__=(UniqueConstraint('training_id','claim_id',name='uq_review_source'),)
 id=Column(Integer,primary_key=True);training_id=Column(Integer,ForeignKey('trainings.id'));claim_id=Column(Integer,ForeignKey('claims.id'));score=Column(Float);decision=Column(String(30),default='pending');note=Column(Text,default='');status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class DriftWindow(Base):
 __tablename__='drift_windows'
 id=Column(Integer,primary_key=True);window_end=Column(Date,unique=True,nullable=False);baseline_training_id=Column(Integer,ForeignKey('trainings.id'),nullable=True);state=Column(String(20),default='pending');alert=Column(Boolean,default=False);revision=Column(Integer,default=0);pending_reason=Column(String(30),default='scheduled');metrics=Column(JSON,nullable=True);error=Column(Text,default='');updated=Column(String(40),default='')
class DriftRun(Base):
 __tablename__='drift_runs'
 __table_args__=(UniqueConstraint('window_id','revision',name='uq_run_revision'),)
 id=Column(Integer,primary_key=True);window_id=Column(Integer,ForeignKey('drift_windows.id'));revision=Column(Integer);reason=Column(String(30),default='scheduled');baseline_training_id=Column(Integer,nullable=True);alert=Column(Boolean,default=False);metrics=Column(JSON,nullable=True);error=Column(Text,default='');created=Column(String(40))
class DriftTrigger(Base):
 __tablename__='drift_triggers'
 id=Column(Integer,primary_key=True);training_id=Column(Integer,ForeignKey('trainings.id'),nullable=True);state=Column(String(20),default='fired');round_open=Column(Boolean,default=True);basis_window_ids=Column(JSON);streak_start=Column(Date,nullable=True);baseline_training_id=Column(Integer,nullable=True);cancel_reason=Column(Text,default='');created=Column(String(40));updated=Column(String(40),default='')
class DriftState(Base):
 __tablename__='drift_state'
 id=Column(Integer,primary_key=True)
