// Shows how the supervisor orchestrated a turn: which agent ran, what it did, how long.
const COLORS = {
  supervisor: 'var(--brand)',
  service: '#0f8a5f',
  knowledge: '#8a5a00',
  response: '#7b3fbf',
  evaluation: '#b3261e',
  human_confirmation: '#555',
}

export default function AgentTrace({ trace = [], evaluation }) {
  if (!trace.length) return null
  return (
    <details className="trace">
      <summary>
        Agent trace · {trace.filter((t) => t.agent !== 'supervisor').length} agent steps
        {evaluation ? ` · eval ${evaluation.score}${evaluation.passed ? ' ✓' : ' ✗'}` : ''}
      </summary>
      <ol>
        {trace.map((t, i) => (
          <li key={i}>
            <span className="agent" style={{ color: COLORS[t.agent] || 'inherit' }}>{t.agent}</span>
            <span className="action">{t.action}</span>
            <span className="ms">{t.ms}ms</span>
            {t.detail && <div className="detail">{t.detail}</div>}
          </li>
        ))}
      </ol>
    </details>
  )
}
