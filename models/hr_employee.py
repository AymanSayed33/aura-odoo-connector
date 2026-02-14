# -*- coding: utf-8 -*-
from odoo import models, fields, api
import base64
import requests
import logging

_logger = logging.getLogger(__name__)

class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Aura Platform Integration Fields
    telegram_chat_id = fields.Char(string="Aura Telegram ID", readonly=True)
    is_telegram_linked = fields.Boolean(
        string="Aura Platform Linked", 
        compute="_compute_aura_status", 
        store=True
    )

    @api.depends('telegram_chat_id')
    def _compute_aura_status(self):
        """Compute the link status based on the presence of a Chat ID"""
        for rec in self:
            rec.is_telegram_linked = bool(rec.telegram_chat_id)

    def action_generate_telegram_link(self):
        """Generate a secure, unique link to connect the employee to the Aura Platform"""
        self.ensure_one()
        
        # Retrieve the unique database UUID and employee ID
        db_uuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        raw_token = f"{db_uuid}|{self.id}"
        
        # Encode the token for a safe URL (Base64)
        safe_token = base64.urlsafe_b64encode(raw_token.encode()).decode().replace('=', '')
        
        # Retrieve the Platform username from settings (Default to aura_erp_platform)
        platform_username = self.env['ir.config_parameter'].sudo().get_param('aura.telegram_platform_name', 'aura_erp_platform')
        
        return {
            'type': 'ir.actions.act_url',
            'url': f"https://t.me/builder_erp_bot?start={safe_token}",
            'target': 'new',
        }

    def action_disconnect_telegram(self):
        """Disconnect the employee and notify n8n to clear data from the cloud platform"""
        self.ensure_one()
        chat_id = self.telegram_chat_id
        
        # 1. Notify n8n to delete the mapping in the cloud database (Supabase)
        # Ensure your n8n webhook URL is correct
        n8n_webhook_url = "https://n8n.builderbotics.com/webhook/aura-disconnect-employee"
        try:
            # Send the security API Key to authenticate the request
            api_key = self.env['ir.config_parameter'].sudo().get_param('odoo_connector.api_key')
            requests.post(n8n_webhook_url, json={
                "action": "delete_mapping",
                "chat_id": chat_id,
                "api_key": api_key
            }, timeout=10)
        except Exception as e:
            _logger.error("🚫 Failed to notify Aura Platform for disconnect: %s", str(e))

        # 2. Clear data locally in Odoo
        self.write({'telegram_chat_id': False})

        # 3. Refresh the interface to reflect changes
        return {'type': 'ir.actions.client', 'tag': 'reload'}