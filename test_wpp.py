from notifications import send_whatsapp_admin_alert

customer_data = {
    "name": "Test",
    "last_name": "User",
    "whatsapp": "5493870000000",
    "email": "test@test.com",
    "address": "Calle Falsa 123",
    "zip_code": "4400"
}
items = [
    {"title": "Vino Malbec", "quantity": 2}
]
total_paid = 15000

print("Probando envío de WhatsApp desde el servidor...")
send_whatsapp_admin_alert(customer_data, items, total_paid)
