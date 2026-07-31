# models.py
from typing import Optional, List, Dict, Any
from sqlmodel import Field, SQLModel, Column, JSON
from datetime import datetime

class ContactForm(SQLModel):
    name: str
    email: str
    message: str

class CartItem(SQLModel):
    id: int
    quantity: int
    variant: Optional[str] = "pack"

class UserData(SQLModel):
    name: str
    lastName: str
    email: str
    whatsapp: str
    address: str

class Cart(SQLModel):
    items: List[CartItem]
    user_data: Optional[UserData] = None
    zip_code: Optional[str] = None

class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str
    price: float
    category: str
    long_description: str
    stock: int
    is_active: bool = Field(default=True)
    images: List[str] = Field(sa_column=Column(JSON))  # Soporta una o múltiples rutas de imágenes
    additional_info: Dict[str, Any] = Field(sa_column=Column(JSON))
    pack_info: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    marca: Optional[str] = None
    distincion: Optional[str] = None
    composicion: Optional[str] = None
    cosecha: Optional[str] = None
    region: Optional[str] = None
    elevacion: Optional[str] = None
    presentacion: Optional[str] = None
    alcohol: Optional[str] = None
    acidez: Optional[str] = None
    ph: Optional[str] = None
    metodo_cosecha: Optional[str] = None
    vinificacion: Optional[str] = None
    notas_de_cata: Optional[str] = None
    servicio_ideal: Optional[str] = None
    peso_caja: Optional[str] = None
    medidas_caja: Optional[str] = None
    ficha_tecnica: Optional[str] = None

class ProcessedPayment(SQLModel, table=True):
    payment_id: str = Field(primary_key=True)
    status: str

# Nuevo Modelo para el registro histórico de compras (compras.db)
class PurchaseRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    payment_id: Optional[str] = None
    payment_method: str  # "mp" o "transferencia"
    status: str
    total_paid: float
    items: str  # Almacenado como texto JSON
    user_data: str  # Almacenado como texto JSON
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))