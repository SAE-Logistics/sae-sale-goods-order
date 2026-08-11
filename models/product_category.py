# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        help="Customer that owns this category. Child categories inherit this customer unless they define their own.",
    )
    goods_owner_customer_id = fields.Many2one(
        "res.partner",
        string="Effective Customer",
        compute="_compute_goods_owner_customer_id",
        store=True,
        index=True,
        recursive=True,
        help="Customer inherited from this category or the nearest parent category.",
    )

    @api.depends("customer_id", "parent_id", "parent_id.goods_owner_customer_id")
    def _compute_goods_owner_customer_id(self):
        for category in self:
            customer = category.customer_id.commercial_partner_id
            category.goods_owner_customer_id = customer or category.parent_id.goods_owner_customer_id
