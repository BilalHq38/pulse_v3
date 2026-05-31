export function delay(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export async function retryWithExponentialBackoff(
  operation,
  {
    retries = 2,
    baseDelayMs = 500,
    maxDelayMs = 4000,
    shouldRetry = () => true,
  } = {},
) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await operation(attempt);
    } catch (err) {
      lastError = err;
      if (attempt >= retries || !shouldRetry(err, attempt)) {
        throw err;
      }
      const delayMs = Math.min(maxDelayMs, baseDelayMs * (2 ** attempt));
      await delay(delayMs);
    }
  }
  throw lastError;
}

export function runAfterDelay(callback, ms) {
  const timerId = window.setTimeout(callback, ms);
  return () => window.clearTimeout(timerId);
}
