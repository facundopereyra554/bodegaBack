import pandas as pd
df = pd.read_excel('data-vinos.xlsx')
print("Columns:")
print(df.columns.tolist())
print("\nFirst row:")
print(df.iloc[0].to_dict())
