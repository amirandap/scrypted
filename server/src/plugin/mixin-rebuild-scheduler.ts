/**
 * MixinRebuildScheduler
 *
 * Industry standard: decouple state-change notifications from the business
 * logic they trigger. The original code called rebuildMixinTable() synchronously
 * inside notification handlers, creating recursive loops and RPC floods.
 *
 * This scheduler:
 *  1. Accepts rebuild requests from anywhere (invalidateMixins, notifications, etc.)
 *  2. Deduplicates: requesting the same device ID twice before the rebuild runs
 *     results in exactly one rebuild.
 *  3. Debounces: waits one event-loop tick (setImmediate) before processing, so
 *     multiple synchronous callers in the same tick are batched.
 *  4. Serialises: processes one device at a time with a setImmediate yield between
 *     each — this keeps I/O callbacks (plugin pings) able to fire throughout the
 *     rebuild cycle.
 *  5. Guards re-entry: if a rebuild for device A triggers a notification that
 *     schedules another rebuild for A, the second request is queued for the NEXT
 *     processing cycle, not inserted mid-flight.
 */

import type { ScryptedRuntime } from '../runtime';

export class MixinRebuildScheduler {
    /** IDs waiting to be processed in the current or next batch. */
    private pending = new Set<string>();
    /** IDs currently being processed (guard against mid-flight re-enqueue). */
    private inProgress = new Set<string>();
    /** Whether a processing cycle has been scheduled but not yet started. */
    private scheduled = false;

    constructor(private readonly scrypted: ScryptedRuntime) {}

    /**
     * Request that a device's mixin table be rebuilt.
     * Safe to call from synchronous notification handlers — the actual rebuild
     * is always deferred to the next I/O tick.
     */
    schedule(deviceId: string): void {
        // Ignore devices already being rebuilt in the current cycle;
        // they will be re-queued if they self-schedule during their own rebuild.
        if (this.inProgress.has(deviceId)) {
            // Re-add to pending so it runs again in the NEXT cycle.
            this.pending.add(deviceId);
            return;
        }

        this.pending.add(deviceId);

        if (!this.scheduled) {
            this.scheduled = true;
            setImmediate(() => this.process());
        }
    }

    private process(): void {
        this.scheduled = false;

        if (this.pending.size === 0)
            return;

        // Snapshot and clear the pending set so any rebuilds triggered DURING
        // processing are collected into the next batch, not this one.
        const batch = [...this.pending];
        this.pending.clear();

        // Process one device at a time, yielding to I/O between each.
        // This prevents the combined flood of canMixin/getMixin RPCs from
        // overwhelming plugin processes and starving their ping callbacks.
        const processNext = async () => {
            const deviceId = batch.shift();
            if (!deviceId) {
                // Batch finished. If new requests arrived during processing, run them.
                if (this.pending.size > 0) {
                    this.scheduled = true;
                    setImmediate(() => this.process());
                }
                return;
            }

            this.inProgress.add(deviceId);
            try {
                const proxyPair = this.scrypted.devices[deviceId];
                if (proxyPair?.handler) {
                    proxyPair.handler.rebuildMixinTable();
                }
            }
            catch (e) {
                console.error(`MixinRebuildScheduler: error rebuilding ${deviceId}`, e);
            }
            finally {
                this.inProgress.delete(deviceId);
            }

            // Yield to I/O phase between devices so plugin pings can fire.
            setImmediate(processNext);
        };

        processNext();
    }
}
