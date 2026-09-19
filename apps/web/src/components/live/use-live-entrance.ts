import { useEffect, useState } from 'react'

// A short entrance prevents a fast response from flashing the loading artwork.
export function useLiveEntrance() {
  const [entering, setEntering] = useState(true)
  useEffect(() => {
    const timer = window.setTimeout(() => setEntering(false), window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1600)
    return () => window.clearTimeout(timer)
  }, [])
  return entering
}

