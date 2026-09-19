import { t, useLocale } from '../../i18n'
import { useId } from 'react'

export function LiveConnecting({ title = t("Подключаемся к разговору"), description = t("Устанавливаем голосовую связь"), roomTitle }: {
  title?: string; description?: string; roomTitle?: string;
}) {
  useLocale()
  const id = useId()
  return <section className="live-connecting" role="status" aria-live="polite" aria-atomic="true">
    <div className="live-connecting-art" aria-hidden="true">
      <svg viewBox="0 0 360 300" fill="none" focusable="false">
        <defs>
          <radialGradient id={`${id}-sphere`} cx=".3" cy=".2" r=".85">
            <stop stopColor="#dcebdd" /><stop offset=".42" stopColor="#8db69c" /><stop offset=".78" stopColor="#346f55" /><stop offset="1" stopColor="#174b39" />
          </radialGradient>
          <linearGradient id={`${id}-orbit`} x1="40" y1="80" x2="320" y2="220" gradientUnits="userSpaceOnUse">
            <stop stopColor="#215c49" stopOpacity=".12" /><stop offset=".5" stopColor="#76a589" /><stop offset="1" stopColor="#215c49" stopOpacity=".3" />
          </linearGradient>
          <radialGradient id={`${id}-shadow`}><stop stopColor="#215c49" stopOpacity=".18" /><stop offset="1" stopColor="#215c49" stopOpacity="0" /></radialGradient>
        </defs>
        <ellipse className="live-orb-shadow" cx="180" cy="267" rx="96" ry="16" fill={`url(#${id}-shadow)`} />
        <g className="live-orb-float">
          <circle cx="180" cy="145" r="119" stroke="#215c49" strokeOpacity=".07" strokeDasharray="2 9" />
          <g className="live-orbit live-orbit-back" stroke={`url(#${id}-orbit)`} strokeWidth="1.2">
            <ellipse cx="180" cy="145" rx="140" ry="48" transform="rotate(-32 180 145)" />
            <ellipse cx="180" cy="145" rx="140" ry="48" transform="rotate(42 180 145)" />
          </g>
          <circle cx="180" cy="145" r="69" fill={`url(#${id}-sphere)`} />
          <circle cx="180" cy="145" r="68.5" stroke="white" strokeOpacity=".3" />
          <g stroke="#f4fff4" strokeWidth="4" strokeLinecap="round">
            {[18, 32, 48, 28, 40, 20].map((height, index) => <path key={index} className={`live-orb-wave live-orb-wave-${index}`} d={`M${155 + index * 10} ${145 - height / 2}v${height}`} />)}
          </g>
          <g className="live-orbit live-orbit-front">
            <g transform="rotate(-32 180 145)">
              <ellipse cx="180" cy="145" rx="137" ry="49" stroke={`url(#${id}-orbit)`} strokeWidth="1.4" />
              <circle cx="43" cy="145" r="6" fill="#477d60" stroke="#fffefa" strokeWidth="3" />
              <circle cx="317" cy="145" r="4" fill="#9aba9f" stroke="#fffefa" strokeWidth="2" />
            </g>
          </g>
        </g>
      </svg>
    </div>
    <div className="live-connecting-copy">
      <span className="live-connecting-eyebrow">SOYLE LIVE</span>
      <h1>{title}</h1>
      <p>{description}</p>
      {roomTitle && <span className="live-connecting-room">{roomTitle}</span>}
      <div className="live-connecting-pulse" aria-hidden="true"><i /><i /><i /></div>
    </div>
  </section>
}
