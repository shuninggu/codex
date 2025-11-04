# Quick Breakpoint Reference for VS Code

## 5 Critical Breakpoint Locations

| # | Purpose | File | Line | What to Inspect |
|---|---------|------|------|-----------------|
| 1 | **Model Entry** | `codex-rs/core/src/codex.rs` | **1812** | `run_turn` - First line, count calls |
| 2 | **API Send** | `codex-rs/core/src/chat_completions.rs` | **289** | Logs full prompt JSON |
| 3 | **Response Start** | `codex-rs/core/src/chat_completions.rs` | **402** | `process_chat_sse` starts |
| 4 | **Tool Call** | `codex-rs/core/src/tools/registry.rs` | **67** | Handler lookup |
| 5 | **MCP Call** | `codex-rs/core/src/mcp_tool_call.rs` | **58** | `sess.call_tool()` |

## How to Add Breakpoints in VS Code

1. Open the file mentioned above
2. Navigate to the specified line (Ctrl+G or Cmd+G)
3. Click in the left gutter to add a red dot breakpoint
4. Run with F5 or set up launch configuration
5. Inspect variables in the Debug Console

## Quick Debug Session Setup

### Method 1: VS Code Launch (Recommended)

Create `.vscode/launch.json`:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "type": "lldb",
            "request": "launch",
            "name": "Debug Codex",
            "cargo": {
                "args": ["build", "--bin", "codex"],
                "filter": { "name": "codex", "kind": "bin" }
            },
            "args": [],
            "env": {
                "RUST_LOG": "debug",
                "RUST_BACKTRACE": "1"
            },
            "cwd": "${workspaceFolder}/codex-rs"
        }
    ]
}
```

Then: **F5** to start debugging

### Method 2: Terminal with Logs

```bash
cd codex-rs
RUST_LOG=debug cargo run --bin codex
```

## What Each Breakpoint Shows

### Breakpoint 1 (Line 1812, codex.rs)
```
Variables to inspect:
- turn_context: TurnContext
- input: Vec<ResponseItem> (conversation history)
- task_kind: TaskKind
```

### Breakpoint 2 (Line 289, chat_completions.rs)
```
Variables to inspect:
- payload: JSON with full prompt
- messages: Array of all messages in conversation
- tools_json: Array of available tools
```

### Breakpoint 3 (Line 402, chat_completions.rs)
```
Variables to inspect:
- stream: SSE stream
- otel_event_manager: Event manager
- idle_timeout: Duration
```

### Breakpoint 4 (Line 67, registry.rs)
```
Variables to inspect:
- invocation: ToolInvocation
- tool_name: String
- call_id_owned: String
- handler: Arc<dyn ToolHandler>
```

### Breakpoint 5 (Line 58, mcp_tool_call.rs)
```
Variables to inspect:
- server: String
- tool_name: String
- arguments_value: Option<serde_json::Value>
- invocation: McpInvocation
```

## Counting Interactions

**Question**: How many times does agent interact with model?

**Answer**: Each time breakpoint at line 1812 hits = 1 interaction

Add conditional breakpoint expression:
```rust
call_count += 1  // if you add a counter
```

Or use Debug Console: `call_count++` after each hit

## Logging Without Breakpoints

Instead of breakpoints, add these lines:

**In codex.rs:1813** (after function signature):
```rust
tracing::info!("🔄 MODEL CALL: turn_id={}", turn_context.sub_id);
```

**In registry.rs:68** (after handler match):
```rust
tracing::info!("🔧 TOOL: {} (call_id={})", tool_name, call_id_owned);
```

**In mcp_tool_call.rs:59** (before sess.call_tool):
```rust
tracing::info!("🎯 MCP: server={}, tool={}", server, tool_name);
```

Then run with: `RUST_LOG=info cargo run` to see all logs

## Filtering Logs

### See only model calls:
```bash
RUST_LOG=codex_core::codex=info cargo run 2>&1 | grep "MODEL CALL"
```

### See only tool calls:
```bash
RUST_LOG=codex_core::tools=info cargo run 2>&1 | grep "TOOL:"
```

### See everything:
```bash
RUST_LOG=debug cargo run 2>&1 | tee debug.log
```
