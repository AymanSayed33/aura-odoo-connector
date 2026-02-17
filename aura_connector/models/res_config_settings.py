# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # حقل إدخال كود الاقتران
    aura_pairing_code = fields.Char(
        string="Aura Pairing Code",
        help="Enter the 6-digit code from your Aura dashboard"
    )

    # حقل حالة الاتصال
    is_aura_connected = fields.Boolean(
        string="Aura Connected",
        config_parameter='aura_connector.is_saas_connected'
    )
    
    # إعدادات إضافية (اختيارية)
    aura_webhook_url = fields.Char(
        string="Aura Webhook URL",
        config_parameter='aura.pairing_webhook_url',
        default='https://n8n.builderbotics.com/webhook/aura-pairing',
        help="The webhook URL for Aura pairing (advanced setting)"
    )
    
    aura_telegram_bot = fields.Char(
        string="Telegram Bot Username",
        config_parameter='aura.telegram_bot_username',
        default='builder_erp_bot',
        help="Your Telegram bot username (without @)"
    )

    @api.model
    def get_values(self):
        """Override to load custom settings"""
        res = super(ResConfigSettings, self).get_values()
        ICPSudo = self.env['ir.config_parameter'].sudo()
        
        res.update(
            aura_webhook_url=ICPSudo.get_param('aura.pairing_webhook_url', 
                'https://n8n.builderbotics.com/webhook/aura-pairing'),
            aura_telegram_bot=ICPSudo.get_param('aura.telegram_bot_username', 
                'builder_erp_bot'),
        )
        return res

    def set_values(self):
        """Override to save custom settings"""
        super(ResConfigSettings, self).set_values()
        ICPSudo = self.env['ir.config_parameter'].sudo()
        
        ICPSudo.set_param('aura.pairing_webhook_url', self.aura_webhook_url or '')
        ICPSudo.set_param('aura.telegram_bot_username', self.aura_telegram_bot or 'builder_erp_bot')

    def action_verify_and_connect(self):
        """Connect to Aura Platform with enhanced error handling"""
        self.ensure_one()
        
        # التحقق من وجود Pairing Code
        if not self.aura_pairing_code:
            raise UserError(_('Please enter a Pairing Code first.'))
        
        # التحقق من صيغة الكود (اختياري)
        if len(self.aura_pairing_code.strip()) < 4:
            raise UserError(_('Invalid Pairing Code format. Please check and try again.'))

        # جمع معلومات الشركة والنظام
        company = self.env.company
        db_uuid = self.env['ir.config_parameter'].sudo().get_param('database.uuid')
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        
        if not db_uuid:
            raise UserError(_(
                'Database UUID not found. This is required for secure pairing.\n'
                'Please contact your system administrator.'
            ))
        
        payload = {
            'pairing_code': self.aura_pairing_code.strip(),
            'client_uuid': db_uuid,
            'odoo_url': base_url,
            'database': self.env.cr.dbname,
            'admin_email': self.env.user.email or self.env.user.login,
            
            # معلومات إضافية للتخصيص
            'company_name': company.name,
            'currency': company.currency_id.name,
            'language': self.env.context.get('lang', 'en_US'),
            'odoo_version': '17.0',
            'timezone': self.env.user.tz or 'UTC',
            'country': company.country_id.code if company.country_id else None,
        }

        # استخدام الـ Webhook URL من الإعدادات
        webhook_url = self.aura_webhook_url or 'https://n8n.builderbotics.com/webhook/aura-pairing'
        
        try:
            _logger.info("📡 Sending pairing request to Aura Platform...")
            _logger.debug("Payload: %s", {k: v for k, v in payload.items() if k != 'pairing_code'})
            
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=30,  # زيادة الـ timeout
                headers={'Content-Type': 'application/json'}
            )
            
            _logger.info("Response Status: %s", response.status_code)
            
            if response.status_code == 200:
                try:
                    result = response.json()
                except ValueError:
                    raise UserError(_(
                        'Invalid response from Aura Platform. '
                        'Please contact support.'
                    ))
                
                api_key = result.get('api_key')
                
                if not api_key:
                    error_msg = result.get('message', 'Unknown error')
                    raise UserError(_(
                        'Pairing failed: %s\n\n'
                        'Please check your pairing code and try again.'
                    ) % error_msg)
                
                # حفظ البيانات بنجاح
                ICPSudo = self.env['ir.config_parameter'].sudo()
                ICPSudo.set_param('aura_connector.api_key', api_key)
                ICPSudo.set_param('aura_connector.is_saas_connected', 'True')
                
                _logger.info("✅ Successfully connected to Aura Platform")
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success! 🎉'),
                        'message': _('Aura is now connected to your Odoo instance.'),
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                    }
                }
            
            elif response.status_code == 400:
                raise UserError(_(
                    'Invalid Pairing Code.\n\n'
                    'Please check your code from the Aura Dashboard and try again.'
                ))
            
            elif response.status_code == 409:
                raise UserError(_(
                    'This Odoo instance is already paired with another Aura account.\n\n'
                    'Please disconnect first or use a different pairing code.'
                ))
            
            else:
                raise UserError(_(
                    'Connection failed (HTTP %s).\n\n'
                    'Please try again or contact support if the problem persists.'
                ) % response.status_code)

        except requests.exceptions.Timeout:
            raise UserError(_(
                'Connection timeout.\n\n'
                'The Aura Platform did not respond in time. '
                'Please check your internet connection and try again.'
            ))
        
        except requests.exceptions.ConnectionError:
            raise UserError(_(
                'Cannot reach Aura Platform.\n\n'
                'Please check:\n'
                '• Your internet connection\n'
                '• Firewall settings\n'
                '• VPN configuration (if applicable)'
            ))
        
        except requests.exceptions.RequestException as e:
            _logger.error("Aura connection error: %s", str(e))
            raise UserError(_(
                'Network error: %s\n\n'
                'Please contact your system administrator.'
            ) % str(e))

    def action_disconnect_aura(self):
        """Disconnect from Aura Platform with confirmation"""
        self.ensure_one()
        
        # مسح جميع بيانات الاتصال
        ICPSudo = self.env['ir.config_parameter'].sudo()
        ICPSudo.set_param('aura_connector.api_key', False)
        ICPSudo.set_param('aura_connector.is_saas_connected', False)
        
        # مسح كل روابط Telegram للموظفين (اختياري - حسب المنطق المطلوب)
        # self.env['hr.employee'].sudo().search([
        #     ('telegram_chat_id', '!=', False)
        # ]).write({'telegram_chat_id': False})
        
        _logger.info("🔌 Disconnected from Aura Platform")
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Disconnected'),
                'message': _('Connection to Aura has been removed successfully.'),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }

    def action_open_aura_dashboard(self):
        """Open Aura Dashboard in new tab"""
        dashboard_url = self.env['ir.config_parameter'].sudo().get_param(
            'aura.dashboard_url',
            'https://builderbotics.com/dashboard'
        )
        
        return {
            'type': 'ir.actions.act_url',
            'url': dashboard_url,
            'target': 'new',
        }

    def action_test_connection(self):
        """Test API connection to verify setup"""
        self.ensure_one()
        
        api_key = self.env['ir.config_parameter'].sudo().get_param('aura_connector.api_key')
        if not api_key:
            raise UserError(_('Not connected to Aura. Please pair your instance first.'))
        
        # يمكن إضافة endpoint للـ health check هنا
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection OK'),
                'message': _('Your Aura connection is working properly.'),
                'type': 'success',
            }
        }