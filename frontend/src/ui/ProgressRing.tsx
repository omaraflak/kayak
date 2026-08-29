import React from 'react';

/**
 * Progress as a ring, sized to sit inside a chip.
 *
 * Two modes, because there are two honest states: work that has not reported a
 * position yet spins, and work that has shows the fraction actually done. A ring
 * that creeps forward on a timer would be inventing a number.
 */
export const ProgressRing: React.FC<{ fraction?: number; className?: string }> = ({
  fraction,
  className = 'w-3 h-3',
}) => {
  const radius = 5;
  const circumference = 2 * Math.PI * radius;

  if (fraction === undefined) {
    return (
      <svg viewBox="0 0 14 14" className={`${className} animate-spin`} aria-hidden="true">
        <circle
          cx="7"
          cy="7"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={`${circumference * 0.3} ${circumference}`}
          opacity="0.9"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 14 14" className={`${className} -rotate-90`} aria-hidden="true">
      <circle cx="7" cy="7" r={radius} fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <circle
        cx="7"
        cy="7"
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.min(1, Math.max(0, fraction)))}
        className="transition-[stroke-dashoffset] duration-500 ease-out"
      />
    </svg>
  );
};
