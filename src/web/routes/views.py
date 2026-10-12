from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlmodel import Session, select
from src.web.db.session import get_session
from src.web.db.models_db import TrackedOrphanModel, DriftLogModel

router = APIRouter(tags=["Web UI Views"])

# تحديد مسار مجلد الـ templates
templates_path = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

@router.get("/")
def render_dashboard(request: Request, session: Session = Depends(get_session)):
    """عرض صفحة لوحة التحكم وجلب عدد الحاويات المعزولة وسجلات الانحراف من قاعدة البيانات"""
    # استعلام جلب عدد الحاويات المعزولة
    orphans_count = len(session.exec(select(TrackedOrphanModel)).all())
    
    # استعلام جلب عدد الانحرافات الأمنية
    drifts_count = len(session.exec(select(DriftLogModel)).all())

    return templates.TemplateResponse(
        request, 
        "dashboard.html", 
        {
            "orphans_count": orphans_count,
            "drifts_count": drifts_count
        }
    )