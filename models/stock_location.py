from collections import defaultdict

from odoo import api, fields, models
from odoo.tools.float_utils import float_round


class StockLocation(models.Model):
    _inherit = "stock.location"

    storage_product_id = fields.Many2one(
        "product.product",
        string="Storage Product",
        domain="[('type', '=', 'service')]",
        help="Service product used for fixed rental billing and customer-specific pricelist pricing.",
    )
    max_pallet_capacity = fields.Integer(
        string="Max Pallets",
        help="Maximum number of pallets allowed in this location.",
    )
    container_count = fields.Integer(
        string="No. of Containers",
        compute="_compute_container_count",
    )

    def _compute_container_count(self):
        grouped = defaultdict(float)
        location_ids = self.ids
        if location_ids:
            container_lines = self.env["stock.picking.container"].search([
                ("state", "=", "done"),
                ("container_count", ">", 0),
                "|",
                ("location_id", "in", location_ids),
                ("location_dest_id", "in", location_ids),
            ])
            for impact in container_lines._iter_container_impacts(date_to=fields.Date.context_today(self)):
                if impact["location_id"] not in location_ids:
                    continue
                signed_qty = impact["quantity"] if impact["direction"] == "inward" else -impact["quantity"]
                grouped[impact["location_id"]] += signed_qty

        for location in self:
            location.container_count = int(float_round(
                grouped.get(location.id, 0.0),
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
