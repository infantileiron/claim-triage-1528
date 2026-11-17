import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
url=os.environ['DATABASE_URL']
kw={'pool_pre_ping':True}
# MySQL 使用 READ COMMITTED：漂移重算与触发评估在单行互斥锁内读到对方已提交的最新状态。
if url.startswith('mysql'):kw['isolation_level']='READ COMMITTED'
engine=create_engine(url,**kw)
Session=sessionmaker(bind=engine,expire_on_commit=False)
Base=declarative_base()
def db():
 with Session() as s: yield s
