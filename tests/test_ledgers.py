"""总账与明细账：期初行、本月合计、本年累计、滚动余额、跨年“上年结转”、辅助项筛选。"""
from app import ledger
from app.db import AppError
from tests.support import LedgerTestCase, Y, D, C


def signed(row):
    return {'借': row['balance'], '贷': -row['balance'], '平': 0}[row['dir']]


class GeneralLedgerTest(LedgerTestCase):

    def test_monthly_rows(self):
        self.build_basic()
        gl = ledger.general_ledger(self.conn, '1001', 202601, 202602)
        types = [r['type'] for r in gl['rows']]
        self.assertEqual(types, ['begin', 'month', 'ytd', 'month', 'ytd'])
        begin, jan, jan_ytd, feb, feb_ytd = gl['rows']
        self.assertEqual(begin['summary'], '年初余额')
        self.assertEqual(signed(begin), 0)
        self.assertEqual((jan['debit'], jan['credit']), (Y(1011350), Y(3000)))
        self.assertEqual(signed(jan), Y(1008350))
        self.assertEqual((feb['debit'], feb['credit']), (Y(5650), Y(3000)))
        self.assertEqual((feb_ytd['debit'], feb_ytd['credit']), (Y(1017000), Y(6000)))
        self.assertEqual(signed(feb_ytd), Y(1011000))

    def test_parent_account_includes_children(self):
        self.build_basic()
        gl = ledger.general_ledger(self.conn, '6001', 202601, 202602)
        months = [r for r in gl['rows'] if r['type'] == 'month']
        self.assertEqual([m['credit'] for m in months], [Y(10000), Y(5000)])
        self.assertEqual(months[-1]['dir'], '贷')
        self.assertEqual(signed(months[-1]), -Y(15000))

    def test_start_mid_range_carries_ytd(self):
        """从 2 月开始查：期初余额 = 1 月末，本年累计仍包含 1 月。"""
        self.build_basic()
        gl = ledger.general_ledger(self.conn, '1001', 202602, 202602)
        begin, feb, feb_ytd = gl['rows']
        self.assertEqual(begin['summary'], '期初余额')
        self.assertEqual(signed(begin), Y(1008350))
        self.assertEqual(feb_ytd['debit'], Y(1017000))

    def test_cross_year(self):
        self.post('2026-12-10', D('cash', Y(500), cf='CF01'), C('rent_short', Y(500)))
        self.post('2027-01-10', D('cash', Y(200), cf='CF01'), C('rent_short', Y(200)))
        gl = ledger.general_ledger(self.conn, '1001', 202612, 202701)
        summaries = [(r['period'], r['summary']) for r in gl['rows']]
        self.assertIn((202701, '上年结转'), summaries)
        carry = [r for r in gl['rows'] if r['summary'] == '上年结转'][0]
        self.assertEqual(signed(carry), Y(500))
        ytd = [r for r in gl['rows'] if r['type'] == 'ytd']
        self.assertEqual(ytd[0]['debit'], Y(500))   # 2026 年累计
        self.assertEqual(ytd[1]['debit'], Y(200))   # 2027 年重新累计
        self.assertEqual(signed(ytd[1]), Y(700))

    def test_unknown_account(self):
        with self.assertRaises(AppError):
            ledger.general_ledger(self.conn, '9999', 202601, 202601)

    def test_matches_balance_table(self):
        """总账每月的余额必须和科目余额表一致。"""
        self.build_basic()
        for code in ('1001', '2221', '6001', '6602'):
            for p in (202601, 202602):
                bt = {r['code']: r for r in ledger.balance_table(self.conn, p, p)['rows']}[code]
                gl = ledger.general_ledger(self.conn, code, p, p)
                month = [r for r in gl['rows'] if r['type'] == 'month'][0]
                ytd = [r for r in gl['rows'] if r['type'] == 'ytd'][0]
                self.assertEqual((month['debit'], month['credit']), (bt['pd'], bt['pc']), (code, p))
                self.assertEqual((ytd['debit'], ytd['credit']), (bt['yd'], bt['yc']), (code, p))
                self.assertEqual(signed(month), bt['end_d'] - bt['end_c'], (code, p))


class DetailLedgerTest(LedgerTestCase):

    def test_running_balance(self):
        self.build_basic()
        dl = ledger.detail_ledger(self.conn, '1001', 202601, 202601)
        lines = [r for r in dl['rows'] if r['type'] == 'line']
        self.assertEqual(len(lines), 5)
        expected = [1000000, 1011300, 1009300, 1008300, 1008350]
        self.assertEqual([signed(r) for r in lines], [Y(x) for x in expected])
        self.assertEqual([r['voucher'] for r in lines], ['记-0001', '记-0002', '记-0003', '记-0004', '记-0005'])
        month = [r for r in dl['rows'] if r['type'] == 'month'][0]
        self.assertEqual((month['debit'], month['credit']), (Y(1011350), Y(3000)))
        self.assertEqual(signed(month), Y(1008350))

    def test_mid_year_begin_and_ytd(self):
        self.build_basic()
        dl = ledger.detail_ledger(self.conn, '1001', 202602, 202602)
        self.assertEqual(dl['rows'][0]['type'], 'begin')
        self.assertEqual(signed(dl['rows'][0]), Y(1008350))
        ytd = [r for r in dl['rows'] if r['type'] == 'ytd'][0]
        self.assertEqual((ytd['debit'], ytd['credit']), (Y(1017000), Y(6000)))

    def test_cross_year(self):
        self.post('2026-12-10', D('cash', Y(500), cf='CF01'), C('rent_short', Y(500)))
        self.post('2027-01-10', D('cash', Y(200), cf='CF01'), C('rent_short', Y(200)))
        dl = ledger.detail_ledger(self.conn, '1001', 202612, 202701)
        self.assertIn('上年结转', [r['summary'] for r in dl['rows']])
        ytd = [r for r in dl['rows'] if r['type'] == 'ytd']
        self.assertEqual([r['debit'] for r in ytd], [Y(500), Y(200)])
        self.assertEqual(signed(ytd[-1]), Y(700))

    def test_aux_filter(self):
        a = self.add_aux('客户', 'K01', '客户甲')
        b = self.add_aux('客户', 'K02', '客户乙')
        self.post('2026-01-10', D('ar', Y(1000), aux=a), C('rent_short', Y(1000)))
        self.post('2026-01-11', D('ar', Y(400), aux=b), C('rent_short', Y(400)))
        self.post('2026-01-20', D('cash', Y(600), cf='CF01'), C('ar', Y(600), aux=a))
        only_a = ledger.detail_ledger(self.conn, '1122', 202601, 202601, aux_id=a)
        lines = [r for r in only_a['rows'] if r['type'] == 'line']
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(r['aux'] == 'K01 客户甲' for r in lines))
        self.assertEqual(signed(lines[-1]), Y(400))
        everyone = ledger.detail_ledger(self.conn, '1122', 202601, 202601, aux_id='all')
        self.assertEqual(signed([r for r in everyone['rows'] if r['type'] == 'month'][0]), Y(800))



class MidYearStartLedgerTest(LedgerTestCase):
    """年中（7 月）启用：期初 = 启用时余额，本年累计含启用前累计。"""

    START = (2026, 7)

    def test_detail_ledger(self):
        self.set_opening([('cash', Y(500000), Y(100000), Y(30000)), ('capital', -Y(570000), 0, 0)])
        self.post('2026-07-10', D('cash', Y(100), cf='CF01'), C('rent_short', Y(100)))
        dl = ledger.detail_ledger(self.conn, '1001', 202607, 202607)
        self.assertEqual(dl['rows'][0]['summary'], '期初余额（账套启用）')
        self.assertEqual(signed(dl['rows'][0]), Y(570000))
        ytd = [r for r in dl['rows'] if r['type'] == 'ytd'][0]
        self.assertEqual((ytd['debit'], ytd['credit']), (Y(100100), Y(30000)))

    def test_general_ledger(self):
        self.set_opening([('cash', Y(500000), Y(100000), Y(30000)), ('capital', -Y(570000), 0, 0)])
        self.post('2026-07-10', D('cash', Y(100), cf='CF01'), C('rent_short', Y(100)))
        gl = ledger.general_ledger(self.conn, '1001', 202607, 202607)
        begin, month, ytd = gl['rows']
        self.assertEqual(begin['summary'], '期初余额（账套启用）')
        self.assertEqual(signed(begin), Y(570000))
        self.assertEqual((ytd['debit'], ytd['credit']), (Y(100100), Y(30000)))
        self.assertEqual(signed(ytd), Y(570100))
