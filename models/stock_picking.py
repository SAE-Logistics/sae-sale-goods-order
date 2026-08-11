from collections import defaultdict

from odoo import fields, models
from odoo.tools.float_utils import float_round


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    is_material_picking = fields.Boolean(
        string="Materials",
        help="Marks this picking as a materials consumption picking.",
    )
    # The picking this material picking was created from
    source_picking_id = fields.Many2one(
        "stock.picking",
        string="Source Picking",
        help="Original picking for which these materials were consumed.",
    )
    # All material consumption pickings created from this one
    material_consumption_ids = fields.One2many(
        "stock.picking",
        "source_picking_id",
        string="Material Consumptions",
    )
    # Flattened list of consumed moves tied back to this picking
    consumed_move_ids = fields.One2many(
        "stock.move",
        "consumption_source_picking_id",
        string="Consumed Products",
    )

    consumed_product_line_ids = fields.One2many(
        "stock.picking.consumption.line",
        "picking_id",
        string="Consumed Products Summary",
    )
    transport_leg_ids = fields.One2many(
        'sale.transport.leg',
        'picking_id',
        string='Transport Legs',
    )
    package_ids = fields.One2many(
        'sale.package.line',
        'picking_id',
        string='Package Details',
    )
    pallet_qty = fields.Integer(string='Pallets', default=0)
    sale_order_type = fields.Selection(
        related='sale_id.order_type',
        string='Sale Order Type',
    )
    sale_goods_customer_id = fields.Many2one(
        'res.partner',
        related='sale_id.commercial_partner_id',
        string='Sale Customer',
    )
    sale_no_transport_needed = fields.Boolean(
        related='sale_id.no_transport_needed',
        string='No Transport Needed',
    )

    collect_note = fields.Text(related='sale_id.collect_note')
    deliver_note = fields.Text(related='sale_id.deliver_note')

    def action_open_material_wizard(self):
        """Button on picking: open wizard with template lines."""
        self.ensure_one()
        # Make sure there is an SO linked
        sale = self.sale_id
        if not sale:
            # you can change to UserError if you prefer
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "No Sale Order",
                    "message": "This transfer is not linked to a Sale Order.",
                    "sticky": False,
                    "type": "warning",
                },
            }

        wizard = self.env["material.picking.wizard"].create({
            "picking_id": self.id,
            "sale_id": sale.id,
        })
        wizard._load_lines_from_template()

        return {
            "name": "Add Materials",
            "type": "ir.actions.act_window",
            "res_model": "material.picking.wizard",
            "view_mode": "form",
            "target": "new",
            "res_id": wizard.id,
        }

    def button_validate(self):
        incoming_pickings = self.filtered(lambda p: p.picking_type_code == 'incoming')
        incoming_pickings._route_incoming_to_rental_or_default()

        res = super().button_validate()
        done_pickings = self.filtered(lambda p: p.state == 'done')
        if done_pickings:
            done_pickings._update_goods_order_metrics()
        return res


    def write(self, vals):
        res = super().write(vals)
        if self.filtered(lambda p: p.state == 'done') and (
            'move_line_ids' in vals or 'package_ids' in vals
        ):
            self._update_goods_order_metrics()
        return res


    def _update_goods_order_metrics(self):
        sales = self.filtered(
            lambda p: (
                not p.is_material_picking
                and p.sale_id
                and p.sale_id.order_type in ['goods_in', 'goods_out']
            )
        ).mapped('sale_id')
        for sale in sales:
            done_pickings = sale.picking_ids.filtered(
                lambda p: not p.is_material_picking and p.state == 'done'
            )
            metrics = defaultdict(float)
            for picking in done_pickings:
                picking_metrics = self._collect_metrics_from_picking(picking)
                for key, qty in picking_metrics.items():
                    metrics[key] += qty
            self._apply_metrics_to_sale_order(sale, metrics)
            self._clear_legacy_pallet_sale_lines(sale)
            self._sync_package_sale_lines(sale, done_pickings)

    def _collect_metrics_from_picking(self, picking):
        metrics = defaultdict(float)

        done_lines = picking.move_line_ids.filtered(lambda l: l.quantity > 0)

        products = set(done_lines.mapped('product_id'))
        if picking.sale_id.picking_type == 'sku':
            metrics['sku_in' if picking.picking_type_code == 'incoming' else 'sku_out'] = len(products)
        elif picking.sale_id.picking_type == 'qty':
            metrics['qty_in' if picking.picking_type_code == 'incoming' else 'qty_out'] = sum(done_lines.mapped('quantity'))
        lots = set()
        serials = set()
        expiries = set()
        for line in done_lines:
            lot = line.lot_id or line.lot_name
            if lot:
                if line.product_id.tracking == 'lot':
                    lots.add(lot.id if hasattr(lot, 'id') else lot)
                if line.product_id.tracking == 'serial':
                    serials.add(lot.id if hasattr(lot, 'id') else lot)
            lot_rec = line.lot_id
            if lot_rec and (lot_rec.removal_date or lot_rec.use_date or lot_rec.expiration_date):
                expiries.add(lot_rec.id)

        metrics['lot_count'] = len(lots)
        metrics['serial_count'] = len(serials)
        metrics['expiry_count'] = len(expiries)
        metrics['order_receipt'] = 1
        return metrics

    def _apply_metrics_to_sale_order(self, sale, metrics):
        config = self.env['ir.config_parameter'].sudo()
        mapping = {
            'order_receipt': config.get_param('sale_gto.product_order_receipt_id', False),
            'sku_in': config.get_param('sale_gto.product_sku_in_id', False),
            'qty_in': config.get_param('sale_gto.product_qty_in_id', False),
            'sku_out': config.get_param('sale_gto.product_sku_out_id', False),
            'qty_out': config.get_param('sale_gto.product_qty_out_id', False),
            'serial_count': config.get_param('sale_gto.product_serial_id', False),
            'lot_count': config.get_param('sale_gto.product_lot_id', False),
            'expiry_count': config.get_param('sale_gto.product_expiry_id', False),
        }

        product_quantities = defaultdict(float)
        for key, qty in metrics.items():
            product_id = mapping.get(key)
            if not product_id or qty <= 0:
                continue
            product_quantities[int(product_id)] += qty

        for product_id, qty in product_quantities.items():
            product = self.env['product.product'].browse(product_id)
            if not product:
                continue

            line = sale.order_line.filtered(lambda l: l.product_id == product and not l.display_type)
            if not line:
                line = self.env['sale.order.line'].create({
                    'order_id': sale.id,
                    'product_id': product.id,
                    'name': product.get_product_multiline_description_sale() or product.display_name,
                    'product_uom_qty': qty,
                    'product_uom': product.uom_id.id,
                    'price_unit': product.lst_price,
                })
            else:
                line = line[0]
                line.write({
                    'name': product.get_product_multiline_description_sale() or product.display_name,
                    'product_uom_qty': qty,
                })

            if hasattr(line, 'qty_delivered_method') and line.qty_delivered_method != 'manual':
                line.qty_delivered_method = 'manual'

            if hasattr(line, 'qty_delivered_manual'):
                line.qty_delivered_manual = qty
            else:
                line.qty_delivered = qty

    def _clear_legacy_pallet_sale_lines(self, sale):
        config = self.env['ir.config_parameter'].sudo()
        pallet_product_ids = {
            int(product_id)
            for product_id in (
                config.get_param('sale_gto.product_pallets_in_id', False),
                config.get_param('sale_gto.product_pallets_out_id', False),
            )
            if product_id
        }
        if not pallet_product_ids:
            return

        legacy_lines = sale.order_line.filtered(
            lambda line: (
                not line.display_type
                and line.product_id
                and line.product_id.id in pallet_product_ids
                and not line.package_type_id
            )
        )
        for line in legacy_lines:
            vals = {'product_uom_qty': 0.0}
            if hasattr(line, 'qty_delivered_method') and line.qty_delivered_method != 'manual':
                vals['qty_delivered_method'] = 'manual'
            line.write(vals)
            if hasattr(line, 'qty_delivered_manual'):
                line.qty_delivered_manual = 0.0
            else:
                line.qty_delivered = 0.0

    def _sync_package_sale_lines(self, sale, done_pickings):
        package_groups = {}
        for package_line in done_pickings.mapped('package_ids').filtered('package_type_id'):
            package_type = package_line.package_type_id
            package_product = package_type.product_id
            if not package_product:
                continue
            group = package_groups.setdefault(
                package_type.id,
                {
                    'package_type': package_type,
                    'product': package_product,
                    'quantity': 0.0,
                    'package_lines': self.env['sale.package.line'],
                },
            )
            group['quantity'] += package_line.quantity or 0.0
            group['package_lines'] |= package_line

        managed_lines = sale.order_line.filtered(
            lambda line: not line.display_type and line.container_charge_line and line.package_type_id
        )
        active_package_type_ids = set(package_groups)

        for package_type_id, group in package_groups.items():
            package_type = group['package_type']
            product = group['product']
            quantity = group['quantity']
            package_lines = group['package_lines']
            line = managed_lines.filtered(lambda sale_line: sale_line.package_type_id == package_type)[:1]

            values = {
                'order_id': sale.id,
                'product_id': product.id,
                'name': product.get_product_multiline_description_sale() or product.display_name,
                'product_uom_qty': quantity,
                'product_uom': product.uom_id.id,
                'price_unit': product.lst_price,
                'package_type_id': package_type.id,
                'container_charge_line': True,
            }
            if not line:
                line = self.env['sale.order.line'].create(values)
            else:
                line.write(values)

            delivered_vals = {}
            if hasattr(line, 'qty_delivered_method') and line.qty_delivered_method != 'manual':
                delivered_vals['qty_delivered_method'] = 'manual'
            if delivered_vals:
                line.write(delivered_vals)
            if hasattr(line, 'qty_delivered_manual'):
                line.qty_delivered_manual = quantity
            else:
                line.qty_delivered = quantity

            package_lines.with_context(skip_goods_order_sync=True).write({
                'order_line_id': line.id,
            })
            line._recompute_package_count_weight()

        stale_lines = managed_lines.filtered(
            lambda line: line.package_type_id.id not in active_package_type_ids
        )
        for line in stale_lines:
            linked_packages = line.package_ids.filtered(
                lambda package: package.picking_id in done_pickings
            )
            if linked_packages:
                linked_packages.with_context(skip_goods_order_sync=True).write({
                    'order_line_id': False,
                })
            vals = {'product_uom_qty': 0.0}
            if hasattr(line, 'qty_delivered_method') and line.qty_delivered_method != 'manual':
                vals['qty_delivered_method'] = 'manual'
            line.write(vals)
            if hasattr(line, 'qty_delivered_manual'):
                line.qty_delivered_manual = 0.0
            else:
                line.qty_delivered = 0.0
            line._recompute_package_count_weight()

    def _get_pallet_qty(self):
        self.ensure_one()
        return max(int(self.pallet_qty or 0), 0)

    def _route_incoming_to_rental_or_default(self):
        for picking in self:
            warehouse = picking.picking_type_id.warehouse_id
            rental_location = warehouse.rental_location_id
            default_location = warehouse.lot_stock_id
            if not warehouse or not rental_location or not default_location:
                continue

            available_capacity = warehouse._get_available_rental_pallet_capacity()
            pallet_count = picking._get_pallet_qty()
            target_location = rental_location
            if pallet_count and pallet_count > available_capacity:
                target_location = default_location

            move_updates = []
            for move in picking.move_ids_without_package.filtered(
                lambda m: m.state not in ('done', 'cancel')
            ):
                if move.location_dest_id != target_location:
                    move_updates.append((move, target_location))

            for move, target_location in move_updates:
                move.write({'location_dest_id': target_location.id})
                move.move_line_ids.write({'location_dest_id': target_location.id})

    def _get_relocation_quant_data(self, warehouse, max_pallets_to_move):
        self.ensure_one()
        if max_pallets_to_move <= 0 or not warehouse.lot_stock_id:
            return [], 0

        quants = self.env['stock.quant'].search(
            [
                ('location_id', '=', warehouse.lot_stock_id.id),
                ('quantity', '>', 0),
            ],
            order='id',
        )
        move_values = []
        remaining_capacity = max_pallets_to_move
        total_pallets_to_move = remaining_capacity
        if total_pallets_to_move <= 0:
            return [], 0

        total_available_qty = sum(
            getattr(quant, 'available_quantity', quant.quantity)
            for quant in quants
            if getattr(quant, 'available_quantity', quant.quantity) > 0
        )
        if total_available_qty <= 0:
            return [], 0

        allocated_pallets = 0
        for quant in quants:
            if remaining_capacity <= 0:
                break
            available_quantity = getattr(quant, 'available_quantity', quant.quantity)
            if available_quantity <= 0:
                continue

            qty_share = available_quantity / total_available_qty
            pallets_to_move = int(float_round(total_pallets_to_move * qty_share, precision_digits=0))
            if pallets_to_move <= 0 and remaining_capacity > 0:
                pallets_to_move = 1
            pallets_to_move = min(pallets_to_move, remaining_capacity)
            qty_to_move = float_round(available_quantity * min(pallets_to_move, total_pallets_to_move) / total_pallets_to_move, precision_rounding=quant.product_id.uom_id.rounding)
            if qty_to_move <= 0:
                continue

            move_values.append({
                'name': quant.product_id.display_name,
                'product_id': quant.product_id.id,
                'product_uom_qty': qty_to_move,
                'product_uom': quant.product_id.uom_id.id,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': warehouse.rental_location_id.id,
            })
            remaining_capacity -= pallets_to_move
            allocated_pallets += pallets_to_move

        return move_values, allocated_pallets

    def _create_internal_transfer_to_rental(self):
        warehouses = self.mapped('picking_type_id.warehouse_id').filtered(
            lambda w: (
                w.enable_auto_rental_refill
                and w.rental_location_id
                and w.lot_stock_id
                and w.int_type_id
            )
        )
        for warehouse in warehouses:
            existing_transfer = self.env['stock.picking'].search(
                [
                    ('state', 'not in', ('done', 'cancel')),
                    ('picking_type_id', '=', warehouse.int_type_id.id),
                    ('location_id', '=', warehouse.lot_stock_id.id),
                    ('location_dest_id', '=', warehouse.rental_location_id.id),
                ],
                limit=1,
            )
            available_capacity = warehouse._get_available_rental_pallet_capacity()
            if available_capacity <= 0 or warehouse._get_default_location_pallet_count() <= 0:
                continue

            existing_pallets = max(int(existing_transfer.pallet_qty or 0), 0)
            remaining_capacity = max(available_capacity - existing_pallets, 0)
            if remaining_capacity <= 0:
                continue

            move_values, pallets_to_move = self._get_relocation_quant_data(warehouse, remaining_capacity)
            if not move_values or not pallets_to_move:
                continue

            if existing_transfer:
                write_vals = {}
                if move_values:
                    write_vals['move_ids_without_package'] = [(0, 0, values) for values in move_values]
                write_vals['pallet_qty'] = existing_pallets + pallets_to_move
                existing_transfer.write(write_vals)
                picking = existing_transfer
            else:
                create_vals = {
                    'picking_type_id': warehouse.int_type_id.id,
                    'location_id': warehouse.lot_stock_id.id,
                    'location_dest_id': warehouse.rental_location_id.id,
                    'origin': 'Auto Refill Rental Location',
                    'pallet_qty': pallets_to_move,
                }
                if move_values:
                    create_vals['move_ids_without_package'] = [(0, 0, values) for values in move_values]
                picking = self.env['stock.picking'].create(create_vals)
            picking.action_confirm()
            picking.action_assign()

    def _recompute_consumed_products(self):
        """
        Rebuild the aggregated consumed products for each picking, based on
        all DONE material consumption moves linked via consumption_source_picking_id.
        """
        Move = self.env["stock.move"]
        Line = self.env["stock.picking.consumption.line"]

        for picking in self:
            # Find all done moves that consumed materials for this picking
            moves = Move.search([
                ("consumption_source_picking_id", "=", picking.id),
                ("state", "=", "done"),
            ])

            # Group by (product, uom)
            aggregated = {}
            for move in moves:
                key = (move.product_id.id, move.product_uom.id)
                qty = move.quantity or move.product_uom_qty
                aggregated[key] = aggregated.get(key, 0.0) + qty

            # Remove old summary lines
            picking.consumed_product_line_ids.unlink()

            # Create new summary lines
            for (product_id, uom_id), qty in aggregated.items():
                Line.create({
                    "picking_id": picking.id,
                    "product_id": product_id,
                    "product_uom_id": uom_id,
                    "quantity": qty,
                })
    def _action_done(self):
        res = super()._action_done()
        self._update_goods_order_metrics()
        self.filtered(
            lambda p: p.picking_type_code == 'outgoing' and p.state == 'done'
        )._create_internal_transfer_to_rental()
        material_pickings = self.filtered(
            lambda p: p.is_material_picking and p.state == "done" and p.source_picking_id
        )
        for mat_picking in material_pickings:
            mat_picking.source_picking_id._recompute_consumed_products()
        return res
    #     if self.sale_id and self.sale_id.order_type != 'standard':
    #         print("called during deliveries as well")
    #         return res
    #     sale_order_lines_vals = []
    #     for move in self.move_ids:
    #         sale_order = move.picking_id.sale_id
    #         # Creates new SO line only when pickings linked to a sale order and
    #         # for moves with qty. done and not already linked to a SO line.
    #         if not sale_order or move.sale_line_id or not move.picked or not (
    #             (move.location_dest_id.usage in ['customer', 'transit'] and not move.move_dest_ids)
    #             or (move.location_id.usage == 'customer' and move.to_refund)
    #         ):
    #             continue
    #         product = move.product_id
    #         quantity = move.quantity
    #         if move.to_refund:
    #             quantity *= -1
    #
    #         so_line_vals = {
    #             'move_ids': [(4, move.id, 0)],
    #             'name': product.display_name,
    #             'order_id': sale_order.id,
    #             'product_id': product.id,
    #             'product_uom_qty': 0,
    #             'qty_delivered': quantity,
    #             'product_uom': move.product_uom.id,
    #         }
    #         so_line = sale_order.order_line.filtered(lambda sol: sol.product_id == product)
    #         if product.invoice_policy == 'delivery':
    #             # Check if there is already a SO line for this product to get
    #             # back its unit price (in case it was manually updated).
    #             if so_line:
    #                 so_line_vals['price_unit'] = so_line[0].price_unit
    #         elif product.invoice_policy == 'order':
    #             # No unit price if the product is invoiced on the ordered qty.
    #             so_line_vals['price_unit'] = 0
    #         # New lines should be added at the bottom of the SO (higher sequence number)
    #         if not so_line:
    #             so_line_vals['sequence'] = max(sale_order.order_line.mapped('sequence')) + len(sale_order_lines_vals) + 1
    #         sale_order_lines_vals.append(so_line_vals)
    #
    #     if sale_order_lines_vals:
    #         self.env['sale.order.line'].with_context(skip_procurement=True).create(sale_order_lines_vals)
    #     return res
