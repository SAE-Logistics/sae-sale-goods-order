from collections import defaultdict

from odoo import _, fields, models
from odoo.fields import Command
from odoo.exceptions import UserError


class SaleBulkInvoiceWizard(models.TransientModel):
    _name = "sale.bulk.invoice.wizard"
    _description = "Bulk Invoice Goods and Transport Orders"

    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    invoice_date = fields.Date(required=True, default=fields.Date.context_today)
    partner_id = fields.Many2one("res.partner", string="Customer")
    order_family = fields.Selection(
        [
            ("all", "Goods and Transport"),
            ("goods", "Goods Only"),
            ("transport", "Transport Only"),
        ],
        required=True,
        default="all",
    )
    only_confirmed = fields.Boolean(default=True)

    def _get_product_invoice_lines(self, invoice):
        return invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" or not line.display_type
        )

    def _get_order_types(self):
        self.ensure_one()
        if self.order_family == "goods":
            return ["goods_in", "goods_out"]
        if self.order_family == "transport":
            return ["transport"]
        return ["goods_in", "goods_out", "transport"]

    def _get_order_domain(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("Start Date must be earlier than or equal to End Date."))

        domain = [
            ("order_type", "in", self._get_order_types()),
            ("date_order", ">=", fields.Datetime.to_datetime(self.date_from)),
            ("date_order", "<", fields.Datetime.to_datetime(fields.Date.add(self.date_to, days=1))),
            ("invoice_status", "=", "to invoice"),
        ]
        if self.only_confirmed:
            domain.append(("state", "in", ["sale", "done"]))
        if self.partner_id:
            domain.append(("commercial_partner_id", "=", self.partner_id.commercial_partner_id.id))
        return domain

    def _bucket_orders(self, orders):
        buckets = defaultdict(lambda: self.env["sale.order"])
        for order in orders.sorted(key=lambda so: (so.commercial_partner_id.id, so.date_order or fields.Datetime.now(), so.id)):
            family = "transport" if order.order_type == "transport" else "goods"
            key = (
                order.company_id.id,
                order.commercial_partner_id.id,
                family,
            )
            buckets[key] |= order
        return buckets

    def _prepare_bucket_invoice_line_vals(self, invoice_line):
        return {
            "sequence": invoice_line.sequence,
            "name": invoice_line.name,
            "product_id": invoice_line.product_id.id,
            "account_id": invoice_line.account_id.id,
            "product_uom_id": invoice_line.product_uom_id.id,
            "quantity": invoice_line.quantity,
            "discount": invoice_line.discount,
            "price_unit": invoice_line.price_unit,
            "tax_ids": [Command.set(invoice_line.tax_ids.ids)],
            "sale_line_ids": [Command.set(invoice_line.sale_line_ids.ids)],
            "analytic_distribution": invoice_line.analytic_distribution,
        }

    def _create_bucket_invoice(self, orders):
        temporary_invoices = self.env["account.move"]
        invoice_line_vals = []

        for order in orders.sorted(key=lambda so: (so.date_order or fields.Datetime.now(), so.id)):
            invoice = order._create_invoices(grouped=True, date=self.invoice_date)[:1]
            if not invoice:
                continue
            temporary_invoices |= invoice
            order_lines = self._get_product_invoice_lines(invoice)
            if not order_lines:
                continue
            invoice_line_vals.extend(
                self._prepare_bucket_invoice_line_vals(invoice_line)
                for invoice_line in order_lines.sorted(key=lambda line: (line.sequence, line.id))
            )

        if not invoice_line_vals:
            if temporary_invoices:
                temporary_invoices.unlink()
            return self.env["account.move"]

        base_order = orders[0].with_company(orders[0].company_id)
        storage_line_vals = self._prepare_storage_charge_line_vals(orders, base_order)
        invoice_vals = base_order._prepare_invoice()
        invoice_vals.update({
            "invoice_date": self.invoice_date,
            "invoice_origin": ", ".join(orders.mapped("name")),
            "ref": ", ".join(filter(None, orders.mapped("client_order_ref")))[:2000],
            "invoice_period_start": self.date_from,
            "invoice_period_end": self.date_to,
            "invoice_line_ids": [Command.create(vals) for vals in invoice_line_vals + storage_line_vals],
        })
        payment_refs = temporary_invoices.mapped("payment_reference")
        invoice_vals["payment_reference"] = payment_refs[0] if len(set(filter(None, payment_refs))) == 1 else False

        new_invoice = self.env["account.move"].create(invoice_vals)
        if temporary_invoices:
            temporary_invoices.unlink()
        return new_invoice

    def _prepare_manual_product_line_vals(self, order, product, quantity, price_unit, description):
        taxes = order.fiscal_position_id.map_tax(
            product.taxes_id.filtered(lambda tax: tax.company_id == order.company_id)
        )
        income_account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        if not income_account:
            accounts = product.product_tmpl_id.get_product_accounts(fiscal_pos=order.fiscal_position_id)
            income_account = accounts.get("income")
        return {
            "name": description,
            "product_id": product.id,
            "product_uom_id": product.uom_id.id,
            "quantity": quantity,
            "price_unit": price_unit,
            "account_id": income_account.id,
            "tax_ids": [Command.set(taxes.ids)],
        }

    def _get_pricelist_price(self, order, product, quantity):
        pricelist = order.pricelist_id
        if pricelist:
            return pricelist._get_product_price(
                product,
                quantity,
                currency=order.currency_id,
                uom=product.uom_id,
                date=self.invoice_date,
            )
        return product.lst_price

    def _prepare_storage_charge_line_vals(self, orders, base_order):
        if not orders or any(order.order_type == "transport" for order in orders):
            return []
        partner = base_order.commercial_partner_id
        warehouses = self.env["stock.warehouse"].search([
            ("commercial_partner_id", "=", partner.id),
            ("company_id", "=", base_order.company_id.id),
        ])
        if not warehouses:
            return []

        line_vals = []
        for warehouse in warehouses:
            if warehouse.rental_location_id and warehouse.rental_location_id.storage_product_id:
                rental_product = warehouse.rental_location_id.storage_product_id
                line_vals.append(
                    self._prepare_manual_product_line_vals(
                        base_order,
                        rental_product,
                        warehouse._count_billing_months(self.date_from, self.date_to),
                        self._get_pricelist_price(base_order, rental_product, 1),
                        _(
                            "%(warehouse)s Fixed Rental - %(location)s"
                        ) % {
                            "warehouse": warehouse.display_name,
                            "location": warehouse.rental_location_id.complete_name,
                        },
                    )
                )
            outside_rental_weeks = warehouse._get_outside_rental_container_weeks(
                self.date_from, self.date_to
            )
            package_types = self.env["stock.package.type"].browse(list(outside_rental_weeks))
            for package_type in package_types:
                if not package_type.product_id or not outside_rental_weeks.get(package_type.id):
                    continue
                quantity = outside_rental_weeks[package_type.id]
                line_vals.append(
                    self._prepare_manual_product_line_vals(
                        base_order,
                        package_type.product_id,
                        quantity,
                        self._get_pricelist_price(base_order, package_type.product_id, quantity),
                        _("%(warehouse)s Container Storage Outside Rental Area - %(container)s") % {
                            "warehouse": warehouse.display_name,
                            "container": package_type.display_name,
                        },
                    )
                )
        return line_vals

    def action_generate_invoices(self):
        self.ensure_one()
        orders = self.env["sale.order"].search(self._get_order_domain(), order="commercial_partner_id,date_order,id")
        if not orders:
            raise UserError(_("No invoiceable goods or transport orders were found for the selected period."))

        orders = orders.filtered(
            lambda order: not order.invoice_ids.filtered(
                lambda invoice: invoice.move_type == "out_invoice" and invoice.state == "draft"
            )
        )
        if not orders:
            raise UserError(_("All matching orders already have draft customer invoices."))

        invoices = self.env["account.move"]
        buckets = self._bucket_orders(orders)
        for grouped_orders in buckets.values():
            invoices |= self._create_bucket_invoice(grouped_orders)

        if not invoices:
            raise UserError(_("No invoices were created. Check that the selected orders have invoiceable lines."))

        action = self.env.ref("account.action_move_out_invoice_type").read()[0]
        if len(invoices) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = invoices.id
        else:
            action["domain"] = [("id", "in", invoices.ids)]
        return action
