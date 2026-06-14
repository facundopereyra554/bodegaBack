from database import create_db_and_tables
create_db_and_tables()

from sqlmodel import Session, select
from database import engine_compras
from models import PurchaseRecord
import traceback

try:
    with Session(engine_compras) as session:
        purchases = session.exec(select(PurchaseRecord)).all()
        if not isinstance(purchases, list):
            purchases = list(purchases)
        purchases.reverse()
        print("SUCCESS!", len(purchases))
except Exception as e:
    print("FAILED!")
    traceback.print_exc()
