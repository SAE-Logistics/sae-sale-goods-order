# stock_materials_consumption/models/material_template.py
from odoo import api, fields, models


class SaleMaterialTemplate(models.Model):
    _name = "sale.material.template"
    _description = "Materials Template for Sales"
    _rec_name = "name"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True
    )
    picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Materials Picking Type",
        help="Picking Type that will be used when creating material consumption pickings.",
    )
    line_ids = fields.One2many(
        "sale.material.template.line", "template_id", string="Material Lines"
    )


class SaleMaterialTemplateLine(models.Model):
    _name = "sale.material.template.line"
    _description = "Materials Template Line"

    template_id = fields.Many2one(
        "sale.material.template",
        string="Template",
        ondelete="cascade",
        required=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
        domain=[("type", "in", ["consu"])],
    )
    default_qty = fields.Float(
        string="Default Quantity",
        digits="Product Unit of Measure",
        default=0.0,
    )
    uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        related="product_id.uom_id",
        readonly=True,
    )


# class ResCompany(models.Model):
#     _inherit = "res.company"
#
#     sale_material_template_id = fields.Many2one(
#         "sale.material.template",
#         string="Default Materials Template",
#     )
#
