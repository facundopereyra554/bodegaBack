# notifications.py
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

def send_emails(metadata, items, total_paid):
    sender_email = os.getenv("MAIL_USERNAME")
    sender_password = os.getenv("MAIL_PASSWORD")
    
    if not sender_email or not sender_password:
        print("ERROR: Faltan credenciales de correo en .env")
        return

    # --- CORREO 1: AL CLIENTE ---
    try:
        customer_email = metadata.get("email")
        subject_client = "¡Compra confirmada! - Tienda de Vinos"
        
        # Armamos la lista de productos en HTML
        items_html = "<ul>"
        for item in items:
            # MP devuelve los items en un formato específico, a veces strings, a veces dicts
            title = item.get('title', 'Producto')
            qty = item.get('quantity', 1)
            price = item.get('unit_price', 0)
            items_html += f"<li>{qty}x {title} - ${price}</li>"
        items_html += "</ul>"

        body_client = f"""
        <html>
          <body style="font-family: Arial, sans-serif;">
            <h2 style="color: #333;">Hola {metadata.get('name')}, ¡gracias por tu compra!</h2>
            <p>Hemos recibido tu pago correctamente.</p>
            <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px;">
                <h3>Detalle de tu pedido:</h3>
                {items_html}
                <p style="font-size: 1.2rem;"><strong>Total pagado: ${total_paid}</strong></p>
            </div>
            <p>Tus datos de envío registrados:</p>
            <p>{metadata.get('address')}</p>
            <hr>
            <p>Nos pondremos en contacto contigo pronto para coordinar el envío.</p>
          </body>
        </html>
        """

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = customer_email
        msg['Subject'] = subject_client
        msg.attach(MIMEText(body_client, 'html'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        print(f"✅ Correo enviado al cliente: {customer_email}")
        server.quit()

    except Exception as e:
        print(f"❌ Error enviando email al cliente: {e}")

    # --- CORREO 2: AL ADMIN (A TI MISMO) ---
    try:
        # Nos enviamos el mail a nosotros mismos
        admin_email = sender_email 
        subject_admin = f"NUEVA VENTA - {metadata.get('name')} {metadata.get('last_name')}"
        
        # Lista simple texto plano
        items_str = "\n".join([f"- {i.get('quantity')}x {i.get('title')}" for i in items])
        
        body_admin = f"""
        ¡NUEVA VENTA RECIBIDA!
        -----------------------
        CLIENTE: {metadata.get('name')} {metadata.get('last_name')}
        EMAIL: {metadata.get('email')}
        WHATSAPP: {metadata.get('whatsapp')}
        DIRECCIÓN: {metadata.get('address')}
        
        PEDIDO:
        {items_str}
        
        TOTAL: ${total_paid}
        -----------------------
        Revisar panel de Mercado Pago para confirmar acreditación.
        """

        msg_admin = MIMEMultipart()
        msg_admin['From'] = sender_email
        msg_admin['To'] = admin_email
        msg_admin['Subject'] = subject_admin
        msg_admin.attach(MIMEText(body_admin, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg_admin)
        print(f"✅ Alerta enviada al admin.")
        server.quit()
        
    except Exception as e:
        print(f"❌ Error enviando alerta admin: {e}")