import os
import mercadopago
from fastapi import FastAPI, Depends, Request, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select, SQLModel
from typing import List
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from notifications import send_emails, send_transfer_email, send_contact_email
import json
from models import Product, Cart, ContactForm, ProcessedPayment 
from database import engine, create_db_and_tables

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if not mp_access_token:
    raise ValueError("La variable de entorno MERCADOPAGO_ACCESS_TOKEN no está definida.")

sdk = mercadopago.SDK(mp_access_token)


origins = [
    "https://amanece.ar",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

processed_payment_ids = set()

def get_session():
    with Session(engine) as session:
        yield session

def calculate_shipping_cost(cp_str: str) -> float:
    if not cp_str: return 0.0
    cp_clean = cp_str.strip()
    if not cp_clean.isdigit(): return 0.0
    cp = int(cp_clean)
    
    # EJEMPLO - AJUSTAR PRECIOS
    if 1000 <= cp <= 1499:
        return 5000.00 
    elif 1500 <= cp <= 1999:
        return 6500.00 
    elif cp >= 2000 and cp < 9999:
        return 8500.00 
    else:
        return 8500.00 
    
#modificar por algo en desuso

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

@app.get("/api/products", response_model=List[Product])
def get_products(session: Session = Depends(get_session)):
    products = session.exec(select(Product)).all()
    return products

class ShippingRequest(SQLModel):
    zip_code: str

@app.post("/api/calculate_shipping")
def calculate_shipping(data: ShippingRequest):
    cost = calculate_shipping_cost(data.zip_code)
    message = "Costo de envío"
    if cost > 0: message = "Envío a domicilio"
    return {"cost": cost, "message": message}

# --- ENDPOINT MERCADO PAGO ---
@app.post("/api/create_preference")
def create_preference(cart: Cart, session: Session = Depends(get_session)):
    preference_items = []
    has_free_shipping = False 
    
    for item_in_cart in cart.items:
        product = session.get(Product, item_in_cart.id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {item_in_cart.id} no encontrado.")

        title = product.name
        unit_price = product.price
        item_id_string = f"IND|{product.id}" 

        if item_in_cart.variant == "pack":
            if not product.pack_info:
                raise HTTPException(status_code=400, detail="Error en pack.")
            
            pack_stock = product.pack_info.get("pack_stock", 0)
            if pack_stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail="Stock insuficiente pack.")

            title = product.pack_info.get("pack_name")
            unit_price = product.pack_info.get("pack_price")
            item_id_string = f"PACK|{product.id}"
            has_free_shipping = True
        else:
            if product.stock < item_in_cart.quantity:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente {product.name}.")

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
        "shipments": {
            "cost": shipping_cost,
            "mode": "not_specified",
        },
        "payer": payer_info,
        "metadata": metadata,
        "back_urls": {
            "success": "https://amanece.ar/pago-exitoso",
            "failure": "https://amanece.ar/pago-fallido",
            "pending": "https://amanece.ar/pago-pendiente"
        },
        "auto_return": "approved",
        "notification_url": "https://apibod.serv-node.dev/api/webhook"
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
        raise HTTPException(status_code=500, detail="Error server")

# --- WEBHOOK (Anti-Duplicados) ---
@app.post("/api/webhook")
async def webhook_mercado_pago(request: Request):
    try:
        params = request.query_params
        topic = params.get("topic") or params.get("type")
        payment_id = params.get("id") or params.get("data.id")

        if topic == "payment" and payment_id:
            payment_id = str(payment_id)
            with Session(engine) as session:
                existing_payment = session.get(ProcessedPayment, payment_id)
                if existing_payment:
                    print(f"⚠️ Pago {payment_id} ya procesado anteriormente. Ignorando.")
                    return {"status": "ok"}

            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            status = payment.get("status")
            
            if status == "approved":
                print(f"💰 PAGO APROBADO REAL: ID {payment_id}")
                try:
                    with Session(engine) as session:
                        new_payment = ProcessedPayment(payment_id=payment_id, status=status)
                        session.add(new_payment)
                        session.commit()
                except Exception as e_db:
                    print(f"Error guardando ID en DB: {e_db}")

                metadata = payment.get("metadata", {})
                items = payment.get("additional_info", {}).get("items", [])
                total_paid = payment.get("transaction_amount", 0)
                
                try:
                    with Session(engine) as session:
                        for item in items:
                            item_id_str = item.get("id", "")
                            quantity = int(item.get("quantity", 0))
                            
                            if not item_id_str: continue

                            if "|" in item_id_str:
                                tipo, prod_id = item_id_str.split("|")
                                product = session.get(Product, int(prod_id))
                                
                                if product:
                                    if tipo == "IND":
                                        product.stock = max(0, product.stock - quantity)
                                        print(f"📉 Stock Ind. {product.name}: -{quantity}")
                                        
                                    elif tipo == "PACK" and product.pack_info:
                                        current_pack = dict(product.pack_info)
                                        p_stock = current_pack.get("pack_stock", 0)
                                        current_pack["pack_stock"] = max(0, p_stock - quantity)
                                        product.pack_info = current_pack
                                        print(f"📉 Stock Pack {product.name}: -{quantity}")
                                        
                                    session.add(product)
                        
                        session.commit()
                        print("✅ Stock descontado correctamente en DB")
                except Exception as e_stock:
                    print(f"❌ Error crítico descontando stock: {e_stock}")
                print("📧 Enviando notificaciones...")
                send_emails(metadata, items, total_paid)

        return {"status": "ok"}
    except Exception as e:
        print(f"Error General Webhook: {e}")
        return {"status": "error"}

@app.post("/api/create_transfer_order")
async def create_transfer_order(
    cart_data: str = Form(...),    
    file: UploadFile = File(...), 
    session: Session = Depends(get_session)
):
    try:
        data = json.loads(cart_data)
        items = data.get("items", [])
        user_data = data.get("user_data", {})
        total_price = data.get("total_price", 0)
        discount = data.get("discount", 0)
        
        if 'lastName' in user_data:
            user_data['last_name'] = user_data['lastName']
        if 'zip_code' in data:
            user_data['zip_code'] = data['zip_code']

        for item in items:
            product = session.get(Product, item['id'])
            if not product: continue
            
            qty = item['quantity']
            variant = item.get('variant', 'individual')

            if variant == 'individual':
                if product.stock >= qty:
                    product.stock -= qty
                else:
                     raise HTTPException(status_code=400, detail=f"Sin stock de {product.name}")
            elif variant == 'pack' and product.pack_info:
                current_pack = dict(product.pack_info)
                p_stock = current_pack.get('pack_stock', 0)
                if p_stock >= qty:
                    current_pack['pack_stock'] = p_stock - qty
                    product.pack_info = current_pack
                else:
                    raise HTTPException(status_code=400, detail=f"Sin stock de pack {product.name}")
            session.add(product)
        
        session.commit()

        file_content = await file.read()
        
        mail_items = []
        for i in items:
            mail_items.append({'quantity': i['quantity'], 'title': f"{i.get('name')} ({i.get('variant')})"})

        send_transfer_email(
            user_data=user_data,
            items=mail_items,
            total_paid=total_price,
            discount=discount,
            file_bytes=file_content,
            filename=file.filename
        )

        return {"status": "ok", "message": "Orden recibida"}

    except Exception as e:
        print(f"Error transferencia: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/contact")
def submit_contact_form(form: ContactForm):
    send_contact_email(form)
    return {"status": "ok", "message": "Mensaje enviado"}


# --- SEGURIDAD: Verificar Contraseña de Admin ---
def verify_admin(x_admin_token: str = Header(None)):
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if not admin_pass or x_admin_token != admin_pass:
        raise HTTPException(status_code=401, detail="Acceso no autorizado")

# --- CRUD DE PRODUCTOS (Solo Admin) ---

# 1. CREAR PRODUCTO
@app.post("/api/products", status_code=201)
def create_product(product: Product, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    session.add(product)
    session.commit()
    session.refresh(product)
    return product

# 2. EDITAR PRODUCTO (Stock, Precio, Info)
@app.put("/api/products/{product_id}")
def update_product(product_id: int, product_data: Product, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    product_db = session.get(Product, product_id)
    if not product_db:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    # Actualizamos los campos
    product_data_dict = product_data.model_dump(exclude_unset=True)
    # Evitamos sobrescribir el ID
    if "id" in product_data_dict:
        del product_data_dict["id"]
        
    for key, value in product_data_dict.items():
        setattr(product_db, key, value)

    session.add(product_db)
    session.commit()
    session.refresh(product_db)
    return product_db

# 3. BORRAR PRODUCTO
@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    session.delete(product)
    session.commit()
    return {"ok": True}

@app.post("/api/admin/login")
def admin_login_check(authorized: bool = Depends(verify_admin)):
    return {"status": "ok"}