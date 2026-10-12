from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field

class TrackedOrphanModel(SQLModel, table=True):
    """جدول لتخزين الحاويات المعزولة وتتبع حالة الـ State Buffer"""
    __tablename__ = "tracked_orphans"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    container_id: str
    container_name: str
    status: str = "ADOPT_PENDING_DEPLOYMENT"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class DriftLogModel(SQLModel, table=True):
    """جدول لتخزين سجلات الانحرافات والأخطاء الأمنية"""
    __tablename__ = "drift_logs"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    container_name: str
    event_type: str
    details: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)