# stock_materials_consumption/wizard/material_picking_wizard.py
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MaterialPickingWizard(models.TransientModel):
    _name = "material.picking.wizard"
    _description = "Wizard to Create Materials Picking"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Source Picking",
        required=True,
    )
    sale_id = fields.Many2one(
        "sale.order",
        string="Sale Order",
        required=True,
    )
    line_ids = fields.One2many(
        "material.picking.wizard.line",
        "wizard_id",
        string="Materials",
    )


    def _get_template(self):
        config = self.env['ir.config_parameter'].sudo()
        template = config.get_param('sale_gto.sale_material_template_id', False)
        if not template:
            raise UserError(_("No Materials Template configured on the company."))
        template = self.env['sale.material.template'].browse(int(template))
        if not template.picking_type_id:
            raise UserError(_("Please set a Picking Type on the Materials Template."))
        return template


    def _load_lines_from_template(self):
        """Populate wizard lines from the configured template."""
        self.ensure_one()
        config = self.env['ir.config_parameter'].sudo()
        template = config.get_param('sale_gto.sale_material_template_id', False)
        if not template:
            return
        template = self.env['sale.material.template'].browse(int(template))
        lines_vals = []
        for tmpl_line in template.line_ids:
            lines_vals.append((
                0,
                0,
                {
                    "product_id": tmpl_line.product_id.id,
                    "uom_id": tmpl_line.uom_id.id,
                    "qty": tmpl_line.default_qty,
                },
            ))
        self.line_ids = lines_vals

    def action_confirm(self):
        self.ensure_one()
        self.line_ids = self.line_ids.filtered(lambda l: l.qty > 0)
        if not self.line_ids:
            raise UserError(_("Please set quantity for at least one product."))

        template = self._get_template()
        picking_type = template.picking_type_id
        source_picking = self.picking_id

        # Decide source/destination locations
        location_id = (
            picking_type.default_location_src_id.id
            or source_picking.location_dest_id.id
            or source_picking.location_id.id
        )
        location_dest_id = (
            picking_type.default_location_dest_id.id
            or source_picking.location_dest_id.id
        )

        # Use the same picking type / destination as the source picking
        picking_vals = {
            "picking_type_id": picking_type.id,
            "location_id": location_id,
            "location_dest_id": location_dest_id,
            "origin": source_picking.name,
            "sale_id": self.sale_id.id,
            "is_material_picking": True,
            "source_picking_id": source_picking.id,
        }

        new_picking = self.env["stock.picking"].create(picking_vals)
        move_vals_list = []

        for line in self.line_ids:
            move_vals_list.append({
                "name": line.product_id.display_name,
                "product_id": line.product_id.id,
                "product_uom": line.uom_id.id,
                "product_uom_qty": line.qty,
                "picking_id": new_picking.id,
                "location_id": new_picking.location_id.id,
                "location_dest_id": new_picking.location_dest_id.id,
                "consumption_source_picking_id": source_picking.id,
            })

        self.env["stock.move"].create(move_vals_list)

        # Optionally confirm/assign the picking
        new_picking.action_confirm()
        # new_picking.action_assign()  # if you want to immediately reserve

        return {
            "type": "ir.actions.act_window",
            "name": _("Materials Picking"),
            "res_model": "stock.picking",
            "view_mode": "form",
            "res_id": new_picking.id,
        }


class MaterialPickingWizardLine(models.TransientModel):
    _name = "material.picking.wizard.line"
    _description = "Materials Wizard Line"

    wizard_id = fields.Many2one(
        "material.picking.wizard",
        string="Wizard",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain=[("detailed_type", "in", ["consu", "product"])],
    )
    qty = fields.Float(
        string="Quantity",
        digits="Product Unit of Measure",
        default=0.0,
    )
    uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        related="product_id.uom_id",
        readonly=True,
    )
