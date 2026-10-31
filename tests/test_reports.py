"""资产负债表平衡与取数、利润表各项目（企业准则和小企业准则两套格式）。"""
from app import closing, reports
from tests.support import LedgerTestCase, Y, D, C


def bs_values(rep, col='end'):
    return {r['key']: r[col] for r in rep['rows']}


def is_values(rep, col='month'):
    return {r['key']: r[col] for r in rep['rows']}


class BalanceSheetTest(LedgerTestCase):

    def assertBalanced(self, rep):
        self.assertEqual(rep['checks']['end_diff'], 0, '期末资产 ≠ 负债 + 所有者权益')
        self.assertEqual(rep['checks']['begin_diff'], 0, '期初资产 ≠ 负债 + 所有者权益')

    def test_empty(self):
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        self.assertEqual(bs_values(rep)['assets'], 0)

    def test_values_and_unclosed_profit(self):
        """未结转的损益计入“未分配利润”，报表照样平衡。"""
        self.build_basic()
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        v = bs_values(rep)
        self.assertEqual(v['cash'], Y(1008350))
        self.assertEqual(v['assets'], Y(1008350))
        self.assertEqual(v['tax'], Y(1300))
        self.assertEqual(v['capital'], Y(1000000))
        self.assertEqual(v['retained'], Y(7050))
        self.assertEqual(v['le_total'], Y(1008350))
        self.assertEqual(rep['begin_label'], '年初余额')

    def test_begin_column(self):
        self.build_basic()
        rep = reports.balance_sheet(self.conn, 202602, 202602)
        self.assertBalanced(rep)
        self.assertEqual(bs_values(rep, 'begin')['cash'], Y(1008350))  # 2 月初 = 1 月末
        self.assertEqual(bs_values(rep)['retained'], Y(9050))           # 1、2 月累计利润

    def test_same_after_carry_forward(self):
        self.build_basic()
        before = bs_values(reports.balance_sheet(self.conn, 202601))
        closing.carry_forward(self.conn, 202601)
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        self.assertEqual(bs_values(rep), before)

    def test_receivable_reclassification(self):
        """应收账款按明细：借方余额列应收账款，贷方余额（预收）列预收款项。"""
        a = self.add_aux('客户', 'K01', '客户甲')
        b = self.add_aux('客户', 'K02', '客户乙')
        self.post('2026-01-05', D('cash', Y(10000), cf='CF21'), C('capital', Y(10000)))
        self.post('2026-01-10', D('ar', Y(1000), aux=a), C('rent_short', Y(1000)))
        self.post('2026-01-12', D('cash', Y(300), cf='CF01'), C('ar', Y(300), aux=b))
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        v = bs_values(rep)
        self.assertEqual(v['ar'], Y(1000))
        self.assertEqual(v['adv'], Y(300))

    def test_vat_debit_balance_is_current_asset(self):
        """应交增值税借方余额（留抵）列“其他流动资产”，不冲减应交税费。"""
        self.post('2026-01-05', D('cash', Y(10000), cf='CF21'), C('capital', Y(10000)))
        self.post('2026-01-10', D('veh_repair', Y(1000)), D('vat_in', Y(130)), C('cash', Y(1130), cf='CF04'))
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        v = bs_values(rep)
        self.assertEqual(v['other_ca'], Y(130))
        self.assertEqual(v['tax'], 0)

    def test_fixed_assets_net_of_depreciation(self):
        self.post('2026-01-05', D('cash', Y(200000), cf='CF21'), C('capital', Y(200000)))
        vid = self.add_vehicle('京A00001', '2025-12-01', Y(100000))
        aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        self.post('2026-01-06', D('vehicle_fa', Y(100000), aux=aux), C('cash', Y(100000), cf='CF16'))
        closing.run_depreciation(self.conn, 202601)
        rep = reports.balance_sheet(self.conn, 202601)
        self.assertBalanced(rep)
        self.assertEqual(bs_values(rep)['fixed'], Y(100000) - 197917)

    def test_mid_year_start_with_opening(self):
        self.conn.close()
        # 换一个年中启用的账套
        from app import db
        cid = db.create_company('年中启用', 2026, 7)
        self.conn = db.connect(cid)
        self.addCleanup(self.conn.close)
        self.set_opening([
            ('cash', Y(500000), Y(100000), Y(30000)),
            ('capital', -Y(500000), 0, 0),
            ('profit', 0, 0, Y(70000)),
        ])
        self.post('2026-07-10', D('cash', Y(1000), cf='CF01'), C('rent_short', Y(1000)))
        rep = reports.balance_sheet(self.conn, 202607)
        self.assertBalanced(rep)
        self.assertEqual(rep['begin_label'], '年初余额')
        self.assertEqual(bs_values(rep, 'begin')['cash'], Y(500000))
        self.assertEqual(bs_values(rep, 'begin')['retained'], 0)
        self.assertEqual(bs_values(rep)['cash'], Y(571000))
        self.assertEqual(bs_values(rep)['retained'], Y(71000))


class IncomeStatementTest(LedgerTestCase):

    def test_items(self):
        self.build_basic()
        v = is_values(reports.income_statement(self.conn, 202601))
        self.assertEqual(v['revenue'], Y(10000))
        self.assertEqual(v['cost'], Y(1000))
        self.assertEqual(v['admin'], Y(2000))
        self.assertEqual(v['finance'], -Y(50))     # 利息收入冲减财务费用
        self.assertEqual(v['interest_inc'], Y(50))
        self.assertEqual(v['op_profit'], Y(7050))
        self.assertEqual(v['total_profit'], Y(7050))
        self.assertEqual(v['net_profit'], Y(7050))

    def test_month_ytd_and_range(self):
        self.build_basic()
        rep = reports.income_statement(self.conn, 202602)
        self.assertEqual(is_values(rep)['net_profit'], Y(2000))
        self.assertEqual(is_values(rep, 'ytd')['net_profit'], Y(9050))
        self.assertEqual(is_values(rep, 'ytd')['revenue'], Y(15000))
        self.assertFalse(rep['has_prev'])  # 账套启用前没有上年同期
        both = reports.income_statement(self.conn, 202601, 202602)
        self.assertEqual(is_values(both)['net_profit'], Y(9050))

    def test_excludes_carry_forward_vouchers(self):
        self.build_basic()
        before = reports.income_statement(self.conn, 202601)
        closing.carry_forward(self.conn, 202601)
        after = reports.income_statement(self.conn, 202601)
        self.assertEqual(is_values(after), is_values(before))
        self.assertEqual(is_values(after, 'ytd'), is_values(before, 'ytd'))

    def test_net_profit_equals_retained_change(self):
        """利润表净利润 = 资产负债表未分配利润的增加额。"""
        self.build_basic()
        bs = bs_values(reports.balance_sheet(self.conn, 202601, 202602))
        bs_begin = bs_values(reports.balance_sheet(self.conn, 202601, 202602), 'begin')
        net = is_values(reports.income_statement(self.conn, 202601, 202602))['net_profit']
        self.assertEqual(bs['retained'] - bs_begin['retained'], net)

    def test_previous_year_column(self):
        self.post('2026-03-10', D('cash', Y(800), cf='CF01'), C('rent_short', Y(800)))
        self.post('2027-03-10', D('cash', Y(900), cf='CF01'), C('rent_short', Y(900)))
        rep = reports.income_statement(self.conn, 202703)
        self.assertTrue(rep['has_prev'])
        self.assertEqual(is_values(rep)['revenue'], Y(900))
        self.assertEqual(is_values(rep, 'prev')['revenue'], Y(800))
        self.assertEqual(is_values(rep, 'ytd')['revenue'], Y(900))  # 新年度累计不含上年

    def test_mid_year_opening_ytd(self):
        self.conn.close()
        from app import db
        cid = db.create_company('年中启用', 2026, 7)
        self.conn = db.connect(cid)
        self.addCleanup(self.conn.close)
        self.set_opening([
            ('cash', Y(70000), Y(80000), Y(10000)),
            ('profit', 0, Y(10000), Y(80000)),
            ('rent_short', 0, 0, Y(80000)),            # 损益类只填启用前实际发生额
            ('office', 0, Y(10000), 0),
        ])
        self.post('2026-07-10', D('cash', Y(1000), cf='CF01'), C('rent_short', Y(1000)))
        rep = reports.income_statement(self.conn, 202607)
        self.assertEqual(is_values(rep)['revenue'], Y(1000))           # 本月不含启用前
        self.assertEqual(is_values(rep, 'ytd')['revenue'], Y(81000))   # 本年累计含启用前
        self.assertEqual(is_values(rep, 'ytd')['admin'], Y(10000))
        self.assertEqual(is_values(rep, 'ytd')['net_profit'], Y(71000))


class SmallBusinessReportsTest(LedgerTestCase):
    """《小企业会计准则》格式：本年利润 3103，损益类 5xxx。"""

    STANDARD = 'small'

    def test_codes(self):
        self.assertEqual(self.code('profit'), '3103')
        self.assertTrue(self.code('main_rev').startswith('5'))

    def test_reports(self):
        self.build_basic()
        bs = reports.balance_sheet(self.conn, 202601)
        self.assertEqual(bs['form_no'], '会小企01表')
        self.assertEqual(bs['checks']['end_diff'], 0)
        self.assertEqual(bs['checks']['begin_diff'], 0)
        self.assertEqual(bs_values(bs)['retained'], Y(7050))
        inc = reports.income_statement(self.conn, 202601)
        self.assertEqual(inc['form_no'], '会小企02表')
        v = is_values(inc)
        self.assertEqual(v['revenue'], Y(10000))
        self.assertEqual(v['finance'], -Y(50))
        self.assertEqual(v['net_profit'], Y(7050))

    def test_fixed_assets_lines(self):
        self.post('2026-01-05', D('cash', Y(200000), cf='CF21'), C('capital', Y(200000)))
        vid = self.add_vehicle('京A00001', '2025-12-01', Y(100000))
        aux = self.conn.execute('SELECT aux_id FROM vehicles WHERE id = ?', (vid,)).fetchone()[0]
        self.post('2026-01-06', D('vehicle_fa', Y(100000), aux=aux), C('cash', Y(100000), cf='CF16'))
        closing.run_depreciation(self.conn, 202601)
        v = bs_values(reports.balance_sheet(self.conn, 202601))
        self.assertEqual(v['fa_cost'], Y(100000))
        self.assertEqual(v['accum'], 197917)
        self.assertEqual(v['fa_net'], Y(100000) - 197917)
