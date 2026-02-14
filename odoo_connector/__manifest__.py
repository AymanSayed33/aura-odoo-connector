# -*- coding: utf-8 -*-
{
    'name': 'Aura ERP Connector',  # الاسم الجديد المعتمد
    'version': '17.0.1.1.0',
    'category': 'Sales/Automation',
    'summary': 'AI-Powered Bridge for Aura ERP & BuilderBotics',
    'description': """
        Aura ERP SaaS Connector
        =======================
        Connects Odoo to the Aura AI platform for:
        - Intelligent Expense Analysis (AI)
        - Smart Collection & Debt Management
        - Telegram Bot Integration
    """,
    'author': 'BuilderBotics',
    'website': 'https://www.builderbotics.com',
    'license': 'LGPL-3',
    'depends': [
        'base', 
        'web', 
        'hr', 
        'account',      # ضروري للتحصيل
        'hr_expense',   # ضروري للمصروفات
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/hr_employee_views.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}