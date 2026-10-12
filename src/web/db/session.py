from sqlmodel import SQLModel, create_engine, Session

# مسار قاعدة بيانات SQLite المحلية داخل مجلد المشروع
DATABASE_URL = "sqlite:///sentinel.db"

# إنشاء محرك الاتصال
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def init_db():
    """إنشاء الجداول في قاعدة البيانات عند بدء التشغيل"""
    SQLModel.metadata.create_all(engine)

def get_session():
    """توفير جلسة اتصال (Session) للاستعلامات داخل مسارات الـ API"""
    with Session(engine) as session:
        yield session