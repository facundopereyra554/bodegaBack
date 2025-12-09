# notifications.py
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from email.mime.base import MIMEBase
from email import encoders
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

    try:
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


def send_transfer_email(user_data, items, total_paid, discount, file_bytes, filename):
    sender_email = os.getenv("MAIL_USERNAME")
    sender_password = os.getenv("MAIL_PASSWORD")
    
    try:
        msg_client = MIMEMultipart()
        msg_client['From'] = sender_email
        msg_client['To'] = user_data.get('email')
        msg_client['Subject'] = "Pedido por Transferencia Recibido - Bodega Valle del Cóndor"
        
        body_client = f"""
        Hola {user_data.get('name')},
        
        Hemos recibido tu pedido y el comprobante de transferencia.
        
        Total a pagar (con descuento): ${total_paid}
        
        Verificaremos la acreditación en nuestra cuenta bancaria y te avisaremos cuando el pedido sea despachado.
        
        Muchas gracias por tu compra.
        """
        msg_client.attach(MIMEText(body_client, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg_client)
        server.quit()
        print("Mail cliente enviado.")
    except Exception as e:
        print(f"Error mail cliente: {e}")

    # 2. CORREO AL ADMIN (CON EL COMPROBANTE ADJUNTO)
    try:
        msg_admin = MIMEMultipart()
        msg_admin['From'] = sender_email
        msg_admin['To'] = sender_email # Se lo manda a sí mismo
        msg_admin['Subject'] = f"NUEVA TRANSFERENCIA - {user_data.get('name')} {user_data.get('last_name')}"
        
        items_str = "\n".join([f"- {i['quantity']}x {i.get('title', 'Producto')}" for i in items])
        
        body_admin = f"""
        ¡NUEVA VENTA POR TRANSFERENCIA!
        -------------------------------
        Cliente: {user_data.get('name')} {user_data.get('last_name')}
        Email: {user_data.get('email')}
        WhatsApp: {user_data.get('whatsapp')}
        Dirección: {user_data.get('address')}
        CP: {user_data.get('zip_code')}
        
        PEDIDO:
        {items_str}
        
        Total Original: ${total_paid / (1 - discount)}
        Descuento aplicado: {int(discount * 100)}%
        TOTAL FINAL: ${total_paid}
        
        -------------------------------
        >>> REVISA EL COMPROBANTE ADJUNTO <<<
        """
        msg_admin.attach(MIMEText(body_admin, 'plain'))

        # ADJUNTAR EL ARCHIVO
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(file_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= {filename}")
        msg_admin.attach(part)

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg_admin)
        server.quit()
        print("Mail admin con comprobante enviado.")
        
    except Exception as e:
        print(f"Error mail admin: {e}")

def send_contact_email(contact_data):
    sender_email = os.getenv("MAIL_USERNAME")
    sender_password = os.getenv("MAIL_PASSWORD")
    # Se envía al mismo correo que envía (el del dueño)
    admin_email = sender_email 
    
    subject = f"CONSULTA WEB - {contact_data.name}"
    
    body = f"""
    NUEVO MENSAJE DE CONTACTO
    -------------------------
    Nombre: {contact_data.name}
    Email: {contact_data.email}
    
    Mensaje:
    {contact_data.message}
    -------------------------
    Responder a este correo para contactar al cliente.
    """

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = admin_email
        # Esto es un truco: ponemos el email del cliente en "Reply-To"
        # Así cuando le das "Responder" en Gmail, le respondes al cliente y no a ti mismo
        msg.add_header('Reply-To', contact_data.email) 
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print("Mail de contacto enviado.")
    except Exception as e:
        print(f"Error mail contacto: {e}")