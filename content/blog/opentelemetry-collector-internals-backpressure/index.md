---
title: "Inside the OpenTelemetry Collector: Pipelines, Memory Limiting, and Backpressure"
date: "2026-08-05T10:02:14.000Z"
description: "A deep technical dive into how the OpenTelemetry Collector processes telemetry streams, manages memory buffers, and handles backpressure under heavy load."
---

# Inside the OpenTelemetry Collector: Pipelines, Memory Limiting, and Backpressure

When telemetry pipelines fall over under heavy production load, engineers usually blame the backend database or network bottlenecks. Most of the time, the bottleneck sits right inside the telemetry proxy layer. The OpenTelemetry Collector operates as an out-of-process proxy, receiving spans, metrics, and logs from hundreds of application instances, running transformations, and fanning payloads out to backends like New Relic, Prometheus, or Jaeger.

Processing hundreds of megabytes per second without dropping data or crashing from out-of-memory errors requires a tight concurrency model. Under the hood, the collector relies on Go channels, memory guardrails, and synchronous error propagation to enforce end-to-end backpressure.

## Pipeline Architecture: Receivers, Processors, and Exporters

The collector config file looks like simple declarative YAML, but at startup the runtime builds a directed graph of Go routines and memory buffers. Telemetry data moves through three distinct components arranged in series: receivers, processors, and exporters.

```
+-----------------------------------------------------------------------+
|                       OTel Collector Pipeline                         |
|                                                                       |
|  +------------+     +-------------------+     +--------------------+  |
|  |  Receiver  | --> | Memory Limiter P. | --> | Batch Processor    |  |
|  +------------+     +-------------------+     +--------------------+  |
|         |                                                |            |
|     gRPC / HTTP                                   Go Channel Buffer   |
|         |                                                v            |
|  +--------------+                             +--------------------+  |
|  | OTLP Client  |                             | Queued Retry Exp.  |  |
|  +--------------+                             +--------------------+  |
|                                                          |            |
|                                                    gRPC / HTTP        |
|                                                          v            |
|                                               +--------------------+  |
|                                               |  Backend (NR/OTLP) |  |
|                                               +--------------------+  |
+-----------------------------------------------------------------------+
```

Receivers host network endpoints such as gRPC or HTTP listeners. When a client SDK pushes telemetry, the receiver unmarshals protocol buffer bytes into pdata, which is the internal memory representation used throughout the collector. Instead of reallocating byte buffers at every processing stage, pdata relies on pointer structures over underlying slice allocations. This design keeps garbage collection overhead manageable even during high throughput.

Once parsed, the receiver invokes the first processor's consume method synchronously on the connection's goroutine. If processors execute synchronously, execution stays on the receiver goroutine all the way to the exporter queue. That synchronous call chain is what enables backpressure to travel backward from exporter to client.

## Memory Limiter Processor: Guarding Against OOM

The memory limiter processor is the first critical line of defense. Without it, a traffic spike will blow past container memory limits and trigger a kernel OOM kill, wiping out buffered data instantly.

The memory limiter runs a background loop querying memory statistics at fixed tick intervals, typically set between 50ms and 100ms. It checks against two configurable thresholds, a soft limit and a hard limit. The soft limit defines when memory pressure is getting dangerous. When heap allocation or cgroup usage crosses the soft limit, the processor starts dropping incoming data or returning retryable errors to the receiver.

```go
func (ml *memoryLimiter) processTraces(ctx context.Context, td ptrace.Traces) (ptrace.Traces, error) {
    if ml.isHardLimitReached() {
        runtime.GC()
        if ml.isHardLimitReached() {
            return td, errMemoryLimitExceeded
        }
    }
    if ml.isSoftLimitReached() {
        if ml.shouldDropData() {
            return td, errMemoryLimitExceeded
        }
    }
    return td, nil
}
```

If memory consumption shoots past the soft limit and strikes the hard limit, the limiter forces an immediate garbage collection call using runtime.GC(). If memory stays above the hard limit after GC, the processor rejects all incoming payload calls immediately. The caller receives an error before any new objects get allocated on the heap, holding total memory steady until in-flight batches exit the pipeline.

## The Batch Processor: Micro-Batching Logic

Sending individual span or metric records over the network to remote endpoints creates crippling HTTP/2 framing overhead and kernel context switching. The batch processor aggregates individual pdata objects into larger batches before handing them off to exporters.

The batch processor operates on two concurrent triggers: size threshold and timer flush. It maintains an internal batch buffer protected by a mutex. As telemetry streams through the pipeline, each incoming item appends to the active batch and increments a counter. Once the item count reaches `send_batch_size`, the batch processor detaches the slice and sends it downstream immediately.

Traffic does not always arrive in clean bursts. If an application sends a few traces and goes quiet, waiting for `send_batch_size` would introduce unacceptable telemetry latency. To prevent stale data, the batch processor runs a `time.Ticker` set to a configured `timeout` duration. When the ticker fires, whatever partial batch currently resides in memory flushes down the pipeline regardless of count.

```go
type batchTraces struct {
    newItemItemChan chan ptrace.Traces
    timeout         time.Duration
    sendBatchSize   int
    maxBatchSize    int
}

func (b *batchTraces) start(ctx context.Context) {
    ticker := time.NewTicker(b.timeout)
    for {
        select {
        case td := <-b.newItemItemChan:
            b.addTraces(td)
            if b.currentSize() >= b.sendBatchSize {
                b.flush()
            }
        case <-ticker.C:
            if b.currentSize() > 0 {
                b.flush()
            }
        }
    }
}
```

Setting `send_batch_max_size` higher than `send_batch_size` gives the processor a safety valve. If a massive payload arrives all at once, `send_batch_max_size` forces the processor to split the incoming payload into multiple smaller chunks rather than pushing an oversized batch that causes upstream backend timeouts.

## Queued Retry Exporter: Asynchronous Buffering

After exiting the processors, telemetry enters the exporter layer. Exporters wrap their network transport code inside a `queued_retry` wrapper, which separates the pipeline's synchronous processing chain from network I/O.

The queued retry exporter places outgoing batches onto a bounded Go channel. A pool of worker goroutines consumes from this channel and executes the HTTP or gRPC calls to external backends like New Relic or OTLP endpoints. Decoupling receiver goroutines from export network latency ensures that slow network writes do not stall the main processing pipeline under normal conditions.

If a backend returns an ephemeral failure, such as HTTP 503 or gRPC Unavailable, the worker does not discard the payload. It applies exponential backoff with jitter and attempts to re-queue the batch. If the remote endpoint stays down and the internal Go channel buffer reaches capacity, the queue behavior depends on configuration. When the queue fills completely, attempts to push new batches onto the channel fail instantly.

```go
type queuedRetrySender struct {
    queue         chan request
    numWorkers    int
    retrySettings RetrySettings
}

func (q *queuedRetrySender) send(ctx context.Context, req request) error {
    select {
    case q.queue <- req:
        return nil
    default:
        // Channel is at capacity, backpressure triggers
        return errQueueIsFull
    }
}
```

## How Backpressure Propagates End-to-End

Backpressure works in reverse order of data flow. The exporter channel buffer acts as the primary signal generator. When the backend stalls or network bandwidth drops, worker goroutines cannot drain the channel fast enough, causing the channel buffer to fill to capacity.

Once the exporter queue fills up, `send` returns `errQueueIsFull` back to the batch processor. The batch processor catches this error, aborts its flush, and returns the error up to the memory limiter processor. The memory limiter sees the failure and passes it straight back to the receiver.

The receiver receives the Go error and converts it into protocol-specific status codes. For gRPC OTLP connections, the receiver returns status code `ResourceExhausted` or `Unavailable`. For HTTP OTLP connections, it returns HTTP 429 Too Many Requests or 503 Service Unavailable.

```
[ Client SDK ] --(OTLP gRPC)--> [ Receiver ] --(Sync Call)--> [ Memory Limiter ]
     ^                                                             |
     | Backpressure (gRPC ResourceExhausted)                       v
     +--------------------------------------------------- [ Batch Processor ]
                                                                   |
                                                                   v
     [ Backend ] <-- (gRPC Fail) -- [ Workers ] <-- (Channel Full) -- [ Exporter Queue ]
```

When client application SDKs receive a 429 or ResourceExhausted response, well-designed SDKs pause sending and buffer telemetry locally in app memory. This end-to-end chain prevents packet drop inside the collector while preserving collector memory stability.

## Pipeline Configuration Rules

YAML ordering in the collector configuration file directly dictates Go execution order. A common misconfiguration is putting `batch` before `memory_limiter` in the processor array. When `batch` sits first, telemetry gets stored in batch buffers before the memory limiter ever inspects memory pressure, nullifying memory cap protections.

Always place `memory_limiter` as the very first processor in every pipeline. Follow it with `batch`, and then place any transformation or attribute filtering processors afterward.

Worker pool size in the queued retry exporter should match available CPU resources and destination rate limits. Setting worker count too high leads to aggressive concurrent network pushes that trigger rate limiting on external APM platforms. Setting it too low causes unnecessary channel queue overflow even when network bandwidth is available.
