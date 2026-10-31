"""
测试公用工具：每个测试用例在临时目录里新建一个 SQLite 账套，并提供录凭证、录期初的简便写法。

金额一律用“分”。为了好读，写成 Y(1300) 表示 1300 元 = 130000 分。
科目既可以写关键字（如 'cash'、'rent_short'），也可以直接写编码（如 '1001'）。
"""
import shutil
import tempfile
import unittest
from unittest import mock

from app import db, vouchers, vehicles


def Y(yuan):
    """元 → 分。"""
    return int(round(yuan * 100))


def D(acct, amount, aux=0, cf='', summary='测试'):
    """借方分录。"""
    return {'account': acct, 'debit': amount, 'credit': 0, 'aux_id': aux, 'cf_item': cf, 'summary': summary}


def C(acct, amount, aux=0, cf='', summary='测试'):
    """贷方分录。"""
    return {'account': acct, 'debit': 0, 'credit': amount, 'aux_id': aux, 'cf_item': cf, 'summary': summary}


class LedgerTestCase(unittest.TestCase):
    """每个测试一个全新的账套。子类可以改 STANDARD / INDUSTRY / START。"""

    STANDARD = 'enterprise'
    INDUSTRY = 'car_rental'
    START = (2026, 1)

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix='caiwu-case-')
        self.addCleanup(shutil.rmtree, tmp, True)
        patcher = mock.patch.object(db, 'DATA_DIR', tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cid = db.create_company('测试公司', self.START[0], self.START[1],
                                     standard=self.STANDARD, industry=self.INDUSTRY)
        self.conn = db.connect(self.cid)
        self.addCleanup(self.conn.close)  # 先关连接，再删临时目录（Windows 上打开的文件删不掉）
        self.km = db.company_profile(self.conn)['keymap']

    # ------------------------------------------------------------ 基础操作

    def code(self, acct):
        """关键字 → 科目编码；本身就是编码时原样返回。"""
        return self.km.get(acct, acct)

    def post(self, date, *lines):
        """保存一张普通凭证，返回凭证 id。"""
        data = {'date': date, 'lines': [dict(ln, account=self.code(ln['account'])) for ln in lines]}
        return vouchers.save_voucher(self.conn, data)

    def set_opening(self, rows):
        """rows: [(科目, 年初余额(借正贷负), 启用前本年借方累计, 启用前本年贷方累计[, 辅助项])]"""
        data = []
        for r in rows:
            data.append({'account_code': self.code(r[0]), 'year_begin': r[1], 'ytd_debit': r[2],
                         'ytd_credit': r[3], 'aux_id': r[4] if len(r) > 4 else 0})
        vouchers.save_opening(self.conn, data)

    def add_aux(self, aux_type, code, name):
        return vouchers.save_aux(self.conn, {'type': aux_type, 'code': code, 'name': name})

    def add_vehicle(self, plate, purchase_date, original, salvage_rate=5, life_months=48, opening_accum=0):
        return vehicles.save_vehicle(self.conn, {
            'plate': plate, 'model': '测试车型', 'purchase_date': purchase_date, 'original': original,
            'salvage_rate': salvage_rate, 'life_months': life_months, 'opening_accum': opening_accum})

    def audit_period(self, p):
        ids = [r[0] for r in self.conn.execute('SELECT id FROM vouchers WHERE period = ?', (p,))]
        vouchers.audit_vouchers(self.conn, ids, True, '审核人')

    # ------------------------------------------------------------ 标准业务

    def build_basic(self):
        """
        2026 年 1、2 月的一组简单业务（全部用库存现金收付）：

        1 月：投资款 100 万；短租收入 1 万（销项税 1300）；办公费 2000；维修费 1000；利息收入 50
              → 1 月利润 = 10000 − 1000 − 2000 + 50 = 7050
        2 月：短租收入 5000（销项税 650）；管理人员工资 3000
              → 2 月利润 = 2000
        """
        self.post('2026-01-05', D('cash', Y(1000000), cf='CF21'), C('capital', Y(1000000)))
        self.post('2026-01-10', D('cash', Y(11300), cf='CF01'), C('rent_short', Y(10000)), C('vat_out', Y(1300)))
        self.post('2026-01-15', D('office', Y(2000)), C('cash', Y(2000), cf='CF07'))
        self.post('2026-01-20', D('veh_repair', Y(1000)), C('cash', Y(1000), cf='CF04'))
        self.post('2026-01-31', D('cash', Y(50), cf='CF03'), C('fin_income', Y(50)))
        self.post('2026-02-10', D('cash', Y(5650), cf='CF01'), C('rent_short', Y(5000)), C('vat_out', Y(650)))
        self.post('2026-02-20', D('admin_wage', Y(3000)), C('cash', Y(3000), cf='CF05'))

    # ------------------------------------------------------------ 断言

    def assertVoucherBalanced(self, vid):
        d, c = self.conn.execute('SELECT SUM(debit), SUM(credit) FROM lines WHERE voucher_id = ?', (vid,)).fetchone()
        self.assertEqual(d, c, '凭证 %s 借贷不平衡' % vid)

    def assertAllVouchersBalanced(self):
        for (vid,) in self.conn.execute('SELECT id FROM vouchers').fetchall():
            self.assertVoucherBalanced(vid)
