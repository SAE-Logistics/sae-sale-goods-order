from datetime import datetime, time

from odoo import fields, models
from odoo.exceptions import UserError


class StockContainerReportWizard(models.TransientModel):
    _name = "stock.container.report.wizard"
    _description = "Containers Ledger"

    from_date = fields.Date(
        string="From Date",
        required=True,
        default=fields.Date.context_today,
    )
    to_date = fields.Date(
        string="To Date",
        required=True,
        default=fields.Date.context_today,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Internal Location",
        domain="[('usage', '=', 'internal')]",
    )
    container_type_id = fields.Many2one(
        "stock.package.type",
        string="Container Type",
    )
    result_line_ids = fields.One2many(
        "stock.container.report.line",
        "wizard_id",
        string="Results",
    )

    def action_generate_report(self):
        self.ensure_one()
        if self.from_date > self.to_date:
            raise UserError("'From Date' cannot be later than 'To Date'.")
        self.result_line_ids.unlink()

        snapshot = self.env["stock.picking.container"].get_container_ledger_snapshot(
            self.from_date,
            self.to_date,
            partner_id=self.partner_id.id,
            location_id=self.location_id.id,
            container_type_id=self.container_type_id.id,
        )
        line_values = []
        for (partner_id, container_type_id, location_id), values in sorted(
            snapshot.items(), key=lambda item: item[0]
        ):
            if not any(values.values()):
                continue
            line_values.append(
                {
                    "wizard_id": self.id,
                    "partner_id": partner_id,
                    "container_type_id": container_type_id,
                    "location_id": location_id,
                    "opening": values["opening"],
                    "inward": values["inward"],
                    "outward": values["outward"],
                    "balance": values["balance"],
                }
            )

        if line_values:
            self.env["stock.container.report.line"].create(line_values)

        return {
            "type": "ir.actions.act_window",
            "name": "Containers Ledger",
            "res_model": "stock.container.report.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }


class StockContainerReportLine(models.TransientModel):
    _name = "stock.container.report.line"
    _description = "Containers Ledger Line"
    _order = "partner_id, container_type_id, location_id"

    wizard_id = fields.Many2one(
        "stock.container.report.wizard",
        required=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
    )
    container_type_id = fields.Many2one(
        "stock.package.type",
        string="Container Type",
        required=True,
    )
    location_id = fields.Many2one(
        "stock.location",
        string="Internal Location",
        required=True,
    )
    opening = fields.Integer(
        string="Opening",
        required=True,
    )
    inward = fields.Integer(
        string="Inward",
        required=True,
    )
    outward = fields.Integer(
        string="Outward",
        required=True,
    )
    balance = fields.Integer(
        string="Balance",
        required=True,
    )

    def _get_move_action(self, direction):
        self.ensure_one()
        field_name = "location_dest_id" if direction == "inward" else "location_id"
        action_name = "Inward Moves" if direction == "inward" else "Outward Moves"
        domain = [
            ("state", "=", "done"),
            ("container_count", ">", 0),
            ("container_type_id", "=", self.container_type_id.id),
            (field_name, "=", self.location_id.id),
            (
                "movement_date",
                ">=",
                fields.Datetime.to_string(
                    datetime.combine(self.wizard_id.from_date, time.min)
                ),
            ),
            (
                "movement_date",
                "<=",
                fields.Datetime.to_string(
                    datetime.combine(self.wizard_id.to_date, time.max)
                ),
            ),
        ]
        if self.partner_id:
            domain.append(("commercial_partner_id", "child_of", self.partner_id.id))
        return {
            "type": "ir.actions.act_window",
            "name": action_name,
            "res_model": "stock.picking.container",
            "view_mode": "list,form,pivot,graph",
            "domain": domain,
            "context": {
                "search_default_done": 1,
                "group_by": "picking_type_id",
            },
        }

    def action_view_inward_moves(self):
        return self._get_move_action("inward")

    def action_view_outward_moves(self):
        return self._get_move_action("outward")
