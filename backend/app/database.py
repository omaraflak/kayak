from contextlib import asynccontextmanager
from datetime import datetime
import json
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
    """Initializes the SQLite schema with tables and indices."""
    async with get_db_connection() as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            isolated_container INTEGER DEFAULT 0,
            container_id TEXT,
            status TEXT DEFAULT 'active',
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
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)

        # Migration: Ensure thinking column exists if table was previously created without it
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN thinking TEXT;")
        except Exception:
            pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            name TEXT NOT NULL,
            command TEXT,
            status TEXT DEFAULT 'running',
            pid INTEGER,
            exit_code INTEGER,
            stdout TEXT DEFAULT '',
            stderr TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)

        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON"
            " messages(conversation_id, created_at);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON"
            " tasks(conversation_id);"
        )
        await db.commit()


# --- Row-to-Model Helpers ---


def _row_to_conversation(row: aiosqlite.Row) -> Conversation:
    """Converts a database row to a Conversation model."""
    return Conversation(
        id=row["id"],
        title=row["title"],
        agent_id=row["agent_id"],
        isolated_container=bool(row["isolated_container"]),
        container_id=row["container_id"],
        status=ConversationStatus(row["status"]),
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
        pid=row["pid"],
        exit_code=row["exit_code"],
        stdout=row["stdout"],
        stderr=row["stderr"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_message(row: aiosqlite.Row) -> Message:
    """Converts a database row to a Message model."""
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        thinking=row["thinking"] if "thinking" in row.keys() else None,
        tool_calls=json.loads(row["tool_calls"]) if row["tool_calls"] else None,
        tool_call_id=row["tool_call_id"],
        name=row["name"],
        created_at=row["created_at"],
    )


# --- Conversation Operations ---


async def create_conversation(
    title: str,
    agent_id: str = "general",
    isolated_container: bool = False,
    conversation_id: Optional[str] = None,
) -> Conversation:
    """Creates a new conversation record in the database.

    Args:
        title: Title of the conversation session.
        agent_id: ID of the agent profile assigned to this conversation.
        isolated_container: Whether to run this conversation in an isolated Docker container.
        conversation_id: Optional predetermined UUID identifier.

    Returns:
        Conversation: The newly created Conversation instance.
    """
    cid = conversation_id or str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    status = ConversationStatus.ACTIVE

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO conversations (id, title, agent_id, isolated_container, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (cid, title, agent_id, 1 if isolated_container else 0, status.value, now, now),
        )
        await db.commit()

    workspace = settings.WORKSPACES_DIR / cid
    workspace.mkdir(parents=True, exist_ok=True)

    return Conversation(
        id=cid,
        title=title,
        agent_id=agent_id,
        isolated_container=isolated_container,
        container_id=None,
        status=status,
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
    agent_id: Optional[str] = None,
    status: Optional[ConversationStatus | str] = None,
    container_id: Optional[str] = None,
) -> None:
    """Updates specified attributes of an existing conversation.

    Args:
        conversation_id: Unique identifier of the conversation to update.
        title: Optional new title.
        agent_id: Optional new agent identifier.
        status: Optional updated conversation status enum or string.
        container_id: Optional Docker container identifier.
    """
    now = datetime.utcnow().isoformat()
    fields = ["updated_at = ?"]
    values: List[Any] = [now]

    if title is not None:
        fields.append("title = ?")
        values.append(title)
    if agent_id is not None:
        fields.append("agent_id = ?")
        values.append(agent_id)
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

    Returns:
        Message: The created Message record.
    """
    mid = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    role_enum = role if isinstance(role, MessageRole) else MessageRole(role)
    tool_calls_json = json.dumps(tool_calls) if tool_calls else None

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO messages (id, conversation_id, role, content, thinking, tool_calls, tool_call_id, name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            " created_at ASC",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_message(row) for row in rows]


# --- Task Operations ---


async def create_task(
    conversation_id: str,
    task_type: TaskType | str,
    name: str,
    command: Optional[str] = None,
    pid: Optional[int] = None,
) -> BackgroundTask:
    """Inserts a new asynchronous background task record.

    Args:
        conversation_id: ID of the originating conversation.
        task_type: Task classification type.
        name: Descriptive name for the task.
        command: Optional shell command line executed.
        pid: Optional process ID.

    Returns:
        BackgroundTask: Created background task record.
    """
    tid = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    type_enum = task_type if isinstance(task_type, TaskType) else TaskType(task_type)
    status_enum = TaskStatus.RUNNING

    async with get_db_connection() as db:
        await db.execute(
            """
        INSERT INTO tasks (id, conversation_id, task_type, name, command, status, pid, stdout, stderr, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
        """,
            (tid, conversation_id, type_enum.value, name, command, status_enum.value, pid, now, now),
        )
        await db.commit()

    return BackgroundTask(
        id=tid,
        conversation_id=conversation_id,
        task_type=type_enum,
        name=name,
        command=command,
        status=status_enum,
        pid=pid,
        exit_code=None,
        stdout="",
        stderr="",
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


async def list_tasks(
    conversation_id: Optional[str] = None,
) -> List[BackgroundTask]:
    """Lists background tasks, optionally filtered by conversation.

    Args:
        conversation_id: Optional filter for a specific conversation session.

    Returns:
        List[BackgroundTask]: List of task records.
    """
    async with get_db_connection() as db:
        if conversation_id:
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
    pid: Optional[int] = None,
) -> None:
    """Updates attributes of an active or completed background task.

    Args:
        task_id: Unique task identifier.
        status: Optional updated task status enum or string.
        exit_code: Optional process exit code.
        stdout: Optional full stdout replacement.
        stderr: Optional full stderr replacement.
        pid: Optional process ID.
    """
    now = datetime.utcnow().isoformat()
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
    if pid is not None:
        fields.append("pid = ?")
        values.append(pid)

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
    now = datetime.utcnow().isoformat()
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
