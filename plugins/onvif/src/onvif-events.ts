import { EventEmitter } from 'events';
import { ObjectsDetected, ScryptedDeviceBase, ScryptedInterface } from "@scrypted/sdk";
import { OnvifCameraAPI, OnvifEvent } from "./onvif-api";
import { Destroyable } from "../../rtsp/src/rtsp";

// Maximum consecutive inner subscription failures before we surrender and let
// the outer listenLoop do a full restart (which has its own circuit breaker).
const MAX_INNER_FAILURES = 20;

// If no ONVIF event arrives within this window we assume the subscription has
// silently died and trigger an internal reconnect (without killing the outer
// listenLoop / video stream).
const SUBSCRIPTION_IDLE_TIMEOUT_MS = 3 * 60 * 1000; // 3 minutes

// Heartbeat emitted on the outer 'data' channel so the listenLoop's own 5-minute
// activityTimeout never fires while we're in the middle of an inner reconnect.
const HEARTBEAT_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

/**
 * Self-healing ONVIF event subscription.
 *
 * Returns a long-lived Destroyable whose lifetime is decoupled from individual
 * ONVIF PullPoint connections.  Network hiccups, subscription expiry, and
 * firmware-level max-subscription limits are all handled internally with
 * exponential back-off.  The outer RtspSmartCamera.listenLoop() never sees a
 * transient disconnect — it only gets an error after MAX_INNER_FAILURES
 * consecutive attempts have failed, at which point the outer circuit-breaker
 * takes over.
 *
 * @param thisDevice  The camera device (for sensor state updates + console log)
 * @param clientFactory  Called for every connection attempt — must return a
 *                       fresh OnvifCameraAPI each time.
 * @param motionTimeoutMs  How long motion stays "true" after the last event.
 */
export async function listenEvents(
    thisDevice: ScryptedDeviceBase,
    clientFactory: () => Promise<OnvifCameraAPI>,
    motionTimeoutMs = 30000,
): Promise<Destroyable> {
    let motionTimeout: NodeJS.Timeout;
    let binaryTimeout: NodeJS.Timeout;

    // ── outer stable emitter ──────────────────────────────────────────────────
    // The listenLoop attaches its 'data' / 'close' / 'error' handlers here.
    // We never emit 'close' or 'error' unless we've truly given up.
    const outerEmitter = new EventEmitter();
    outerEmitter.setMaxListeners(32);

    // ── reconnect state ───────────────────────────────────────────────────────
    let destroyed = false;
    let innerFailCount = 0;
    let currentClient: OnvifCameraAPI | undefined;
    let reconnectTimer: NodeJS.Timeout;
    let heartbeatTimer: NodeJS.Timeout;
    let idleTimer: NodeJS.Timeout;

    // ── motion / sensor helpers ───────────────────────────────────────────────
    const triggerMotion = () => {
        thisDevice.motionDetected = true;
        clearTimeout(motionTimeout);
        motionTimeout = setTimeout(() => thisDevice.motionDetected = false, motionTimeoutMs);
    };

    // ── heartbeat ─────────────────────────────────────────────────────────────
    // Keeps the outer listenLoop's 5-minute activityTimeout from firing while
    // we're silently reconnecting in the background.
    const scheduleHeartbeat = () => {
        clearTimeout(heartbeatTimer);
        if (destroyed) return;
        heartbeatTimer = setTimeout(() => {
            outerEmitter.emit('data', Buffer.from('heartbeat'));
            scheduleHeartbeat();
        }, HEARTBEAT_INTERVAL_MS);
    };

    // ── inner reconnect scheduler ─────────────────────────────────────────────
    const scheduleReconnect = (immediateMs = 0) => {
        if (destroyed) return;
        innerFailCount++;

        if (innerFailCount > MAX_INNER_FAILURES) {
            thisDevice.console.error(
                `ONVIF: subscription failed ${MAX_INNER_FAILURES} times, escalating to outer restart.`
            );
            destroyed = true;
            clearTimeout(heartbeatTimer);
            outerEmitter.emit('error', new Error('onvif: max inner reconnect attempts exceeded'));
            return;
        }

        // Exponential back-off: 10 s → 20 s → 40 s → … → 5 min cap
        const backoffMs = immediateMs || Math.min(10000 * Math.pow(2, Math.min(innerFailCount - 1, 5)), 300000);
        thisDevice.console.error(
            `ONVIF: reconnecting subscription in ${Math.round(backoffMs / 1000)}s ` +
            `(inner attempt ${innerFailCount}/${MAX_INNER_FAILURES})`
        );
        reconnectTimer = setTimeout(connectSubscription, backoffMs);
        scheduleHeartbeat(); // keep outer loop alive while waiting
    };

    // ── per-connection idle watchdog ──────────────────────────────────────────
    // Chinese cameras frequently let PullPoint connections go silent without
    // closing the TCP socket.  This catches that case.
    const resetIdleTimer = () => {
        clearTimeout(idleTimer);
        if (destroyed) return;
        idleTimer = setTimeout(() => {
            thisDevice.console.warn(
                `ONVIF: no events for ${SUBSCRIPTION_IDLE_TIMEOUT_MS / 60000} minutes, ` +
                `refreshing subscription silently...`
            );
            cleanupCurrentClient();
            scheduleReconnect(0);
        }, SUBSCRIPTION_IDLE_TIMEOUT_MS);
    };

    // ── cleanup helper ────────────────────────────────────────────────────────
    const cleanupCurrentClient = () => {
        clearTimeout(idleTimer);
        if (currentClient) {
            // Best-effort unsubscribe with 2-second timeout so a dead camera
            // doesn't stall the cleanup path.
            Promise.race([
                currentClient.unsubscribe() as Promise<unknown>,
                new Promise<void>(r => setTimeout(r, 2000)),
            ]).catch(() => {});
            try { currentClient.destroy(); } catch (e) {}
            currentClient = undefined;
        }
    };

    // ── connect / reconnect ───────────────────────────────────────────────────
    async function connectSubscription() {
        if (destroyed) return;
        clearTimeout(reconnectTimer);

        let client: OnvifCameraAPI;
        try {
            client = await clientFactory();
            currentClient = client;

            try { await client.supportsEvents(); } catch (e) { /* non-fatal */ }
            await client.createSubscription();

            const camEvents = client.listenEvents();

            // Forward raw data + structured events to the stable outer emitter
            camEvents.on('data', (xml: string) => {
                outerEmitter.emit('data', xml);
                resetIdleTimer();          // real traffic — reset the idle watchdog
                clearTimeout(heartbeatTimer); // don't need synthetic ping right now
                scheduleHeartbeat();       // but reschedule for later
            });

            camEvents.on('event', (event: OnvifEvent, className: string) => {
                outerEmitter.emit('event', event, className);
            });

            camEvents.on('onvifEvent', (topic: string, value: any) => {
                outerEmitter.emit('onvifEvent', topic, value);
            });

            // Success — reset failure counter and start idle watchdog
            innerFailCount = 0;
            thisDevice.console.log('ONVIF: subscription active');
            resetIdleTimer();
            scheduleHeartbeat();
        } catch (e: any) {
            thisDevice.console.error('ONVIF: subscription connect failed:', e?.message);
            if (client!) {
                try { client.destroy(); } catch (_) {}
                currentClient = undefined;
            }
            scheduleReconnect(0);
        }
    }

    // ── wire motion / sensor handlers to the stable outer emitter ────────────
    // These are registered once and survive all inner reconnects.
    outerEmitter.on('event', (event: OnvifEvent, className: string) => {
        if (event === OnvifEvent.MotionBuggy) {
            triggerMotion();
            return;
        }
        if (event === OnvifEvent.BinaryRingEvent) {
            thisDevice.binaryState = true;
            clearTimeout(binaryTimeout);
            binaryTimeout = setTimeout(() => thisDevice.binaryState = false, motionTimeoutMs);
            return;
        }
        if (event === OnvifEvent.MotionStart) {
            triggerMotion();
        }
        else if (event === OnvifEvent.MotionStop) {
            if (thisDevice.motionDetected)
                triggerMotion();
        }
        else if (event === OnvifEvent.AudioStart)
            thisDevice.audioDetected = true;
        else if (event === OnvifEvent.AudioStop)
            thisDevice.audioDetected = false;
        else if (event === OnvifEvent.BinaryStart)
            thisDevice.binaryState = true;
        else if (event === OnvifEvent.BinaryStop)
            thisDevice.binaryState = false;
        else if (event === OnvifEvent.Detection) {
            const d: ObjectsDetected = {
                timestamp: Date.now(),
                detections: [{ score: undefined, className }],
            };
            thisDevice.onDeviceEvent(ScryptedInterface.ObjectDetector, d);
        }
    });

    // ── initial connect ───────────────────────────────────────────────────────
    // If the very first attempt fails, still return the Destroyable — the inner
    // reconnect loop will keep trying in the background.
    await connectSubscription().catch(() => {});

    // ── stable Destroyable returned to listenLoop ─────────────────────────────
    const ret: Destroyable = {
        destroy() {
            destroyed = true;
            clearTimeout(motionTimeout);
            clearTimeout(binaryTimeout);
            clearTimeout(reconnectTimer);
            clearTimeout(heartbeatTimer);
            clearTimeout(idleTimer);
            cleanupCurrentClient();
            outerEmitter.removeAllListeners();
        },
        on(eventName: string | symbol, listener: (...args: any[]) => void) {
            outerEmitter.on(eventName, listener);
        },
        emit(eventName: string | symbol, ...args: any[]): boolean {
            return outerEmitter.emit(eventName, ...args);
        },
    };

    return ret;
}
