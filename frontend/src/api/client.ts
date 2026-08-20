export type Filters = { start_date: string; end_date: string; store_id: string | null }
export type Summary = { net_revenue: number; order_count: number; average_order_value: number }
export type DailyPoint = { date: string; net_revenue: number; order_count: number; average_order_value: number }
export type Product = { rank: number; product_id: string; product_name: string; product_category: string; net_revenue: number; quantity: number; order_count: number }
export type Store = { store_id: string; store_name: string; district: string }
export type DashboardData = { summary: Summary; daily: DailyPoint[]; products: Product[] }
export type QualityStatus = 'healthy' | 'warning' | 'critical'
export type QualityRun = { run_id: string; status: string; completed_at: string; error_message: string | null; report: Record<string, unknown> | null }
export type DataQuality = { status: QualityStatus; raw_updated_at: string | null; cleaned_at: string | null; database_updated_at: string | null; metrics: { raw_rows: number; valid_rows: number; isolated_rows: number; isolation_rate: number | null; missing_amount_count: number; invalid_store_fk: number; invalid_product_fk: number; duplicate_rows: number; date_min: string | null; date_max: string | null; latest_sales_date: string | null }; checks: Array<{ name: string; status: QualityStatus; message: string }> }
export type Alert = { alert_id: string; type: string; severity: 'info'|'warning'|'critical'; date: string; store_id: string|null; product_id: string|null; product_name: string|null; metric: string; actual_value: number; baseline_value: number|null; change_rate: number|null; sample_size: number; message: string; is_read: boolean; dashboard_target: { start_date: string; end_date: string; store_id: string|null; product_id: string|null; view: 'trend'|'products' } }
export type OperationsData = { quality: DataQuality; runs: QualityRun[]; alerts: Alert[] }
export type ChangeValue = { current: number; previous: number; absolute_change: number; change_rate: number|null }
export type CompareData = { current_period: Filters; previous_period: Filters; metrics: { net_revenue: ChangeValue; order_count: ChangeValue; average_order_value: ChangeValue; quantity: ChangeValue }; daily: { current: Array<{date:string;net_revenue:number;order_count:number;quantity:number}>; previous: Array<{date:string;net_revenue:number;order_count:number;quantity:number}> } }
export type RankingMetric = 'net_revenue'|'order_count'|'average_order_value'|'change_rate'|'refund_ratio'
export type StoreRank = Store & { rank:number; value:number|null; previous_value:number; change_rate:number|null; net_revenue:number; order_count:number; average_order_value:number; refund_ratio:number }
export type RankingData = { metric: RankingMetric; current_period: Filters; previous_period: Filters; data: StoreRank[] }
export type Diagnosis = { store: StoreRank; rankings: {net_revenue:number;order_count:number;average_order_value:number}; changes:{net_revenue:number|null}; top_products:Array<{product_id:string;product_name:string;net_revenue:number;order_count:number;previous_revenue:number;change_rate:number|null}>; declining_products:Array<{product_id:string;product_name:string;net_revenue:number;previous_revenue:number;change_rate:number}> }
export type ProductMixItem = Product & { revenue_share:number; quantity_share:number; previous_rank:number|null; rank_change:number|null; is_new_top_10:boolean; previous_revenue:number; previous_order_count:number; change_rate:number|null; refund_amount:number; store_distribution:Array<{store_id:string;store_name:string;net_revenue:number;quantity:number}> }
export type ProductMixData = { current_period: Filters; previous_period: Filters; data: ProductMixItem[] }
export type ProductDecline = { alert_id:string; severity:'info'|'warning'|'critical'; product_id:string; product_name:string; store_id:string|null; actual_value:number; baseline_value:number; change_rate:number|null; sample_size:number; trigger:'revenue_drop'|'consecutive_no_sales'; consecutive_no_sales_days:number; is_read:boolean; dashboard_target: {start_date:string;end_date:string;previous_start_date:string;previous_end_date:string;store_id:string|null;product_id:string;view:'products'} }
export type Phase2Data = { compare: CompareData; ranking: RankingData; productMix: ProductMixData; declines: ProductDecline[] }
export type DailyReportSummary = { report_id:string; report_date:string; store_id:string; version:number; generated_at:string; data_version:string; quality_status:QualityStatus }
export type DailyReport = DailyReportSummary & { source:string; filters:{report_date:string;store_id:string|null}; quality:DataQuality; summary:Summary; previous_date:string|null; comparison:CompareData; best_store:StoreRank|null; best_product:ProductMixItem|null; store_ranking:StoreRank[]; product_mix:ProductMixItem[]; alerts:Alert[] }

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
  if (import.meta.env.VITE_USE_MOCK === 'false') {
    const response = await fetch('/api/v1/filters').then(json)
    return response.data
  }
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

const mockQuality: DataQuality = { status: 'healthy', raw_updated_at: '2026-08-19T10:49:13Z', cleaned_at: '2026-08-19T10:49:13Z', database_updated_at: '2026-08-19T10:49:13Z', metrics: { raw_rows: 12131, valid_rows: 11888, isolated_rows: 167, isolation_rate: .0138, missing_amount_count: 120, invalid_store_fk: 7, invalid_product_fk: 30, duplicate_rows: 78, date_min: '2026-05-01', date_max: '2026-07-31', latest_sales_date: '2026-07-31' }, checks: [{ name: 'freshness', status: 'healthy', message: '数据在新鲜度范围内' }] }
export async function getOperations(filters: Filters, unreadOnly = false): Promise<OperationsData> {
  if (import.meta.env.VITE_USE_MOCK !== 'false') return { quality: mockQuality, runs: [{ run_id: 'mock', status: 'success', completed_at: mockQuality.cleaned_at!, error_message: null, report: null }], alerts: [] }
  const query = new URLSearchParams({ start_date: filters.start_date, end_date: filters.end_date, limit: '20' })
  if (filters.store_id && filters.store_id !== 'ALL') query.set('store_id', filters.store_id)
  if (unreadOnly) query.set('is_read', 'false')
  const [quality, runs, alerts] = await Promise.all([
    fetch('/api/v1/data-quality').then(json),
    fetch('/api/v1/data-quality/runs?limit=5').then(json),
    fetch(`/api/v1/alerts?${query}`).then(json),
  ])
  return { quality: quality.data, runs: runs.data, alerts: alerts.data }
}
export async function setAlertRead(alertId: string, isRead: boolean): Promise<Alert> {
  const response = await fetch(`/api/v1/alerts/${alertId}/read`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_read: isRead }) })
  const body = await json(response)
  return body.data
}

function periodQuery(filters: Filters, previous?: {start_date:string;end_date:string}, extra?: Record<string,string>) {
  const query = new URLSearchParams({ current_start_date: filters.start_date, current_end_date: filters.end_date, ...extra })
  if (filters.store_id && filters.store_id !== 'ALL') query.set('store_id', filters.store_id)
  if (previous) { query.set('previous_start_date', previous.start_date); query.set('previous_end_date', previous.end_date) }
  return query
}

function mockPhase2(filters: Filters, metric: RankingMetric): Phase2Data {
  const dashboard = createMockData(filters), previousFilters = { ...filters, start_date: '2026-04-01', end_date: '2026-04-30' }
  const change = (current:number, previous:number):ChangeValue => ({current, previous, absolute_change:current-previous, change_rate:previous ? (current-previous)/Math.abs(previous) : null})
  const compare:CompareData = { current_period:filters, previous_period:previousFilters, metrics:{net_revenue:change(dashboard.summary.net_revenue, dashboard.summary.net_revenue*.91),order_count:change(dashboard.summary.order_count,Math.round(dashboard.summary.order_count*.96)),average_order_value:change(dashboard.summary.average_order_value,dashboard.summary.average_order_value*.95),quantity:change(dashboard.products.reduce((s,p)=>s+p.quantity,0),900)}, daily:{current:dashboard.daily.map(({date,net_revenue,order_count})=>({date,net_revenue,order_count,quantity:order_count*2})),previous:dashboard.daily.map(({date,net_revenue,order_count})=>({date,net_revenue:Math.round(net_revenue*.91),order_count:Math.round(order_count*.96),quantity:order_count*2}))} }
  const ranks:StoreRank[] = stores.slice(1).map((store,index)=>({...store,rank:index+1,value:90000-index*8000,previous_value:85000-index*7000,change_rate:.05-index*.02,net_revenue:90000-index*8000,order_count:2100-index*150,average_order_value:43+index,refund_ratio:.015+index*.004}))
  const mix:ProductMixItem[] = dashboard.products.map((p,index)=>({...p,revenue_share:.16-index*.009,quantity_share:.15-index*.008,previous_rank:index===2?5:index+1,rank_change:index===2?2:0,is_new_top_10:false,previous_revenue:p.net_revenue*.94,previous_order_count:Math.max(10,p.order_count),change_rate:.064,refund_amount:index*30,store_distribution:ranks.slice(0,3).map(s=>({store_id:s.store_id,store_name:s.store_name,net_revenue:p.net_revenue/3,quantity:p.quantity/3}))}))
  return {compare,ranking:{metric,current_period:filters,previous_period:previousFilters,data:ranks},productMix:{current_period:filters,previous_period:previousFilters,data:mix},declines:[]}
}

export async function getPhase2(filters: Filters, metric: RankingMetric, previous?: {start_date:string;end_date:string}): Promise<Phase2Data> {
  if (import.meta.env.VITE_USE_MOCK !== 'false') return mockPhase2(filters, metric)
  const base = periodQuery(filters, previous), ranking = periodQuery({...filters,store_id:null}, previous, {metric,limit:'50'})
  const [compare, ranks, mix, declines] = await Promise.all([
    fetch(`/api/v1/dashboard/compare?${base}`).then(json), fetch(`/api/v1/dashboard/store-ranking?${ranking}`).then(json),
    fetch(`/api/v1/dashboard/product-mix?${base}`).then(json), fetch(`/api/v1/alerts/product-decline?${base}`).then(json),
  ])
  return {compare:compare.data,ranking:ranks.data,productMix:mix.data,declines:declines.data}
}

export async function getStoreDiagnosis(storeId:string, filters:Filters, previous:{start_date:string;end_date:string}):Promise<Diagnosis> {
  const response = await fetch(`/api/v1/dashboard/store-diagnosis/${encodeURIComponent(storeId)}?${periodQuery({...filters,store_id:null},previous)}`).then(json)
  return response.data
}

const mockReports:DailyReport[] = []
export async function listDailyReports(reportDate?:string, storeId?:string|null):Promise<DailyReportSummary[]> {
  if (import.meta.env.VITE_USE_MOCK !== 'false') return mockReports.filter(r=>(!reportDate||r.report_date===reportDate)&&(!storeId||storeId==='ALL'||r.store_id===storeId))
  const query=new URLSearchParams({limit:'20'}); if(reportDate)query.set('report_date',reportDate);if(storeId)query.set('store_id',storeId)
  return (await fetch(`/api/v1/reports/daily?${query}`).then(json)).data
}
export async function getDailyReport(reportId:string):Promise<DailyReport>{
  if(import.meta.env.VITE_USE_MOCK!=='false'){const found=mockReports.find(r=>r.report_id===reportId);if(!found)throw new Error('日报版本不存在');return found}
  return (await fetch(`/api/v1/reports/daily/${encodeURIComponent(reportId)}`).then(json)).data
}
export async function createDailyReport(reportDate:string,storeId?:string|null):Promise<DailyReport>{
  if(import.meta.env.VITE_USE_MOCK!=='false'){
    const filters={start_date:reportDate,end_date:reportDate,store_id:storeId??null},dashboard=createMockData(filters),phase=mockPhase2(filters,'net_revenue'),version=mockReports.filter(r=>r.report_date===reportDate&&r.store_id===(storeId||'ALL')).length+1
    const report:DailyReport={report_id:`mock-${reportDate}-${version}`,report_date:reportDate,store_id:storeId||'ALL',version,generated_at:new Date().toISOString(),data_version:'mock-data-v1',quality_status:'healthy',source:'mock:sales_clean',filters:{report_date:reportDate,store_id:storeId??null},quality:mockQuality,summary:dashboard.summary,previous_date:phase.compare.previous_period.end_date,comparison:phase.compare,best_store:phase.ranking.data[0],best_product:phase.productMix.data[0],store_ranking:phase.ranking.data,product_mix:phase.productMix.data,alerts:[]};mockReports.unshift(report);return report
  }
  return (await fetch('/api/v1/reports/daily',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({report_date:reportDate,store_id:storeId&&storeId!=='ALL'?storeId:null})}).then(json)).data
}
export async function downloadDailyReport(report:DailyReport,format:'csv'|'xlsx'|'pdf'):Promise<void>{
  if(import.meta.env.VITE_USE_MOCK!=='false'){const blob=new Blob([JSON.stringify(report,null,2)],{type:'text/plain'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`moneki_daily_${report.report_date}_v${report.version}.${format}`;a.click();URL.revokeObjectURL(url);return}
  const response=await fetch(`/api/v1/reports/daily/export?${new URLSearchParams({report_id:report.report_id,format})}`);if(!response.ok)throw new Error((await response.json().catch(()=>({}))).detail||'日报下载失败');const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`moneki_daily_${report.report_date}_v${report.version}.${format}`;a.click();URL.revokeObjectURL(url)
}

async function json(response: Response) {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})), detail = payload.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'API request failed')
  }
  return response.json()
}
