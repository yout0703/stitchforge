# 多渠道支付接入

目前没有真实商户账号，所有渠道默认关闭。代码已实现微信支付、支付宝与 Stripe 的支付适配器；本地验签、请求构造、查单、重复入账及界面流程已测试，真实商户联调和收款验收尚未进行。

## 通用接口与账本

`payments/base.py` 定义 `PaymentProvider`：声明渠道 ID、显示名称、支持的币种与场景，并实现 `checkout`（支付入口）、`query`（主动查单）、`notification`（通知验签与解析）。各渠道只返回统一的 `PaymentReceipt`：商户订单号、渠道交易号、金额和币种。

共用服务把经过验证的结果交给 `PaymentStore.settle`。它先确认商户订单与渠道的绑定，再执行 `AccountStore.settle_order` 的原子入账；图纸生成不依赖任何渠道代码。

- 套餐在服务端配置，金额按最小货币单位保存。支持 CNY、USD、EUR、GBP、HKD、SGD、AUD、CAD，均按两位小数处理；不自动换汇。新币种需要同时确认最小单位和渠道支持。
- 订单第一次发起支付时固定渠道与场景，保存独立的 32 字符商户订单号。重试沿用原订单号；不能在同一笔订单上同时切换两个渠道收款。
- 支付入口缓存到数据库。微信原生支付返回二维码，H5／支付宝／Stripe 返回托管支付页面；前端不接收商户密钥。
- 回调和主动查单都验证金额、币种和订单；微信额外检查 appid／mchid，支付宝通知检查 app_id／seller_id，Stripe 检查正式／测试环境、支付模式与订单 metadata。渠道交易号加渠道前缀后全局唯一，重复通知仅入账一次。
- 浏览器返回、二维码展示以及“我已支付”按钮本身不会增加次数；按钮请求服务端向平台查单。
- 支付入口在本应用中 30 分钟后停止继续使用。Stripe 托管会话设置为 60 分钟以满足接口最短创建有效期；最终到账仍依有效签名和查单结果，迟到通知不会丢失。入口过期后应先查单再创建新订单。

## 渠道与场景

| 适配器 | 电脑端 | 手机端 | 配置要求 |
|---|---|---|---|
| 微信支付 | Native 扫码 | H5 跳转；未开通 H5 则仍为扫码 | 直连商户、与商户绑定的 appid、APIv3 密钥、商户签名私钥及证书序列号、微信支付公钥 |
| 支付宝 | 电脑网站支付的官方收银台 | 手机网站支付 | 已开通对应支付产品的应用、商户身份、应用私钥、支付宝公钥 |
| Stripe | Hosted Checkout | Hosted Checkout | 可用商户账户的 Secret Key 和 Webhook Secret；具体支付方式由账户和 Dashboard 配置决定 |

微信 H5 面向微信外浏览器；在微信内置浏览器打开时会提示改用系统浏览器。公众号内 JSAPI 和小程序支付需要 openid／OAuth 等额外流程，本版未加入。支付宝使用公钥模式，暂未实现证书模式。Stripe 没有 Connect、多商户路由和自动税费／优惠逻辑，确保套餐金额与实付核验一致。

## 配置方法

完整变量模板在根目录 `.env.example`。`stitchforge-api` 自动读取启动目录的 `.env`，已有进程环境变量优先；部署也可直接注入环境变量。设置 `STITCHFORGE_PUBLIC_ORIGIN` 为正式 HTTPS 来源，配置 `STITCHFORGE_CREDIT_PACKS` 套餐；密钥从环境变量指定的本地文件读取，不提交仓库、不发到聊天、不写到网页。

仅开通和配置好某个渠道后，将其 `STITCHFORGE_<渠道>_ENABLED` 设置为 `1`。开关为 0 时该渠道不会读取密钥或出现在可用支付列表。开关为 1 而必要参数缺失时，应用会拒绝启动，避免出现看似可支付的半配置渠道。

### 微信支付

- `WECHAT_APP_ID`、`WECHAT_MCH_ID`：直连商户的已绑定应用与商户号。
- `WECHAT_CERT_SERIAL`、`WECHAT_PRIVATE_KEY_FILE`：商户签名证书序列号和对应私钥。
- `WECHAT_API_V3_KEY_FILE`：内容为 32 字节 APIv3 密钥的文件。
- `WECHAT_PUBLIC_KEY_ID`：当前微信支付公钥 ID。
- `WECHAT_PUBLIC_KEYS_JSON`：公钥 ID 到本地 PEM 公钥路径的 JSON 映射；可同时保留明确配置的旧公钥以处理切换。
- `WECHAT_H5_ENABLED=1`：仅在 H5 产品和域名已经开通时启用；默认仅 Native。

以上均需加 `STITCHFORGE_` 前缀。当前实现使用微信支付公钥模式，商户需完成该模式配置；未知公钥 ID 的应答或通知会被拒绝，不会临时信任网络上下载的任意证书。所有 API 成功响应及通知均按原始报文字节验证 RSA-SHA256；通知验证后使用 APIv3 密钥进行 AES-256-GCM 解密。

### 支付宝

配置 `STITCHFORGE_ALIPAY_APP_ID`、`STITCHFORGE_ALIPAY_SELLER_ID`、应用私钥文件及支付宝公钥文件，并以 `STITCHFORGE_ALIPAY_MODES=page,wap` 声明实际开通的产品（可以只配置一个）。

官方 Python SDK 使用进程本地时间生成请求时间戳；部署进程应设置 `TZ=Asia/Shanghai`。设置 `STITCHFORGE_ALIPAY_SANDBOX=1` 使用沙箱网关，网页会标明“测试环境”。同步查单由 SDK 验证原始响应签名；异步表单通知由适配器严格验证 RSA2，拒绝重复参数、错误卖家和错误应用。

### Stripe

配置 `STITCHFORGE_STRIPE_SECRET_KEY_FILE` 和 `STITCHFORGE_STRIPE_WEBHOOK_SECRET_FILE`。Checkout 使用按订单稳定的幂等键，回调必须带有效 Stripe-Signature。

订阅 `checkout.session.completed` 和 `checkout.session.async_payment_succeeded`。只有 `payment_status=paid` 才入账，延迟支付完成前保持待付款。测试 Key 对应的渠道会在界面标记为测试环境；不能把测试 Key 与正式回调混用。实际账户可使用的币种和支付方式需在开通时核对。

## 回调与客户端接口

| 路径 | 作用 |
|---|---|
| `POST /api/v1/payments/webhooks/wechat` | 微信通知，验签和解密后入账，成功返回 204 |
| `POST /api/v1/payments/webhooks/alipay` | 支付宝表单通知，成功返回纯文本 `success` |
| `POST /api/v1/payments/webhooks/stripe` | Stripe 原始 JSON 通知，成功返回 204 |
| `POST /api/v1/billing/orders/{id}/checkout` | 登录用户请求支付入口，提交 `provider` 和 `device`（desktop／mobile） |
| `GET /api/v1/billing/orders/{id}` | 登录用户读取自己的订单状态 |
| `POST /api/v1/billing/orders/{id}/sync` | 服务端主动查单，平台确认已支付后入账 |
| `GET /api/v1/billing/orders/{id}/qr` | 登录用户读取自己的未过期支付二维码 |

只有三个精确匹配的通知路径豁免浏览器 Cookie／CSRF 要求，由渠道签名验证保护。其他写接口仍要求登录、资源归属及安全请求头。通知限 256 KiB，读取时保留原始字节；反向代理不能改变请求体或删除验签头。

支付入口和查单有独立的账号限流。手机支付返回 `/#billing` 后会打开账号面板，尝试同步最近待付款订单；二维码面板每 3 秒查询本地订单状态，手工“查询到账”才发起平台查单。

## 增加其他渠道

新增一个实现 `PaymentProvider` 的适配器，在 `load_providers` 中注册，并为它添加明确的通知路径允许项；支付 UI 从服务端渠道元数据渲染，不需要改次数账本。托管页面类型默认使用 hosted 场景；特殊客户端授权场景需要单独实现，不能伪装成普通跳转。

## 验证范围与上线前待办

`tests/test_payments.py` 使用生成的测试 RSA 密钥、AES-GCM 加密报文、HMAC 签名和被拦截的网络响应，测试渠道请求签名、回调验签、商户与金额不匹配、未付款／延迟付款、重复通知、订单归属及幂等查单。`tools/check_web_payments.py` 测试前端币种筛选、渠道选择、二维码、支付跳转和查单后余额更新。它们均不发起真实交易。

还需在获得商户账号后完成实际产品开通、套餐定价、回调可达性、小额真机收款和超时／异常订单对账验收。自动退款、已消费次数的退款扣回、争议／拒付及周期性后台对账尚未实现；不要把本地验签测试当成真实收款联调通过。

## 官方资料

- [微信 Native 下单](https://pay.weixin.qq.com/doc/v3/merchant/4012791877)、[H5 下单](https://pay.weixin.qq.com/doc/v3/merchant/4012791834)：按产品场景选择二维码或手机跳转。
- [微信支付公钥验签](https://pay.wechatpay.cn/doc/v3/merchant/4013053249)：签名覆盖原始报文，不能重新序列化后再验证。
- [支付宝官方 Python SDK](https://github.com/alipay/alipay-sdk-python-all)、[手机网站支付参数](https://developer.alibaba.com/docs/api.htm?apiId=1056&docType=4)：RSA2 签名、收银台请求和同步查单。
- [Stripe Checkout 履约](https://docs.stripe.com/checkout/fulfillment)、[Webhook 验签](https://docs.stripe.com/webhooks?lang=python)：跳转成功不能单独作为到账依据。
