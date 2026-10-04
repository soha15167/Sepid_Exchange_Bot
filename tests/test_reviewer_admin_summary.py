import json
import unittest
from unittest.mock import patch
from handlers import offers


class ReviewerAdminSummaryTests(unittest.TestCase):
    def test_summary_includes_viewer_receipts(self):
        gate = {'offer_id': 531, 'viewer_toman_receipt_log': json.dumps([{'type': 'photo', 'file_id': 'receipt'}])}
        with patch('database.db.deal_gate_seller_toman_admin_list', return_value=[]):
            body = offers._seller_toman_admin_receipt_line_html(gate, slides_mode=True)
        self.assertIn('<b>1</b>', body)
        self.assertNotIn('در انتظار', body)

    def test_stage_requires_delivery_and_ignores_removed_receipts(self):
        gate = {'offer_id': 531, 'seller_telegram_id': 222, 'viewer_toman_receipt_log': json.dumps([{'type': 'photo'}])}
        with patch('database.db.deal_gate_buyer_receipt_list', return_value=[]), patch('database.db.deal_gate_seller_receipt_list', return_value=[]), patch('database.db.deal_gate_seller_toman_admin_list', return_value=[]), patch.object(offers, '_outbound_delivered_to_user', return_value=True):
            body = offers._deal_admin_steps_checklist_html(gate)
            self.assertIn('✅ <b>9.</b>', body)
            gate['viewer_toman_receipt_log'] = json.dumps([{'attachment_removed_at': 123}])
            body = offers._deal_admin_steps_checklist_html(gate)
            self.assertNotIn('✅ <b>9.</b>', body)
