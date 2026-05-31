import React from 'react';
import BrandedLoader from '@/components/ui/BrandedLoader';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, showFallback: false };
    this.fallbackTimer = null;
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error, showFallback: false };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
    this.fallbackTimer = window.setTimeout(() => {
      this.setState({ showFallback: true });
    }, 3000);
  }

  componentWillUnmount() {
    if (this.fallbackTimer) window.clearTimeout(this.fallbackTimer);
  }

  render() {
    if (this.state.hasError) {
      if (!this.state.showFallback) return <BrandedLoader />;
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', minHeight: '100vh', padding: '2rem',
          fontFamily: 'sans-serif'
        }}>
          <h2 style={{ color: '#1a1a1a', marginBottom: '0.5rem' }}>Something went wrong</h2>
          <p style={{ color: '#666', marginBottom: '1.5rem' }}>
            An unexpected error occurred. Please refresh the page to try again.
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '0.75rem 1.5rem', background: '#2563eb', color: '#fff',
              border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '1rem'
            }}
          >
            Refresh Page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
