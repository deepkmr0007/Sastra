package com.sastra.app.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.util.UUID

data class LocalMessage(val id: String, val conversationId: String, val role: String, val content: String,
                        val provider: String?, val createdAt: Long, val queued: Boolean)

data class LocalConversation(val id: String, val serverId: String?, val title: String, val private: Boolean, val updatedAt: Long)

data class LocalMemory(val id: String, val serverId: String?, val category: String, val content: String,
                       val status: String, val synced: Boolean, val updatedAt: Long)

data class LocalTask(val id: String, val serverId: String?, val title: String, val request: String, val kind: String,
                     val state: String, val result: String?, val error: String?, val synced: Boolean, val updatedAt: Long)

/**
 * On-device store (app-private, excluded from backups). Works without the server:
 * conversations, memory and queued tasks live here and sync when online.
 */
class LocalDb(context: Context) : SQLiteOpenHelper(context, "sastra_local.db", null, 1) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE conversations (id TEXT PRIMARY KEY, server_id TEXT, title TEXT NOT NULL, private INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, provider TEXT, created_at INTEGER NOT NULL, queued INTEGER NOT NULL DEFAULT 0)")
        db.execSQL("CREATE INDEX ix_msg_conv ON messages(conversation_id, created_at)")
        db.execSQL("CREATE TABLE memories (id TEXT PRIMARY KEY, server_id TEXT, category TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', synced INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE tasks (id TEXT PRIMARY KEY, server_id TEXT, title TEXT NOT NULL, request TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error TEXT, synced INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE local_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {}

    override fun onConfigure(db: SQLiteDatabase) {
        db.setForeignKeyConstraintsEnabled(true)
    }

    private fun newId() = UUID.randomUUID().toString().replace("-", "")
    private fun now() = System.currentTimeMillis()

    private inline fun <T> Cursor.mapAll(f: (Cursor) -> T): List<T> = use { c ->
        val out = ArrayList<T>()
        while (c.moveToNext()) out.add(f(c))
        out
    }

    private fun Cursor.str(col: String): String? = getColumnIndexOrThrow(col).let { if (isNull(it)) null else getString(it) }
    private fun Cursor.lng(col: String): Long = getLong(getColumnIndexOrThrow(col))
    private fun Cursor.int(col: String): Int = getInt(getColumnIndexOrThrow(col))

    // ------------------------------------------------------------ audit
    fun audit(action: String, detail: String) {
        writableDatabase.insert("local_audit", null, ContentValues().apply {
            put("ts", now()); put("action", action); put("detail", detail.take(500))
        })
    }

    fun auditLog(limit: Int = 200): List<Triple<Long, String, String>> =
        readableDatabase.rawQuery("SELECT ts, action, detail FROM local_audit ORDER BY id DESC LIMIT $limit", null)
            .mapAll { Triple(it.lng("ts"), it.str("action") ?: "", it.str("detail") ?: "") }

    // ---------------------------------------------------- conversations
    fun createConversation(title: String, private: Boolean): LocalConversation {
        val c = LocalConversation(newId(), null, title.take(80).ifBlank { "New chat" }, private, now())
        writableDatabase.insert("conversations", null, ContentValues().apply {
            put("id", c.id); put("title", c.title); put("private", if (private) 1 else 0); put("updated_at", c.updatedAt)
        })
        return c
    }

    fun setConversationServerId(id: String, serverId: String) {
        writableDatabase.update("conversations", ContentValues().apply { put("server_id", serverId) }, "id = ?", arrayOf(id))
    }

    fun conversations(): List<LocalConversation> =
        readableDatabase.rawQuery("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 200", null).mapAll {
            LocalConversation(it.str("id")!!, it.str("server_id"), it.str("title")!!, it.int("private") == 1, it.lng("updated_at"))
        }

    fun conversation(id: String): LocalConversation? = conversations().firstOrNull { it.id == id }

    fun deleteConversation(id: String) {
        writableDatabase.delete("messages", "conversation_id = ?", arrayOf(id))
        writableDatabase.delete("conversations", "id = ?", arrayOf(id))
    }

    // --------------------------------------------------------- messages
    fun addMessage(conversationId: String, role: String, content: String, provider: String?, queued: Boolean = false): LocalMessage {
        val m = LocalMessage(newId(), conversationId, role, content, provider, now(), queued)
        writableDatabase.insert("messages", null, ContentValues().apply {
            put("id", m.id); put("conversation_id", conversationId); put("role", role); put("content", content)
            put("provider", provider); put("created_at", m.createdAt); put("queued", if (queued) 1 else 0)
        })
        writableDatabase.update("conversations", ContentValues().apply { put("updated_at", m.createdAt) }, "id = ?", arrayOf(conversationId))
        return m
    }

    fun updateMessage(id: String, content: String, provider: String?) {
        writableDatabase.update("messages", ContentValues().apply { put("content", content); put("provider", provider) }, "id = ?", arrayOf(id))
    }

    fun messages(conversationId: String): List<LocalMessage> =
        readableDatabase.rawQuery("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at", arrayOf(conversationId)).mapAll {
            LocalMessage(it.str("id")!!, it.str("conversation_id")!!, it.str("role")!!, it.str("content")!!, it.str("provider"),
                it.lng("created_at"), it.int("queued") == 1)
        }

    fun queuedMessages(): List<LocalMessage> =
        readableDatabase.rawQuery("SELECT * FROM messages WHERE queued = 1 ORDER BY created_at LIMIT 100", null).mapAll {
            LocalMessage(it.str("id")!!, it.str("conversation_id")!!, it.str("role")!!, it.str("content")!!, it.str("provider"),
                it.lng("created_at"), true)
        }

    fun setQueued(id: String) {
        writableDatabase.update("messages", ContentValues().apply { put("queued", 1) }, "id = ?", arrayOf(id))
    }

    fun clearQueued(id: String) {
        writableDatabase.update("messages", ContentValues().apply { put("queued", 0) }, "id = ?", arrayOf(id))
    }

    // --------------------------------------------------------- memories
    fun addMemory(category: String, content: String, serverId: String? = null, status: String = "active", synced: Boolean = false): LocalMemory {
        val m = LocalMemory(newId(), serverId, category, content, status, synced, now())
        writableDatabase.insert("memories", null, ContentValues().apply {
            put("id", m.id); put("server_id", serverId); put("category", category); put("content", content)
            put("status", status); put("synced", if (synced) 1 else 0); put("updated_at", m.updatedAt)
        })
        return m
    }

    fun memories(query: String = ""): List<LocalMemory> {
        val rows = readableDatabase.rawQuery("SELECT * FROM memories ORDER BY updated_at DESC LIMIT 1000", null).mapAll {
            LocalMemory(it.str("id")!!, it.str("server_id"), it.str("category")!!, it.str("content")!!, it.str("status")!!,
                it.int("synced") == 1, it.lng("updated_at"))
        }
        if (query.isBlank()) return rows
        val terms = query.lowercase().split(Regex("\\W+")).filter { it.length > 2 }
        return rows.map { m -> m to terms.count { m.content.lowercase().contains(it) } }
            .filter { it.second > 0 && it.first.status == "active" }
            .sortedByDescending { it.second }.map { it.first }
    }

    fun unsyncedMemories(): List<LocalMemory> = memories().filter { !it.synced }

    fun markMemorySynced(id: String, serverId: String, status: String) {
        writableDatabase.update("memories", ContentValues().apply { put("synced", 1); put("server_id", serverId); put("status", status) },
            "id = ?", arrayOf(id))
    }

    /** Replace the synced cache with the server's view; keep unsynced local notes. */
    fun replaceSyncedMemories(items: List<LocalMemory>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.delete("memories", "synced = 1", null)
            for (m in items) {
                db.insert("memories", null, ContentValues().apply {
                    put("id", newId()); put("server_id", m.serverId); put("category", m.category); put("content", m.content)
                    put("status", m.status); put("synced", 1); put("updated_at", m.updatedAt)
                })
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun deleteMemory(id: String) {
        writableDatabase.delete("memories", "id = ?", arrayOf(id))
    }

    // ------------------------------------------------------------ tasks
    fun addTask(request: String, kind: String): LocalTask {
        val t = LocalTask(newId(), null, request.take(120), request, kind, "QUEUED_OFFLINE", null, null, false, now())
        writableDatabase.insert("tasks", null, ContentValues().apply {
            put("id", t.id); put("title", t.title); put("request", request); put("kind", kind); put("state", t.state)
            put("synced", 0); put("updated_at", t.updatedAt)
        })
        return t
    }

    fun unsyncedTasks(): List<LocalTask> =
        readableDatabase.rawQuery("SELECT * FROM tasks WHERE synced = 0 ORDER BY updated_at", null).mapAll { taskRow(it) }

    fun markTaskSynced(id: String, serverId: String) {
        writableDatabase.update("tasks", ContentValues().apply { put("synced", 1); put("server_id", serverId); put("state", "CREATED") },
            "id = ?", arrayOf(id))
    }

    fun offlineTasks(): List<LocalTask> =
        readableDatabase.rawQuery("SELECT * FROM tasks WHERE synced = 0 ORDER BY updated_at DESC", null).mapAll { taskRow(it) }

    private fun taskRow(it: Cursor) = LocalTask(it.str("id")!!, it.str("server_id"), it.str("title")!!, it.str("request")!!,
        it.str("kind")!!, it.str("state")!!, it.str("result"), it.str("error"), it.int("synced") == 1, it.lng("updated_at"))

    fun wipe() {
        val db = writableDatabase
        for (t in listOf("messages", "conversations", "memories", "tasks", "local_audit")) db.delete(t, null, null)
        db.execSQL("VACUUM")
    }
}
