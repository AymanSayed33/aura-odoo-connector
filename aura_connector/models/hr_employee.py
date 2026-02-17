# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import base64
import requests
import logging

_logger = logging.getLogger(__name__)

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Aura Platform Integration Fields
    telegram_chat_id = fields.Char(
        string="Aura Telegram ID",
        readonly=True,
        help="Telegram Chat ID linked to this employee"
    )
    is_telegram_linked = fields.Boolean(
        string="Aura Platform Linked",
        compute="_compute_aura_status",
        store=True,
        help="Indicates if employee is connected to Aura Platform"
    )

    @api.depends('telegram_chat_id')
    def _compute_aura_status(self):
        """Compute the link status based on the presence of a Chat ID"""
        for rec in self:
            rec.is_telegram_linked = bool(rec.telegram_chat_id)

    @api.constrains('telegram_chat_id')
    def _check_unique_telegram_id(self):
        """التأكد من أن كل Chat ID مرتبط بموظف واحد فقط"""
        for rec in self:
            if rec.telegram_chat_id:
                duplicate = self.search([
                    ('telegram_chat_id', '=', rec.telegram_chat_id),
                    ('id', '!=', rec.id)
                ], limit=1)
                if duplicate:
                    raise ValidationError(_(
                        'This Telegram account is already linked to %s. '
                        'Please disconnect it first.'
                    ) % duplicate.name)

    def action_generate_telegram_link(self):
        """Generate a secure, unique link to connect the employee to the Aura Platform"""
        self.ensure_one()
        
        # التحقق من أن الموديول متصل بـ Aura
        is_connected = self.env['ir.config_parameter'].sudo().get_param(
            'aura_connector.is_saas_connected'
        )
        if not is_connected or is_connected == 'False':
            raise UserError(_(
                'Aura is not connected!\n\n'
                'Please go to Settings > Aura ERP and connect your instance first.'
            ))
        
        # Retrieve the unique database UUID and employee ID
        db_uuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        if not db_uuid:
            raise UserError(_('Database UUID not found. Please contact your administrator.'))
        
        raw_token = f"{db_uuid}|{self.id}"
        
        # Encode the token for a safe URL (Base64)
        safe_token = base64.urlsafe_b64encode(raw_token.encode()).decode().rstrip('=')
        
        # Retrieve the bot username from settings (configurable)
        bot_username = self.env['ir.config_parameter'].sudo().get_param(
            'aura.telegram_bot_username',
            'builder_erp_bot'  # Default fallback
        )
        
        telegram_url = f"https://t.me/builder_erp_bot?start={safe_token}"
        
        return {
            'type': 'ir.actions.act_url',
            'url': telegram_url,
            'target': 'new',
        }

    def action_disconnect_telegram(self):
        """Disconnect the employee and notify n8n to clear data from the cloud platform"""
        self.ensure_one()
        
        if not self.telegram_chat_id:
            raise UserError(_('This employee is not linked to any Telegram account.'))
        
        chat_id = self.telegram_chat_id
        
        # 1. Retrieve n8n webhook URL from settings (configurable)
        webhook_url = self.env['ir.config_parameter'].sudo().get_param(
            'aura.disconnect_webhook_url',
            'https://n8n.builderbotics.com/webhook/aura-disconnect-employee'
        )
        
        # 2. Notify n8n to delete the mapping in the cloud database
        try:
            api_key = self.env['ir.config_parameter'].sudo().get_param('aura_connector.api_key')
            if not api_key:
                _logger.warning("API Key not found. Skipping cloud notification.")
            else:
                response = requests.post(
                    webhook_url,
                    json={
                        "action": "delete_mapping",
                        "chat_id": chat_id,
                        "api_key": api_key
                    },
                    timeout=10
                )
                
                if response.status_code != 200:
                    _logger.warning(
                        "Failed to notify Aura Platform (HTTP %s). "
                        "Proceeding with local disconnect.",
                        response.status_code
                    )
                    
        except requests.exceptions.Timeout:
            _logger.error("Timeout while notifying Aura Platform for disconnect")
            raise UserError(_(
                'Connection timeout. Please check your internet connection and try again.'
            ))
        except requests.exceptions.RequestException as e:
            _logger.error("Failed to notify Aura Platform: %s", str(e))
            # لا نوقف العملية، نكمل الـ disconnect محلياً

        # 3. Clear data locally in Odoo
        self.write({'telegram_chat_id': False})
        
        # 4. Show success message
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Disconnected'),
                'message': _('Employee successfully disconnected from Aura Platform.'),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }