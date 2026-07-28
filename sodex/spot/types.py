"""Spark spot-engine request types."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sodex.common.enums import OrderSide, OrderType, TimeInForce
from sodex.common.types import BuilderParams


class BatchNewOrderItem:
    """A single order entry within a BatchNewOrderRequest."""

    def __init__(
        self,
        symbol_id: int,
        cl_ord_id: str,
        side: OrderSide,
        type: OrderType,
        time_in_force: TimeInForce,
        price: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        funds: Optional[Decimal] = None,
    ) -> None:
        self.symbol_id = symbol_id
        self.cl_ord_id = cl_ord_id
        self.side = side
        self.type = type
        self.time_in_force = time_in_force
        self.price = price
        self.quantity = quantity
        self.funds = funds

    def to_dict(self) -> dict:
        # Field order mirrors the Go struct definition.
        d: dict = {
            "symbolID": self.symbol_id,
            "clOrdID": self.cl_ord_id,
            "side": int(self.side),
            "type": int(self.type),
            "timeInForce": int(self.time_in_force),
        }
        if self.price is not None:
            d["price"] = str(self.price)
        if self.quantity is not None:
            d["quantity"] = str(self.quantity)
        if self.funds is not None:
            d["funds"] = str(self.funds)
        return d


class BatchNewOrderRequest:
    """Batch new-order placement request. Exclusive to the Spark spot engine."""

    def __init__(
        self,
        account_id: int,
        orders: list[BatchNewOrderItem],
        builder: Optional[BuilderParams] = None,
    ) -> None:
        self.account_id = account_id
        self.orders = orders
        self.builder = builder

    def action_name(self) -> str:
        return "batchNewOrder"

    def to_json_payload(self) -> dict:
        payload = {
            "accountID": self.account_id,
            "orders": [o.to_dict() for o in self.orders],
        }
        if self.builder is not None:
            payload["builder"] = self.builder.to_dict()
        return payload


class BatchCancelOrderItem:
    """A single cancellation entry within a BatchCancelOrderRequest."""

    def __init__(
        self,
        symbol_id: int,
        cl_ord_id: str,
        order_id: Optional[int] = None,
        orig_cl_ord_id: Optional[str] = None,
    ) -> None:
        self.symbol_id = symbol_id
        self.cl_ord_id = cl_ord_id
        self.order_id = order_id
        self.orig_cl_ord_id = orig_cl_ord_id

    def to_dict(self) -> dict:
        d: dict = {
            "symbolID": self.symbol_id,
            "clOrdID": self.cl_ord_id,
        }
        if self.order_id is not None:
            d["orderID"] = self.order_id
        if self.orig_cl_ord_id is not None:
            d["origClOrdID"] = self.orig_cl_ord_id
        return d


class BatchCancelOrderRequest:
    """Batch order-cancellation request. Exclusive to the Spark spot engine."""

    def __init__(self, account_id: int, cancels: list[BatchCancelOrderItem]) -> None:
        self.account_id = account_id
        self.cancels = cancels

    def action_name(self) -> str:
        return "batchCancelOrder"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "cancels": [c.to_dict() for c in self.cancels],
        }
