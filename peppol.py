import base64
import xmlrpc.client

from parse_pdf import parse_invoice

invoice_data = parse_invoice("Factuur/KuldeviBv_19122025_7216.pdf")

print(invoice_data)

url = "https://skbctesting.odoo.com"
db = "skbctesting"
username = "skbc.bv@gmail.com"
api_key = "7e231b61aa3afc6c8c8fae66fcf60c35e22f4e2d"

common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
uid = common.authenticate(db, username, api_key, {})

if uid:
    print("connected to db")
else:
    print("failed to authenticate")

models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


vat = invoice_data["buyer"]["vat"]

partner_id = models.execute_kw(
    db, uid, api_key,
    'res.partner', 'search',
    [[('vat', '=', vat)]],
    {'limit': 1}
)

partner_id = partner_id[0] if partner_id else None

print(partner_id)

tax_ids = models.execute_kw(
    db, uid, api_key,
    'account.tax', 'search_read',
    [[]],  # no filter, get all taxes
    {'fields': ['name', 'amount', 'type_tax_use']}
)
print(tax_ids)

# tax ids, 2 = 21%, 6 = 6%, account id = 374 = Sales rendered in Belgium (marchandises)

line1 = (0, 0, {
    'name': 'Service A',
    'quantity': 1,
    'price_unit': float(invoice_data["totals"]["btw_21_amount"]),
    'account_id': 374,
    'tax_ids': [(6, 0, [2])],
})

line2 = (0, 0, {
    'name': 'Service B',
    'quantity': 1,
    'price_unit': float(invoice_data["totals"]["btw_6_amount"]),
    'account_id': 374,
    'tax_ids': [(6, 0, [6])],  # Different VAT
})

invoice_lines = [line1, line2]


account_ids = models.execute_kw(
    db, uid, api_key,
    'account.account', 'search',
    [[('name', '=', 'Sales rendered in Belgium (marchandises)')]],
    {'limit': 1}
)
account_id = account_ids[0] if account_ids else None

print("account_id:", account_id)

invoice_id = models.execute_kw(
    db, uid, api_key,
    'account.move', 'create',
    [{
        'move_type': 'out_invoice',       # Customer invoice
        'partner_id': partner_id,         # Customer
        'invoice_date': invoice_data["metadata"]["invoice_date"],     # Invoice date
        'invoice_line_ids': invoice_lines, # Lines with taxes
        'ref': 'INV-2026-001',            # Optional: your invoice number
    }]
)

print("Created invoice ID:", invoice_id)

with open("Factuur/KuldeviBv_19122025_7216.pdf", "rb") as f:
    pdf_base64 = base64.b64encode(f.read()).decode('utf-8')

attachment_id = models.execute_kw(
    db, uid, api_key,
    'ir.attachment', 'create',
    [{
        'name': 'Invoice 2026-001.pdf',
        'type': 'binary',
        'datas': pdf_base64,
        'res_model': 'account.move',
        'res_id': invoice_id,
        'mimetype': 'application/pdf',
    }]
)
