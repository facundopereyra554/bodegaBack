# database.py
from sqlmodel import create_engine, SQLModel, Session

DATABASE_URL = "sqlite:///tienda.db"
engine = create_engine(DATABASE_URL, echo=True)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)