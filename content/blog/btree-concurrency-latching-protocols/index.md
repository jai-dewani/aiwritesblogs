---
title: "Concurrency Latching Protocols for High-Throughput B-Tree Node Splits and Merges"
date: "2026-06-10T09:00:00.000Z"
description: "An architectural examination of B-tree concurrent latching protocols, covering lock coupling, Lehman-Yao right-links, optimistic latching, and structural modification handling."
---

# Concurrency Latching Protocols for High-Throughput B-Tree Node Splits and Merges

Database storage engines rely on B-trees or their variants to maintain sorted data on disk and in memory. When thousands of concurrent threads query, insert, and delete records simultaneously, the storage engine must guarantee structural integrity of the tree without turning the root node into a bottleneck. Transactions use locks to maintain isolation guarantees across logical statements, but database kernels use latches to protect in-memory data structures like page buffers and tree nodes during short physical reads and writes.

Handling structural modification operations like page splits and page merges concurrently is one of the hardest problems in database engine design. If a reader thread traverses a parent pointer down to a child node while a writer thread is actively splitting that child and moving half its keys to a newly allocated page, the reader can end up seeing inconsistent state or missing a key entirely. Fixing this with coarse table-level or tree-level locks destroys parallel throughput. Storage engine architects have spent decades refining latching protocols to maximize concurrency during node splits and merges.

## Lock Coupling and the Overhead of Latch Crabbing

The standard baseline protocol for concurrent B-tree traversal is lock coupling, often called latch crabbing. The core idea is simple. A thread descending the tree acquires a latch on a child node before releasing the latch on its parent. This hand-over-hand latching guarantees that no concurrent thread can modify the path immediately ahead of the reader or writer.

```
Read Traversal (Latch Crabbing):

  [ Node A (Read Latch) ]
            |
            v  1. Acquire Read Latch on B
  [ Node B (Read Latch) ]
            |
            v  2. Release Read Latch on A
  [ Node C (Read Latch) ]
```

For read operations, latch crabbing requires acquiring shared latches down the branch. Shared latches allow multiple concurrent readers to inspect the same page header and slot array. As soon as a reader inspects Node A, calculates the child page slot for Node B, and successfully acquires a shared latch on Node B, it drops the shared latch on Node A.

For write operations, latch crabbing becomes far more restrictive. An insertion requires acquiring exclusive latches down the path to prevent readers or other writers from seeing partial writes. If a writer naive crabbed exclusive latches from the root all the way down to a leaf page, every write operation would block the root page, reducing the entire database to single-threaded write execution.

To mitigate root bottlenecking, write crabbing uses a safety check heuristic. When a writer descends the tree, it acquires an exclusive latch on the parent, then acquires an exclusive latch on the child. Once the writer inspects the child node and verifies that the child is safe, meaning it has enough free space to absorb an insertion without splitting, the writer immediately releases all exclusive latches held on ancestor nodes. If the child page is full, the child is deemed unsafe because inserting a key will trigger a page split. When a child is unsafe, the writer retains the exclusive latch on the parent and all ancestors up to the highest unsafe node.

```
Write Crabbing with Unsafe Nodes:

  [ Root (Exclusive Latch) ] ---> Unsafe (Full)
            |
            v
  [ Internal (Exclusive Latch) ] ---> Safe (Has space)
            |
            v  (Release Root Latch here)
  [ Leaf (Exclusive Latch) ] ---> Unsafe (Full, triggers split)
```

Write crabbing works reasonably well when page splits are rare. However, under high write workloads, cascaded splits propagate up the tree. When a leaf splits, it requires inserting a separator key into its parent node. If that parent is also full, the split cascades upward to the grandparent, potentially reaching the root. Holding exclusive latches along an entire ancestor path stalls every concurrent reader attempting to traverse through those upper-level nodes.

## Lehman-Yao B-Link Trees and Right-Links

Lehman and Yao proposed a breakthrough protocol in 1981 that eliminated the need to hold parent latches while splitting child nodes. The Lehman-Yao structure, known as a B-link tree, adds two crucial metadata fields to every page header: a right-link pointer pointing directly to the right sibling page, and a high-key value storing the highest possible key stored in that page.

```
Lehman-Yao Node Layout and Right-Link:

  +--------------------------------------------------------+
  | Node Page Header                                       |
  | High Key: 100  | Right Link: Page ID 0x42                |
  +--------------------------------------------------------+
  | Slot Array / Keys: [12, 45, 67, 89, 99]               |
  +--------------------------------------------------------+
                             |
                             v
               +---------------------------+
               | Sibling Page (0x42)       |
               | High Key: 200             |
               +---------------------------+
```

In a standard B-tree, if a page splits into two pages (Left Page and Right Page), a reader descending from the parent might follow a pointer to Left Page. If the search key was moved to Right Page during the split, and the parent page has not yet been updated with the new separator key, a standard reader will fail to find the key.

The Lehman-Yao right-link changes this invariant completely. During a search, a reading thread acquires a shared latch on a page, inspects its high-key, and compares the search key against that high-key. If the search key is strictly greater than the page high-key, the reader knows that a concurrent split pushed the target key to the right sibling. Instead of re-traversing from the root or failing, the reader follows the right-link to the right sibling page, acquiring a shared latch on the sibling and releasing the current page latch.

```c
struct btree_page {
    uint32_t page_id;
    uint32_t right_link;
    int64_t high_key;
    uint16_t num_keys;
    int64_t keys[128];
    uint32_t children[129];
};

btree_page* traverse_blink_tree(btree_page* root, int64_t target_key) {
    btree_page* curr = root;
    acquire_read_latch(curr);

    while (!is_leaf(curr)) {
        // Step 1: Check if target key migrated right due to a split
        while (curr->high_key != UNBOUNDED && target_key > curr->high_key) {
            uint32_t next_id = curr->right_link;
            btree_page* next_page = get_page_by_id(next_id);
            acquire_read_latch(next_page);
            release_read_latch(curr);
            curr = next_page;
        }

        // Step 2: Find child index within current node
        uint32_t child_id = find_child_slot(curr, target_key);
        btree_page* child_page = get_page_by_id(child_id);

        // Step 3: Descend without lock coupling on parent!
        acquire_read_latch(child_page);
        release_read_latch(curr);
        curr = child_page;
    }

    return curr;
}
```

Because readers can recover from missing child updates by traversing right-links, writers splitting a node do not need to acquire exclusive latches on the parent node concurrently with the child split.

When a writer splits a full node in a Lehman-Yao tree, it executes the split in distinct phases. First, it allocates a new page for the right sibling and populates it with the upper half of the keys. Second, it sets the right-link of the new page to the original page's old right-link. Third, it updates the original page's high-key to match the median key and sets its right-link to point to the newly allocated right sibling. Fourth, the writer releases the exclusive latch on the original page.

At this point, the split is physically committed and accessible to concurrent threads via the right-link, even though the parent node has not been touched yet. Finally, the writer ascends to the parent node, acquires an exclusive latch on the parent, and inserts the separator key pointing to the new sibling page. If the parent page itself is full, the writer splits the parent using the exact same decoupled protocol.

By decoupling the node split from the parent separator insertion, Lehman-Yao reduces latch hold times by orders of magnitude. Root latches are only held for the duration of a single page update rather than across an entire recursive tree traversal.

## Optimistic Lock Coupling and In-Memory Storage Engines

While Lehman-Yao resolves structural propagation bottlenecks, modern main-memory databases like HyPer, LeanStore, and the Adaptive Radix Tree (ART) face another hurdle: cache line bouncing caused by traditional reader-writer latches.

Standard read latches modify memory. When a thread executes an atomic fetch-and-add instruction to increment a shared latch reader count, CPU core cache coherence protocols (like MESI) invalidate that cache line across every other CPU socket and core. If dozens of threads are simultaneously reading the root node, atomic operations on the latch header cause severe memory bus contention.

Optimistic Lock Coupling (OLC) eliminates cache line invalidation for reader threads by using version-based optimistic validation. Each node header contains a 64-bit word that encodes both a version counter and a single write-latch bit.

```
64-bit Node Version Header:

  +-------------------------------------------------------+
  | Version Counter (Bits 0-62)    | Write Latch Bit (Bit 63)|
  +-------------------------------------------------------+
```

When a writer locks a page, it uses an atomic compare-and-swap or fetch-and-add instruction to set the write-latch bit and increment the version counter. When the writer finishes modifying the node, it unlocks the page by clearing the write-latch bit and incrementing the version counter again. Thus, an even version counter indicates a clean, unlatched page, while an odd version counter indicates an active write latch.

Readers do not execute any atomic write instructions. Instead, a reader reads the 64-bit version counter, performs its memory reads from the node layout, and then reads the version counter a second time to validate the operation.

```c
uint64_t read_node_version(volatile uint64_t* version_ptr) {
    uint64_t v = *version_ptr;
    // Spin if write bit (LSB) is set
    while (v & 1UL) {
        _mm_pause();
        v = *version_ptr;
    }
    return v;
}

bool validate_node_version(volatile uint64_t* version_ptr, uint64_t old_version) {
    // Memory fence to prevent compiler/CPU reordering of payload reads after validation
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return *version_ptr == old_version;
}

btree_node* traverse_optimistic(btree_node* root, int64_t target_key) {
    btree_node* curr = root;

retry_traversal:
    uint64_t v_curr = read_node_version(&curr->version);

    while (!curr->is_leaf) {
        btree_node* child = get_child_pointer(curr, target_key);
        uint64_t v_child = read_node_version(&child->version);

        // Validate parent version before stepping down
        if (!validate_node_version(&curr->version, v_curr)) {
            goto retry_traversal; // Concurrent split/modification detected, restart
        }

        curr = child;
        v_curr = v_child;
    }

    return curr;
}
```

If the node version remained unchanged between the initial read and the post-read validation, the reader guarantees that no writer modified the node header, keys, or pointers while it was inspecting them. If the version changed or if the write-latch bit was set during validation, the reader immediately drops its intermediate calculations and restarts the traversal from a valid ancestor or the root.

Optimistic lock coupling allows millions of concurrent read queries per second to pass through internal B-tree nodes without modifying a single bit of shared memory header state. Shared cache lines remain in the Shared state within L1/L2 caches, eliminating bus traffic entirely during read sweeps.

## Handling Structural Modifications and Merges under Deletions

While node splits push new keys rightward, deletion workloads create empty or underfull pages that must be merged to maintain the logarithmic search height of the B-tree. Merging nodes is inherently more dangerous and complex than splitting nodes.

Splitting a node requires latches on two pages: the original page and its newly created right sibling. Node merging requires acquiring exclusive latches on three distinct nodes simultaneously: the underfull page, its left (or right) sibling page, and their mutual parent page.

```
Three-Node Merge Latching Problem:

         [ Parent Node (Exclusive Latch) ]
                 /             \
                v               v
    [ Left Sibling ]  <---  [ Underfull Page ]
   (Exclusive Latch)       (Exclusive Latch)
```

The fundamental challenge with node merges is deadlock prevention. Standard B-tree traversals acquire latches top-to-bottom (root to leaf) and left-to-right (following right-links). A page merge requires locking a left sibling from the context of an underfull right page, which violates the strict left-to-right latch acquisition order. If Thread A is traversing left-to-right while Thread B is executing a node merge right-to-left, a classic AB-BA latch deadlock occurs.

To safely execute node merges without deadlocking, high-performance database engines use restartable try-latching or background structural consolidation.

In try-latching protocols, a thread initiating a node merge attempts to acquire an exclusive latch on the left sibling using a non-blocking try-lock call (such as `pthread_rwlock_trywrlock`). If the try-lock succeeds, the thread holds all three required latches and proceeds with moving keys, updating the parent separator key, and reclaiming the empty page. If the try-lock fails because another thread holds a latch on the left sibling, the merging thread drops all its currently held exclusive latches, aborts the merge operation, and defers page consolidation.

Modern storage architectures like LeanStore go a step further by decoupling page merges from user transaction threads entirely. When a record deletion leaves a page below its target fill factor (for example, under 50 percent full), the page is marked with a dirty underfull flag in its header. A dedicated background epoch-based garbage collection thread scans underfull pages asynchronously.

The background task batches structural modifications, acquiring latches in strict top-to-bottom, left-to-right order during periods of low activity or via epoch quiescence. By preventing user write transactions from executing synchronous node merges on the hot critical path, storage engines avoid complex lock-ordering deadlocks and eliminate tail-latency spikes on DELETE queries.

## Memory Reclaim and Epoch-Based Reclamation

A subtle bug in concurrent latching protocols involves memory reclamation. Suppose a writer thread merges an underfull page, unlinks it from the parent node and sibling right-links, and frees the page memory back to the operating system or buffer pool allocator. What happens if a concurrent reader thread is currently traversing that exact page using optimistic validation?

Without proper memory protection, the reader thread will read freed memory, resulting in a segmentation fault or silent data corruption (use-after-free).

To solve this, concurrent database engines pair latching protocols with Epoch-Based Reclamation (EBR) or Read-Copy-Update (RCU) memory management.

```
Epoch-Based Reclamation Pipeline:

  Global Epoch: E_10

  Thread 1 (Reader): Active in Epoch E_10
  Thread 2 (Writer): Unlinks Page X in Epoch E_10
                     Adds Page X to E_10 Retirement Garbage List

  ... Time Passes, Thread 1 finishes read ...

  Global Epoch advances to E_12
  Safe to free E_10 Garbage List (No active readers remain in E_10)
```

In Epoch-Based Reclamation, the system maintains a global epoch counter. Every worker thread registers its active status and current epoch when entering a B-tree operation. When a page split or merge physically unlinks a node from the tree structure, the node memory is not immediately freed. Instead, the node pointer is attached to a retirement list tagged with the current global epoch.

The memory reclamation subsystem monitors active threads across epochs. A retired page is only returned to the buffer allocator or `free()` after all threads that started execution in or before the retirement epoch have finished their operations. This guarantees that no reader thread will ever experience a use-after-free error when reading node data optimistically, making lock-free and optimistic B-tree traversals safe at high concurrency.

## Combining the Modern Latching Stack

Building a high-throughput storage engine requires layering these concurrency protocols into a cohesive stack. Optimistic lock coupling handles read traversals down the internal nodes of the tree, ensuring zero memory writes on shared cache lines. Lehman-Yao right-links handle concurrent page splits at the leaf level, allowing writers to commit node partitions without holding exclusive latches up the parent chain. Epoch-based reclamation protects concurrent readers from page deallocations during asynchronous background page merges.

When these components align, B-tree traversal performance scales linearly across modern NUMA multi-socket servers, allowing storage engines to process millions of concurrent transactions per second without descending into lock contention or cache thrashing.
