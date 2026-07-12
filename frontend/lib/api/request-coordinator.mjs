/** Coordinates replaceable requests and rejects stale completions. */
export class LatestRequestCoordinator {
  #generation = 0;
  #controller = null;

  begin() {
    this.#controller?.abort();
    const generation = ++this.#generation;
    const controller = new AbortController();
    this.#controller = controller;
    return {
      signal: controller.signal,
      isCurrent: () => generation === this.#generation && !controller.signal.aborted,
    };
  }

  cancel() {
    ++this.#generation;
    this.#controller?.abort();
    this.#controller = null;
  }
}
