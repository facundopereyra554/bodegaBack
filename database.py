# database.py
from sqlmodel import create_engine, SQLModel

# Base de datos de catálogo y productos
DATABASE_URL = "sqlite:///tienda.db"
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False, "timeout": 15})

# Base de datos independiente para el registro de compras
COMPRAS_DATABASE_URL = "sqlite:///compras.db"
engine_compras = create_engine(COMPRAS_DATABASE_URL, echo=False, connect_args={"check_same_thread": False, "timeout": 15})

def create_db_and_tables():
    import models  # Nos aseguramos de registrar los modelos en la metadata
    
    # Inicializa solo las tablas correspondientes en cada base de datos
    # tienda.db: productos y pagos procesados
    # compras.db: historial de compras
    tienda_tables = [models.Product.__table__, models.ProcessedPayment.__table__]
    compras_tables = [models.PurchaseRecord.__table__]
    SQLModel.metadata.create_all(engine, tables=tienda_tables)
    SQLModel.metadata.create_all(engine_compras, tables=compras_tables)