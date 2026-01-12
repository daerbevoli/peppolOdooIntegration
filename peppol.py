import base64
import xmlrpc.client

from parse_pdf import parse_invoice, generate_filename

url = "https://skbctesting.odoo.com"
db = "skbctesting"
username = "skbc.bv@gmail.com"
api_key = "7e231b61aa3afc6c8c8fae66fcf60c35e22f4e2d"


def connect():
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})

    if uid:
        print("connected to db")
    else:
        print("failed to authenticate")

    return xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"), uid


def get_customer_data(invoice_pdf: str) -> tuple:
    data = parse_invoice(invoice_pdf)
    print(data)
    return data["metadata"], data["buyer"], data["items"], data["totals"]



def get_create_partner(models, uid, api_key, customer_info: dict) -> int:
    vat = customer_info["vat"]

    partner_id = models.execute_kw(
        db, uid, api_key,
        'res.partner', 'search',
        [[('vat', '=', vat)]],
        {'limit': 1}
    )

    partner_id = partner_id[0] if partner_id else None

    print(f"partner id: {partner_id}")

    if partner_id is None:
        partner_id = models.execute_kw(
            db, uid, api_key,
            'res.partner', 'create',
            [{
                'name': customer_info["name"],
                'street': customer_info["street"],
                'city': customer_info["city"],
                'zip': customer_info["zipcode"],
                'country_id': 21,  # Belgium
                'vat': customer_info["vat"],
            }]
        )
        print(f"Created partner with id: {partner_id}")

    return partner_id

def create_invoice_lines(customer_totals: dict) -> list:

    line1 = (0, 0, {
        'name': 'Standard/Non food',
        'quantity': 1,
        'price_unit': float(customer_totals["btw_21_amount"]),
        'account_id': 374, # account id = 374 = Sales rendered in Belgium (merchandises)
        'tax_ids': [(6, 0, [2])], # tax id: 2 = 21% S
    })

    line2 = (0, 0, {
        'name': 'Food and beverages',
        'quantity': 1,
        'price_unit': float(customer_totals["btw_6_amount"]),
        'account_id': 374, # account id = 374 = Sales rendered in Belgium (merchandises)
        'tax_ids': [(6, 0, [5])],  # tax id: 5 = 6% S
    })

    return [line1, line2]


def create_invoice(partner_id: int, customer_meta: dict, invoice_lines: list):
    invoice_id = models.execute_kw(
        db, uid, api_key,
        'account.move', 'create',
        [{
            'move_type': 'out_invoice',       # Customer invoice
            'partner_id': partner_id,         # Customer
            'invoice_date': customer_meta["invoice_date"],     # Invoice date
            'invoice_line_ids': invoice_lines, # Lines with taxes
        }]
    )

    print("Created invoice ID:", invoice_id)

    with open("20251230164920Faktuur.pdf", "rb") as f:
        pdf_base64 = base64.b64encode(f.read()).decode('utf-8')

    attachment_id = models.execute_kw(
        db, uid, api_key,
        'ir.attachment', 'create',
        [{
            'name': "20251230164920Faktuur.pdf",
            'type': 'binary',
            'datas': pdf_base64,
            'res_model': 'account.move',
            'res_id': invoice_id,
            'mimetype': 'application/pdf',
        }]
    )

    print(attachment_id)

    return invoice_id

if __name__ == "__main__":
    # 1. Connect to Odoo
    models, uid = connect()

    # 2. Parse PDF and get customer data
    customer_metadata, customer_info, _, customer_totals = get_customer_data(invoice_pdf="20251230164920Faktuur.pdf")

    # 3. Get or create partner and create invoice lines
    partner_id = get_create_partner(models, uid, api_key, customer_info)
    invoice_lines = create_invoice_lines(customer_totals)

    # 5. Create invoice
    invoice_id = create_invoice(partner_id, customer_metadata, invoice_lines)

    # 6. Post it
    models.execute_kw(
        db, uid, api_key,
        'account.move', 'action_post',
        [[invoice_id]]
    )

