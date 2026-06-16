/**
 * RPC error classification.
 *
 * Industry standard: errors must be categorized so callers can apply
 * appropriate retry/backoff/give-up strategies rather than treating
 * every failure the same way.
 *
 * Categories:
 *  - transient:  Peer is temporarily unavailable (loading, restarting).
 *                Caller should treat as passthrough and retry on next cycle.
 *  - permanent:  Peer explicitly rejected the request (e.g., "not a MixinProvider").
 *                Caller should remove the offending config entry; don't retry.
 *  - timeout:    Peer did not respond within the deadline.
 *                Caller should treat as transient but may also trigger health check.
 *  - killed:     Peer process has been explicitly killed / shut down.
 *                Caller should treat as transient — a new process will be started.
 *  - unknown:    Unrecognized error — log and treat conservatively (don't loop).
 */

export type RpcErrorClass = 'transient' | 'permanent' | 'timeout' | 'killed' | 'unknown';

/** Messages that indicate the remote method is not yet available but will be. */
const TRANSIENT_PATTERNS = [
    'not implemented',    // Plugin not yet registered its RPC params
    'ECONNREFUSED',
    'ECONNRESET',
    'EHOSTUNREACH',
    'EPIPE',
    'connection closed',
    'plugin disconnected',
] as const;

/** Messages that indicate the peer process has been explicitly shut down. */
const KILLED_PATTERNS = [
    'peer was killed',
    'RpcPeer has been killed',
    'plugin killed',
] as const;

/** Messages that indicate a permanent configuration mismatch. */
const PERMANENT_PATTERNS = [
    'not a MixinProvider',
    'not found',
    'does not exist',
    'is unavailable',
] as const;

export function classifyRpcError(e: unknown): RpcErrorClass {
    const msg = ((e instanceof Error) ? e.message : String(e)) ?? '';

    // Check most-specific first.
    if (e instanceof RpcTimeoutError || msg.includes('timeout') || msg.includes('ETIMEDOUT'))
        return 'timeout';

    for (const pattern of KILLED_PATTERNS) {
        if (msg.includes(pattern)) return 'killed';
    }
    for (const pattern of PERMANENT_PATTERNS) {
        if (msg.includes(pattern)) return 'permanent';
    }
    for (const pattern of TRANSIENT_PATTERNS) {
        if (msg.includes(pattern)) return 'transient';
    }

    return 'unknown';
}

/** Convenience: true when caller should treat as passthrough and not loop. */
export function isRpcPassthrough(e: unknown): boolean {
    const cls = classifyRpcError(e);
    return cls === 'transient' || cls === 'killed' || cls === 'timeout';
}

// Import here to avoid circular dependency (rpc.ts → rpc-timeout.ts → rpc-errors.ts).
import { RpcTimeoutError } from './rpc-timeout';
