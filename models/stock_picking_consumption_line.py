from odoo import fields, models


class StockPickingConsumptionLine(models.Model):
    _name = "stock.picking.consumption.line"
    _description = "Consumed Products Summary for Picking"

    picking_id = fields.Many2one(
        "stock.picking",
        string="Delivery Order",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        required=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit of Measure",
        required=True,
    )
    quantity = fields.Float(
        string="Consumed Quantity",
        digits="Product Unit of Measure",
        required=True,
        default=0.0,
    )
