# models.py
from typing import Optional, List, Dict, Any
from sqlmodel import Field, SQLModel, Column, JSON

class CartItem(SQLModel):
    id: int
    quantity: int
    variant: Optional[str] = "individual"

# --- NUEVA CLASE PARA LOS DATOS DEL CLIENTE ---
class UserData(SQLModel):
    name: str
    lastName: str
    email: str
    whatsapp: str
    address: str

# --- ACTUALIZAMOS EL CART PARA INCLUIR USER_DATA ---
class Cart(SQLModel):
    items: List[CartItem]
    user_data: Optional[UserData] = None # Campo nuevo opcional

# ... (El resto, class Product, déjalo igual) ...
class Product(SQLModel, table=True):
    # ... tu código de Product ...
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str
    price: float
    sku: str = Field(unique=True)
    category: str
    long_description: str
    stock: int
    images: List[str] = Field(sa_column=Column(JSON))
    additional_info: Dict[str, Any] = Field(sa_column=Column(JSON))
    pack_info: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))