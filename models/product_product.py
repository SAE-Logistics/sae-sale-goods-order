# -*- coding: utf-8 -*-
from odoo import fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    is_surcharge_product = fields.Boolean(
        related="product_tmpl_id.is_surcharge_product",
        store=True,
        readonly=False,
        string="Surcharge",
    )
    goods_owner_customer_id = fields.Many2one(
        "res.partner",
        string="Category Customer",
        related="categ_id.goods_owner_customer_id",
        store=True,
        index=True,
        readonly=True,
    )
