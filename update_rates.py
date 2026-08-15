import requests
import json
import pandas as pd
from datetime import datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')

terms = {
    "1": "Không kỳ hạn",
    "2": "< 1 tháng",
    "3": "01 tháng",
    "4": "02 tháng",
    "5": "03 tháng",
    "8": "06 tháng",
    "11": "09 tháng",
    "14": "12 tháng",
    "18": "24 tháng",
    "19": "36 tháng"
}

banks = "VCB,BID,CTG,Agribank,MBB,TCB,VPB,ACB,HDB,SHB,TPB,VIB,MSB,LPB,STB,EIB,OCB,SSB,SCB,NAB,ABB,BVB,KLB,NVB,PGB,VAB,VBB,BAB,SGB"

all_data = []

headers = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://vnsignal.vn",
    "Referer": "https://vnsignal.vn/"
}

print("Fetching data...")
for term_id, term_name in terms.items():
    url = "https://apiv2.vnsignal.vn/macro/bank-interest/comparison"
    params = {
        "termIDs": term_id,
        "days": "2000",
        "banks": banks
    }
    r = requests.get(url, params=params, headers=headers)
    if r.status_code == 200:
        resp = r.json()
        data = resp.get("data", resp) if isinstance(resp, dict) else resp
        if isinstance(data, list):
            for d in data:
                d['TermName'] = term_name # Ensure consistency
            all_data.extend(data)
            print(f"Fetched {len(data)} records for {term_name}")
    else:
        print(f"Failed to fetch {term_name}: {r.status_code}")

print(f"Total records: {len(all_data)}")

# Calculate TRUNG BÌNH (Average)
df = pd.DataFrame(all_data)
if not df.empty and 'ReportTime' in df.columns:
    df['InterestRate'] = pd.to_numeric(df['InterestRate'], errors='coerce')
    # Group by ReportTime and TermName to get the mean
    avg_df = df.groupby(['ReportTime', 'TermName', 'InterestTermID']).agg({'InterestRate': 'mean'}).reset_index()
    avg_df['BankName'] = 'TRUNG BÌNH'
    avg_df['DateConvert'] = df.groupby(['ReportTime', 'TermName'])['DateConvert'].first().values
    avg_df['DisplayOrder'] = 0
    # Format InterestRate to 4 decimal places as string to match original
    avg_df['InterestRate'] = avg_df['InterestRate'].apply(lambda x: f"{x:.4f}")
    
    # Convert to dict and extend all_data
    avg_records = avg_df.to_dict('records')
    all_data.extend(avg_records)

# Save raw data
with open("lai_suat_all.json", "w", encoding="utf-8") as f:
    json.dump(all_data, f, ensure_ascii=False)


