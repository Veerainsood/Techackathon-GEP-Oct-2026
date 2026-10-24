// Renders the small Markdown subset the agents use (paragraphs, **bold**, `code`, "- " lists, | tables |) in the existing text style.
import type { ReactNode } from 'react'

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    out.push(
      tok.startsWith('**') ? (
        <b key={`${key}-${i++}`}>{tok.slice(2, -2)}</b>
      ) : (
        <code key={`${key}-${i++}`} className="rounded bg-stone-100 px-1 py-0.5 text-[13px]">
          {tok.slice(1, -1)}
        </code>
      ),
    )
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/
const TABLE_ROW = /^\s*\|.*\|\s*$/
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/
const NUMERIC = /^[-+(]?[$€£₹]?\s?[\d,]+(\.\d+)?%?\)?$/

const cells = (row: string) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())

function Table({ rows, id }: { rows: string[]; id: string }) {
  const body = rows.filter((r) => !TABLE_RULE.test(r))
  const hasHeader = rows.length > 1 && TABLE_RULE.test(rows[1])
  const head = hasHeader ? cells(body[0]) : null
  const data = (hasHeader ? body.slice(1) : body).map(cells)
  return (
    <div className="my-2 overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-[13px]">
        {head && (
          <thead className="bg-stone-50 text-left text-[11px] uppercase tracking-wider text-muted">
            <tr>
              {head.map((h, i) => (
                <th key={i} className="px-3 py-1.5 font-medium">
                  {inline(h, `${id}-h${i}`)}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {data.map((r, ri) => (
            <tr key={ri} className="border-t border-line first:border-t-0">
              {r.map((c, ci) => (
                <td key={ci} className={`px-3 py-1.5 ${NUMERIC.test(c.replace(/\*/g, '')) ? 'text-right tabular-nums' : ''}`}>
                  {inline(c, `${id}-${ri}-${ci}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Markdown({ text, tail }: { text: string; tail?: ReactNode }) {
  const blocks: ReactNode[] = []
  let para: string[] = []
  let items: string[] = []
  let table: string[] = []
  const flushTable = () => {
    if (table.length) blocks.push(<Table key={`t${blocks.length}`} id={`t${blocks.length}`} rows={table} />)
    table = []
  }
  const flushPara = () => {
    if (para.length) blocks.push(<p key={`p${blocks.length}`}>{inline(para.join(' ').replace(/^#+\s*/, ''), `p${blocks.length}`)}</p>)
    para = []
  }
  const flushList = () => {
    if (items.length)
      blocks.push(
        <ul key={`u${blocks.length}`} className="list-disc space-y-1 pl-5">
          {items.map((it, i) => (
            <li key={i}>{inline(it, `u${blocks.length}-${i}`)}</li>
          ))}
        </ul>,
      )
    items = []
  }
  for (const line of text.split('\n')) {
    if (TABLE_ROW.test(line) || (table.length && TABLE_RULE.test(line))) {
      flushPara()
      flushList()
      table.push(line)
    } else if (!line.trim()) {
      flushTable()
      flushPara()
      flushList()
    } else if (BULLET.test(line)) {
      flushTable()
      flushPara()
      items.push(line.replace(BULLET, ''))
    } else {
      flushTable()
      flushList()
      para.push(line.trim())
    }
  }
  flushTable()
  flushPara()
  flushList()
  if (tail && blocks.length) blocks.push(<span key="tail">{tail}</span>)
  return <>{blocks}</>
}
