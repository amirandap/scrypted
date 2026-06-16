/**
 * RPC timeout utilities.
 *
 * Industry standard: every async I/O call must have a deadline.
 * Without timeouts, a single unresponsive plugin blocks callers
 * indefinitely, eventually starving the event loop.
 */

/** Per-method RPC timeout in milliseconds. */
export const RPC_TIMEOUT_MAP: Record<string, number> = {
    // Fast capability query — plugin should answer immediately.
    canMixin: 5_000,
    // Mixin object instantiation — allow plugin some startup time.
    getMixin: 15_000,
    // Snapshot — network round-trip to camera; use hap-nodejs grace minus margin.
    takePicture: 12_000,
    // Plugin ping — should respond within its own tick.
    ping: 10_000,
    // Generic "get parameter" handshake used during plugin startup.
    getParam: 10_000,
};

/** Default timeout for any RPC method not listed above. */
export const RPC_DEFAULT_TIMEOUT_MS = 30_000;

export class RpcTimeoutError extends Error {
    constructor(method: string, ms: number) {
        super(`RPC timeout: ${method} did not respond within ${ms}ms`);
        this.name = 'RpcTimeoutError';
    }
}

/**
 * Wraps a promise with a method-specific (or default) timeout.
 * Throws RpcTimeoutError if the deadline is exceeded.
 */
export function withRpcTimeout<T>(promise: Promise<T>, method: string, overrideMs?: number): Promise<T> {
    const ms = overrideMs ?? RPC_TIMEOUT_MAP[method] ?? RPC_DEFAULT_TIMEOUT_MS;

    let timeoutHandle: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
        timeoutHandle = setTimeout(() => reject(new RpcTimeoutError(method, ms)), ms);
    });

    return Promise.race([
        promise.then(v => { clearTimeout(timeoutHandle); return v; }),
        timeout,
    ]).catch(e => {
        clearTimeout(timeoutHandle);
        throw e;
    });
}
