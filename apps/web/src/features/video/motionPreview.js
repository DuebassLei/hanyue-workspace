export const DEFAULT_MOTION_INTENSITY = 0.12
export const DEFAULT_MOTION_FOCUS = Object.freeze({ x: 0.5, y: 0.5 })
export const MAX_MOTION_INTENSITY = 0.25

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

export function normalizedFocus(focus) {
  return {
    x: clamp(Number(focus?.x ?? 0.5), 0, 1),
    y: clamp(Number(focus?.y ?? 0.5), 0, 1),
  }
}

export function effectiveMotion(shot, settings = {}) {
  const intensity = clamp(
    Number(shot?.intensity ?? settings.motionIntensity ?? DEFAULT_MOTION_INTENSITY),
    0,
    MAX_MOTION_INTENSITY,
  )
  const focus = normalizedFocus(shot?.focus ?? settings.motionFocus ?? DEFAULT_MOTION_FOCUS)
  // Keep the focal point away from crop edges; the overscan comes from the motion scale.
  const inset = intensity / (2 * (1 + intensity))
  return {
    intensity,
    focus: {
      x: clamp(focus.x, inset, 1 - inset),
      y: clamp(focus.y, inset, 1 - inset),
    },
  }
}

/**
 * Maps a shot to CSS custom properties that drive cinematic Ken Burns animations.
 *
 * Design rationale:
 * - Single-pass forward animation (fill-mode: forwards, iteration-count: 1) — real
 *   documentary cameras never ping-pong, so we don't loop back.
 * - Separate keyframe names per direction so CSS can assign the right start/end transforms.
 * - Three CSS custom properties control every transform so the keyframe body stays
 *   generic and the values come from JS:
 *     --kb-scale-from / --kb-scale-to   : scale at start and end of shot
 *     --kb-dx / --kb-dy                 : translate offset (percentage of element size)
 *       The sign convention is: positive dx moves the visible window right (image drifts left).
 * - Duration: longer for gentle shots (more time to savour the drift), shorter for punchy ones.
 *   Base 9 s at max intensity, 16 s at zero; a shot's own durationMs can override this later.
 * - Focus point shifts the transform-origin so the subject of interest stays anchored
 *   during zoom rather than the geometric centre.
 */
export function motionPreviewStyle(shot, settings = {}) {
  const { intensity, focus } = effectiveMotion(shot, settings)
  const direction = shot?.motion || 'zoom_in'

  // How much the image overscans beyond the visible frame (fractional).
  const overscan = intensity                              // e.g. 0.12 → 12 % margin
  const scaleFrom = 1
  const scaleTo   = 1 + overscan

  // Travel distance as percentage of the element's own size.
  // The pan travels the full overscan budget so the image exactly fills the frame at both ends.
  const travelPct = `${((overscan / (1 + overscan)) * 100).toFixed(2)}%`

  // Vertical pan drift follows the subject's vertical focus, with no drift at center.
  const panDy = `${((focus.y - 0.5) * overscan * 30).toFixed(2)}%`

  // Duration: fast-intensity shots feel snappier; slow ones linger cinematically.
  const durationS = (9 + (MAX_MOTION_INTENSITY - intensity) / MAX_MOTION_INTENSITY * 7).toFixed(1)

  // Focus x/y as percentage strings for transform-origin.
  const focusX = `${(focus.x * 100).toFixed(2)}%`
  const focusY = `${(focus.y * 100).toFixed(2)}%`

  // Direction drives which keyframe set to run; shared scale/translate vars do the work.
  let animation, kbScaleFrom, kbScaleTo, kbDx, kbDy
  if (direction === 'still') {
    animation = 'motion-still'
    kbScaleFrom = '0.99'
    kbScaleTo = '1.01'
    kbDx = '0%'
    kbDy = '0%'
  } else switch (direction) {
    case 'zoom_out':
      // Pull back: start zoomed in on focus point, coast out to full frame.
      animation    = 'motion-zoom-out'
      kbScaleFrom  = String(scaleTo)
      kbScaleTo    = String(scaleFrom)
      // Slight drift toward focus point as we pull out enhances the reveal feeling.
      kbDx = `${((focus.x - 0.5) * overscan * 30).toFixed(2)}%`
      kbDy = `${((focus.y - 0.5) * overscan * 30).toFixed(2)}%`
      break
    case 'pan_left':
      // Dolly left: image drifts right-to-left; keep mild scale to fill gaps at edges.
      animation    = 'motion-pan-left'
      kbScaleFrom  = String(1 + overscan * 0.4)
      kbScaleTo    = String(1 + overscan * 0.4)
      kbDx         = travelPct    // from +travel (right) to 0
      kbDy         = panDy
      break
    case 'pan_right':
      animation    = 'motion-pan-right'
      kbScaleFrom  = String(1 + overscan * 0.4)
      kbScaleTo    = String(1 + overscan * 0.4)
      kbDx         = travelPct    // from 0 to +travel (left)
      kbDy         = panDy
      break
    default:
      // zoom_in: push slowly toward the focus point.
      animation    = 'motion-zoom-in'
      kbScaleFrom  = String(scaleFrom)
      kbScaleTo    = String(scaleTo)
      kbDx = `${((focus.x - 0.5) * overscan * 30).toFixed(2)}%`
      kbDy = `${((focus.y - 0.5) * overscan * 30).toFixed(2)}%`
      break
  }

  return {
    '--motion-focus-x':    focusX,
    '--motion-focus-y':    focusY,
    '--motion-animation':  animation,
    '--motion-duration':   `${durationS}s`,
    '--kb-scale-from':     kbScaleFrom,
    '--kb-scale-to':       kbScaleTo,
    '--kb-dx':             kbDx,
    '--kb-dy':             kbDy,
  }
}
