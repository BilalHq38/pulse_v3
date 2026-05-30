import React from 'react';

export default class GlobalErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    // Keep the browser console useful without exposing application state in the UI.
    console.error('Unhandled frontend render error', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
          <section className="max-w-md rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
            <h1 className="text-xl font-semibold text-slate-900">Something went wrong</h1>
            <p className="mt-2 text-sm text-slate-600">Reload the page to continue. If the problem persists, contact support.</p>
            <button
              className="mt-5 rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
              onClick={() => window.location.reload()}
              type="button"
            >
              Reload page
            </button>
          </section>
        </main>
      );
    }

    return this.props.children;
  }
}
