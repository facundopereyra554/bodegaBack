from sqlmodel import select, Session
from database import engine_compras
from models import PurchaseRecord
import traceback

try:
    with Session(engine_compras) as session:
        purchases = session.exec(select(PurchaseRecord).order_by(PurchaseRecord.id.desc())).all()
        print("SUCCESS! Found", len(purchases))
except Exception as e:
    print("FAILED!")
    traceback.print_exc()
