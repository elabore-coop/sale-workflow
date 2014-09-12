# -*- coding: utf-8 -*-
#
#    Author: Alexandre Fayolle
#    Copyright 2014 Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#
import logging

from openerp import models, fields, api, _
from openerp.exceptions import except_orm

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.multi
    def action_button_confirm(self):
        """before triggering the workflow, if some lines need sourcing, run the
        sourcing wizard, otherwise, propagate the call and do the confirmation
        of the SO.
        """
        self.ensure_one()
        order = self[0]
        lines_to_source = []
        for line in order.order_line:
            if line.needs_sourcing():
                lines_to_source.append(line)
        if lines_to_source:
            wizard = self._create_sourcing_wizard(lines_to_source)
            return {'type': 'ir.actions.act_window',
                    'view_mode': 'form',
                    'view_type': 'form',
                    'res_model': 'sale.order.sourcing',
                    'res_id': wizard.id,
                    'target': 'new',
                    }
        else:
            return super(SaleOrder, self).action_button_confirm()

    def _create_sourcing_wizard(self, lines_to_source):
        line_values = []
        for line in lines_to_source:
            line_values.append((0, 0, {'so_line_id': line.id, 'po_id': False}))
        values = {'sale_id': self[0].id,
                  'line_ids': line_values,
                  }
        return self.env['sale.order.sourcing'].create(values)


    def _prepare_order_line_procurement(self, cr, uid, order, line, group_id, context=None):
        proc_data = super(SaleOrder, self)._prepare_order_line_procurement(cr, uid, order, line, group_id, context)
        procurement_rule_obj = self.pool['procurement.rule']
        rule_ids = procurement_rule_obj.search(cr, uid,
                                            [('warehouse_id', '=', proc_data['warehouse_id']),
                                             ('action', '=', 'buy'),
                                             # ('procurement_method', '=', 'make_to_order'),
                                            ],
                                            limit=1,
                                            order='route_sequence',
                                            context=context)
        if not rule_ids:
            raise except_orm(_('configuration problem'),
                             _('no buy rule configured for warehouse %d') % proc_data['warehouse_id'])
        if line.manually_sourced:
            proc_data['rule_id'] = rule_ids[0]
        print proc_data
        return proc_data


class ProcurementOrder(models.Model):
    _inherit = 'procurement.order'

    @api.multi
    def make_po(self):
        """only call the base implementation for procurement of SO lines not manually sourced
        otherwise, just link to the existing PO and PO line"""
        res = {}
        to_propagate = self.browse()
        for procurement in self:
            if procurement.sale_line_id.manually_sourced:
                po_line = procurement.sale_line_id.sourced_by
                res[procurement.id] = po_line.id
                procurement.purchase_line_id = po_line
                procurement.message_post(body=_('Manually sourced'))
            else:
                to_propagate |= procurement
        res.update(super(ProcurementOrder, to_propagate).make_po())
        return res


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    manually_sourced = fields.Boolean('Manually Sourced')
    sourced_by = fields.Many2one('purchase.order.line')

    @api.multi
    def needs_sourcing(self):
        return any(line.manually_sourced and not line.sourced_by
                   for line in self)


class QuotationSourcingWizard(models.TransientModel):
    _name = 'sale.order.sourcing'
    sale_id = fields.Many2one('sale.order', string='Sale Order')
    line_ids = fields.One2many('sale.order.line.sourcing', 'wizard_id',
                               string='Lines')
    _rec_name = 'sale_id'

    @api.multi
    def action_done(self):
        self.ensure_one()
        for line in self.line_ids:
            line.so_line_id.sourced_by =  line.po_line_id
        return self[0].sale_id.action_button_confirm()


class QuotationLineSource(models.TransientModel):
    _name = 'sale.order.line.sourcing'
    wizard_id = fields.Many2one('sale.order.sourcing', string='Wizard')
    so_line_id = fields.Many2one('sale.order.line', string='Sale Order Line')
    product_id = fields.Many2one('product.product', string='Product', related=('so_line_id', 'product_id'))
    po_id = fields.Many2one('purchase.order', string='Purchase Order')
    po_line_id = fields.Many2one('purchase.order.line', string='Sourced By')

    @api.onchange('po_id')
    def onchange_po(self):
        if self.po_id:
            return {'domain': {'po_line_id': [('order_id', '=', self.po_id.id),
                                              ('product_id', '=', self.product_id.id),
                                              ('state', 'not in', ('done', 'cancel')),
                                              ]
                                    }
                        }
        else:
            return {'domain': {'po_line_id': [('product_id', '=', self.product_id.id),
                                              ('state', 'not in', ('done', 'cancel')),
                                            ]
                            }
                }
