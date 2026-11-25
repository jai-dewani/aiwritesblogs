---
title: "Inside eBPF CO-RE and BTF: Type Graphs, Field Relocations, and Portable Kernel Bytecode Execution"
date: "2026-11-25T13:23:36.000Z"
description: "A deep technical dive into BPF Type Format metadata, CO-RE field relocation records, and how libbpf dynamically rewrites bytecode for cross-kernel portability."
---

For years, writing production eBPF programs was a deployment nightmare. If you wanted to inspect kernel data structures like `struct task_struct` or `struct socket` across multiple Linux distributions, your eBPF program compiled on Ubuntu 20.04 would fail or crash on Fedora or RHEL. Internal kernel structures change layout across kernel versions. A field like `pid` or `skc_daddr` might sit at offset 1040 on kernel 5.4, but move to offset 1056 on kernel 5.15 because a developer added a new field upstream.

The initial workaround was the BPF Compiler Collection (BCC) approach. Instead of compiling eBPF programs into ELF binary objects ahead of time, BCC shipped the entire Clang and LLVM compiler toolchain alongside kernel header packages directly into production containers. At runtime, BCC would fetch local kernel headers, invoke LLVM on host machines, and compile eBPF code on the fly. This solved the layout mismatch problem, but it cost hundreds of megabytes of RAM, burned CPU cycles during startup, and failed completely on stripped enterprise hosts lacking C header files.

The modern solution to this problem is BPF CO-RE (Compile Once, Run Everywhere) powered by BTF (BPF Type Format). Instead of embedding a heavy compiler toolchain on production machines, CO-RE shifts offset resolution into the user-space ELF loader library, usually `libbpf`. Clang compiles C code into an eBPF binary once, capturing intent rather than hardcoded memory offsets. At load time, `libbpf` inspects the running host kernel's embedded type graph, calculates the true structural offsets, and patches the eBPF bytecode instructions in memory before submitting them to the kernel verifier.

```mermaid
flowchart TD
    subgraph Build System
        A[eBPF C Source Code] -->|Clang -g -O2 -target bpf| B[eBPF Bytecode Object File]
        A -->|Emits Access Paths| C[.BTF.ext Relocation Records]
        A -->|Emits C Type Definitions| D[.BTF Type Section]
    end

    subgraph Target Host System
        B & C & D --> E[libbpf Loader Engine]
        F[/sys/kernel/btf/vmlinux
Running Kernel Type Graph] --> E
        E -->|Match Struct Names & Fields| G[Calculate Dynamic Byte Offsets]
        G -->|Rewrite Instruction Offsets| H[Patched eBPF Bytecode]
        H -->|sys_bpf BPF_PROG_LOAD| I[Kernel BPF Verifier]
    end
```

The entire mechanism relies on BTF, a space-efficient metadata format designed to encode C type definitions into binary blobs orders of magnitude smaller than DWARF debug symbols. Linux kernels compiled with `CONFIG_DEBUG_INFO_BTF=y` export their complete type graph via `/sys/kernel/btf/vmlinux`. By inspecting this sysfs node, any user-space program can discover the exact memory layout of every struct, union, enum, and function parameter present in the running kernel.

To understand how BTF achieves its small footprint, examine the physical binary header layout defined in `linux/btf.h`. DWARF debug files routinely exceed 100 megabytes because they duplicate type trees across compilation units and encode verbose string names. BTF eliminates this overhead by deduplicating type nodes globally and splitting string storage into a dedicated string block.

```
+-------------------------------------------------------------------------+
|                           struct btf_header                             |
| magic: 0xEB9F | version: 1 | flags: 0 | hdr_len: 24                      |
| type_off | type_len | str_off | str_len                                 |
+-------------------------------------------------------------------------+
|                           Type Information                              |
| [ID 1] BTF_KIND_INT     (name_off='int', size=4, encoding=SIGNED)       |
| [ID 2] BTF_KIND_PTR     (type_id=1)                                     |
| [ID 3] BTF_KIND_STRUCT  (name_off='task_struct', vlen=2, size=2432)     |
|        - member 0: name_off='state', type_id=1, bit_offset=0          |
|        - member 1: name_off='tasks', type_id=4, bit_offset=64         |
+-------------------------------------------------------------------------+
|                             String Storage                              |
| \0 int \0 task_struct \0 state \0 tasks \0 ...                           |
+-------------------------------------------------------------------------+
```

The binary layout begins with `struct btf_header`. The `magic` field must contain `0xEB9F`. The header defines the offset and length of two sequential byte arrays that follow: the type section and the string section. Type references inside the type section are represented as simple 32-bit integer indexes starting from 1. Type ID 0 is reserved for `void`.

Each type in the type section begins with `struct btf_type`. The `name_off` field points directly to a byte offset inside the string section. The `info` field packs three pieces of metadata into a single 32-bit word: the type kind (such as `BTF_KIND_INT`, `BTF_KIND_STRUCT`, `BTF_KIND_ARRAY`, `BTF_KIND_FUNC`), a 16-bit `vlen` field indicating how many variable-length array members follow this header, and a bit flag for root type attributes. For `BTF_KIND_STRUCT`, the `btf_type` header is immediately followed in memory by an array of `struct btf_member` items, equal to the length defined in `vlen`.

Each `struct btf_member` record contains `name_off` pointing to the field name in the string section, `type` specifying the Type ID of the field, and `offset` encoding the bit position of the field relative to the start of the enclosing struct. Because bit offsets are recorded explicitly, bitfields and padded fields are represented with exact alignment accuracy without needing extra metadata wrapper types.

When you compile eBPF code using Clang with the `-g` flag and target `-target bpf`, Clang generates two distinct BTF sections inside the ELF object file: `.BTF` and `.BTF.ext`. The `.BTF` section contains the local type graph representing all structs and types defined in your local C header files. The `.BTF.ext` section contains CO-RE relocation records that map specific BPF instructions back to high-level type access paths.

Instead of generating standard C field accesses that Clang would normally emit as fixed memory loads, developers use CO-RE access macros like `BPF_CORE_READ()` or annotate their code with `__builtin_preserve_access_index()`. When Clang encounters this compiler intrinsic, it does not lock in a hardcoded byte offset inside the eBPF opcode. Instead, it emits a dummy BPF instruction with offset 0 and writes a `struct bpf_core_relo` record into `.BTF.ext`.

A single CO-RE relocation record in `.BTF.ext` tracks four vital fields: `insn_off`, `type_id`, `access_str_off`, and `kind`. `insn_off` specifies the exact byte offset within the eBPF program's instruction stream where the memory load opcode resides. `type_id` specifies the Type ID in the local `.BTF` section representing the root struct being read. `access_str_off` points to a string in the BTF string table representing the field path as a sequence of member array indices, such as `0:2:1` to represent `task->mm->rss_stat`.

The `kind` field dictates what structural property the loader should evaluate and patch. The most common relocation kind is `BPF_CORE_FIELD_BYTE_OFFSET`, which instructs the loader to rewrite the instruction's imm or offset field with the real byte offset of the target field. Other relocation kinds include `BPF_CORE_FIELD_EXISTS`, which yields 1 or 0 depending on whether the field exists in the target kernel, `BPF_CORE_FIELD_SIZE`, which evaluates to the size of the target field, and `BPF_CORE_TYPE_EXISTS`, which checks for the presence of specific kernel type definitions.

```c
// Example of eBPF kernel space program using CO-RE
#include <vmlinux.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(trace_openat2, int dfd, const char *filename)
{
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    pid_t pid;
    
    // Clang generates a CO-RE relocation record for this access
    pid = BPF_CORE_READ(task, pid);
    
    bpf_printk("Trace openat2 called by PID %d\n", pid);
    return 0;
}
```

When `libbpf` opens this compiled ELF file via `bpf_object__open()`, it executes a sequence of dynamic type resolution routines during `bpf_object__load()`. First, it parses the binary `.BTF` section embedded in your eBPF binary to build the local type graph. Next, it opens `/sys/kernel/btf/vmlinux` and parses the running kernel's active type graph into a parallel in-memory lookup table.

For every relocation record present in `.BTF.ext`, `libbpf` executes a field matching algorithm. It takes the local `type_id` (representing `struct task_struct` in your compiled binary), extracts its string name (`task_struct`), and performs a lookup in the kernel's BTF type table. Once it locates `struct task_struct` in the host kernel's BTF graph, `libbpf` walks the access string path.

If the local access string is `0:15`, `libbpf` checks the local BTF definition to see that index 15 corresponds to the field named `pid`. It then iterates through the host kernel's `struct task_struct` member list searching for a member named `pid`. When it finds `pid` at member index 18 in the kernel's type definition, it reads the `bit_offset` value associated with member 18, converts it to a byte offset (e.g., 1248 bytes), and updates the local instruction.

The eBPF load instruction format is an 8-byte structure consisting of an 8-bit opcode, a 4-bit destination register, a 4-bit source register, a 16-bit signed offset, and a 32-bit immediate value (`imm`). If the BPF program uses direct memory dereferencing via modern kernel instructions, `libbpf` locates the target 8-byte instruction inside the byte array using `insn_off` and rewrites the 16-bit offset field directly.

```
eBPF Instruction Format (8 Bytes / 64 bits):
+---------------+-------+-------+-----------------------+--------------------------------+
| opcode (8b)   | dst(4)| src(4)| offset (16 bits)      | immediate (32 bits)            |
+---------------+-------+-------+-----------------------+--------------------------------+
                                           ^ 
                                           | libbpf overwrites this 16-bit value
                                           | with calculated target kernel byte offset
```

Handling field renames or structural changes across major kernel versions requires conditional logic in eBPF code. CO-RE supports this through `bpf_core_field_exists()` and struct flavor annotations. If a field was renamed from `state` to `__state` in kernel 5.14, a single eBPF binary can handle both kernel generations by branching at load time.

When `bpf_core_field_exists(task->__state)` is evaluated, `libbpf` attempts field matching against the host kernel. If the field is found, `libbpf` patches the conditional instruction's immediate operand with 1. If matching fails, `libbpf` patches the operand with 0. The BPF verifier performs dead code elimination on the branch that evaluates to false, removing the invalid access branch entirely before verifying the program's memory safety.

Struct flavors allow eBPF programmers to define multiple layout variations for a single kernel type within the same C source file. By appending a triple-underscore suffix to a struct name (such as `struct task_struct___v510`), you inform Clang and `libbpf` that this definition is a target variant. During type matching, `libbpf` strips the triple underscore and everything after it, matching `task_struct___v510` directly against the kernel's canonical `task_struct` type node.

```c
// Struct flavor declaration for older kernel compatibility
struct task_struct___old {
    long state;
    pid_t pid;
};

SEC("kprobe/sys_enter")
int trace_state(void *ctx)
{
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    long state;

    if (bpf_core_field_exists(task->bpf_core_relo_field_exists_helper)) {
        // Modern kernel read path
        state = BPF_CORE_READ(task, __state);
    } else {
        // Fallback for older kernels using struct flavor cast
        struct task_struct___old *old_task = (void *)task;
        state = BPF_CORE_READ(old_task, state);
    }

    return 0;
}
```

The performance advantage of BTF and CO-RE over legacy dynamic runtime compilers is substantial. Loading a complex eBPF monitoring agent using BCC takes several seconds and tens of megabytes of RAM while Clang compiles C code inside host memory. Loading the same agent compiled into a CO-RE ELF binary using `libbpf` takes less than 15 milliseconds and consumes negligible RAM, as `libbpf` merely performs linear array scanning, string hashing, and in-memory byte patching of existing instructions.

By leveraging BTF binary graphs and CO-RE relocation records, eBPF programs achieve native cross-kernel execution without sacrificing runtime memory safety or execution performance. The burden of ABI tracking moves completely into type metadata evaluation, making eBPF binary deployment as reliable as compiled native code.
