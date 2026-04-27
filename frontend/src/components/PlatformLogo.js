import { useState } from 'react';
import { Link } from 'react-router-dom';
import logoSrc from './logo.bmp';

export default function PlatformLogo({
  to = '/',
  text = 'Pulse Engine',
  textColor = '#0f172a',
  imageWidth = 46,
  fontSize = 18,
  fontWeight = 700,
  gap = 11,
}) {
  const [hovered, setHovered] = useState(false);

  const content = (
    <>
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '7px 11px',
          borderRadius: 13,
          background: '#ffffff',
          border: '1px solid rgba(148,163,184,0.18)',
          boxShadow: '0 6px 18px rgba(15,23,42,0.08)',
        }}
      >
        <img
          src={logoSrc}
          alt="Pulse Engine"
          style={{
            display: 'block',
            width: imageWidth,
            height: 'auto',
            transition: 'transform 0.45s cubic-bezier(0.34,1.56,0.64,1)',
            transform: hovered ? 'rotate(180deg)' : 'rotate(0deg)',
          }}
        />
      </span>
      <span
        style={{
          fontSize,
          fontWeight: hovered ? 800 : fontWeight,
          color: hovered ? '#3b82f6' : textColor,
          letterSpacing: hovered ? '-0.05em' : '-0.04em',
          transition: 'color 0.25s ease, font-weight 0.2s ease, letter-spacing 0.25s ease',
        }}
      >
        {text}
      </span>
    </>
  );

  const sharedStyle = { display: 'inline-flex', alignItems: 'center', gap, textDecoration: 'none' };

  if (!to) {
    return (
      <div style={sharedStyle} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
        {content}
      </div>
    );
  }

  return (
    <Link to={to} style={sharedStyle} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      {content}
    </Link>
  );
}
