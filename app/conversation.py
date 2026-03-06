import os
import uuid
import datetime
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from app.logger import logger
from app.model_registry import get_notion_model


def summarize_turn(old_summary: Optional[str], user_msg: str, assistant_msg: str) -> str:
    """Summarize one historical user/assistant turn and merge it into existing summary text."""
    # TODO: Replace this stub with a real LLM-powered summarization call.
    prior = (old_summary or "").strip()
    latest = f"User asked: {user_msg}\nAssistant replied: {assistant_msg}"
    return f"{prior}\n\n{latest}" if prior else latest


class ConversationManager:
    """SQLite-backed conversation history manager with rolling context compression."""

    WINDOW_SIZE = 6

    def __init__(self):
        """Initialize database paths, schema, and summarization dependency."""
        self.db_path = os.getenv("DB_PATH", "./data/conversations.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._summarize_turn_fn: Callable[[Optional[str], str, str], str] = summarize_turn
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Create a SQLite connection with busy-timeout and foreign key settings."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        """Create tables and apply lightweight migration for summary column."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at INTEGER,
                    summary TEXT
                )
                '''
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    role TEXT,
                    content TEXT,
                    created_at INTEGER,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                '''
            )

            # Migration for existing DB files without the summary column.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "summary" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")

            conn.commit()

    def _count_messages(self, conn: sqlite3.Connection, conversation_id: str) -> int:
        """Return message count for a conversation."""
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def _fetch_recent_messages(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        limit: int,
    ) -> List[Dict[str, str]]:
        """Fetch the newest messages and return them in chronological order."""
        rows = conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        messages = [{"role": r["role"], "content": r["content"]} for r in rows]
        messages.reverse()
        return messages

    def _normalize_window_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Enforce strict user/assistant alternation starting from user."""
        normalized: List[Dict[str, str]] = []
        expected_role = "user"
        for msg in messages:
            role = msg.get("role", "")
            if role not in {"user", "assistant"}:
                continue
            if role != expected_role:
                continue
            normalized.append({"role": role, "content": msg.get("content", "")})
            expected_role = "assistant" if expected_role == "user" else "user"

        # Keep transcript append-safe for a new user prompt.
        while normalized and normalized[-1]["role"] != "assistant":
            normalized.pop()
        return normalized

    def _compress_oldest_turn(
        self,
        conversation_id: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Compress the oldest user+assistant turn into the running summary.

        Returns False when there is not enough data to compress or role pairing is invalid.
        """
        owns_conn = conn is None
        connection = conn or self._get_conn()
        try:
            conv_row = connection.execute(
                "SELECT summary FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not conv_row:
                raise ValueError(f"Conversation ID '{conversation_id}' does not exist.")

            rows = connection.execute(
                """
                SELECT id, role, content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT 2
                """,
                (conversation_id,),
            ).fetchall()
            if len(rows) < 2:
                return False

            first, second = rows[0], rows[1]
            if first["role"] != "user" or second["role"] != "assistant":
                logger.warning(
                    "Skip compression due to non user/assistant oldest pair",
                    extra={
                        "request_info": {
                            "event": "conversation_compress_skipped",
                            "conversation_id": conversation_id,
                            "roles": [first["role"], second["role"]],
                        }
                    },
                )
                return False

            merged_summary = self._summarize_turn_fn(
                conv_row["summary"],
                first["content"],
                second["content"],
            )
            connection.execute(
                "UPDATE conversations SET summary = ? WHERE id = ?",
                (merged_summary, conversation_id),
            )
            connection.execute(
                "DELETE FROM messages WHERE id IN (?, ?)",
                (first["id"], second["id"]),
            )

            if owns_conn:
                connection.commit()
            return True
        finally:
            if owns_conn:
                connection.close()

    def new_conversation(self) -> str:
        """Create a new conversation and return a UUID conversation_id."""
        conv_id = str(uuid.uuid4())
        created_at = int(datetime.datetime.now().timestamp())
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at) VALUES (?, ?, ?)",
                (conv_id, "New Chat", created_at),
            )
            conn.commit()
        logger.info(
            "Conversation created",
            extra={"request_info": {"event": "conversation_created", "conversation_id": conv_id}},
        )
        return conv_id

    def conversation_exists(self, conversation_id: str) -> bool:
        """Check whether a conversation exists."""
        if not conversation_id:
            return False

        with self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            return row is not None

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        """Append one message and auto-compress old turns when window size is exceeded."""
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Invalid role: {role}")

        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError(f"Conversation ID '{conversation_id}' does not exist.")

            created_at = int(datetime.datetime.now().timestamp())
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, created_at),
            )

            while self._count_messages(conn, conversation_id) > self.WINDOW_SIZE:
                compressed = self._compress_oldest_turn(conversation_id, conn=conn)
                if not compressed:
                    break

            conn.commit()

    def _build_dialog_block(
        self,
        role: str,
        content: str,
        notion_client: Any,
    ) -> Dict[str, Any]:
        """Build a transcript dialog block for user/assistant content."""
        block: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": role,
            "value": [[content]],
        }
        if role == "user":
            block["userId"] = notion_client.user_id
        return block

    def get_transcript(self, notion_client, conversation_id: str, new_prompt: str, model_name: str) -> list:
        """
        Build Notion transcript with strict ordering:
        config -> context -> optional summary blocks -> sliding window messages -> new user prompt.
        """
        summary_text = ""
        messages: List[Dict[str, str]] = []

        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError(f"Conversation ID '{conversation_id}' does not exist.")

            summary_row = conn.execute(
                "SELECT summary FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            summary_text = (summary_row["summary"] or "").strip() if summary_row else ""
            messages = self._fetch_recent_messages(conn, conversation_id, self.WINDOW_SIZE)

        messages = self._normalize_window_messages(messages)

        transcript: List[Dict[str, Any]] = []

        config_block = {
            "id": str(uuid.uuid4()),
            "type": "config",
            "value": {
                "type": "workflow",
                "model": get_notion_model(model_name),
                "modelFromUser": True,
                "useWebSearch": True,
                "useReadOnlyMode": False,
                "writerMode": False,
                "isCustomAgent": False,
                "isCustomAgentBuilder": False,
                "useCustomAgentDraft": False,
                "use_draft_actor_pointer": False,
                "enableAgentAutomations": True,
                "enableAgentIntegrations": True,
                "enableCustomAgents": True,
                "enableAgentDiffs": True,
                "enableAgentCreateDbTemplate": True,
                "enableCsvAttachmentSupport": True,
                "enableDatabaseAgents": False,
                "enableAgentThreadTools": False,
                "enableRunAgentTool": False,
                "enableAgentDashboards": False,
                "enableAgentCardCustomization": True,
                "enableSystemPromptAsPage": False,
                "enableUserSessionContext": False,
                "enableCreateAndRunThread": True,
                "enableAgentGenerateImage": False,
                "enableSpeculativeSearch": False,
                "enableUpdatePageV2Tool": True,
                "enableUpdatePageAutofixer": True,
                "enableUpdatePageMarkdownTree": False,
                "enableUpdatePageOrderUpdates": True,
                "enableAgentSupportPropertyReorder": True,
                "enableAgentVerification": False,
                "useServerUndo": True,
                "databaseAgentConfigMode": False,
                "isOnboardingAgent": False,
                "availableConnectors": [],
                "customConnectorNames": [],
                "searchScopes": [{"type": "everything"}],
                "useSearchToolV2": False,
                "useRulePrioritization": False,
                "enableExperimentalIntegrations": False,
                "enableAgentViewNotificationsTool": False,
                "enableScriptAgent": False,
                "enableScriptAgentAdvanced": False,
                "enableScriptAgentSlack": False,
                "enableScriptAgentMcpServers": False,
                "enableScriptAgentMail": False,
                "enableScriptAgentCalendar": False,
                "enableScriptAgentCustomAgentTools": False,
                "enableScriptAgentSearchConnectorsInCustomAgent": False,
                "enableScriptAgentGoogleDriveInCustomAgent": False,
                "enableQueryCalendar": False,
                "enableQueryMail": False,
                "enableMailExplicitToolCalls": True,
            },
        }
        transcript.append(config_block)

        context_block = {
            "id": str(uuid.uuid4()),
            "type": "context",
            "value": {
                "timezone": "Asia/Shanghai",
                "userName": notion_client.user_name,
                "userId": notion_client.user_id,
                "userEmail": notion_client.user_email,
                "spaceName": "Notion",
                "spaceId": notion_client.space_id,
                "spaceViewId": notion_client.space_view_id,
                "currentDatetime": datetime.datetime.now().astimezone().isoformat(),
                "surface": "ai_module",
                "agentName": notion_client.user_name,
            },
        }
        transcript.append(context_block)

        if summary_text:
            transcript.append(
                self._build_dialog_block(
                    "user",
                    f"Previous conversation summary:\n{summary_text}",
                    notion_client,
                )
            )
            transcript.append(
                self._build_dialog_block(
                    "assistant",
                    "Understood, I have the context from our previous conversation.",
                    notion_client,
                )
            )

        for msg in messages:
            transcript.append(self._build_dialog_block(msg["role"], msg["content"], notion_client))

        transcript.append(self._build_dialog_block("user", new_prompt, notion_client))
        return transcript

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete one conversation."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            conn.commit()

    def list_conversations(self) -> List[str]:
        """Return all conversation ids sorted by creation time descending."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT id FROM conversations ORDER BY created_at DESC")
            return [row["id"] for row in cursor.fetchall()]
