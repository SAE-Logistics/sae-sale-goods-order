from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


ORDER_TYPE = [
        ('standard', 'Standard'),
        ('goods_in', 'Goods In Order'),
        ('goods_out', 'Goods Out Order'),
        ('transport', 'Transport Order'),
    ]

PICKING_TYPE = [
        ('sku', 'SKU'),
        ('qty', 'QTY'),
        ('labour', 'Labour'),
        ('emg', 'EMG'),
    ]
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # order_type = fields.Selection(ORDER_TYPE, default=lambda self: self._context.get('order_type', 'standard'), required=True, index=True)
    order_type = fields.Selection(related='sale_order_template_id.order_type')
    picking_type = fields.Selection(related='sale_order_template_id.picking_type')
    materials_picking_count = fields.Integer(
        string="Materials",
        compute="_compute_materials_picking_count",
    )
    commercial_partner_id = fields.Many2one(related='partner_id.commercial_partner_id', store=True)
    cost_centre_id = fields.Many2one('partner.cost.centre', string='Cost Centre')
    collect_note = fields.Text(string='Collection Note')
    deliver_note = fields.Text(string='Delivery Note')

    product_line_ids = fields.One2many(
        "transport.product.line",
        "sale_order_id",
        string="Transport Product Lines",
        copy=True,
    )

    transport_from_id = fields.Many2one(
        "res.partner",
        string="From (Pickup Address)",
        domain="[('type', 'in', ('delivery', 'contact', 'other'))]",
        help="Pickup location for transport orders",
    )

    transport_to_id = fields.Many2one(
        "res.partner",
        string="To (Delivery Address)",
        domain="[('type', 'in', ('delivery', 'contact', 'other'))]",
        help="Drop-off location for transport orders",
    )

    collection_address_id = fields.Many2one(
        'res.partner',
        string='Collection Address',
        help='Customer address from which goods will be collected for Goods In orders.',
    )

    transport_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("scheduled", "Scheduled"),
            ("in_transit", "In Transit"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        string="Transport State",
        default="pending",
        tracking=True,
    )

    # rep_name = fields.Char(string="Rep Name")
    label_email = fields.Char(string="Collection Label Email")
    order_note = fields.Text(string="Order Notes")
    no_transport_needed = fields.Boolean(
        string="No Transport Needed",
        copy=False,
        help="Enable this when no transport legs should be added for this goods order.",
    )
    has_transport_legs = fields.Boolean(
        string="Has Transport Legs",
        compute="_compute_has_transport_legs",
    )
    transport_all_leg_ids = fields.One2many(
        'sale.transport.leg',
        'order_id',
        string='Transport Legs',
        readonly=True,
    )
    transport_leg_tag_ids = fields.Many2many(
        'sale.transport.leg',
        string='Leg Status',
        compute='_compute_transport_leg_tag_ids',
    )
    transport_leg_status = fields.Selection(
        [
            ('to_do', 'To Do'),
            ('missing', 'Missing'),
            ('not_needed', 'Not Needed'),
            ('completed', 'Completed'),
        ],
        string='Transport Status',
        compute='_compute_transport_leg_status',
        store=True,
        index=True,
    )

    @api.depends('transport_all_leg_ids')
    def _compute_has_transport_legs(self):
        for order in self:
            order.has_transport_legs = bool(order.transport_all_leg_ids)

    @api.depends(
        'transport_all_leg_ids',
        'transport_all_leg_ids.state',
        'transport_all_leg_ids.from_location.zip',
        'transport_all_leg_ids.to_location.zip',
    )
    def _compute_transport_leg_tag_ids(self):
        for order in self:
            order.transport_leg_tag_ids = order.transport_all_leg_ids

    @api.depends('no_transport_needed', 'transport_all_leg_ids', 'transport_all_leg_ids.state')
    def _compute_transport_leg_status(self):
        for order in self:
            legs = order.transport_all_leg_ids
            if order.no_transport_needed:
                order.transport_leg_status = 'not_needed'
            elif not legs:
                order.transport_leg_status = 'missing'
            elif any(leg.state != 'completed' for leg in legs):
                order.transport_leg_status = 'to_do'
            else:
                order.transport_leg_status = 'completed'

    def _mark_transport_leg_lines_delivered(self):
        for order in self:
            legs = order.transport_all_leg_ids
            if not legs or any(leg.state != 'completed' for leg in legs):
                continue

            lines = (
                legs.mapped('order_line_id')
                | order.order_line.filtered('transport_surcharge_charge_line')
            ).filtered(lambda line: not line.display_type)
            for line in lines:
                if hasattr(line, 'qty_delivered_method') and line.qty_delivered_method != 'manual':
                    line.qty_delivered_method = 'manual'
                if hasattr(line, 'qty_delivered_manual'):
                    line.qty_delivered_manual = line.product_uom_qty
                else:
                    line.qty_delivered = line.product_uom_qty

    def _check_no_transport_needed_allowed(self):
        for order in self:
            if order.no_transport_needed and order.has_transport_legs:
                raise ValidationError(_("You cannot enable No Transport Needed once transport legs are already linked to the sale order."))

    @api.model_create_multi
    def create(self, vals_list):
        self._set_order_type_sequence_names(vals_list)
        orders = super().create(vals_list)
        orders._check_no_transport_needed_allowed()
        return orders

    def _set_order_type_sequence_names(self, vals_list):
        sequence_by_order_type = {
            'goods_in': 'sale.order.goods.in',
            'goods_out': 'sale.order.goods.out',
            'transport': 'sale.order.transport',
        }
        template_ids = [
            vals.get('sale_order_template_id')
            for vals in vals_list
            if vals.get('sale_order_template_id')
        ]
        templates = self.env['sale.order.template'].browse(template_ids)
        template_by_id = {template.id: template for template in templates}
        for vals in vals_list:
            if vals.get('name', _('New')) != _('New'):
                continue
            template = template_by_id.get(vals.get('sale_order_template_id'))
            sequence_code = sequence_by_order_type.get(template.order_type if template else False)
            if not sequence_code:
                continue
            seq_date = fields.Datetime.context_timestamp(
                self, fields.Datetime.to_datetime(vals['date_order'])
            ) if vals.get('date_order') else None
            vals['name'] = (
                self.env['ir.sequence']
                .with_company(vals.get('company_id'))
                .next_by_code(sequence_code, sequence_date=seq_date)
                or _('New')
            )

    def write(self, vals):
        if vals.get('no_transport_needed'):
            for order in self:
                if order.order_line.mapped('transport_leg_ids'):
                    raise ValidationError(_("You cannot enable No Transport Needed once transport legs are already linked to the sale order."))
        res = super().write(vals)
        if 'fiscal_position_id' in vals:
            self.order_line._refresh_product_taxes_from_product()
        return res

    def _get_warehouse_delivery_address(self, warehouse):
        self.ensure_one()
        if not warehouse.partner_id:
            return self.env["res.partner"]

        address_ids = warehouse.partner_id.address_get(["delivery", "contact"])
        shipping_partner_id = (
            address_ids.get("delivery")
            or address_ids.get("contact")
            or warehouse.partner_id.id
        )
        return self.env["res.partner"].browse(shipping_partner_id)

    @api.depends('partner_id', 'warehouse_id', 'order_type')
    def _compute_partner_shipping_id(self):
        super()._compute_partner_shipping_id()
        for order in self:
            if order.order_type == 'goods_out':
                order.partner_shipping_id = False
                continue
            if order.order_type != 'goods_in' or not order.warehouse_id.partner_id:
                continue
            order.partner_shipping_id = order._get_warehouse_delivery_address(
                order.warehouse_id
            )

    @api.onchange('order_type')
    def _onchange_order_type_set_template(self):
        if self.order_type in ['goods_in', 'goods_out'] and not self.sale_order_template_id:
            tmpl_xmlid = self.env.context.get('default_template_xmlid')
            if tmpl_xmlid:
                self.sale_order_template_id = self.env.ref(tmpl_xmlid, raise_if_not_found=False)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        res = super()._onchange_partner_id()
        for order in self:
            if (
                order.collection_address_id
                and order.collection_address_id.commercial_partner_id != order.partner_id.commercial_partner_id
            ):
                order.collection_address_id = False
        self._compute_warehouse_id()
        self._compute_partner_shipping_id()
        return res

    @api.onchange('warehouse_id', 'order_type')
    def _onchange_warehouse_id_set_goods_in_shipping(self):
        self._compute_partner_shipping_id()

    @api.constrains('sale_order_template_id', 'partner_shipping_id', 'collection_address_id')
    def _check_required_goods_addresses(self):
        for order in self:
            if order.order_type == 'goods_out' and not order.partner_shipping_id:
                raise ValidationError(_("Delivery Address is required for Goods Out orders."))
            if order.order_type == 'goods_in' and not order.collection_address_id:
                raise ValidationError(_("Collection Address is required for Goods In orders."))

    def _compute_has_only_service_products(self):
        """
        Helper to determine if the sale order contains only service products.
        """
        # Check if all lines are service type, or if there are no lines
        return all(line.product_id.type == 'service' for line in self.order_line.filtered(lambda sol: not sol.display_type))

    def _create_picking_for_service_only(self):
        """
        Creates a stock.picking record even if the sale order contains only service products.
        This method is called after the standard picking creation process.
        """
        # Check if the standard Odoo process created any pickings
        # and if the order contains only service products (which means no pickings were created)
        if not self.picking_ids:
            # Get the default picking type for sales orders (usually 'Outgoing Shipments')
            picking_type = self.warehouse_id.out_type_id if self.order_type == 'goods_out' else self.warehouse_id.in_type_id

            # Prepare the values for the new stock.picking record
            picking_vals = {
                'partner_id': self.partner_shipping_id.id,
                'origin': self.name,
                'company_id': self.company_id.id,
                'picking_type_id': picking_type.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'sale_id': self.id,
            }

            # Create the picking record
            picking = self.env['stock.picking'].create(picking_vals)

            # Link the picking to the sale order (Odoo will do this automatically via sale_id,
            # but we can ensure it's in the picking_ids list for consistency)
            # order.write({'picking_ids': [(4, picking.id)]})

            # Log the creation for debugging/auditing
            self.message_post(body=f"Manually created Stock Picking {picking.name} for {next(value for key, value in ORDER_TYPE if key == self.order_type)}.")

    def action_confirm(self):
        """
        Overrides the standard action_confirm to ensure a picking is created
        if the order only contains service products.
        """
        self._check_required_goods_addresses()
        # 1. Execute the standard Odoo confirmation logic
        res = super(SaleOrder, self).action_confirm()
        # 2. After confirmation, check if we need to create a manual picking
        for order in self:
            if order.state == 'sale' and order._compute_has_only_service_products() and order.order_type in ['goods_in', 'goods_out']:
                order._create_picking_for_service_only()

        return res

    def _compute_materials_picking_count(self):
        Picking = self.env["stock.picking"]
        for order in self:
            count = Picking.search_count([
                ("sale_id", "=", order.id),
                ("is_material_picking", "=", True),
            ])
            order.materials_picking_count = count

    def action_view_materials_pickings(self):
        self.ensure_one()
        action = self.env.ref("stock.action_picking_tree_all").read()[0]
        action["domain"] = [
            ("sale_id", "=", self.id),
            ("is_material_picking", "=", True),
        ]
        action["context"] = {
            "default_sale_id": self.id,
            "default_is_material_picking": True,
        }
        return action

    @api.depends('picking_ids')
    def _compute_picking_ids(self):
        for order in self:
            order.delivery_count = len(order.picking_ids.filtered(lambda p: not p.is_material_picking))

    def action_view_delivery(self):
        return self._get_action_view_picking(self.picking_ids.filtered(lambda p: not p.is_material_picking))

    def _sync_transport_surcharge_sale_lines(self):
        for order in self:
            if not order:
                continue
            surcharge_lines = self.env['sale.transport.leg.surcharge.line'].search([
                ('order_id', '=', order.id),
                ('surcharge_product_id', '!=', False),
            ])
            totals_by_product = {}
            for surcharge_line in surcharge_lines:
                product = surcharge_line.surcharge_product_id
                if product.id not in totals_by_product:
                    totals_by_product[product.id] = {
                        'product': product,
                        'buy_rate': 0.0,
                        'sell_rate': 0.0,
                    }
                totals_by_product[product.id]['buy_rate'] += surcharge_line.buy_rate
                totals_by_product[product.id]['sell_rate'] += surcharge_line.sell_rate

            existing_lines = order.order_line.filtered(
                lambda line: line.transport_surcharge_charge_line and not line.display_type
            )
            existing_by_product = {line.product_id.id: line for line in existing_lines if line.product_id}

            obsolete_lines = existing_lines.filtered(lambda line: line.product_id.id not in totals_by_product)
            if obsolete_lines:
                confirmed_obsolete_lines = obsolete_lines.filtered(lambda line: line.order_id.state in ('sale', 'done'))
                draft_obsolete_lines = obsolete_lines - confirmed_obsolete_lines
                if confirmed_obsolete_lines:
                    confirmed_obsolete_lines.write({
                        'product_uom_qty': 0.0,
                        'price_unit': 0.0,
                        'purchase_price': 0.0,
                    })
                if draft_obsolete_lines:
                    draft_obsolete_lines.unlink()

            for product_id, totals in totals_by_product.items():
                product = totals['product']
                line_vals = {
                    'name': product.get_product_multiline_description_sale() or product.display_name,
                    'product_uom_qty': 1.0,
                    'price_unit': totals['sell_rate'],
                    'purchase_price': totals['buy_rate'],
                    'transport_surcharge_charge_line': True,
                    'invoice_service_type': 'transport',
                }
                if product.uom_id:
                    line_vals['product_uom'] = product.uom_id.id
                existing_line = existing_by_product.get(product_id)
                if existing_line:
                    line_vals['tax_id'] = [(6, 0, existing_line._get_product_sale_tax_ids(product, order))]
                    existing_line.write(line_vals)
                else:
                    line_vals.update({
                        'order_id': order.id,
                        'product_id': product_id,
                    })
                    self.env['sale.order.line']._add_product_taxes_to_vals(line_vals)
                    self.env['sale.order.line'].create(line_vals)

    @api.constrains("sale_order_template_id", "order_line")
    def _check_transport_order_has_no_stock_moves(self):
        """
        You said all products in default order_line will be services for transport orders.
        This prevents accidental stockable/consu items from sneaking in.
        """
        for order in self:
            if order.order_type != "transport":
                continue
            bad = order.order_line.filtered(lambda l: l.product_id and l.product_id.type != "service")
            if bad:
                raise ValidationError(_(
                    "Transport orders must not contain stockable/consumable products in Order Lines.\n"
                    "Please use only Service products in order_line."
                ))

    @api.depends('user_id', 'company_id')
    def _compute_warehouse_id(self):
        super(SaleOrder, self)._compute_warehouse_id()
        for order in self:
            if order.commercial_partner_id:
                default_warehouse_id = self.env['stock.warehouse'].search([('commercial_partner_id', '=', order.commercial_partner_id.id)], limit=1)
                if default_warehouse_id:
                    order.warehouse_id = default_warehouse_id


class SaleOrderTemplate(models.Model):
    _inherit = 'sale.order.template'

    order_type = fields.Selection(ORDER_TYPE, default='standard', required=True)
    picking_type = fields.Selection(PICKING_TYPE, default='sku', required=True)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    order_type = fields.Selection(related='order_id.order_type')
    invoice_service_type = fields.Selection(
        [
            ('transport', 'Transport'),
            ('warehouse', 'Warehouse / Picking Service'),
        ],
        string='Invoice Service Type',
        copy=True,
        index=True,
    )
    container_charge_line = fields.Boolean(string='Container Charge Line', copy=False)
    goods_delivery_transport_charge_line = fields.Boolean(string='Goods Delivery Transport Charge Line', copy=False)
    transport_surcharge_charge_line = fields.Boolean(string='Transport Surcharge Charge Line', copy=False)
    package_type_id = fields.Many2one('stock.package.type', string='Package Type')
    package_qty = fields.Integer(string='Package Qty')
    total_weight = fields.Float(string='Total Weight')
    transport_leg_ids = fields.One2many('sale.transport.leg', 'order_line_id', string='Transport Legs')
    package_ids = fields.One2many('sale.package.line', 'order_line_id', string='Package Details')
    fs_unit_price = fields.Monetary(string='F/S')

    @api.model
    def _get_product_sale_tax_ids(self, product, order):
        if not product or not order:
            return []
        taxes = product.taxes_id._filter_taxes_by_company(order.company_id)
        if not taxes:
            return []
        return order.fiscal_position_id.map_tax(taxes).ids

    @api.model
    def _add_product_taxes_to_vals(self, vals):
        if 'tax_id' in vals or not vals.get('product_id') or not vals.get('order_id'):
            return vals
        order = self.env['sale.order'].browse(vals['order_id'])
        product = self.env['product.product'].browse(vals['product_id'])
        vals['tax_id'] = [(6, 0, self._get_product_sale_tax_ids(product, order))]
        return vals

    @api.model
    def _add_product_taxes_to_vals_list(self, vals_list):
        for vals in vals_list:
            self._add_product_taxes_to_vals(vals)
        return vals_list

    def _refresh_product_taxes_from_product(self):
        for line in self.filtered(lambda sol: not sol.display_type and sol.product_id):
            line.tax_id = [(6, 0, line._get_product_sale_tax_ids(line.product_id, line.order_id))]

    def _prepare_invoice_line(self, **optional_values):
        self.ensure_one()
        values = super()._prepare_invoice_line(**optional_values)
        service_type = self.invoice_service_type
        if not service_type:
            if (
                self.transport_leg_ids
                or self.goods_delivery_transport_charge_line
                or self.transport_surcharge_charge_line
            ):
                service_type = 'transport'
            elif self.order_id.order_type in ('goods_in', 'goods_out'):
                service_type = 'warehouse'
        if service_type:
            values['invoice_service_type'] = service_type
        return values

    def _recompute_transport_cost_sell_from_legs(self):
        """Server-side recalculation of cost from transport legs (no computed field)."""
        for line in self:
            legs = self.env['sale.transport.leg'].search([('order_line_id', '=', line.id)])
            total_buy_rate = sum((leg.base_buy_rate or leg.buy_rate or 0.0) for leg in legs)
            total_sell_rate = sum((leg.base_sell_rate or leg.sell_rate or 0.0) for leg in legs)
            vals = {
                'purchase_price': total_buy_rate,
                'price_unit': total_sell_rate,
            }
            if legs:
                vals['product_uom_qty'] = 1.0
            line.write(vals)

    def _recompute_package_count_weight(self):
        """Server-side recalculation of cost from transport legs (no computed field)."""
        for line in self:
            packages = self.env['sale.package.line'].search([('order_line_id', '=', line.id)])
            total_weight = sum(packages.mapped('weight') or [0.0])
            total_quantity = sum(packages.mapped('quantity') or [0.0])
            line.write({'total_weight': total_weight, 'package_qty': total_quantity})


    @api.onchange('package_type_id')
    def onchange_package_type_id(self):
        for record in self:
            if record.package_type_id:
                record.product_id = record.package_type_id.product_id
                record.tax_id = [(6, 0, record._get_product_sale_tax_ids(record.product_id, record.order_id))]
            else:
                record.product_id = False
                record.tax_id = False

    @api.model_create_multi
    def create(self, vals_list):
        # If nothing to create, just return empty recordset
        if not vals_list:
            return self.env["sale.order.line"]

        # 1) Check if ANY of the vals has `move_ids`
        contains_move_ids = any(vals.get("move_ids") for vals in vals_list)
        if not contains_move_ids:
            # No special handling → normal behavior
            self._add_product_taxes_to_vals_list(vals_list)
            return super(SaleOrderLine, self).create(vals_list)

        # 2) We are in the "material consumption" flow → load template
        material_ids = []
        template = self.env["material.picking.wizard"]._get_template()
        if template:
            material_ids = set(template.line_ids.mapped("product_id").ids)

        lines_to_create = []

        for vals in vals_list:
            # Case A: material-flow lines (non-empty move_ids)
            if vals.get("move_ids"):
                vals.setdefault('invoice_service_type', 'warehouse')
                product_id = vals.get("product_id")
                order_id = vals.get("order_id")

                # If we don't have product or order, fall back to normal create
                if not product_id or not order_id:
                    lines_to_create.append(vals)
                    continue

                # If this product is not part of the materials template, ignore it
                if product_id not in material_ids:
                    # neither update nor create a line for this vals
                    continue

                # Look for an existing SO line with same order + product
                existing_line = self.env["sale.order.line"].search([
                    ("order_id", "=", order_id),
                    ("product_id", "=", product_id),
                ], limit=1)

                material_qty = vals.get("qty_delivered") or vals.get("product_uom_qty") or 0.0
                if existing_line:
                    # Add extra material quantity to existing line.
                    if material_qty:
                        current_material_qty = max(
                            existing_line.product_uom_qty,
                            existing_line.qty_delivered,
                        )
                        existing_line.write({
                            "product_uom_qty": current_material_qty + material_qty,
                            "qty_delivered": existing_line.qty_delivered + material_qty,
                        })
                    # Do NOT create a new line for this vals
                else:
                    # No existing SO line with this product: create a new one
                    if material_qty and not vals.get("product_uom_qty"):
                        vals["product_uom_qty"] = material_qty
                    lines_to_create.append(vals)

            # Case B: non-material lines (no move_ids key or empty move_ids)
            else:
                # Just create as usual
                lines_to_create.append(vals)

        # 3) If nothing left to create, return empty recordset
        if not lines_to_create:
            return self.env["sale.order.line"]

        # 4) Create remaining lines normally
        self._add_product_taxes_to_vals_list(lines_to_create)
        return super(SaleOrderLine, self).create(lines_to_create)

    def write(self, vals):
        if vals.get('product_id') and 'tax_id' not in vals and not self.env.context.get('skip_sale_goods_order_tax_write'):
            product = self.env['product.product'].browse(vals['product_id'])
            for line in self:
                order = self.env['sale.order'].browse(vals.get('order_id')) if vals.get('order_id') else line.order_id
                line.with_context(skip_sale_goods_order_tax_write=True).write({
                    'tax_id': [(6, 0, self._get_product_sale_tax_ids(product, order))]
                })
        return super().write(vals)

    def action_open_transport_legs(self):
        self.ensure_one()
        if self.order_id.order_type == 'transport' and (not self.order_id.transport_from_id or not self.order_id.transport_to_id):
            raise ValidationError(_("Please specify the Pickup and Dropoff locations in 'Transport Products' tab"))
        if not self.transport_leg_ids:
            default_address_id = int(self.env['ir.config_parameter'].sudo().get_param('sale_gto.transport_address_id', 0))
            if self.order_id.order_type == 'transport':
                self.transport_leg_ids.create([
                    {
                        'from_location': self.order_id.transport_from_id.id,
                        'to_location': default_address_id,
                        'sequence': 1,
                        'order_line_id': self.id
                    },
                    {
                        'sequence': 2,
                        'from_location': default_address_id,
                        'to_location': self.order_id.transport_to_id.id,
                        'order_line_id': self.id
                    }

                ])

        domain = [('id', 'in', self.transport_leg_ids.ids)]
        ctx = dict(self.env.context or {})
        # Optional defaults if you create from this screen
        # ctx.update({
        #     "default_sale_line_id": self.id,
        #     "default_order_id": self.order_id.id,
        # })



        return {
            "type": "ir.actions.act_window",
            "name": _("Transport Legs"),
            "res_model": "sale.transport.leg",
            "view_mode": "list,form",
            "domain": domain,
            "context": ctx,
            "target": "current",
        }

    def add_view_package_details(self):
        domain = [('id', 'in', self.package_ids.ids)]
        ctx = dict(self.env.context or {})
        ctx['default_order_line_id'] = self.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Package Details"),
            "res_model": "sale.package.line",
            "view_mode": "list,form",
            "domain": domain,
            "context": ctx,
            "target": "new",
        }
