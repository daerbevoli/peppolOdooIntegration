import logging

import requests
import base64
import os
from parse_pdf import parse_invoice, generate_filename

class OdooClient:
    def __init__(self, url, db, username, api_key):
        self.url = f"{url}/json/2"
        self.db = db
        self.username = username
        self.api_key = api_key

        self.headers = {}



    def connect(self):
        """
        Validate JSON-2 API access.
        JSON-2 does NOT authenticate or return a uid.
        We verify access by calling a lightweight endpoint.
        """
        headers = {"Authorization": f"bearer {self.api_key}",
                    "X-Odoo-Database": self.db}
        try:
            res = requests.post(
                f"{self.url}/res.users/context_get",
                headers=headers,
                json={},  # no ids, @api.model method
                timeout=10,
            )

            if res.status_code != 200:
                raise PermissionError(
                    f"Authentication failed (status {res.status_code}): {res.text}"
                )

            # JSON-2 has no uid, but we can store user context
            user_context = res.json()
            if not user_context['uid']:
                raise PermissionError("Authentication failed: Check username/API key.")
            self.headers = headers
            return True

        except requests.RequestException as e:
            raise ConnectionError(f"Could not connect to Odoo JSON-2 API: {e}")

    #

    def get_json(self, model: str, method: str, domain: list):
        res = requests.post(
            f"{self.url}/{model}/{method}",
            headers=self.headers,
            json={
                "domain": domain,
                "limit": 1
            },
        )

        if res.status_code != 200:
            raise PermissionError(f"Authentication failed (status {res.status_code}): {res.text}")

        return res.json()


    """
    Helper function to get the ids of the internal fields.
    """
    def get_sales_account_id(self, code='700000'):
        domain = [["code", '=', code]]
        res = self.get_json(model='account.account', method='search', domain=domain)
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
        """
        Get the partner id if partner exists else create it.
        :param customer_info: info extracted from the pdf invoice
        :return: partner id
        """
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
            'phone': customer_info.get("phone") or False,

            'country_id': country_id,
            'vat': vat,
            'lang': 'nl_BE',
            'is_company': True,

            'invoice_sending_method': 'peppol',
            'invoice_edi_format': 'ubl_bis3',
        }])
        return new_id

    def create_invoice_lines(self, totals):
        """
        Create invoice lines based on totals.
        :param totals: the separate totals amount of the invoice
        :return: invoice lines
        """
        account_id = self.get_sales_account_id()  # Default 700000

        lines = []
        # Mapping: (rate, label)
        tax_map = [
            (0.0, 'Vrijgesteld'),
            (6.0, 'Voeding en levensmiddelen'),
            (21.0, 'Divers/non-food')
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

    def create_post_invoice(self, file_path):
        """
        Workflow: Parse -> Partner -> Lines -> Invoice -> Post
        Returns: invoice id, new filename, message
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
                'ref': meta.get("invoice_number"),
            }
            filename = generate_filename(meta, buyer)
            invoice_number = meta.get("invoice_number")

            if not invoice_number:
                return None, None, f"Invoice number missing in PDF {filename}"

            existing_invoice = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move', 'search',
                [[('move_type', '=', 'out_invoice'), ('ref', '=', invoice_number)]],
                {'limit': 1}
            )

            # check for duplicate
            if existing_invoice:
                invoice_id = existing_invoice[0]
                return invoice_id, filename, f"Invoice {invoice_number} already exists."

            invoice_id = self.models.execute_kw(self.db, self.uid, self.api_key,
                                                'account.move', 'create', [invoice_vals])

            # 4. Attach PDF (Clean filename)
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

            return invoice_id, filename, f"Invoice {invoice_number} created & posted."

        except Exception as e:
            return None, None, str(e)

    def send_peppol_verify(self, invoice_id):
        """
        Sends the invoice via peppol
        :param invoice_id: the id of the invoice
        :return: success/failure sending invoice, message
        """

        # 1. Fetch invoice and partner details
        invoice = self.models.execute_kw(
            self.db, self.uid, self.api_key,
            'account.move', 'read', [invoice_id],
            {'fields': ['state', 'peppol_move_state', 'partner_id']}
        )[0]

        partner_id = invoice['partner_id'][0]
        move_state = invoice.get('peppol_move_state')

        # 2. Verify Partner Peppol Status
        partner_data = self.models.execute_kw(
            self.db, self.uid, self.api_key,
            'res.partner', 'read', [partner_id],
            {'fields': ['peppol_verification_state']}
        )[0]

        partner_on_peppol = partner_data['peppol_verification_state']

        # Trigger verification if not done yet
        if partner_on_peppol not in ('valid', 'not_valid'):
            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'res.partner', 'button_account_peppol_check_partner_endpoint',
                [[partner_id]]
            )
            return False, "Peppol partner verification triggered. Please retry in a moment."

        if partner_on_peppol == 'not_valid':
            return False, "Partner is not on Peppol. Manual intervention required."

        # 3. Handle Peppol Transmission Logic
        if move_state == 'done':
            return True, "Invoice already sent via Peppol."

        if move_state == 'error':
            return False, "Peppol error detected. Manual intervention required."

        # 4. Initiate Peppol Send via Wizard
        action = self.models.execute_kw(
            self.db, self.uid, self.api_key,
            'account.move', 'action_invoice_sent', [[invoice_id]]
        )
        context = action.get('context', {})

        try:
            # 5. Prepare wizard values
            wizard_vals = {
                'move_id': invoice_id,
                'sending_methods': ['peppol'],
            }

            # Create the wizard record
            wizard_id = self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move.send.wizard', 'create', [wizard_vals],
                {'context': context}
            )

            # 6. Execute the send action
            self.models.execute_kw(
                self.db, self.uid, self.api_key,
                'account.move.send.wizard', 'action_send_and_print',
                [[wizard_id]],
                {'context': context}
            )

            return True, "Invoice sent via Peppol."


        except Exception as e:
            return False, f"Failed to initiate Peppol send: {str(e)}"

if __name__ == "__main__":
    # #
    # URL = os.getenv("ODOO_URL")
    # DB = os.getenv("ODOO_DB")
    # USERNAME = os.getenv("ODOO_USERNAME")
    # API_KEY = os.getenv("ODOO_API_KEY")
    #
    # client = OdooClient(URL, DB, USERNAME, API_KEY)
    #
    #
    client = OdooClient(
            url="https://skbctesting.odoo.com",
            db="skbctesting",
            username="skbc.bv@gmail.com",
            api_key="7e231b61aa3afc6c8c8fae66fcf60c35e22f4e2d"
        )

    print(client.connect())

    print(client.get_sales_account_id(code='700000'))
