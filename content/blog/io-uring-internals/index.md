---
title: "Inside io_uring: Architecture, Ring Buffers, and Zero-Syscall Async I/O"
date: "2026-08-04T15:00:09.000Z"
description: "An architectural deep dive into Linux io_uring lockless ring buffers, kernel submission polling, fixed resource registration, and async kernel work queues."
---

# Inside io_uring: Architecture, Ring Buffers, and Zero-Syscall Async I/O

## The Historical Failure of Linux Async I/O

Linux had system calls like io_submit and aio_read for a long time, but Linux native asynchronous I/O was deeply flawed. Native POSIX and Linux AIO only worked for unbuffered disk operations configured with the O_DIRECT flag. If you attempted to read or write a file on standard file systems like ext4 or xfs without O_DIRECT, the kernel silently fell back to blocking execution on page cache misses. File system metadata modifications, inode lock contention, and page allocations converted what developers assumed was non-blocking I/O back into synchronous context switches. Completion handling also required dedicated system calls to collect results, creating heavy context switch overhead under high IOPS demands.

Jens Axboe built io_uring to resolve these structural limitations. Instead of issuing system calls for every single I/O submission or completion, io_uring establishes a pair of ring buffers residing in memory shared directly between user space and kernel space.

## Ring Buffer Architecture and Memory Mapping

Two primary ring buffers form the operational core of io_uring. The Submission Queue handles requests passed from user applications to the kernel, while the Completion Queue receives completion event notifications pushed by the kernel back to user space. You initialize these structures by calling the io_uring_setup system call. The kernel creates the underlying rings and returns a file descriptor. The application then issues mmap system calls using offset constants to map the ring structures and entry arrays into the application process virtual memory space.

The memory layout separates ring index management from the actual Submission Queue Entry array. The Submission Queue ring control structure contains a head offset, a tail offset, a ring mask, and an array of integer indices. These indices point into a contiguous array of struct io_uring_sqe elements. This level of indirection allows the kernel to consume submission entries in flexible orderings if necessary. The Completion Queue layout is simpler. The Completion Queue Entry array directly contains the completion status, error codes, user data identification cookies, and result flags.

```
User Space                           Kernel Space
+-------------------------------+   +-------------------------------+
|  Submission Queue (SQ)        |   |  Kernel Ring Controller       |
|  - head (kernel updates)      |   |  - Reads SQ tail pointer      |
|  - tail (user updates)        |==>|  - Consumes SQEs from array   |
|  - sqes array indices         |   |  - Executes async I/O         |
+-------------------------------+   +-------------------------------+
                                                    |
+-------------------------------+                   |
|  Completion Queue (CQ)        |                   v
|  - head (user updates)        |<==|  Writes CQEs to ring          |
|  - tail (kernel updates)      |   |  - Updates CQ tail pointer    |
|  - cqe ring array             |   +-------------------------------+
+-------------------------------+
```

Managing ring state across user space and kernel space without mutexes requires lockless ring semantics and CPU memory barriers. When an application writes an entry to the Submission Queue, it populates the target Submission Queue Entry inside the entry array, updates the index mapping array, and increments the submission tail pointer using a release memory barrier. The release barrier ensures that all memory writes to the entry payload complete before the updated tail index becomes visible to other processor cores. When the kernel reads the Submission Queue tail pointer, it applies an acquire memory barrier to guarantee it reads the newly written entry fields accurately.

When completing an operation, the kernel writes the result to the Completion Queue Entry array and updates the Completion Queue tail pointer using a release memory barrier. The application inspects the tail pointer using an acquire barrier and increments the Completion Queue head pointer once it finishes reading the completion result.

## Execution Paths and Internal Kernel Workqueues

When an application submits a request, io_uring evaluates whether the request can complete inline without blocking the calling thread. For socket read or write operations, the kernel attempts an initial non-blocking operation directly on the file descriptor. If the underlying device or socket returns EAGAIN, io_uring registers an internal poll wait callback with the kernel event notification mechanism. The submission context returns immediately without blocking. When network packets arrive, the driver callback triggers an internal execution pass that runs the requested operation and posts the completion result to the Completion Queue.

Disk file system operations behave differently because block storage devices do not support epoll style poll notifications. When a disk read encounters a page cache miss or requires block allocation, non-blocking inline execution fails. To prevent blocking the submission thread, io_uring dispatches the request to io-wq, an internal kernel worker thread pool bound to the io_uring instance. The kernel worker thread handles the blocking VFS operation synchronously while user space continues issuing new submissions.

## Zero-Syscall I/O via Kernel Polling

Calling io_uring_enter to notify the kernel of new submissions still incurs context switch overhead. To eliminate system calls entirely during steady-state processing, io_uring supports the IORING_SETUP_SQPOLL flag. When this flag is enabled during setup, the kernel spawns a dedicated polling thread named io_uring-sq bound to a specific CPU core. This kernel thread spins in a loop checking the Submission Queue tail pointer in shared memory.

When the application populates a new Submission Queue Entry and updates the tail index, the polling thread spots the change immediately, consumes the request, and executes the operation without a system call transition. Under heavy sustained I/O workloads, an application can submit and reap millions of operations per second with zero system calls. If the Submission Queue stays idle beyond a configured timeout, the kernel polling thread goes to sleep to conserve CPU resources. The kernel sets a flag in the shared ring memory signaling to user space that the polling thread is sleeping. The application checks this flag and issues an io_uring_enter call with the IORING_ENTER_SQ_WAKEUP flag only when it needs to wake the dormant kernel thread.

## Resource Pre-Registration: Fixed Buffers and Files

Standard file system read and write operations incur overhead from file descriptor lookup and memory page pinning. Every read system call calls fget to retrieve the kernel file structure from the process file descriptor table and increments atomic reference counters. Furthermore, the kernel must map user space virtual memory addresses to physical pages and pin them in RAM using get_user_pages. Pinning memory alters page table reference counts, triggering cache line invalidation across CPU sockets.

io_uring bypasses these bottlenecks through resource pre-registration via the io_uring_register system call. Applications can pre-register an array of memory buffers using the IORING_REGISTER_BUFFERS operation. The kernel pins the memory pages into physical RAM once during registration and constructs internal data mapping structures. Subsequent Submission Queue Entries reference registered buffers by their array index rather than passing raw virtual memory pointers. This removes page table traversal and memory pinning overhead from the fast path.

Similarly, an application can pre-register file descriptors using IORING_REGISTER_FILES. The kernel creates a fixed-file table array attached to the io_uring context. Submissions specify the array index of the registered file rather than the system file descriptor number. The kernel retrieves the internal struct file pointer directly from the array, eliminating atomic lock contention on the process file table.

## Manual Ring Setup and Submission Code

Here is a C implementation demonstrating how to map io_uring rings directly and submit read operations using low-level memory ordering without depending on external helper libraries.

```c
#include <linux/io_uring.h>
#include <sys/mmap.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>

struct app_ring {
    int ring_fd;
    unsigned *sq_head;
    unsigned *sq_tail;
    unsigned *sq_ring_mask;
    unsigned *sq_array;
    struct io_uring_sqe *sqes;
    
    unsigned *cq_head;
    unsigned *cq_tail;
    unsigned *cq_ring_mask;
    struct io_uring_cqe *cqes;
};

int setup_ring(struct app_ring *ring, unsigned entries) {
    struct io_uring_params p;
    memset(&p, 0, sizeof(p));
    
    ring->ring_fd = syscall(__NR_io_uring_setup, entries, &p);
    if (ring->ring_fd < 0) return -1;
    
    size_t sq_size = p.sq_off.array + p.sq_entries * sizeof(unsigned);
    size_t cq_size = p.cq_off.cqes + p.cq_entries * sizeof(struct io_uring_cqe);
    
    if (p.features & IORING_FEAT_SINGLE_MMAP) {
        if (cq_size > sq_size) sq_size = cq_size;
        cq_size = sq_size;
    }
    
    void *sq_ptr = mmap(0, sq_size, PROT_READ | PROT_WRITE,
                        MAP_SHARED | MAP_POPULATE, ring->ring_fd, IORING_OFF_SQ_RING);
    
    ring->sq_head = sq_ptr + p.sq_off.head;
    ring->sq_tail = sq_ptr + p.sq_off.tail;
    ring->sq_ring_mask = sq_ptr + p.sq_off.ring_mask;
    ring->sq_array = sq_ptr + p.sq_off.array;
    
    ring->sqes = mmap(0, p.sq_entries * sizeof(struct io_uring_sqe),
                      PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE,
                      ring->ring_fd, IORING_OFF_SQES);
                      
    void *cq_ptr = mmap(0, cq_size, PROT_READ | PROT_WRITE,
                        MAP_SHARED | MAP_POPULATE, ring->ring_fd, IORING_OFF_CQ_RING);
                        
    ring->cq_head = cq_ptr + p.cq_off.head;
    ring->cq_tail = cq_ptr + p.cq_off.tail;
    ring->cq_ring_mask = cq_ptr + p.cq_off.ring_mask;
    ring->cqes = cq_ptr + p.cq_off.cqes;
    
    return 0;
}

void submit_read(struct app_ring *ring, int fd, void *buf, unsigned len, unsigned long long offset) {
    unsigned tail = atomic_load_explicit((_Atomic unsigned *)ring->sq_tail, memory_order_relaxed);
    unsigned index = tail & *ring->sq_ring_mask;
    
    struct io_uring_sqe *sqe = &ring->sqes[index];
    memset(sqe, 0, sizeof(*sqe));
    sqe->opcode = IORING_OP_READ;
    sqe->fd = fd;
    sqe->addr = (unsigned long)buf;
    sqe->len = len;
    sqe->off = offset;
    sqe->user_data = 0xDEADBEEF;
    
    ring->sq_array[index] = index;
    atomic_store_explicit((_Atomic unsigned *)ring->sq_tail, tail + 1, memory_order_release);
}
```

The memory order release store on sq_tail guarantees that writes to the sqes entry payload and sq_array slot are globally visible across memory buses before the updated tail pointer reaches the kernel. When the kernel or SQPOLL thread reads the tail index, it accesses fully populated request data.

## Multishot Operations and Buffer Ring Pools

Modern kernel releases extended io_uring capabilities through multishot request modes. In traditional event-driven networking, an application must submit a new receive or accept request after consuming every completion. Multishot requests change this workflow. A single submission entry registered with flags like IORING_ACCEPT_MULTISHOT or IORING_RECV_MULTISHOT remains active inside the kernel request state machine, continuously outputting completion entries as new connections or data packets arrive.

Multishot operations integrate directly with provided buffer rings configured via IORING_REGISTER_PBUF_RING. User space populates a shared ring buffer with pre-allocated memory buffers. As packets hit the network stack, the kernel automatically consumes an available buffer from the provided buffer ring, copies the packet payload, and posts a Completion Queue Entry containing the selected buffer ID. This architecture removes both submission system calls and user-space buffer allocation overhead from high-performance networking pipelines.
