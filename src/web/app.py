from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.web.db.session import init_db
from src.web.routes import views
# استيراد النماذج لضمان إنشائها في قاعدة البيانات
from src.web.db.models_db import TrackedOrphanModel, DriftLogModel

app = FastAPI(
    title="GitOps-Sentinel Control Plane",
    description="Web Dashboard & Backend Control Plane",
    version="1.0.0"
)

# تفعيل ملفات الـ Static (مثل الـ CSS والـ JS)
app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

# تسجيل مسارات العرض (Views)
app.include_router(views.router)

@app.on_event("startup")
def on_startup():
    """تتم عند إقلاع السيرفر وتنشئ ملف قاعدة البيانات والجداول تلقائياً"""
    init_db()

@app.get("/health", tags=["Web Status"])
def web_health():
    return {
        "status": "healthy",
        "module": "Web Control Plane",
        "database": "Initialized"
    }