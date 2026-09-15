"""Explicitly enabled provider registry; credentials stay in server-side secret files."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .alipay import AlipayProvider
from .base import PaymentProvider
from .stripe import StripeProvider
from .wechat import WechatProvider


def required(name: str) -> str:
    value = os.environ.get("STITCHFORGE_" + name, "").strip()
    if not value:
        raise ValueError(f"支付配置缺少 STITCHFORGE_{name}")
    return value


def secret(name: str) -> str:
    try:
        return Path(required(name)).read_text().strip()
    except OSError as error:
        raise ValueError(f"无法读取 STITCHFORGE_{name} 指定的密钥文件") from error


def private_key(value: str):
    key = serialization.load_pem_private_key(value.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("支付私钥必须为至少 2048 位 RSA PEM")
    return key


def public_key(value: str):
    key = serialization.load_pem_public_key(value.encode())
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError("支付公钥必须为至少 2048 位 RSA PEM")
    return key


def load_providers(public_origin: str) -> dict[str, PaymentProvider]:
    providers = {}
    enabled = {
        name
        for name in ("WECHAT", "ALIPAY", "STRIPE")
        if os.environ.get(f"STITCHFORGE_{name}_ENABLED") == "1"
    }
    if enabled and not public_origin.startswith("https://"):
        raise ValueError("启用在线支付前必须配置 STITCHFORGE_PUBLIC_ORIGIN 的 HTTPS 来源")
    if "WECHAT" in enabled:
        key_paths = json.loads(required("WECHAT_PUBLIC_KEYS_JSON"))
        if not isinstance(key_paths, dict) or not key_paths:
            raise ValueError("微信支付公钥配置必须是 ID 到 PEM 文件路径的映射")
        keys = {key_id: public_key(Path(path).read_text()) for key_id, path in key_paths.items()}
        active_id = required("WECHAT_PUBLIC_KEY_ID")
        if active_id not in keys:
            raise ValueError("微信支付当前公钥 ID 必须存在于公钥映射")
        api_key = secret("WECHAT_API_V3_KEY_FILE").encode()
        if len(api_key) != 32:
            raise ValueError("微信支付 APIv3 密钥必须为 32 字节")
        providers["wechat"] = WechatProvider(
            app_id=required("WECHAT_APP_ID"),
            mch_id=required("WECHAT_MCH_ID"),
            serial=required("WECHAT_CERT_SERIAL"),
            private_key=private_key(secret("WECHAT_PRIVATE_KEY_FILE")),
            public_keys=keys,
            public_key_id=active_id,
            api_v3_key=api_key,
            public_origin=public_origin,
            h5_enabled=os.environ.get("STITCHFORGE_WECHAT_H5_ENABLED") == "1",
        )
    if "ALIPAY" in enabled:
        modes = tuple(required("ALIPAY_MODES").split(","))
        if not modes or any(mode not in {"page", "wap"} for mode in modes):
            raise ValueError("支付宝模式仅支持 page,wap，需与已开通产品匹配")
        private = (
            private_key(secret("ALIPAY_PRIVATE_KEY_FILE"))
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
            .decode()
        )
        public = (
            public_key(secret("ALIPAY_PUBLIC_KEY_FILE"))
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        providers["alipay"] = AlipayProvider(
            app_id=required("ALIPAY_APP_ID"),
            seller_id=required("ALIPAY_SELLER_ID"),
            private_key=private,
            public_key=public,
            public_origin=public_origin,
            modes=modes,
            sandbox=os.environ.get("STITCHFORGE_ALIPAY_SANDBOX") == "1",
        )
    if "STRIPE" in enabled:
        providers["stripe"] = StripeProvider(
            secret_key=secret("STRIPE_SECRET_KEY_FILE"),
            webhook_secret=secret("STRIPE_WEBHOOK_SECRET_FILE"),
            public_origin=public_origin,
        )
    return providers
