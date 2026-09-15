export interface User { user_id: string; email: string; credits: number }
export interface CreditPack { id: string; credits: number; amount_minor: number; currency: string }
export interface CreditOrder { order_id: string; pack_id: string; credits: number; amount_minor: number; currency: string; status: 'pending' | 'paid'; created_at_ms: number; payment_provider?: string; payment_device?: 'mobile' | 'desktop' }
export interface PaymentProvider { id: string; label: string; currencies: string[]; modes: string[]; test_mode: boolean }
export interface Checkout { order_id: string; provider: string; kind: 'qr' | 'redirect'; url?: string; qr_url?: string; expires_at_ms: number }
export interface Billing {
  credits: number; providers: PaymentProvider[]; packs: CreditPack[]; checkout_enabled: boolean; orders: CreditOrder[]
  ledger: { entry_id: string; delta: number; kind: string; created_at_ms: number }[]
}

export async function apiFetch(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  if (init.method && !['GET', 'HEAD'].includes(init.method)) headers.set('X-StitchForge-Request', '1')
  const response = await fetch(input, { ...init, headers, credentials: 'same-origin' })
  if (response.status === 401 && !input.includes('/auth/')) window.dispatchEvent(new Event('stitchforge:session-expired'))
  return response
}

export async function accountRequest<T>(path: string, data?: object): Promise<T> {
  const response = await apiFetch(`/api/v1${path}`, data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  })
  if (response.status === 204) return undefined as T
  const body = await response.json()
  if (!response.ok) throw new Error(body.message ?? '请求失败，请重试')
  return body as T
}
