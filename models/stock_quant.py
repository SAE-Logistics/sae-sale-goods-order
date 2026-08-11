from collections import defaultdict

from odoo import api, fields, models
from odoo.tools.float_utils import float_round


class StockQuant(models.Model):
    _inherit = "stock.quant"

    container_count = fields.Integer(
        string="No. of Containers",
        compute="_compute_container_count",
    )

    def _compute_container_count(self):
        grouped = defaultdict(float)
        product_ids = self.mapped("product_id").ids
        location_ids = self.mapped("location_id").ids
        if product_ids and location_ids:
            moves = self.env["stock.move"].search([
                ("state", "=", "done"),
                ("container_count", ">", 0),
                ("container_type_id", "!=", False),
                ("product_id", "in", product_ids),
                "|",
                ("location_id", "in", location_ids),
                ("location_dest_id", "in", location_ids),
            ])
            for impact in moves._iter_container_impacts(date_to=fields.Date.context_today(self)):
                if impact["product_id"] not in product_ids or impact["location_id"] not in location_ids:
                    continue
                signed_qty = impact["quantity"] if impact["direction"] == "inward" else -impact["quantity"]
                grouped[(impact["product_id"], impact["location_id"])] += signed_qty

        for quant in self:
            quant.container_count = int(float_round(
                grouped.get((quant.product_id.id, quant.location_id.id), 0.0),
                precision_digits=0,
            ))

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        results = super().read_group(
            domain,
            fields,
            groupby,
            offset=offset,
            limit=limit,
            orderby=orderby,
            lazy=lazy,
        )
        if "container_count" not in fields:
            return results

        for group in results:
            group_domain = group.get("__domain")
            if not group_domain:
                continue
            group["container_count"] = sum(
                self.search(group_domain).mapped("container_count")
            )
        return results
