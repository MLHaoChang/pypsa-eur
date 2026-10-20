import { useId } from 'react'

/** Decorative dimensional presence. Media/work truth belongs to the launcher. */
export default function Pearl() {
  const id = useId().replace(/:/g, '')
  return <svg width="84" height="84" viewBox="0 0 84 84" fill="none" data-testid="companion-pearl">
    <defs>
      <radialGradient id={`${id}-glass`} cx=".3" cy=".2" r=".85">
        <stop stopColor="#ffffff" /><stop offset=".3" stopColor="#e3ecfa" />
        <stop offset=".65" stopColor="#a6b9db" /><stop offset="1" stopColor="#4a5b81" />
      </radialGradient>
      <radialGradient id={`${id}-shine`} cx=".25" cy=".15" r=".65">
        <stop stopColor="#fff" stopOpacity=".9" /><stop offset="1" stopColor="#fff" stopOpacity="0" />
      </radialGradient>
    </defs>
    <circle className="companion-live-orbit" cx="42" cy="42" r="39" stroke="#ff586b" strokeWidth="1.5" strokeDasharray="65 180" />
    <circle cx="42" cy="42" r="32" fill={`url(#${id}-glass)`} stroke="#e5edfa" />
    <circle cx="42" cy="42" r="29" stroke="#fff" strokeOpacity=".4" />
    <circle cx="42" cy="42" r="31" fill={`url(#${id}-shine)`} />
    <g className="pearl-gaze" stroke="#37435e" strokeLinecap="round">
      <path d="M35 39v3M48 39v3" strokeWidth="3" />
      <path d="M39 49q3 2 6 0" strokeWidth="1.3" />
    </g>
    <path className="companion-node-sector" d="M34 65q8 3 16 0" stroke="#ff586b" strokeWidth="2" strokeLinecap="round" />
    <ellipse cx="30" cy="22" rx="10" ry="4" transform="rotate(-30 30 22)" fill="#fff" fillOpacity=".4" />
  </svg>
}
