from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Billing metrics used by automation
    product_order_receipt_id = fields.Many2one('product.product', string='Order Receipt Product', config_parameter='sale_gto.product_order_receipt_id')
    product_sku_in_id       = fields.Many2one('product.product', string='SKU (Incoming)',      config_parameter='sale_gto.product_sku_in_id')
    product_qty_in_id       = fields.Many2one('product.product', string='Qty (Incoming)',      config_parameter='sale_gto.product_qty_in_id')
    product_sku_out_id      = fields.Many2one('product.product', string='SKU (Outgoing)',      config_parameter='sale_gto.product_sku_out_id')
    product_qty_out_id      = fields.Many2one('product.product', string='Qty (Outgoing)',      config_parameter='sale_gto.product_qty_out_id')
    product_serial_id       = fields.Many2one('product.product', string='Serial Capture',       config_parameter='sale_gto.product_serial_id')
    product_lot_id          = fields.Many2one('product.product', string='Lot Capture',          config_parameter='sale_gto.product_lot_id')
    product_expiry_id       = fields.Many2one('product.product', string='Expiry Capture',       config_parameter='sale_gto.product_expiry_id')

    # Handling – explicit items (for manual usage or future automation)
    product_container20_id  = fields.Many2one('product.product', string='Offload/Load 20ft Container', config_parameter='sale_gto.product_container20_id')
    product_container40_id  = fields.Many2one('product.product', string='Offload/Load 40ft Container', config_parameter='sale_gto.product_container40_id')
    product_pallets_in_id   = fields.Many2one('product.product', string='Pallets In', config_parameter='sale_gto.product_pallets_in_id')
    product_pallets_out_id  = fields.Many2one('product.product', string='Pallets Out', config_parameter='sale_gto.product_pallets_out_id')
    product_handling_other_id = fields.Many2one('product.product', string='Handling – Other', config_parameter='sale_gto.product_handling_other_id')

    # Storage – explicit items (manual/periodic billing)
    product_storage_pallet_week_id    = fields.Many2one('product.product', string='Pallet Storage (per week)', config_parameter='sale_gto.product_storage_pallet_week_id')
    product_storage_carton_week_id    = fields.Many2one('product.product', string='Carton Storage (per week)', config_parameter='sale_gto.product_storage_carton_week_id')
    product_storage_container_month_id = fields.Many2one('product.product', string='Container Storage (per month)', config_parameter='sale_gto.product_storage_container_month_id')
    product_storage_unit_month_id     = fields.Many2one('product.product', string='Unit Storage (per month)', config_parameter='sale_gto.product_storage_unit_month_id')
    product_storage_yard_month_id     = fields.Many2one('product.product', string='Yard Space (per month)', config_parameter='sale_gto.product_storage_yard_month_id')
    product_storage_other_id          = fields.Many2one('product.product', string='Storage – Other', config_parameter='sale_gto.product_storage_other_id')

    transport_address_id = fields.Many2one('res.partner', string='Transport Order Default Address', config_parameter='sale_gto.transport_address_id')
    carrier_rate_api_url = fields.Char(
        string='Carrier Rate API URL',
        config_parameter='sale_gto.carrier_rate_api_url',
        default='https://automate.eartisan.co.uk/webhook/get-rates',
    )
    container_default_type_id = fields.Many2one(
        'stock.package.type',
        string='Default Container Type',
        config_parameter='sale_gto.default_container_type_id',
    )

    sale_material_template_id = fields.Many2one(
        "sale.material.template",
        string="Materials Template",
        config_parameter='sale_gto.sale_material_template_id',
    )
