from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class StockMove(models.Model):
    _inherit = "stock.move"

    consumption_source_picking_id = fields.Many2one(
        "stock.picking",
        string="Consumption Source Picking",
        help="Original picking for which this move represents material consumption.",
    )

    def _check_goods_customer_product_allowed(self):
        for move in self:
            picking = move.picking_id
            sale = picking.sale_id
            product = move.product_id
            if not sale or sale.order_type not in ("goods_in", "goods_out") or not product:
                continue
            customer = sale.commercial_partner_id
            owner = product.goods_owner_customer_id
            if owner and owner != customer:
                raise ValidationError(_(
                    "Product '%(product)s' is not allowed for customer '%(customer)s'. "
                    "Configure the customer on the product category or one of its parent categories."
                ) % {
                    "product": product.display_name,
                    "customer": customer.display_name,
                })

    @api.constrains("product_id", "picking_id")
    def _constrain_goods_customer_product_allowed(self):
        self._check_goods_customer_product_allowed()
