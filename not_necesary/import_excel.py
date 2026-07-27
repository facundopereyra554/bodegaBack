import pandas as pd
import math
from sqlmodel import Session
from database import engine, create_db_and_tables
from models import Product

def is_nan(val):
    if isinstance(val, float) and math.isnan(val):
        return True
    return False

def clean_val(val):
    if is_nan(val):
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val)

def import_data():
    create_db_and_tables()
    df = pd.read_excel('data-vinos.xlsx')
    
    with Session(engine) as session:
        # Opcional: limpiar tabla antes de insertar
        # from sqlmodel import select
        # existing = session.exec(select(Product)).all()
        # for p in existing:
        #     session.delete(p)
        # session.commit()
        
        for index, row in df.iterrows():
            # Obtener datos de las columnas usando nombres aproximados por si la codificación falla
            row_dict = row.to_dict()
            keys = list(row_dict.keys())
            
            # Map columns
            col_marca = next(k for k in keys if 'Marca' in k)
            col_comp = next(k for k in keys if 'Composici' in k)
            col_cosecha = next(k for k in keys if 'Cosecha' in k)
            col_region = next(k for k in keys if 'Regi' in k)
            col_elev = next(k for k in keys if 'Elevaci' in k)
            col_pres = next(k for k in keys if 'Presentaci' in k)
            col_alc = next(k for k in keys if 'Alcohol' in k)
            col_acidez = next(k for k in keys if 'Acidez' in k)
            col_ph = next(k for k in keys if 'pH' in k)
            col_metodo = next(k for k in keys if 'todo de cosecha' in k)
            col_vini = next(k for k in keys if 'Vinificaci' in k)
            col_notas = next(k for k in keys if 'Notas de cata' in k)
            col_serv = next(k for k in keys if 'Servicio ideal' in k)
            col_stock = next(k for k in keys if 'Stock' in k)
            col_peso = next(k for k in keys if 'Peso' in k)
            col_medidas = next(k for k in keys if 'Medidas' in k)
            col_precio = next(k for k in keys if 'Precio' in k)
            
            marca = clean_val(row_dict[col_marca])
            comp = clean_val(row_dict[col_comp])
            name = f"{marca} - {comp}" if comp else marca
            
            stock_val = 0
            if not is_nan(row_dict[col_stock]):
                try:
                    stock_val = int(row_dict[col_stock])
                except ValueError:
                    stock_val = 0
                
            price_val = 0.0
            if not is_nan(row_dict[col_precio]):
                try:
                    price_val = float(row_dict[col_precio])
                except ValueError:
                    price_val = 0.0
            
            product = Product(
                name=name,
                category=marca,
                description="",
                long_description="",
                price=price_val,
                stock=stock_val,
                images=[],
                additional_info={},
                pack_info={
                    "pack_name": "Caja x6 Botellas",
                    "pack_price": price_val,
                    "pack_stock": stock_val
                },
                marca=marca,
                composicion=comp,
                cosecha=clean_val(row_dict[col_cosecha]),
                region=clean_val(row_dict[col_region]),
                elevacion=clean_val(row_dict[col_elev]),
                presentacion=clean_val(row_dict[col_pres]),
                alcohol=clean_val(row_dict[col_alc]),
                acidez=clean_val(row_dict[col_acidez]),
                ph=clean_val(row_dict[col_ph]),
                metodo_cosecha=clean_val(row_dict[col_metodo]),
                vinificacion=clean_val(row_dict[col_vini]),
                notas_de_cata=clean_val(row_dict[col_notas]),
                servicio_ideal=clean_val(row_dict[col_serv]),
                peso_caja=clean_val(row_dict[col_peso]),
                medidas_caja=clean_val(row_dict[col_medidas])
            )
            session.add(product)
        session.commit()
        print("Datos importados con éxito!")

if __name__ == "__main__":
    import_data()
