# Debugging Guide: Logging Model Interactions and Tool Calls

## Overview

This guide explains how to:
1. Track how many times the agent interacts with the model
2. Log prompts sent to the model and responses received
3. Add breakpoints and logging for debugging

## Key Files for Logging

### 1. Model Interaction Entry Point

**Location**: `codex-rs/core/src/codex.rs` - `run_turn` function

**Add logging here to track each model call**:

```rust
// Around line 1750-1800 in codex.rs
async fn run_turn(
    sess: Arc<Session>,
    turn_context: Arc<TurnContext>,
    input: Vec<ResponseItem>,
    cancellation_token: CancellationToken,
) -> TurnResult {
    
    // ADD THIS LOGGING
    tracing::info!(
        "🔵 MODEL CALL: turn_id={}, input_items={}", 
        turn_context.sub_id, 
        input.len()
    );
    
    // Log the prompt being sent
    tracing::debug!("📤 PROMPT: {:?}", serde_json::to_string_pretty(&input));
    
    let router = Arc::new(ToolRouter::from_config(
        &turn_context.tools_config,
        Some(mcp_tools),
    ));
    
    let prompt = Prompt {
        input,
        tools: router.specs(),
        parallel_tool_calls,
        base_instructions_override: turn_context.base_instructions.clone(),
        output_schema: turn_context.final_output_json_schema.clone(),
    };
    
    // Log tools available
    tracing::debug!("🛠️ TOOLS: {:?}", router.specs().len());
}
```

### 2. Streaming Response Handler

**Location**: `codex-rs/core/src/codex.rs` - `run_turn` function, streaming handler

**Add logging here to track model responses**:

```rust
// Around line 1800-1850 in codex.rs
async fn handle_streaming_response(stream: ResponseStream) {
    // ADD THIS LOGGING
    tracing::info!("📥 STREAMING RESPONSE START");
    
    while let Some(event) = stream.next().await {
        match event {
            Ok(ResponseEvent::OutputTextDelta(delta)) => {
                // Log text deltas
                tracing::trace!("📝 DELTA: {}", delta);
            }
            Ok(ResponseEvent::OutputItemDone(item)) => {
                // Log complete items
                tracing::info!("✅ ITEM DONE: {:?}", item);
            }
            Ok(ResponseEvent::Completed { response_id, token_usage }) => {
                // Log completion
                tracing::info!("🏁 COMPLETED: id={}, tokens={:?}", response_id, token_usage);
            }
            Err(e) => {
                tracing::error!("❌ ERROR: {:?}", e);
            }
        }
    }
}
```

### 3. API Request Logger

**Location**: `codex-rs/core/src/client.rs` - `stream_responses` or `stream_chat_completions`

**Existing logging**:

```rust
// Line 289-293 in chat_completions.rs
debug!(
    "POST to {}: {}",
    provider.get_full_url(&None),
    serde_json::to_string_pretty(&payload).unwrap_or_default()
);
```

**To see API calls, set log level**:
```bash
RUST_LOG=debug cargo run
```

### 4. Tool Call Handler

**Location**: `codex-rs/core/src/tools/registry.rs` - `dispatch` function

**Add logging here to track tool calls**:

```rust
// Around line 51-74 in tools/registry.rs
pub async fn dispatch(
    &self,
    invocation: ToolInvocation,
) -> Result<ResponseInputItem, FunctionCallError> {
    
    // ADD THIS LOGGING
    tracing::info!(
        "🔧 TOOL CALL: name={}, call_id={}", 
        invocation.tool_name, 
        invocation.call_id
    );
    tracing::debug!("📦 PAYLOAD: {:?}", invocation.payload);
    
    let handler = match self.handler(tool_name.as_ref()) {
        Some(handler) => handler,
        None => {
            tracing::error!("❌ UNKNOWN TOOL: {}", tool_name);
            // ...
        }
    };
    
    // Log tool result
    match handler.handle(invocation).await {
        Ok(output) => {
            tracing::info!("✅ TOOL SUCCESS: {:?}", output);
            Ok(output)
        }
        Err(e) => {
            tracing::error!("❌ TOOL ERROR: {:?}", e);
            Err(e)
        }
    }
}
```

### 5. MCP Tool Call Logger

**Location**: `codex-rs/core/src/mcp_tool_call.rs`

**Add logging here**:

```rust
// Around line 1-80 in mcp_tool_call.rs
pub async fn execute_mcp_tool_call(
    connection_manager: &McpConnectionManager,
    invocation: McpInvocation,
) -> Result<ResponseInputItem, FunctionCallError> {
    
    // ADD THIS LOGGING
    tracing::info!(
        "🎯 MCP CALL: server={}, tool={}", 
        invocation.server, 
        invocation.tool
    );
    tracing::debug!("📤 MCP ARGS: {:?}", invocation.arguments);
    
    // ...
    
    match result {
        Ok(output) => {
            tracing::info!("✅ MCP SUCCESS: {:?}", output);
            Ok(output)
        }
        Err(e) => {
            tracing::error!("❌ MCP ERROR: {:?}", e);
            Err(e)
        }
    }
}
```

## Running with Logging

### Method 1: Environment Variable

```bash
# See all debug logs
export RUST_LOG=debug
cargo run --bin codex -- <command>

# See only Codex logs
export RUST_LOG=codex_core=debug,codex_protocol=debug
cargo run --bin codex -- <command>

# Specific modules
export RUST_LOG=codex_core::codex=debug,codex_core::tools=debug
cargo run --bin codex -- <command>
```

### Method 2: CLI with Logging

```bash
# Using the justfile
just run RUST_LOG=debug
```

### Method 3: VS Code Launch Configuration

Edit `.vscode/launch.json`:

```json
{
    "type": "lldb",
    "request": "launch",
    "name": "Debug Codex with Logging",
    "cargo": {
        "args": ["build", "--bin", "codex"],
        "filter": {
            "name": "codex",
            "kind": "bin"
        }
    },
    "args": [],
    "env": {
        "RUST_LOG": "debug",
        "RUST_BACKTRACE": "1"
    },
    "cwd": "${workspaceFolder}/codex-rs"
}
```

## Breakpoint Locations

### Critical Breakpoints for Model Interactions

1. **Entry to Model Call**
   - File: `codex-rs/core/src/codex.rs`
   - Function: `run_turn`
   - **Line: 1812**
   - ⭐ **Recommended**: Add breakpoint here to count model interactions

2. **API Request Sent**
   - File: `codex-rs/core/src/chat_completions.rs`
   - Function: `stream_chat_completions`
   - **Line: 289** (logs payload) or **Line: 307** (actual HTTP send)
   - ⭐ **Recommended**: Line 289 shows complete JSON payload

3. **Response Received**
   - File: `codex-rs/core/src/chat_completions.rs`
   - Function: `process_chat_sse`
   - **Line: 402** (stream processing starts) or **Line: 476** (first chunk parsed)
   - ⭐ **Recommended**: Line 402 for SSE processing entry

4. **Tool Call Decision**
   - File: `codex-rs/core/src/tools/registry.rs`
   - Function: `dispatch`
   - **Line: 57** (function entry) or **Line: 67** (handler lookup)
   - ⭐ **Recommended**: Line 67 to see which tool is called

5. **MCP Tool Execution**
   - File: `codex-rs/core/src/mcp_tool_call.rs`
   - Function: `handle_mcp_tool_call`
   - **Line: 16** (function entry) or **Line: 58** (actual tool call)
   - ⭐ **Recommended**: Line 58 for MCP invocation

### Best Places for printf/logging

For **counting model interactions**, add this at **Line 1812 in codex.rs**:
```rust
tracing::info!("🔄 MODEL CALL #{}: turn_id={}", call_count, turn_context.sub_id);
```

For **logging complete prompts**, the existing debug log at **Line 289 in chat_completions.rs** already does this!
```rust
debug!("POST to {}: {}", ...);  // ← This logs the full JSON payload
```

For **tool call inspection**, add at **Line 67 in registry.rs**:
```rust
tracing::info!("🔧 DISPATCH: tool={}, call_id={}", tool_name, call_id_owned);
```

## Counting Model Interactions

To count total model interactions in a session:

```rust
// Add to Session struct (codex.rs around line 245)
pub(crate) struct Session {
    // ... existing fields ...
    pub(crate) model_call_count: Arc<AtomicU64>,  // ADD THIS
}

// Increment in run_turn
impl Session {
    async fn run_turn(&self, ...) {
        self.model_call_count.fetch_add(1, Ordering::SeqCst);
        
        // Log count
        let count = self.model_call_count.load(Ordering::SeqCst);
        tracing::info!("📊 MODEL CALL #{count}");
    }
}
```

## Logging to File

### Option 1: Simple File Logger

Add to `Cargo.toml`:
```toml
[dependencies]
tracing-subscriber = { version = "0.3", features = ["file"] }
```

In `main.rs`:
```rust
use tracing_subscriber::{fmt, EnvFilter};

fn main() {
    let file = std::fs::File::create("codex_debug.log").unwrap();
    tracing_subscriber::fmt()
        .with_writer(file)
        .with_env_filter(EnvFilter::from_default_env())
        .init();
}
```

### Option 2: Structured Logging

```rust
use tracing_subscriber::layer::SubscriberExt;

tracing_subscriber::registry()
    .with(
        tracing_subscriber::fmt::layer()
            .with_file(true)
            .with_line_number(true)
            .with_target(true)
    )
    .with(EnvFilter::from_default_env())
    .init();
```

## Example Output

With proper logging, you'll see:

```
2024-01-15T10:30:00 INFO 🔵 MODEL CALL: turn_id=abc123, input_items=5
2024-01-15T10:30:00 DEBUG 📤 PROMPT: [user messages, tool definitions...]
2024-01-15T10:30:00 DEBUG 🛠️ TOOLS: 15
2024-01-15T10:30:01 INFO 📥 STREAMING RESPONSE START
2024-01-15T10:30:01 INFO 🔧 TOOL CALL: name=shell, call_id=call_1
2024-01-15T10:30:02 DEBUG 📦 PAYLOAD: {"command": ["python3", "-c", "print('hello')"]}
2024-01-15T10:30:02 INFO ✅ TOOL SUCCESS: {output: "hello\n"}
2024-01-15T10:30:03 INFO 🏁 COMPLETED: id=resp_1, tokens={input: 100, output: 50}
```

## Quick Start

1. Add the logging code snippets above to relevant functions
2. Run with: `RUST_LOG=debug cargo run`
3. For file output, implement one of the logging setups above
4. Set breakpoints in VS Code using the locations listed
5. Review the logs to understand the interaction flow

