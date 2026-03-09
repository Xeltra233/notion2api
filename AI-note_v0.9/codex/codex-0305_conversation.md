# Conversation Compression Refactor (2026-03-05)

## Scope
- Refactored only `app/conversation.py`.
- Added this delivery note file as requested.
- No other files were modified.

## Implemented Changes

### 1) Hybrid context compression (Sliding Window + Summary)
- Added `WINDOW_SIZE = 6` in `ConversationManager`.
- Added `_compress_oldest_turn(conversation_id, conn=None)`:
  - Reads current `conversations.summary`.
  - Reads oldest two messages (expected `user` then `assistant`).
  - Calls injected summarizer function (`self._summarize_turn_fn`).
  - Updates summary and deletes compressed two messages.
- Added automatic compression trigger in `add_message()`:
  - After insert, while message count exceeds 6, compress oldest turn.

### 2) Summarization dependency + stub
- Added module-level stub:
  - `summarize_turn(old_summary, user_msg, assistant_msg) -> str`
  - Includes explicit TODO to replace with real LLM summarization.
- Wired into manager via dependency field:
  - `self._summarize_turn_fn = summarize_turn`

### 3) Schema migration
- Updated conversations table schema to include:
  - `summary TEXT`
- Added migration logic in `_init_db()` for existing DBs:
  - `PRAGMA table_info(conversations)` check
  - `ALTER TABLE conversations ADD COLUMN summary TEXT` when missing
- Preserved existing WAL + busy_timeout setup.

### 4) Transcript assembly order and alternation
- Removed the old system-breaking assistant injection block entirely.
- `get_transcript()` now assembles in strict order:
  1. config block
  2. context block
  3. optional summary pair:
     - user: `Previous conversation summary:\n{summary}`
     - assistant: `Understood, I have the context from our previous conversation.`
  4. recent sliding-window messages
  5. new user prompt
- Added `_normalize_window_messages()` to enforce strict `user`/`assistant` alternation and ensure append-safety before the new user prompt.

### 5) Internal helper improvements
- Added typed helper methods:
  - `_count_messages(...)`
  - `_fetch_recent_messages(...)`
  - `_build_dialog_block(...)`
- Added/updated type hints and docstrings for new/modified methods.

## Validation Performed
- Syntax check passed:
  - `python -m py_compile app/conversation.py`

## Notes
- Public methods requested to remain unchanged are preserved:
  - `new_conversation`, `add_message`, `get_transcript`, `delete_conversation`, `list_conversations`, `conversation_exists`
- Refactor is confined to `app/conversation.py` plus this note file.
