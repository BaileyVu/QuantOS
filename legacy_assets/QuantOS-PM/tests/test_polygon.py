import unittest
from quantos_pm.adapters.polygon import PublicPolygon, inspect_receipt, TRANSFER

W='0x'+'1'*40
TX='0x'+'a'*64
BLOCK='0x'+'b'*64
ROW=dict(transaction_hash=TX,timestamp=100,type='DEPOSIT',usdc_size='5')


def receipt():
    return dict(transactionHash=TX,blockHash=BLOCK,blockNumber='0x1',status='0x1',
                logs=[dict(address='0x'+'2'*40,topics=[TRANSFER,'0x'+'0'*64,'0x'+'0'*24+W[2:]],data='0x'+f'{5000000:064x}')])


class PolygonTests(unittest.TestCase):
    def test_only_read_methods_are_allowed(self):
        rpc=PublicPolygon()
        for method,args in [('eth_sendRawTransaction',['0xdead']),('eth_sendTransaction',[{}]),
                            ('personal_sign',['x']),('eth_getBlockByNumber',['latest',True]),
                            ('eth_getTransactionReceipt',['bad-hash'])]:
            with self.assertRaises(ValueError):rpc.read(method,args)

    def test_receipt_wallet_and_raw_units(self):
        r=inspect_receipt(W,ROW,receipt(),dict(hash=BLOCK,timestamp='0x64'))
        self.assertTrue(r['verified'])
        self.assertEqual(r['erc20_net_raw_units'],{'0x'+'2'*40:'5000000'})
        self.assertIn('no assumed decimals',r['interpretation'])

    def test_mismatch_missing_failed_receipts_not_verified(self):
        self.assertFalse(inspect_receipt(W,ROW,None,None)['verified'])
        failed=receipt();failed['status']='0x0'
        self.assertFalse(inspect_receipt(W,ROW,failed,dict(hash=BLOCK,timestamp='0x64'))['verified'])
        self.assertFalse(inspect_receipt(W,ROW,receipt(),dict(hash=BLOCK,timestamp='0x65'))['verified'])
        with self.assertRaises(ValueError):inspect_receipt(W,ROW,receipt(),dict(hash='wrong',timestamp='0x64'))

    def test_other_wallet_does_not_count(self):
        r=inspect_receipt('0x'+'3'*40,ROW,receipt(),dict(hash=BLOCK,timestamp='0x64'))
        self.assertFalse(r['verified'])
