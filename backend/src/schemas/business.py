from pydantic import BaseModel,Field,field_validator
from typing import Optional,Literal
from datetime import date
class Login(BaseModel):email:str;password:str
class ClaimInput(BaseModel):
 code:str=Field(min_length=1,max_length=60)
 amount:float=Field(ge=0,le=100000,allow_inf_nan=False)
 distance:float=Field(ge=0,le=10000,allow_inf_nan=False)
 holiday:bool=False
 label:Optional[Literal[0,1]]=None
 note:str=Field(default='',max_length=1000)
 claimed_on:date
 @field_validator('claimed_on')
 @classmethod
 def not_future(cls,v):
  if v>date.today():raise ValueError('报销日期不能晚于今天')
  return v
class TrainingInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 threshold:float=Field(default=.5,ge=.1,le=.9,allow_inf_nan=False)
class ReviewInput(BaseModel):
 decision:Literal['valid','flagged']
 note:str=Field(min_length=6,max_length=2000)
