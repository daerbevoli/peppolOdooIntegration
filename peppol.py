import xmlrpc.client
import base64
import os
from parse_pdf import parse_invoice


class OdooClient:
    def __init__(self, url, db, username, api_key):
        self.url = url
        self.db = db
        self.username = username
        self.api_key = api_key
        self.common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        self.models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        self.uid = None

    def connect(self):
        """Authenticates with Odoo."""
        try:
            self.uid = self.common.authenticate(self.db, self.username, self.api_key, {})
            if not self.uid:
                raise PermissionError("Authentication failed: Check username/API key.")
            return True
        except Exception as e:
            raise ConnectionError(f"Could not connect to Odoo: {e}")

    def get_sales_account_id(self, code='700000'):
        res = self.models.execute_kw(self.db, self.uid, self.api_key,
                                     'account.account', 'search', [[('code', '=', code)]], {'limit': 1})
        if not res:
            raise ValueError(f"Sales account {code} not found in Odoo.")
        return res[0]

    def get_sale_tax_id(self, rate):
        res = self.models.execute_kw(self.db, self.uid, self.api_key,
            'account.tax', 'search',
            [[
                ('type_tax_use', '=', 'sale'),
                ('amount', '=', rate),
                ('active', '=', True)
            ]],
            {'limit': 1}
        )
        if not res:
            raise ValueError(f"Sales tax for rate {rate}% not found.")
        return res[0]

    def get_journal_id(self, code='VF'):
        res = self.models.execute_kw(self.db, self.uid, self.api_key,
                                     'account.journal', 'search', [[('code', '=', code)]], {'limit': 1})
        if not res:
            raise ValueError(f"Journal '{code}' not found.")
        return res[0]

    def get_country_id(self, code='BE'):
        res = self.models.execute_kw(self.db, self.uid, self.api_key,
                                     'res.country', 'search', [[('code', '=', code)]], {'limit': 1})
        if not res:
            raise ValueError(f"Country code '{code}' not found.")
        return res[0]

    def get_or_create_partner(self, customer_info):
        vat = customer_info.get("vat")

        # Search by VAT
        partner_id = self.models.execute_kw(self.db, self.uid, self.api_key,
                                                          'res.partner', 'search', [[('vat', '=', vat)]], {'limit': 1})

        if partner_id:
            return partner_id[0]

        # Create if not found
        country_id = self.get_country_id('BE')
        new_id = self.models.execute_kw(self.db, self.uid, self.api_key,
            'res.partner', 'create',
        [{
            'name': customer_info["name"],
            'street': customer_info.get("street"),
            'city': customer_info.get("city"),
            'zip': customer_info.get("zip"),
            'country_id': country_id,
            'vat': vat,
            'lang': 'nl_BE',
            'is_company': True,

            'invoice_sending_method': 'peppol',
            'invoice_edi_format': 'ubl_bis3',
        }])
        return new_id

    def create_invoice_lines(self, totals):
        account_id = self.get_sales_account_id()  # Default 700000

        lines = []
        # Mapping: (rate, label)
        tax_map = [
            (0.0, 'Exempt/Zero rated'),
            (6.0, 'Food and beverages'),
            (21.0, 'Standard/Non food')
        ]

        for rate, label in tax_map:
            # Construct key name based on your parser output (e.g. "btw_21_amount")
            key = f"btw_{int(rate)}_amount"
            amount = float(totals.get(key, 0))

            if amount > 0:
                tax_id = self.get_sale_tax_id(rate)
                lines.append((0, 0, {
                    'name': label,
                    'quantity': 1,
                    'price_unit': amount,
                    'account_id': account_id,
                    'tax_ids': [(6, 0, [tax_id])],
                }))

        if not lines:
            raise ValueError("No invoice lines created. Check parsed totals.")

        return lines

    def process_invoice(self, file_path):
        """
        Main workflow: Parse -> Partner -> Lines -> Invoice -> Post
        Returns: (Success: bool, Message: str)
        """
        try:
            # 1. Parse
            data = parse_invoice(file_path)
            meta, buyer, items, totals = data["metadata"], data["buyer"], data["items"], data["totals"]

            # 2. Partner & Lines
            partner_id = self.get_or_create_partner(buyer)
            invoice_lines = self.create_invoice_lines(totals)
            journal_id = self.get_journal_id('VF')

            # 3. Create Invoice Header
            invoice_vals = {
                'move_type': 'out_invoice',
                'journal_id': journal_id,
                'partner_id': partner_id,
                'invoice_date': meta.get("invoice_date"),
                'invoice_line_ids': invoice_lines,
            }

            invoice_id = self.models.execute_kw(self.db, self.uid, self.api_key,
                                                'account.move', 'create', [invoice_vals])

            # 4. Attach PDF (Clean filename)
            filename = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                pdf_content = base64.b64encode(f.read()).decode('utf-8')

            self.models.execute_kw(self.db, self.uid, self.api_key,
                'ir.attachment', 'create',
            [{
                'name': filename,
                'type': 'binary',
                'datas': pdf_content,
                'res_model': 'account.move',
                'res_id': invoice_id,
                'mimetype': 'application/pdf',
            }])

            # 5. Post Invoice
            self.models.execute_kw(self.db, self.uid, self.api_key,
                                   'account.move', 'action_post', [[invoice_id]])

            return True, f"Invoice {invoice_id} created & posted."

        except Exception as e:
            # Return the error message so the UI can log it
            return False, str(e)

# This mimics your old interface but uses the class internally
def create_post_invoice(odoo_client, file_path):
    # Notice we pass the client object, not raw models/uid
    return odoo_client.process_invoice(file_path)


if __name__ == "__main__":
    client = OdooClient(
            url="https://skbctesting.odoo.com",
            db="skbctesting",
            username="skbc.bv@gmail.com",
            api_key="7e231b61aa3afc6c8c8fae66fcf60c35e22f4e2d"
        )
    client.connect()
    invoice = client.process_invoice("Factuur/20260101163627Faktuur.pdf")
    print(invoice)