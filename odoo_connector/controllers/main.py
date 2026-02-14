# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class AuraConnectorAPI(http.Controller):

    def _verify_aura_request(self):
        """التحقق من الهوية باستخدام التوكن الجديد"""
        received_key = request.httprequest.headers.get('x-aura-token')
        actual_key = request.env['ir.config_parameter'].sudo().get_param('odoo_connector.api_key')
        return actual_key and received_key == actual_key

    @http.route('/aura/v1/execute', type='http', auth='public', methods=['POST'], csrf=False)
    def execute_aura_command(self, **kwargs):
        # 1. التحقق من الأمان
        if not self._verify_aura_request():
            _logger.warning("🚫 محاولة وصول غير مصرح بها للـ API.")
            return request.make_response(json.dumps({'status': 'error', 'message': 'Unauthorized'}), 
                                       headers=[('Content-Type', 'application/json')], status=401)

        # 2. تحليل البيانات
        try:
            data = json.loads(request.httprequest.data)
            action = data.get('action')
            params = data.get('params', {})
        except Exception:
            return request.make_response(json.dumps({'status': 'error', 'message': 'Invalid JSON format'}), status=400)

        _logger.info("✅ تنفيذ أمر Aura API: %s", action)

        try:
            # --- المسار 1: ربط التليجرام ---
            if action == 'link_telegram':
                emp_id = params.get('employee_id')
                chat_id = params.get('chat_id')
                request.env['hr.employee'].sudo().browse(int(emp_id)).write({'telegram_chat_id': str(chat_id)})
                result = {'status': 'success', 'message': 'Linked Successfully'}

            # --- المسار 2: قطع الاتصال (Mapping) ---
            elif action == 'delete_mapping':
                chat_id = params.get('chat_id')
                employee = request.env['hr.employee'].sudo().search([('telegram_chat_id', '=', str(chat_id))], limit=1)
                if employee:
                    employee.write({'telegram_chat_id': False})
                    result = {'status': 'success', 'message': 'Unlinked Successfully'}
                else:
                    result = {'status': 'error', 'message': 'Employee not found'}

            # --- المسار 3: إنشاء مصروف AI (المسار القانوني الكامل) ---
            elif action == 'create_expense_ai':
                cat_code = params.get('category_code', 'EXP_OTHERS')
                product = request.env['product.product'].sudo().search([
                    ('default_code', '=', cat_code), ('can_be_expensed', '=', True)
                ], limit=1)
                if not product:
                    product = request.env['product.product'].sudo().search([('can_be_expensed', '=', True)], limit=1)

                expense = request.env['hr.expense'].sudo().create({
                    'name': params.get('vendor') or 'AI Expense',
                    'employee_id': int(params.get('employee_id')),
                    'total_amount': float(params.get('amount', 0)),
                    'product_id': product.id,
                    'description': params.get('notes'),
                    'date': params.get('date'),
                })
                sheet = request.env['hr.expense.sheet'].sudo().create({
                    'name': f"Aura Report: {expense.name}",
                    'employee_id': expense.employee_id.id,
                    'expense_line_ids': [(4, expense.id)],
                })
                sheet.action_submit_sheet()
                result = {'status': 'success', 'expense_id': expense.id, 'sheet_id': sheet.id}

            # --- المسار 4: رفع المرفقات (الصورة) ---
            elif action == 'upload_attachment':
                attachment = request.env['ir.attachment'].sudo().create({
                    'name': params.get('name'),
                    'res_id': int(params.get('res_id')),
                    'res_model': params.get('res_model'),
                    'datas': params.get('datas'),
                    'type': 'binary',
                })
                result = {'status': 'success', 'attachment_id': attachment.id}
            
            # --- المسار 5: تحديث حالة المصروف (مع الـ SQL Fallback القوي) ---
            elif action == 'update_expense_status':
                sheet_id = params.get('sheet_id')
                state = params.get('state') # 'approve' أو 'refuse'
                sheet = request.env['hr.expense.sheet'].sudo().browse(int(sheet_id))
                
                if sheet.exists():
                    try:
                        if state == 'approve':
                            if hasattr(sheet, 'action_approve_sheet'): sheet.action_approve_sheet()
                            elif hasattr(sheet, 'approve_expense_sheets'): sheet.approve_expense_sheets()
                            else: sheet.write({'state': 'approve'})
                        elif state == 'refuse':
                            if hasattr(sheet, 'action_refuse_sheet'): sheet.action_refuse_sheet()
                            else: sheet.write({'state': 'cancel'})
                        result = {'status': 'success'}
                    except Exception as e:
                        _logger.info(f"🔄 Forced SQL update for sheet {sheet_id}")
                        request.env.cr.execute("UPDATE hr_expense_sheet SET state=%s WHERE id=%s", 
                                             ('approve' if state == 'approve' else 'cancel', sheet.id))
                        result = {'status': 'success', 'note': 'Forced via SQL'}
                else:
                    result = {'status': 'error', 'message': 'Sheet not found'}

            # --- المسار 6: التهيئة التلقائية (9 تصنيفات كاملة) ---
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
                for cat in categories:
                    if not request.env['product.product'].sudo().search([('default_code', '=', cat['code'])], limit=1):
                        request.env['product.product'].sudo().create({
                            'name': cat['name'], 'default_code': cat['code'],
                            'can_be_expensed': True, 'type': 'service',
                        })
                        created_count += 1
                result = {'status': 'success', 'message': f'Created {created_count} categories.'}

            # --- المسار 7: التحصيل الذكي (مع منطق الـ Grouping الأصلي) ---
            elif action == 'get_collection_dashboard':
                from datetime import date, timedelta
                days_limit = params.get('days_limit', 7)
                try: days_limit = int(days_limit)
                except: days_limit = 7
                
                target_date = date.today() - timedelta(days=days_limit)
                overdue_moves = request.env['account.move'].sudo().search([
                    ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
                    ('payment_state', 'in', ['not_paid', 'partial']), ('invoice_date_due', '<=', target_date)
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
                            'total_due': 0, 'currency': inv.currency_id.name, 'invoice_details': []
                        }
                    collection_data[p_id]['total_due'] += inv.amount_residual
                    collection_data[p_id]['invoice_details'].append(f"• فاتورة {inv.name} (استحقاق: {inv.invoice_date_due})")

                result = {'status': 'success', 'data': list(collection_data.values())}

            else:
                result = {'status': 'error', 'message': f'Unknown Action: {action}'}

            return request.make_response(json.dumps(result), headers=[('Content-Type', 'application/json')])

        except Exception as e:
            _logger.error("❌ Aura Error: %s", str(e))
            return request.make_response(json.dumps({'status': 'error', 'message': str(e)}), status=500)