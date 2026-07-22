import os
import json
import shutil
import uuid
import secrets
import hashlib
import hmac as hmac_module
import threading
import sqlite3
import tempfile
import mercadopago
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, select, SQLModel
import csv
import io
from typing import List, Dict, Any
from dotenv import load_dotenv

from models import Product, Cart, CartItem, ContactForm, ProcessedPayment, PurchaseRecord
from database import engine, engine_compras, create_db_and_tables
from notifications import send_emails, send_transfer_email, send_contact_email

load_dotenv()

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    import sqlite3
    try:
        # Parche de migración: agregar columna si no existe
        conn = sqlite3.connect("tienda.db")
        conn.execute("ALTER TABLE product ADD COLUMN distincion VARCHAR;")
        conn.commit()
        conn.close()
    except Exception:
        pass
        
    create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

# Montamos la carpeta estática para servir tanto imágenes como archivos cargados
os.makedirs("static/products", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
if not mp_access_token:
    raise ValueError("La variable de entorno MERCADOPAGO_ACCESS_TOKEN no está definida.")

sdk = mercadopago.SDK(mp_access_token)

mp_webhook_secret = os.getenv("MERCADOPAGO_WEBHOOK_SECRET")
if not mp_webhook_secret:
    print("⚠️ MERCADOPAGO_WEBHOOK_SECRET no definido. El webhook aceptará notificaciones sin verificar firma.")

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

# Startup se maneja con lifespan (ver arriba)

@app.get("/api/products", response_model=List[Product])
def get_products(include_inactive: bool = False, session: Session = Depends(get_session)):
    query = select(Product)
    if not include_inactive:
        query = query.where(Product.is_active == True)
    products = session.exec(query).all()
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

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
    api_public_url = os.getenv("API_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")

    preference_data = {
        "items": preference_items,
        "shipments": {"cost": totals["shipping_cost"], "mode": "not_specified"},
        "payer": payer_info,
        "metadata": metadata,
        "back_urls": {
            "success": f"{frontend_url}/pago-exitoso",
            "failure": f"{frontend_url}/pago-fallido",
            "pending": f"{frontend_url}/pago-pendiente"
        },
        "auto_return": "approved",
    }
    
    # MercadoPago no acepta localhost o 127.0.0.1 en el notification_url
    if "localhost" not in api_public_url and "127.0.0.1" not in api_public_url:
        preference_data["notification_url"] = f"{api_public_url}/api/webhook"

    try:
        preference_response = sdk.preference().create(preference_data)
        if preference_response and "response" in preference_response and "id" in preference_response["response"]:
            return {"preference_id": preference_response["response"]["id"]}
        raise HTTPException(status_code=500, detail=f"Error MP: {preference_response}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Verificación de Firma del Webhook ---
def verify_webhook_signature(request: Request, data_id: str) -> bool:
    """Verifica la firma HMAC-SHA256 de las notificaciones de MercadoPago."""
    if not mp_webhook_secret:
        return True  # Si no hay secret configurado, se permite (con warning al inicio)

    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")

    if not x_signature:
        return False

    # Parsear ts y v1 del header x-signature (formato: "ts=123,v1=abc")
    parts = {}
    for part in x_signature.split(","):
        kv = part.strip().split("=", 1)
        if len(kv) == 2:
            parts[kv[0].strip()] = kv[1].strip()

    ts = parts.get("ts", "")
    v1 = parts.get("v1", "")

    if not ts or not v1:
        return False

    # Construir la cadena manifest y calcular HMAC
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    expected = hmac_module.new(
        mp_webhook_secret.encode(), manifest.encode(), hashlib.sha256
    ).hexdigest()

    return hmac_module.compare_digest(v1, expected)

# --- WEBHOOK MERCADO PAGO ---
@app.post("/api/webhook")
async def webhook_mercado_pago(request: Request):
    try:
        # 1. Leer datos del webhook (soporta body JSON v2 y query params legacy)
        payment_id = None
        topic = None

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        if body:
            # Formato v2: {"action": "payment.created", "data": {"id": "123"}, "type": "payment"}
            action = body.get("action", "")
            data = body.get("data", {})
            if "payment" in action or body.get("type") == "payment":
                topic = "payment"
                payment_id = str(data.get("id", ""))

        # Fallback a query params (IPN legacy)
        if not payment_id:
            params = request.query_params
            topic = params.get("topic") or params.get("type")
            payment_id = params.get("id") or params.get("data.id")

        if topic != "payment" or not payment_id:
            return {"status": "ok"}

        payment_id = str(payment_id)

        # 2. Verificar firma HMAC
        if not verify_webhook_signature(request, payment_id):
            print(f"⚠️ Webhook con firma inválida rechazado. Payment ID: {payment_id}")
            raise HTTPException(status_code=401, detail="Firma inválida")

        # 3. Idempotencia: verificar si ya se procesó
        with Session(engine) as session:
            existing_payment = session.get(ProcessedPayment, payment_id)
            if existing_payment:
                return {"status": "ok"}

        # 4. Obtener info del pago desde MP
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
                        if product:
                            if tipo == "PACK" and product.pack_info:
                                current_pack = dict(product.pack_info)
                                p_stock = current_pack.get("pack_stock", 0)
                                current_pack["pack_stock"] = max(0, p_stock - quantity)
                                product.pack_info = current_pack
                            product.stock = max(0, product.stock - quantity)
                            session.add(product)
                session.commit()

            # Registrar compra en compras.db
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

            # Enviar emails en background para no bloquear la respuesta al webhook
            threading.Thread(target=send_emails, args=(metadata, items, total_paid), daemon=True).start()

        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
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

        # Descontar Stock: AHORA SE HACE EN LA APROBACIÓN POR ADMIN, NO AQUÍ
        # for v_item in totals["items"]:
        #     product = session.get(Product, v_item["product_id"])
        #     current_pack = dict(product.pack_info)
        #     current_pack["pack_stock"] = max(0, current_pack.get("pack_stock", 0) - v_item["qty"])
        #     product.pack_info = current_pack
        #     product.stock = max(0, product.stock - v_item["qty"])
        #     session.add(product)
        # session.commit()

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
                items=json.dumps(totals["items"]),  # Guardar los items completos con product_id para luego descontar stock
                user_data=json.dumps(user_data)
            )
            compras_session.add(purchase)
            compras_session.commit()
            compras_session.refresh(purchase)
            transfer_id = f"TR-{purchase.id}"
            
            purchase.payment_id = transfer_id
            compras_session.add(purchase)
            compras_session.commit()

        threading.Thread(
            target=send_transfer_email,
            args=(user_data, mail_items, round(total_a_pagar, 2), TRANSFER_DISCOUNT_PCT, file_content, file.filename),
            daemon=True
        ).start()

        return {"status": "ok", "message": "Orden recibida", "transfer_id": transfer_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/contact")
def submit_contact_form(form: ContactForm):
    threading.Thread(target=send_contact_email, args=(form,)).start()
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


# --- ADMIN: COMPRAS ---

@app.get("/api/admin/purchases")
def get_purchases(authorized: bool = Depends(verify_admin)):
    with Session(engine_compras) as compras_session:
        # Ordenamos de más reciente a más antigua
        purchases = compras_session.exec(select(PurchaseRecord)).all()
        purchases.reverse()
        return purchases

@app.put("/api/admin/purchases/{purchase_id}/approve")
def approve_purchase(purchase_id: int, authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    with Session(engine_compras) as compras_session:
        purchase = compras_session.get(PurchaseRecord, purchase_id)
        if not purchase:
            raise HTTPException(status_code=404, detail="Compra no encontrada")
        
        if purchase.status != "pending_review":
            raise HTTPException(status_code=400, detail="Esta compra ya no está pendiente")
        
        # Descontar stock ahora sí
        try:
            items = json.loads(purchase.items)
            for v_item in items:
                product = session.get(Product, v_item["product_id"])
                if product:
                    current_pack = dict(product.pack_info) if product.pack_info else {}
                    if current_pack:
                        current_pack["pack_stock"] = max(0, current_pack.get("pack_stock", 0) - v_item["qty"])
                        product.pack_info = current_pack
                    product.stock = max(0, product.stock - v_item["qty"])
                    session.add(product)
            session.commit()
            
            # Cambiar estado
            purchase.status = "approved"
            compras_session.add(purchase)
            compras_session.commit()
            
            return {"message": "Compra aprobada y stock descontado con éxito"}
        except Exception as e:
            session.rollback()
            compras_session.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/purchases/{purchase_id}/reject")
def reject_purchase(purchase_id: int, authorized: bool = Depends(verify_admin)):
    with Session(engine_compras) as compras_session:
        purchase = compras_session.get(PurchaseRecord, purchase_id)
        if not purchase:
            raise HTTPException(status_code=404, detail="Compra no encontrada")
        
        if purchase.status != "pending_review":
            raise HTTPException(status_code=400, detail="Esta compra ya no está pendiente")
        
        purchase.status = "rejected"
        compras_session.add(purchase)
        compras_session.commit()
        
        return {"message": "Compra rechazada"}

# Descargar Copia de Seguridad de la Base de Productos (tienda.db)
@app.get("/api/admin/backup/tienda")
def download_tienda_db(authorized: bool = Depends(verify_admin)):
    file_path = "tienda.db"
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename="backup_tienda.db", media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="Base de datos tienda.db no encontrada.")



# Subir e Importar Base de Datos de Productos
@app.post("/api/admin/upload-tienda-db")
def upload_tienda_db(file: UploadFile = File(...), authorized: bool = Depends(verify_admin), session: Session = Depends(get_session)):
    if not file.filename.endswith(".db"):
        raise HTTPException(status_code=400, detail="El archivo debe tener extensión .db")

    try:
        content = file.file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM product")
        rows = cursor.fetchall()
        
        for row in rows:
            data = dict(row)
            # Eliminar ID para crear como nuevo registro (sin pisar los existentes)
            data.pop("id", None)
            
            # Parsear columnas JSON de SQLite a objetos Python
            if "images" in data and isinstance(data["images"], str):
                try: data["images"] = json.loads(data["images"])
                except: data["images"] = []
            
            if "additional_info" in data and isinstance(data["additional_info"], str):
                try: data["additional_info"] = json.loads(data["additional_info"])
                except: data["additional_info"] = {}
                
            if "pack_info" in data and isinstance(data["pack_info"], str):
                try: data["pack_info"] = json.loads(data["pack_info"])
                except: data["pack_info"] = None

            # Dividir Marca y Nombre automáticamente si existe el patrón "Marca - Nombre" en el nombre y no tiene marca asignada
            if data.get("name") and not data.get("marca"):
                if " - " in data["name"]:
                    parts = data["name"].split(" - ", 1)
                    data["marca"] = parts[0].strip()
                    data["name"] = parts[1].strip()

            new_product = Product(**data)
            session.add(new_product)
            
        session.commit()
        conn.close()
        os.remove(tmp_path)
        
        return {"ok": True, "message": f"Se han importado {len(rows)} productos exitosamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Descargar Copia de Seguridad de la Base de Historial de Compras (compras.db)
@app.get("/api/admin/backup/compras")
def download_compras_db(authorized: bool = Depends(verify_admin)):
    file_path = "compras.db"
    if os.path.exists(file_path):
        return FileResponse(path=file_path, filename="backup_compras.db", media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail="Base de datos compras.db no encontrada.")

@app.get("/api/admin/backup/compras-csv")
def download_compras_csv(authorized: bool = Depends(verify_admin)):
    with Session(engine_compras) as session:
        purchases = session.exec(select(PurchaseRecord)).all()
        
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["ID", "Payment ID", "Payment Method", "Status", "Total Paid", "Items", "User Data", "Created At"])
    
    for p in purchases:
        writer.writerow([p.id, p.payment_id, p.payment_method, p.status, p.total_paid, p.items, p.user_data, p.created_at])
        
    # Usamos yield para el StreamingResponse
    def iterfile():
        yield stream.getvalue().encode("utf-8")
        
    response = StreamingResponse(iterfile(), media_type="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=ventas.csv"
    return response

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

    # Usamos exclude_none=False y exclude_unset=False para asegurarnos
    # de que campos JSON como pack_info siempre se actualicen en la DB
    product_data_dict = product_data.model_dump(exclude_none=False)
    # Nunca permitir cambiar el ID
    product_data_dict.pop("id", None)

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