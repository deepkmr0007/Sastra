package com.sastra.app.data

import com.sastra.app.engine.LocalEngine
import com.sastra.app.engine.LocalModelClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder

/**
 * Offline-first repository. Everything the owner creates is written locally
 * first; the server is used when reachable, and the outbox syncs later with
 * client ids so replays are idempotent.
 */
class Repository(val db: LocalDb, val store: SecureStore) {

    val api = ApiClient({ serverUrl }, { token })
    private val engine = LocalEngine { q -> db.memories(q).map { it.content } }

    var serverUrl: String
        get() = store.getString("server_url", "")
        set(v) = store.putString("server_url", v.trim().trimEnd('/'))

    val token: String? get() = store.getSecret("token")
    val username: String get() = store.getString("username", "")
    val loggedIn: Boolean get() = token != null && serverUrl.isNotBlank()

    var localModelUrl: String
        get() = store.getString("local_model_url", "")
        set(v) = store.putString("local_model_url", v.trim())
    var localModelName: String
        get() = store.getString("local_model_name", "")
        set(v) = store.putString("local_model_name", v.trim())

    fun localModel() = LocalModelClient(localModelUrl, localModelName)

    suspend fun reachable(): Boolean = withContext(Dispatchers.IO) { serverUrl.isNotBlank() && api.reachable() }

    // ------------------------------------------------------------- auth
    suspend fun ownerExists(): Boolean = withContext(Dispatchers.IO) { api.get("/api/setup").optBoolean("owner_exists", true) }

    suspend fun createOwner(user: String, password: String, code: String) = withContext(Dispatchers.IO) {
        api.post("/api/setup", JSONObject().put("username", user).put("password", password).put("setup_code", code))
    }

    suspend fun login(user: String, password: String, totp: String?) = withContext(Dispatchers.IO) {
        val body = JSONObject().put("username", user).put("password", password)
            .put("device_name", "Android " + android.os.Build.MODEL)
        if (!totp.isNullOrBlank()) body.put("totp", totp)
        val res = api.post("/api/auth/login", body)
        store.putSecret("token", res.getString("token"))
        store.putString("username", user)
        db.audit("login", "Signed in to $serverUrl")
    }

    suspend fun logout() = withContext(Dispatchers.IO) {
        try { api.post("/api/auth/logout") } catch (_: Exception) {}
        store.putSecret("token", null)
        db.audit("logout", "Signed out")
    }

    // ------------------------------------------------------------- chat
    data class ChatResult(val text: String, val provider: String, val taskId: String? = null)

    /**
     * Answer a message. Online → server (streaming). Offline → on-device model if
     * configured, else the deterministic local engine; unanswerable requests are
     * queued for the server.
     */
    suspend fun chat(conv: LocalConversation, text: String, route: String, onDelta: (String) -> Unit): ChatResult =
        withContext(Dispatchers.IO) {
            val userMsg = db.addMessage(conv.id, "user", text, null)
            if (loggedIn && api.reachable()) {
                try {
                    var serverConv = conv.serverId
                    if (serverConv == null && conv.private) {
                        serverConv = api.post("/api/conversations", JSONObject().put("title", conv.title).put("privacy", "private")).getString("id")
                        db.setConversationServerId(conv.id, serverConv)
                    }
                    val body = JSONObject().put("message", text).put("route", route).put("client_id", userMsg.id)
                    if (serverConv != null) body.put("conversation_id", serverConv)
                    val sb = StringBuilder()
                    var provider = "server"
                    var taskId: String? = null
                    api.streamChat(body) { event, data ->
                        when (event) {
                            "meta" -> if (conv.serverId == null) db.setConversationServerId(conv.id, data.getString("conversation_id"))
                            "delta" -> { val t = data.optString("text"); sb.append(t); onDelta(t) }
                            "task" -> taskId = data.optString("task_id")
                            "done" -> provider = data.optString("provider") + "/" + data.optString("model")
                            "error" -> throw ApiException(500, data.optString("error"))
                        }
                    }
                    db.addMessage(conv.id, "assistant", sb.toString(), provider)
                    return@withContext ChatResult(sb.toString(), provider, taskId)
                } catch (e: ApiException) {
                    if (e.status == 401) store.putSecret("token", null)
                    if (e.status in 400..499) throw e
                } catch (_: java.io.IOException) {
                    // fall through to offline
                }
            }
            offlineAnswer(conv, userMsg, text, onDelta)
        }

    private fun offlineAnswer(conv: LocalConversation, userMsg: LocalMessage, text: String, onDelta: (String) -> Unit): ChatResult {
        val lm = localModel()
        if (lm.configured) {
            try {
                val history = db.messages(conv.id).takeLast(12).filter { it.role in setOf("user", "assistant") }
                    .map { it.role to it.content }
                val memory = db.memories(text).take(5).joinToString("\n") { "- " + it.content }
                val system = "You are SASTRA running fully on the owner's phone. Be concise and honest about uncertainty." +
                    if (memory.isNotBlank()) "\nSaved notes:\n$memory" else ""
                val out = lm.stream(system, history, onDelta)
                db.addMessage(conv.id, "assistant", out, "on-device")
                return ChatResult(out, "on-device")
            } catch (_: Exception) {
                // on-device model not running — fall back to the rule engine
            }
        }
        val r = engine.answer(text)
        r.remember?.let { db.addMemory("preference", it) }
        if (r.queueForServer) db.setQueued(userMsg.id) // the server enforces the conversation's privacy class
        onDelta(r.text)
        db.addMessage(conv.id, "assistant", r.text, "offline-engine")
        return ChatResult(r.text, "offline-engine")
    }

    // ------------------------------------------------------------- sync
    suspend fun sync(): String = withContext(Dispatchers.IO) {
        if (!loggedIn || !api.reachable()) return@withContext "Offline — nothing synced"
        val queued = db.queuedMessages()
        val tasks = db.unsyncedTasks()
        val mems = db.unsyncedMemories()
        val body = JSONObject()
            .put("messages", JSONArray(queued.map { m ->
                JSONObject().put("client_id", m.id).put("text", m.content).apply {
                    db.conversation(m.conversationId)?.serverId?.let { put("conversation_id", it) }
                }
            }))
            .put("tasks", JSONArray(tasks.map { JSONObject().put("client_id", it.id).put("request", it.request).put("kind", it.kind) }))
            .put("memories", JSONArray(mems.map { JSONObject().put("client_id", it.id).put("content", it.content).put("category", it.category) }))
        val res = api.post("/api/sync", body)
        res.optJSONArray("messages")?.objects()?.forEach { r ->
            val local = queued.firstOrNull { it.id == r.optString("client_id") } ?: return@forEach
            db.clearQueued(local.id)
            if (db.conversation(local.conversationId)?.serverId == null) db.setConversationServerId(local.conversationId, r.optString("conversation_id"))
            db.addMessage(local.conversationId, "assistant", r.optString("reply"), "server (synced)")
        }
        res.optJSONArray("tasks")?.objects()?.forEach { db.markTaskSynced(it.optString("client_id"), it.optString("task_id")) }
        res.optJSONArray("memories")?.objects()?.forEach { db.markMemorySynced(it.optString("client_id"), it.optString("id"), it.optString("status")) }
        pullMemories()
        val errors = res.optJSONArray("errors")?.length() ?: 0
        db.audit("sync", "pushed ${queued.size} messages, ${tasks.size} tasks, ${mems.size} memories; $errors errors")
        "Synced ${queued.size} messages, ${tasks.size} tasks, ${mems.size} notes" + if (errors > 0) " ($errors errors)" else ""
    }

    private fun pullMemories() {
        val list = api.get("/api/memory").optJSONArray("memories")?.objects() ?: return
        db.replaceSyncedMemories(list.map {
            LocalMemory("", it.optString("id"), it.optString("category"), it.optString("content"), it.optString("status"), true,
                (it.optDouble("updated_at", 0.0) * 1000).toLong())
        })
    }

    // ------------------------------------------------- server passthroughs
    suspend fun get(path: String): JSONObject = withContext(Dispatchers.IO) { api.get(path) }
    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject = withContext(Dispatchers.IO) { api.post(path, body) }
    suspend fun put(path: String, body: JSONObject): JSONObject = withContext(Dispatchers.IO) { api.put(path, body) }
    suspend fun delete(path: String): JSONObject = withContext(Dispatchers.IO) { api.delete(path) }
    suspend fun bytes(path: String): Pair<ByteArray, String?> = withContext(Dispatchers.IO) { api.bytes(path) }

    suspend fun createTask(request: String, kind: String?, projectId: String?): String = withContext(Dispatchers.IO) {
        if (loggedIn && api.reachable()) {
            val body = JSONObject().put("request", request)
            if (kind != null) body.put("kind", kind)
            if (projectId != null) body.put("project_id", projectId)
            api.post("/api/tasks", body).getString("id")
        } else {
            db.addTask(request, kind ?: "direct").id.also { db.audit("task.queued_offline", request.take(80)) }
        }
    }

    suspend fun addMemory(content: String, category: String): String = withContext(Dispatchers.IO) {
        val local = db.addMemory(category, content)
        if (loggedIn && api.reachable()) {
            try {
                val rec = api.post("/api/memory", JSONObject().put("content", content).put("category", category))
                db.markMemorySynced(local.id, rec.getString("id"), rec.optString("status", "active"))
                "Saved"
            } catch (e: ApiException) {
                db.deleteMemory(local.id) // server refused (e.g. looks like a secret) — don't keep it locally either
                throw e
            }
        } else "Saved on phone; will sync"
    }

    fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")
}
