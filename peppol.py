import base64
import xmlrpc.client

from parse_pdf import parse_invoice

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

def get_sales_account_id(models, db, uid, api_key, code='700000'):
    account_id = models.execute_kw(
        db, uid, api_key,
        'account.account', 'search',
        [[('code', '=', code)]],
        {'limit': 1}
    )
    if not account_id:
        raise Exception("Sales account not found")

    # return sale code = sales rendered in Belgium (merchandises)
    return account_id[0]

# % put tax x % S on non active set tax to default
def get_sale_tax_id(models, db, uid, api_key, rate):
    tax_ids = models.execute_kw(
        db, uid, api_key,
        'account.tax', 'search',
        [[
            ('type_tax_use', '=', 'sale'),
            ('amount', '=', rate),
            ('active', '=', True)
        ]],
        {'limit': 1}
    )
    if not tax_ids:
        raise Exception(f"Sale tax {rate}% not found")
    return tax_ids[0]

def get_country_id(models, db, uid, api_key, country_code: str) -> int:
    country_ids = models.execute_kw(
        db, uid, api_key,
        'res.country', 'search',
        [[('code', '=', country_code)]],
        {'limit': 1}
    )
    if not country_ids:
        raise Exception(f"Country with code {country_code} not found")

    # return country id = Belgium = BE
    return country_ids[0]

def get_journal_id(models, db, uid, api_key, journal_code: str) -> int:
    journal_ids = models.execute_kw(
        db, uid, api_key,
        'account.journal', 'search',
        [[('code', '=', journal_code)]],
        {'limit': 1}
    )
    if not journal_ids:
        raise Exception(f"Journal with code {journal_code} not found")

    # return sales journal = VF = verkoopfacturen
    return journal_ids[0]

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

    country_id = get_country_id(models, db, uid, api_key, country_code='BE')

    if partner_id is None:
        partner_id = models.execute_kw(
            db, uid, api_key,
            'res.partner', 'create',
            [{
                'name': customer_info["name"],
                'street': customer_info["street"],
                'city': customer_info["city"],
                'zip': customer_info["zip"],
                'country_id': country_id,  # Belgium
                'vat': customer_info["vat"],
                'lang': 'nl_BE',
                'is_company': True,

                # Accounting settings for Peppol e-invoicing
                'invoice_sending_method': 'peppol',
                'invoice_edi_format': 'ubl_bis3',
            }]
        )
        print(f"Created partner with id: {partner_id}")

    return partner_id


def create_invoice_lines(models, uid, customer_totals: dict) -> list:

    account_id = get_sales_account_id(models, db, uid, api_key, code='700000')
    tax_id_0 = get_sale_tax_id(models, db, uid, api_key, 0.0)
    tax_id_6 = get_sale_tax_id(models, db, uid, api_key, 6.0)
    tax_id_21 = get_sale_tax_id(models, db, uid, api_key, 21.0)

    btw_0_total = float(customer_totals["btw_0_amount"])
    btw_6_total = float(customer_totals["btw_6_amount"])
    btw_21_total = float(customer_totals["btw_21_amount"])

    invoice_lines = []

    if btw_0_total > 0:
        line0 = (0, 0, {
            'name': 'Exempt/Zero rated',
            'quantity': 1,
            'price_unit': btw_0_total,
            'account_id': account_id,
            'tax_ids': [(6, 0, [tax_id_0])],
        })
        invoice_lines.append(line0)
    if btw_6_total > 0:
        line1 = (0, 0, {
            'name': 'Food and beverages',
            'quantity': 1,
            'price_unit': btw_6_total,
            'account_id': account_id,
            'tax_ids': [(6, 0, [tax_id_6])],
        })
        invoice_lines.append(line1)
    if btw_21_total > 0:
        line2 = (0, 0, {
            'name': 'Standard/Non food',
            'quantity': 1,
            'price_unit': btw_21_total,
            'account_id': account_id,
            'tax_ids': [(6, 0, [tax_id_21])],
        })
        invoice_lines.append(line2)

    return invoice_lines


def create_invoice(models, uid, partner_id: int, customer_meta: dict, invoice_lines: list, invoice_pdf: str = None) -> int:

    journal_id = get_journal_id(models, db, uid, api_key, journal_code='VF')

    invoice_id = models.execute_kw(
        db, uid, api_key,
        'account.move', 'create',
        [{
            'move_type': 'out_invoice',   # Customer sale invoice
            'journal_id': journal_id,     # Sales journal = VF = 8
            'partner_id': partner_id,         # Customer
            'invoice_date': customer_meta["invoice_date"],     # Invoice date
            'invoice_line_ids': invoice_lines, # Lines with taxes
        }]
    )

    print("Created invoice ID:", invoice_id)

    with open(invoice_pdf, "rb") as f:
        pdf_base64 = base64.b64encode(f.read()).decode('utf-8')

    attachment_id = models.execute_kw(
        db, uid, api_key,
        'ir.attachment', 'create',
        [{
            'name': invoice_pdf, # TODO: make viable name
            'type': 'binary',
            'datas': pdf_base64,
            'res_model': 'account.move',
            'res_id': invoice_id,
            'mimetype': 'application/pdf',
        }]
    )

    print(attachment_id)

    return invoice_id

def create_post_invoice(models, uid, invoice_pdf: str = None):

    try:
        # 2. Parse PDF and get customer data
        customer_metadata, customer_info, _, customer_totals = get_customer_data(invoice_pdf)
        print(get_create_partner(models, uid, api_key, customer_info))
        # 3. Get or create partner and create invoice lines
        partner_id = get_create_partner(models, uid, api_key, customer_info)
        invoice_lines = create_invoice_lines(models, uid, customer_totals)

        # 5. Create invoice
        invoice_id = create_invoice(models, uid, partner_id, customer_metadata, invoice_lines, invoice_pdf)

        # 6. Post it
        models.execute_kw(
            db, uid, api_key,
            'account.move', 'action_post',
            [[invoice_id]]
        )
        return True
    except Exception as e:
        print("Error creating and posting invoice:", str(e))
        return False


# if __name__ == "__main__":
#
#
#     # TODO: Send invoice via Peppol test somehow
#     # # Send invoice
#     # models.execute_kw(
#     #     db, uid, api_key,
#     #     'account.move', 'action_invoice_sent',
#     #     [[invoice_id]]
#     # )

