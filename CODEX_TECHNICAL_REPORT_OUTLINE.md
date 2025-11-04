# Codex Agent Technical Report
## Outline & Structure

---

## Abstract / Executive Summary
- Brief introduction to Codex Agent
- Core design philosophy
- Key contributions

---

## 1. Introduction

### 1.1 Motivation
- AI-assisted development challenges
- Safety and autonomy balance
- Runtime execution control

### 1.2 Architecture Overview
- Session-based conversation management
- Tool-oriented execution model
- Multi-turn LLM interaction

### 1.3 Scope & Contributions
- Sandboxed execution framework
- Approval-based safety mechanism
- Tool orchestration and dispatch
- File system abstraction

---

## 2. System Architecture

### 2.1 Core Components

#### 2.1.1 Session Management
- **Session**: `codex-rs/core/src/codex.rs:245-252`
- **TurnContext**: `codex-rs/core/src/codex.rs:254-272`
- Lifecycle: Session → Turn → Task → Tool calls

#### 2.1.2 Codex Spawn & Initialization
- `Codex::spawn()`: `codex-rs/core/src/codex.rs:155-209`
- Submission loop: `submission_loop()`: `codex-rs/core/src/codex.rs:1131-1402`
- Event channels & communication

### 2.2 Execution Model

#### 2.2.1 Task Orchestration
- `run_task()`: `codex-rs/core/src/codex.rs:1504-1650`
- `run_turn()`: `codex-rs/core/src/codex.rs:1812-`
- Multi-turn conversations
- Parallel tool execution

#### 2.2.2 Tool Registry & Dispatch
- `ToolRegistry`: `codex-rs/core/src/tools/registry.rs:37-48`
- `build_specs()`: `codex-rs/core/src/tools/spec.rs:862-1001`
- Handler pattern
- MCP integration

---

## 3. Safety & Sandboxing Framework

### 3.1 Sandbox Policies

#### 3.1.1 Policy Types
- **ReadOnly**: `codex-rs/protocol/src/protocol.rs:236-238`
- **WorkspaceWrite**: `codex-rs/protocol/src/protocol.rs:243-262`
- **DangerFullAccess**: `codex-rs/protocol/src/protocol.rs:233-234`

#### 3.1.2 Platform Implementation
- macOS Seatbelt
- Linux Seccomp
- Windows (future)

### 3.2 Command Safety Classification

#### 3.2.1 Whitelist Mechanism
- Implementation: `codex-rs/core/src/command_safety/is_safe_command.rs:43-135`
- **Pure read commands**: cat, cd, echo, false, grep, head, ls, nl, pwd, tail, true, wc, which - automatically approved with 0-parameter checks
- **Conditional commands**: 
  - `find`: Safe unless dangerous options (-exec, -delete, -fls, etc.)
  - `rg`: Safe unless external execution (-pre, --hostname-bin, -z, --search-zip)
  - `git`: Only safe for read-only subcommands (branch, status, log, diff, show)
  - `cargo`: Only `check` subcommand is safe
  - `sed`: Safe for limited print patterns (`sed -n {N|M,N}p`)
- **Bash -lc parsing**: Supports parsing `bash -lc "command"` with operators (&&, ||, ;, |), checks each individual command in sequence

#### 3.2.2 Blacklist Mechanism  
- Implementation: `codex-rs/core/src/command_safety/is_dangerous_command.rs:3-36`
- **Git destructive**: `git reset`, `git rm` - hard resets and file deletion
- **Force deletion**: `rm -f`, `rm -rf` - mandatory approval required
- **Recursive sudo**: If command starts with `sudo`, recursively checks the underlying command
- **Detection**: Checks both direct command execution and parsed `bash -lc` sequences

#### 3.2.3 Static vs Runtime Analysis
- Static whitelist/blacklist
- Runtime sandbox enforcement
- Path-based checks for `apply_patch`

### 3.3 Approval System

#### 3.3.1 Approval Policies
- **UnlessTrusted**: `codex-rs/protocol/src/protocol.rs:204-210`
- **OnFailure**: `codex-rs/protocol/src/protocol.rs:212-216`
- **OnRequest**: `codex-rs/protocol/src/protocol.rs:218-220`
- **Never**: `codex-rs/protocol/src/protocol.rs:222-224`

#### 3.3.2 Decision Flow
- Initial approval: `wants_initial_approval()`: `codex-rs/core/src/tools/runtimes/shell.rs:106-134`
- Escalation mechanism: `codex-rs/core/src/tools/orchestrator.rs:95-139`
- Caching: `ApprovalStore`: `codex-rs/core/src/tools/sandboxing.rs:26-49`

#### 3.3.3 Sandbox Denial Detection
- Keywords: `SANDBOX_DENIED_KEYWORDS`: `codex-rs/core/src/exec.rs:109-134` - 7 cross-platform keywords including "operation not permitted", "permission denied", "read-only file system", "seccomp", "sandbox", "landlock", "failed to write file"
- Exit code analysis: Quick rejects for non-sandbox errors (2: misuse, 126: permission, 127: not found), allows all other non-zero exit codes to be considered potential sandbox denials
- Platform-specific signals: Linux seccomp SIGSYS detection using exit code calculation (128 + signal number)
- Heuristic approach: `is_likely_sandbox_denied()` combines exit codes + keyword matching + signal detection for robust cross-platform identification

---

## 4. Tool System

### 4.1 Tool Architecture

#### 4.1.1 Tool Types
- Built-in tools (shell, apply_patch, read_file, etc.)
- MCP tools (dynamic)
- Resource tools

#### 4.1.2 Tool Selection
- `ToolsConfig`: `codex-rs/core/src/tools/spec.rs:26-81` - determines shell type, apply_patch availability, web search, view image, MCP tools based on feature flags and model family
- Model family-based configuration: `codex-rs/core/src/model_family.rs:87-167` - each model family specifies base instructions, apply_patch preference (Freeform/Function), experimental tools (grep_files, list_dir, read_file, test_sync), reasoning support
- Feature flags: Centralized `Features` enum with stages (Experimental, Beta, Stable, Deprecated, Removed) - UnifiedExec, StreamableShell, ApplyPatchFreeform, ViewImageTool, WebSearchRequest, RmcpClient

### 4.2 Execution Layer

#### 4.2.1 Tool Orchestrator
- `ToolOrchestrator::run()`: `codex-rs/core/src/tools/orchestrator.rs:31-142`
- Approval → Sandbox → Execute → Retry
- Failure handling & escalation

#### 4.2.2 Tool Handlers
- Shell handler: `codex-rs/core/src/tools/handlers/shell.rs`
- Apply patch handler: `codex-rs/core/src/tools/handlers/apply_patch.rs`
- MCP handler: `codex-rs/core/src/tools/handlers/mcp.rs`

### 4.3 Special Tools

#### 4.3.1 apply_patch
- Safety assessment: `codex-rs/core/src/safety.rs:25-80`
- Path analysis: `is_write_patch_constrained_to_writable_paths()`: `codex-rs/core/src/safety.rs:92-164`
- Freeform vs Function type

#### 4.3.2 File System Tools
- `read_file`: `codex-rs/core/src/tools/handlers/read_file.rs`
- `list_dir`: `codex-rs/core/src/tools/handlers/list_dir.rs`
- `grep_files`: `codex-rs/core/src/tools/handlers/grep_files.rs`
- Independent from shell execution

---

## 5. Model Integration

### 5.1 Prompt System

#### 5.1.1 Prompt Selection
- `prompt.md`: `codex-rs/core/prompt.md` (default)
- `gpt_5_codex_prompt.md`: `codex-rs/core/gpt_5_codex_prompt.md`
- `review_prompt.md`: `codex-rs/core/review_prompt.md`

#### 5.1.2 Dynamic Prompt Selection
- `ModelFamily::find_family_for_model()`: `codex-rs/core/src/model_family.rs:87-167` - prefix matching (o3, gpt-4.1, codex-, gpt-5-codex, etc.) with fallback to `derive_default_model_family()` if no match
- `base_instructions` assignment: Default uses `prompt.md`, GPT-5 models use `gpt_5_codex_prompt.md`, can be overridden via `experimental.instructions_file` config
- Review thread isolation: Uses `REVIEW_PROMPT` instead of base instructions, creates isolated `ConversationHistory` without parent session history, injects environment context for cwd awareness

### 5.2 LLM Interaction

#### 5.2.1 Chat Completions
- `stream_chat_completions()`: `codex-rs/core/src/chat_completions.rs`
- SSE processing
- Response parsing

#### 5.2.2 Tool Calling
- Function calling
- Freeform tools
- Custom tools

#### 5.2.3 Reasoning & Summarization
- `supports_reasoning_summaries`
- `reasoning_summary_format`
- Streaming reasoning

---

## 6. Execution Flow & Lifecycle

### 6.1 User Input Processing
- TUI input: `codex-rs/tui/src/chatwidget.rs`
- CLI exec mode: `codex-rs/exec/src/lib.rs:165-220`
- App server: `codex-rs/app-server/src/codex_message_processor.rs`

### 6.2 Tool Call Flow
1. LLM returns FunctionCall
2. `ToolRouter::build_tool_call()`: `codex-rs/core/src/tools/router.rs:57-130`
3. `registry.dispatch()`: `codex-rs/core/src/tools/registry.rs:58-139`
4. `handler.handle()` 
5. `orchestrator.run()`: approval → sandbox → execute
6. Return result to LLM

### 6.3 Multi-Turn Conversations
- Turn isolation: Each turn creates a new `TurnContext` with unique `sub_id`, preserves session-wide state (approval cache, history) across turns
- Conversation history: `ConversationHistory` stores ordered `ResponseItem` vector (oldest → newest), normalizes call/output pairs, filters API messages, `Session.record_conversation_items()` appends to history + rollout recording
- Review threads: `is_review_mode` flag, separate `review_thread_history`, no parent session history contamination, uses `REVIEW_PROMPT`, exits with `ExitedReviewMode` event

---

## 7. Specialized Features

### 7.1 Apply Patch Tool
- Grammar-based patch format
- Safety verification
- Path constraint checking
- Incremental execution

### 7.2 Planning System
- `update_plan` tool: `codex-rs/core/src/tools/handlers/plan.rs` - markdown-formatted planning document with status tracking, allows agent to break down complex tasks into steps
- Step tracking: Markdown-based plan updates with status markers, records in conversation history for visibility
- Progress visualization: Rendered in TUI with step status display, incremental updates as work proceeds

### 7.3 MCP Integration
- Dynamic tool discovery: `list_all_tools()` from connected MCP servers, tools registered at runtime with `build_specs()`, number of tools not fixed - depends on configured servers
- Server management: `codex-rs/core/src/mcp_connection_manager.rs` - manages multiple MCP server connections, tool routing by server name, lifecycle management
- Authentication handling: OAuth flows via RMCP client feature, credential management per server
- Resource access: `list_mcp_resources`, `read_mcp_resource`, `list_mcp_resource_templates` - unified resource abstraction across MCP servers, parallel-safe operations

---

## 10. Implementation Details

### 10.1 Code Organization
- Core execution engine: `codex-rs/core/src/codex.rs`
- Tool system: `codex-rs/core/src/tools/`
- Command safety: `codex-rs/core/src/command_safety/`
- Protocol definitions: `codex-rs/protocol/src/`

### 10.2 Async Architecture
- Tokio runtime: Full async/await throughout, multi-threaded runtime for concurrent operations, spawn blocking for CPU-intensive tasks
- Event channels: `async_channel::bounded(64)` for submission ops, `async_channel::unbounded()` for events, `oneshot` channels for approval callbacks, broadcast for cancellation propagation
- Task spawning: `submission_loop` runs in background tokio task until `Op::Shutdown`, each tool call spawns separate async task for parallel execution, `AbortOnDropHandle` ensures cleanup
- Cancellation tokens: Hierarchical `CancellationToken` with child tokens per turn, `CancellationToken::child_token()` for isolation, `is_cancelled()` checks before expensive operations

### 10.3 Error Handling
- `ToolError` enum: `codex-rs/core/src/tools/sandboxing.rs:157-167` - `ToolError::Codex(CodexErr)`, `ToolError::Rejected`, `ToolError::SandboxDenied` - orchestrator converts to appropriate responses
- `FunctionCallError` variants: `codex-rs/core/src/function_tool.rs` - `RespondToModel` (returns error to LLM), `Fatal` (terminates turn), `MissingLocalShellCallId` (protocol error)
- Graceful degradation: User-friendly error messages (2KB limit), context preservation in error events, retry with backoff for transient failures, approvual cache prevents repeated prompts

---

## 11. Configuration & Extensibility

### 11.1 Configuration System
- `config.toml`: `codex-rs/core/src/config.rs` - located in `~/.codex/config.toml`, hierarchical loading with profiles, field-level overrides via `-c key=value` flags
- Profile-based settings: Named profiles (e.g., `[profile.work]`) with inheritance, profile-specific model, approval policy, sandbox mode, feature toggles
- CLI overrides: `--approval-mode`, `--sandbox`, `--model`, `--oss`, `--full-auto` (convenience for workspace-write + on-request), `--dangerously-bypass-approvals-and-sandbox` (danger-full-access + never), inline `-c` overrides take highest precedence

### 11.2 Feature Flags
- `Feature` enum: `codex-rs/core/src/features.rs` - unified feature registry with lifecycle stages, backward-compatible legacy alias support, centralized enable/disable logic in `Features::from_config()`
- Tool availability: Feature toggles control which tools are built into registry (UnifiedExec → exec_command/write_stdin, StreamableShell → streamable variants, ApplyPatchFreeform → freeform patch tool)
- Experimental features: Features progress through stages (Experimental → Beta → Stable → Deprecated → Removed), defaults vary by stage (experimental: false, stable: true), runtime toggling via config without recompilation

### 11.3 Extension Points
- Custom MCP servers: Add MCP servers to `config.toml` under `[mcp.servers]`, implements MCP protocol, dynamic tool discovery, authentication via RMCP client, resource access, unified tool registry integration
- Feature toggles: Users can enable/disable experimental features via config without code changes, backward compatibility maintained through legacy aliases
- Prompt customization: `experimental.instructions_file` for custom base prompts, `AGENTS.md` in project directories for project-specific instructions (Git-root based discovery), `AGENTS.override.md` for local overrides, concatenated with `--- project-doc ---` separator

---

## Appendix

### A. File Reference Guide
- Key source files
- Code locations
- Entry points
`codex-rs/cli/src/main.rs`
fn main() -> anyhow::Result<()> {
    arg0_dispatch_or_else(|codex_linux_sandbox_exe| async move {
        cli_main(codex_linux_sandbox_exe).await?;
        Ok(())
    })
}

### C. Configuration Examples
- Common configurations
- Use case patterns
- Best practices
