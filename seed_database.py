import json
from sqlmodel import Session
from database import engine, create_db_and_tables
from models import Product

def seed_data():
    print("Creando la base de datos y las tablas...")
    create_db_and_tables()
    print("Base de datos y tablas creadas")

    with open("products.json", "r", encoding="utf-8") as f:
        products_data = json.load(f)

    with Session(engine) as session:
        print("Llenando la base de datos con los productos del JSON")
        for product_dict in products_data:
            product = Product.model_validate(product_dict)
            session.add(product)
        session.commit()
        print("¡Datos insertados con éxito!")

if __name__ == "__main__":
    seed_data()