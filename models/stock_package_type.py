from odoo import api, fields, models


class StockPackageType(models.Model):
    _inherit = 'stock.package.type'

    product_id = fields.Many2one('product.product', string='Product')
    # Pallet package types are rated differently by the carriers, so the rate
    # API needs to know whether a consignment is palletised. Every pallet type
    # in use carries "Pallet" in its name and no other type does, so the name
    # is a reliable signal. Stored + readonly=False keeps it auto-detected but
    # overridable if a future type is named ambiguously.
    is_pallet = fields.Boolean(
        string='Is Pallet',
        compute='_compute_is_pallet',
        store=True,
        readonly=False,
        help='Detected from the package type name. Sent to the carrier rate API '
             'and used to work out the pallet quantity.',
    )
    container_move_count = fields.Integer(
        string='Container Moves',
        compute='_compute_container_move_count',
    )

    @api.depends('name')
    def _compute_is_pallet(self):
        for record in self:
            record.is_pallet = 'pallet' in (record.name or '').strip().lower()

    def _compute_container_move_count(self):
        grouped_data = self.env['stock.picking.container'].read_group(
            [('container_type_id', 'in', self.ids), ('state', '=', 'done')],
            ['container_type_id'],
            ['container_type_id'],
        )
        counts = {
            group['container_type_id'][0]: group['container_type_id_count']
            for group in grouped_data
            if group.get('container_type_id')
        }
        for record in self:
            record.container_move_count = counts.get(record.id, 0)

    def action_view_container_moves(self):
        self.ensure_one()
        return {
            'name': 'Container Moves',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking.container',
            'view_mode': 'list,form,pivot,graph',
            'domain': [
                ('container_type_id', '=', self.id),
                ('state', '=', 'done'),
            ],
            'context': {'search_default_done': 1},
        }


class SalePackageLine(models.Model):
    _name = 'sale.package.line'
    _description = 'Package Details'

    name = fields.Char(string='Name')
    package_type_id = fields.Many2one('stock.package.type', string='Package Type')
    order_line_id = fields.Many2one('sale.order.line', string='Order Line')
    picking_id = fields.Many2one('stock.picking', string='Picking')
    order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        compute='_compute_order_id',
        store=True,
    )
    length = fields.Float(string='Length')
    width = fields.Float(string='Width')
    height = fields.Float(string='Height')
    weight = fields.Float(string='Weight')
    quantity = fields.Integer(string='Quantity')

    @api.onchange('package_type_id')
    def _onchange_package_type_id_set_dimensions(self):
        for record in self:
            package_type = record.package_type_id
            if not package_type:
                continue
            record.length = package_type.packaging_length
            record.width = package_type.width
            record.height = package_type.height

    @api.depends('order_line_id.order_id', 'picking_id.sale_id')
    def _compute_order_id(self):
        for record in self:
            record.order_id = record.order_line_id.order_id or record.picking_id.sale_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            package_type_id = vals.get('package_type_id')
            if not package_type_id:
                continue
            package_type = self.env['stock.package.type'].browse(package_type_id)
            if 'length' not in vals:
                vals['length'] = package_type.packaging_length
            if 'width' not in vals:
                vals['width'] = package_type.width
            if 'height' not in vals:
                vals['height'] = package_type.height
        records = super().create(vals_list)
        lines = records.mapped('order_line_id').filtered(lambda line: line)
        if lines:
            lines._recompute_package_count_weight()
        done_pickings = records.mapped('picking_id').filtered(lambda picking: picking.state == 'done')
        if done_pickings and not self.env.context.get('skip_goods_order_sync'):
            done_pickings._update_goods_order_metrics()
        return records

    def write(self, vals):
        lines_before = self.mapped('order_line_id').filtered(lambda line: line)
        done_pickings = self.mapped('picking_id').filtered(lambda picking: picking.state == 'done')
        res = super().write(vals)
        lines_after = self.mapped('order_line_id').filtered(lambda line: line)

        # If sale_line_id changed, recompute both old and new lines
        (lines_before | lines_after)._recompute_package_count_weight()
        done_pickings |= self.mapped('picking_id').filtered(lambda picking: picking.state == 'done')
        if done_pickings and not self.env.context.get('skip_goods_order_sync'):
            done_pickings._update_goods_order_metrics()
        return res

    def unlink(self):
        lines = self.mapped('order_line_id').filtered(lambda line: line)
        done_pickings = self.mapped('picking_id').filtered(lambda picking: picking.state == 'done')
        res = super().unlink()
        if lines:
            lines._recompute_package_count_weight()
        if done_pickings and not self.env.context.get('skip_goods_order_sync'):
            done_pickings._update_goods_order_metrics()
        return res
