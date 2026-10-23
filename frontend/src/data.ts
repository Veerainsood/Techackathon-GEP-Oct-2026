export type ActionStatus =
  | 'verified'
  | 'simulated'
  | 'pending'
  | 'failed'
  | 'blocked'
  | 'recommended'

export type Agent = 'Collections' | 'Spend Control' | 'Payments' | 'Cash-Flow' | 'Risk'

export interface PolicyCheck {
  label: string
  result: 'pass' | 'fail' | 'warn'
  detail: string
}

export interface AuditEvent {
  time: string
  actor: string
  event: string
}

export interface Action {
  id: string
  agent: Agent
  title: string
  entity: string
  amountL: number
  impact: string
  rationale: string
  status: ActionStatus
  autonomy: 'L2' | 'L3' | 'L4'
  approval: string
  reversible: boolean
  sources: number[]
  checks: PolicyCheck[]
  audit: AuditEvent[]
}

export interface Source {
  id: number
  name: string
  system: string
  detail: string
  freshness: string
  stale?: boolean
  color: string
}

export const formatL = (lakhs: number) => {
  const abs = Math.abs(lakhs)
  const sign = lakhs < 0 ? '−' : ''
  if (abs >= 100) return `${sign}₹${(abs / 100).toFixed(2)} Cr`
  if (abs < 1) return `${sign}₹${Math.round(abs * 100)}K`
  return `${sign}₹${Number.isInteger(abs) ? abs : abs.toFixed(1)}L`
}

export const sources: Source[] = [
  { id: 1, name: 'HDFC Current A/c', system: 'Bank feed · sandbox', detail: 'Available balance ₹1.00 Cr', freshness: '2 min ago', color: 'bg-blue-600' },
  { id: 2, name: 'NetSuite ERP', system: 'Accounting · mock', detail: 'AR/AP ledgers, 214 invoices', freshness: '14 min ago', color: 'bg-indigo-600' },
  { id: 3, name: 'HubSpot CRM', system: 'CRM · mock', detail: 'Customer history, owners', freshness: '6 min ago', color: 'bg-orange-500' },
  { id: 4, name: 'Procurement Hub', system: 'Source-to-pay · mock', detail: 'PRs, POs, contracts & invoices', freshness: '9 min ago', color: 'bg-brand' },
  { id: 5, name: 'Budget Book FY27', system: 'Budgets · mock', detail: '8 departments, Oct actuals', freshness: '1 hr ago', color: 'bg-violet-600' },
  { id: 6, name: 'Payroll Run', system: 'HRMS · mock', detail: 'Oct payroll ₹30L on day 15', freshness: '26 hr ago', stale: true, color: 'bg-rose-500' },
]

// 30-day forecast in lakhs, built from discrete scheduled events.
type Event = { day: number; amount: number }
const baseEvents: Event[] = [
  { day: 5, amount: 5 }, // Nimbus Retail collection
  { day: 12, amount: 10 }, // Kestrel Labs collection
  { day: 26, amount: 20 }, // Orion Logistics (expected)
  { day: 28, amount: 5 }, // Sapphire Foods
  { day: 10, amount: -12 }, // Acme Components
  { day: 15, amount: -30 }, // Payroll
  { day: 20, amount: -8 }, // Cloudnine Hosting
]
const revisedEvents: Event[] = [
  { day: 5, amount: 5 },
  { day: 12, amount: 10 },
  { day: 19, amount: 20 }, // Orion promise-to-pay after escalation
  { day: 28, amount: 5 },
  { day: 8, amount: -11.76 }, // Acme paid early with 2% discount
  { day: 15, amount: -30 },
  { day: 27, amount: -8 }, // Cloudnine deferred within terms
]

const build = (events: Event[]) => {
  let bal = 100
  const out: number[] = [bal]
  for (let d = 1; d <= 30; d++) {
    bal -= 1 // daily opex
    events.filter((e) => e.day === d).forEach((e) => (bal += e.amount))
    out.push(Math.round(bal * 10) / 10)
  }
  return out
}

const base = build(baseEvents)
const revised = build(revisedEvents)

export const forecast = base.map((v, i) => ({
  day: i,
  label: i === 0 ? 'Today' : `D${i}`,
  baseline: v,
  revised: revised[i],
  target: 50,
  minimum: 30,
}))

export const forecastStats = {
  current: 100,
  collections: 40,
  expenses: 80,
  ending: base[30],
  trough: Math.min(...base),
  troughDay: base.indexOf(Math.min(...base)),
  revisedTrough: Math.min(...revised),
  breachDays: base.filter((v) => v < 50).length,
}

export const overdue = [
  { customer: 'Vertex Health', amountL: 35, days: 60, risk: 'High', action: 'Collection-risk review' },
  { customer: 'Orion Logistics', amountL: 20, days: 45, risk: 'Medium', action: 'Escalate to owner' },
  { customer: 'Nimbus Retail', amountL: 5, days: 12, risk: 'Low', action: 'Send reminder' },
]

export const initialActions: Action[] = [
  {
    id: 'ACT-102',
    agent: 'Collections',
    title: 'Escalate Orion Logistics invoice to account owner',
    entity: 'INV-20931 · ₹20L · 45 days overdue',
    amountL: 20,
    impact: 'Pulls ₹20L forward from D26 → D19 (promise-to-pay)',
    rationale:
      'Largest recoverable overdue balance with a good payment history. Escalation to account owner Priya M. and finance manager has the highest expected acceleration.',
    status: 'verified',
    autonomy: 'L3',
    approval: 'None — within delegated authority',
    reversible: true,
    sources: [2, 3],
    checks: [
      { label: 'Invoice not disputed', result: 'pass', detail: 'No open dispute in CRM' },
      { label: 'Escalation path configured', result: 'pass', detail: 'Owner: Priya M. → FM: Rahul S.' },
      { label: 'Suppression window', result: 'pass', detail: 'No contact in last 7 days' },
    ],
    audit: [
      { time: '13:31:02', actor: 'Collections Agent', event: 'Ranked #1 by value × days overdue' },
      { time: '13:31:04', actor: 'Policy Engine v3.2', event: '3/3 checks passed' },
      { time: '13:31:05', actor: 'CRM connector', event: 'Task HS-8812 created' },
      { time: '13:31:07', actor: 'CRM connector', event: 'Verified: task visible in source system' },
    ],
  },
  {
    id: 'ACT-101',
    agent: 'Collections',
    title: 'Send payment reminder to Nimbus Retail',
    entity: 'INV-21107 · ₹5L · 12 days overdue',
    amountL: 5,
    impact: 'Protects ₹5L expected on D5',
    rationale: 'Low-risk customer, short overdue window. Approved template "Gentle reminder v2" applies.',
    status: 'simulated',
    autonomy: 'L3',
    approval: 'None — reminders are autonomous',
    reversible: false,
    sources: [2, 3],
    checks: [
      { label: 'Approved template', result: 'pass', detail: 'Gentle reminder v2' },
      { label: 'Authorized channel', result: 'pass', detail: 'ar@company.com (sandbox)' },
      { label: 'Suppression window', result: 'pass', detail: 'Last reminder 9 days ago' },
    ],
    audit: [
      { time: '13:31:08', actor: 'Collections Agent', event: 'Reminder drafted from template' },
      { time: '13:31:09', actor: 'Policy Engine v3.2', event: '3/3 checks passed' },
      { time: '13:31:10', actor: 'Email sandbox', event: 'Simulated send — no live connector' },
    ],
  },
  {
    id: 'ACT-108',
    agent: 'Collections',
    title: 'Send reminder to Sapphire Foods',
    entity: 'INV-21160 · ₹5L · 8 days overdue',
    amountL: 5,
    impact: 'Protects ₹5L expected on D28',
    rationale: 'Routine reminder for an eligible overdue invoice.',
    status: 'failed',
    autonomy: 'L3',
    approval: 'None — reminders are autonomous',
    reversible: false,
    sources: [2],
    checks: [
      { label: 'Approved template', result: 'pass', detail: 'Gentle reminder v2' },
      { label: 'Delivery verified', result: 'fail', detail: 'Hard bounce: billing@sapphire.in' },
    ],
    audit: [
      { time: '13:31:11', actor: 'Email sandbox', event: 'Send attempted' },
      { time: '13:31:14', actor: 'Email sandbox', event: 'Delivery failed — hard bounce' },
      { time: '13:31:14', actor: 'Orchestrator', event: 'Not retried. Escalated to AR owner to update contact' },
    ],
  },
  {
    id: 'ACT-105',
    agent: 'Payments',
    title: 'Defer Cloudnine Hosting payment to D27',
    entity: 'BILL-4410 · ₹8L · due D28',
    amountL: 8,
    impact: 'Removes ₹8L from the D20–D26 trough; no penalty',
    rationale:
      'Contract terms are Net-45 with due date on D28. Paying on D27 keeps the vendor current while protecting the reserve during the payroll trough.',
    status: 'pending',
    autonomy: 'L4',
    approval: 'CFO — payment > ₹2L',
    reversible: true,
    sources: [1, 2],
    checks: [
      { label: 'Within contractual due date', result: 'pass', detail: 'Due D28, scheduled D27' },
      { label: 'Payee & bank details unchanged', result: 'pass', detail: 'Matches vendor master' },
      { label: 'Amount threshold', result: 'warn', detail: '₹8L > ₹2L — human approval required' },
    ],
    audit: [
      { time: '13:31:16', actor: 'Payments Agent', event: 'Proposed reschedule D20 → D27' },
      { time: '13:31:17', actor: 'Policy Engine v3.2', event: 'Requires approval: amount > ₹2L' },
      { time: '13:31:17', actor: 'Orchestrator', event: 'Routed to CFO approval inbox' },
    ],
  },
  {
    id: 'ACT-106',
    agent: 'Payments',
    title: 'Pay Acme Components early to capture 2% discount',
    entity: 'BILL-4398 · ₹12L · 2/10 Net-30',
    amountL: 12,
    impact: 'Saves ₹24K (estimated); balance stays ≥ ₹50L through D15',
    rationale:
      'Early payment on D8 captures a 2% discount. Cash-Flow Agent objected; orchestrator found the trough is driven by D20+ outflows, so paying early does not breach the reserve.',
    status: 'pending',
    autonomy: 'L4',
    approval: 'CFO — payment > ₹2L',
    reversible: false,
    sources: [1, 2],
    checks: [
      { label: 'PO & GRN match', result: 'pass', detail: 'PO-7781 three-way matched' },
      { label: 'Duplicate invoice check', result: 'pass', detail: 'No duplicates in 180 days' },
      { label: 'Reserve after payment', result: 'pass', detail: 'Min ₹55.2L in revised plan' },
      { label: 'Amount threshold', result: 'warn', detail: '₹12L > ₹2L — human approval required' },
    ],
    audit: [
      { time: '13:31:18', actor: 'Payments Agent', event: 'Recommended early payment' },
      { time: '13:31:18', actor: 'Cash-Flow Agent', event: 'Objection: preserve liquidity' },
      { time: '13:31:20', actor: 'Orchestrator', event: 'Conflict resolved via priority rule P-2' },
      { time: '13:31:21', actor: 'Policy Engine v3.2', event: 'Requires approval: amount > ₹2L' },
    ],
  },
  {
    id: 'ACT-104',
    agent: 'Spend Control',
    title: 'Hold Marketing purchase request — exceeds budget',
    entity: 'PR-3302 · ₹1L · Event sponsorship',
    amountL: 1,
    impact: '₹50K overrun prevented pending owner decision',
    rationale:
      'Marketing has spent ₹4.5L of a ₹5L monthly budget. The ₹1L request leaves a ₹50K overrun, and exceeds the ₹25K auto-approve limit.',
    status: 'blocked',
    autonomy: 'L3',
    approval: 'Budget owner — Ananya K.',
    reversible: true,
    sources: [4, 5],
    checks: [
      { label: 'Remaining budget', result: 'fail', detail: '₹50K remaining vs ₹1L requested' },
      { label: 'Auto-approve limit', result: 'fail', detail: '₹1L > ₹25K' },
      { label: 'Duplicate purchase', result: 'pass', detail: 'No similar request in 90 days' },
    ],
    audit: [
      { time: '13:31:22', actor: 'Spend Control Agent', event: 'Budget check failed' },
      { time: '13:31:22', actor: 'Policy Engine v3.2', event: 'Rule SP-4: hold & escalate' },
      { time: '13:31:23', actor: 'Procurement connector', event: 'Verified: PR status = On Hold' },
    ],
  },
  {
    id: 'ACT-103',
    agent: 'Risk',
    title: 'Flag Vertex Health for collection-risk review',
    entity: 'INV-20544 · ₹35L · 60 days overdue',
    amountL: 35,
    impact: 'Excluded from forecast until confirmed',
    rationale:
      'Two missed promise-to-pay dates and a 60-day delay. Treated as at-risk; not counted as an expected inflow.',
    status: 'recommended',
    autonomy: 'L2',
    approval: 'Finance manager review',
    reversible: true,
    sources: [2, 3],
    checks: [
      { label: 'Dispute status', result: 'warn', detail: 'Customer email mentions "service credit"' },
      { label: 'Untrusted content', result: 'pass', detail: 'Email instructions ignored (SEC-09)' },
    ],
    audit: [
      { time: '13:31:24', actor: 'Risk Agent', event: 'Risk score 0.82 — high' },
      { time: '13:31:24', actor: 'Orchestrator', event: 'Recommendation only (L2)' },
    ],
  },
  {
    id: 'ACT-107',
    agent: 'Spend Control',
    title: 'Review 3 unused SaaS subscriptions',
    entity: '14 idle seats across 3 tools',
    amountL: 3,
    impact: '₹3L/yr estimated savings — not realized',
    rationale: 'No logins in 60+ days for 14 seats. Cancellation requests prepared for owner review.',
    status: 'recommended',
    autonomy: 'L2',
    approval: 'IT & department owners',
    reversible: true,
    sources: [4, 5],
    checks: [{ label: 'Cancellation authority', result: 'warn', detail: 'Agent may not cancel — prepared only' }],
    audit: [{ time: '13:31:25', actor: 'Spend Control Agent', event: 'Drafted 3 cancellation requests' }],
  },
]

export const steps = [
  { title: 'Interpreting goal', detail: 'Reserve ≥ ₹50L for 30 days · maximize collections · control spend' },
  { title: 'Reading 6 connected sources', detail: '5 fresh · Payroll feed stale (26 hr)' },
  { title: 'Forecasting 30-day cash position', detail: 'Baseline trough ₹40L on D25' },
  { title: 'Running specialist agents', detail: 'Collections · Spend Control · Payments · Risk' },
  { title: 'Validating 8 candidate actions', detail: 'Policy engine v3.2 · deterministic' },
  { title: 'Executing within authority', detail: '2 executed · 1 failed · 2 awaiting approval' },
  { title: 'Verifying & re-forecasting', detail: 'Revised trough ₹55.2L — above target' },
]

export const suggestions = [
  'Keep ₹50L in cash for the next 30 days while maximizing collections',
  'Which overdue invoices should we chase first?',
  'Will the Marketing purchase request break the budget?',
  'Optimize vendor payment timing this month',
]

export const recentThreads = [
  'Protect ₹50L reserve — Oct',
  'Q3 vendor payment schedule',
  'Marketing budget overrun',
  'Unused SaaS subscriptions',
]

export const policies = [
  { id: 'P-1', name: 'Minimum cash reserve', rule: 'Available cash ≥ ₹30L at all times; target ₹50L', owner: 'CFO', version: 'v3.2', autonomy: 'L4' },
  { id: 'CO-1', name: 'Collection reminders', rule: 'Send approved templates; 7-day suppression window', owner: 'AR Lead', version: 'v2.0', autonomy: 'L3' },
  { id: 'SP-2', name: 'Expense auto-approval', rule: 'Approve ≤ ₹25K when all checks pass', owner: 'Controller', version: 'v1.4', autonomy: 'L3' },
  { id: 'SP-4', name: 'Budget overrun', rule: 'Hold & escalate requests exceeding remaining budget', owner: 'Controller', version: 'v1.4', autonomy: 'L3' },
  { id: 'PY-1', name: 'Payment approval', rule: 'Human approval for any payment > ₹2L', owner: 'CFO', version: 'v3.2', autonomy: 'L2' },
  { id: 'PY-9', name: 'Vendor bank details', rule: 'Never change autonomously', owner: 'CFO', version: 'v3.2', autonomy: 'L1' },
]

export interface StandingGoal {
  id: string
  title: string
  metric: string
  status: 'on-track' | 'at-risk'
  progress: number
  lastRun: string
  actionsToday: number
}

export const standingGoals: StandingGoal[] = [
  { id: 'G-1', title: 'Keep ₹50L cash reserve', metric: 'Lowest projected ₹55.2L · 30 days', status: 'on-track', progress: 100, lastRun: '4 min ago', actionsToday: 6 },
  { id: 'G-2', title: 'Bring overdue AR under ₹40L', metric: '₹60L → ₹47L this month', status: 'at-risk', progress: 65, lastRun: '12 min ago', actionsToday: 9 },
  { id: 'G-3', title: 'Zero budget overruns', metric: '1 overrun held · 0 slipped through', status: 'on-track', progress: 100, lastRun: '2 min ago', actionsToday: 5 },
]

export interface AutoRun {
  time: string
  title: string
  detail: string
  impact?: string
  realized: boolean
}

export const awaySummary = {
  window: 'since 7:00 PM yesterday',
  handled: 18,
  escalated: 2,
  realizedL: 14.4,
  estimatedL: 14,
  hoursSaved: 11,
}

export const overnightRuns: AutoRun[] = [
  { time: '06:42', title: 'Collected ₹12L from Zenith Pharma', detail: 'Escalated on Tuesday → payment received, matched in bank', impact: '+₹12L cash', realized: true },
  { time: '05:10', title: 'Auto-approved 9 purchase requests', detail: 'All ≤ ₹25K, within budget, no duplicates', impact: '₹1.6L committed', realized: true },
  { time: '02:30', title: 'Blocked a duplicate Acme invoice', detail: 'Same amount, PO and date as BILL-4398', impact: '₹2.4L duplicate payment avoided', realized: true },
  { time: '23:15', title: 'Sent 6 collection reminders', detail: 'Approved templates · 7-day suppression respected', impact: '₹14L chased', realized: false },
  { time: '21:04', title: 'Paused 2 payroll-dependent actions', detail: 'Payroll feed went stale — safe-fail, resumes after resync', realized: true },
]

export interface LiveEvent {
  source: string
  title: string
  outcome: string
  handled: 'auto' | 'escalated' | 'blocked'
  rule: string
  action?: Action
}

export const liveEvents: LiveEvent[] = [
  {
    source: 'Procurement Hub',
    title: 'New purchase request PR-3318 · ₹18K · Engineering',
    outcome: 'Auto-approved — budget has ₹2.1L left, no duplicates',
    handled: 'auto',
    rule: 'SP-2 · ≤ ₹25K',
  },
  {
    source: 'HDFC bank feed',
    title: 'Payment received · Kestrel Labs · ₹10L',
    outcome: 'Matched to INV-21088, marked paid, forecast refreshed',
    handled: 'auto',
    rule: 'Verified in source',
  },
  {
    source: 'NetSuite ERP',
    title: 'Cloudnine Hosting changed bank account details',
    outcome: 'Payments to vendor frozen · verification sent to AP lead',
    handled: 'escalated',
    rule: 'PY-9 · never autonomous',
    action: {
      id: 'ACT-109',
      agent: 'Risk',
      title: 'Verify Cloudnine bank detail change before any payment',
      entity: 'Vendor V-0231 · new account ending 4471',
      amountL: 8,
      impact: '₹8L payment on hold until verified',
      rationale:
        'Bank details changed 9 days before a scheduled payment, a common invoice-fraud pattern. Agent froze payouts and asked for call-back verification. It can never approve this change itself.',
      status: 'pending',
      autonomy: 'L2',
      approval: 'AP lead + CFO (dual control)',
      reversible: true,
      sources: [2, 1],
      checks: [
        { label: 'Bank details change', result: 'fail', detail: 'Changed 16:52 today via vendor portal' },
        { label: 'Autonomous change allowed', result: 'fail', detail: 'Policy PY-9 — never' },
        { label: 'Payment hold applied', result: 'pass', detail: 'Verified in payment system' },
      ],
      audit: [
        { time: '16:52:10', actor: 'NetSuite connector', event: 'Vendor master change detected' },
        { time: '16:52:11', actor: 'Risk Agent', event: 'Risk score 0.91 — payment timing + detail change' },
        { time: '16:52:12', actor: 'Policy Engine v3.2', event: 'PY-9: hold & require dual approval' },
      ],
    },
  },
  {
    source: 'Procurement Hub',
    title: 'Invoice from Acme Components · ₹4.2L',
    outcome: 'Held — unit price 8% above contract rate (₹33.6K leakage)',
    handled: 'blocked',
    rule: 'Contract price match',
  },
  {
    source: 'HubSpot CRM',
    title: 'Nimbus Retail promised payment for Monday',
    outcome: 'Promise recorded · reminders paused · forecast updated',
    handled: 'auto',
    rule: 'CO-1',
  },
]

export type Mode = 'auto' | 'approve' | 'never'

export interface Boundary {
  action: string
  agent: string
  mode: Mode
  limit: string
  locked?: boolean
}

export const boundaries: Boundary[] = [
  { action: 'Send collection reminders', agent: 'Collections', mode: 'auto', limit: 'Approved templates only' },
  { action: 'Escalate overdue accounts', agent: 'Collections', mode: 'auto', limit: 'Any amount' },
  { action: 'Approve purchase requests', agent: 'Spend Control', mode: 'auto', limit: 'Up to ₹25K, within budget' },
  { action: 'Hold over-budget requests', agent: 'Spend Control', mode: 'auto', limit: 'Any amount' },
  { action: 'Schedule vendor payments', agent: 'Payments', mode: 'auto', limit: 'Up to ₹2L · above needs approval' },
  { action: 'Pay early for discounts', agent: 'Payments', mode: 'approve', limit: 'If reserve stays ≥ ₹50L' },
  { action: 'Hold suspicious payments', agent: 'Risk', mode: 'auto', limit: 'Release needs a human' },
  { action: 'Cancel subscriptions', agent: 'Spend Control', mode: 'approve', limit: 'Prepared, not executed' },
  { action: 'Renew or terminate contracts', agent: 'Contracts', mode: 'approve', limit: 'Owner + legal' },
  { action: 'Move money between accounts', agent: 'Cash-Flow', mode: 'never', limit: 'Out of scope', locked: true },
  { action: 'Change vendor bank details', agent: 'Risk', mode: 'never', limit: 'Hard-coded block', locked: true },
]
