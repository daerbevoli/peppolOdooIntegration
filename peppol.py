
import requests

from parse_pdf import convert

API_KEY = "64a203a8-7fec-4d82-b533-3eeadb2911ac"
url = "https://api.sandbox.billit.be/v1/account/accountInformation"
CREATE_ENDPOINT = "https://api.sandbox.billit.be/v1/orders"
SEND_ENDPOINT = "https://api.sandbox.billit.be/v1/orders/commands/send"


headers = {
    "ApiKey": API_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json"
}

pdf_path = "Faktuur20251220102357.pdf"

# billit_invoice = convert(pdf_path)
#
# print(billit_invoice)


import json

with open("file.json", "r", encoding="utf-8") as f:
    data = json.load(f)



# Create invoice in Billit
resp = requests.post(CREATE_ENDPOINT, json=data, headers=headers)
print(f"Status Code: {resp.status_code}")
print(f"Response: {resp.text}")
# resp.raise_for_status()
# order_id = create_resp.json()["OrderID"]
# print("Invoice created with OrderID:", order_id)
#
# # Send invoice via PEPPOL
# send_payload = {"TransportType": "Peppol", "OrderIDs": [order_id]}
# send_resp = requests.post(SEND_ENDPOINT, json=send_payload, headers=headers)
# send_resp.raise_for_status()
# print("Invoice sent via PEPPOL successfully!")
