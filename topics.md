# Topics You'd Probably Enjoy Writing (or Reading) About Next

Based on all 24 of your blog posts, here's what I see as your core interest profile, followed by specific topic suggestions in each area.

---

## Your Interest DNA

Your blogs cluster around a few recurring themes: you like understanding **how things actually work under the hood** (OS internals, file formats, framework pipelines), you gravitate toward **.NET/C# backend architecture** as your professional stack, you enjoy **Python as a scripting Swiss army knife**, and you have a clear pattern of writing "I figured this out so you don't have to" practical guides. You also care about **developer tooling and workflow** (Jujutsu over Git, console debugging tricks, resume sharing hacks).

---

## 1. Software Architecture & Design Patterns

**What you've already covered:** CQRS, Composition over Inheritance, NgRx/Redux state management, Middleware pipelines

**Topics you'd likely dig into:**
- **Event-driven architecture & message queues** (RabbitMQ, Kafka, Azure Service Bus). You wrote about CQRS which naturally leads here, especially the Event Sourcing side you mentioned but didn't deep-dive.
- **Domain-Driven Design (DDD)** as a way to organize complex .NET backends. Bounded contexts, aggregates, value objects. Pairs well with your CQRS post.
- **Saga pattern & distributed transactions.** How do you handle workflows that span multiple services when you can't just do a database transaction?
- **Clean Architecture / Vertical Slice Architecture** in .NET. You clearly care about code organization (composition over inheritance, middleware layering). These patterns are the natural next step.
- **API design: REST vs GraphQL vs gRPC.** You write backend code, this is a conversation you've probably had opinions on already.

## 2. .NET / C# Deep Dives

**What you've already covered:** IEnumerable deferred execution, Generic Hosts, ASP.NET Middleware, Composition patterns, FunMark benchmarking

**Topics you'd likely dig into:**
- **Channels and async streams in C#.** You liked deferred execution; producer-consumer patterns with `System.Threading.Channels` and `IAsyncEnumerable` are in the same spirit.
- **Source generators and compile-time metaprogramming.** Understanding how the framework does magic at build time. Very "how things work under the hood."
- **Minimal APIs deep dive.** You covered Generic Host and middleware already. How Minimal APIs wire things differently would complete the trilogy.
- **.NET Aspire and cloud-native .NET.** The new orchestration layer for distributed .NET apps. If you're interested in Generic Host, this is its evolution.
- **Memory management, Span<T>, and performance tricks.** Your FunMark benchmarking post shows you care about performance. `Span<T>`, `Memory<T>`, object pooling, and reducing allocations would scratch that itch.

## 3. Operating Systems & Systems Programming

**What you've already covered:** macOS SIP/kernel lockdown, Windows file system structure

**Topics you'd likely dig into:**
- **Linux internals for backend devs.** cgroups, namespaces, how containers actually work at the kernel level. Connects your OS curiosity to your backend work.
- **How Docker/containers work under the hood.** Given your interest in OS internals and deployment (Heroku posts), understanding what containers actually _are_ at the syscall level seems like your kind of thing.
- **eBPF for observability.** A relatively new way to instrument the Linux kernel without loading kernel modules. You wrote about macOS kext restrictions, eBPF is the Linux world's answer to "how do you extend the kernel safely."
- **File systems compared** (ext4, Btrfs, ZFS, APFS). You wrote about Windows file _structure_; the file _system_ layer underneath is equally fascinating.

## 4. Developer Tooling & Workflow

**What you've already covered:** Jujutsu VCS, console debugging methods, URL/redirect tricks, resume sharing automation

**Topics you'd likely dig into:**
- **Dev containers and reproducible dev environments.** Codespaces, devcontainers, Nix. You clearly care about tooling that makes life easier.
- **Terminal multiplexers and CLI productivity** (tmux, zellij, modern shell setups with starship/zoxide/fzf).
- **Observability stack for developers.** OpenTelemetry, structured logging, distributed tracing. Connects your middleware interest (where you'd instrument things) with your practical tooling mindset.
- **AI-assisted development tools** and how they fit into workflows. You're already using them, writing about the experience would be natural.

## 5. Networking & Security

**What you've already covered:** PCAP to CSV conversion, WiFi password extraction, macOS kernel security

**Topics you'd likely dig into:**
- **How TLS/HTTPS actually works**, step by step. Certificate chains, handshakes, cipher suites. Practical security knowledge that connects to your network packet analysis background.
- **OAuth 2.0 and OpenID Connect internals.** You build web backends. Understanding auth flows deeply (not just plugging in a library) would pair well with your middleware post.
- **Web application security (OWASP Top 10) from a developer's perspective.** SQL injection, XSS, CSRF explained with .NET examples.
- **DNS deep dive.** You've touched DNS with your URL redirect post. How DNS resolution actually works, DNS-over-HTTPS, and DNS as an attack vector could be interesting.

## 6. Python Scripting & Automation

**What you've already covered:** WhatsApp automation, YouTube downloader, WiFi extraction, PCAP parsing, Office file extraction, Heroku deployment

**Topics you'd likely dig into:**
- **Web scraping at scale** with Playwright or Scrapy. You've done small automation scripts, scaling that up is a natural progression.
- **Building CLI tools properly** with Click or Typer. You build scripts, packaging them as proper CLI tools is the next level.
- **Task orchestration with Celery or Prefect.** Going from single scripts to coordinated workflows.

## 7. Frontend Architecture (Occasional Interest)

**What you've already covered:** NgRx store pattern, JavaScript console methods

**Topics you'd likely dig into:**
- **Signals-based reactivity** (Angular Signals, Solid.js, or the concept in general). If NgRx interested you, the shift toward signals as a simpler state management primitive might catch your attention.
- **Web Components and the Shadow DOM.** The "how does this actually work" angle for frontend, similar to how you explore backend framework internals.

---

## Things I Am Actively Working On

These are technologies, tools, and platforms I am currently working with. 
- **New Relic & NRQL (New Relic Query Language)**: Telemetry data querying, dashboard optimization, application performance monitoring (APM), and custom alert configurations.

---

## The Pattern I'd Bet On

If I had to pick the **5 topics** most aligned with your writing style and interests:

1. **How Docker/containers work under the hood** — combines your OS curiosity with practical backend relevance
2. **Event-driven architecture with message queues** — natural sequel to your CQRS post
3. **Channels, async streams, and `IAsyncEnumerable` in C#** — sequel to your deferred execution post
4. **OAuth 2.0 / OpenID Connect internals** — your security interest meets your backend work
5. **Clean Architecture or Vertical Slice Architecture in .NET** — ties together your design patterns posts into a cohesive approach
