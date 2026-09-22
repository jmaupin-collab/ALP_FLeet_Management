"""Test fixtures and configuration."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh in-memory database for each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
