interface KayakMarkProps {
  /** Sizing and spacing classes. The mark fills whatever box it is given. */
  className?: string;
}

/**
 * The Kayak brand mark: a paddle behind a hull, on a rounded plate.
 *
 * The same shape is drawn by the desktop launcher's app icon, so the window a
 * user opens and the page inside it carry one mark. Colours are literal rather
 * than themed for the same reason -- a brand mark that changed colour between
 * light and dark mode would stop matching the icon in the dock.
 *
 * The hull is stroked in the plate colour, which reads as a gap where the
 * paddle passes behind it. That stroke is centred on the outline, so the
 * radii here are larger than the visible hull by half the stroke width.
 */
export function KayakMark({ className }: KayakMarkProps) {
  return (
    <svg viewBox="0 0 100 100" className={className} aria-hidden="true">
      <rect width="100" height="100" rx="24" fill="#1a73e8" />
      {/* Hull and paddle share a rotation, which is what keeps them perpendicular. */}
      <g transform="rotate(-45 50 50)">
        <rect x="17" y="45.5" width="66" height="9" rx="4.5" fill="#ffffff" />
        <ellipse
          cx="50"
          cy="50"
          rx="11.5"
          ry="39.5"
          fill="#ffffff"
          stroke="#1a73e8"
          strokeWidth="5.2"
        />
      </g>
    </svg>
  );
}
