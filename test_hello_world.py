import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

def test_hello_world():
    token = os.getenv("WHATSAPP_API_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    admin_num = os.getenv("ADMIN_WHATSAPP_NUMBER")
    
    print(f"Usando Teléfono ID: {phone_id}")
    print(f"Enviando a: {admin_num}")
    
    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "to": admin_num,
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {
                "code": "en_US"
            }
        }
    }
    
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST')
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req) as response:
            res_body = response.read()
            print(f"✅ ¡ÉXITO! Mensaje enviado correctamente.")
            print(f"Respuesta de Meta: {res_body.decode('utf-8')}")
            print("¡Revisa tu WhatsApp personal!")
            
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8')
        print(f"❌ Error HTTP {e.code} de Meta. Detalles: {error_msg}")
    except Exception as e:
        print(f"❌ Error interno: {e}")

if __name__ == "__main__":
    print("Iniciando prueba con hello_world...")
    test_hello_world()
