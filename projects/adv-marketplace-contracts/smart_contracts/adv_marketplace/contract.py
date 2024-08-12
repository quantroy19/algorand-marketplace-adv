from algopy import Account, ARC4Contract, Asset, Global, Txn, UInt64, arc4, gtxn, itxn, op, subroutine
from algopy.arc4 import abimethod

BOX_LENGTH_FOR_SALE = 64
MINIMUM_FREE_FOR_SALE = 2500 + 400 * BOX_LENGTH_FOR_SALE


class AdvMarketplace(ARC4Contract):
    # apt-into-access
    # allow-access
    # deposit
    # first-deposit
    # withdraw
    # buy

    @abimethod
    def allow_access(self, pay: gtxn.PaymentTransaction, asset: Asset) -> None:
        assert not Global.current_application_address.is_opted_in(asset)

        assert pay.receiver == Global.current_application_address
        assert pay.amount == Global.asset_opt_in_min_balance

        itxn.AssetTransfer(
            xfer_asset=asset, asset_receiver=Global.current_application_address, asset_amount=0, fee=0
        ).submit()

    # Box =  Key -> values
    # asset_id (unitary_price) -> amount -> Box -> Bytes
    @abimethod
    def first_deposit(
        self,
        xfer: gtxn.AssetTransferTransaction,
        nonce: arc4.UInt64,
        unitary_price: arc4.UInt64,
        pay: gtxn.PaymentTransaction,
    ) -> None:
        assert pay.sender == Txn.sender
        assert pay.receiver == Global.current_application_address
        assert pay.amount == MINIMUM_FREE_FOR_SALE
        # decimal Algorand = 10^6
        # fee create box = 0.0025
        # fee per byte = 0.0004 * 64 bytes

        # address + asset_id + nonce
        box_key = Txn.sender.bytes + op.itob(xfer.xfer_asset.id) + nonce.bytes

        _length, exists = op.Box.length(box_key)

        assert not exists
        assert xfer.sender == Txn.sender
        assert xfer.asset_receiver == Global.current_application_address
        assert xfer.asset_amount > 0

        assert op.Box.create(box_key, BOX_LENGTH_FOR_SALE)  # create a box with 64 bytes

        op.Box.replace(box_key, 0, op.itob(xfer.asset_amount) + unitary_price.bytes)  # store unitary_price + amount

    # global state
    @abimethod
    def deposit(self, xfer: gtxn.AssetTransferTransaction, nonce: arc4.UInt64) -> None:
        assert xfer.sender == Txn.sender
        assert xfer.asset_receiver == Global.current_application_address
        assert xfer.asset_amount > 0

        box_key = Txn.sender.bytes + op.itob(xfer.xfer_asset.id) + nonce.bytes
        _length, exists = op.Box.length(box_key)

        assert exists
        current_amount = op.itob(op.Box.extract(box_key, 0, 8))

        op.Box.replace(box_key, 0, op.itob(xfer.asset_amount + current_amount))  # ghi de len gia tri cu, update amount

    @abimethod
    def set_price(self, asset_id: Asset, nonce: arc4.UInt64, unitary_price: arc4.UInt64) -> None:
        box_key = Txn.sender.bytes + op.itob(asset_id) + nonce.bytes

        op.Box.replace(box_key, 8, unitary_price.bytes)

    @abimethod
    def buy(
        self,
        buyer: arc4.Address,
        asset: Asset,
        nonce: arc4.UInt64,
        buyer_txn: gtxn.PaymentTransaction,
        quantity: UInt64,
    ) -> None:
        box_key = Txn.sender.bytes + op.itob(asset) + nonce.bytes
        current_unitary_price = op.btoi(op.Box.extract(box_key, 8, 8))

        amount_to_paid = self._quantity_price(quantity, current_unitary_price, asset.decimals)

        assert buyer_txn.sender == Txn.sender
        assert buyer_txn.receiver == buyer
        assert buyer_txn.amount == amount_to_paid

        itxn.AssetTransfer(xfer_asset=asset, asset_receiver=Txn.sender, asset_amount=quantity, fee=0).submit()
        # new token = currentToken - tokenTransfer

    @subroutine
    def _quantity_price(self, quantity: UInt64, price: UInt64, asset_decimals: UInt64) -> UInt64:
        amount_scaled_hight, amount_scaled_low = op.mulw(price, quantity)
        scaling_high, scaling_low = op.expw(10, asset_decimals)  # 10^asset_decimals
        _a, amount_to_paid, remainder_high, remainder_low = op.divmodw(
            amount_scaled_hight, amount_scaled_low, scaling_high, scaling_low
        )

        assert not _a
        return amount_to_paid

    @abimethod
    def bid(
        self,
        pay: gtxn.PaymentTransaction,
        asset: Asset,
        nonce: arc4.UInt64,
        owner: arc4.Address,
        unitary_price: arc4.UInt64,
        quantity: arc4.UInt64,
    ) -> None:
        assert Txn.sender.is_opted_in(asset)
        box_key = owner.bytes + op.itob(asset.id) + nonce.bytes

        # 0 -> 16 (amount, unitary_price)
        current_bidder = Account(op.Box.extract(box_key, 16, 32))  # address -> 32 bytes

        if current_bidder != Global.zero_address:
            current_bid_quantity = op.btoi(op.Box.extract(box_key, 48, 8))
            current_bid_unitary_price = op.btoi(op.Box.extract(box_key, 56, 8))

            assert unitary_price > current_bid_unitary_price

            current_bid_quantity = self._quantity_price(current_bid_quantity, current_bid_unitary_price, asset.decimals)
            itxn.Payment(receiver=current_bidder, amount=current_bid_quantity, fee=0).submit()

            amount_to_paid = self._quantity_price(quantity.native, unitary_price.native, asset.decimals)

            assert pay.sender == Txn.sender
            assert pay.receiver == Global.current_application_address
            assert pay.amount == amount_to_paid

            op.Box.replace(box_key, 16, Txn.sender.bytes + quantity.bytes + unitary_price.bytes + unitary_price.bytes)

    @abimethod
    def accept_bid(self, asset: Asset, nonce: arc4.UInt64) -> None:
        box_key = Txn.sender.bytes + op.itob(asset.id) + nonce.bytes

        winner = Account(op.Box.extract(box_key, 16, 32))  # address -> 32 bytes

        highest_bid_quantity = op.btoi(op.Box.extract(box_key, 48, 8))
        highest_unitary_price = op.btoi(op.Box.extract(box_key, 56, 8))

        current_quantity = op.btoi(op.Box.extract(box_key, 0, 8))
        current_unitary_price = op.btoi(op.Box.extract(box_key, 8, 8))

        assert current_unitary_price > highest_unitary_price

        # min_quantity
        min_quantity = (
            current_quantity if current_quantity < highest_bid_quantity else highest_bid_quantity
        )  # 10_000 -> quantity -> 9000
        highest_bid_quantity = self._quantity_price(min_quantity, highest_unitary_price, asset.decimals)

        op.Box.replace(box_key, 0, op.itob(current_quantity - min_quantity))  # 9000 - 9000
        op.Box.replace(box_key, 48, op.itob(highest_bid_quantity - min_quantity))  # 10_000 - 9000 = 1000

        itxn.Payment(receiver=Txn.sender, amount=highest_bid_quantity, fee=0).submit()

    @abimethod
    def withdraw(self, asset: Asset, nonce: arc4.UInt64) -> None:
        box_key = Txn.sender.bytes + op.itob(asset.id) + nonce.bytes
        current_bidder = Account(op.Box.extract(box_key, 16, 32))
        current_deposit = op.btoi(op.Box.extract(box_key, 0, 8))

        if current_bidder != Global.zero_address:
            current_bid_remainder = self._quantity_price(
                op.btoi(op.Box.extract(box_key, 48, 8)), op.btoi(op.Box.extract(box_key, 56, 8)), asset.decimals
            )

            itxn.Payment(receiver=current_bidder, amount=current_bid_remainder, fee=0).submit()

        _deleted = op.Box.delete(box_key)
        itxn.Payment(receiver=Txn.sender, amount=MINIMUM_FREE_FOR_SALE, fee=0).submit()
        itxn.AssetTransfer(xfer_asset=asset, asset_receiver=Txn.sender, asset_amount=current_deposit).submit()
