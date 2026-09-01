import json
import logging
from urllib.parse import quote_plus

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)

class SaleTransportLeg(models.Model):
    _name = 'sale.transport.leg'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Sale Transport Leg'

    name = fields.Char(string='Name')
    sequence = fields.Integer(string='Sequence', default=1)
    preferred_service_id = fields.Many2one('sale.transport.service', string='Service Type')
    order_line_id = fields.Many2one('sale.order.line', string='Sale Order Line')
    picking_id = fields.Many2one('stock.picking', string='Delivery')
    order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        compute='_compute_order_id',
        store=True,
    )
    customer_id = fields.Many2one(related='order_id.partner_id', string='Company Name')
    from_location = fields.Many2one('res.partner', string='Company (Pickup)')
    from_date = fields.Date(string='Date (Pickup)')
    from_postcode = fields.Char(related='from_location.zip', string='Post Code (Pickup)')
    from_address = fields.Char(string='Address (Pickup)')
    from_town = fields.Char(string='Town (Pickup)')
    from_county = fields.Many2one(related='from_location.state_id', string='County (Pickup)')
    from_country = fields.Many2one(related='from_location.country_id', string='Country (Pickup)')
    from_tel = fields.Char(string='Telephone (Pickup)')
    from_contact = fields.Char(string='Contact (Pickup)')
    from_email = fields.Char(string='Email (Pickup)')
    from_instructions = fields.Text(string='Instructions (Pickup)')

    to_location = fields.Many2one('res.partner', string='Company (Drop Off)')
    to_date = fields.Date(string='Date (Drop Off)')
    to_postcode = fields.Char(related='to_location.zip', string='Post Code (Drop Off)')
    to_address = fields.Char(string='Address (Drop Off)')
    to_town = fields.Char(string='Town (Drop Off)')
    to_county = fields.Many2one(related='to_location.state_id', string='County (Drop Off)')
    to_country = fields.Many2one(related='to_location.country_id', string='Country (Drop Off)')
    to_tel = fields.Char(string='Telephone (Drop Off)')
    to_contact = fields.Char(string='Contact (Drop Off)')
    to_email = fields.Char(string='Email (Drop Off)')
    to_instructions = fields.Text(string='Instructions (Drop Off)')
    # service_id = fields.Many2one('sale.transport.service', string='Service')
    carrier_service_option_ids = fields.One2many('sale.carrier.service.option', 'transport_leg_id', string='Carrier Option')
    surcharge_line_ids = fields.One2many('sale.transport.leg.surcharge.line', 'transport_leg_id', string='Surcharges')
    carrier_service_id = fields.Many2one('sale.carrier.service.option', string='Carrier Service')
    carrier_id = fields.Many2one(related='carrier_service_id.carrier_id', string='Carrier')
    carrier_code = fields.Char(related='carrier_service_id.carrier_code', string='Carrier Code')
    service_id = fields.Many2one(related='carrier_service_id.service_id', string='Service')
    order_type = fields.Selection(related='order_id.order_type', string='Order Type')
    tracking_code = fields.Char(string='Tracking Code')
    carrier_tracking_ref = fields.Char(string='Carrier Tracking Reference', compute='_compute_carrier_tracking_ref')
    rate_postcode_source = fields.Selection(
        [
            ('to_location', 'To Location'),
            ('from_location', 'From Location'),
        ],
        string='Rate Postcode Source',
        default='to_location',
        required=True,
    )
    rate_carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Carrier Filter',
        help='Optional delivery method/carrier filter to send as carrier_code.',
    )
    rate_service_code = fields.Char(
        string='Service Code Filter',
        help='Optional service_code to send to the carrier rate API.',
    )
    rate_request_json = fields.Text(string='Last Rate Request', readonly=True, copy=False)
    rate_response_json = fields.Text(string='Last Rate Response', readonly=True, copy=False)
    rate_queried_at = fields.Datetime(string='Rates Queried At', readonly=True, copy=False)
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id.id)
    base_buy_rate = fields.Monetary(string='Base Buy')
    base_sell_rate = fields.Monetary(string='Base Sell')
    buy_rate = fields.Monetary(string='Buy')
    fs_buy_rate = fields.Monetary(string='Surcharges Buy')
    sell_rate = fields.Monetary(string='Sell')
    fs_sell_rate = fields.Monetary(string='Surcharges Sell')
    margin = fields.Float(string='Margin %', compute='_compute_margin', store=True)
    fs_margin = fields.Float(string='Surcharges Margin %', compute='_compute_margin', store=True)
    state = fields.Selection([
        ('scheduled', 'Scheduled'),
        ('in_transit', 'In Transit'),
        ('completed', 'Completed')
    ], string='Status', default='scheduled')
    tag_color = fields.Integer(string='Tag Color', compute='_compute_tag_color')
    date_completion = fields.Datetime(string='Completion Time')
    contact = fields.Char(string='Contact')
    reference = fields.Char(string='Reference')
    is_internal = fields.Boolean(string='Is Internal')
    fleet_id = fields.Many2one('fleet.vehicle', string='Vehicle')
    driver_id = fields.Many2one('hr.employee', string='Driver')

    @api.model
    def _get_goods_delivery_default_locations(self, picking):
        sale = picking.sale_id
        if not sale or sale.order_type not in ('goods_in', 'goods_out'):
            return {}

        warehouse_address = sale._get_warehouse_delivery_address(sale.warehouse_id)
        if sale.order_type == 'goods_in':
            return {
                'from_location': sale.collection_address_id.id if sale.collection_address_id else False,
                'to_location': warehouse_address.id if warehouse_address else False,
            }

        return {
            'from_location': warehouse_address.id if warehouse_address else False,
            'to_location': sale.partner_shipping_id.id if sale.partner_shipping_id else False,
        }

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        picking_id = self.env.context.get('default_picking_id')
        if picking_id:
            picking = self.env['stock.picking'].browse(picking_id)
            defaults = self._get_goods_delivery_default_locations(picking)
            for field_name, value in defaults.items():
                if field_name in fields_list and not vals.get(field_name) and value:
                    vals[field_name] = value
        return vals

    def _get_target_sale_order(self, vals=None):
        self.ensure_one()
        vals = vals or {}
        order_line = self.order_line_id
        picking = self.picking_id
        if vals.get('order_line_id'):
            order_line = self.env['sale.order.line'].browse(vals['order_line_id'])
        if vals.get('picking_id'):
            picking = self.env['stock.picking'].browse(vals['picking_id'])
        return order_line.order_id or picking.sale_id

    @api.model
    def _check_no_transport_needed_on_vals(self, vals_list):
        for vals in vals_list:
            sale = self.env['sale.order.line'].browse(vals['order_line_id']).order_id if vals.get('order_line_id') else False
            if not sale and vals.get('picking_id'):
                sale = self.env['stock.picking'].browse(vals['picking_id']).sale_id
            if sale and sale.no_transport_needed:
                raise ValidationError(_("No transport legs can be added because No Transport Needed is enabled on the sale order."))

    def _check_no_transport_needed_on_records(self, vals=None):
        for record in self:
            sale = record._get_target_sale_order(vals=vals)
            if sale and sale.no_transport_needed:
                raise ValidationError(_("No transport legs can be added because No Transport Needed is enabled on the sale order."))

    def _get_or_create_goods_delivery_sale_line(self):
        self.ensure_one()
        sale = self.picking_id.sale_id
        if not sale or sale.order_type not in ('goods_in', 'goods_out'):
            return self.env['sale.order.line']

        existing_line = sale.order_line.filtered(
            lambda line: (
                not line.display_type
                and (
                    line.goods_delivery_transport_charge_line
                    or line.transport_leg_ids.filtered('picking_id')
                )
            )
        )[:1]
        if existing_line:
            if not existing_line.goods_delivery_transport_charge_line:
                existing_line.goods_delivery_transport_charge_line = True
            return existing_line

        product = self.env.ref('sale_goods_order.prod_transport_delivery_leg', raise_if_not_found=False)
        line_vals = {
            'order_id': sale.id,
            'name': product.get_product_multiline_description_sale() if product else _('Transport Charges'),
            'product_uom_qty': 1.0,
            'price_unit': 0.0,
            'goods_delivery_transport_charge_line': True,
            'invoice_service_type': 'transport',
        }
        if product:
            line_vals.update({
                'product_id': product.id,
                'product_uom': product.uom_id.id,
                'name': product.get_product_multiline_description_sale() or product.display_name,
            })
            self.env['sale.order.line']._add_product_taxes_to_vals(line_vals)
        return self.env['sale.order.line'].create(line_vals)

    def _assign_goods_delivery_order_lines(self):
        for record in self:
            if not record.picking_id or not record.picking_id.sale_id:
                continue
            sale = record.picking_id.sale_id
            if sale.order_type not in ('goods_in', 'goods_out'):
                continue
            line = record._get_or_create_goods_delivery_sale_line()
            if line and not line.goods_delivery_transport_charge_line:
                line.goods_delivery_transport_charge_line = True
            if line and record.order_line_id != line:
                record.order_line_id = line.id

    @api.depends('order_line_id.order_id', 'picking_id.sale_id')
    def _compute_order_id(self):
        for record in self:
            record.order_id = record.order_line_id.order_id or record.picking_id.sale_id

    @api.depends('sell_rate', 'buy_rate', 'fs_sell_rate', 'fs_buy_rate')
    def _compute_margin(self):
        for record in self:
            if record.sell_rate:
                record.margin = ((record.sell_rate - record.buy_rate) / record.sell_rate) * 100
            else:
                record.margin = 0.0
            if record.fs_sell_rate:
                record.fs_margin = ((record.fs_sell_rate - record.fs_buy_rate) / record.fs_sell_rate) * 100
            else:
                record.fs_margin = 0.0

    @api.onchange('surcharge_line_ids', 'surcharge_line_ids.buy_rate', 'surcharge_line_ids.sell_rate')
    def _onchange_surcharge_line_ids_update_totals(self):
        for record in self:
            record._set_surcharge_totals_from_lines()

    def _get_surcharge_totals_from_lines(self):
        self.ensure_one()
        surcharge_lines = self.surcharge_line_ids.filtered(
            lambda line: (line.name or line.surcharge_product_id.name or '').strip().lower() != 'base'
        )
        return {
            'fs_buy_rate': sum(surcharge_lines.mapped('buy_rate') or [0.0]),
            'fs_sell_rate': sum(surcharge_lines.mapped('sell_rate') or [0.0]),
        }

    def _set_surcharge_totals_from_lines(self):
        for record in self:
            totals = record._get_surcharge_totals_from_lines()
            record.fs_buy_rate = totals['fs_buy_rate']
            record.fs_sell_rate = totals['fs_sell_rate']

    def _write_surcharge_totals_from_lines(self):
        for record in self:
            record.write(record._get_surcharge_totals_from_lines())

    @api.depends('tracking_code')
    def _compute_carrier_tracking_ref(self):
        for record in self:
            record.carrier_tracking_ref = record.tracking_code

    def action_open_tracking_url(self):
        self.ensure_one()
        if not self.tracking_code:
            raise UserError(_('Please enter a tracking code first.'))
        url = False
        if self.carrier_id:
            try:
                url = self.carrier_id.get_tracking_link(self)
            except Exception:
                _logger.exception('Could not build carrier tracking URL for transport leg %s', self.id)
        if url and self.tracking_code in url:
            url = url.replace(self.tracking_code, quote_plus(self.tracking_code))
        if not url and self.carrier_id.tracking_url:
            url = self.carrier_id.tracking_url.replace('<shipmenttrackingnumber>', quote_plus(self.tracking_code))
        if not url:
            url = 'https://www.google.com/search?q=%s' % quote_plus('%s tracking %s' % (
                self.carrier_code or self.carrier_id.name or '',
                self.tracking_code,
            ))
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    @api.depends('state')
    def _compute_tag_color(self):
        color_by_state = {
            'scheduled': 0,
            'draft': 0,
            'in_transit': 2,
            'completed': 10,
            'done': 10,
        }
        for record in self:
            record.tag_color = color_by_state.get(record.state, 0)

    @api.depends('from_location.zip', 'to_location.zip')
    def _compute_display_name(self):
        for record in self:
            source = record.from_location.zip or '-'
            destination = record.to_location.zip or '-'
            record.display_name = '%s -> %s' % (source, destination)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('picking_id'):
                picking = self.env['stock.picking'].browse(vals['picking_id'])
                defaults = self._get_goods_delivery_default_locations(picking)
                if defaults.get('from_location') and not vals.get('from_location'):
                    vals['from_location'] = defaults['from_location']
                if defaults.get('to_location') and not vals.get('to_location'):
                    vals['to_location'] = defaults['to_location']
        self._check_no_transport_needed_on_vals(vals_list)
        records = super().create(vals_list)
        records.filtered('surcharge_line_ids')._write_surcharge_totals_from_lines()
        records._assign_goods_delivery_order_lines()
        lines = records.mapped('order_line_id')
        if lines:
            lines._recompute_transport_cost_sell_from_legs()
        return records

    def write(self, vals):
        self._check_no_transport_needed_on_records(vals=vals)
        lines_before = self.mapped('order_line_id')
        orders_before = self.mapped('order_id')
        res = super().write(vals)
        self._assign_goods_delivery_order_lines()
        lines_after = self.mapped('order_line_id')

        # If sale_line_id changed, recompute both old and new lines
        (lines_before | lines_after)._recompute_transport_cost_sell_from_legs()
        affected_orders = orders_before | self.mapped('order_id')
        affected_orders._sync_transport_surcharge_sale_lines()
        if {'state', 'order_line_id', 'picking_id'} & set(vals):
            affected_orders._mark_transport_leg_lines_delivered()
        return res

    def unlink(self):
        lines = self.mapped('order_line_id')
        orders = self.mapped('order_id')
        res = super().unlink()
        if lines:
            lines._recompute_transport_cost_sell_from_legs()
        orders._sync_transport_surcharge_sale_lines()
        return res
    def action_in_transit(self):
        self.write({'state': 'in_transit'})

    def action_open_form_view(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Transport Leg'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('sale_goods_order.sale_transport_leg_form_view').id, 'form')],
            'target': 'current',
        }

    def action_completed(self):
        self.write({'state': 'completed'})

    def action_back(self):
        if self.state == 'completed':
            self.write({'state': 'in_transit'})
        elif self.state == 'in_transit':
            self.write({'state': 'scheduled'})

    def action_fetch_service_carrier_options(self):
        for leg in self:
            leg._fetch_service_carrier_options()
        return True

    def _get_rate_locations(self):
        """Return the (collection, delivery) partners for this leg.

        The pickup end is always the collection and the drop-off end always the
        delivery, whatever the order type: goods_in collects from the customer
        into the warehouse, goods_out runs the other way, and transport legs
        carry both explicitly. Each end falls back to the address held on the
        sale order when the leg itself has none.
        """
        self.ensure_one()
        collection = self.from_location or self.order_id.transport_from_id
        delivery = self.to_location or self.order_id.transport_to_id
        return collection, delivery

    def _get_rate_package_totals(self):
        self.ensure_one()
        package_lines = self.picking_id.package_ids or self.order_line_id.package_ids
        if package_lines:
            # Only pallet package types count towards the pallet quantity; a
            # Carton or an Item must not be rated as a pallet. Per SAE, every
            # pallet counts as one full pallet regardless of size, so a Half or
            # Qtr Pallet is charged as a full one. Weight is still totalled
            # across every package, as that is the chargeable weight.
            pallet_lines = package_lines.filtered(lambda line: line.package_type_id.is_pallet)
            return {
                'weight_kg': sum(package_lines.mapped('weight') or [0.0]),
                'pallet_qty': max(int(sum(pallet_lines.mapped('quantity') or [0])), 0),
                # Derived from the presence of pallet lines rather than from
                # pallet_qty, because a pallet line whose quantity was left at
                # 0 is still a pallet consignment.
                'is_pallet': bool(pallet_lines),
            }

        # No package details captured. Transport product lines describe goods, not
        # packaging, so their qty is never a pallet count; fall back to the pallet
        # count entered on the delivery.
        pallet_qty = max(int(self.picking_id.pallet_qty or 0), 0)
        transport_product_lines = self.order_id.product_line_ids
        return {
            'weight_kg': sum(transport_product_lines.mapped('weight') or [0.0]),
            'pallet_qty': pallet_qty,
            'is_pallet': bool(pallet_qty),
        }

    def _get_carrier_rate_api_url(self):
        return (
            self.env['ir.config_parameter']
            .sudo()
            .get_param('sale_gto.carrier_rate_api_url')
            or 'https://automate.eartisan.co.uk/webhook/get-rates'
        )

    def _get_carrier_code(self):
        self.ensure_one()
        carrier = self.rate_carrier_id
        if not carrier:
            return False
        code = getattr(carrier, 'sae_carrier_code', False)
        return code or (carrier.name or '').strip().upper().replace(' ', '_')

    def _prepare_rate_payload(self):
        self.ensure_one()
        if not self.picking_id and not self.order_id:
            raise UserError(_('Please link the transport leg to a sale order or delivery before fetching rates.'))

        # The pricing engine rates on the collection-to-delivery pair, so both
        # ends are required. Each is reported separately so the user knows which
        # address to go and fix.
        collection, delivery = self._get_rate_locations()
        if not collection:
            raise UserError(_('Please set the Company (Pickup) before fetching rates.'))
        if not collection.zip:
            raise UserError(
                _('Please set a postcode on the collection address %s before fetching rates.')
                % collection.display_name
            )
        if not delivery:
            raise UserError(_('Please set the Company (Drop Off) before fetching rates.'))
        if not delivery.zip:
            raise UserError(
                _('Please set a postcode on the delivery address %s before fetching rates.')
                % delivery.display_name
            )

        totals = self._get_rate_package_totals()
        if totals['weight_kg'] <= 0:
            raise UserError(_(
                'Please add weights before fetching rates. For delivery legs, use Package Details on the delivery. '
                'For transport order legs, use Package Details on the sale order line or weights on Transport Products.'
            ))

        payload = {
            'collection_postcode': collection.zip,
            'collection_country_code': collection.country_id.code or 'GB',
            'delivery_postcode': delivery.zip,
            'delivery_country_code': delivery.country_id.code or 'GB',
            'weight_kg': totals['weight_kg'],
            'pallet_qty': totals['pallet_qty'],
            # The rate API treats a consignment as palletised when
            # is_pallet is true OR pallet_qty > 0.
            'is_pallet': totals['is_pallet'],
            'order_reference': (
                self.picking_id.name
                or self.picking_id.origin
                or self.order_id.client_order_ref
                or self.order_id.name
                or ''
            ),
        }
        carrier_code = self._get_carrier_code()
        if carrier_code:
            payload['carrier_code'] = carrier_code
        if self.rate_service_code:
            payload['service_code'] = self.rate_service_code
        return payload

    def _fetch_service_carrier_options(self):
        self.ensure_one()
        payload = self._prepare_rate_payload()
        url = self._get_carrier_rate_api_url()
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            _logger.exception('Carrier rate API request failed for transport leg %s', self.id)
            raise UserError(_('Carrier rate query failed: %s') % exc) from exc

        request_json = json.dumps(payload, indent=2, sort_keys=True)
        response_json = json.dumps(result, indent=2, sort_keys=True)
        if not result.get('success'):
            self.write({
                'rate_request_json': request_json,
                'rate_response_json': response_json,
                'rate_queried_at': fields.Datetime.now(),
            })
            raise UserError(_('No rates returned: %s') % (result.get('error') or _('Unknown error')))

        self.carrier_service_option_ids.unlink()
        self.surcharge_line_ids.unlink()
        carrier_rows = self._filter_rate_response_carriers(result.get('carriers', []), payload)
        option_vals = [
            self._prepare_carrier_option_values(carrier_data)
            for carrier_data in carrier_rows
        ]
        self.write({
            'rate_request_json': request_json,
            'rate_response_json': response_json,
            'rate_queried_at': fields.Datetime.now(),
        })
        if option_vals:
            self.env['sale.carrier.service.option'].create(option_vals)
        else:
            raise UserError(_('The carrier rate API returned no carrier options matching the selected filters.'))
        return True

    def _filter_rate_response_carriers(self, carrier_rows, payload):
        carrier_code = (payload.get('carrier_code') or '').upper()
        service_code = (payload.get('service_code') or '').upper()
        filtered_rows = []
        for row in carrier_rows:
            row_carrier_code = (row.get('carrier') or '').upper()
            row_service_code = (row.get('service_code') or '').upper()
            if carrier_code and row_carrier_code != carrier_code:
                continue
            if service_code and row_service_code != service_code:
                continue
            filtered_rows.append(row)
        return filtered_rows

    def _prepare_carrier_option_values(self, carrier_data):
        self.ensure_one()
        carrier_code = carrier_data.get('carrier') or ''
        service_name = carrier_data.get('service_name') or carrier_data.get('service_code') or _('Standard')
        base_rate = carrier_data.get('base_rate') or {}
        service = self.env['sale.transport.service'].search([('name', '=', service_name)], limit=1)
        if not service:
            service = self.env['sale.transport.service'].create({'name': service_name})

        delivery_carrier = self._find_delivery_carrier(carrier_code)
        return {
            'name': '%s - %s' % (carrier_code or _('Carrier'), service_name),
            'transport_leg_id': self.id,
            'carrier_id': delivery_carrier.id if delivery_carrier else False,
            'carrier_code': carrier_code,
            'service_id': service.id,
            'service_code': carrier_data.get('service_code') or False,
            'service_name': service_name,
            'zone_code': carrier_data.get('zone_code') or False,
            'zone_name': carrier_data.get('zone_name') or False,
            'base_buy_rate': base_rate.get('buy') or 0.0,
            'base_sell_rate': base_rate.get('sell') or 0.0,
            'buy_rate': carrier_data.get('buy_rate') or 0.0,
            'sell_rate': carrier_data.get('sell_rate') or 0.0,
            'margin_amount': carrier_data.get('margin') or 0.0,
            'margin_percent': carrier_data.get('margin_percent') or 0.0,
            'chargeable_weight': carrier_data.get('chargeable_weight') or 0.0,
            'service_hours': carrier_data.get('service_hours') or 0.0,
            'pallet_quantity': carrier_data.get('pallet_quantity') or 0.0,
            'currency_id': self.currency_id.id,
            'raw_response_json': json.dumps(carrier_data, indent=2, sort_keys=True),
            'surcharge_line_ids': [
                (0, 0, surcharge_values)
                for surcharge_values in self._prepare_option_surcharge_values(carrier_data)
            ],
        }

    def _prepare_option_surcharge_values(self, carrier_data):
        surcharge_totals = {}
        for surcharge_data in carrier_data.get('surcharges') or []:
            surcharge_name = (surcharge_data.get('name') or '').strip()
            if not surcharge_name:
                continue
            product = self.env['sale.transport.leg.surcharge.line']._get_or_create_surcharge_product(surcharge_name)
            key = product.id
            if key not in surcharge_totals:
                surcharge_totals[key] = {
                    'name': surcharge_name,
                    'surcharge_product_id': product.id,
                    'buy_rate': 0.0,
                    'sell_rate': 0.0,
                    'currency_id': self.currency_id.id,
                }
            surcharge_totals[key]['buy_rate'] += self._get_surcharge_amount(surcharge_data, 'buy', 'buy_rate')
            surcharge_totals[key]['sell_rate'] += self._get_surcharge_amount(surcharge_data, 'sell', 'sell_rate')
        return list(surcharge_totals.values())

    def _prepare_surcharge_line_values(self, carrier_rows):
        self.ensure_one()
        surcharge_totals = {}
        for carrier_data in carrier_rows:
            for surcharge_data in carrier_data.get('surcharges') or []:
                surcharge_name = (surcharge_data.get('name') or '').strip()
                if not surcharge_name:
                    continue
                product = self.env['sale.transport.leg.surcharge.line']._get_or_create_surcharge_product(surcharge_name)
                key = product.id
                if key not in surcharge_totals:
                    surcharge_totals[key] = {
                        'name': surcharge_name,
                        'surcharge_product_id': product.id,
                        'buy_rate': 0.0,
                        'sell_rate': 0.0,
                    }
                surcharge_totals[key]['buy_rate'] += self._get_surcharge_amount(surcharge_data, 'buy', 'buy_rate')
                surcharge_totals[key]['sell_rate'] += self._get_surcharge_amount(surcharge_data, 'sell', 'sell_rate')

        return [
            dict(values, transport_leg_id=self.id, currency_id=self.currency_id.id)
            for values in surcharge_totals.values()
        ]

    def _get_surcharge_amount(self, surcharge_data, *keys):
        for key in keys:
            value = surcharge_data.get(key)
            if value in (None, False, ''):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    def _get_surcharge_carrier_rows(self, carrier_rows, result=None):
        self.ensure_one()
        if not carrier_rows:
            return []
        selected_option = self.carrier_service_id
        if selected_option:
            selected_rows = [
                row for row in carrier_rows
                if (
                    (row.get('carrier') or '') == (selected_option.carrier_code or '')
                    and (row.get('service_code') or '') == (selected_option.service_code or '')
                    and (row.get('service_name') or '') == (selected_option.service_name or '')
                )
            ]
            if selected_rows:
                return selected_rows[:1]

        recommended = (result or {}).get('recommended') or {}
        if recommended:
            recommended_rows = [
                row for row in carrier_rows
                if (
                    (not recommended.get('carrier') or row.get('carrier') == recommended.get('carrier'))
                    and (not recommended.get('service_name') or row.get('service_name') == recommended.get('service_name'))
                )
            ]
            if recommended_rows:
                return recommended_rows[:1]
        return carrier_rows[:1]

    def _find_delivery_carrier(self, carrier_code):
        if not carrier_code:
            return self.env['delivery.carrier']
        Carrier = self.env['delivery.carrier']
        carrier = Carrier.search([('sae_carrier_code', '=ilike', carrier_code)], limit=1)
        if carrier:
            return carrier
        return Carrier.search([('name', '=ilike', carrier_code)], limit=1)

    @api.constrains('from_date', 'to_date')
    def check_dates_chronology(self):
        for record in self:
            if record.from_date and record.to_date:
                if record.from_date > record.to_date:
                    raise ValidationError(_('From Date should be less or equal to the To Date'))


class SaleTransportService(models.Model):
    _name = 'sale.transport.service'
    _description = 'Sale Transport Service'

    name = fields.Char(string='Name')


class SaleTransportCarrier(models.Model):
    _name = 'sale.transport.carrier'
    _description = 'Sale Transport Carrier'

    name = fields.Char(string='Name')


class SaleTransportLegSurchargeLine(models.Model):
    _name = 'sale.transport.leg.surcharge.line'
    _description = 'Transport Leg Surcharge'

    name = fields.Char(string='Surcharge Name', required=True)
    transport_leg_id = fields.Many2one('sale.transport.leg', string='Transport Leg', required=True, ondelete='cascade')
    order_id = fields.Many2one(related='transport_leg_id.order_id', store=True, string='Sale Order')
    surcharge_product_id = fields.Many2one('product.product', string='Surcharge Product')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id.id)
    buy_rate = fields.Monetary(string='Buy')
    sell_rate = fields.Monetary(string='Sell')

    @api.model
    def _get_or_create_surcharge_product(self, surcharge_name):
        product = self.env['product.product'].search([('name', '=ilike', surcharge_name)], limit=1)
        if product:
            return product
        vals = {
            'name': surcharge_name,
            'type': 'service',
            'categ_id': self.env.ref('product.product_category_all').id,
            'is_surcharge_product': True,
            'list_price': 0.0,
            'standard_price': 0.0,
        }
        if 'service_type' in self.env['product.product']._fields:
            vals['service_type'] = 'manual'
        return self.env['product.product'].create(vals)

    @api.onchange('name')
    def _onchange_name_set_surcharge_product(self):
        for line in self:
            if line.name:
                line.surcharge_product_id = line._get_or_create_surcharge_product(line.name)

    @api.onchange('surcharge_product_id')
    def _onchange_surcharge_product_id_set_name(self):
        for line in self:
            if line.surcharge_product_id:
                line.name = line.surcharge_product_id.display_name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('surcharge_product_id') and not vals.get('name'):
                vals['name'] = self.env['product.product'].browse(vals['surcharge_product_id']).display_name
            if vals.get('name') and not vals.get('surcharge_product_id'):
                vals['surcharge_product_id'] = self._get_or_create_surcharge_product(vals['name']).id
        records = super().create(vals_list)
        records.mapped('transport_leg_id')._write_surcharge_totals_from_lines()
        orders = records.mapped('order_id')
        orders._sync_transport_surcharge_sale_lines()
        orders._mark_transport_leg_lines_delivered()
        records.mapped('transport_leg_id.order_line_id')._recompute_transport_cost_sell_from_legs()
        return records

    def write(self, vals):
        orders_before = self.mapped('order_id')
        lines_before = self.mapped('transport_leg_id.order_line_id')
        legs_before = self.mapped('transport_leg_id')
        if vals.get('surcharge_product_id') and 'name' not in vals:
            vals['name'] = self.env['product.product'].browse(vals['surcharge_product_id']).display_name
        if vals.get('name') and 'surcharge_product_id' not in vals:
            vals['surcharge_product_id'] = self._get_or_create_surcharge_product(vals['name']).id
        res = super().write(vals)
        (legs_before | self.mapped('transport_leg_id'))._write_surcharge_totals_from_lines()
        affected_orders = orders_before | self.mapped('order_id')
        affected_orders._sync_transport_surcharge_sale_lines()
        affected_orders._mark_transport_leg_lines_delivered()
        (lines_before | self.mapped('transport_leg_id.order_line_id'))._recompute_transport_cost_sell_from_legs()
        return res

    def unlink(self):
        orders = self.mapped('order_id')
        lines = self.mapped('transport_leg_id.order_line_id')
        legs = self.mapped('transport_leg_id')
        res = super().unlink()
        legs._write_surcharge_totals_from_lines()
        orders._sync_transport_surcharge_sale_lines()
        orders._mark_transport_leg_lines_delivered()
        lines._recompute_transport_cost_sell_from_legs()
        return res


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    sae_carrier_code = fields.Char(
        string='SAE Carrier Code',
        help='Carrier code sent to the SAE carrier rate API, for example DPD, APC, CROSSFLIGHT, MPXR, or PALLETWORKS.',
    )


class SaleCarrierServiceOption(models.Model):
    _name = 'sale.carrier.service.option'
    _description = 'Carrier Option'

    name = fields.Char(string='Name')
    transport_leg_id = fields.Many2one('sale.transport.leg', string='Transport Leg')
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier')
    carrier_code = fields.Char(string='Carrier Code')
    service_id = fields.Many2one('sale.transport.service', string='Service')
    service_code = fields.Char(string='Service Code')
    service_name = fields.Char(string='Service Name')
    zone_code = fields.Char(string='Zone Code')
    zone_name = fields.Char(string='Zone Name')
    base_buy_rate = fields.Monetary(string='Base Buy')
    base_sell_rate = fields.Monetary(string='Base Sell')
    buy_rate = fields.Monetary(string='Buy')
    sell_rate = fields.Monetary(string='Sell')
    margin_amount = fields.Monetary(string='Margin')
    margin_percent = fields.Float(string='Margin %')
    chargeable_weight = fields.Float(string='Chargeable Weight')
    service_hours = fields.Float(string='Service Hours')
    pallet_quantity = fields.Float(string='Pallet Quantity')
    currency_id = fields.Many2one('res.currency', string='Currency')
    is_selected = fields.Boolean(string='Opt In')
    raw_response_json = fields.Text(string='Raw Response', readonly=True)
    surcharge_line_ids = fields.One2many(
        'sale.carrier.service.option.surcharge.line',
        'carrier_service_option_id',
        string='Surcharges',
    )

    def action_select_option(self):
        for option in self:
            option._select_option()
        return True

    def _select_option(self):
        self.ensure_one()
        leg = self.transport_leg_id
        if not leg:
            return
        other_options = leg.carrier_service_option_ids - self
        if other_options:
            other_options.write({'is_selected': False})
        if not self.carrier_id and self.carrier_code:
            carrier = leg._find_delivery_carrier(self.carrier_code)
            if carrier:
                self.carrier_id = carrier.id
        self.with_context(skip_carrier_option_select=True).is_selected = True
        leg.write({
            'carrier_service_id': self.id,
            'base_buy_rate': self.base_buy_rate,
            'base_sell_rate': self.base_sell_rate,
            'buy_rate': self.buy_rate,
            'sell_rate': self.sell_rate,
        })
        self._copy_surcharges_to_transport_leg()

    def _copy_surcharges_to_transport_leg(self):
        self.ensure_one()
        leg = self.transport_leg_id
        if not leg:
            return
        leg.surcharge_line_ids.unlink()
        surcharge_vals = [
            {
                'transport_leg_id': leg.id,
                'name': surcharge.name,
                'surcharge_product_id': surcharge.surcharge_product_id.id,
                'currency_id': leg.currency_id.id,
                'buy_rate': surcharge.buy_rate,
                'sell_rate': surcharge.sell_rate,
            }
            for surcharge in self.surcharge_line_ids
        ]
        if not surcharge_vals and self.raw_response_json:
            try:
                carrier_data = json.loads(self.raw_response_json)
            except json.JSONDecodeError:
                carrier_data = {}
            surcharge_vals = leg._prepare_surcharge_line_values([carrier_data])
        if surcharge_vals:
            self.env['sale.transport.leg.surcharge.line'].create(surcharge_vals)

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_selected') and not self.env.context.get('skip_carrier_option_select'):
            for option in self:
                option.with_context(skip_carrier_option_select=True)._select_option()
        return res


class SaleCarrierServiceOptionSurchargeLine(models.Model):
    _name = 'sale.carrier.service.option.surcharge.line'
    _description = 'Carrier Option Surcharge'

    name = fields.Char(string='Surcharge Name', required=True)
    carrier_service_option_id = fields.Many2one(
        'sale.carrier.service.option',
        string='Carrier Option',
        required=True,
        ondelete='cascade',
    )
    surcharge_product_id = fields.Many2one('product.product', string='Surcharge Product')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id.id)
    buy_rate = fields.Monetary(string='Buy')
    sell_rate = fields.Monetary(string='Sell')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('surcharge_product_id') and not vals.get('name'):
                vals['name'] = self.env['product.product'].browse(vals['surcharge_product_id']).display_name
            if vals.get('name') and not vals.get('surcharge_product_id'):
                vals['surcharge_product_id'] = self.env[
                    'sale.transport.leg.surcharge.line'
                ]._get_or_create_surcharge_product(vals['name']).id
        return super().create(vals_list)
