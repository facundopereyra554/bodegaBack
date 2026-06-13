import os
import json
import shutil
import uuid
import secrets
import mercadopago
from fastapi import FastAPI, Depends, Request, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlmodel import Session, select, SQLModel
from typing import List, Dict, Any
from dotenv import load_dotenv

from models import Product, Cart, CartItem, ContactForm, ProcessedPayment, PurchaseRecord
from database import engine, engine_compras, create_db_and_tables
from notifications import send_emails, send_transfer_email, send_contact_email

load_dotenv()

app = FastAPI()

# Montamos la carpeta estática para servir tanto imágenes como archivos cargados
os.makedirs("static/products", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if not mp_access_token:
    raise ValueError("La variable de entorno MERCADOPAGO_ACCESS_TOKEN no está definida.")

sdk = mercadopago.SDK(mp_access_token)

origins = [
    "https://amanece.ar",
    "https://bodegavalledelcondor.com"
]

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

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

@app.get("/api/products", response_model=List[Product])
def get_products(session: Session = Depends(get_session)):
    products = session.exec(select(Product)).all()
    return products

def calculate_shipping_cost(cp_str: str) -> float:
    if not cp_str: return 0.0
    cp_clean = cp_str.strip()
    if not cp_clean.isdigit(): return 0.0
    cp = int(cp_clean)
    
    if 1000 <= cp <= 1499:
        return 5000.00 
    elif 1500 <= cp <= 1999:
        return 6500.00 
    else:
        return 8500.00 

class ShippingRequest(SQLModel):
    zip_code: str

@app.post("/api/calculate_shipping")
def calculate_shipping(data: ShippingRequest):
    cost = calculate_shipping_cost(data.zip_code)
    return {"cost": cost, "message": "Costo de envío a domicilio"}

# --- LÓGICA CENTRAL DE NEGOCIO ---
def calculate_cart_totals(cart_items: List[CartItem], zip_code: str, session: Session) -> Dict[str, Any]:
    total_packs = 0
    subtotal = 0.0
    validated_items = []
    
    aggregated_items = {}
    for item in cart_items:
        if item.quantity <= 0: continue
        aggregated_items[item.id] = aggregated_items.get(item.id, 0) + item.quantity
    
    for prod_id, qty in aggregated_items.items():
        product = session.get(Product, prod_id)
        if not product or not product.pack_info:
            raise HTTPException(status_code=400, detail=f"Producto {prod_id} no válido.")
        if not product.is_active:
            raise HTTPException(status_code=400, detail=f"Producto {product.name} no está disponible temporalmente.")
        
        total_packs += qty
        pack_price = product.pack_info.get("pack_price", 0.0)
        pack_name = product.pack_info.get("pack_name", product.name)
        pack_stock = product.pack_info.get("pack_stock", 0)
        
        if pack_stock < qty:
            raise HTTPException(status_code=400, detail=f"Stock insuficiente para {pack_name}.")
            
        validated_items.append({
            "product_id": product.id,
            "qty": qty,
            "base_price": pack_price,
            "name": pack_name
        })
        subtotal += (pack_price * qty)
        
    if total_packs == 0:
        raise HTTPException(status_code=400, detail="El carrito está vacío.")

    shipping_cost = 0.0
    if total_packs < 2 and zip_code:
        shipping_cost = calculate_shipping_cost(zip_code)
        
    volume_discount_pct = 0.0
    if total_packs >= 6:
        volume_discount_pct = 0.15
    elif 3 <= total_packs <= 5:
        volume_discount_pct = 0.10
        
    return {
        "items": validated_items,
        "total_packs": total_packs,
        "subtotal": subtotal,
        "volume_discount_pct": volume_discount_pct,
        "shipping_cost": shipping_cost
    }

# --- ENDPOINT MERCADO PAGO ---
@app.post("/api/create_preference")
def create_preference(cart: Cart, session: Session = Depends(get_session)):
    totals = calculate_cart_totals(cart.items, cart.zip_code, session)
    
    preference_items = []
    discount_multiplier = 1.0 - totals["volume_discount_pct"]
    
    for v_item in totals["items"]:
        final_unit_price = round(v_item["base_price"] * discount_multiplier, 2)
        
        preference_items.append({
            "id": f"PACK|{v_item['product_id']}",
            "title": v_item["name"], 
            "quantity": v_item["qty"],
            "unit_price": final_unit_price, 
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
        "shipments": {"cost": totals["shipping_cost"], "mode": "not_specified"},
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
        raise HTTPException(status_code=500, detail="Error MP")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- WEBHOOK MERCADO PAGO ---
@app.post("/api/webhook")
def webhook_mercado_pago(request: Request):
    try:
        params = request.query_params
        topic = params.get("topic") or params.get("type")
        payment_id = params.get("id") or params.get("data.id")

        if topic == "payment" and payment_id:
            payment_id = str(payment_id)
            with Session(engine) as session:
                existing_payment = session.get(ProcessedPayment, payment_id)
                if existing_payment:
                    return {"status": "ok"}

            payment_info = sdk.payment().get(payment_id)
            payment = payment_info.get("response", {})
            status = payment.get("status")
            
            if status == "approved":
                with Session(engine) as session:
                    new_payment = ProcessedPayment(payment_id=payment_id, status=status)
                    session.add(new_payment)
                    session.commit()

                metadata = payment.get("metadata", {})
                additional_info = payment.get("additional_info") or {}
                items = additional_info.get("items", [])
                total_paid = payment.get("transaction_amount", 0)
                
                # Descuento de Stock
                with Session(engine) as session:
                    for item in items:
                        item_id_str = item.get("id", "")
                        quantity = int(item.get("quantity", 0))
                        
                        if "|" in item_id_str:
                            tipo, prod_id = item_id_str.split("|")
                            product = session.get(Product, int(prod_id))
                            if product and tipo == "PACK" and product.pack_info:
                                current_pack = dict(product.pack_info)
                                p_stock = current_pack.get("pack_stock", 0)
                                current_pack["pack_stock"] = max(0, p_stock - quantity)
                                product.pack_info = current_pack
                                product.stock = max(0, product.stock - quantity)
                                session.add(product)
                    session.commit()
                
                # ASENTAR EN LA BASE DE DATOS DE COMPRAS (compras.db)
                with Session(engine_compras) as compras_session:
                    purchase = PurchaseRecord(
                        payment_id=payment_id,
                        payment_method="mp",
                        status=status,
                        total_paid=float(total_paid),
                        items=json.dumps(items),
                        user_data=json.dumps(metadata)
                    )
                    compras_session.add(purchase)
                    compras_session.commit()

                send_emails(metadata, items, total_paid)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

# --- ORDEN DE TRANSFERENCIA ---
@app.post("/api/create_transfer_order")
def create_transfer_order(
    cart_data: str = Form(...),    
    file: UploadFile = File(...), 
    session: Session = Depends(get_session)
):
    try:
        data = json.loads(cart_data)
        items_data = data.get("items", [])
        user_data = data.get("user_data", {})
        
        cart_items = [CartItem(id=i["id"], quantity=i["quantity"]) for i in items_data]
        zip_code = data.get("zip_code") or user_data.get("zip_code", "")

        totals = calculate_cart_totals(cart_items, zip_code, session)
        
        TRANSFER_DISCOUNT_PCT = 0.05
        discount_multiplier = 1.0 - totals["volume_discount_pct"]
        subtotal_con_volumen = totals["subtotal"] * discount_multiplier
        total_a_pagar = (subtotal_con_volumen * (1.0 - TRANSFER_DISCOUNT_PCT)) + totals["shipping_cost"]

        # Descontar Stock
        for v_item in totals["items"]:
            product = session.get(Product, v_item["product_id"])
            current_pack = dict(product.pack_info)
            current_pack["pack_stock"] = current_pack.get("pack_stock", 0) - v_item["qty"]
            product.pack_info = current_pack
            product.stock = max(0, product.stock - v_item["qty"])
            session.add(product)
        session.commit()

        if 'lastName' in user_data:
            user_data['last_name'] = user_data['lastName']
        user_data['zip_code'] = zip_code

        mail_items = []
        for v_item in totals["items"]:
            mail_items.append({'quantity': v_item["qty"], 'title': f"{v_item['name']} (Pack)"})

        file_content = file.file.read()
        
        # Validar tamaño del archivo (max 5MB)
        if len(file_content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="El comprobante es demasiado grande. El límite es 5MB.")

        # ASENTAR EN LA BASE DE DATOS DE COMPRAS (compras.db)
        with Session(engine_compras) as compras_session:
            purchase = PurchaseRecord(
                payment_id=None,
                payment_method="transferencia",
                status="pending_review",
                total_paid=round(total_a_pagar, 2),
                items=json.dumps(mail_items),
                user_data=json.dumps(user_data)
            )
            compras_session.add(purchase)
            compras_session.commit()

        send_transfer_email(
            user_data=user_data,
            items=mail_items,
            total_paid=round(total_a_pagar, 2),
            discount=TRANSFER_DISCOUNT_PCT,
            file_bytes=file_content,
            filename=file.filename
        )

        return {"status": "ok", "message": "Orden recibida"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/contact")
def submit_contact_form(form: ContactForm):
    send_contact_email(form)
    return {"status": "ok", "message": "Mensaje enviado"}

# --- SEGURIDAD: VERIFICAR TOKEN DE ADMIN ---
def verify_admin(x_admin_token: str = Header(None)):
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if not admin_pass or not x_admin_token:
        raise HTTPException(status_code=401, detail="Acceso no autorizado")
    
    # Prevención contra ataques de tiempo (Timing Attacks)
    if not secrets.compare_digest(x_admin_token, admin_pass):
        raise HTTPException(status_code=401, detail="Acceso no autorizado")

# --- ENDPOINTS EXCLUSIVOS DEL ADMINISTRADOR ---

# Subir una o varias imágenes reales al servidor
@app.post("/api/admin/upload-images")
def upload_images(files: List[UploadFile] = File(...), authorized: bool = Depends(verify_admin)):
    saved_paths = []
    upload_dir = "static/products"
    
    for file in files:
        # Generamos una ruta segura usando el nombre del archivo original
        safe_filename = f"{uuid.uuid4().hex}_{os.path.basename(file.filename)}"
        file_path = os.path.join(upload_dir, safe_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        # Retornamos la ruta relativa que se concatenará con la url estática del servidor
        saved_paths.append(f"/static/products/{safe_filename}")
        
    return {"paths": saved_paths}

# Descargar Copia de Seguridad de la Base de Productos (tienda.db)
@app.get("/api/admin/backup/tienda")
def download_tienda_db(authorized: bool = Depends(verify_admin)):
    file_path = "tienda.db"
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename="backup_tienda.db", media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="Base de datos tienda.db no encontrada.")

# Descargar Copia de Seguridad de la Base de Historial de Compras (compras.db)
@app.get("/api/admin/backup/compras")
def download_compras_db(authorized: bool = Depends(verify_admin)):
    file_path = "compras.db"
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename="backup_compras.db", media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="Base de datos compras.db no encontrada.")

# CRUD DE PRODUCTOS
@app.post("/api/products", status_code=201)
def create_product(product: Product, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    session.add(product)
    session.commit()
    session.refresh(product)
    return product

@app.put("/api/products/{product_id}")
def update_product(product_id: int, product_data: Product, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    product_db = session.get(Product, product_id)
    if not product_db:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    product_data_dict = product_data.model_dump(exclude_unset=True)
    if "id" in product_data_dict:
        del product_data_dict["id"]
        
    for key, value in product_data_dict.items():
        setattr(product_db, key, value)

    session.add(product_db)
    session.commit()
    session.refresh(product_db)
    return product_db

@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    if product.images:
        for image_path in product.images:
            if image_path.startswith("/"):
                image_path = image_path[1:]
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except Exception:
                    pass

    session.delete(product)
    session.commit()
    return {"ok": True}

@app.post("/api/admin/login")
def admin_login_check(authorized: bool = Depends(verify_admin)):
    return {"status": "ok"}