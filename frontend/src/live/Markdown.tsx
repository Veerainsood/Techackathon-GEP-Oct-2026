// Renders the small Markdown subset the agents use (paragraphs, **bold**, `code`, "- " lists) in the existing text style.
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

export function Markdown({ text, tail }: { text: string; tail?: ReactNode }) {
  const blocks: ReactNode[] = []
  let para: string[] = []
  let items: string[] = []
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
    if (!line.trim()) {
      flushPara()
      flushList()
    } else if (BULLET.test(line)) {
      flushPara()
      items.push(line.replace(BULLET, ''))
    } else {
      flushList()
      para.push(line.trim())
    }
  }
  flushPara()
  flushList()
  if (tail && blocks.length) blocks.push(<span key="tail">{tail}</span>)
  return <>{blocks}</>
}
