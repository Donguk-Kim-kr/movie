'''
database/db_connection.py - PostgreSQL 연결 + 세션 의존성
'''
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = st.secrets["DATABASE_URL"]

@st.cache_resource
def get_engine():
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
    )

engine = get_engine()

SessionFactory = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)

def get_session():
    session = SessionFactory()
    try:
        yield session

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()
