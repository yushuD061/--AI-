export type Filters = { start_date: string; end_date: string; store_id: string | null }
export type Summary = { net_revenue: number; order_count: number; average_order_value: number }
export type DailyPoint = { date: string; net_revenue: number; order_count: number; average_order_value: number }
export type Product = { rank: number; product_id: string; product_name: string; product_category: string; net_revenue: number; quantity: number; order_count: number }
export type Store = { store_id: string; store_name: string; district: string }
export type DashboardData = { summary: Summary; daily: DailyPoint[]; products: Product[] }

const stores: Store[] = [
  { store_id: 'ALL', store_name: '全部门店', district: '全城' },
  { store_id: 'S01', store_name: 'Super Souper', district: '上海·徐汇' },
  { store_id: 'S02', store_name: 'Makai Poke', district: '上海·静安' },
  { store_id: 'S03', store_name: 'Juicy Bao Bao', district: '上海·浦东' },
  { store_id: 'S04', store_name: 'Arigato Sando', district: '上海·长宁' },
  { store_id: 'S05', store_name: 'Little Yuzu', district: '上海·虹口' },
]
const productNames = ['三文鱼poke', '牛肉poke', '豚骨拉面', '照烧鸡饭', '味增拉面', '炸鸡饭团', '和风沙拉', '明太子饭团', '鲜虾云吞', '照烧鸡三明治']
const productCategories = ['主食', '主食', '主食', '主食', '主食', '小食', '轻食', '小食', '点心', '三明治']
const formatDay = (value: Date) => value.toISOString().slice(0, 10)

function createMockData(filters: Filters): DashboardData {
  const start = new Date(`${filters.start_date}T00:00:00`), end = new Date(`${filters.end_date}T00:00:00`)
  const days: DailyPoint[] = []
  for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {
    const day = cursor.getDate(), weekendLift = [0, 6].includes(cursor.getDay()) ? 1.12 : 1, storeLift = filters.store_id && filters.store_id !== 'ALL' ? 0.2 : 1
    const revenue = Math.round((5150 + ((day * 173) % 2100)) * weekendLift * storeLift), orders = Math.max(1, Math.round(revenue / (34 + (day % 5))))
    days.push({ date: formatDay(cursor), net_revenue: revenue, order_count: orders, average_order_value: Number((revenue / orders).toFixed(2)) })
  }
  const total = days.reduce((sum, item) => sum + item.net_revenue, 0), orders = days.reduce((sum, item) => sum + item.order_count, 0)
  const products = productNames.map((name, index) => ({ rank: index + 1, product_id: `P${String(index + 1).padStart(2, '0')}`, product_name: name, product_category: productCategories[index], net_revenue: Math.round(total * (0.16 - index * 0.009)), quantity: Math.round(total * (0.16 - index * 0.009) / (27 + index)), order_count: Math.round(orders * (0.14 - index * 0.008)) }))
  return { summary: { net_revenue: total, order_count: orders, average_order_value: Number((total / orders).toFixed(2)) }, daily: days, products }
}

export async function getFilters(): Promise<{ date_min: string; date_max: string; stores: Store[] }> {
  return { date_min: '2026-05-01', date_max: '2026-07-31', stores }
}
export async function getDashboard(filters: Filters): Promise<DashboardData> {
  // Set VITE_USE_MOCK=false once the FastAPI service is available.
  if (import.meta.env.VITE_USE_MOCK !== 'false') return createMockData(filters)
  const query = new URLSearchParams({ start_date: filters.start_date, end_date: filters.end_date })
  if (filters.store_id && filters.store_id !== 'ALL') query.set('store_id', filters.store_id)
  const requests = ['summary', 'daily', 'top-products'].map((path) => fetch(`/api/v1/dashboard/${path}?${query}`).then((response) => { if (!response.ok) throw new Error('API request failed'); return response.json() }))
  const [summary, daily, products] = await Promise.all(requests)
  return { summary: summary.data, daily: daily.data, products: products.data }
}
