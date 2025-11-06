# Simple Message Flow: "hello" Example

This document traces the complete flow when a user sends a simple message like "hello" that doesn't require any tool calls.

## Summary

**Number of Model Interactions: 1**

For a simple conversational message like "hello", the agent interacts with the LLM model **exactly once**. The model returns an assistant message without any tool calls, and the task completes immediately.

---

## Detailed Flow

### Phase 1: Input Reception

**Location**: `codex-rs/core/src/codex.rs:1131-1402` (`submission_loop()`)

1. User types "hello" in TUI/CLI/API
2. Input is converted to `Op::UserInput` or `Op::UserTurn`
3. `submission_loop()` receives the submission
4. `sess.new_turn_with_sub_id()` creates a `TurnContext`
5. `sess.spawn_task()` spawns a background task calling `run_task()`

### Phase 2: Task Initialization

**Location**: `codex-rs/core/src/codex.rs:1504-1540` (`run_task()`)

1. `run_task()` is called with:
   - `input`: `vec![UserInput::Text { text: "hello" }]`
   - `turn_context`: Contains model config, policies, CWD, etc.
   - `task_kind`: `Regular`

2. User input is converted to `ResponseInputItem::Message`:
   ```rust
   ResponseInputItem::Message {
       role: "user",
       content: vec![ContentItem::InputText { text: "hello" }]
   }
   ```

3. Input is recorded to conversation history via `sess.record_input_and_rollout_usermsg()`

4. `EventMsg::TaskStarted` is sent to UI

### Phase 3: First (and Only) Turn

**Location**: `codex-rs/core/src/codex.rs:1538-1586` (`run_task()` loop iteration 1)

#### 3.1 Build Turn Input

**Location**: `codex-rs/core/src/codex.rs:1543-1565`

The conversation history is retrieved:
```rust
let turn_input: Vec<ResponseItem> = sess.history_snapshot().await;
```

For the first turn, `turn_input` contains:
1. **Environment Context** (`<environment_context>` block):
   ```json
   {
     "type": "message",
     "role": "user",
     "content": [{
       "type": "input_text",
       "text": "<environment_context>\nCWD: /Users/user/workspace\nSandbox: workspace-write\nApproval: on-request\n...</environment_context>"
     }]
   }
   ```

2. **User Instructions** (if present, from `AGENTS.md`):
   ```json
   {
     "type": "message",
     "role": "user",
     "content": [{
       "type": "input_text",
       "text": "<user_instructions>\nProject-specific instructions...\n</user_instructions>"
     }]
   }
   ```

3. **User Message**:
   ```json
   {
     "type": "message",
     "role": "user",
     "content": [{
       "type": "input_text",
       "text": "hello"
     }]
   }
   ```

#### 3.2 Call Model

**Location**: `codex-rs/core/src/codex.rs:1586-1594`

`run_turn()` is called with the conversation history.

#### 3.3 Prompt Construction

**Location**: `codex-rs/core/src/codex.rs:1812-1840` (`run_turn()`)

1. **MCP Tools Discovery**: `sess.services.mcp_connection_manager.list_all_tools()` - discovers any connected MCP tools

2. **Tool Router Creation**: `ToolRouter::from_config()` - builds tool registry from:
   - Built-in tools (shell, apply_patch, read_file, list_dir, grep_files, plan, etc.)
   - MCP tools (dynamically discovered)

3. **Prompt Struct Built**:
   ```rust
   Prompt {
       input: turn_input,  // The 2-3 items above (env context, user instructions, user message)
       tools: router.specs(),  // All available tool specifications
       parallel_tool_calls: true/false,  // Based on model family
       base_instructions_override: None,  // Or custom instructions if specified
       output_schema: None,  // Or JSON schema if structured output requested
   }
   ```

4. **Full Instructions Generated**: `prompt.get_full_instructions(&model_family)`
   - Default models: Content from `codex-rs/core/prompt.md` (entire file, ~1000+ lines)
   - GPT-5 models: Content from `codex-rs/core/gpt_5_codex_prompt.md`
   - Custom: If `base_instructions_override` is set

5. **Formatted Input**: `prompt.get_formatted_input()` - returns the input items as-is (or reserialized for freeform apply_patch)

#### 3.4 HTTP Request Sent

**Location**: `codex-rs/core/src/client.rs:132-172` (`stream_with_task_kind()`)

**For Responses API** (`stream_responses()`):
```json
POST https://api.openai.com/v1/responses
{
  "model": "gpt-4o",
  "instructions": "<full content of prompt.md>",
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "<environment_context>..."}]
    },
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "hello"}]
    }
  ],
  "tools": [
    {"name": "shell", "description": "...", "parameters": {...}},
    {"name": "apply_patch", "description": "...", "parameters": {...}},
    // ... all available tools
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": {...},
  "store": false,
  "stream": true,
  "include": ["reasoning.encrypted_content"]
}
```

**For Chat Completions API** (`stream_chat_completions()`):
```json
POST https://api.openai.com/v1/chat/completions
{
  "model": "gpt-4o",
  "messages": [
    {
      "role": "system",
      "content": "<full content of prompt.md>"
    },
    {
      "role": "user",
      "content": "<environment_context>..."
    },
    {
      "role": "user",
      "content": "hello"
    }
  ],
  "tools": [...],
  "tool_choice": "auto",
  "stream": true
}
```

#### 3.5 Response Processing

**Location**: `codex-rs/core/src/codex.rs:2077-2113` (`try_run_turn()`)

The SSE stream is processed event by event:

1. **`ResponseEvent::Created`**: Response started
2. **`ResponseEvent::OutputTextDelta("Hello")`**: Streaming assistant message text
3. **`ResponseEvent::OutputTextDelta("! How can")`**: More text chunks
4. **`ResponseEvent::OutputTextDelta(" I help you?")`**: Final text chunks
5. **`ResponseEvent::OutputItemDone(Message { role: "assistant", content: "Hello! How can I help you?" })`**: Final message item
6. **`ResponseEvent::Completed { response_id: "...", token_usage: {...} }`**: Turn complete

**Important**: No `FunctionCall` items are received, meaning no tool calls were made.

#### 3.6 Turn Result Processing

**Location**: `codex-rs/core/src/codex.rs:1922-2002` (`run_turn()`)

1. All `ProcessedResponseItem`s are collected
2. For a simple message, `processed_items` contains only:
   ```rust
   ProcessedResponseItem {
       item: ResponseItem::Message {
           role: "assistant",
           content: vec![ContentItem::OutputText { text: "Hello! How can I help you?" }]
       },
       response: None  // No tool output
   }
   ```

3. Response summary is logged (if enabled)

### Phase 4: Loop Decision

**Location**: `codex-rs/core/src/codex.rs:1613-1750` (`run_task()` loop continuation)

1. `processed_items` are iterated
2. Since `item` is `ResponseItem::Message` and `response` is `None`, it matches the first branch:
   ```rust
   (ResponseItem::Message { role, .. }, None) if role == "assistant" => {
       items_to_record_in_conversation_history.push(item);
   }
   ```

3. `responses` vector remains **empty** (no tool outputs)

4. Token limit check passes (not reached)

5. **Loop Exit Condition** (`codex-rs/core/src/codex.rs:1735`):
   ```rust
   if responses.is_empty() {
       last_agent_message = get_last_assistant_message_from_turn(...);
       sess.notifier().notify(&UserNotification::AgentTurnComplete {...});
       break;  // Exit the loop!
   }
   ```

### Phase 5: Task Completion

**Location**: `codex-rs/core/src/codex.rs:1750-1758`

1. `EventMsg::TaskComplete` is sent with the final assistant message
2. `run_task()` returns `Some("Hello! How can I help you?")`
3. Background task terminates
4. UI displays the response and awaits next user input

---

## Complete Example: Request and Response

### Request to Model (Simplified)

**Prompt Structure**:
```
Base Instructions (from prompt.md):
  - You are Codex, a coding assistant...
  - [~1000 lines of instructions about tool usage, safety, etc.]

Input Items:
  1. Environment Context:
     <environment_context>
     CWD: /Users/user/workspace
     Sandbox: workspace-write
     Approval: on-request
     Shell: /bin/bash
     </environment_context>

  2. User Message:
     hello

Tools Available:
  - shell: Execute shell commands
  - apply_patch: Apply file changes
  - read_file: Read file contents
  - list_dir: List directory contents
  - ... (all other tools)
```

### Response from Model

**SSE Stream Events** (simplified):
```
event: response.created
data: {"response": {"id": "resp_123"}}

event: response.output_text.delta
data: {"delta": "Hello"}

event: response.output_text.delta
data: {"delta": "! How can"}

event: response.output_text.delta
data: {"delta": " I help you?"}

event: response.output_item.done
data: {"item": {"type": "message", "role": "assistant", "content": "Hello! How can I help you?"}}

event: response.completed
data: {"response_id": "resp_123", "token_usage": {"prompt_tokens": 1500, "completion_tokens": 10}}
```

**Final Parsed Response**:
- **Type**: `ResponseItem::Message`
- **Role**: `"assistant"`
- **Content**: `"Hello! How can I help you?"`
- **Tool Calls**: None
- **Reasoning**: None (or empty if reasoning was enabled)

---

## Key Code Locations

| Action | File | Line Range |
|--------|------|------------|
| Task entry point | `codex-rs/core/src/codex.rs` | 1504-1540 |
| Loop decision | `codex-rs/core/src/codex.rs` | 1735-1750 |
| Turn execution | `codex-rs/core/src/codex.rs` | 1812-2002 |
| Prompt construction | `codex-rs/core/src/codex.rs` | 1836-1910 |
| HTTP request | `codex-rs/core/src/client.rs` | 174-260 |
| Response processing | `codex-rs/core/src/codex.rs` | 2077-2113 |

---

## Comparison: Simple Message vs. Complex Task

| Aspect | Simple Message ("hello") | Complex Task ("make slides") |
|--------|-------------------------|------------------------------|
| Model Interactions | **1** | **4-10+** |
| Tool Calls | **0** | **Multiple** (plan, shell, apply_patch) |
| Loop Iterations | **1** | **4-10+** |
| Response Type | `Message` only | `Message` + `FunctionCall` + `FunctionCallOutput` |
| Task Duration | **~1-2 seconds** | **30+ seconds** |

---

## Notes

1. **Logging**: With the logging enhancements added, you would see:
   - `🔄 MODEL CALL: turn_id=1` at the start of `run_turn()`
   - `📥 PROMPT TO MODEL` with prompt details
   - `📥 RESPONSE FROM MODEL` with response summary
   - `✅ MODEL RESPONSE: turn_id=1, items=["Message(assistant)"]`

2. **Reasoning**: If reasoning is enabled, you would also see `Reasoning` items in the stream, but they don't affect the loop continuation logic.

3. **Token Usage**: Even simple messages consume tokens for:
   - Base instructions (~1000+ tokens)
   - Environment context (~50 tokens)
   - User message (~5 tokens)
   - Tool specifications (~500-1000 tokens depending on available tools)
   - Total: ~1500-2000 tokens per turn

4. **MCP Tools**: If MCP servers are connected, their tools are included in the prompt, but the model may not use them for simple greetings.
