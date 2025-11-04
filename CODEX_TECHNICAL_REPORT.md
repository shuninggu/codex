# Codex Agent Technical Report

## 1. System Architecture

### 1.1 Core Components

#### 1.1.1 Session Management
- **Session**: `codex-rs/core/src/codex.rs:245-252`
- **TurnContext**: `codex-rs/core/src/codex.rs:254-272`
- Lifecycle: Session → Turn → Task → Tool calls

#### 1.1.2 Codex Spawn & Initialization
- `Codex::spawn()`: `codex-rs/core/src/codex.rs:155-209`
- Submission loop: `submission_loop()`: `codex-rs/core/src/codex.rs:1131-1402`
- Event channels & communication

### 1.2 Execution Model

#### 1.2.1 Task Orchestration
- `run_task()`: `codex-rs/core/src/codex.rs:1504-1650`
- `run_turn()`: `codex-rs/core/src/codex.rs:1812-`
- Multi-turn conversations
- Parallel tool execution

**Task Lifecycle Overview**: A task represents a complete user request from initial input to final completion. The `run_task()` function orchestrates multiple turns within a single task, maintaining conversation history and coordinating tool execution. Each turn (`run_turn()`) represents one interaction with the LLM, which may result in tool calls that are executed before the next turn begins. The loop continues until the model returns a final assistant message with no tool calls, at which point the task completes.

**Multi-Turn Loop**: The core loop in `run_task()` repeatedly calls `run_turn()` with accumulated conversation history. After each turn, if tool calls were executed, their outputs are added to the conversation history and the loop continues. This enables the model to iteratively break down complex tasks into smaller steps, executing tools and observing results before proceeding. Token limit monitoring triggers automatic conversation compaction when the context window approaches capacity.

**Turn Context Management**: Each turn operates within a `TurnContext` that encapsulates the current working directory, approval policies, sandbox policies, model configuration, and tool availability. The context persists across turns within a task, allowing the model to maintain awareness of its environment. Review mode creates an isolated context with a separate conversation history, enabling independent code review sessions without affecting the main conversation thread.

#### 1.2.2 Tool Registry & Dispatch
- `ToolRegistry`: `codex-rs/core/src/tools/registry.rs:37-48`
- `build_specs()`: `codex-rs/core/src/tools/spec.rs:862-1001`
- Handler pattern
- MCP integration

---

## 2. Safety & Sandboxing Framework

### 2.1 Sandbox Policies

#### 2.1.1 Policy Types
- **ReadOnly**: `codex-rs/protocol/src/protocol.rs:236-238`
- **WorkspaceWrite**: `codex-rs/protocol/src/protocol.rs:243-262`
- **DangerFullAccess**: `codex-rs/protocol/src/protocol.rs:233-234`

#### 2.1.2 File Access Permissions & Workspace Path Handling
- **Read operations**: All sandbox policies (`ReadOnly`, `WorkspaceWrite`, `DangerFullAccess`) allow reading files from anywhere on the disk. The `has_full_disk_read_access()` method always returns `true` regardless of policy: `codex-rs/protocol/src/protocol.rs:313-315`. This means agents can read files outside the workspace without requiring approval or logging workspace paths.
- **Write/Delete operations**: Writing and deleting files are restricted based on the sandbox policy:
  - **ReadOnly**: All write operations are blocked by the sandbox
  - **WorkspaceWrite**: Write operations are only allowed within writable roots (workspace directory, `/tmp`, `$TMPDIR`). Path checking is performed via `is_write_patch_constrained_to_writable_paths()`: `codex-rs/core/src/safety.rs:92-164`, which normalizes paths and verifies they fall within configured writable roots before allowing the operation
  - **DangerFullAccess**: No restrictions, can write anywhere
- **Workspace path in tool calls**: The `workdir`/`cwd` parameter appears in tool call arguments (e.g., `{"command":["rm","t2.txt"],"workdir":"/Users/user/workspace"}`) for two purposes:
  1. **Command execution context**: Shell commands execute relative to this directory
  2. **Path resolution**: Relative paths in tool calls are resolved against this directory to determine absolute paths for write permission checks
- **Workspace path logging**: The workspace path is not logged separately for every file operation. Instead, it appears:
  - In the tool call arguments JSON when a tool requires a working directory context
  - In session metadata at the start of a session: `codex-rs/core/src/rollout/recorder.rs:133-143`
  - Only when writing/deleting files outside writable roots does the system need to check and potentially log workspace boundaries for approval requests
- **Path normalization**: When checking if a file operation is within writable roots, paths are normalized (removing `.` and resolving `..` components) without touching the filesystem, ensuring consistent comparison even for non-existent paths: `codex-rs/core/src/safety.rs:109-119`

### 2.2 Command Safety Classification

#### 2.2.1 Whitelist Mechanism
- Implementation: `codex-rs/core/src/command_safety/is_safe_command.rs:43-135`
- **Pure read commands**: cat, cd, echo, false, grep, head, ls, nl, pwd, tail, true, wc, which - automatically approved with 0-parameter checks
- **Conditional commands**: 
  - `find`: Safe unless dangerous options (-exec, -delete, -fls, etc.)
  - `rg`: Safe unless external execution (-pre, --hostname-bin, -z, --search-zip)
  - `git`: Only safe for read-only subcommands (branch, status, log, diff, show)
  - `cargo`: Only `check` subcommand is safe
  - `sed`: Safe for limited print patterns (`sed -n {N|M,N}p`)
- **Bash -lc parsing**: Supports parsing `bash -lc "command"` with operators (&&, ||, ;, |), checks each individual command in sequence

#### 2.2.2 Blacklist Mechanism  
- Implementation: `codex-rs/core/src/command_safety/is_dangerous_command.rs:3-36`
- **Git destructive**: `git reset`, `git rm` - hard resets and file deletion
- **Force deletion**: `rm -f`, `rm -rf` - mandatory approval required
- **Recursive sudo**: If command starts with `sudo`, recursively checks the underlying command
- **Detection**: Checks both direct command execution and parsed `bash -lc` sequences

#### 2.2.3 Static vs Runtime Analysis
- Static whitelist/blacklist
- Runtime sandbox enforcement
- Path-based checks for `apply_patch`

### 2.3 Approval System

#### 2.3.1 Approval Policies
- **UnlessTrusted**: `codex-rs/protocol/src/protocol.rs:204-210`
- **OnFailure**: `codex-rs/protocol/src/protocol.rs:212-216`
- **OnRequest**: `codex-rs/protocol/src/protocol.rs:218-220`
- **Never**: `codex-rs/protocol/src/protocol.rs:222-224`

#### 2.3.2 Decision Flow
- Initial approval: `wants_initial_approval()`: `codex-rs/core/src/tools/runtimes/shell.rs:106-134`
- Escalation mechanism: `codex-rs/core/src/tools/orchestrator.rs:95-139`
- Caching: `ApprovalStore`: `codex-rs/core/src/tools/sandboxing.rs:26-49`

#### 2.3.3 Sandbox Denial Detection
- Keywords: `SANDBOX_DENIED_KEYWORDS`: `codex-rs/core/src/exec.rs:109-134` - 7 cross-platform keywords including "operation not permitted", "permission denied", "read-only file system", "seccomp", "sandbox", "landlock", "failed to write file"
- Exit code analysis: Quick rejects for non-sandbox errors (2: misuse, 126: permission, 127: not found), allows all other non-zero exit codes to be considered potential sandbox denials
- Platform-specific signals: Linux seccomp SIGSYS detection using exit code calculation (128 + signal number)
- Heuristic approach: `is_likely_sandbox_denied()` combines exit codes + keyword matching + signal detection for robust cross-platform identification

---

## 3. Tool System

### 3.1 Tool Architecture

#### 3.1.1 Tool Types
- Built-in tools (shell, apply_patch, read_file, etc.)
- MCP tools (dynamic)
- Resource tools

#### 3.1.2 Tool Selection
- `ToolsConfig`: `codex-rs/core/src/tools/spec.rs:26-81` - determines shell type, apply_patch availability, web search, view image, MCP tools based on feature flags and model family
- Model family-based configuration: `codex-rs/core/src/model_family.rs:87-167` - each model family specifies base instructions, apply_patch preference (Freeform/Function), experimental tools (grep_files, list_dir, read_file, test_sync), reasoning support
- Feature flags: Centralized `Features` enum with stages (Experimental, Beta, Stable, Deprecated, Removed) - UnifiedExec, StreamableShell, ApplyPatchFreeform, ViewImageTool, WebSearchRequest, RmcpClient

### 3.2 Execution Layer

#### 3.2.1 Tool Orchestrator
- `ToolOrchestrator::run()`: `codex-rs/core/src/tools/orchestrator.rs:31-142`
- Approval → Sandbox → Execute → Retry
- Failure handling & escalation

#### 3.2.2 Tool Handlers
- Shell handler: `codex-rs/core/src/tools/handlers/shell.rs`
- Apply patch handler: `codex-rs/core/src/tools/handlers/apply_patch.rs`
- MCP handler: `codex-rs/core/src/tools/handlers/mcp.rs`

### 3.3 Special Tools

#### 3.3.1 apply_patch
- Safety assessment: `codex-rs/core/src/safety.rs:25-80`
- Path analysis: `is_write_patch_constrained_to_writable_paths()`: `codex-rs/core/src/safety.rs:92-164`
- Freeform vs Function type

#### 3.3.2 File System Tools
- `read_file`: `codex-rs/core/src/tools/handlers/read_file.rs`
- `list_dir`: `codex-rs/core/src/tools/handlers/list_dir.rs`
- `grep_files`: `codex-rs/core/src/tools/handlers/grep_files.rs`
- Independent from shell execution

---

## 4. Model Integration

### 4.1 Prompt System

#### 4.1.1 Prompt Structure
The prompt sent to the LLM consists of two main components: base instructions and formatted input. Base instructions are loaded from markdown files that contain the agent's system-level directives:

- `prompt.md`: `codex-rs/core/prompt.md` (default) - Standard instructions for most models
- `gpt_5_codex_prompt.md`: `codex-rs/core/gpt_5_codex_prompt.md` - Specialized instructions for GPT-5 family models
- `review_prompt.md`: `codex-rs/core/review_prompt.md` - Instructions specifically for code review mode

**Base Instructions Content**: These files contain high-level guidance about the agent's capabilities, tool usage, safety considerations, and expected behavior. They are loaded as strings and sent as the `instructions` field in the Responses API or converted to a system message in Chat Completions API.

**Formatted Input**: The conversation history is formatted into input items using `Prompt::get_formatted_input()` which converts `ResponseItem` enum variants into API-compatible formats. User messages become `{type: "message", role: "user", content: [...]}`, tool calls become function call items, tool outputs become tool response items, and reasoning items are included with their full content or summaries.

#### 4.1.2 Dynamic Prompt Construction
**Model Family Selection**: `ModelFamily::find_family_for_model()` in `codex-rs/core/src/model_family.rs:87-167` uses prefix matching to identify the model family (e.g., `o3`, `gpt-4.1`, `codex-`, `gpt-5-codex`) and retrieves the corresponding `ModelFamily` configuration. If no match is found, `derive_default_model_family()` provides fallback defaults. The model family determines which base instructions file to use, whether reasoning is supported, and which tools are available.

**Base Instructions Assignment**: Default models use `prompt.md`, GPT-5 family models automatically use `gpt_5_codex_prompt.md`, and users can override via `experimental.instructions_file` config setting. The selected instructions file is loaded at runtime and stored in `TurnContext.base_instructions`, which is then included in the `Prompt` struct during `run_turn()`.

**User Instructions Injection**: Project-specific instructions from `AGENTS.md` files (discovered via Git root traversal) are injected into the prompt as special content blocks. The content is wrapped in `<user_instructions>` tags and added to the conversation history as text content items. This allows projects to customize agent behavior without modifying system prompts.

**Environment Context**: Contextual information (current working directory, sandbox policy, approval policy) is injected as `<environment_context>` blocks, providing the model with awareness of its execution environment. This context is automatically added to review threads and can be included in normal turns via `Session::build_initial_context()`.

**Review Thread Isolation**: Review mode uses `REVIEW_PROMPT` constant instead of base instructions, creating a focused context for code review tasks. Review threads maintain a separate `ConversationHistory` instance (`review_thread_history`) that excludes parent session history and user instructions, ensuring the review model sees only the code context and review-specific instructions. Environment context is still injected to maintain awareness of the working directory.

**Tool Specifications**: Available tools are formatted as JSON schemas and included in the prompt. For Responses API, tools are sent as a `tools` array in the request payload. For Chat Completions API, tools are embedded in the messages structure. MCP tools are dynamically discovered and merged with built-in tools before prompt construction, so the model sees a unified tool list regardless of source.

### 4.2 LLM Interaction

#### 4.2.1 HTTP-Based Model Calls
Codex directly calls LLM providers via standard HTTP POST requests using the `reqwest` library. The system supports two API formats:

**Responses API (Experimental)**: `stream_responses()` in `codex-rs/core/src/client.rs:174-260` - An OpenAI experimental API that provides structured responses with built-in reasoning support. The request payload includes model configuration, full instructions (from `prompt.md` or model-specific prompts), formatted conversation history as input items, available tools as JSON schema, reasoning parameters, and streaming configuration. The endpoint is typically `https://api.openai.com/v1/responses` or provider-specific variants.

**Chat Completions API**: `stream_chat_completions()` in `codex-rs/core/src/chat_completions.rs:118-329` - The standard OpenAI Chat Completions API using message-based format. Conversation history is converted from `ResponseItem` enum to message array format, with tool calls embedded in assistant messages. The endpoint is `https://api.openai.com/v1/chat/completions` or provider equivalents.

**Request Construction Flow**: `ModelClient::stream_with_task_kind()` in `codex-rs/core/src/client.rs:132-172` dispatches to the appropriate API based on provider configuration. The request builder (`ModelProviderInfo::create_request_builder()` in `codex-rs/core/src/model_provider_info.rs:119-137`) constructs the HTTP POST request with authentication headers (Bearer token from API key or OAuth), provider-specific headers (e.g., `OpenAI-Beta: responses=experimental`), conversation metadata (conversation_id, session_id), and JSON payload. The `reqwest::Client` sends the request and receives Server-Sent Events (SSE) stream responses.

**Streaming Response Processing**: Both APIs return SSE streams that are processed incrementally. The `process_responses_sse()` and `process_chat_sse()` functions parse event streams, extract reasoning deltas, function calls, and messages, converting them into internal `ResponseEvent` enum variants. Tool calls trigger immediate execution while streaming continues, enabling parallel processing of model reasoning and tool execution.

#### 4.2.2 Tool Calling
Function calling is the primary mechanism for tool invocation. When the model returns a `FunctionCall` item in its response, the `ToolRouter::build_tool_call()` function in `codex-rs/core/src/tools/router.rs:57-130` extracts the tool name and arguments, resolving MCP tool names (format: `server__tool_name`) to server/tool pairs. The tool invocation is dispatched to the appropriate handler through the `ToolRegistry::dispatch()` function.

**Freeform Tools**: Some tools like `apply_patch` support both function-style (structured JSON arguments) and freeform (raw text) formats. The handler determines the payload type from the `ToolPayload` enum variant and parses accordingly. Freeform tools are useful for complex structured input that benefits from natural language formatting.

**Custom Tools**: Custom tools are protocol-level extensions that don't fit the standard function calling model. They include local shell calls (with special handling for interactive execution) and unified exec commands (PTY-based interactive execution). Custom tools maintain compatibility with legacy code paths while supporting new execution models.

#### 4.2.3 Reasoning & Summarization
Models with reasoning capabilities (e.g., o3, GPT-5) can return `Reasoning` items in their responses. The `supports_reasoning_summaries` flag in `ModelFamily` configuration indicates whether a model provides both detailed reasoning content and summary summaries. The `reasoning_summary_format` specifies the structure (e.g., bullet points, paragraphs).

**Important: Encrypted Reasoning Content in Responses API**: When using the Responses API with reasoning enabled, the system requests `reasoning.encrypted_content` in the `include` parameter (`codex-rs/core/src/client.rs:210`). This causes the API to return reasoning content in an encrypted format stored in the `encrypted_content` field, while the `content` field remains `null`. The `should_serialize_reasoning_content()` function in `codex-rs/protocol/src/models.rs:123-129` prevents serialization of `content` when it contains `ReasoningText` items. **Current limitation**: The codebase does not implement decryption of `encrypted_content`; it requires an additional backend API call to retrieve the decrypted reasoning content. The streaming `ReasoningContentDelta` events still provide reasoning text incrementally during streaming, but the final `Reasoning` item may only contain `encrypted_content` instead of plain text `content`. To view reasoning content, use the streaming deltas (`ResponseEvent::ReasoningContentDelta`) which contain the plain text reasoning, or rely on the `summary` field which is always available in plain text format.

**Streaming Reasoning**: Reasoning content streams in real-time as `ReasoningContentDelta` events, allowing the UI to display progressive reasoning updates. Summary sections arrive as `ReasoningSummaryDelta` events, providing high-level insights without the full reasoning text. The system captures both in conversation history and rollout files for post-analysis, while respecting the `show_raw_agent_reasoning` configuration to control UI display verbosity.

**Reasoning in Context**: Reasoning items are included in the conversation history and sent to the model in subsequent turns, enabling self-reflection and iterative refinement. However, for token efficiency, only reasoning summaries may be included in compacted history, with full reasoning preserved in rollout files.

---

## 5. Execution Flow & Lifecycle

### 5.1 User Input Processing
User input enters the system through three primary interfaces, all ultimately submitting operations to the `Session::submit()` method via the submission queue:

**TUI Input** (`codex-rs/tui/src/chatwidget.rs:1297-1337`): The terminal UI captures user keystrokes and constructs `UserInput` items (text and/or local images). When the user submits via Enter key, `submit_user_message()` creates `Op::UserInput { items }` and sends it through `codex_op_tx` channel. If a task is currently running, the input is queued and processed after the current turn completes.

**CLI Exec Mode** (`codex-rs/exec/src/lib.rs:165-350`): Non-interactive mode accepts prompts from command-line arguments or stdin. The `run_exec_mode()` function creates a `CodexConversation` instance, sends `Op::UserTurn` with complete turn configuration (cwd, approval policy, sandbox policy, model, reasoning effort/summary), and blocks waiting for `TaskComplete` events. This mode is designed for automation and scripting scenarios.

**App Server** (`codex-rs/app-server/src/codex_message_processor.rs:1096-1196`): The web/API interface receives JSON-RPC requests (`sendUserMessage` or `sendUserTurn`), maps wire format items to `CoreInputItem` enum, and submits `Op::UserInput` or `Op::UserTurn` to the conversation. The server maintains a `ConversationManager` that maps conversation IDs to `CodexConversation` instances, enabling multiple concurrent conversations.

**Submission Queue Processing**: All submissions flow through `submission_loop()` in `codex-rs/core/src/codex.rs:1131-1402`. This async loop receives `Submission` structs containing operations, processes them sequentially, and routes to appropriate handlers. `Op::UserInput` and `Op::UserTurn` trigger `sess.new_turn_with_sub_id()` to create or update `TurnContext`, then either inject input into a running task or spawn a new task via `sess.spawn_task()`.

### 5.2 Complete End-to-End Flow: From User Input to Task Completion

**Phase 1: Input Reception & Task Initiation** (Lines: `submission_loop()` → `spawn_task()` → `run_task()`)
1. User submits input (TUI/CLI/API) as `Op::UserInput` or `Op::UserTurn`
2. `submission_loop()` receives the `Submission` and extracts items
3. `sess.new_turn_with_sub_id()` creates/updates `TurnContext` with policies, model config, and CWD
4. If no task is running, `sess.spawn_task()` creates a new background task calling `run_task()`
5. `run_task()` initializes conversation history: records user input, seeds review thread history if in review mode, or adds to session history for normal turns

**Phase 2: Turn Execution Loop** (Lines: `run_task()` loop → `run_turn()` → `try_run_turn()`)
6. **Loop begins**: `run_task()` enters its main loop, which continues until task completion
7. **Build turn input**: Retrieve full conversation history via `sess.history_snapshot().await` (normal mode) or `review_thread_history.get_history()` (review mode)
8. **Call model**: Invoke `run_turn()` with accumulated history as input
9. **Prompt construction**: `run_turn()` builds `Prompt` struct containing:
   - `input`: Full conversation history as `ResponseItem` vector
   - `tools`: Tool specifications from `ToolRouter::from_config()` (built-in + MCP tools)
   - `base_instructions_override`: Custom instructions if specified
   - `output_schema`: JSON schema for structured output if requested
10. **Tool registry**: MCP tools are discovered via `sess.services.mcp_connection_manager.list_all_tools()` and merged with built-in tools
11. **Model API call**: `turn_context.client.stream_with_task_kind(prompt, task_kind)` dispatches to Responses API or Chat Completions API
12. **HTTP request**: `ModelClient` constructs HTTP POST request with JSON payload containing instructions, input items, tool schemas, reasoning config, and streaming flags
13. **SSE stream**: Receive Server-Sent Events stream from LLM provider, parse events into `ResponseEvent` enum variants

**Phase 3: Response Processing & Tool Execution** (Lines: `try_run_turn()` → stream processing → tool dispatch)
14. **Stream processing loop**: `try_run_turn()` iterates over SSE events in the response stream
15. **Event handling**: For each `ResponseEvent::OutputItemDone(item)`:
    - If `ResponseItem::FunctionCall`: Extract tool name and arguments via `ToolRouter::build_tool_call()`
    - Dispatch to `tool_runtime.handle_tool_call()` which calls `ToolRegistry::dispatch()`
    - Tool execution proceeds through approval checks, sandbox selection, and execution
    - Tool output is collected as `ProcessedResponseItem` and added to results
    - If `ResponseItem::Message`: Extract assistant message text for final response
    - If `ResponseItem::Reasoning`: Capture reasoning content and summaries
16. **Parallel execution**: If model returns multiple tool calls and `parallel_tool_calls=true`, all tools execute concurrently via `FuturesOrdered`
17. **Turn completion**: When stream ends with `ResponseEvent::Completed`, aggregate all processed items and return `TurnRunResult`

**Phase 4: Tool Call Execution Details** (Lines: `ToolRegistry::dispatch()` → `ToolOrchestrator::run()` → handler execution)
18. **Tool dispatch**: `registry.dispatch()` looks up handler by tool name, constructs `ToolInvocation` with session, turn context, call ID, and payload
19. **Handler selection**: MCP tools route through `McpHandler`, built-in tools use specific handlers (`ShellHandler`, `ApplyPatchHandler`, etc.)
20. **Approval flow**: `ToolOrchestrator::run()` checks `tool.wants_initial_approval()` based on command safety, approval policy, and sandbox policy:
    - If approval needed: `tool.start_approval_async()` requests user decision via UI event
    - Approval decision cached to prevent repeated prompts for same command
21. **Sandbox execution**: Select initial sandbox type based on policy and tool preferences, execute tool in sandboxed environment
22. **Sandbox denial detection**: If execution fails, `is_likely_sandbox_denied()` checks exit codes and output keywords
23. **Escalation**: If sandbox denied and `tool.escalate_on_failure()`, request approval for unsandboxed execution, retry without sandbox on approval
24. **Result formatting**: Tool output converted to `ResponseInputItem::FunctionCallOutput` or appropriate variant, returned to turn processing

**Phase 5: Loop Continuation & Completion** (Lines: `run_task()` loop continuation → history updates → task exit)
25. **Process turn results**: After `run_turn()` returns, `run_task()` processes `TurnRunResult`:
    - Extract assistant messages for final output
    - Add tool call outputs to conversation history via `sess.record_conversation_items()`
    - Update rollout recording with new items
26. **Token limit check**: If total tokens exceed `auto_compact_token_limit`, trigger inline auto-compaction task to summarize history
27. **Loop decision**: If turn produced tool calls, loop continues to step 7 with updated history. If turn produced only assistant message (no tool calls), task completes
28. **Task completion**: Send `EventMsg::TaskComplete` with final assistant message, return from `run_task()`, background task terminates
29. **Event propagation**: UI receives completion event, displays final response, and awaits next user input

### 5.3 Multi-Turn Conversations
- Turn isolation: Each turn creates a new `TurnContext` with unique `sub_id`, preserves session-wide state (approval cache, history) across turns
- Conversation history: `ConversationHistory` stores ordered `ResponseItem` vector (oldest → newest), normalizes call/output pairs, filters API messages, `Session.record_conversation_items()` appends to history + rollout recording
- Review threads: `is_review_mode` flag, separate `review_thread_history`, no parent session history contamination, uses `REVIEW_PROMPT`, exits with `ExitedReviewMode` event

**History Management**: The conversation history grows with each turn, containing all messages, tool calls, tool outputs, and reasoning items. For token efficiency, the system monitors token usage and triggers automatic compaction when approaching context window limits. Compaction creates a summary of older history while preserving recent context, enabling long-running conversations without losing context.

**Context Window Handling**: When the model's context window limit is approached (detected via token counting), `run_task()` spawns an inline auto-compaction task that calls the model with a summarization prompt. The compaction process creates a "history bridge" that replaces old items with a summary, maintaining conversation continuity while reducing token count. The compaction happens transparently within the task loop, and the next turn continues with the compacted history.

---

## 6. Specialized Features

### 6.1 Apply Patch Tool
- Grammar-based patch format
- Safety verification
- Path constraint checking
- Incremental execution

### 6.2 Planning System
- `update_plan` tool: `codex-rs/core/src/tools/handlers/plan.rs` - markdown-formatted planning document with status tracking, allows agent to break down complex tasks into steps
- Step tracking: Markdown-based plan updates with status markers, records in conversation history for visibility
- Progress visualization: Rendered in TUI with step status display, incremental updates as work proceeds

### 6.3 MCP Integration
**Important Clarification**: MCP (Model Context Protocol) is **not used for calling LLM models**. Instead, Codex acts as an MCP **client** that connects to MCP **servers** to provide additional tools and resources to the LLM. The actual LLM calls are made via direct HTTP requests (see Section 5.2.1).

**MCP Architecture**: Codex connects to one or more MCP servers configured in `config.toml` under `[mcp_servers]`. Each server provides a set of tools that are discovered at runtime and added to the tool registry. MCP tools are identified by fully-qualified names in the format `server__tool_name` (using double underscore delimiter to comply with OpenAI tool naming requirements).

**Dynamic Tool Discovery**: `McpConnectionManager::list_all_tools()` in `codex-rs/core/src/mcp_connection_manager.rs:354-380` queries all connected MCP servers via the `tools/list` MCP protocol method, aggregating tools across servers into a unified map. The number of tools is not fixed and depends entirely on which MCP servers are configured. Tools are registered at runtime during `ToolRouter::from_config()` via `build_specs()` in `codex-rs/core/src/tools/spec.rs:862-1001`, which merges built-in tools with discovered MCP tools.

**Server Management**: The `McpConnectionManager` maintains persistent connections to each configured MCP server, handling initialization, tool discovery, and lifecycle management. Each server connection uses either STDIO (command-based servers) or Streamable HTTP transport. The manager handles server startup timeouts, tool call timeouts, and graceful error handling when servers fail or become unavailable.

**Tool Execution**: When the LLM requests an MCP tool, `ToolRegistry::dispatch()` routes to `McpHandler` in `codex-rs/core/src/tools/handlers/mcp.rs:10-54`, which parses the fully-qualified tool name to extract server and tool components. The handler calls `Session::call_tool()` which delegates to `McpConnectionManager::call_tool()`, sending a JSON-RPC request to the appropriate MCP server via the `tools/call` protocol method. The server executes the tool and returns results, which are formatted as `FunctionCallOutput` and added to conversation history.

**Resource Access**: MCP servers can expose resources (files, database schemas, application state) via the resources protocol. Codex provides three built-in tools (`list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource`) that allow the LLM to discover and access these resources. Resources are server-specific and provide context without requiring tool execution, making them efficient for read-only operations like file browsing or schema inspection.

**Authentication Handling**: MCP servers may require authentication via OAuth flows. The RMCP client (`codex-rs/rmcp-client/`) implements OAuth credential management per server, storing tokens securely and refreshing them as needed. Authentication status is exposed via `mcp auth-status` CLI command and can be managed through the `codex mcp` subcommands.

---

## 7. Implementation Details

### 7.1 Code Organization
- Core execution engine: `codex-rs/core/src/codex.rs`
- Tool system: `codex-rs/core/src/tools/`
- Command safety: `codex-rs/core/src/command_safety/`
- Protocol definitions: `codex-rs/protocol/src/`

### 7.2 Async Architecture
- Tokio runtime: Full async/await throughout, multi-threaded runtime for concurrent operations, spawn blocking for CPU-intensive tasks
- Event channels: `async_channel::bounded(64)` for submission ops, `async_channel::unbounded()` for events, `oneshot` channels for approval callbacks, broadcast for cancellation propagation
- Task spawning: `submission_loop` runs in background tokio task until `Op::Shutdown`, each tool call spawns separate async task for parallel execution, `AbortOnDropHandle` ensures cleanup
- Cancellation tokens: Hierarchical `CancellationToken` with child tokens per turn, `CancellationToken::child_token()` for isolation, `is_cancelled()` checks before expensive operations

### 7.3 Error Handling
- `ToolError` enum: `codex-rs/core/src/tools/sandboxing.rs:157-167` - `ToolError::Codex(CodexErr)`, `ToolError::Rejected`, `ToolError::SandboxDenied` - orchestrator converts to appropriate responses
- `FunctionCallError` variants: `codex-rs/core/src/function_tool.rs` - `RespondToModel` (returns error to LLM), `Fatal` (terminates turn), `MissingLocalShellCallId` (protocol error)
- Graceful degradation: User-friendly error messages (2KB limit), context preservation in error events, retry with backoff for transient failures, approvual cache prevents repeated prompts

---

## 8. Configuration & Extensibility

### 8.1 Configuration System
- `config.toml`: `codex-rs/core/src/config.rs` - located in `~/.codex/config.toml`, hierarchical loading with profiles, field-level overrides via `-c key=value` flags
- Profile-based settings: Named profiles (e.g., `[profile.work]`) with inheritance, profile-specific model, approval policy, sandbox mode, feature toggles
- CLI overrides: `--approval-mode`, `--sandbox`, `--model`, `--oss`, `--full-auto` (convenience for workspace-write + on-request), `--dangerously-bypass-approvals-and-sandbox` (danger-full-access + never), inline `-c` overrides take highest precedence

### 8.2 Feature Flags
- `Feature` enum: `codex-rs/core/src/features.rs` - unified feature registry with lifecycle stages, backward-compatible legacy alias support, centralized enable/disable logic in `Features::from_config()`
- Tool availability: Feature toggles control which tools are built into registry (UnifiedExec → exec_command/write_stdin, StreamableShell → streamable variants, ApplyPatchFreeform → freeform patch tool)
- Experimental features: Features progress through stages (Experimental → Beta → Stable → Deprecated → Removed), defaults vary by stage (experimental: false, stable: true), runtime toggling via config without recompilation