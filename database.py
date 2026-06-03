# database.py
from sqlmodel import create_engine, SQLModel

# Base de datos de catálogo y productos
DATABASE_URL = "sqlite:///tienda.db"
engine = create_engine(DATABASE_URL, echo=True)

# Base de datos independiente para el registro de compras
COMPRAS_DATABASE_URL = "sqlite:///compras.db"
engine_compras = create_engine(COMPRAS_DATABASE_URL, echo=True)

def create_db_and_tables():
    import models  # Nos aseguramos de registrar los modelos en la metadata
    
    # Inicializa las tablas correspondientes en cada base de datos
    SQLModel.metadata.create_all(engine)
    SQLModel.metadata.create_all(engine_compras)