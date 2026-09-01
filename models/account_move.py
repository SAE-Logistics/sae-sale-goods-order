from datetime import timedelta

from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    invoice_service_type = fields.Selection(
        [
            ("transport", "Transport"),
            ("warehouse", "Warehouse / Picking Service"),
        ],
        string="Invoice Service Type",
        copy=True,
        index=True,
    )


class AccountMove(models.Model):
    _inherit = "account.move"

    invoice_period_start = fields.Date(copy=False)
    invoice_period_end = fields.Date(copy=False)

    def _get_report_product_invoice_lines(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda line: (
                (line.display_type == "product" or not line.display_type)
                and not self.currency_id.is_zero(line.price_subtotal)
            )
        )

    def _get_sale_orders_from_origin(self, order_types):
        self.ensure_one()
        origins = [
            origin.strip()
            for origin in (self.invoice_origin or "").split(",")
            if origin and origin.strip()
        ]
        if not origins:
            return self.env["sale.order"]
        return self.env["sale.order"].search(
            [
                ("name", "in", origins),
                ("order_type", "in", order_types),
            ],
            order="date_order,id",
        )

    def _get_transport_sale_orders_from_origin(self):
        return self._get_sale_orders_from_origin(["transport"])

    def _get_transport_invoice_lines(self):
        """Return only invoiced lines backed by transport sale lines.

        A mixed invoice can contain warehouse services and transport charges.
        The transport report must therefore be scoped through the preserved
        invoice-line/sale-line relationship, not through ``invoice_origin``.
        """
        self.ensure_one()
        invoice_lines = self._get_report_product_invoice_lines()
        directly_linked_sale_lines = invoice_lines.sale_line_ids.filtered("transport_leg_ids")
        transport_orders = directly_linked_sale_lines.order_id
        return invoice_lines.filtered(
            lambda invoice_line: any(
                sale_line.transport_leg_ids
                or (
                    sale_line.transport_surcharge_charge_line
                    and sale_line.order_id in transport_orders
                )
                for sale_line in invoice_line.sale_line_ids
            )
        )

    def _get_transport_sale_orders(self):
        self.ensure_one()
        sale_orders = self._get_transport_invoice_lines().sale_line_ids.order_id
        return sale_orders.sorted(key=lambda order: (order.date_order or fields.Datetime.now(), order.id))

    def _get_transport_invoice_customer_order(self):
        self.ensure_one()
        sale_orders = self._get_transport_sale_orders()
        return ", ".join(
            filter(None, sale_orders.mapped("client_order_ref") or sale_orders.mapped("name"))
        )

    def _get_transport_invoice_rows(self):
        self.ensure_one()
        rows = []
        seen_sale_line_ids = set()
        for invoice_line in self._get_transport_invoice_lines():
            sale_lines = invoice_line.sale_line_ids.filtered("transport_leg_ids")
            for sale_line in sale_lines:
                if sale_line.id in seen_sale_line_ids:
                    continue
                seen_sale_line_ids.add(sale_line.id)
                legs = sale_line.transport_leg_ids.sorted(key=lambda leg: (leg.sequence, leg.id))
                row = self._prepare_transport_row(
                    invoice_line=invoice_line,
                    sale_line=sale_line,
                    legs=legs,
                )
                fuel_charge = 0.0
                surcharge_groups = {}
                surcharge_order = []
                for leg in legs:
                    if leg.surcharge_line_ids:
                        for surcharge in leg.surcharge_line_ids.sorted(key=lambda line: line.id):
                            if "fuel" in (surcharge.name or "").lower():
                                fuel_charge += surcharge.sell_rate
                            else:
                                key = (
                                    surcharge.surcharge_product_id.id
                                    or (surcharge.name or "").strip().lower()
                                )
                                if key not in surcharge_groups:
                                    surcharge_groups[key] = {
                                        "name": surcharge.name or surcharge.surcharge_product_id.display_name,
                                        "value": 0.0,
                                    }
                                    surcharge_order.append(key)
                                surcharge_groups[key]["value"] += surcharge.sell_rate
                    else:
                        # Legacy legs store the combined surcharge in F/S only.
                        fuel_charge += leg.fs_sell_rate
                row["fuel_charge"] = fuel_charge
                rows.append(row)
                for key in surcharge_order:
                    surcharge_group = surcharge_groups[key]
                    rows.append(
                        self._prepare_transport_surcharge_row(
                            legs[0],
                            surcharge_group["name"],
                            surcharge_group["value"],
                        )
                    )
        return rows

    def _format_transport_location(self, partner):
        if not partner:
            return ""
        return ", ".join(filter(None, [partner.city, partner.zip]))

    def _prepare_transport_row(self, invoice_line=False, sale_line=False, legs=False):
        order = sale_line.order_id if sale_line else False
        legs = legs or self.env["sale.transport.leg"]
        first_leg = legs[0] if legs else False
        last_leg = legs[-1] if legs else False
        partner_from = (
            first_leg.from_location
            if first_leg and first_leg.from_location
            else order.transport_from_id if order else False
        )
        partner_to = (
            last_leg.to_location
            if last_leg and last_leg.to_location
            else order.transport_to_id if order else False
        )
        invoice_description = ""
        if invoice_line:
            invoice_description = (
                invoice_line.product_id.display_name if invoice_line.product_id else invoice_line.name or ""
            )
        description = ""
        if sale_line:
            package_descriptions = list(
                dict.fromkeys(
                    filter(None, sale_line.package_ids.mapped("package_type_id.name"))
                )
            )
            description = (
                ", ".join(package_descriptions)
                or sale_line.package_type_id.name
                or invoice_description
                or sale_line.name
            )
        elif invoice_line:
            description = invoice_description
        customer_ref = ""
        if order:
            customer_ref = order.client_order_ref or order.cost_centre_id.display_name or order.partner_id.ref or ""
        row_date = (
            first_leg.from_date
            if first_leg and first_leg.from_date
            else order.date_order.date() if order and order.date_order else self.invoice_date
        )
        references = list(dict.fromkeys(filter(None, legs.mapped("reference"))))
        services = list(dict.fromkeys(filter(None, legs.mapped("service_id.display_name"))))

        return {
            "row_type": "consignment",
            "date": row_date,
            "client_ref": customer_ref,
            "job_no": ", ".join(references) if references else order.name if order else self.invoice_origin or self.name,
            "service": (
                ", ".join(services)
                if services
                else invoice_line.product_id.display_name if invoice_line and invoice_line.product_id
                else sale_line.product_id.display_name if sale_line and sale_line.product_id
                else ""
            ),
            "description": description,
            "weight": sale_line.total_weight if sale_line else 0.0,
            "from_postcode": self._format_transport_location(partner_from),
            "to_postcode": self._format_transport_location(partner_to),
            "consignee": partner_to.name if partner_to else invoice_line.partner_id.display_name if invoice_line else self.partner_id.display_name,
            "value": sum((leg.base_sell_rate or leg.sell_rate or 0.0) for leg in legs),
            "fuel_charge": 0.0,
        }

    def _prepare_transport_surcharge_row(self, first_leg, surcharge_name, surcharge_value):
        return {
            "row_type": "surcharge",
            "date": first_leg.from_date or self.invoice_date,
            "client_ref": "Additional charges:",
            "job_no": "",
            "service": surcharge_name,
            "description": "",
            "weight": 0.0,
            "from_postcode": "",
            "to_postcode": "",
            "consignee": "",
            "value": surcharge_value,
            "fuel_charge": 0.0,
        }

    def _get_transport_invoice_report_data(self):
        self.ensure_one()
        invoice_lines = self._get_transport_invoice_lines()
        rows = self._get_transport_invoice_rows()
        consignment_rows = [row for row in rows if row["row_type"] == "consignment"]
        taxes = invoice_lines.tax_ids
        untaxed = sum(invoice_lines.mapped("price_subtotal"))
        total = sum(invoice_lines.mapped("price_total"))
        return {
            "rows": rows,
            "consignment_count": len(consignment_rows),
            "summary_value": sum(row["value"] for row in rows),
            "summary_fuel": sum(row["fuel_charge"] for row in consignment_rows),
            "untaxed": self.currency_id.round(untaxed),
            "tax": self.currency_id.round(total - untaxed),
            "total": self.currency_id.round(total),
            "tax_label": ", ".join(taxes.mapped("name")),
        }

    def _get_warehouse_invoice_lines(self):
        """Return the warehouse/picking portion of a possibly mixed invoice."""
        self.ensure_one()
        invoice_lines = self._get_report_product_invoice_lines()
        transport_lines = self._get_transport_invoice_lines()
        goods_sale_orders = invoice_lines.sale_line_ids.order_id.filtered(
            lambda order: order.order_type in ("goods_in", "goods_out")
        )

        return (invoice_lines - transport_lines).filtered(
            lambda invoice_line: (
                invoice_line.invoice_service_type == "warehouse"
                or any(
                    sale_line.invoice_service_type == "warehouse"
                    or sale_line.order_id.order_type in ("goods_in", "goods_out")
                    for sale_line in invoice_line.sale_line_ids
                )
                # Legacy storage/rental lines were created directly on goods
                # invoices and consequently have no sale_line_ids.
                or (not invoice_line.sale_line_ids and bool(goods_sale_orders))
            )
        ).sorted(key=lambda line: (line.sequence, line.id))

    def _get_warehouse_invoice_report_data(self):
        self.ensure_one()
        invoice_lines = self._get_warehouse_invoice_lines()
        grouped_rows = []
        rows_by_key = {}
        for invoice_line in invoice_lines:
            key = (
                invoice_line.product_id.id or ("line", invoice_line.id),
                invoice_line.product_uom_id.id,
                tuple(sorted(invoice_line.tax_ids.ids)),
            )
            row = rows_by_key.get(key)
            if not row:
                row = {
                    "description": invoice_line.name or invoice_line.product_id.display_name,
                    "quantity": 0.0,
                    "subtotal": 0.0,
                    "total": 0.0,
                    "tax_label": ", ".join(invoice_line.tax_ids.mapped("name")),
                }
                rows_by_key[key] = row
                grouped_rows.append(row)
            row["quantity"] += invoice_line.quantity
            row["subtotal"] += invoice_line.price_subtotal
            row["total"] += invoice_line.price_total

        for row in grouped_rows:
            row["unit_price"] = (
                row["subtotal"] / row["quantity"]
                if row["quantity"]
                else 0.0
            )
            row["subtotal"] = self.currency_id.round(row["subtotal"])
            row["total"] = self.currency_id.round(row["total"])

        untaxed = sum(invoice_lines.mapped("price_subtotal"))
        total = sum(invoice_lines.mapped("price_total"))
        return {
            "lines": invoice_lines,
            "rows": grouped_rows,
            "untaxed": self.currency_id.round(untaxed),
            "tax": self.currency_id.round(total - untaxed),
            "total": self.currency_id.round(total),
        }

    def _get_goods_sale_orders_from_origin(self):
        return self._get_sale_orders_from_origin(["goods_in", "goods_out"])

    def _get_goods_sale_orders(self):
        self.ensure_one()
        sale_orders = self.invoice_line_ids.sale_line_ids.order_id.filtered(
            lambda order: order.order_type in ("goods_in", "goods_out")
        )
        if not sale_orders:
            sale_orders = self._get_goods_sale_orders_from_origin()
        return sale_orders.sorted(key=lambda order: (order.date_order or fields.Datetime.now(), order.id))

    def _get_goods_report_region(self):
        self.ensure_one()
        order = self._get_goods_sale_orders()[:1]
        country = order.warehouse_id.partner_id.country_id if order else self.company_id.country_id
        return country.name if country else ""

    def _get_goods_metric_products(self):
        config = self.env["ir.config_parameter"].sudo()
        mapping = {
            "order_handling": [
                "sale_gto.product_order_receipt_id",
                "sale_gto.product_sku_in_id",
                "sale_gto.product_qty_in_id",
                "sale_gto.product_sku_out_id",
                "sale_gto.product_qty_out_id",
                "sale_gto.product_serial_id",
                "sale_gto.product_container20_id",
                "sale_gto.product_container40_id",
                "sale_gto.product_handling_other_id",
            ],
            "lot_expiry": [
                "sale_gto.product_lot_id",
                "sale_gto.product_expiry_id",
            ],
            "containers": [],
            "storage": [
                "sale_gto.product_storage_pallet_week_id",
                "sale_gto.product_storage_carton_week_id",
                "sale_gto.product_storage_container_month_id",
                "sale_gto.product_storage_unit_month_id",
                "sale_gto.product_storage_yard_month_id",
                "sale_gto.product_storage_other_id",
            ],
        }
        result = {}
        for key, params in mapping.items():
            ids = set()
            for param in params:
                value = config.get_param(param)
                if value:
                    try:
                        ids.add(int(value))
                    except (TypeError, ValueError):
                        continue
            result[key] = ids
        return result

    def _is_storable_product(self, product):
        detailed_type = getattr(product, "detailed_type", False)
        return (
            detailed_type == "product"
            or product.type == "product"
            or bool(getattr(product, "is_storable", False))
        )

    def _get_sale_order_report_date(self, order):
        confirmation_date = getattr(order, "confirmation_date", False)
        if confirmation_date:
            return confirmation_date.date()
        return order.date_order.date() if order.date_order else False

    def _get_goods_sale_order_section(self):
        self.ensure_one()
        metric_products = self._get_goods_metric_products()
        sale_orders = self._get_goods_sale_orders()
        invoice_lines = self._get_report_product_invoice_lines()
        rows = []

        for order in sale_orders:
            order_sale_lines = order.order_line.filtered(lambda line: not line.display_type)
            order_invoice_lines = invoice_lines.filtered(
                lambda line: bool(line.sale_line_ids & order_sale_lines)
            )
            row = {
                "job_id": order.name,
                "date": self._get_sale_order_report_date(order),
                "type": "Goods In" if order.order_type == "goods_in" else "Goods Out" if order.order_type == "goods_out" else "Goods",
                "order_handling": 0.0,
                "containers": 0.0,
                "lot_expiry": 0.0,
                "materials": 0.0,
                "total": 0.0,
            }
            source_lines = order_invoice_lines.sorted(key=lambda line: (line.sequence, line.id))
            if not source_lines:
                source_lines = order.order_line.filtered(lambda line: not line.display_type and line.product_id)

            for line in source_lines:
                subtotal = line.price_subtotal
                product = line.product_id
                if self._is_storable_product(product):
                    row["materials"] += subtotal
                elif getattr(line, "container_charge_line", False):
                    row["containers"] += subtotal
                elif product.id in metric_products["lot_expiry"]:
                    row["lot_expiry"] += subtotal
                else:
                    row["order_handling"] += subtotal
                row["total"] += subtotal
            if row["total"]:
                rows.append(row)

        return {
            "rows": rows,
            "subtotal": sum(row["total"] for row in rows),
        }

    def _get_goods_invoice_period(self):
        self.ensure_one()
        date_from = self.invoice_period_start
        date_to = self.invoice_period_end
        if date_from and date_to:
            return date_from, date_to

        sale_orders = self._get_goods_sale_orders()
        if not sale_orders:
            return False, False
        dates = [
            self._get_sale_order_report_date(order)
            for order in sale_orders
            if self._get_sale_order_report_date(order)
        ]
        if not dates:
            return False, False
        return min(dates), max(dates)

    def _get_goods_invoice_warehouses(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        return self.env["stock.warehouse"].search(
            [
                ("commercial_partner_id", "=", partner.id),
                ("company_id", "=", self.company_id.id),
            ],
            order="id",
        )

    def _prepare_storage_report_rows(self, rows):
        grouped_rows = []
        rows_by_week = {}
        for row in rows:
            rows_by_week.setdefault(row["week_label"], []).append(row)

        for week_label in sorted(rows_by_week):
            week_rows = rows_by_week[week_label]
            rowspan = len(week_rows)
            for index, row in enumerate(week_rows):
                prepared_row = dict(row)
                prepared_row["show_week"] = index == 0
                prepared_row["week_rowspan"] = rowspan if index == 0 else 0
                grouped_rows.append(prepared_row)
        return grouped_rows

    def _get_storage_section(self):
        self.ensure_one()
        date_from, date_to = self._get_goods_invoice_period()
        if not date_from or not date_to:
            return {"rows": [], "subtotal": 0.0}

        rows = []
        subtotal = 0.0
        warehouses = self._get_goods_invoice_warehouses()
        move_model = self.env["stock.move"]
        sale_orders = self._get_goods_sale_orders()
        base_order = sale_orders[:1]

        for warehouse in warehouses:
            weekly_rows = warehouse._get_outside_rental_weekly_rows(date_from, date_to)
            package_types = self.env["stock.package.type"].browse(
                sorted({row["container_type_id"] for row in weekly_rows})
            )
            package_types_by_id = {package_type.id: package_type for package_type in package_types}

            for weekly_row in weekly_rows:
                container_type = package_types_by_id.get(weekly_row["container_type_id"])
                if not container_type:
                    continue

                rate = 0.0
                if base_order and container_type.product_id:
                    rate = (
                        base_order.pricelist_id._get_product_price(
                            container_type.product_id,
                            weekly_row["closing"] or 1.0,
                            currency=base_order.currency_id,
                            uom=container_type.product_id.uom_id,
                            date=self.invoice_date or fields.Date.context_today(self),
                        )
                        if base_order.pricelist_id
                        else container_type.product_id.lst_price
                    )
                amount = weekly_row["closing"] * rate
                if amount <= 0:
                    continue
                subtotal += amount
                rows.append(
                    {
                        "week_label": "%s - %s" % (weekly_row["week_start"], weekly_row["week_end"]),
                        "container_type": container_type.display_name,
                        "opening": weekly_row["opening"],
                        "inward": weekly_row["inward"],
                        "closing": weekly_row["closing"],
                        "outward": weekly_row["outward"],
                        "rate": rate,
                        "amount": amount,
                    }
                )

        return {
            "rows": self._prepare_storage_report_rows(rows),
            "subtotal": subtotal,
        }

    def _get_rental_section(self):
        self.ensure_one()
        date_from, date_to = self._get_goods_invoice_period()
        if not date_from or not date_to:
            return {"rows": [], "subtotal": 0.0}

        sale_orders = self._get_goods_sale_orders()
        base_order = sale_orders[:1]
        rows = []
        subtotal = 0.0

        for warehouse in self._get_goods_invoice_warehouses():
            rental_location = warehouse.rental_location_id
            rental_product = rental_location.storage_product_id if rental_location else False
            if not rental_location or not rental_product:
                continue

            rate = (
                base_order.pricelist_id._get_product_price(
                    rental_product,
                    1.0,
                    currency=base_order.currency_id,
                    uom=rental_product.uom_id,
                    date=self.invoice_date or fields.Date.context_today(self),
                )
                if base_order and base_order.pricelist_id
                else rental_product.lst_price
            )
            months = warehouse._count_billing_months(date_from, date_to)
            subtotal += rate * months
            rows.append(
                {
                    "rental_space_name": "%s - %s" % (warehouse.display_name, rental_location.complete_name),
                    "rent_per_month": rate,
                }
            )

        return {
            "rows": rows,
            "subtotal": subtotal,
        }

    def _get_goods_invoice_report_data(self):
        self.ensure_one()
        goods_section = self._get_goods_sale_order_section()
        storage_section = self._get_storage_section()
        rental_section = self._get_rental_section()
        untaxed_total = goods_section["subtotal"] + storage_section["subtotal"] + rental_section["subtotal"]
        tax_amount = self.amount_total - self.amount_untaxed
        return {
            "goods_section": goods_section,
            "storage_section": storage_section,
            "rental_section": rental_section,
            "untaxed_total": untaxed_total,
            "tax_amount": tax_amount,
            "total_with_tax": self.amount_total,
            "balance": self.amount_residual,
            "period_start": self.invoice_period_start,
            "period_end": self.invoice_period_end,
        }

    def _get_goods_invoice_rows(self):
        self.ensure_one()
        metric_products = self._get_goods_metric_products()
        rows = []
        sale_orders = self._get_goods_sale_orders()

        invoice_lines = self._get_report_product_invoice_lines()
        for invoice_line in invoice_lines:
            sale_lines = invoice_line.sale_line_ids.filtered(
                lambda line: line.order_id.order_type in ("goods_in", "goods_out")
            )
            if not sale_lines and sale_orders:
                continue
            for sale_line in sale_lines or self.env["sale.order.line"]:
                rows.append(
                    self._prepare_goods_row(
                        sale_line=sale_line,
                        invoice_line=invoice_line,
                        metric_products=metric_products,
                    )
                )

        if rows:
            return rows

        for sale_order in sale_orders:
            for sale_line in sale_order.order_line.filtered(lambda line: not line.display_type):
                rows.append(
                    self._prepare_goods_row(
                        sale_line=sale_line,
                        metric_products=metric_products,
                    )
                )
        return rows

    def _prepare_goods_row(self, sale_line=False, invoice_line=False, metric_products=False):
        metric_products = metric_products or self._get_goods_metric_products()
        order = sale_line.order_id if sale_line else False
        product = (
            invoice_line.product_id
            if invoice_line and invoice_line.product_id
            else sale_line.product_id if sale_line and sale_line.product_id
            else False
        )
        product_id = product.id if product else False
        qty = 0.0
        if invoice_line:
            qty = invoice_line.quantity
        elif sale_line:
            qty = sale_line.qty_delivered or sale_line.product_uom_qty
        unit_price = invoice_line.price_unit if invoice_line else sale_line.price_unit if sale_line else 0.0
        subtotal = (
            invoice_line.price_subtotal
            if invoice_line
            else sale_line.price_subtotal if sale_line
            else 0.0
        )
        destination = (
            order.partner_shipping_id.display_name
            if order and order.partner_shipping_id
            else self.partner_id.display_name
        )
        description = (
            invoice_line.name
            if invoice_line and invoice_line.name
            else sale_line.name if sale_line and sale_line.name
            else product.display_name if product
            else ""
        )
        picking = order.picking_ids.filtered(lambda p: not p.is_material_picking)[:1] if order else False
        row = {
            "department": order.partner_id.display_name if order else self.partner_id.display_name,
            "date": (
                picking.scheduled_date.date()
                if picking and picking.scheduled_date
                else order.date_order.date() if order and order.date_order
                else self.invoice_date
            ),
            "client_ref": order.client_order_ref or order.partner_id.ref if order else "",
            "job_no": picking.name if picking else order.name if order else self.invoice_origin or self.name,
            "description": description,
            "to": destination,
            "sku": "",
            "materials": "",
            "labour_units": "",
            "labour_value": 0.0,
            "pick_pack_qty": "",
            "pick_pack_value": 0.0,
            "order_value": 0.0,
            "unit_value": unit_price,
            "qty_value": qty,
            "line_total": subtotal,
        }

        if product_id in metric_products["order"]:
            row["order_value"] = subtotal
        elif product_id in metric_products["pick_pack_qty"]:
            row["pick_pack_qty"] = qty
            row["pick_pack_value"] = subtotal
            if order and order.picking_type == "sku":
                row["sku"] = "Yes"
        elif product_id in metric_products["materials"]:
            row["materials"] = qty
        elif product_id in metric_products["labour"]:
            row["labour_units"] = qty
            row["labour_value"] = subtotal
        else:
            row["materials"] = qty
        return row
