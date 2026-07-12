export type CoordinatedRequest = {
  signal: AbortSignal;
  isCurrent: () => boolean;
};

export class LatestRequestCoordinator {
  begin(): CoordinatedRequest;
  cancel(): void;
}
