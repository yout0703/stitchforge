from __future__ import annotations

import base64
import json
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .base import PaymentError, PaymentReceipt


class WechatProvider:
    id = "wechat"
    label = "微信支付"
    currencies = frozenset({"CNY"})

    def __init__(
        self,
        *,
        app_id,
        mch_id,
        serial,
        private_key,
        public_keys,
        public_key_id,
        api_v3_key,
        public_origin,
        h5_enabled=False,
    ):
        self.app_id, self.mch_id, self.serial = app_id, mch_id, serial
        self.private_key, self.public_keys = private_key, public_keys
        self.public_key_id, self.api_v3_key = public_key_id, api_v3_key
        self.public_origin = public_origin
        self.modes = ("native", "h5") if h5_enabled else ("native",)

    def verify(self, payload: bytes, headers: dict[str, str]) -> None:
        try:
            timestamp, nonce = headers["wechatpay-timestamp"], headers["wechatpay-nonce"]
            if abs(time.time() - int(timestamp)) > 300:
                raise ValueError("stale response")
            key = self.public_keys[headers["wechatpay-serial"]]
            signature = base64.b64decode(headers["wechatpay-signature"], validate=True)
            key.verify(
                signature,
                f"{timestamp}\n{nonce}\n".encode() + payload + b"\n",
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (KeyError, ValueError, InvalidSignature) as error:
            raise PaymentError(
                "微信支付签名校验失败。", "INVALID_PAYMENT_SIGNATURE", 400
            ) from error

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body else b""
        )
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode() + payload + b"\n"
        signature = base64.b64encode(
            self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        authorization = (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mch_id}",nonce_str="{nonce}",'
            f'timestamp="{timestamp}",serial_no="{self.serial}",signature="{signature}"'
        )
        try:
            response = httpx.request(
                method,
                "https://api.mch.weixin.qq.com" + path,
                content=payload,
                headers={
                    "Authorization": authorization,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Wechatpay-Serial": self.public_key_id,
                },
                timeout=15,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise PaymentError("暂时无法连接微信支付，请从原订单重试。") from error
        # Verify successful responses before trusting QR URLs, amounts or payment status.
        if response.is_success:
            self.verify(response.content, dict(response.headers))
            return response.json()
        raise PaymentError("微信支付请求未成功，请检查商户配置或稍后从原订单重试。")

    def checkout(self, order: dict, attempt: dict, mode: str, client_ip: str) -> dict:
        payload = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": f"StitchForge 图片生成 {order['credits']} 次",
            "out_trade_no": attempt["merchant_trade_no"],
            "notify_url": self.public_origin + "/api/v1/payments/webhooks/wechat",
            "time_expire": datetime.fromtimestamp(
                (attempt["created_at_ms"] + 1800_000) / 1000, UTC
            ).isoformat(),
            "amount": {"total": order["amount_minor"], "currency": order["currency"]},
        }
        if mode == "h5":
            payload["scene_info"] = {"payer_client_ip": client_ip, "h5_info": {"type": "Wap"}}
        result = self.request("POST", f"/v3/pay/transactions/{mode}", payload)
        if mode == "native":
            code_url = result.get("code_url", "")
            if not code_url.startswith("weixin://"):
                raise PaymentError("微信支付返回了无效二维码。")
            return {"kind": "qr", "code_url": code_url}
        url = result.get("h5_url", "")
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "wx.tenpay.com":
            raise PaymentError("微信支付返回了无效跳转地址。")
        return {
            "kind": "redirect",
            "url": url + "&" + urlencode({"redirect_url": self.public_origin + "/#billing"}),
        }

    def receipt(self, transaction: dict) -> PaymentReceipt | None:
        if transaction.get("appid") != self.app_id or transaction.get("mchid") != self.mch_id:
            raise PaymentError("微信支付商户或应用不匹配。", "PAYMENT_MISMATCH", 400)
        if transaction.get("trade_state") != "SUCCESS":
            return None
        try:
            return PaymentReceipt(
                transaction["out_trade_no"],
                transaction["transaction_id"],
                transaction["amount"]["total"],
                transaction["amount"]["currency"],
            )
        except KeyError as error:
            raise PaymentError("微信支付结果字段不完整。", "INVALID_PAYMENT", 400) from error

    def query(self, order: dict, attempt: dict) -> PaymentReceipt | None:
        result = self.request(
            "GET",
            "/v3/pay/transactions/out-trade-no/"
            + quote(attempt["merchant_trade_no"], safe="")
            + "?"
            + urlencode({"mchid": self.mch_id}),
        )
        receipt = self.receipt(result)
        if receipt and receipt.merchant_trade_no != attempt["merchant_trade_no"]:
            raise PaymentError("微信查单结果与订单不匹配。", "PAYMENT_MISMATCH", 400)
        return receipt

    def notification(self, payload: bytes, headers: dict[str, str]) -> PaymentReceipt | None:
        self.verify(payload, headers)
        try:
            event = json.loads(payload)
            if event["event_type"] != "TRANSACTION.SUCCESS":
                return None
            resource = event["resource"]
            if resource["algorithm"] != "AEAD_AES_256_GCM":
                raise ValueError("invalid algorithm")
            plaintext = AESGCM(self.api_v3_key).decrypt(
                resource["nonce"].encode(),
                base64.b64decode(resource["ciphertext"], validate=True),
                resource.get("associated_data", "").encode(),
            )
            return self.receipt(json.loads(plaintext))
        except (KeyError, ValueError, TypeError, InvalidTag) as error:
            raise PaymentError("微信支付通知内容校验失败。", "INVALID_PAYMENT", 400) from error
