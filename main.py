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

print("--- INICIANDO SERVIDOR ---")
print(f"Mercado Pago Access Token: {os.getenv('MERCADOPAGO_ACCESS_TOKEN')}")
print("--------------------------")


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if not mp_access_token:
    raise ValueError("La variable de entorno MERCADOPAGO_ACCESS_TOKEN no está definida.")

sdk = mercadopago.SDK(mp_access_token)
from models import Product, Cart
from database import engine

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

@app.get("/api/products", response_model=List[Product])
def get_products(session: Session = Depends(get_session)):
    products = session.exec(select(Product)).all()
    return products

class ShippingRequest(SQLModel):
    zip_code: str


@app.post("/api/calculate_shipping")
def calculate_shipping(data: ShippingRequest):
    cp_str = data.zip_code.strip()
    if not cp_str.isdigit():
        raise HTTPException(status_code=400, detail="El código postal debe contener solo números.")
    
    cp = int(cp_str)
    cost = 0.0
    message = ""
    if 1000 <= cp <= 1499:
        cost = 3500.00
        message = "Envío CABA"
        
    elif 1500 <= cp <= 1999:
        cost = 5800.00
        message = "Envío GBA"
        
    elif cp >= 2000 and cp < 9999:
        cost = 8500.00
        message = "Envío Nacional (Correo)"
        
    else:
        cost = 8500.00
        message = "Envío Estándar"

    return {"cost": cost, "message": message}

@app.post("/api/create_preference")
def create_preference(cart: Cart, session: Session = Depends(get_session)):
    preference_items = []
    
    # 1. Recorremos el carrito para validar productos y stock
    for item_in_cart in cart.items:
        product = session.get(Product, item_in_cart.id)
        
        if not product:
            raise HTTPException(status_code=404, detail=f"Producto con ID {item_in_cart.id} no encontrado.")

        title = product.name
        unit_price = product.price

        # Lógica para Packs vs Individual
        if item_in_cart.variant == "pack":
            if not product.pack_info:
                raise HTTPException(status_code=400, detail=f"El producto {product.name} no tiene un pack.")
            
            # Verificamos el stock del pack
            pack_stock = product.pack_info.get("pack_stock", 0)
            if pack_stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para el pack de {product.name}.")

            title = product.pack_info.get("pack_name")
            unit_price = product.pack_info.get("pack_price")
        else:
            # Verificamos el stock individual
            if product.stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para {product.name}.")

        preference_items.append({
            "title": title, 
            "quantity": item_in_cart.quantity,
            "unit_price": unit_price, 
            "currency_id": "ARS"
        })

    if not preference_items:
        raise HTTPException(status_code=400, detail="El carrito está vacío o contiene items inválidos.")

    # 2. Preparamos la METADATA (Datos del usuario para el Webhook)
    metadata = {}
    payer_info = {}

    if cart.user_data:
        # Mercado Pago prefiere las keys en snake_case (sin mayúsculas tipo camelCase)
        metadata = {
            "name": cart.user_data.name,
            "last_name": cart.user_data.lastName,
            "email": cart.user_data.email,
            "whatsapp": cart.user_data.whatsapp,
            "address": cart.user_data.address
        }
        
        # Información del pagador para Mercado Pago
        payer_info = {
            "name": cart.user_data.name,
            "surname": cart.user_data.lastName,
            "email": cart.user_data.email
        }

    # 3. Armamos el objeto de preferencia completo
    preference_data = {
        "items": preference_items,
        "payer": payer_info,     # <--- Agregamos datos del pagador
        "metadata": metadata,    # <--- Agregamos la metadata para notificaciones
        "back_urls": {
            "success": "https://amt-dcv.com/pago-exitoso",
            "failure": "https://amt-dcv.com/pago-fallido",
            "pending": "https://amt-dcv.com/pago-pendiente"
        },
        "auto_return": "approved",
    }

    try:
        # El SDK devuelve directamente el diccionario con los datos de la preferencia
        preference_response = sdk.preference().create(preference_data)
        
        # Verificamos si la respuesta es la esperada antes de acceder a sus claves
        if preference_response and "response" in preference_response and "id" in preference_response["response"]:
            preference_id = preference_response["response"]["id"]
            return {"preference_id": preference_id}
        else:
            # Si la respuesta no tiene la forma esperada, la imprimimos para depurar
            print("Respuesta inesperada de Mercado Pago:")
            print(preference_response)
            raise HTTPException(status_code=500, detail="Respuesta inesperada de Mercado Pago.")

    except Exception as e:
        # Imprimimos el error específico para tener más detalles
        print(f"Error al crear la preferencia de MP: {e}")
        # También es útil imprimir los datos que se enviaron
        print(f"Datos enviados a MP: {preference_data}")
        raise HTTPException(status_code=500, detail="Error al comunicarse con Mercado Pago.")

@app.post("/api/webhook")
async def webhook_mercado_pago(request: Request):
    try:
        params = request.query_params
        topic = params.get("topic") or params.get("type")
        payment_id = params.get("id") or params.get("data.id")

        if topic == "payment" and payment_id:
            # Consultamos el estado del pago a MP
            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            
            status = payment.get("status")
            
            if status == "approved":
                print(f"💰 PAGO APROBADO: ID {payment_id}")
                
                # Extraemos la data
                metadata = payment.get("metadata", {})
                items = payment.get("additional_info", {}).get("items", [])
                total_paid = payment.get("transaction_amount", 0)
                
                # Disparamos los correos
                print("Enviando correos...")
                send_emails(metadata, items, total_paid)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error Webhook: {e}")
        return {"status": "error"}