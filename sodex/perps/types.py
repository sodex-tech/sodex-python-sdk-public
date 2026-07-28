"""Bolt perpetuals-engine request types."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sodex.common.enums import (
    MarginMode,
    OrderModifier,
    OrderSide,
    OrderType,
    PositionSide,
    StopType,
    TimeInForce,
    TriggerType,
)
from sodex.common.types import BuilderParams


class RawOrder:
    """A single order entry within a perps NewOrderRequest."""

    def __init__(
        self,
        cl_ord_id: str,
        modifier: OrderModifier,
        side: OrderSide,
        type: OrderType,
        time_in_force: TimeInForce,
        price: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        funds: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        stop_type: Optional[StopType] = None,
        trigger_type: Optional[TriggerType] = None,
        reduce_only: bool = False,
        position_side: PositionSide = PositionSide.BOTH,
    ) -> None:
        self.cl_ord_id = cl_ord_id
        self.modifier = modifier
        self.side = side
        self.type = type
        self.time_in_force = time_in_force
        self.price = price
        self.quantity = quantity
        self.funds = funds
        self.stop_price = stop_price
        self.stop_type = stop_type
        self.trigger_type = trigger_type
        self.reduce_only = reduce_only
        self.position_side = position_side

    def to_dict(self) -> dict:
        # Non-optional fields first, in Go struct definition order.
        d: dict = {
            "clOrdID": self.cl_ord_id,
            "modifier": int(self.modifier),
            "side": int(self.side),
            "type": int(self.type),
            "timeInForce": int(self.time_in_force),
        }
        # Optional fields in struct definition order (omitempty).
        # Note: decimal fields must be strings for Go JSON unmarshalling.
        if self.price is not None:
            d["price"] = str(self.price)
        if self.quantity is not None:
            d["quantity"] = str(self.quantity)
        if self.funds is not None:
            d["funds"] = str(self.funds)
        if self.stop_price is not None:
            d["stopPrice"] = str(self.stop_price)
        if self.stop_type is not None:
            d["stopType"] = int(self.stop_type)
        if self.trigger_type is not None:
            d["triggerType"] = int(self.trigger_type)
        # Non-optional fields that follow the optional block in the Go struct.
        d["reduceOnly"] = self.reduce_only
        d["positionSide"] = int(self.position_side)
        return d


class NewOrderRequest:
    """Perpetuals order placement request. Exclusive to the Bolt engine."""

    def __init__(
        self,
        account_id: int,
        symbol_id: int,
        orders: list[RawOrder],
        builder: Optional[BuilderParams] = None,
    ) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.orders = orders
        self.builder = builder

    def action_name(self) -> str:
        return "newOrder"

    def to_json_payload(self) -> dict:
        payload = {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
            "orders": [o.to_dict() for o in self.orders],
        }
        if self.builder is not None:
            payload["builder"] = self.builder.to_dict()
        return payload


class CancelOrder:
    """A single order to cancel within a perps CancelOrderRequest."""

    def __init__(
        self,
        symbol_id: int,
        order_id: Optional[int] = None,
        cl_ord_id: Optional[str] = None,
    ) -> None:
        self.symbol_id = symbol_id
        self.order_id = order_id
        self.cl_ord_id = cl_ord_id

    def to_dict(self) -> dict:
        d: dict = {"symbolID": self.symbol_id}
        if self.order_id is not None:
            d["orderID"] = self.order_id
        if self.cl_ord_id is not None:
            d["clOrdID"] = self.cl_ord_id
        return d


class CancelOrderRequest:
    """Batch order-cancellation request. Exclusive to the Bolt engine."""

    def __init__(self, account_id: int, cancels: list[CancelOrder]) -> None:
        self.account_id = account_id
        self.cancels = cancels

    def action_name(self) -> str:
        return "cancelOrder"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "cancels": [c.to_dict() for c in self.cancels],
        }


class UpdateLeverageRequest:
    """Position-leverage update request. Exclusive to the Bolt engine."""

    def __init__(
        self,
        account_id: int,
        symbol_id: int,
        leverage: int,
        margin_mode: MarginMode,
    ) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.leverage = leverage
        self.margin_mode = margin_mode

    def action_name(self) -> str:
        return "updateLeverage"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
            "leverage": self.leverage,
            "marginMode": int(self.margin_mode),
        }


class UpdateMarginRequest:
    """Position-margin adjustment request. Exclusive to the Bolt engine."""

    def __init__(self, account_id: int, symbol_id: int, amount: Decimal) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.amount = amount

    def action_name(self) -> str:
        return "updateMargin"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
            "amount": str(self.amount),
        }


class UpdateCollateralRequest:
    """Add or remove non-USDC cross-margin collateral (testnet only)."""

    def __init__(self, account_id: int, coin_id: int, amount: Decimal) -> None:
        self.account_id = account_id
        self.coin_id = coin_id
        self.amount = amount

    def action_name(self) -> str:
        return "updateCollateral"

    def to_json_payload(self) -> dict:
        return {
            "accountID": self.account_id,
            "coinID": self.coin_id,
            "amount": str(self.amount),
        }


class ModifyOrderRequest:
    """Single-order modify request. Exclusive to the Bolt perpetuals engine.

    Identify the target order via ``order_id`` or ``orig_cl_ord_id`` — exactly
    one must be set. At least one of ``price`` / ``quantity`` / ``stop_price``
    must be non-None for the modification to take effect.
    """

    def __init__(
        self,
        account_id: int,
        symbol_id: int,
        order_id: Optional[int] = None,
        cl_ord_id: Optional[str] = None,
        price: Optional[Decimal] = None,
        quantity: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
    ) -> None:
        self.account_id = account_id
        self.symbol_id = symbol_id
        self.order_id = order_id
        self.cl_ord_id = cl_ord_id
        self.price = price
        self.quantity = quantity
        self.stop_price = stop_price

    def action_name(self) -> str:
        return "modifyOrder"

    def to_json_payload(self) -> dict:
        # Field order mirrors the Go struct declaration (ModifyOrderRequest).
        d: dict = {
            "accountID": self.account_id,
            "symbolID": self.symbol_id,
        }
        if self.order_id is not None:
            d["orderID"] = self.order_id
        if self.cl_ord_id is not None:
            d["clOrdID"] = self.cl_ord_id
        if self.price is not None:
            d["price"] = str(self.price)
        if self.quantity is not None:
            d["quantity"] = str(self.quantity)
        if self.stop_price is not None:
            d["stopPrice"] = str(self.stop_price)
        return d
