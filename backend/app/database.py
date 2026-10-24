from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional
import uuid
import aiosqlite
from backend.app.config import settings
from backend.app.models import (
    BackgroundTask,
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    TaskStatus,
    TaskType,
)

logger = logging.getLogger(__name__)

#: Appended to a background task that was still running when Kayak stopped.
TASK_INTERRUPTED_NOTE = "\n[Stopped because Kayak was closed.]\n"


def _utc_now_iso() -> str:
    """Returns the current UTC time as a naive ISO-8601 string.

    Timestamps are stored without an offset so that lexicographic ordering in SQL
    matches chronological ordering across every row.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


@asynccontextmanager
async def get_db_connection():
    """Async context manager that provides a configured aiosqlite connection.

    Yields:
        aiosqlite.Connection: Active database connection with row factory and WAL mode.
    """
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        yield db


async def init_db() -> None:
    """Initializes the SQLite schema, setting aside a database that cannot be read.

    A damaged database file used to stop the server from starting at all, which left
    the user with a window that never loaded and no way to reach Report a problem.
    The file is renamed rather than deleted, so nothing is lost for good, and Kayak
    starts with an empty history instead of not starting.
    """
    try:
        await _create_schema()
    except sqlite3.DatabaseError as error:
        # Only a file SQLite cannot read as a database. An OperationalError -- the
        # disk full, the file locked by a server still shutting down -- says nothing
        # about the data, and setting a healthy history aside over it would be the
        # worst outcome there is.
        if isinstance(error, sqlite3.OperationalError):
            raise
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.error(
            "The database at %s could not be read (%s); moving it aside as"
            " *.damaged-%s and starting a new one.",
            settings.DB_PATH,
            error,
            stamp,
        )
        for suffix in ("", "-wal", "-shm"):
            damaged = settings.DB_PATH.with_name(settings.DB_PATH.name + suffix)
            if damaged.exists():
                damaged.rename(damaged.with_name(f"{damaged.name}.damaged-{stamp}"))
        await _create_schema()


async def _create_schema() -> None:
    """Creates every table and index that does not exist yet."""
    async with get_db_connection() as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            container_id TEXT,
            status TEXT DEFAULT 'active',
            -- Set on sub-agent conversations; deleting the parent deletes these.
            parent_conversation_id TEXT,
            -- Where a branch was taken from. Separate from the parent link so that
            -- deleting a conversation does not take its branches with it.
            branched_from_conversation_id TEXT,
            branched_from_message_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            thinking TEXT,
            tool_calls TEXT,
            tool_call_id TEXT,
            name TEXT,
            -- A tool result that reports a failure, or an agent turn that failed
            -- and was recorded so the user can see why. See add_message.
            is_error INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)
        await _add_missing_column(db, "messages", "is_error", "INTEGER NOT NULL DEFAULT 0")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            name TEXT NOT NULL,
            command TEXT,
            status TEXT DEFAULT 'running',
            exit_code INTEGER,
            stdout TEXT DEFAULT '',
            stderr TEXT DEFAULT '',
            -- For a sub-agent task, the conversation the delegated agent works in.
            subagent_conversation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)

        await db.execute(
            # rowid is implicit in every index entry, so ordering by
            # (created_at, rowid) is still served by this index.
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON"
            " messages(conversation_id, created_at);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON"
            " tasks(conversation_id);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_parent ON"
            " conversations(parent_conversation_id);"
        )
        await db.commit()


async def _add_missing_column(db, table: str, column: str, definition: str) -> None:
    """Adds a column to a table created by an older Kayak.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing table exactly as it was, so a
    column introduced later has to be added to databases that predate it.
    """
    cursor = await db.execute(f"PRAGMA table_info({table})")
    if any(row["name"] == column for row in await cursor.fetchall()):
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def reconcile_interrupted_state() -> None:
    """Clears state left behind by a server that stopped mid-turn.

    A conversation whose turn was interrupted stays marked RUNNING forever, which
    pins the composer into its "generating" state with no way out. The same applies
    to background tasks whose owning process no longer exists: they are reported as
    stopped, with a note saying why, rather than as having failed -- nothing went
    wrong with the command itself.
    """
    async with get_db_connection() as db:
        await db.execute(
            "UPDATE conversations SET status = ? WHERE status = ?",
            (ConversationStatus.ACTIVE.value, ConversationStatus.RUNNING.value),
        )
        await db.execute(
            "UPDATE tasks SET status = ?, stdout = stdout || ?, updated_at = ?"
            " WHERE status = ?",
            (
                TaskStatus.STOPPED.value,
                TASK_INTERRUPTED_NOTE,
                _utc_now_iso(),
                TaskStatus.RUNNING.value,
            ),
        )
        await db.commit()


# --- Row-to-Model Helpers ---


def _row_to_conversation(row: aiosqlite.Row) -> Conversation:
    """Converts a database row to a Conversation model."""
    return Conversation(
        id=row["id"],
        title=row["title"],
        agent_id=row["agent_id"],
        container_id=row["container_id"],
        status=ConversationStatus(row["status"]),
        parent_conversation_id=row["parent_conversation_id"],
        branched_from_conversation_id=row["branched_from_conversation_id"],
        branched_from_message_id=row["branched_from_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_task(row: aiosqlite.Row) -> BackgroundTask:
    """Converts a database row to a BackgroundTask model."""
    return BackgroundTask(
        id=row["id"],
        conversation_id=row["conversation_id"],
        task_type=TaskType(row["task_type"]),
        name=row["name"],
        command=row["command"],
        status=TaskStatus(row["status"]),
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        subagent_conversation_id=row["subagent_conversation_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_message(row: aiosqlite.Row) -> Message:
    """Converts a database row to a Message model."""
    tool_calls = None
    if row["tool_calls"]:
        try:
            tool_calls = json.loads(row["tool_calls"])
        except (json.JSONDecodeError, TypeError):
            tool_calls = None

    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        thinking=row["thinking"],
        tool_calls=tool_calls,
        tool_call_id=row["tool_call_id"],
        name=row["name"],
        is_error=bool(row["is_error"]),
        created_at=row["created_at"],
    )


# --- Conversation Operations ---


async def create_conversation(
    title: str,
    agent_id: str = "general",
    container_id: Optional[str] = None,
    parent_conversation_id: Optional[str] = None,
    branched_from_conversation_id: Optional[str] = None,
    branched_from_message_id: Optional[str] = None,
) -> Conversation:
    """Creates a new conversation record in the database.

    Args:
        title: Title of the conversation session.
        agent_id: ID of the agent profile assigned to this conversation.
        container_id: Optional Docker container this conversation is bound to.
        parent_conversation_id: Optional parent conversation, set for sub-agent sessions.
        branched_from_conversation_id: Conversation this one was branched from, if any.
        branched_from_message_id: Message in the source conversation the branch was taken at.

    Returns:
        Conversation: The newly created Conversation instance.
    """
    cid = str(uuid.uuid4())
    now = _utc_now_iso()
    status = ConversationStatus.ACTIVE

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO conversations (id, title, agent_id, container_id, status, parent_conversation_id, branched_from_conversation_id, branched_from_message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                cid,
                title,
                agent_id,
                container_id,
                status.value,
                parent_conversation_id,
                branched_from_conversation_id,
                branched_from_message_id,
                now,
                now,
            ),
        )
        await db.commit()

    workspace = settings.WORKSPACES_DIR / cid
    workspace.mkdir(parents=True, exist_ok=True)

    return Conversation(
        id=cid,
        title=title,
        agent_id=agent_id,
        container_id=container_id,
        status=status,
        parent_conversation_id=parent_conversation_id,
        branched_from_conversation_id=branched_from_conversation_id,
        branched_from_message_id=branched_from_message_id,
        created_at=now,
        updated_at=now,
    )


async def get_conversation(conversation_id: str) -> Optional[Conversation]:
    """Retrieves a single conversation by its ID.

    Args:
        conversation_id: Unique identifier for the conversation.

    Returns:
        Optional[Conversation]: Conversation instance if found, otherwise None.
    """
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        row = await cursor.fetchone()
        return _row_to_conversation(row) if row else None


async def list_conversations() -> List[Conversation]:
    """Lists all stored conversations sorted by updated_at descending.

    Returns:
        List[Conversation]: List of Conversation records.
    """
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [_row_to_conversation(row) for row in rows]


async def update_conversation(
    conversation_id: str,
    title: Optional[str] = None,
    status: Optional[ConversationStatus | str] = None,
    container_id: Optional[str] = None,
) -> None:
    """Updates specified attributes of an existing conversation.

    Args:
        conversation_id: Unique identifier of the conversation to update.
        title: Optional new title.
        status: Optional updated conversation status enum or string.
        container_id: Optional Docker container identifier.
    """
    now = _utc_now_iso()
    fields = ["updated_at = ?"]
    values: List[Any] = [now]

    if title is not None:
        fields.append("title = ?")
        values.append(title)
    if status is not None:
        status_val = status.value if isinstance(status, ConversationStatus) else str(status)
        fields.append("status = ?")
        values.append(status_val)
    if container_id is not None:
        fields.append("container_id = ?")
        values.append(container_id)

    values.append(conversation_id)
    query = f"UPDATE conversations SET {', '.join(fields)} WHERE id = ?"

    async with get_db_connection() as db:
        await db.execute(query, tuple(values))
        await db.commit()


async def delete_conversation(conversation_id: str) -> None:
    """Permanently removes a conversation and cascades to its messages and tasks.

    Args:
        conversation_id: Unique identifier of the conversation.
    """
    async with get_db_connection() as db:
        await db.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        await db.commit()


# --- Message Operations ---


async def add_message(
    conversation_id: str,
    role: MessageRole | str,
    content: Optional[str] = None,
    thinking: Optional[str] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    tool_call_id: Optional[str] = None,
    name: Optional[str] = None,
    is_error: bool = False,
) -> Message:
    """Inserts a new message into the conversation history.

    Args:
        conversation_id: Unique conversation identifier.
        role: Role of the message (user, assistant, system, tool).
        content: Optional text body of the message.
        thinking: Optional reasoning / thinking text generated by model.
        tool_calls: Optional list of structured tool calls requested by assistant.
        tool_call_id: Optional ID of the tool call this message responds to.
        name: Optional function or tool name.
        is_error: On a tool result, that the step failed. On an assistant message,
            that it explains why a turn failed rather than being anything the
            model said -- it is shown to the user and never sent to the model.

    Returns:
        Message: The created Message record.
    """
    mid = str(uuid.uuid4())
    now = _utc_now_iso()
    role_enum = role if isinstance(role, MessageRole) else MessageRole(role)
    tool_calls_json = json.dumps(tool_calls) if tool_calls else None

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO messages (id, conversation_id, role, content, thinking, tool_calls, tool_call_id, name, is_error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                mid,
                conversation_id,
                role_enum.value,
                content,
                thinking,
                tool_calls_json,
                tool_call_id,
                name,
                int(is_error),
                now,
            ),
        )
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        await db.commit()

    return Message(
        id=mid,
        conversation_id=conversation_id,
        role=role_enum,
        content=content,
        thinking=thinking,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        name=name,
        is_error=is_error,
        created_at=now,
    )


async def get_messages(conversation_id: str) -> List[Message]:
    """Retrieves all chronological messages for a conversation session.

    Args:
        conversation_id: Unique conversation identifier.

    Returns:
        List[Message]: Chronologically ordered messages.
    """
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY"
            " created_at ASC, rowid ASC",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_message(row) for row in rows]


async def get_last_message_id(conversation_id: str) -> Optional[str]:
    """The id of the newest message in a conversation, if it has any."""
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY"
            " created_at DESC, rowid DESC LIMIT 1",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return row["id"] if row else None


async def _message_rowid(db, conversation_id: str, message_id: str) -> Optional[int]:
    """Returns the storage order key of a message, or None if it is not in this conversation.

    Messages are only ever appended, so rowid is exact insertion order -- a stronger
    ordering than the created_at timestamps, which can tie.
    """
    cursor = await db.execute(
        "SELECT rowid FROM messages WHERE conversation_id = ? AND id = ?",
        (conversation_id, message_id),
    )
    row = await cursor.fetchone()
    return row["rowid"] if row else None


async def delete_messages_from(conversation_id: str, message_id: str) -> int:
    """Deletes a message and everything stored after it in the same conversation.

    Args:
        conversation_id: Conversation to truncate.
        message_id: First message to remove.

    Returns:
        int: Number of messages deleted; 0 if the message is not in this conversation.
    """
    async with get_db_connection() as db:
        rowid = await _message_rowid(db, conversation_id, message_id)
        if rowid is None:
            return 0

        cursor = await db.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND rowid >= ?",
            (conversation_id, rowid),
        )
        removed = cursor.rowcount
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_utc_now_iso(), conversation_id),
        )
        await db.commit()
        return removed


async def get_preceding_user_message(
    conversation_id: str, message_id: str
) -> Optional[Message]:
    """Finds the user message that comes before the given message.

    Used to answer "what did I ask that produced this?", which is the prompt a revert
    puts back in the composer.
    """
    async with get_db_connection() as db:
        rowid = await _message_rowid(db, conversation_id, message_id)
        if rowid is None:
            return None

        cursor = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND rowid < ?"
            " AND role = ? ORDER BY rowid DESC LIMIT 1",
            (conversation_id, rowid, MessageRole.USER.value),
        )
        row = await cursor.fetchone()
        return _row_to_message(row) if row else None


async def copy_messages_through(
    source_conversation_id: str,
    target_conversation_id: str,
    message_id: str,
) -> int:
    """Copies a conversation's history into another, up to and including a message.

    Tool call ids are carried over unchanged. They only have to agree with each other
    within the copied history, and copying a contiguous prefix keeps every call paired
    with its result -- which the provider requires.

    Returns:
        int: Number of messages copied; 0 if the anchor is not in the source.
    """
    async with get_db_connection() as db:
        rowid = await _message_rowid(db, source_conversation_id, message_id)
        if rowid is None:
            return 0

        cursor = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? AND rowid <= ?"
            " ORDER BY rowid ASC",
            (source_conversation_id, rowid),
        )
        rows = await cursor.fetchall()

        now = _utc_now_iso()
        for row in rows:
            await db.execute(
                """
            INSERT INTO messages (id, conversation_id, role, content, thinking, tool_calls, tool_call_id, name, is_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    str(uuid.uuid4()),
                    target_conversation_id,
                    row["role"],
                    row["content"],
                    row["thinking"],
                    row["tool_calls"],
                    row["tool_call_id"],
                    row["name"],
                    row["is_error"],
                    row["created_at"],
                ),
            )

        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, target_conversation_id),
        )
        await db.commit()
        return len(rows)


async def list_child_conversations(conversation_id: str) -> List[Conversation]:
    """Lists sub-agent conversations spawned by a conversation.

    Args:
        conversation_id: Identifier of the parent conversation.

    Returns:
        List[Conversation]: Direct children of the given conversation.
    """
    async with get_db_connection() as db:
        cursor = await db.execute(
            "SELECT * FROM conversations WHERE parent_conversation_id = ?",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_conversation(row) for row in rows]


# --- Task Operations ---


async def create_task(
    conversation_id: str,
    task_type: TaskType | str,
    name: str,
    command: Optional[str] = None,
    subagent_conversation_id: Optional[str] = None,
) -> BackgroundTask:
    """Inserts a new asynchronous background task record.

    Args:
        conversation_id: ID of the conversation that started the task.
        task_type: Task classification type.
        name: Descriptive name for the task.
        command: Optional shell command line executed.
        subagent_conversation_id: For a sub-agent task, the conversation the delegated
            agent is working in, so its transcript can be opened from the task.

    Returns:
        BackgroundTask: Created background task record.
    """
    tid = str(uuid.uuid4())
    now = _utc_now_iso()
    type_enum = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
    status_enum = TaskStatus.RUNNING

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO tasks (id, conversation_id, task_type, name, command, status, stdout, stderr, subagent_conversation_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)
        """,
            (
                tid,
                conversation_id,
                type_enum.value,
                name,
                command,
                status_enum.value,
                subagent_conversation_id,
                now,
                now,
            ),
        )
        await db.commit()

    return BackgroundTask(
        id=tid,
        conversation_id=conversation_id,
        task_type=type_enum,
        name=name,
        command=command,
        status=status_enum,
        exit_code=None,
        stdout="",
        stderr="",
        subagent_conversation_id=subagent_conversation_id,
        created_at=now,
        updated_at=now,
    )


async def get_task(task_id: str) -> Optional[BackgroundTask]:
    """Fetches a background task by its ID.

    Args:
        task_id: Task unique identifier.

    Returns:
        Optional[BackgroundTask]: Task instance if found, else None.
    """
    async with get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return _row_to_task(row) if row else None


#: Every conversation descended from one conversation through sub-agent delegation,
#: including the conversation itself. Sub-agents share their parent's container, so
#: their processes are running in the same place and belong in the same list.
_CONVERSATION_TREE_CTE = """
WITH RECURSIVE tree(id) AS (
    SELECT ?
    UNION
    SELECT conversations.id FROM conversations
    JOIN tree ON conversations.parent_conversation_id = tree.id
)
"""


async def list_tasks(
    conversation_id: Optional[str] = None,
    include_subagents: bool = False,
) -> List[BackgroundTask]:
    """Lists background tasks, optionally filtered by conversation.

    Args:
        conversation_id: Optional filter for a specific conversation session.
        include_subagents: Also return tasks started by sub-agents of that
            conversation, at any depth. Their work runs in the same container, so a
            process left behind by a sub-agent is the caller's process to see.

    Returns:
        List[BackgroundTask]: List of task records.
    """
    async with get_db_connection() as db:
        if conversation_id and include_subagents:
            cursor = await db.execute(
                _CONVERSATION_TREE_CTE
                + "SELECT tasks.* FROM tasks JOIN tree ON tasks.conversation_id ="
                " tree.id ORDER BY tasks.created_at DESC",
                (conversation_id,),
            )
        elif conversation_id:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE conversation_id = ? ORDER BY"
                " created_at DESC",
                (conversation_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [_row_to_task(row) for row in rows]


async def update_task(
    task_id: str,
    status: Optional[TaskStatus | str] = None,
    exit_code: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
) -> None:
    """Updates attributes of an active or completed background task.

    Args:
        task_id: Unique task identifier.
        status: Optional updated task status enum or string.
        exit_code: Optional process exit code.
        stdout: Optional full stdout replacement.
        stderr: Optional full stderr replacement.
    """
    now = _utc_now_iso()
    fields = ["updated_at = ?"]
    values: List[Any] = [now]

    if status is not None:
        status_val = status.value if isinstance(status, TaskStatus) else str(status)
        fields.append("status = ?")
        values.append(status_val)
    if exit_code is not None:
        fields.append("exit_code = ?")
        values.append(exit_code)
    if stdout is not None:
        fields.append("stdout = ?")
        values.append(stdout)
    if stderr is not None:
        fields.append("stderr = ?")
        values.append(stderr)

    values.append(task_id)
    query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?"

    async with get_db_connection() as db:
        await db.execute(query, tuple(values))
        await db.commit()


async def append_task_output(
    task_id: str,
    stdout_chunk: Optional[str] = None,
    stderr_chunk: Optional[str] = None,
) -> None:
    """Appends live stream text chunks to a task's stdout or stderr in the DB.

    Args:
        task_id: Unique task identifier.
        stdout_chunk: Optional new text chunk from stdout.
        stderr_chunk: Optional new text chunk from stderr.
    """
    now = _utc_now_iso()
    async with get_db_connection() as db:
        if stdout_chunk:
            await db.execute(
                "UPDATE tasks SET stdout = stdout || ?, updated_at = ? WHERE id"
                " = ?",
                (stdout_chunk, now, task_id),
            )
        if stderr_chunk:
            await db.execute(
                "UPDATE tasks SET stderr = stderr || ?, updated_at = ? WHERE id"
                " = ?",
                (stderr_chunk, now, task_id),
            )
        await db.commit()
