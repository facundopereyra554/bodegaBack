import os
import mercadopago
from dotenv import load_dotenv

load_dotenv()
mp_access_token = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
print("TOKEN:", mp_access_token)
sdk = mercadopago.SDK(mp_access_token)

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
api_public_url = os.getenv("API_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")

preference_data = {
    "items": [
        {
            "id": "PACK|1",
            "title": "Test Product",
            "quantity": 1,
            "unit_price": 100.0,
            "currency_id": "ARS"
        }
    ],
    "shipments": {"cost": 0.0, "mode": "not_specified"},
    "payer": {
        "name": "Test",
        "surname": "User",
        "email": "test@test.com"
    },
    "metadata": {},
    "back_urls": {
        "success": f"{frontend_url}/pago-exitoso",
        "failure": f"{frontend_url}/pago-fallido",
        "pending": f"{frontend_url}/pago-pendiente"
    },
    "auto_return": "approved",
    "notification_url": f"{api_public_url}/api/webhook"
}

with open("out.txt", "w") as f:
    f.write(str(preference_data) + "\n")
    try:
        response = sdk.preference().create(preference_data)
        f.write("Response: " + str(response) + "\n")
    except Exception as e:
        f.write("Error: " + str(e) + "\n")
