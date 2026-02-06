import pdfplumber
import re
from typing import List, Dict, Any

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
    # Matches 'Factuur 7216'
    inv_match = re.search(r"Faktuur\s+(\d+)", text)
    # Matches 'Datum 19-12-2025'
    date_match = re.search(r"Datum\s+(\d{2}-\d{2}-\d{4})", text)
    new_date_str = None
    if date_match:
        date = date_match.group(1)
        dd, mm, yyyy = date.split("-")
        new_date_str = f"{yyyy}-{mm}-{dd}"

    return {
        "invoice_number": inv_match.group(1) if inv_match else None,
        "invoice_date": new_date_str if date_match else None,
    }

def extract_buyer_info(page) -> Dict[str, Any]:
    """Extracts buyer details by finding the postal code anchor."""
    width, height = page.width, page.height
    # Focus on the top right quadrant where buyer info resides
    right_box = (width * 0.45, 0, width, height * 0.4)
    text = page.crop(right_box).extract_text()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    info = {"name": None, "street": None, "zip": None, "city": None, "phone": None, "vat": None}

    # 1. Regex patterns for specific identifiers
    vat_pattern = re.compile(r"BE\s?[\d.]{10,14}", re.IGNORECASE)
    phone_pattern = re.compile(r"(?:Tel|Mobile|GSM|Telefoon|Phone)[\s.:]*(?P<num>[\d.\s/-]{8,})", re.IGNORECASE)
    zip_city_pattern = re.compile(r"^(?P<zip>\d{4})\s+(?P<city>.+)$")

    for i, line in enumerate(lines):
        # Match VAT
        vat_match = vat_pattern.search(line)
        phone_match = phone_pattern.search(line)
        if vat_match:
            info["vat"] = vat_match.group(0).replace(" ", "").replace(".", "")
            continue

        if phone_match:
            info["phone"] = phone_match.group("num").strip()
            continue

        # Match Address Anchor (e.g., 9200 DENDERMONDE)
        zip_match = zip_city_pattern.match(line)
        if zip_match:
            info["zip"] = zip_match.group("zip")
            info["city"] = zip_match.group("city")
            # Logic: Street is 1 line above, Name is 2 lines above
            if i - 1 >= 0: info["street"] = lines[i - 1]
            if i - 2 >= 0: info["name"] = lines[i - 2]

    return info

def extract_items(page) -> List[Dict]:
    """Extracts line items using a non-greedy regex to handle mid-line descriptions."""
    text = page.extract_text()
    lines = text.splitlines()
    items = []

    # Pattern: Qty -> Description -> Total Bedrag -> Unit Prijs
    # Ex: "2 Duck Roasted Boneless 650g, 15,00 € 7,50 €"
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
        "basis": 0.0,
        "btw_0": 0.0,
        "btw_6": 0.0,
        "btw_21": 0.0,
        "total": 0.0
    }

    # Extract Basis
    basis_match = re.search(r"Basis\s+([\d.,]+)\s*€", text)
    if basis_match:
        totals["basis"] = parse_eu_float(basis_match.group(1))

    # Extract BTW 0%
    btw0_match = re.search(r"Btw 0% op ([\d.,]+)\s*€\s+([\d.,]+)\s*€", text)
    if btw0_match:
        totals["btw_0"] = parse_eu_float(btw0_match.group(1))

    # Extract BTW 6%
    btw6_match = re.search(r"Btw 6% op ([\d.,]+)\s*€\s+([\d.,]+)\s*€", text)
    if btw6_match:
        totals["btw_6"] = parse_eu_float(btw6_match.group(1))

    # Extract BTW 21%
    btw21_match = re.search(r"Btw 21% op ([\d.,]+)\s*€\s+([\d.,]+)\s*€", text)
    if btw21_match:
        totals["btw_21"] = parse_eu_float(btw21_match.group(1))

    # Extract Total
    total_match = re.search(r"Totaal\s+([\d.,]+)\s*€", text)
    if total_match:
        totals["total"] = parse_eu_float(total_match.group(1))

    return totals

def parse_invoice(pdf_path: str) -> Dict:
    items_all = []
    full_text_all = ""

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
            full_text_all += text + "\n"

            # Extract items from every page
            items_all.extend(extract_items(page))

        # Metadata and buyer info from first page only
        metadata = extract_invoice_metadata(pdf.pages[0].extract_text())
        buyer = extract_buyer_info(pdf.pages[0])

        # Totals from all text
        totals = extract_totals(full_text_all)

    return {
        "metadata": metadata,
        "buyer": buyer,
        "items": items_all,
        "totals": totals
    }

def generate_filename(metadata, buyer):
    """
    Creates a safe filename: Company_YYYYMMDD_InvNum.pdf
    """
    # Clean company name
    safe_company = "".join([c for c in buyer['name'] if c.isalnum() or c in (' ', '')]).strip().replace(" ", "")

    # Clean date (Remove separators like / - .)
    safe_date = re.sub(r"[-./]", "", metadata['invoice_date'])

    return f"{safe_company}_{safe_date}_{metadata['invoice_number']}.pdf"


# if __name__ == "__main__":
#     PDF_PATH = "Factuur_processed/20260106160928Faktuur.pdf"
#     data = parse_invoice(PDF_PATH)
#
#     print(data)




