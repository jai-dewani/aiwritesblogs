---
title: "T-Digest Mechanics: How Observability Engines Calculate Percentiles at Scale"
date: "2026-08-07T12:11:20.323Z"
description: "An in-depth technical exploration of the T-Digest data structure, scale function constraints, centroid merging algorithms, and distributed aggregation in high-throughput APM telemetry systems."
---

Calculating exact percentiles like p95, p99, and p99.9 across millions of latency samples per second presents a severe computational bottleneck in modern observability platforms. Storing every raw metric point in memory demands unbounded space. Sorting a buffer of size N requires O(N log N) time complexity, making real-time telemetry processing completely infeasible at scale. Exact quantile computation in a streaming model with bounded memory is mathematically impossible according to the Munro-Paterson theorem.

Monitoring engines cannot rely on simple moving averages or standard deviations because latency distributions in distributed systems are heavily skewed, exhibiting long tails rather than normal distributions. Averaging percentiles across multiple nodes produces mathematically invalid results. A telemetry platform processing metric streams must employ a probabilistic data structure that provides tight error bounds, particularly at the extreme tails, while maintaining constant memory consumption and low insertion overhead.

### Centroids and the T-Digest Architecture

The T-Digest data structure solves this problem by compressing a stream of floating-point numbers into an ordered sequence of discrete clusters known as centroids. Each centroid stores two values: a mean value representing the center of the cluster, and a weight representing the count of data points assigned to that cluster.

Rather than maintaining uniform bin widths like traditional fixed-width histograms, the T-Digest dynamically adjusts centroid sizes based on their position in the cumulative distribution function. Near the median where the quantile q equals 0.5, centroids are allowed to accumulate a large total weight, sacrificing fine-grained accuracy where relative error matters less. Near the extreme edges of the distribution where quantile q approaches 0 or 1, centroids are restricted to small maximum weights. This architectural constraint enforces extreme precision where performance engineers need it most, such as measuring p99.9 tail latencies.

### Scale Functions and Weight Capacity

The core mathematical driver of the T-Digest is its scale function, denoted as k(q, delta), where q is the quantile value between 0 and 1, and delta is the compression factor governing the trade-off between memory usage and accuracy. A higher compression factor permits more centroids, increasing memory allocation while reducing quantile error.

The scale function maps a quantile q to an index k. The maximum weight that a centroid can hold is constrained by the condition that the span in k-space across any single centroid must not exceed 1. Under standard scale functions like k_1, defined using the arcsine function, the derivative approaches infinity near q = 0 and q = 1. As a consequence, the permitted maximum weight for centroids near the boundary shrinks toward 1.

A centroid located at q = 0.999 might contain only a single data point, guaranteeing exact retention of extreme outliers, while a centroid near q = 0.5 might hold tens of thousands of data points without violating the scale constraint.

### Merging Mechanics and Streaming Ingestion

Adding individual data points directly into an ordered list of centroids using binary search can lead to frequent rebalancing overhead. High-performance implementations utilize a two-stage buffering mechanism. Incoming floating-point latency values are written directly into an unbuffered flat array in memory.

When this incoming buffer fills up, the execution pipeline triggers a merge operation. The algorithm sorts the raw incoming buffer alongside the existing array of centroids. It then iterates sequentially through the merged stream, accumulating points into a current building centroid.

As the building centroid accumulates weight, the algorithm continuously checks if adding the next item would cause the centroid to exceed the maximum permitted weight defined by the scale function for its current cumulative quantile. If the threshold is exceeded, the algorithm finalizes the current centroid, writes it to a new ordered array, and instantiates a new centroid with the next data point. This batch-merge strategy replaces individual point insertions with vectorized linear scans and cache-friendly contiguous array operations.

### Distributed Aggregation and Monoid Properties

Observability platforms aggregate metrics across hundreds of distributed collector pods before servicing query engine requests. A foundational property of the T-Digest is that it forms an additive monoid. Two or more distinct T-Digest structures constructed independently on separate servers can be merged into a single consolidated T-Digest without requiring access to the original raw data points.

To combine multiple digests, the query server extracts all centroids from each input digest, sorts them by their mean values, and passes them through the standard sequential merging algorithm. The scale function constraints guarantee that the resulting unified digest retains the same theoretical error bounds as a digest built from a single monolithic stream. This property enables APM query engines to perform map-reduce aggregations over distributed time windows with minimal CPU overhead.

### Memory Layout and Cache Considerations

In low-level runtimes, a T-Digest structure is typically represented using parallel, contiguous primitive arrays rather than pointer-heavy object graphs. Separate 64-bit floating-point arrays for centroid means and centroid weights ensure linear memory layout, enabling SIMD vectorization during scale function computations and minimizing CPU cache misses.

A typical T-Digest configured with a compression parameter delta = 100 allocates roughly 100 to 200 centroids, taking up less than 3 kilobytes of memory. Because the data structure consumes bounded, predictable memory regardless of whether it processes ten thousand or ten billion events, telemetry collectors can maintain hundreds of thousands of active metric streams concurrently without triggering out-of-memory errors or runtime garbage collection stalls.