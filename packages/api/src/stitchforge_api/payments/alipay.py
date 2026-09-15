from __future__ import annotations

import base64
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
from alipay.aop.api.domain.AlipayTradePagePayModel import AlipayTradePagePayModel
from alipay.aop.api.domain.AlipayTradeQueryModel import AlipayTradeQueryModel
from alipay.aop.api.domain.AlipayTradeWapPayModel import AlipayTradeWapPayModel
from alipay.aop.api.request.AlipayTradePagePayRequest import AlipayTradePagePayRequest
from alipay.aop.api.request.AlipayTradeQueryRequest import AlipayTradeQueryRequest
from alipay.aop.api.request.AlipayTradeWapPayRequest import AlipayTradeWapPayRequest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import PaymentError, PaymentReceipt


def amount_to_minor(value: str) -> int:
    try:
        amount = Decimal(value) * 100
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError("invalid amount")
        return int(amount)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PaymentError("支付宝金额无效。", "PAYMENT_MISMATCH", 400) from error


class AlipayProvider:
    id = "alipay"
    label = "支付宝"
    currencies = frozenset({"CNY"})

    def __init__(
        self,
        *,
        app_id,
        seller_id,
        private_key,
        public_key,
        public_origin,
        modes=("page", "wap"),
        sandbox=False,
    ):
        config = AlipayClientConfig(sandbox_debug=sandbox)
        config.app_id, config.app_private_key, config.alipay_public_key = (
            app_id,
            private_key,
            public_key,
        )
        config.sign_type, config.skip_sign, config.timeout = "RSA2", False, 15
        self.client = DefaultAlipayClient(alipay_client_config=config)
        self.app_id, self.seller_id, self.public_key = app_id, seller_id, public_key
        self.verify_key = serialization.load_pem_public_key(public_key.encode())
        self.public_origin, self.modes, self.test_mode = public_origin, modes, sandbox

    def checkout(self, order: dict, attempt: dict, mode: str, client_ip: str) -> dict:
        if mode == "wap":
            model = AlipayTradeWapPayModel()
            model.product_code = "QUICK_WAP_WAY"
            model.seller_id = self.seller_id
            model.quit_url = self.public_origin + "/#billing"
            request_type = AlipayTradeWapPayRequest
        else:
            model = AlipayTradePagePayModel()
            model.product_code = "FAST_INSTANT_TRADE_PAY"
            request_type = AlipayTradePagePayRequest
        model.out_trade_no = attempt["merchant_trade_no"]
        model.total_amount = f"{order['amount_minor'] // 100}.{order['amount_minor'] % 100:02}"
        model.subject = f"StitchForge 图片生成 {order['credits']} 次"
        model.timeout_express = "30m"
        request = request_type(biz_model=model)
        request.notify_url = self.public_origin + "/api/v1/payments/webhooks/alipay"
        request.return_url = self.public_origin + "/#billing"
        try:
            return {"kind": "redirect", "url": self.client.page_execute(request, http_method="GET")}
        except Exception as error:
            raise PaymentError("暂时无法创建支付宝支付，请从原订单重试。") from error

    @staticmethod
    def receipt(transaction: dict) -> PaymentReceipt | None:
        if transaction.get("trade_status") not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            return None
        try:
            return PaymentReceipt(
                transaction["out_trade_no"],
                transaction["trade_no"],
                amount_to_minor(transaction["total_amount"]),
                "CNY",
            )
        except KeyError as error:
            raise PaymentError("支付宝支付结果字段不完整。", "INVALID_PAYMENT", 400) from error

    def query(self, order: dict, attempt: dict) -> PaymentReceipt | None:
        model = AlipayTradeQueryModel()
        model.out_trade_no = attempt["merchant_trade_no"]
        try:
            # The official SDK verifies the exact signed response before returning its JSON.
            result = json.loads(self.client.execute(AlipayTradeQueryRequest(biz_model=model)))
        except Exception as error:
            raise PaymentError("支付宝查单未成功，请稍后重试。") from error
        if result.get("sub_code") == "ACQ.TRADE_NOT_EXIST":
            return None
        if result.get("code") != "10000":
            raise PaymentError("支付宝查单未成功，请稍后重试。")
        receipt = self.receipt(result)
        if receipt and receipt.merchant_trade_no != attempt["merchant_trade_no"]:
            raise PaymentError("支付宝查单结果与订单不匹配。", "PAYMENT_MISMATCH", 400)
        return receipt

    def notification(self, payload: bytes, headers: dict[str, str]) -> PaymentReceipt | None:
        try:
            pairs = parse_qsl(payload.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
            values = dict(pairs)
            if len(values) != len(pairs) or values.get("sign_type") != "RSA2":
                raise ValueError("duplicate fields or signature algorithm")
            content = "&".join(
                f"{key}={values[key]}"
                for key in sorted(values)
                if key not in {"sign", "sign_type"} and values[key] != ""
            )
            self.verify_key.verify(
                base64.b64decode(values["sign"], validate=True),
                content.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as error:
            raise PaymentError(
                "支付宝通知签名校验失败。", "INVALID_PAYMENT_SIGNATURE", 400
            ) from error
        if values.get("app_id") != self.app_id or values.get("seller_id") != self.seller_id:
            raise PaymentError("支付宝应用或收款账号不匹配。", "PAYMENT_MISMATCH", 400)
        return self.receipt(values)
