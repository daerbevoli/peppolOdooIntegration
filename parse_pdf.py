import pdfplumber
import re
import datetime
from typing import List, Dict, Any
import json

def parse_eu_float(val: str) -> float:
    """Safely converts European formatted strings (1.234,56) to floats."""
    if not val:
        return 0.0
    # Remove currency symbols and spaces
    clean_val = val.replace("€", "").strip()
    # Remove thousands separator (dot) and replace decimal (comma) with dot
    clean_val = clean_val.replace(".", "").replace(",", ".")
    try:
        return float(clean_val)
    except ValueError:
        return 0.0

def extract_invoice_metadata(text: str) -> Dict:
    """Uses regex to find standard Belgian invoice headers."""
    # Matches 'Faktuur 7216' [cite: 8]
    inv_match = re.search(r"Faktuur\s+(\d+)", text)
    # Matches 'Datum 19-12-2025' [cite: 9]
    date_match = re.search(r"Datum\s+(\d{2}-\d{2}-\d{4})", text)

    return {
        "invoice_number": inv_match.group(1) if inv_match else None,
        "invoice_date": date_match.group(1) if date_match else None
    }

def extract_buyer_info(page) -> Dict[str, Any]:
    """Extracts buyer details by finding the postal code anchor[cite: 7]."""
    width, height = page.width, page.height
    # Focus on the top right quadrant where buyer info typically resides [cite: 5, 6, 7]
    right_box = (width * 0.45, 0, width, height * 0.4)
    text = page.crop(right_box).extract_text()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    info = {"name": None, "street": None, "zipcode": None, "city": None, "phone": None, "vat": None}

    # 1. Regex patterns for specific identifiers
    vat_pattern = re.compile(r"BE\s?[\d.]{10,14}", re.IGNORECASE)
    phone_pattern = re.compile(r"(?:Tel|Mobile|GSM|Telefoon|Phone)[\s.:]*(?P<num>[\d.\s/-]{8,})", re.IGNORECASE)
    zip_city_pattern = re.compile(r"^(?P<zip>\d{4})\s+(?P<city>.+)$")

    for i, line in enumerate(lines):
        # Match VAT [cite: 12]
        vat_match = vat_pattern.search(line)
        phone_match = phone_pattern.search(line)
        if vat_match:
            info["vat"] = vat_match.group(0)
            continue

        if phone_match:
            info["phone"] = phone_match.group("num").strip()
            continue

        # Match Address Anchor (e.g., 9200 DENDERMONDE) [cite: 7]
        zip_match = zip_city_pattern.match(line)
        if zip_match:
            info["zipcode"] = zip_match.group("zip")
            info["city"] = zip_match.group("city")
            # Logic: Street is 1 line above [cite: 6], Name is 2 lines above [cite: 5]
            if i - 1 >= 0: info["street"] = lines[i - 1]
            if i - 2 >= 0: info["name"] = lines[i - 2]

    return info

def extract_items(page) -> List[Dict]:
    """Extracts line items using a non-greedy regex to handle mid-line descriptions."""
    text = page.extract_text()
    lines = text.splitlines()
    items = []

    # Pattern: Qty -> Description -> Total Bedrag -> Unit Prijs
    # Example: "2 Duck Roasted Boneless 650g, 15,00 € 7,50 €"
    item_pattern = re.compile(
        r"^\s*\"?(?P<qty>\d+)\"?\s+"  # Qty
        r"\"?(?P<desc>.+?)\"?\s+"  # Description (non-greedy)
        r"(?P<total>[\d.,]+\s*€)\s+"  # Total amount
        r"(?P<unit>[\d.,]+\s*€)"  # Unit price
    )

    for line in lines:
        match = item_pattern.search(line)
        if match:
            items.append({
                "quantity": int(match.group("qty")),
                "description": match.group("desc").strip(", "),
                "unit_price": parse_eu_float(match.group("unit")),
                "total": parse_eu_float(match.group("total"))
            })

    return items

def extract_totals(text: str) -> Dict[str, Any]:
    """Extracts summary totals (Basis, BTW, Totaal) from the footer table."""
    totals = {
        "basis_amount": 0.0,
        "btw_0_amount": 0.0,
        "btw_6_amount": 0.0,
        "btw_21_amount": 0.0,
        "total_amount": 0.0
    }

    # Extract Basis
    basis_match = re.search(r"Basis\s+([\d.,]+)\s*€", text)
    if basis_match:
        totals["basis_amount"] = parse_eu_float(basis_match.group(1))

    # Extract BTW 0% (Added for robustness)
    btw0_match = re.search(r"Btw 0% op [\d.,]+\s*€\s+([\d.,]+)\s*€", text)
    if btw0_match:
        totals["btw_0_amount"] = parse_eu_float(btw0_match.group(1))

    # Extract BTW 6%
    btw6_match = re.search(r"Btw 6% op [\d.,]+\s*€\s+([\d.,]+)\s*€", text)
    if btw6_match:
        totals["btw_6_amount"] = parse_eu_float(btw6_match.group(1))

    # Extract BTW 21%
    btw21_match = re.search(r"Btw 21% op [\d.,]+\s*€\s+([\d.,]+)\s*€", text)
    if btw21_match:
        totals["btw_21_amount"] = parse_eu_float(btw21_match.group(1))

    # Extract Total
    total_match = re.search(r"Totaal\s+([\d.,]+)\s*€", text)
    if total_match:
        totals["total_amount"] = parse_eu_float(total_match.group(1))

    return totals

def parse_invoice(pdf_path: str) -> Dict:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        full_text = page.extract_text()

        return {
            "metadata": extract_invoice_metadata(full_text),
            "buyer": extract_buyer_info(page),
            "items": extract_items(page),
            "totals": extract_totals(full_text)
        }

def pdf_to_billit(parsed_invoice: Dict) -> Dict:
    # Datums omzetten naar YYYY-MM-DD
    invoice_dt = datetime.datetime.strptime(parsed_invoice["metadata"]["invoice_date"], "%d-%m-%Y")
    due_dt = invoice_dt + datetime.timedelta(days=30)

    # OrderLines opbouwen volgens de tabel (UnitPriceExcl & VATPercentage)
    order_lines = []
    for item in parsed_invoice["items"]:
        order_lines.append({
            "Quantity": item["quantity"],
            "UnitPriceExcl": item["unit_price"],
            "Description": item["description"],
        })

    # De JSON bouwen volgens jouw specifieke documentatie
    payload = {
        "OrderType": "Invoice",
        "OrderDirection": "Income", # Per doc: Sales = Income
        "OrderNumber": parsed_invoice["metadata"]["invoice_number"],
        "OrderDate": invoice_dt.strftime("%Y-%m-%d"),
        "ExpiryDate": due_dt.strftime("%Y-%m-%d"),
        "Currency": "EUR",
        "TotalAmount": parsed_invoice["totals"]["total_amount"],
        "TotalVatAmount": (
                parsed_invoice["totals"]["btw_0_amount"] +
                parsed_invoice["totals"]["btw_6_amount"] +
                parsed_invoice["totals"]["btw_21_amount"]
        ),
        "Customer": {
            "Name": parsed_invoice["buyer"]["name"],
            "VATNumber": parsed_invoice["buyer"]["vat"].replace(".", "").replace(" ", "") if parsed_invoice["buyer"]["vat"] else "",
            "PartyType": "Customer",
            "Addresses": [
                {
                    "AddressType": "InvoiceAddress",
                    "Name": parsed_invoice["buyer"]["name"],
                    "Street": parsed_invoice["buyer"]["street"] or "",
                    "City": parsed_invoice["buyer"]["city"] or "",
                    "Zipcode": parsed_invoice["buyer"]["zipcode"] or "",
                    "CountryCode": "BE"
                }
            ]
        },
        "OrderLines": order_lines
    }
    return payload

def convert(pdf_path: str) -> Dict[str, Any]:
    data = parse_invoice(pdf_path)
    billit_invoice = pdf_to_billit(data)

    return billit_invoice


if __name__ == "__main__":
    PDF_PATH = "Faktuur20251220102357.pdf"
    data = parse_invoice(PDF_PATH)
    billit_invoice = pdf_to_billit(data)
    print(billit_invoice)
