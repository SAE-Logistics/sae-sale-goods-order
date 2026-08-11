from datetime import datetime, time

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPickingContainer(models.Model):
    _name = "stock.picking.container"
    _description = "Picking Container"
    _order = "movement_date desc, picking_id desc, id desc"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Picking",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="picking_id.company_id", store=True, readonly=True)
    warehouse_id = fields.Many2one(
        related="picking_id.picking_type_id.warehouse_id",
        string="Warehouse",
        store=True,
        readonly=True,
    )
    sale_id = fields.Many2one(related="picking_id.sale_id", store=True, readonly=True)
    state = fields.Selection(related="picking_id.state", store=True, readonly=True)
    picking_type_id = fields.Many2one(related="picking_id.picking_type_id", store=True, readonly=True)
    picking_type_code = fields.Selection(related="picking_id.picking_type_code", store=True, readonly=True)
    partner_id = fields.Many2one(related="picking_id.partner_id", store=True, readonly=True)
    commercial_partner_id = fields.Many2one(
        "res.partner",
        string="Commercial Partner",
        compute="_compute_commercial_partner_id",
        store=True,
        readonly=True,
    )
    location_id = fields.Many2one(related="picking_id.location_id", store=True, readonly=True)
    location_dest_id = fields.Many2one(related="picking_id.location_dest_id", store=True, readonly=True)
    movement_date = fields.Datetime(
        string="Movement Date",
        compute="_compute_movement_date",
        store=True,
        readonly=True,
    )
    container_type_id = fields.Many2one(
        "stock.package.type",
        string="Container Type",
        required=True,
        default=lambda self: self._default_container_type_id(),
    )
    container_count = fields.Integer(
        string="No. of Containers",
        default=0,
        required=True,
    )

    _sql_constraints = [
        (
            "stock_picking_container_unique_type_per_picking",
            "unique(picking_id, container_type_id)",
            "A container type can only appear once on the same picking.",
        ),
        (
            "stock_picking_container_non_negative_count",
            "check(container_count >= 0)",
            "The number of containers cannot be negative.",
        ),
    ]

    @api.depends("partner_id")
    def _compute_commercial_partner_id(self):
        for record in self:
            record.commercial_partner_id = record.partner_id.commercial_partner_id

    @api.depends("picking_id.date_done", "picking_id.date")
    def _compute_movement_date(self):
        for record in self:
            record.movement_date = record.picking_id.date_done or record.picking_id.date

    @api.model
    def _default_container_type_id(self):
        parameter = self.env["ir.config_parameter"].sudo().get_param(
            "sale_gto.default_container_type_id"
        )
        return int(parameter) if parameter else False

    def _check_picking_editable(self):
        if self.env.context.get("bypass_picking_container_lock"):
            return
        locked = self.filtered(lambda line: line.picking_id.state in ("done", "cancel"))
        if locked:
            raise UserError(
                _("Container Type and No. of Containers cannot be changed on done or cancelled pickings.")
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_picking_editable()
        return records

    def write(self, vals):
        if {"container_type_id", "container_count", "picking_id"} & set(vals):
            self._check_picking_editable()
        return super().write(vals)

    def unlink(self):
        self._check_picking_editable()
        return super().unlink()

    @api.model
    def _iter_container_impacts(
        self,
        date_from=False,
        date_to=False,
        partner_id=False,
        location_id=False,
        container_type_id=False,
    ):
        domain = [
            ("state", "=", "done"),
            ("container_count", ">", 0),
        ]
        if date_from:
            date_from_dt = datetime.combine(date_from, time.min)
            domain.append(("movement_date", ">=", fields.Datetime.to_string(date_from_dt)))
        if date_to:
            date_to_dt = datetime.combine(date_to, time.max)
            domain.append(("movement_date", "<=", fields.Datetime.to_string(date_to_dt)))
        if container_type_id:
            domain.append(("container_type_id", "=", container_type_id))
        if partner_id:
            domain.append(("commercial_partner_id", "child_of", partner_id))

        for line in self.search(domain):
            source_location = line.location_id
            dest_location = line.location_dest_id
            directions = []
            if dest_location.usage == "internal":
                directions.append(("inward", dest_location, float(line.container_count or 0)))
            if source_location.usage == "internal":
                directions.append(("outward", source_location, float(line.container_count or 0)))

            for direction, internal_location, quantity in directions:
                if location_id and internal_location.id != location_id:
                    continue
                yield {
                    "line": line,
                    "direction": direction,
                    "partner_id": line.commercial_partner_id.id or False,
                    "container_type_id": line.container_type_id.id,
                    "location_id": internal_location.id,
                    "quantity": quantity,
                }

    @api.model
    def get_container_ledger_snapshot(
        self,
        date_from,
        date_to,
        partner_id=False,
        location_id=False,
        container_type_id=False,
    ):
        snapshot = {}
        for impact in self._iter_container_impacts(
            partner_id=partner_id,
            location_id=location_id,
            container_type_id=container_type_id,
        ):
            move_date = fields.Date.to_date(impact["line"].movement_date)
            key = (
                impact["partner_id"],
                impact["container_type_id"],
                impact["location_id"],
            )
            bucket = snapshot.setdefault(
                key,
                {"opening": 0, "inward": 0, "outward": 0, "balance": 0},
            )
            if move_date < date_from:
                if impact["direction"] == "inward":
                    bucket["opening"] += impact["quantity"]
                else:
                    bucket["opening"] -= impact["quantity"]
                continue
            if move_date > date_to:
                continue
            if impact["direction"] == "inward":
                bucket["inward"] += impact["quantity"]
            else:
                bucket["outward"] += impact["quantity"]

        for values in snapshot.values():
            values["balance"] = values["opening"] + values["inward"] - values["outward"]
        return snapshot

    @api.model
    def get_container_balance_snapshot(
        self,
        as_of_date,
        partner_id=False,
        location_ids=False,
        container_type_id=False,
    ):
        snapshot = {}
        for impact in self._iter_container_impacts(
            date_to=as_of_date,
            partner_id=partner_id,
            container_type_id=container_type_id,
        ):
            impact_date = fields.Date.to_date(impact["line"].movement_date)
            if impact_date > as_of_date:
                continue
            if location_ids and impact["location_id"] not in location_ids:
                continue
            key = (
                impact["partner_id"],
                impact["container_type_id"],
                impact["location_id"],
            )
            signed_qty = impact["quantity"] if impact["direction"] == "inward" else -impact["quantity"]
            snapshot[key] = snapshot.get(key, 0.0) + signed_qty
        return snapshot
