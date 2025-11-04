# Model Interaction, Logging, and Tool Call Guide

This guide explains how to track model interactions, log prompts/responses, understand tool call timing, and identify when approval decisions are made during a task like "make slides".

---

## 1. Model Interaction Flow

### How Many Times Does the Agent Interact with the Model?

The agent interacts with the model **multiple times** in a loop until the task is complete. Here's the flow:

1. **Entry Point**: `run_task()` in `codex-rs/core/src/codex.rs:1504-1750`
   - Called once per user submission (e.g., "make slides for me")
   - Contains a `loop` that calls `run_turn()` repeatedly

2. **Each Model Call**: `run_turn()` in `codex-rs/core/src/codex.rs:1812-1860`
   - **Logging**: Line 1820 logs each model call: `tracing::info!("🔄 MODEL CALL: turn_id={}", turn_context.sub_id);`
   - Called once per model interaction
   - Builds prompt, sends to model, processes response

3. **Loop Continuation**: After `run_turn()` completes:
   - If model returns **tool calls** → execute tools → add results to history → **call `run_turn()` again**
   - If model returns **assistant message only** → task complete, exit loop
   - If token limit reached → auto-compact → **call `run_turn()` again**

### Example: "Make Slides" Task Flow

```
User: "make slides for me, introducing codex."
  ↓
run_task() starts
  ↓
Loop iteration 1: run_turn()
  → Model reasons and plans
  → Returns: update_plan tool call
  → Execute update_plan → add to history
  ↓
Loop iteration 2: run_turn()
  → Model sees plan in history
  → Returns: shell tool call (python script to generate slides)
  → Execute shell → add output to history
  ↓
Loop iteration 3: run_turn()
  → Model sees script output
  → Returns: shell tool call (run python script)
  → Execute shell → add output to history
  ↓
Loop iteration 4: run_turn()
  → Model sees execution results
  → Returns: assistant message "Slides created successfully"
  → Task complete, exit loop
```

**To count interactions**: Look for logs `🔄 MODEL CALL: turn_id=...` - each one is a model interaction.

---

## 2. Logging Prompts and Responses

### Method 1: Rollout Files (Automatic, Comprehensive)

**Location**: `~/.codex/sessions/YYYY/MM/DD/rollout-{timestamp}-{uuid}.jsonl`

**What's recorded**:
- Every user message
- Every assistant message
- Every tool call (FunctionCall)
- Every tool output (FunctionCallOutput)
- Every reasoning block (Reasoning)
- Session metadata (cwd, model, approval policy, etc.)
- Turn context (approval policy, sandbox policy per turn)

**How to inspect**:
```bash
# Find the latest session
ls -lt ~/.codex/sessions/2025/*/*/rollout-*.jsonl | head -1

# Pretty-print the entire conversation
jq -C . ~/.codex/sessions/2025/01/15/rollout-2025-01-15T14-30-45-*.jsonl

# Filter just user messages and assistant messages
jq -C 'select(.item.type == "response_item") | select(.item.payload.role == "user" or .item.payload.role == "assistant")' \
  ~/.codex/sessions/2025/01/15/rollout-*.jsonl

# Count model interactions (each TurnContext item = 1 turn)
jq 'select(.item.type == "turn_context") | .item.payload.model' \
  ~/.codex/sessions/2025/01/15/rollout-*.jsonl | wc -l
```

**Code Reference**: 
- Recording: `codex-rs/core/src/rollout/recorder.rs:156-199`
- Policy: `codex-rs/core/src/rollout/policy.rs:6-78` (determines what gets persisted)

### Method 2: Debug Logs (Real-time, HTTP Requests)

**Enable**: Set environment variable `RUST_LOG=debug` or `RUST_LOG=trace`

**What's logged**:

1. **Full HTTP Request Payload**: `codex-rs/core/src/chat_completions.rs:289`
   ```rust
   debug!(
       "POST to {}: {}",
       provider.get_full_url(&None),
       serde_json::to_string_pretty(&payload).unwrap_or_default()
   );
   ```
   - Shows complete JSON sent to OpenAI API
   - Includes: messages array, tools array, model, system instructions

2. **Model Call Entry**: `codex-rs/core/src/codex.rs:1820`
   ```rust
   tracing::info!("🔄 MODEL CALL: turn_id={}", turn_context.sub_id);
   ```

3. **Tool Call Dispatch**: `codex-rs/core/src/tools/registry.rs:64`
   ```rust
   info!("🔧 TOOL CALL: tool={}, call_id={}", tool_name, call_id_owned);
   ```

4. **MCP Tool Calls**: `codex-rs/core/src/mcp_tool_call.rs:25`
   ```rust
   info!("🎯 MCP CALL: server={}, tool={}, call_id={}", server, tool_name, call_id);
   ```

**How to use**:
```bash
# Run with debug logging
RUST_LOG=debug codex "make slides for me, introducing codex."

# Or trace for even more detail
RUST_LOG=trace codex "make slides for me, introducing codex." 2>&1 | tee codex-debug.log

# Filter just model calls and tool calls
RUST_LOG=info codex "make slides" 2>&1 | grep -E "(MODEL CALL|TOOL CALL|MCP CALL)"
```

### Method 3: Add Custom Logging

To log prompts/responses at specific points, add `tracing::info!` or `tracing::debug!` at:

1. **Before sending prompt**: `codex-rs/core/src/codex.rs:1820` (already exists)
2. **After receiving response**: Add after `try_run_turn()` in `codex-rs/core/src/codex.rs:1853`
3. **Tool call arguments**: `codex-rs/core/src/codex.rs:1978` (already logs preview)

Example addition:
```rust
// In codex-rs/core/src/codex.rs, after line 1853
match try_run_turn(...).await {
    Ok(output) => {
        tracing::debug!("✅ MODEL RESPONSE: turn_id={}, items={}", 
            turn_context.sub_id, 
            output.processed_items.len()
        );
        return Ok(output);
    }
    // ...
}
```

---

## 3. Tool Call Timing

### When Are Tools Called?

Tools are called **immediately after the model returns a tool call in the streaming response**, within the same `run_turn()` call.

**Flow**:
```
run_turn()
  ↓
Stream model response (SSE)
  ↓
Event: ResponseEvent::OutputItemDone(FunctionCall)
  ↓
ToolRouter::build_tool_call() extracts tool name + arguments
  ↓
tool_runtime.handle_tool_call() dispatches
  ↓
Tool executes (may request approval here)
  ↓
Result added to conversation history
  ↓
If more tool calls → process them
  ↓
run_turn() completes
  ↓
run_task() loop: if tool outputs exist → call run_turn() again with updated history
```

**Code Locations**:
- Stream processing: `codex-rs/core/src/codex.rs:1955-2004` (loop over `ResponseEvent`)
- Tool call detection: `codex-rs/core/src/codex.rs:1972-1983`
- Dispatch: `codex-rs/core/src/tools/router.rs:127-188`

### Parallel vs Sequential Tool Calls

- **Parallel tool calls**: If model returns multiple tool calls and `parallel_tool_calls=true`, all execute concurrently
- **Sequential tool calls**: If model returns one tool call, waits for result, then model decides next action in next `run_turn()`

**Determined by**: `model_family.supports_parallel_tool_calls` in `codex-rs/core/src/model_family.rs`

---

## 4. Approval Decision Points

### When Does the Agent Request Approval?

Approval is requested **BEFORE tool execution**, not after reasoning. The decision is made based on:

1. **Command safety check**: `is_known_safe_command()` or `command_might_be_dangerous()`
2. **Approval policy**: `AskForApproval` enum (UnlessTrusted, OnFailure, OnRequest, Never)
3. **Sandbox policy**: `SandboxPolicy` (ReadOnly, WorkspaceWrite, DangerFullAccess)
4. **Tool escalation flag**: `with_escalated_permissions` in tool call

### Decision Flow

```
Model returns tool call (e.g., shell command)
  ↓
ToolOrchestrator::run() called
  ↓
tool.wants_initial_approval() checks:
  - Is command in whitelist? → Auto-approve
  - Is command in blacklist? → Request approval
  - Approval policy?
    - UnlessTrusted: Only whitelisted auto-approved
    - OnFailure: Auto-approve, but escalate on sandbox denial
    - OnRequest: Check if model set with_escalated_permissions or dangerous
    - Never: Never ask, fail on sandbox denial
  ↓
If approval needed → request user → wait for decision
  ↓
If approved or auto-approved → execute tool (first in sandbox)
  ↓
If sandbox denies → check escalation policy → may request approval again
  ↓
Tool completes → result returned to model
```

**Code Locations**:
- Initial approval: `codex-rs/core/src/tools/orchestrator.rs:44-72`
- Shell safety check: `codex-rs/core/src/tools/runtimes/shell.rs:106-134`
- Sandbox denial escalation: `codex-rs/core/src/tools/orchestrator.rs:95-139`

### Example: Approval in "Make Slides" Task

```
Turn 1: Model calls update_plan
  → No approval needed (plan tool is safe)
  → Execute immediately

Turn 2: Model calls shell("python3 generate_slides.py")
  → Check: is "python3" in whitelist? NO
  → Check: is it dangerous? NO (just python execution)
  → Approval policy: OnRequest
  → Check: with_escalated_permissions? NO
  → Check: sandbox policy: WorkspaceWrite
  → Decision: Auto-approve (run in sandbox)
  → Execute in sandbox (workspace writable)
  → Success → continue

Turn 3: Model calls shell("pip install python-pptx")
  → Check: is "pip install" in whitelist? NO
  → Check: is it dangerous? NO
  → Approval policy: OnRequest
  → Check: sandbox policy: WorkspaceWrite (no network by default)
  → Execute in sandbox → **Sandbox denies network access**
  → Detect sandbox denial (keyword: "network" or exit code)
  → Escalation policy: OnRequest allows escalation
  → **Request user approval** to retry without sandbox
  → User approves → Execute without sandbox
  → Success → continue
```

### How to Log Approval Decisions

Add logging at:
- `codex-rs/core/src/tools/orchestrator.rs:47` - before `wants_initial_approval()` check
- `codex-rs/core/src/tools/orchestrator.rs:54` - when approval is requested
- `codex-rs/core/src/tools/orchestrator.rs:95` - when sandbox denial triggers escalation

---

## 5. Reasoning and Planning

### When Does Reasoning Happen?

Reasoning happens **during model streaming**, before tool calls are emitted. The model may stream:

1. **Reasoning deltas**: `ResponseEvent::ReasoningContentDelta` - raw thinking text
2. **Summary deltas**: `ResponseEvent::ReasoningSummaryDelta` - structured summary
3. **Final reasoning item**: `ResponseItem::Reasoning` - aggregated at end of turn

**Processing**:
- Streaming: `codex-rs/core/src/chat_completions.rs:478-580` (SSE processing)
- Aggregation: `codex-rs/core/src/chat_completions.rs:822-860` (aggregated stream)

### How to View Reasoning

1. **In rollout files**: Look for `"type": "reasoning"` items
   ```bash
   jq 'select(.item.type == "response_item") | select(.item.payload.type == "reasoning")' \
     ~/.codex/sessions/.../rollout-*.jsonl
   ```

2. **Enable raw reasoning**: Set `show_raw_agent_reasoning = true` in config
   - Emits `AgentReasoningRawContentDelta` events to UI

3. **In logs**: With `RUST_LOG=trace`, reasoning deltas are logged as they stream

---

## 6. Practical Example: Tracing a "Make Slides" Task

### Step-by-Step Tracing

1. **Start task**:
   ```bash
   RUST_LOG=info codex "make slides for me, introducing codex." 2>&1 | tee slides-task.log
   ```

2. **Count model interactions**:
   ```bash
   grep "🔄 MODEL CALL" slides-task.log | wc -l
   ```

3. **View all tool calls**:
   ```bash
   grep "🔧 TOOL CALL\|🎯 MCP CALL" slides-task.log
   ```

4. **Inspect rollout file**:
   ```bash
   # Find latest rollout
   LATEST=$(ls -t ~/.codex/sessions/2025/*/*/rollout-*.jsonl | head -1)
   
   # Extract all model turns with prompts
   jq -r 'select(.item.type == "turn_context") | "=== Turn: \(.item.payload.model) ==="' $LATEST
   
   # Extract user messages
   jq -r 'select(.item.type == "response_item") | select(.item.payload.role == "user") | .item.payload.content[0].text' $LATEST
   
   # Extract assistant messages
   jq -r 'select(.item.type == "response_item") | select(.item.payload.role == "assistant") | .item.payload.content[0].text' $LATEST
   
   # Extract tool calls
   jq -r 'select(.item.type == "response_item") | select(.item.payload.type == "function_call") | "\(.item.payload.name): \(.item.payload.arguments)"' $LATEST
   
   # Extract reasoning
   jq -r 'select(.item.type == "response_item") | select(.item.payload.type == "reasoning") | .item.payload.content[0].text' $LATEST
   ```

5. **Reconstruct full conversation**:
   ```bash
   jq -s 'sort_by(.timestamp) | .[] | "\(.timestamp) [\(.item.type)] \(.item.payload.role // .item.payload.type // "meta")"' $LATEST
   ```

---

## 7. Summary

- **Model interactions**: Each `run_turn()` call = 1 interaction, logged with `🔄 MODEL CALL`
- **Prompts/responses**: Automatically saved in rollout files (`~/.codex/sessions/.../rollout-*.jsonl`), also logged at debug level
- **Tool calls**: Execute immediately after model returns them, logged with `🔧 TOOL CALL` or `🎯 MCP CALL`
- **Approval**: Checked BEFORE tool execution, based on safety + policy + sandbox, may escalate on sandbox denial
- **Reasoning**: Streamed during model response, captured in rollout files, visible with `show_raw_agent_reasoning = true`

Use rollout files for comprehensive analysis, debug logs for real-time monitoring, and custom logging for specific breakpoints.

---

## 8. 代码实现：如何记录这些内容

### 8.1 记录完整的 Prompt 和 Response

#### 方法 A: 在 `run_turn()` 中记录 Prompt

在 `codex-rs/core/src/codex.rs` 的 `run_turn()` 函数中，构建完 `Prompt` 后添加日志：

```rust
// 在 codex-rs/core/src/codex.rs:1836 附近
let prompt = Prompt {
    input,
    tools: router.specs(),
    parallel_tool_calls,
    base_instructions_override: turn_context.base_instructions.clone(),
    output_schema: turn_context.final_output_json_schema.clone(),
};

// 添加：记录完整的 prompt
let full_instructions = prompt.get_full_instructions(&turn_context.client.get_model_family());
let formatted_input = prompt.get_formatted_input();
tracing::debug!(
    turn_id = %turn_context.sub_id,
    model = %turn_context.client.get_model(),
    instructions_len = full_instructions.len(),
    input_items = formatted_input.len(),
    tools_count = prompt.tools.len(),
    "📤 PROMPT TO MODEL"
);
// 记录详细的 prompt 内容（仅在 trace 级别）
tracing::trace!(
    turn_id = %turn_context.sub_id,
    instructions = %full_instructions,
    input = ?serde_json::to_value(&formatted_input).unwrap_or_default(),
    tools = ?prompt.tools.iter().map(|t| &t.name()).collect::<Vec<_>>(),
    "📤 PROMPT DETAILS"
);
```

#### 方法 B: 在 `try_run_turn()` 中记录 Response

在 `codex-rs/core/src/codex.rs` 的 `try_run_turn()` 函数中，处理完响应后添加日志：

```rust
// 在 codex-rs/core/src/codex.rs:2080 附近，处理完所有响应项后
match try_run_turn(...).await {
    Ok(output) => {
        // 添加：记录响应摘要
        let response_items: Vec<String> = output.processed_items
            .iter()
            .map(|item| match &item.item {
                ResponseItem::Message { role, .. } => format!("Message({})", role),
                ResponseItem::FunctionCall { name, .. } => format!("FunctionCall({})", name),
                ResponseItem::Reasoning { .. } => "Reasoning".to_string(),
                _ => "Other".to_string(),
            })
            .collect();
        
        tracing::debug!(
            turn_id = %turn_context.sub_id,
            response_items = ?response_items,
            items_count = output.processed_items.len(),
            "📥 RESPONSE FROM MODEL"
        );
        
        // 记录详细的响应内容（仅在 trace 级别）
        tracing::trace!(
            turn_id = %turn_context.sub_id,
            response = ?serde_json::to_value(&output.processed_items).unwrap_or_default(),
            "📥 RESPONSE DETAILS"
        );
        
        return Ok(output);
    }
    Err(e) => {
        tracing::error!(
            turn_id = %turn_context.sub_id,
            error = ?e,
            "❌ MODEL CALL FAILED"
        );
        // ... 现有错误处理
    }
}
```

#### 方法 C: 记录 HTTP 请求/响应（在 `client.rs` 中）

在 `codex-rs/core/src/client.rs` 的 `stream_responses()` 函数中，已经有 `debug!` 日志记录 payload，但可以增强：

```rust
// 在 codex-rs/core/src/client.rs:291 附近的 attempt_stream_responses() 中
async fn attempt_stream_responses(...) -> Result<ResponseStream, StreamAttemptError> {
    // ... 现有代码构建 payload_json ...
    
    // 增强日志：记录完整的请求（包括敏感信息过滤）
    let sanitized_payload = sanitize_payload_for_logging(&payload_json);
    tracing::debug!(
        attempt = attempt,
        url = %self.provider.get_full_url(&auth),
        payload_size = payload_json.to_string().len(),
        "📤 HTTP REQUEST"
    );
    tracing::trace!(
        attempt = attempt,
        payload = %serde_json::to_string_pretty(&sanitized_payload).unwrap_or_default(),
        "📤 HTTP REQUEST DETAILS"
    );
    
    // ... 发送请求 ...
    
    // 记录响应状态
    if let Ok(resp) = &res {
        tracing::debug!(
            attempt = attempt,
            status = resp.status().as_u16(),
            "📥 HTTP RESPONSE"
        );
    }
    
    // ... 其余代码 ...
}

// 辅助函数：过滤敏感信息
fn sanitize_payload_for_logging(payload: &serde_json::Value) -> serde_json::Value {
    let mut sanitized = payload.clone();
    // 移除或遮蔽敏感字段（如果有）
    // 例如：API keys, tokens 等
    sanitized
}
```

### 8.2 记录工具调用详情

#### 增强 `registry.rs` 中的工具调用日志

在 `codex-rs/core/src/tools/registry.rs` 的 `dispatch()` 函数中：

```rust
// 在 codex-rs/core/src/tools/registry.rs:57 附近
pub async fn dispatch(
    &self,
    invocation: ToolInvocation,
) -> Result<ResponseInputItem, FunctionCallError> {
    let tool_name = invocation.tool_name.clone();
    let call_id_owned = invocation.call_id.clone();
    
    // 现有日志
    info!(
        "🔧 TOOL CALL: tool={}, call_id={}",
        tool_name, call_id_owned
    );
    
    // 增强：记录工具调用的完整参数
    let payload_str = match &invocation.payload {
        ToolPayload::Function { arguments } => arguments.clone(),
        ToolPayload::Custom { input } => input.clone(),
        ToolPayload::LocalShell { params } => serde_json::to_string(params).unwrap_or_default(),
        ToolPayload::UnifiedExec { arguments } => arguments.clone(),
        ToolPayload::Mcp { raw_arguments, .. } => raw_arguments.clone(),
    };
    
    tracing::debug!(
        tool = %tool_name,
        call_id = %call_id_owned,
        turn_id = %invocation.turn.sub_id,
        payload = %payload_str,
        "🔧 TOOL CALL DETAILS"
    );
    
    // ... 执行工具 ...
    
    // 记录工具执行结果
    match result {
        Ok(output_item) => {
            let output_preview = match &output_item {
                ResponseInputItem::FunctionCallOutput { output, .. } => {
                    telemetry_preview(&output.content)
                }
                ResponseInputItem::CustomToolCallOutput { output, .. } => {
                    telemetry_preview(output)
                }
                ResponseInputItem::McpToolCallOutput { result, .. } => {
                    format!("{result:?}")
                }
                _ => "unknown".to_string(),
            };
            
            tracing::debug!(
                tool = %tool_name,
                call_id = %call_id_owned,
                output_preview = %output_preview,
                "✅ TOOL CALL SUCCESS"
            );
        }
        Err(e) => {
            tracing::warn!(
                tool = %tool_name,
                call_id = %call_id_owned,
                error = ?e,
                "❌ TOOL CALL FAILED"
            );
        }
    }
    
    result
}

// 辅助函数：截断长内容用于日志
fn telemetry_preview(content: &str) -> String {
    const MAX_PREVIEW_LEN: usize = 500;
    if content.len() <= MAX_PREVIEW_LEN {
        content.to_string()
    } else {
        format!("{}... ({} bytes total)", &content[..MAX_PREVIEW_LEN], content.len())
    }
}
```

### 8.3 记录审批决策

#### 在 `orchestrator.rs` 中记录审批流程

在 `codex-rs/core/src/tools/orchestrator.rs` 的 `run()` 函数中：

```rust
// 在 codex-rs/core/src/tools/orchestrator.rs:44 附近
pub async fn run<Rq, Out, T>(
    &mut self,
    tool: &mut T,
    req: &Rq,
    tool_ctx: &ToolCtx<'_>,
    turn_ctx: &crate::codex::TurnContext,
    approval_policy: AskForApproval,
) -> Result<Out, ToolError>
where
    T: ToolRuntime<Rq, Out>,
{
    let tool_name = tool_ctx.tool_name.clone();
    let call_id = tool_ctx.call_id.clone();
    
    // 记录审批检查开始
    let needs_initial_approval =
        tool.wants_initial_approval(req, approval_policy, &turn_ctx.sandbox_policy);
    
    tracing::debug!(
        tool = %tool_name,
        call_id = %call_id,
        turn_id = %turn_ctx.sub_id,
        needs_approval = needs_initial_approval,
        approval_policy = ?approval_policy,
        sandbox_policy = ?turn_ctx.sandbox_policy,
        "🔐 APPROVAL CHECK"
    );
    
    // ... 现有审批逻辑 ...
    
    if needs_initial_approval {
        tracing::info!(
            tool = %tool_name,
            call_id = %call_id,
            turn_id = %turn_ctx.sub_id,
            "⏸️  REQUESTING USER APPROVAL"
        );
        
        let approval_ctx = ApprovalCtx { ... };
        let decision = tool.start_approval_async(req, approval_ctx).await;
        
        tracing::info!(
            tool = %tool_name,
            call_id = %call_id,
            turn_id = %turn_ctx.sub_id,
            decision = ?decision,
            "✅ APPROVAL DECISION"
        );
        
        match decision {
            ReviewDecision::Denied | ReviewDecision::Abort => {
                tracing::warn!(
                    tool = %tool_name,
                    call_id = %call_id,
                    "❌ TOOL CALL DENIED BY USER"
                );
                return Err(ToolError::Rejected("rejected by user".to_string()));
            }
            ReviewDecision::Approved | ReviewDecision::ApprovedForSession => {
                tracing::debug!(
                    tool = %tool_name,
                    call_id = %call_id,
                    "✅ TOOL CALL APPROVED BY USER"
                );
            }
        }
    } else {
        tracing::debug!(
            tool = %tool_name,
            call_id = %call_id,
            "✅ TOOL CALL AUTO-APPROVED"
        );
    }
    
    // 记录沙箱执行和升级
    let initial_sandbox = self
        .sandbox
        .select_initial(&turn_ctx.sandbox_policy, tool.sandbox_preference());
    
    tracing::debug!(
        tool = %tool_name,
        call_id = %call_id,
        sandbox = ?initial_sandbox,
        "🔒 EXECUTING IN SANDBOX"
    );
    
    // ... 执行工具 ...
    
    // 如果沙箱拒绝，记录升级
    if let Err(ToolError::Codex(CodexErr::Sandbox(SandboxErr::Denied { output }))) = result {
        tracing::warn!(
            tool = %tool_name,
            call_id = %call_id,
            sandbox_output = %format_exec_output_for_logging(output),
            "🚫 SANDBOX DENIED"
        );
        
        if tool.escalate_on_failure() {
            tracing::info!(
                tool = %tool_name,
                call_id = %call_id,
                "⬆️  ESCALATING TO UNSANDBOXED EXECUTION"
            );
            // ... 升级逻辑 ...
        }
    }
    
    result
}

fn format_exec_output_for_logging(output: &ExecToolCallOutput) -> String {
    format!(
        "exit_code={}, stdout_len={}, stderr_len={}",
        output.exit_code,
        output.stdout.text.len(),
        output.stderr.text.len()
    )
}
```

### 8.4 创建自定义日志文件

#### 创建一个专门的日志记录器模块

创建新文件 `codex-rs/core/src/logging/model_interaction_logger.rs`：

```rust
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use serde::{Deserialize, Serialize};
use time::OffsetDateTime;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ModelInteractionLog {
    pub timestamp: String,
    pub turn_id: String,
    pub interaction_number: usize,
    pub model: String,
    pub prompt: PromptLog,
    pub response: ResponseLog,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct PromptLog {
    pub instructions: String,
    pub input_items_count: usize,
    pub tools_count: usize,
    pub tool_names: Vec<String>,
    // 可选：完整内容（可能很大）
    pub full_input: Option<serde_json::Value>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ResponseLog {
    pub items_count: usize,
    pub item_types: Vec<String>,
    pub has_reasoning: bool,
    pub tool_calls: Vec<ToolCallLog>,
    // 可选：完整内容
    pub full_response: Option<serde_json::Value>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ToolCallLog {
    pub tool_name: String,
    pub call_id: String,
    pub payload_preview: String,
    pub needs_approval: bool,
    pub approval_decision: Option<String>,
    pub sandbox_type: Option<String>,
    pub escalated: bool,
    pub success: bool,
    pub output_preview: Option<String>,
}

pub struct ModelInteractionLogger {
    file: Arc<Mutex<std::fs::File>>,
    interaction_counter: Arc<Mutex<usize>>,
}

impl ModelInteractionLogger {
    pub fn new(log_path: PathBuf) -> std::io::Result<Self> {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_path)?;
        
        Ok(Self {
            file: Arc::new(Mutex::new(file)),
            interaction_counter: Arc::new(Mutex::new(0)),
        })
    }
    
    pub fn log_interaction(&self, log: ModelInteractionLog) -> std::io::Result<()> {
        let mut counter = self.interaction_counter.lock().unwrap();
        *counter += 1;
        let interaction_number = *counter;
        drop(counter);
        
        let mut log_entry = log;
        log_entry.interaction_number = interaction_number;
        
        let json = serde_json::to_string(&log_entry)?;
        let mut file = self.file.lock().unwrap();
        writeln!(file, "{}", json)?;
        file.flush()?;
        
        Ok(())
    }
}

// 使用示例：在 Session 中初始化
impl Session {
    // 在 Session::new() 中
    pub(crate) fn new(...) -> Self {
        // ... 现有初始化代码 ...
        
        // 可选：创建模型交互日志记录器
        let interaction_logger = if let Ok(codex_home) = std::env::var("CODEX_HOME") {
            let log_path = PathBuf::from(codex_home)
                .join("model_interactions")
                .join(format!("interactions-{}.jsonl", 
                    OffsetDateTime::now_utc().format(
                        &time::format_description::parse(
                            "[year]-[month]-[day]T[hour]-[minute]-[second]"
                        ).unwrap()
                    ).unwrap()
                ));
            if let Ok(logger) = ModelInteractionLogger::new(log_path) {
                Some(Arc::new(logger))
            } else {
                None
            }
        } else {
            None
        };
        
        // ... 其余代码 ...
    }
}

// 在 run_turn() 中使用
pub(crate) async fn run_turn(...) -> CodexResult<TurnRunResult> {
    // ... 构建 prompt ...
    
    // 记录 prompt
    if let Some(logger) = &sess.model_interaction_logger {
        let prompt_log = PromptLog {
            instructions: full_instructions.to_string(),
            input_items_count: formatted_input.len(),
            tools_count: prompt.tools.len(),
            tool_names: prompt.tools.iter().map(|t| t.name().to_string()).collect(),
            full_input: if std::env::var("CODEX_LOG_FULL_PROMPTS").is_ok() {
                Some(serde_json::to_value(&formatted_input).unwrap_or_default())
            } else {
                None
            },
        };
        // 先创建部分日志，等响应后再完成
    }
    
    // ... 调用模型 ...
    
    // 记录响应
    match try_run_turn(...).await {
        Ok(output) => {
            if let Some(logger) = &sess.model_interaction_logger {
                let response_log = ResponseLog {
                    items_count: output.processed_items.len(),
                    item_types: output.processed_items.iter()
                        .map(|item| format!("{:?}", item.item))
                        .collect(),
                    has_reasoning: output.processed_items.iter()
                        .any(|item| matches!(item.item, ResponseItem::Reasoning { .. })),
                    tool_calls: vec![], // 从工具调用日志中收集
                    full_response: if std::env::var("CODEX_LOG_FULL_RESPONSES").is_ok() {
                        Some(serde_json::to_value(&output.processed_items).unwrap_or_default())
                    } else {
                        None
                    },
                };
                
                let interaction_log = ModelInteractionLog {
                    timestamp: OffsetDateTime::now_utc().to_string(),
                    turn_id: turn_context.sub_id.clone(),
                    interaction_number: 0, // 由 logger 设置
                    model: turn_context.client.get_model().to_string(),
                    prompt: prompt_log,
                    response: response_log,
                };
                
                let _ = logger.log_interaction(interaction_log);
            }
            return Ok(output);
        }
        // ...
    }
}
```

### 8.5 使用环境变量控制日志详细程度

在代码中检查环境变量来决定记录什么：

```rust
// 在 codex-rs/core/src/codex.rs 顶部或配置中
fn should_log_full_prompts() -> bool {
    std::env::var("CODEX_LOG_FULL_PROMPTS")
        .map(|v| v == "1" || v.to_lowercase() == "true")
        .unwrap_or(false)
}

fn should_log_full_responses() -> bool {
    std::env::var("CODEX_LOG_FULL_RESPONSES")
        .map(|v| v == "1" || v.to_lowercase() == "true")
        .unwrap_or(false)
}

fn should_log_tool_calls() -> bool {
    std::env::var("CODEX_LOG_TOOL_CALLS")
        .map(|v| v == "1" || v.to_lowercase() == "true")
        .unwrap_or(true) // 默认记录
}

fn should_log_approval_decisions() -> bool {
    std::env::var("CODEX_LOG_APPROVAL")
        .map(|v| v == "1" || v.to_lowercase() == "true")
        .unwrap_or(true) // 默认记录
}
```

### 8.6 完整使用示例

运行 Codex 并启用详细日志：

```bash
# 启用所有日志
export RUST_LOG=debug
export CODEX_LOG_FULL_PROMPTS=1
export CODEX_LOG_FULL_RESPONSES=1
export CODEX_LOG_TOOL_CALLS=1
export CODEX_LOG_APPROVAL=1

# 运行任务
codex "make slides for me, introducing codex." 2>&1 | tee codex-full.log

# 查看日志
grep "📤 PROMPT" codex-full.log
grep "📥 RESPONSE" codex-full.log
grep "🔧 TOOL CALL" codex-full.log
grep "🔐 APPROVAL" codex-full.log
```

### 8.7 分析日志的辅助脚本

创建一个 Python 脚本来分析日志：

```python
#!/usr/bin/env python3
"""分析 Codex 模型交互日志"""
import json
import sys
from collections import defaultdict

def analyze_model_interactions(log_file):
    interactions = []
    tool_calls = defaultdict(int)
    approval_decisions = defaultdict(int)
    
    with open(log_file, 'r') as f:
        for line in f:
            # 解析 JSONL 格式的自定义日志
            try:
                entry = json.loads(line)
                if 'turn_id' in entry:
                    interactions.append(entry)
                    for tool_call in entry.get('response', {}).get('tool_calls', []):
                        tool_calls[tool_call['tool_name']] += 1
                        if tool_call.get('needs_approval'):
                            approval_decisions[tool_call.get('approval_decision', 'unknown')] += 1
            except json.JSONDecodeError:
                continue
    
    print(f"总交互次数: {len(interactions)}")
    print(f"\n工具调用统计:")
    for tool, count in sorted(tool_calls.items(), key=lambda x: -x[1]):
        print(f"  {tool}: {count}")
    
    print(f"\n审批决策统计:")
    for decision, count in sorted(approval_decisions.items(), key=lambda x: -x[1]):
        print(f"  {decision}: {count}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_logs.py <log_file>")
        sys.exit(1)
    analyze_model_interactions(sys.argv[1])
```

---

总结：通过这些代码修改，你可以：
1. ✅ 记录每次模型交互的完整 prompt 和 response
2. ✅ 记录所有工具调用的参数和结果
3. ✅ 记录审批决策的完整流程
4. ✅ 创建结构化的 JSONL 日志文件便于分析
5. ✅ 通过环境变量控制日志详细程度
6. ✅ 使用脚本分析日志数据
