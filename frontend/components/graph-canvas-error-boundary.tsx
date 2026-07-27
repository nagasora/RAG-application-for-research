"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

type GraphCanvasErrorBoundaryProps = {
  children: ReactNode;
  onRetry?: () => void;
  resetKey?: string;
};

type GraphCanvasErrorBoundaryState = {
  hasError: boolean;
};

/** Contains renderer failures without taking down the surrounding graph workspace. */
export class GraphCanvasErrorBoundary extends Component<GraphCanvasErrorBoundaryProps, GraphCanvasErrorBoundaryState> {
  state: GraphCanvasErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): GraphCanvasErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _errorInfo: ErrorInfo) {
    // Keep implementation details out of the researcher-facing UI.
  }

  componentDidUpdate(previousProps: GraphCanvasErrorBoundaryProps) {
    if (this.state.hasError && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  private retry = () => {
    this.setState({ hasError: false });
    this.props.onRetry?.();
  };

  render() {
    if (this.state.hasError) {
      return <section role="alert" aria-live="assertive" className="grid min-h-72 place-items-center rounded-2xl border border-[#e5c3bd] bg-[#fffaf8] p-6 text-center">
        <div>
          <p className="text-sm font-semibold text-[#7f332d]">知識グラフを表示できませんでした。</p>
          <p className="mt-2 text-xs leading-5 text-[#7a625d]">データを再読み込みして、もう一度お試しください。</p>
          <button type="button" onClick={this.retry} className="mt-4 rounded-full border border-[#b85b54] bg-white px-4 py-2 text-xs font-semibold text-[#7f332d] focus:outline-none focus:ring-2 focus:ring-[#b85b54] focus:ring-offset-2">
            再試行
          </button>
        </div>
      </section>;
    }

    return this.props.children;
  }
}
