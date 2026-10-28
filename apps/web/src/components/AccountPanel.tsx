import { useEffect, useRef, useState } from 'react'
import { XIcon } from '@phosphor-icons/react'
import { accountRequest, type Billing, type Checkout, type CreditOrder, type CreditPack, type User } from '../lib/account'

const money = (amount: number, currency: string) => new Intl.NumberFormat('zh-CN', { style: 'currency', currency }).format(amount / 100)

export function AccountPanel({ user, onClose, onLogout, onRefresh }: {
  user: User; onClose: () => void; onLogout: () => Promise<void>; onRefresh: () => Promise<void>
}) {
  const [billing, setBilling] = useState<Billing | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const [currency, setCurrency] = useState('CNY')
  const [selectedProvider, setSelectedProvider] = useState('')
  const [checkout, setCheckout] = useState<Checkout | null>(null)
  const requestKeys = useRef(new Map<string, string>())
  const pendingKey = `stitchforge.pendingPayment.${user.user_id}`
  const currencies = [...new Set(billing?.packs.map(pack => pack.currency) ?? [])]
  const activeCurrency = currencies.includes(currency) ? currency : currencies[0] ?? 'CNY'
  const providers = billing?.providers?.filter(provider => provider.currencies.includes(activeCurrency)) ?? []
  const providerId = providers.find(provider => provider.id === selectedProvider)?.id ?? providers[0]?.id
  const isMobile = () => window.matchMedia('(pointer: coarse)').matches || window.innerWidth < 700

  async function reload() {
    setRefreshing(true)
    setError('')
    try { setBilling(await accountRequest<Billing>('/billing')); await onRefresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '读取失败') }
    finally { setRefreshing(false) }
  }
  useEffect(() => {
    let active = true
    const pending = window.sessionStorage.getItem(pendingKey)
    const synchronize = pending ? accountRequest<CreditOrder>(`/billing/orders/${encodeURIComponent(pending)}/sync`, {}) : Promise.resolve(null)
    void synchronize.catch(() => null).then(() => accountRequest<Billing>('/billing'))
      .then(value => { if (active) { setBilling(value); void onRefresh() } })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '读取失败') })
    return () => { active = false }
  }, [pendingKey, onRefresh])
  useEffect(() => {
    dialog.current?.showModal()
  }, [])
  useEffect(() => {
    if (!checkout) return
    let active = true
    const timer = window.setInterval(() => {
      void accountRequest<CreditOrder>(`/billing/orders/${checkout.order_id}`).then(order => {
        if (active && order.status === 'paid') {
          window.clearInterval(timer)
          window.sessionStorage.removeItem(pendingKey)
          void accountRequest<Billing>('/billing').then(value => { if (active) {
            setBilling(value); setNotice('充值已到账，可以开始生成图纸。'); setCheckout(null); void onRefresh()
          } })
            .catch(() => { if (active) setError('次数已到账，记录暂时无法刷新。') })
        }
      }).catch(() => { /* Manual synchronization remains available after transient failures. */ })
      if (Date.now() > checkout.expires_at_ms) window.clearInterval(timer)
    }, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [checkout, pendingKey, onRefresh])

  async function startCheckout(orderId: string, provider: string, device: 'mobile' | 'desktop') {
    const result = await accountRequest<Checkout>(`/billing/orders/${orderId}/checkout`, { provider, device })
    window.sessionStorage.setItem(pendingKey, orderId)
    setCheckout(result)
    setNotice('请完成支付。到账以支付平台确认结果为准。')
  }
  async function purchase(pack: CreditPack) {
    if (!providerId) return
    setBusy(true); setError(''); setNotice('')
    const key = `${pack.id}:${providerId}`
    if (!requestKeys.current.has(key)) requestKeys.current.set(key, crypto.randomUUID())
    try {
      const order = await accountRequest<CreditOrder>('/billing/orders', { pack_id: pack.id, request_key: requestKeys.current.get(key) })
      window.sessionStorage.setItem(pendingKey, order.order_id)
      await startCheckout(order.order_id, providerId, isMobile() ? 'mobile' : 'desktop')
      requestKeys.current.delete(key)
      await reload()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '暂时无法发起支付') }
    finally { setBusy(false) }
  }
  async function sync(orderId: string) {
    setBusy(true); setError('')
    try {
      const order = await accountRequest<CreditOrder>(`/billing/orders/${orderId}/sync`, {})
      setNotice(order.status === 'paid' ? '充值已到账。' : '暂未确认到账，如已付款请稍后再查询。')
      if (order.status === 'paid') { setCheckout(null); window.sessionStorage.removeItem(pendingKey) }
      await reload()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '查单失败，请稍后重试') }
    finally { setBusy(false) }
  }

  return <dialog ref={dialog} className="import-dialog account-panel" aria-labelledby="billing-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="import-heading"><div><h2 id="billing-title">我的账号与次数</h2><p>{user.email}</p></div><button autoFocus disabled={busy} aria-label="关闭账号面板" onClick={onClose}><XIcon size={20} aria-hidden="true" /></button></div>
    <div className="account-panel-body">
    <section className="account-balance-card" aria-label="可用生成次数">
      <span className="account-eyebrow">可用生成次数</span>
      <div className="credit-balance"><strong>{billing?.credits.toLocaleString('zh-CN') ?? '-'}</strong><span>次</span></div>
      <p>图片生成成功后扣 1 次，失败不扣次数。</p>
      <div className="account-free-note">OXS / XSD 导入、看图、编辑和下载均免费</div>
    </section>
    {error && <p className="api-error" role="alert">{error}</p>}
    {notice && <p role="status" className="billing-notice">{notice}</p>}
    {!billing ? <p role="status">{error ? '暂时无法读取次数记录，请重试。' : '正在读取次数记录…'}</p> : <>
      <section className="account-section" aria-labelledby="recharge-title">
      <h3 id="recharge-title">充值次数</h3>
      {!billing.checkout_enabled && <p className="billing-notice">在线支付尚未开放，暂时无法自助充值。已有次数可以正常使用。</p>}
      {billing.checkout_enabled && billing.packs.length === 0 && <p>充值套餐尚未上架。</p>}
      {currencies.length > 1 && <label className="payment-currency">支付币种<select aria-label="支付币种" value={activeCurrency} disabled={busy} onChange={event => setCurrency(event.target.value)}>{currencies.map(value => <option key={value}>{value}</option>)}</select></label>}
      {providers.length > 0 && <fieldset className="payment-methods"><legend>支付方式</legend>{providers.map(provider => <label key={provider.id}><input type="radio" name="payment-provider" value={provider.id} checked={providerId === provider.id} disabled={busy} onChange={() => setSelectedProvider(provider.id)} />{provider.label}{provider.test_mode && '（测试环境）'}</label>)}</fieldset>}
      {billing.checkout_enabled && providers.length === 0 && billing.packs.length > 0 && <p>当前币种暂未开放支付。</p>}
      {billing.packs.length > 0 && <div className="credit-packs">{billing.packs.filter(pack => pack.currency === activeCurrency).map(pack => <div key={pack.id}><strong>{pack.credits} 次</strong><span>{money(pack.amount_minor, pack.currency)}</span><button className="primary-button" disabled={busy || !providerId || !!checkout} onClick={() => void purchase(pack)}>{providerId ? '充值' : '待开放'}</button></div>)}</div>}
      {checkout && <section className="payment-checkout" aria-label="完成支付">
        {checkout.kind === 'qr' ? <><p>请使用微信扫描二维码完成支付。</p><img src={checkout.qr_url!} alt="微信支付二维码" width={240} height={240} /></> : <a className="primary-button payment-link" href={checkout.url!} target={isMobile() ? '_self' : '_blank'} rel="noopener" referrerPolicy="origin">前往支付页面</a>}
        <small>支付入口有效至 {new Date(checkout.expires_at_ms).toLocaleTimeString('zh-CN')}。关闭面板后仍可在最近订单中查单。</small>
        <div className="import-actions"><button disabled={busy} onClick={() => void sync(checkout.order_id)}>我已支付，查询到账</button><button disabled={busy} onClick={() => { setCheckout(null); setNotice('可在最近订单中继续支付或查询到账。') }}>稍后支付</button></div>
      </section>}
      </section>
      <section className="account-section" aria-labelledby="orders-title">
      <h3 id="orders-title">最近订单</h3>
      {billing.orders.length === 0 ? <p className="account-empty">暂无充值订单</p> : <ul className="credit-history">{billing.orders.map(order => <li key={order.order_id} className="credit-order"><div><span>{order.credits} 次 · {money(order.amount_minor, order.currency)} · {new Date(order.created_at_ms).toLocaleDateString('zh-CN')}</span><strong>{order.status === 'paid' ? '已到账' : '待付款'}</strong></div>
        {order.status === 'pending' && <div className="order-actions"><button disabled={busy} onClick={() => void sync(order.order_id)}>查询到账</button>{order.payment_provider && <button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await startCheckout(order.order_id, order.payment_provider!, isMobile() ? 'mobile' : 'desktop') } catch (reason) { setError(reason instanceof Error ? reason.message : '无法继续支付') } finally { setBusy(false) } }}>继续支付</button>}</div>}
      </li>)}</ul>}
      </section>
      <section className="account-section" aria-labelledby="ledger-title">
      <h3 id="ledger-title">次数明细</h3>
      {billing.ledger.length === 0 ? <p className="account-empty">暂无使用记录</p> : <ul className="credit-history">{billing.ledger.map(entry => <li key={entry.entry_id}><span className="ledger-description"><span>{entry.kind === 'generation' ? '图片生成' : entry.kind === 'test_grant' ? '测试赠送' : '充值到账'}</span><time>{new Date(entry.created_at_ms).toLocaleString('zh-CN')}</time></span><strong className={entry.delta > 0 ? 'credit-positive' : ''}>{entry.delta > 0 ? '+' : ''}{entry.delta}</strong></li>)}</ul>}
      </section>
    </>}
    </div>
    <div className="account-panel-legal"><a href="/terms.html" target="_blank">服务条款</a><span>·</span><a href="/privacy.html" target="_blank">隐私政策</a><span>充值次数不等同储值余额</span></div>
    <div className="account-panel-footer"><button className="quiet-button" disabled={busy || refreshing} onClick={() => void reload()}>{refreshing ? '刷新中…' : '刷新记录'}</button><button className="quiet-button" disabled={busy} onClick={async () => {
      setBusy(true)
      try { await onLogout() } catch { setError('退出失败，请重试') } finally { setBusy(false) }
    }}>退出登录</button></div>
  </dialog>
}
