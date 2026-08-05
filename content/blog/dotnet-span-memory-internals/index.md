---
title: "How Span<T> and Memory<T> Work Under the Hood in .NET"
date: "2026-08-05T10:15:18.000Z"
description: "An in-depth technical analysis of Span<T>, Memory<T>, ref structs, interior pointers, and CLR garbage collector safety guarantees in modern .NET."
---

Before .NET Core 2.1, working with contiguous blocks of memory in C# always meant making trade-offs between safety, performance, and GC pressure. If you needed to parse a subset of a string or byte array, your options were limited. You could call `Substring` or `Array.Copy`, which allocated a brand-new object on the managed heap every single time. Alternatively, you could drop down to unmanaged code, pin the array using the `fixed` keyword to freeze the Garbage Collector, and manipulate raw pointers directly. Pinning created GC heap fragmentation, while heap allocations created memory pressure that killed throughput in high-concurrency systems.

`Span<T>` and `Memory<T>` changed how .NET handles memory. They provided a unified, zero-allocation abstraction over stack memory, managed arrays, and native heap allocations. Understanding how the runtime implements these types reveals how modern C# achieves native C++ speeds without abandoning memory safety.

### The Problem with Managed Pointers

To understand `Span<T>`, you first have to understand how the Common Language Runtime (CLR) handles memory references. In traditional C#, a reference type holds an object reference pointing to the header of an object allocated on the managed heap. The Garbage Collector monitors these root references. When GC runs, it traces object graphs, moves surviving objects around to compact memory, and updates references to point to their new memory addresses.

Unmanaged pointers like `byte*` operate outside this system. A native pointer holds a fixed 64-bit virtual memory address. If you point a native pointer to index 10 of a managed byte array, the GC has no knowledge of that pointer. If GC compacts the heap and moves the underlying array, your native pointer now points to garbage or corrupted object headers.

This is why `Span<T>` could not be implemented as a simple native pointer wrapper. It required a primitive deeply integrated into the JIT compiler and Garbage Collector: the interior pointer, known in CIL (Common Intermediate Language) as `byref`.

### Ref Structs and Stack Lifetime Enforcements

`Span<T>` is defined in the framework as a `ref struct`. This is not just a hint to the compiler; it enforces rigid structural guarantees verified by the Roslyn compiler and the CLR type loader.

A `ref struct` must always reside on the execution stack. It can never be allocated on the managed heap. Because of this rule, a `ref struct` cannot be boxed into an `object` or interface. It cannot be used as an element in a standard array, because arrays live on the heap. It cannot be a field in a normal class or a non-ref struct. Most importantly, it cannot be captured across async state machine boundaries or lambda closures, because async state machines are compiler-generated classes that live on the heap.

```csharp
// Valid stack usage
public void ProcessBuffer(byte[] data)
{
    Span<byte> span = data.AsSpan(10, 20);
    span[0] = 0xFF;
}

// INVALID: Compiler error CS8345
public class BufferHolder
{
    public Span<byte> Data; // Cannot be a field in a class
}
```

These restrictions exist to prevent stack frame invalidation. If a `Span<T>` could be stored on the heap, it could outlive the stack frame of the method that created it. If that stack frame contained stack-allocated memory created via `stackalloc`, a heap-allocated reference pointing to that stack space would read deallocated stack memory, causing arbitrary memory corruption.

### Interior Pointers and Memory Layout

On a 64-bit architecture, `Span<T>` occupies exactly 16 bytes of stack space. Its layout consists of two fields: an interior pointer and a length integer.

```
Span<T> Stack Memory Layout (16 Bytes on x64)
+-----------------------------------+-----------------------------------+
|  byref T _pointer (8 bytes)       |  int32 _length (4 bytes + pad)    |
+-----------------------------------+-----------------------------------+
                  |
                  |-- Points directly to element 0 or offset index
                  v
[ Managed Heap Array Header | Element 0 | Element 1 | Element 2 | ... ]
```

The `_pointer` field is typed as `byref T`. Unlike a standard object reference that must point to the beginning of an object header, an interior pointer can point directly into the middle of an existing object on the heap, or to a location on the thread stack, or to raw unmanaged memory allocated via `Marshal.AllocHGlobal`.

The JIT compiler and the Garbage Collector treat interior pointers differently than raw native pointers. During the GC Mark and Compact phases, the GC scans stack frames for active `byref` variables. If an interior pointer points inside the bounds of a managed heap object, the GC tracks that reference. When the GC moves the parent array during heap compaction, it calculates the offset displacement and automatically updates the interior pointer inside the stack frame so it keeps pointing to the exact same relative element index.

### Slicing Mechanics and Bounds Checking

Slicing memory with `Span<T>` involves no memory allocation and negligible CPU execution cost. When you invoke `span.Slice(start, length)`, the operation executes pure pointer arithmetic.

```csharp
public Span<T> Slice(int start, int length)
{
    if ((uint)start > (uint)_length || (uint)length > (uint)(_length - start))
    {
        ThrowHelper.ThrowArgumentOutOfRangeException();
    }

    return new Span<T>(ref Unsafe.Add(ref _pointer, start), length);
}
```

The `Unsafe.Add` method translates directly into an `lea` (Load Effective Address) or `add` instruction in x86/x64 assembly. The JIT eliminates bounds checks when it proves that access indices fall within `_length`. The resulting assembly for reading an element out of a sliced span matches the assembly generated by an unmanaged C pointer dereference.

Because `Span<T>` abstracts the underlying memory source, the exact same slicing logic and performance profile apply whether the backing storage is a managed byte array, a `stackalloc` buffer on the thread stack, or a memory-mapped file mapped through native OS APIs.

### Heap Compatibility with Memory<T>

The stack-only constraint of `ref struct` creates an immediate problem for asynchronous pipelines. When an `async` method hits an `await` expression, execution yields, and the current state of local variables gets copied into a heap-allocated state machine instance. Because `Span<T>` cannot exist on the heap, it cannot be held across an `await` boundary.

To solve this, .NET introduced `Memory<T>`. Unlike `Span<T>`, `Memory<T>` is a standard value type (`struct`), not a `ref struct`. It can be boxed, stored as a field in classes, passed across async methods, and stored in collection lists.

`Memory<T>` uses a 16-byte memory footprint consisting of an `object` reference, an integer index, and an integer length.

```csharp
public readonly struct Memory<T>
{
    private readonly object? _object;
    private readonly int _index;
    private readonly int _length;
}
```

The `_object` field can hold a reference to a managed `T[]` array, a `string`, or a custom `MemoryManager<T>`. When code needs to perform actual read or write operations on `Memory<T>`, it invokes the `.Span` property.

```csharp
public Span<T> Span
{
    get
    {
        if (_object is T[] array)
        {
            return new Span<T>(array, _index, _length);
        }
        if (_object is string str)
        {
            return MemoryMarshal.CreateSpan(ref Unsafe.As<char, T>(ref str.GetRawStringData()), _length);
        }
        return ((MemoryManager<T>)_object!).GetSpan().Slice(_index, _length);
    }
}
```

Calling `.Span` dynamically creates a stack-bound `Span<T>` instance valid for the duration of the current synchronous stack frame. You store data across asynchronous boundaries using `Memory<T>`, and when you step into a synchronous block to process bytes, you project it into a `Span<T>`.

### Memory Pooling with IMemoryOwner<T>

Combining `Memory<T>` with `ArrayPool<T>` enables zero-allocation buffer management in web servers and streaming engines. ASP.NET Core Kestrel processes incoming TCP socket streams using pooled memory buffers.

Instead of allocating a new byte array for every HTTP payload, Kestrel rents an `IMemoryOwner<T>` from `MemoryPool<T>.Shared`. The rented owner exposes a `Memory<T>` property pointing to a slice of a large pooled byte array.

```csharp
public async Task ProcessSocketStreamAsync(Stream stream)
{
    using (IMemoryOwner<byte> owner = MemoryPool<byte>.Shared.Rent(4096))
    {
        Memory<byte> memory = owner.Memory;
        int bytesRead = await stream.ReadAsync(memory);
        
        ProcessPayload(memory.Span.Slice(0, bytesRead));
    } // Memory buffer automatically returned to pool on Dispose
}
```

When processing completes, disposing the `IMemoryOwner<T>` returns the underlying array slice to the pool. GC allocation rates drop to near zero because byte arrays are reused indefinitely across millions of HTTP requests.

### Low-Level Compiler Optimizations

The JIT compiler applies specialized optimizations specifically for `Span<T>` and `ReadOnlySpan<T>`.

When you write `ReadOnlySpan<byte> parseTarget = new byte[] { 0x01, 0x02, 0x03, 0x04 };`, Roslyn does not emit code that allocates a new byte array on the heap. Instead, Roslyn bakes the raw byte array data directly into the `.text` or `.rdata` section of the compiled PE assembly metadata.

The JIT turns that statement into a `Span<byte>` pointing directly to the static assembly memory address. The assignment executes in zero instructions at runtime, avoiding heap allocations entirely while maintaining array type safety.

Vectorization and SIMD instructions leverage `Span<T>` layout directly. Methods in `System.MemoryExtensions` and `System.Numerics.Vector<T>` process spans by executing hardware-accelerated AVX2 or SSE instructions. Because elements in a `Span<T>` are guaranteed to be contiguous in memory, SIMD vector instructions can load 256 bits or 512 bits of data straight from `_pointer` into CPU vector registers in a single instruction cycle.

Combining interior pointers, stack-only lifetimes via `ref struct`, and heap-compatible `Memory<T>` handles allows .NET to maintain high-level type safety while matching the raw memory efficiency of unmanaged languages.
