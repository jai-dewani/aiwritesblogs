---
title: "Beyond the Volcano Model: How Vectorized Query Execution Maximizes CPU Cache Locality"
date: "2026-08-05T11:05:25.000Z"
description: "An architectural breakdown of how modern analytical databases replace row-at-a-time iterator models with cache-conscious vectorized execution and morsel-driven parallelism."
---

## The Death of the Volcano Model

For thirty years, database execution engines relied on the Volcano iterator model published by Goetz Graefe in 1990. The design was clean and elegant. Every operator in a query execution plan implemented an interface with three primary methods: open, next, and close. When a query executed, the root node of the operator tree repeatedly called next on its children, pulling a single tuple up the plan tree until the source relation was exhausted.

In an era when database workloads were constrained by slow rotational magnetic disks and memory was measured in megabytes, the Volcano model was ideal. The overhead of a virtual function call per row sat hidden underneath the massive latency of mechanical disk reads. But hardware changed. Main memory expanded into gigabytes and terabytes, while CPU clock frequencies hit physical thermal barriers, forcing chipmakers to pivot toward multi-core parallelism, wider SIMD vector registers, and deep cache hierarchies.

When processing analytical queries on modern hardware, the Volcano iterator model collapses under its own structural weight. Pulling a single row at a time through a chain of ten relational operators turns execution into an endless cycle of virtual method dispatches, instruction cache misses, and pipeline stalls. Modern analytical engines like DuckDB, ClickHouse, and Snowflake abandoned row-at-a-time iterators in favor of vectorized batch execution.

## The Virtual Function Call Pipeline Penalty

To understand why traditional query execution fails on modern hardware, you have to look at what happens inside the CPU instruction pipeline during a Volcano next loop.

```
       [ HashJoin Operator ]
                 ^
                 | next() -> Tuple
       [ Filter (age > 30) ]
                 ^
                 | next() -> Tuple
    [ TableScan (users.dat) ]
```

When the HashJoin operator calls next on the Filter operator, the CPU executes an indirect jump through a virtual method table. The compiler cannot inline virtual function calls across dynamic operator boundaries. As a result, the processor must flush its pipeline registers, push function parameters onto the call stack, and jump to an address resolved at runtime.

When processing a single row, a virtual call costs maybe ten to fifteen CPU cycles. If your query reads five rows to render a user profile in an OLTP app, nobody cares. But when an analytical query scans one hundred million rows to compute an aggregate metric, those virtual call dispatches consume billions of clock cycles. The CPU spends more time managing stack frames and resolving vtable pointers than it does evaluating actual relational expressions.

Instruction cache thrashing makes the problem worse. Because control passes back and forth between different operator implementations for every single tuple, the CPU repeatedly evicts the instruction code of the TableScan operator to load the Filter operator, only to evict the Filter operator a microsecond later to load the HashJoin operator. The L1 instruction cache remains in constant churn.

Data layout compounds the architectural mismatch. Traditional relational databases use the N-ary Storage Model, storing complete rows contiguously in pages on disk and in RAM. A single 64-byte L1 cache line loaded from memory might contain a row's primary key, user ID, email address, password hash, and creation timestamp. If your analytical query only wants to evaluate the age column, eighty percent of the data fetched into the CPU cache line is useless garbage.

## Vectorized Execution and Primitive Batch Chunks

Vectorized execution redesigns the iterator contract from the ground up to solve memory stalls and call overhead simultaneously. Instead of returning a single tuple, the vectorized next method returns a columnar chunk containing arrays of values for a batch of rows, typically between 1024 and 4096 elements.

```
Volcano Iterator:   next() -> Row { id: 1, age: 32, score: 98.4 }
Vectorized Engine:  next() -> Chunk { age_vec: [32, 45, 19, ...], score_vec: [98.4, 88.1, 72.0, ...] }
```

By passing data in vectors, the cost of the virtual function call is amortized across the entire vector batch. Calling next once for a block of 2048 tuples reduces virtual call overhead by three orders of magnitude. The CPU enters an operator, processes thousands of elements in a tight, uninterrupted loop, and only then jumps to the next operator. The L1 instruction cache stays warm because the processor executes the exact same tight instruction loop thousands of times sequentially.

Vectorized execution relies on cache-conscious memory layouts. Within a batch chunk, data for each attribute resides in a contiguous, tightly packed array of primitive data types.

```c
// Volcano row-at-a-time loop
for (int i = 0; i < tuple_count; i++) {
    Tuple t = child->next();
    if (t.age > 30) {
        emit(t);
    }
}

// Vectorized primitive loop operating on contiguous memory
void filter_gt_int32(const int32_t* restrict input, uint16_t* restrict selection_vector, 
                     uint16_t count, int32_t threshold, uint16_t* restrict out_count) {
    uint16_t matches = 0;
    for (uint16_t i = 0; i < count; i++) {
        if (input[i] > threshold) {
            selection_vector[matches++] = i;
        }
    }
    *out_count = matches;
}
```

In the vectorized filter implementation, the input array is a raw pointer to contiguous 32-bit integers. As the CPU iterates through the loop, the hardware prefetcher recognizes the sequential memory access pattern and streams upcoming cache lines from main memory into L1 data cache well before the loop instructions demand them. Memory latency drops to near zero.

Modern compilers like GCC, Clang, and MSVC can automatically transform these tight primitive loops into SIMD vector instructions using AVX2 or AVX-512 register sets. A 512-bit vector register can load sixteen 32-bit integers in a single instruction cycle and evaluate the comparison operator across all sixteen values concurrently without branch mispredictions.

## Selection Vectors and Zero-Copy Intermediate State

Vectorized engines maintain tuple identity across operations without copying full column arrays by using selection vectors. A selection vector is an array of 16-bit integer offsets that tracks which elements in a chunk passed previous filter predicates.

When a filter operator evaluates `age > 30`, it does not allocate a new column vector or copy qualifying integers into a new array. It writes the array indices of qualifying elements into a selection vector. Subsequent downstream operators, such as a projection or hash join, accept both the original column vectors and the selection vector, reading only the indices listed in the selection array.

This design avoids intermediate memory allocations during query execution. Memory for column vectors and selection vectors is pre-allocated in reusable buffer pools when the query execution pipeline initializes. During runtime, operators pass existing buffers back and forth, eliminating heap allocation bottlenecks.

Handling variable-length data like text strings presents a challenge for tight vector layouts. Storing arbitrary strings directly inside fixed-size primitive arrays would break contiguous alignment and waste memory through padding. Modern vectorized engines handle strings using string heaps combined with inline dictionary views.

Each string entry in a vector batch is represented by a fixed 16-byte header. The first four bytes store the length of the string. If the string is twelve bytes or shorter, the remaining twelve bytes of the header store the string data directly inline. For short strings, evaluation operators read text directly out of the vector array without dereferencing pointers.

If the string exceeds twelve bytes, the first four bytes after the length field store a prefix of the string, while the final eight bytes hold a 64-byte pointer to the full payload stored in a separate memory pool page. When performing string comparisons, operators compare the inline four-byte prefix first. If the prefix does not match the search target, the operator rejects the string immediately without following the heap pointer, avoiding a costly out-of-cache memory dereference.

## Morsel-Driven Parallelism and Runtime Trade-offs

Parallelizing vectorized execution across multiple CPU cores requires a scheduling strategy that balances workload without triggering thread synchronization locks. Traditional query planners divided work by assigning fixed static data partitions to dedicated worker threads. If one partition contained skewed data or complex string values, one thread remained bottlenecked while other cores sat idle.

Morsel-driven parallelism solves thread imbalance by slicing data streams into uniform execution units called morsels, typically containing around 100,000 rows. A central runtime scheduler manages a dynamic lock-free queue of morsels. Worker threads pull morsels from the queue, execute the compiled vector pipeline over the batch, and return to the queue for another morsel upon completion.

Because each worker thread operates independently on its own local chunk buffers, thread synchronization is isolated entirely to the morsel dispatch queue and lock-free hash table insertions. Local thread state remains private, preventing cross-core cache invalidation traffic over the interconnect bus.

Vectorization represents a fundamentally different trade-off than runtime JIT code compilation, such as the LLVM machine code generation popularized by systems like HyPer. Code compilation attempts to eliminate iterator overhead by fusing operators together into a single monolithic loop, pushing rows through registers without writing intermediate results back to memory.

While compilation achieves incredible speed for CPU-bound queries with simple arithmetic, it introduces significant compilation latency before query execution can start. Vectorized execution offers predictable, instant execution starts while achieving ninety percent of the raw throughput of compiled code by keeping CPU execution units saturated through SIMD vector instructions and hardware prefetching.

By restructuring relational operators around array-based primitive chunks, selection vectors, and cache-aligned string headers, vectorized query engines turn hardware limitations into raw performance. Understanding these internal mechanics is what separates basic SQL tuning from building resilient, high-throughput data platforms.
