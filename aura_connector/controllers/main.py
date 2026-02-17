# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict

_logger = logging.getLogger(__name__)

# Rate Limiting Cache (في بيئة الإنتاج استخدم Redis)
request_cache = defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 60

class AuraConnectorAPI(http.Controller):

    def _verify_aura_request(self):
        """التحقق من الهوية باستخدام التوكن + Rate Limiting"""
        received_key = request.httprequest.headers.get('x-aura-token')
        
        # التحقق من التوكن
        actual_key = request.env['ir.config_parameter'].sudo().get_param('aura_connector.api_key')
        if not actual_key or received_key != actual_key:
            return False
        
        # Rate Limiting بناءً على الـ Token
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # تنظيف الطلبات القديمة
        request_cache[received_key] = [
            timestamp for timestamp in request_cache[received_key]
            if timestamp > minute_ago
        ]
        
        # التحقق من عدد الطلبات
        if len(request_cache[received_key]) >= MAX_REQUESTS_PER_MINUTE:
            _logger.warning("⚠️ Rate limit exceeded for token: %s", received_key[:10])
            return False
        
        # إضافة الطلب الحالي
        request_cache[received_key].append(now)
        return True

    def _safe_json_response(self, data, status=200):
        """دالة آمنة لإرجاع JSON Response"""
        return request.make_response(
            json.dumps(data, ensure_ascii=False),
            headers=[
                ('Content-Type', 'application/json; charset=utf-8'),
                ('X-Content-Type-Options', 'nosniff'),
                ('X-Frame-Options', 'DENY'),
            ],
            status=status
        )

    def _error_response(self, message, status=400):
        """دالة موحدة للـ Error Responses"""
        _logger.error("❌ API Error: %s", message)
        return self._safe_json_response({
            'status': 'error',
            'message': message,
            'timestamp': datetime.now().isoformat()
        }, status=status)

    @http.route('/aura/v1/execute', type='http', auth='public', methods=['POST'], csrf=False)
    def execute_aura_command(self, **kwargs):
        """
        Aura API Endpoint - Secured with Token + Rate Limiting
        """
        # 1. التحقق من الأمان + Rate Limiting
        if not self._verify_aura_request():
            return self._error_response('Unauthorized or Rate Limit Exceeded', status=401)

        # 2. التحقق من Content-Type
        if request.httprequest.content_type != 'application/json':
            return self._error_response('Content-Type must be application/json', status=415)

        # 3. تحليل البيانات بشكل آمن
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
            action = data.get('action')
            params = data.get('params', {})
            
            if not action:
                return self._error_response('Missing required field: action')
                
        except json.JSONDecodeError as e:
            return self._error_response(f'Invalid JSON format: {str(e)}')
        except Exception as e:
            return self._error_response(f'Request parsing error: {str(e)}')

        _logger.info("✅ Executing Aura API: %s", action)

        try:
            # --- المسار 1: ربط التليجرام ---
            if action == 'link_telegram':
                emp_id = params.get('employee_id')
                chat_id = params.get('chat_id')
                
                if not emp_id or not chat_id:
                    return self._error_response('Missing employee_id or chat_id')
                
                try:
                    employee = request.env['hr.employee'].sudo().browse(int(emp_id))
                    if not employee.exists():
                        return self._error_response('Employee not found')
                    
                    employee.write({'telegram_chat_id': str(chat_id)})
                    result = {'status': 'success', 'message': 'Linked Successfully'}
                except ValueError:
                    return self._error_response('Invalid employee_id format')

            # --- المسار 2: قطع الاتصال ---
            elif action == 'delete_mapping':
                chat_id = params.get('chat_id')
                if not chat_id:
                    return self._error_response('Missing chat_id')
                
                employee = request.env['hr.employee'].sudo().search([
                    ('telegram_chat_id', '=', str(chat_id))
                ], limit=1)
                
                if employee:
                    employee.write({'telegram_chat_id': False})
                    result = {'status': 'success', 'message': 'Unlinked Successfully'}
                else:
                    result = {'status': 'error', 'message': 'Employee not found'}

            # --- المسار 3: إنشاء مصروف AI ---
            elif action == 'create_expense_ai':
                required_fields = ['employee_id', 'amount', 'vendor']
                missing_fields = [f for f in required_fields if not params.get(f)]
                if missing_fields:
                    return self._error_response(f'Missing required fields: {", ".join(missing_fields)}')
                
                try:
                    cat_code = params.get('category_code', 'EXP_OTHERS')
                    product = request.env['product.product'].sudo().search([
                        ('default_code', '=', cat_code),
                        ('can_be_expensed', '=', True)
                    ], limit=1)
                    
                    if not product:
                        product = request.env['product.product'].sudo().search([
                            ('can_be_expensed', '=', True)
                        ], limit=1)
                    
                    if not product:
                        return self._error_response('No expense products available. Please configure expense categories first.')
                    
                    expense = request.env['hr.expense'].sudo().create({
                        'name': params.get('vendor'),
                        'employee_id': int(params.get('employee_id')),
                        'total_amount': float(params.get('amount', 0)),
                        'product_id': product.id,
                        'description': params.get('notes', ''),
                        'date': params.get('date', datetime.now().date()),
                    })
                    
                    sheet = request.env['hr.expense.sheet'].sudo().create({
                        'name': f"Aura Report: {expense.name}",
                        'employee_id': expense.employee_id.id,
                        'expense_line_ids': [(4, expense.id)],
                    })
                    
                    # استخدام الـ Workflow العادي بدلاً من SQL
                    if hasattr(sheet, 'action_submit_sheet'):
                        sheet.action_submit_sheet()
                    
                    result = {
                        'status': 'success',
                        'expense_id': expense.id,
                        'sheet_id': sheet.id
                    }
                    
                except ValueError as e:
                    return self._error_response(f'Invalid data format: {str(e)}')

            # --- المسار 4: رفع المرفقات ---
            elif action == 'upload_attachment':
                required_fields = ['name', 'res_id', 'res_model', 'datas']
                missing_fields = [f for f in required_fields if not params.get(f)]
                if missing_fields:
                    return self._error_response(f'Missing required fields: {", ".join(missing_fields)}')
                
                attachment = request.env['ir.attachment'].sudo().create({
                    'name': params.get('name'),
                    'res_id': int(params.get('res_id')),
                    'res_model': params.get('res_model'),
                    'datas': params.get('datas'),
                    'type': 'binary',
                })
                result = {'status': 'success', 'attachment_id': attachment.id}
            
            # --- المسار 5: تحديث حالة المصروف (بدون SQL) ---
            elif action == 'update_expense_status':
                sheet_id = params.get('sheet_id')
                state = params.get('state')
                
                if not sheet_id or state not in ['approve', 'refuse']:
                    return self._error_response('Invalid sheet_id or state')
                
                sheet = request.env['hr.expense.sheet'].sudo().browse(int(sheet_id))
                
                if not sheet.exists():
                    return self._error_response('Sheet not found')
                
                try:
                    if state == 'approve':
                        if hasattr(sheet, 'action_approve_expense_sheets'):
                            sheet.action_approve_expense_sheets()
                        elif hasattr(sheet, 'approve_expense_sheets'):
                            sheet.approve_expense_sheets()
                        else:
                            # استخدام write بدلاً من SQL المباشر
                            sheet.write({'state': 'approve'})
                    
                    elif state == 'refuse':
                        if hasattr(sheet, 'action_refuse_sheet'):
                            sheet.action_refuse_sheet()
                        else:
                            sheet.write({'state': 'cancel'})
                    
                    result = {'status': 'success'}
                    
                except Exception as e:
                    _logger.error("Failed to update expense sheet: %s", str(e))
                    return self._error_response(f'Failed to update expense: {str(e)}')

            # --- المسار 6: التهيئة التلقائية ---
            elif action == 'setup_saas_categories':
                categories = [
                    {'name': 'وجبات وطعام (Aura)', 'code': 'EXP_MEALS'},
                    {'name': 'سفر وإقامة (Aura)', 'code': 'EXP_TRAVEL'},
                    {'name': 'انتقالات ومواصلات (Aura)', 'code': 'EXP_MILE'},
                    {'name': 'اتصالات وإنترنت (Aura)', 'code': 'EXP_COMM'},
                    {'name': 'هدايا وضيافة (Aura)', 'code': 'EXP_GIFTS'},
                    {'name': 'أدوات مكتبية (Aura)', 'code': 'EXP_OFFICE'},
                    {'name': 'صيانة وإصلاحات (Aura)', 'code': 'EXP_MAINT'},
                    {'name': 'رعاية طبية (Aura)', 'code': 'EXP_MEDICAL'},
                    {'name': 'مصاريف أخرى (Aura)', 'code': 'EXP_OTHERS'},
                ]
                
                created_count = 0
                ProductProduct = request.env['product.product'].sudo()
                
                for cat in categories:
                    if not ProductProduct.search([('default_code', '=', cat['code'])], limit=1):
                        ProductProduct.create({
                            'name': cat['name'],
                            'default_code': cat['code'],
                            'can_be_expensed': True,
                            'type': 'service',
                        })
                        created_count += 1
                
                result = {
                    'status': 'success',
                    'message': f'Created {created_count} categories.'
                }

            # --- المسار 7: التحصيل الذكي ---
            elif action == 'get_collection_dashboard':
                from datetime import date, timedelta
                
                days_limit = params.get('days_limit', 7)
                try:
                    days_limit = int(days_limit)
                except (ValueError, TypeError):
                    days_limit = 7
                
                target_date = date.today() - timedelta(days=days_limit)
                overdue_moves = request.env['account.move'].sudo().search([
                    ('move_type', '=', 'out_invoice'),
                    ('state', '=', 'posted'),
                    ('payment_state', 'in', ['not_paid', 'partial']),
                    ('invoice_date_due', '<=', target_date)
                ])

                collection_data = {}
                for inv in overdue_moves:
                    p_id = inv.partner_id.id
                    if p_id not in collection_data:
                        collection_data[p_id] = {
                            'partner_id': p_id,
                            'partner_name': inv.partner_id.name,
                            'partner_email': inv.partner_id.email or '',
                            'partner_mobile': inv.partner_id.mobile or inv.partner_id.phone or '',
                            'total_due': 0,
                            'currency': inv.currency_id.name,
                            'invoice_details': []
                        }
                    
                    collection_data[p_id]['total_due'] += inv.amount_residual
                    collection_data[p_id]['invoice_details'].append(
                        f"• فاتورة {inv.name} (استحقاق: {inv.invoice_date_due})"
                    )

                result = {
                    'status': 'success',
                    'data': list(collection_data.values())
                }

            else:
                return self._error_response(f'Unknown action: {action}', status=400)

            return self._safe_json_response(result)

        except Exception as e:
            _logger.exception("Unexpected error in Aura API")
            return self._error_response(f'Internal server error: {str(e)}', status=500)