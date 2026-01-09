import pandas as pd
import numpy as np
from pathlib import Path

# Input and output paths
input_path = Path("testdata/Shift_Plan_Example_2.xlsx")
output_path = Path("testdata/Shift_plan_Example_3.xlsx")

# Read the Excel file
xls = pd.ExcelFile(input_path)
sheets = {}

# Process each sheet
for sheet_name in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=sheet_name)
    if sheet_name == "Modulation":
        # Modify Modulation sheet
        new_rows = []
        for _, row in df.iterrows():
            date = row["Date"]
            from_time = pd.to_datetime(row["From"], errors="coerce")
            to_time = pd.to_datetime(row["To"], errors="coerce")
            if pd.isna(from_time) or pd.isna(to_time):
                # If no From/To, keep as is
                new_rows.append(row)
                continue
            start_hour = from_time.hour
            end_hour = to_time.hour
            if end_hour < start_hour:
                end_hour += 24  # Handle overnight, but assuming not
            revenue = row["Revenue"]
            for hour in range(start_hour, end_hour):
                new_row = row.copy()
                new_row["From"] = f"{hour:02d}:00:00"
                new_row["To"] = f"{(hour + 1) % 24:02d}:00:00"
                # Divide revenue by 4 with small random deviation
                deviation = np.random.uniform(-0.1, 0.1)
                new_row["Revenue"] = revenue / 4 * (1 + deviation)
                # Set HC Key
                if hour == 8 or hour == 19:
                    new_row["HC Key"] = 1
                else:
                    new_row["HC Key"] = 0
                new_rows.append(new_row)
        df = pd.DataFrame(new_rows)
    elif sheet_name == "Opening Hours":
        # Modify Opening Hours sheet - spread to single hours
        new_rows = []
        for _, row in df.iterrows():
            date = row["Date"]
            from_time = pd.to_datetime(row["From"], errors="coerce")
            to_time = pd.to_datetime(row["To"], errors="coerce")
            if pd.isna(from_time) or pd.isna(to_time):
                # If no From/To, keep as is
                new_rows.append(row)
                continue
            start_hour = from_time.hour
            end_hour = to_time.hour
            if end_hour < start_hour:
                end_hour += 24  # Handle overnight
            
            # Get the number of hours in the original block
            num_hours = end_hour - start_hour
            
            # Get headcount values
            store_manager = row["Store Manager"]
            sales = row["Sales"]
            checkout = row["Checkout"]
            
            for hour in range(start_hour, end_hour):
                new_row = row.copy()
                new_row["From"] = f"{hour:02d}:00:00"
                new_row["To"] = f"{(hour + 1) % 24:02d}:00:00"
                new_row["open hours"] = "01:00:00"
                
                # Distribute headcount with small random deviation
                deviation = np.random.uniform(-0.1, 0.1)
                new_row["Store Manager"] = int(round(store_manager / num_hours * (1 + deviation)))
                new_row["Sales"] = int(round(sales / num_hours * (1 + deviation)))
                new_row["Checkout"] = int(round(checkout / num_hours * (1 + deviation)))
                
                new_rows.append(new_row)
        df = pd.DataFrame(new_rows)
    sheets[sheet_name] = df

# Write to new Excel file
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)

print(f"Generated {output_path}")