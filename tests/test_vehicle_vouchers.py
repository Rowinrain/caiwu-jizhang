"""车辆台账：购车凭证草稿、台账与账面核对的提示。"""
from app import vehicles, vouchers

from .support import LedgerTestCase, Y, D, C


class PurchaseDraftTest(LedgerTestCase):

    def setUp(self):
        super().setUp()
        self.vid = self.add_vehicle('苏A11111', '2026-01-15', Y(90000))
        self.aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (self.vid,)).fetchone()[0]

    def test_draft_lines(self):
        d = vehicles.purchase_draft(self.conn, self.vid)
        self.assertEqual(d['date'], '2026-01-15')
        self.assertEqual([(l['account'], l['aux_id'], l['debit'], l['credit']) for l in d['lines']], [
            (self.code('vehicle_fa'), self.aux, Y(90000), 0),
            (self.code('vat_in'), 0, Y(11700), 0),
            (self.code('ap'), 0, 0, Y(101700)),
        ])
        self.assertIn('13%', d['note'])
        self.assertNotIn('注意', d['note'])

    def test_draft_can_be_saved_with_bank(self):
        """草稿把贷方改成银行存款后可以直接保存，固定资产记到这辆车上，核对一致。"""
        d = vehicles.purchase_draft(self.conn, self.vid)
        d['lines'][2].update(account=self.code('bank'), aux_id=self.add_aux('银行账户', 'B1', '基本户'), cf_item='CF13')
        vouchers.save_voucher(self.conn, d)
        rc = vehicles.reconcile(self.conn)
        self.assertEqual(rc['book_original'], Y(90000))
        self.assertEqual(rc['card_original'], Y(90000))
        self.assertEqual(rc['mismatches'], [])

    def test_warns_when_already_booked(self):
        self.post('2026-01-15', D('vehicle_fa', Y(90000), aux=self.aux), C('cash', Y(90000), cf='CF13'))
        self.assertIn('避免重复入账', vehicles.purchase_draft(self.conn, self.vid)['note'])

    def test_warns_for_vehicle_before_start(self):
        old = self.add_vehicle('苏A22222', '2025-06-01', Y(50000))
        self.assertIn('从车辆台账带入', vehicles.purchase_draft(self.conn, old)['note'])


class ReconcileReasonTest(LedgerTestCase):

    def test_purchase_date_after_period(self):
        """截图里的情况：车辆购置日期填成 10 月，但 1 月账上已经有这辆车。"""
        vid = self.add_vehicle('苏A11111', '2026-10-31', Y(90000))
        aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        self.post('2026-01-15', D('vehicle_fa', Y(90000), aux=aux), C('cash', Y(90000), cf='CF13'))
        rc = vehicles.reconcile(self.conn)
        self.assertEqual(rc['period'], 202601)
        self.assertEqual((rc['card_original'], rc['book_original']), (0, Y(90000)))
        self.assertEqual(len(rc['mismatches']), 1)
        self.assertIn('2026-10-31', rc['mismatches'][0]['reason'])
        self.assertIn('修改', rc['mismatches'][0]['reason'])
        # 到 10 月末再核对就一致了
        self.assertEqual(vehicles.reconcile(self.conn, 202610)['mismatches'], [])

    def test_not_booked_yet(self):
        self.add_vehicle('苏A33333', '2026-01-05', Y(80000))
        rc = vehicles.reconcile(self.conn)
        self.assertIn('还没录购车凭证', rc['mismatches'][0]['reason'])
