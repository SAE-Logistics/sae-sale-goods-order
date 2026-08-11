from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    commercial_partner_id = fields.Many2one('res.partner', string='Owner')
    rental_location_id = fields.Many2one(
        'stock.location',
        string='Rental Location',
        domain="[('usage', '=', 'internal'), ('id', 'child_of', view_location_id)]",
    )
    enable_auto_rental_refill = fields.Boolean(
        string='Enable Auto Rental Refill',
        default=True,
        help='When enabled, validating a goods-out transfer can create an internal transfer '
             'from the warehouse default location to the rental location when capacity is available.',
    )

    @api.constrains('rental_location_id')
    def _check_rental_location_id(self):
        for warehouse in self:
            if warehouse.rental_location_id and not self.env['stock.location'].search_count([
                ('id', '=', warehouse.rental_location_id.id),
                ('id', 'child_of', warehouse.view_location_id.id),
            ]):
                raise ValidationError(_("The rental location must be inside the warehouse hierarchy."))

    def _get_internal_location_ids(self):
        self.ensure_one()
        return self.env['stock.location'].search([
            ('id', 'child_of', self.view_location_id.id),
            ('usage', '=', 'internal'),
        ]).ids

    def _get_rental_location_ids(self):
        self.ensure_one()
        if not self.rental_location_id:
            return []
        return self.env['stock.location'].search([
            ('id', 'child_of', self.rental_location_id.id),
            ('usage', '=', 'internal'),
        ]).ids

    def _get_outside_rental_location_ids(self):
        self.ensure_one()
        internal_ids = set(self._get_internal_location_ids())
        return list(internal_ids - set(self._get_rental_location_ids()))

    def _is_pallet_container_type(self, container_type):
        self.ensure_one()
        return bool(container_type and 'pallet' in (container_type.name or '').lower())

    def _get_pallet_balance_for_location_ids(self, location_ids):
        self.ensure_one()
        if not location_ids:
            return 0
        warehouse_location_ids = set(self._get_internal_location_ids())
        scoped_location_ids = list(warehouse_location_ids & set(location_ids))
        if not scoped_location_ids:
            return 0

        total = 0
        pickings = self.env['stock.picking'].search([
            ('state', '=', 'done'),
            ('pallet_qty', '>', 0),
            '|',
            ('location_id', 'in', scoped_location_ids),
            ('location_dest_id', 'in', scoped_location_ids),
        ])
        for picking in pickings:
            pallets = max(int(picking.pallet_qty or 0), 0)
            if not pallets:
                continue
            if picking.location_dest_id.id in scoped_location_ids and picking.location_dest_id.usage == 'internal':
                total += pallets
            if picking.location_id.id in scoped_location_ids and picking.location_id.usage == 'internal':
                total -= pallets
        return total

    def _get_rental_location_pallet_count(self):
        self.ensure_one()
        return self._get_pallet_balance_for_location_ids(self._get_rental_location_ids())

    def _get_default_location_pallet_count(self):
        self.ensure_one()
        if not self.lot_stock_id:
            return 0
        return self._get_pallet_balance_for_location_ids([self.lot_stock_id.id])

    def _get_available_rental_pallet_capacity(self):
        self.ensure_one()
        if not self.rental_location_id or self.rental_location_id.max_pallet_capacity <= 0:
            return 0
        return max(
            self.rental_location_id.max_pallet_capacity - self._get_rental_location_pallet_count(),
            0,
        )

    def _count_billing_months(self, date_from, date_to):
        self.ensure_one()
        month_count = 0
        current = fields.Date.start_of(date_from, 'month')
        last = fields.Date.start_of(date_to, 'month')
        while current <= last:
            month_count += 1
            current = fields.Date.add(current, months=1)
        return month_count

    def _get_outside_rental_container_weeks(self, date_from, date_to):
        self.ensure_one()
        if not self.commercial_partner_id:
            return {}
        outside_location_ids = self._get_outside_rental_location_ids()
        if not outside_location_ids:
            return {}

        total_container_weeks = {}
        for row in self._get_outside_rental_weekly_rows(date_from, date_to):
            closing = row.get('closing', 0.0)
            if closing <= 0:
                continue
            container_type_id = row['container_type_id']
            total_container_weeks[container_type_id] = (
                total_container_weeks.get(container_type_id, 0.0) + closing
            )
        return total_container_weeks

    def _get_outside_rental_weekly_rows(self, date_from, date_to):
        self.ensure_one()
        if not self.commercial_partner_id:
            return []
        outside_location_ids = self._get_outside_rental_location_ids()
        if not outside_location_ids:
            return []

        container_model = self.env['stock.picking.container']
        opening_snapshot = container_model.get_container_balance_snapshot(
            fields.Date.subtract(date_from, days=1),
            partner_id=self.commercial_partner_id.id,
            location_ids=outside_location_ids,
        )
        current_opening_by_type = {}
        for (_, container_type_id, _), quantity in opening_snapshot.items():
            current_opening_by_type[container_type_id] = max(
                current_opening_by_type.get(container_type_id, 0.0) + quantity,
                0.0,
            )

        rows = []
        current_week_start = date_from
        while current_week_start <= date_to:
            current_week_end = min(current_week_start + timedelta(days=6), date_to)
            inward_by_type = {}
            outward_by_type = {}
            container_type_ids = set(current_opening_by_type)

            for impact in container_model._iter_container_impacts(
                date_from=current_week_start,
                date_to=current_week_end,
                partner_id=self.commercial_partner_id.id,
            ):
                line = impact['line']
                if line.location_id.id == line.location_dest_id.id:
                    continue
                if impact['location_id'] not in outside_location_ids:
                    continue
                container_type_id = impact['container_type_id']
                container_type_ids.add(container_type_id)
                if impact['direction'] == 'inward':
                    inward_by_type[container_type_id] = (
                        inward_by_type.get(container_type_id, 0.0) + impact['quantity']
                    )
                else:
                    outward_by_type[container_type_id] = (
                        outward_by_type.get(container_type_id, 0.0) + impact['quantity']
                    )

            next_opening_by_type = dict(current_opening_by_type)
            for container_type_id in sorted(container_type_ids):
                opening = max(current_opening_by_type.get(container_type_id, 0.0), 0.0)
                inward = inward_by_type.get(container_type_id, 0.0)
                outward = outward_by_type.get(container_type_id, 0.0)
                closing = opening + inward
                next_opening_by_type[container_type_id] = max(closing - outward, 0.0)
                if not opening and not inward and not outward and not closing:
                    continue
                rows.append({
                    'week_start': current_week_start,
                    'week_end': current_week_end,
                    'container_type_id': container_type_id,
                    'opening': opening,
                    'inward': inward,
                    'outward': outward,
                    'closing': closing,
                })

            current_opening_by_type = next_opening_by_type
            current_week_start = current_week_end + timedelta(days=1)

        return rows
