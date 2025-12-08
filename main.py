import os
import mercadopago
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, SQLModel
from typing import List
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from notifications import send_emails

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if not mp_access_token:
    raise ValueError("La variable de entorno MERCADOPAGO_ACCESS_TOKEN no está definida.")

sdk = mercadopago.SDK(mp_access_token)
from models import Product, Cart
from database import engine

# Tus orígenes permitidos
origins = ["https://amt-dcv.com"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session():
    with Session(engine) as session:
        yield session

# --- FUNCIÓN AUXILIAR PARA CALCULAR ENVÍO (DRY) ---
def calculate_shipping_cost(cp_str: str) -> float:
    if not cp_str:
        return 0.0
    
    # Limpiamos espacios y validamos que sea número
    cp_clean = cp_str.strip()
    if not cp_clean.isdigit():
        return 0.0
    
    cp = int(cp_clean)
    
    if 1000 <= cp <= 1499:
        return 100.00 # CABA
    elif 1500 <= cp <= 1999:
        return 150.00 # GBA
    elif cp >= 2000 and cp < 9999:
        return 120.00 # Interior
    else:
        return 120.00 # Default/Otros

@app.get("/api/products", response_model=List[Product])
def get_products(session: Session = Depends(get_session)):
    products = session.exec(select(Product)).all()
    return products

class ShippingRequest(SQLModel):
    zip_code: str

@app.post("/api/calculate_shipping")
def calculate_shipping(data: ShippingRequest):
    # Usamos la función auxiliar
    cost = calculate_shipping_cost(data.zip_code)
    
    # Mensaje descriptivo simple
    message = "Costo de envío"
    if cost == 100: message = "Envío CABA"
    elif cost == 150: message = "Envío GBA"
    elif cost == 120: message = "Envío Nacional"

    return {"cost": cost, "message": message}

@app.post("/api/create_preference")
def create_preference(cart: Cart, session: Session = Depends(get_session)):
    preference_items = []
    has_free_shipping = False 
    
    for item_in_cart in cart.items:
        product = session.get(Product, item_in_cart.id)
        
        if not product:
            raise HTTPException(status_code=404, detail=f"Producto con ID {item_in_cart.id} no encontrado.")

        title = product.name
        unit_price = product.price
        item_id_string = f"IND|{product.id}" 

        if item_in_cart.variant == "pack":
            if not product.pack_info:
                raise HTTPException(status_code=400, detail=f"El producto {product.name} no tiene un pack.")
            
            pack_stock = product.pack_info.get("pack_stock", 0)
            if pack_stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para el pack.")

            title = product.pack_info.get("pack_name")
            unit_price = product.pack_info.get("pack_price")
            item_id_string = f"PACK|{product.id}" # Marcamos que es PACK
            
            has_free_shipping = True
        else:
            if product.stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}.")

        preference_items.append({
            "id": item_id_string,
            "title": title, 
            "quantity": item_in_cart.quantity,
            "unit_price": unit_price, 
            "currency_id": "ARS"
        })

    if not preference_items:
        raise HTTPException(status_code=400, detail="Carrito vacío.")

    shipping_cost = 0.0
    if not has_free_shipping and cart.zip_code:
        shipping_cost = calculate_shipping_cost(cart.zip_code)
        if shipping_cost > 0:
            preference_items.append({
                "id": "SHIP|0", # ID dummy para el envío
                "title": "Costo de Envío",
                "quantity": 1,
                "unit_price": shipping_cost,
                "currency_id": "ARS"
            })

    metadata = {}
    payer_info = {}

    if cart.user_data:
        metadata = {
            "name": cart.user_data.name,
            "last_name": cart.user_data.lastName,
            "email": cart.user_data.email,
            "whatsapp": cart.user_data.whatsapp,
            "address": cart.user_data.address,
            "zip_code": cart.zip_code
        }
        payer_info = {
            "name": cart.user_data.name,
            "surname": cart.user_data.lastName,
            "email": cart.user_data.email
        }

    preference_data = {
        "items": preference_items,
        "payer": payer_info,
        "metadata": metadata,
        "back_urls": {
            "success": "https://amt-dcv.com/pago-exitoso",
            "failure": "https://amt-dcv.com/pago-fallido",
            "pending": "https://amt-dcv.com/pago-pendiente"
        },
        "auto_return": "approved",
        # --- ¡AQUÍ ESTÁ LA SOLUCIÓN DEL EMAIL! ---
        # Le decimos explícitamente a MP que avise a nuestro VPS
        "notification_url": "https://ia.serv-node.dev/api/webhook" 
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        if preference_response and "response" in preference_response and "id" in preference_response["response"]:
            return {"preference_id": preference_response["response"]["id"]}
        else:
            print("Error MP:", preference_response)
            raise HTTPException(status_code=500, detail="Error MP")

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Error de servidor")

@app.post("/api/webhook")
async def webhook_mercado_pago(request: Request):
    try:
        params = request.query_params
        topic = params.get("topic") or params.get("type")
        payment_id = params.get("id") or params.get("data.id")

        if topic == "payment" and payment_id:
            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            
            status = payment.get("status")
            
            # Solo actuamos si el pago se aprobó
            if status == "approved":
                print(f"💰 PAGO APROBADO: ID {payment_id}")
                
                metadata = payment.get("metadata", {})
                items = payment.get("additional_info", {}).get("items", [])
                total_paid = payment.get("transaction_amount", 0)
                
                try:
                    with Session(engine) as session:
                        print("Actualizando stock...")
                        for item in items:
                            item_id_str = item.get("id", "")
                            quantity = int(item.get("quantity", 0))
                            
                            # Ignoramos el ítem de envío ("SHIP|0")
                            if not item_id_str or "SHIP" in item_id_str:
                                continue

                            # Formato esperado: "TIPO|ID" (ej: "PACK|2" o "IND|5")
                            if "|" in item_id_str:
                                tipo, prod_id = item_id_str.split("|")
                                product = session.get(Product, int(prod_id))
                                
                                if product:
                                    if tipo == "IND":
                                        # Restamos stock individual
                                        product.stock = max(0, product.stock - quantity)
                                        print(f"Restado {quantity} botellas a {product.name}")
                                        
                                    elif tipo == "PACK" and product.pack_info:
                                        # Restamos stock del pack (es un JSON, hay que actualizar el dict)
                                        current_pack_info = dict(product.pack_info) # Copia
                                        current_stock = current_pack_info.get("pack_stock", 0)
                                        current_pack_info["pack_stock"] = max(0, current_stock - quantity)
                                        
                                        # Reasignamos para que SQLModel detecte el cambio
                                        product.pack_info = current_pack_info
                                        print(f"Restado {quantity} packs a {product.name}")
                                        
                                    session.add(product)
                        
                        session.commit()
                        print("✅ Stock actualizado en DB")
                        
                except Exception as e_stock:
                    print(f"❌ Error actualizando stock: {e_stock}")

                # --- 2. ENVIAR CORREOS ---
                print("Enviando correos...")
                send_emails(metadata, items, total_paid)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error Webhook: {e}")
        return {"status": "error"}