# -*- coding: utf-8 -*-
from odoo import models, fields, api
import requests
import logging

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # حقل إدخال كود الاقتران (لا يحتاج حفظ في قاعدة البيانات، يستخدم فقط عند الربط)
    aura_pairing_code = fields.Char(string="Aura Pairing Code", help="Enter the 6-digit code from your Aura dashboard")

    # حقل حالة الاتصال (يتم حفظه تلقائياً في جداول الإعدادات)
    is_aura_connected = fields.Boolean(
        string="Aura Connected", 
        config_parameter='odoo_connector.is_saas_connected'
    )

    def action_verify_and_connect(self):
        self.ensure_one()
        if not self.aura_pairing_code:
            return self._show_notification('Warning', 'Please enter a Pairing Code first.', 'warning')

        # استخراج "كل المعلومات الممكنة" آلياً
        company = self.env.company
        payload = {
            'pairing_code': self.aura_pairing_code,
            'client_uuid': self.env['ir.config_parameter'].sudo().get_param('database.uuid'),
            'odoo_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
            'database': self.env.cr.dbname,
            'admin_email': self.env.user.email,
            
            # معلومات إضافية لتخصيص الـ Onboarding
            'company_name': company.name,
            'currency': company.currency_id.name,
            'language': self.env.context.get('lang'),
            'odoo_version': '17.0', # أو استخراجها برمجياً
            'timezone': self.env.user.tz or 'UTC',
        }

        webhook_url = "https://n8n.builderbotics.com/webhook/aura-pairing"
        
        try:
            _logger.info("📡 Sending full discovery payload to Aura: %s", payload)
            response = requests.post(webhook_url, json=payload, timeout=20)
            # ... باقي منطق التحقق من الـ API Key كما في الكود السابق
            
            if response.status_code == 200:
                result = response.json()
                api_key = result.get('api_key')
                
                if api_key:
                    # 1. حفظ مفتاح الـ API السري القادم من n8n
                    self.env['ir.config_parameter'].sudo().set_param('odoo_connector.api_key', api_key)
                    # 2. تحديث حالة الاتصال
                    self.env['ir.config_parameter'].sudo().set_param('odoo_connector.is_saas_connected', 'True')
                    
                    return self._show_notification('Success 🎉', 'Aura is now connected to your Odoo instance.', 'success', reload=True)
                
            return self._show_notification('Error', 'Invalid Pairing Code or Connection Refused.', 'danger')

        except Exception as e:
            _logger.error("Aura Connection Error: %s", str(e))
            return self._show_notification('Error', 'Could not reach Aura server. Check your internet/Firewall.', 'danger')

    def action_disconnect_aura(self):
        """مسح كافة بيانات الاتصال لقطع الربط"""
        self.env['ir.config_parameter'].sudo().set_param('odoo_connector.api_key', False)
        self.env['ir.config_parameter'].sudo().set_param('odoo_connector.is_saas_connected', False)
        return self._show_notification('Disconnected', 'Connection to Aura has been removed.', 'warning', reload=True)

    def action_open_aura_dashboard(self):
        """فتح رابط الداشبورد للعميل"""
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://builderbotics.com/dashboard',
            'target': 'new',
        }

    def _show_notification(self, title, message, type, reload=False):
        """دالة مساعدة لإظهار التنبيهات في واجهة أودو"""
        notification = {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': type,
                'sticky': False,
            }
        }
        if reload:
            notification['params']['next'] = {'type': 'ir.actions.client', 'tag': 'reload'}
        return notification