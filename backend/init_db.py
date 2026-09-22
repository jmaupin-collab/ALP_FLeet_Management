"""Initialize database with schema and seed data"""
import sys
sys.path.insert(0, "/Users/jeffreymaupin/Desktop/ALPR_Fleet_MGMT/backend")

from app.database import Base, engine, SessionLocal
from app.models import *  # Import all models to register them
from app.seed_quick import seed_quick

print("Creating tables...")
Base.metadata.create_all(bind=engine)
print("✓ Tables created")

print("\nSeeding database...")
db = SessionLocal()
try:
    seed_quick(db)
finally:
    db.close()

print("\n✅ Database initialized!")
