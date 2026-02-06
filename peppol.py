import base64
import os
import requests
from parse_pdf import parse_invoice, generate_filename


class OdooClientError(Exception):
    pass

class OdooClient:
    """
    Client that connect to the Odoo database via the external JSON-2 API.
    JSON-2
    """
    def __init__(self, url: str, db: str, api_key: str, timeout: int = 15):
        self.base_url = f"{url.rstrip('/')}/json/2"
        self.db = db
        self.api_key = api_key
        self.timeout = timeout

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Odoo-Database": self.db,
            "Content-Type": "application/json",
        }

    def connect(self):
        """
        Validate JSON-2 API access.
        JSON-2 does NOT authenticate or return a uid.
        We verify access by calling a lightweight endpoint.
        """
        try:
            res = requests.post(
                f"{self.base_url}/res.users/context_get",
                headers=self.headers,
                json={},
                timeout=self.timeout,
            )

            if res.status_code != 200:
                raise PermissionError(
                    f"Authentication failed (status {res.status_code}): {res.text}"
                )

            # JSON-2 has no uid, but we can store user context
            user_context = res.json()
            if not user_context['uid']:
                raise PermissionError("Authentication failed: Check username/API key.")
            return True

        except requests.RequestException as e:
            raise ConnectionError(f"Could not connect to Odoo JSON-2 API: {e}")

    #

    def _call(self, model: str, method: str, payload: dict):
        try:
            res = requests.post(
                f"{self.base_url}/{model}/{method}",
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise OdooClientError(f"Connection error: {e}")

        if res.status_code != 200:
            raise OdooClientError(
                f"HTTP {res.status_code} {model}.{method}: {res.text}"
            )

        data = res.json()

        if isinstance(data, dict) and data.get("error"):
            raise OdooClientError(data["error"])

        return data

    # --------------------------------------------------
    # Common ORM helpers
    # --------------------------------------------------
    def search(self, model, domain: list, limit=1):
        payload = {"domain": domain}
        if limit:
            payload["limit"] = limit
        return self._call(model, "search", payload)

    def read(self, model, ids, fields):
        return self._call(model, "read", {
            "ids": ids,
            "fields": fields,
        })

    def create(self, model, vals, **kwargs):
        payload = {"vals_list": [vals]}
        payload.update(kwargs)
        ids = self._call(model, "create", payload)
        return ids[0]

    def button(self, model, method, ids, **kwargs):
        payload = {"ids": ids, "context": {}}
        payload.update(kwargs)
        return self._call(model, method, payload)

    # --------------------------------------------------
    # Master data helpers
    # --------------------------------------------------

    def get_sales_account_id(self, code: str = "700000") -> int:
        ids = self.search(
            model="account.account",
            domain=[["code", "=", code]],
            limit=1,
        )
        if not ids:
            raise ValueError(f"Sales account {code} not found in Odoo.")
        return ids[0]

    def get_sale_tax_id(self, rate: float) -> int:
        ids = self.search(
            model="account.tax",
            domain=[
                ["type_tax_use", "=", "sale"],
                ["amount", "=", rate],
                ["active", "=", True],
            ],
            limit=1,
        )
        if not ids:
            raise ValueError(f"Sales tax for rate {rate}% not found.")
        return ids[0]

    def get_journal_id(self, code: str = "VF") -> int:
        ids = self.search(
            model="account.journal",
            domain=[["code", "=", code]],
            limit=1,
        )
        if not ids:
            raise ValueError(f"Journal '{code}' not found.")
        return ids[0]

    def get_country_id(self, code: str = "BE") -> int:
        ids = self.search(
            model="res.country",
            domain=[["code", "=", code]],
            limit=1,
        )
        if not ids:
            raise ValueError(f"Country code '{code}' not found.")
        return ids[0]

    def get_or_create_partner(self, customer_info: dict) -> int:
        vat = customer_info.get("vat")
        if not vat:
            raise ValueError("Customer VAT number is required.")

        # Search by VAT
        ids = self.search(
            model="res.partner",
            domain=[["vat", "=", vat]],
            limit=1,
        )
        if ids:
            return ids[0]

        # Create partner
        country_id = self.get_country_id("BE")

        return self.create(
            model="res.partner",
            vals={
                "name": customer_info["name"],
                "street": customer_info.get("street"),
                "city": customer_info.get("city"),
                "zip": customer_info.get("zip"),
                "phone": customer_info.get("phone") or False,
                "country_id": country_id,
                "vat": vat,
                "lang": "nl_BE",
                "is_company": True,
                "invoice_sending_method": "peppol",
                "invoice_edi_format": "ubl_bis3",
            },
        )

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
            key = f"btw_{int(rate)}"
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
        Creates and post invoice from filepath
        :param file_path: path of invoice file
        :return: invoice id
        """

        # 1. Parse invoice and extract data
        invoice_data = parse_invoice(file_path)
        meta, buyer, _, totals =  invoice_data["metadata"], invoice_data["buyer"], invoice_data["items"], invoice_data["totals"]

        # Get partner and journal id
        partner_id = self.get_or_create_partner(buyer)
        journal_id = self.get_journal_id("VF")

        # If no invoice number or date -> invoice invalid
        invoice_number = meta.get("invoice_number")
        invoice_date = meta.get("invoice_date")
        if not invoice_number or not invoice_date:
            raise OdooClientError(f"Missing crucial invoice data")

        filename = generate_filename(meta, buyer)

        # 3. Check for duplicate -> return original invoice id
        existing = self.search(
            model="account.move",
            domain=[["move_type", "=", "out_invoice"], ["ref", "=", invoice_number]],
            limit=1,
        )
        if existing:
            return existing[0], filename, f"Invoice {invoice_number} already exists."

        # 4. No duplicate -> create invoice
        invoice_id = self.create(
            model="account.move",
            vals={
                "move_type": "out_invoice",
                "journal_id": journal_id,
                "partner_id": partner_id,
                "invoice_date": invoice_date,
                "ref": invoice_number,
                "invoice_line_ids": self.create_invoice_lines(totals),
            }
        )

        with open(file_path, "rb") as f:
            pdf_content = base64.b64encode(f.read()).decode('utf-8')

        # 5. Create and add attachment
        self.create(
            model="ir.attachment",
            vals={
                'name': filename,
                'type': 'binary',
                'datas': pdf_content,
                'res_model': 'account.move',
                'res_id': invoice_id,
                'mimetype': 'application/pdf',
            }
        )

        # 6. Post invoice
        self.button("account.move", "action_post", [invoice_id])

        return invoice_id, filename, f"Invoice {invoice_number} created & posted."

    def send_peppol(self, invoice_id):
        """
        Send a invoice via the Peppol network.
        :param invoice_id: id of the invoice to be sent
        :return: invoice sent success / false otherwise
        """

        # 1. Get partner of invoice and partner peppol state
        invoice = self.read(
            model="account.move",
            ids=[invoice_id],
            fields=["partner_id", "peppol_move_state"],
        )[0]

        partner_id = invoice["partner_id"][0]
        move_state = invoice["peppol_move_state"]

        # Get partner name from partner id
        partner = self.read(
            model="res.partner",
            ids=[partner_id],
            fields=["peppol_verification_state"],
        )[0]

        partner_state = partner["peppol_verification_state"]

        # 2. If partner peppol verification not active -> make active
        if partner_state not in ("valid", "not_valid"):
            self.button(
                "res.partner",
                "button_account_peppol_check_partner_endpoint",
                [partner_id],
            )
            return "Partner verification triggered: Manual sending required"

        # If partner not on peppol -> stop: manual intervention
        if partner_state == "not_valid":
            raise OdooClientError("Partner not on Peppol")


        if move_state == "done":
            return False, "Already sent"

        if move_state == "error":
            return False, "Peppol failed"

        action = self.button(
            model="account.move",
            method="action_invoice_sent",
            ids=[invoice_id]
        )
        context = action.get("context")

        print(action)
        print(context)

        wizard_id = self.create(
            model="account.move.send.wizard",
            vals={
                "move_id": invoice_id,
                "sending_methods": ["peppol"],
            },
            context=context,
        )

        self.button(
            "account.move.send.wizard",
            "action_send_and_print",
            [wizard_id],
        )

        return True, "Invoice sent via Peppol"


if __name__ == "__main__":
    #
    URL = os.getenv("ODOO_URL")
    DB = os.getenv("ODOO_DB")
    API_KEY = os.getenv("ODOO_API_KEY")

    client = OdooClient(URL, DB, API_KEY)

    # client = OdooClient(
    #         url="https://skbctesting.odoo.com",
    #         db="skbctesting",
    #         api_key="7e231b61aa3afc6c8c8fae66fcf60c35e22f4e2d"
    #     )

    # print(client.connect())
    #
    # file_path = "Factuur_sent/AsmitaBv_20260123_7361.pdf"
    #
    # # invoice_id, s, a = client.create_post_invoice(file_path)
    #
    # print(client.create_post_invoice(file_path))
    #
    # # print("invoice id", invoice_id)
    #
    # print(client.send_peppol(19433))

