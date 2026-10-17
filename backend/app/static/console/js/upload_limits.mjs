export const MAX_VIDEO_BYTES = 1536 * 1024 * 1024;
export const MAX_VIDEO_LABEL = '1.5 GB';

export function exceedsVideoLimit(size) {
  return Number(size) > MAX_VIDEO_BYTES;
}
