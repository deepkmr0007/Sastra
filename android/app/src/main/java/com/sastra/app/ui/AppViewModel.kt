package com.sastra.app.ui

import android.content.Context
import android.os.BatteryManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.sastra.app.data.ApiException
import com.sastra.app.data.LocalConversation
import com.sastra.app.data.LocalMemory
import com.sastra.app.data.LocalMessage
import com.sastra.app.data.LocalTask
import com.sastra.app.data.Repository
import com.sastra.app.data.objects
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

enum class Screen(val title: String) {
    HOME("Home"), CHAT("Chat"), TASKS("Tasks"), RESEARCH("Research"), PROJECTS("Projects"), MEMORY("Memory"),
    FILES("Files"), APPROVALS("Approvals"), MODELS("Models"), SECURITY("Security"), SETTINGS("Settings")
}

class AppViewModel(val repo: Repository) : ViewModel() {
    var screen by mutableStateOf(if (repo.loggedIn) Screen.HOME else Screen.SETTINGS)
    var online by mutableStateOf(false)
    var busy by mutableStateOf(false)
    var message by mutableStateOf<String?>(null)
    var status by mutableStateOf<JSONObject?>(null)

    // chat
    var conversations by mutableStateOf<List<LocalConversation>>(emptyList())
    var currentConv by mutableStateOf<LocalConversation?>(null)
    val chatMessages = mutableStateListOf<LocalMessage>()
    var streaming by mutableStateOf("")
    var chatRoute by mutableStateOf("auto")

    // server lists
    var tasks by mutableStateOf<List<JSONObject>>(emptyList())
    var offlineTasks by mutableStateOf<List<LocalTask>>(emptyList())
    var taskDetail by mutableStateOf<JSONObject?>(null)
    var approvals by mutableStateOf<List<JSONObject>>(emptyList())
    var grants by mutableStateOf<List<JSONObject>>(emptyList())
    var reports by mutableStateOf<List<JSONObject>>(emptyList())
    var report by mutableStateOf<JSONObject?>(null)
    var projects by mutableStateOf<List<JSONObject>>(emptyList())
    var project by mutableStateOf<JSONObject?>(null)
    var fileView by mutableStateOf<Pair<String, String>?>(null)
    var artifacts by mutableStateOf<List<JSONObject>>(emptyList())
    var memories by mutableStateOf<List<LocalMemory>>(emptyList())
    var serverMemories by mutableStateOf<List<JSONObject>>(emptyList())
    var models by mutableStateOf<JSONObject?>(null)
    var controls by mutableStateOf<JSONObject?>(null)
    var privacy by mutableStateOf<JSONObject?>(null)
    var budget by mutableStateOf<JSONObject?>(null)
    var audit by mutableStateOf<List<JSONObject>>(emptyList())
    var auditIntact by mutableStateOf<Boolean?>(null)
    var events by mutableStateOf<List<JSONObject>>(emptyList())
    var sessions by mutableStateOf<List<JSONObject>>(emptyList())
    var benchmark by mutableStateOf<String?>(null)
    var ownerExists by mutableStateOf(true)

    init {
        refreshConversations()
        viewModelScope.launch {
            while (true) {
                refreshStatus()
                delay(20_000)
            }
        }
    }

    private fun launchSafe(label: String = "", block: suspend () -> Unit) = viewModelScope.launch {
        busy = true
        try {
            block()
        } catch (e: ApiException) {
            message = if (e.status == 401) "Session expired — sign in again in Settings" else (e.message ?: "Error")
        } catch (e: Exception) {
            message = (if (label.isNotEmpty()) "$label: " else "") + (e.message ?: e.javaClass.simpleName)
        } finally {
            busy = false
        }
    }

    fun go(s: Screen) {
        screen = s
        when (s) {
            Screen.HOME -> { refreshStatus(); loadTasks(); loadProjects() }
            Screen.TASKS -> loadTasks()
            Screen.RESEARCH -> loadReports()
            Screen.PROJECTS -> loadProjects()
            Screen.MEMORY -> loadMemory("")
            Screen.FILES -> loadArtifacts()
            Screen.APPROVALS -> loadApprovals()
            Screen.MODELS -> loadModels()
            Screen.SECURITY -> loadSecurity()
            Screen.SETTINGS -> checkOwner()
            Screen.CHAT -> refreshConversations()
        }
    }

    fun refreshStatus() = viewModelScope.launch {
        online = repo.reachable()
        status = if (online && repo.loggedIn) try { repo.get("/api/status") } catch (_: Exception) { null } else null
    }

    // ------------------------------------------------------------- auth
    fun checkOwner() = viewModelScope.launch {
        if (repo.serverUrl.isNotBlank()) ownerExists = try { repo.ownerExists() } catch (_: Exception) { true }
    }

    fun saveServer(url: String) {
        repo.serverUrl = url
        refreshStatus()
        checkOwner()
        message = "Server saved"
    }

    fun createOwner(user: String, pw: String, code: String) = launchSafe("Setup") {
        repo.createOwner(user, pw, code)
        repo.login(user, pw, null)
        message = "Owner created and signed in"
        ownerExists = true
        go(Screen.HOME)
    }

    fun login(user: String, pw: String, totp: String) = launchSafe("Sign in") {
        repo.login(user, pw, totp)
        message = "Signed in"
        go(Screen.HOME)
    }

    var totpSecret by mutableStateOf<String?>(null)
    fun beginTotp() = launchSafe("2-step") { totpSecret = repo.post("/api/auth/totp/begin").optString("secret") }
    fun confirmTotp(code: String) = launchSafe("2-step") {
        repo.post("/api/auth/totp/confirm", JSONObject().put("code", code.trim()))
        totpSecret = null
        message = "2-step login enabled"
    }

    fun logout() = launchSafe { repo.logout(); message = "Signed out" }

    fun sync() = launchSafe("Sync") { message = repo.sync(); refreshConversations() }

    // ------------------------------------------------------------- chat
    fun refreshConversations() {
        conversations = repo.db.conversations()
        currentConv?.let { c -> chatMessages.clear(); chatMessages.addAll(repo.db.messages(c.id)) }
    }

    fun openConversation(c: LocalConversation?) {
        currentConv = c
        chatMessages.clear()
        if (c != null) chatMessages.addAll(repo.db.messages(c.id))
    }

    fun newConversation(private: Boolean) {
        openConversation(repo.db.createConversation(if (private) "Private chat" else "New chat", private))
        refreshConversations()
    }

    fun deleteConversation(c: LocalConversation) {
        repo.db.deleteConversation(c.id)
        if (currentConv?.id == c.id) openConversation(null)
        refreshConversations()
    }

    fun send(text: String, onReply: (String) -> Unit = {}) {
        if (text.isBlank()) return
        val conv = currentConv ?: repo.db.createConversation(text.take(40), false).also { currentConv = it }
        viewModelScope.launch {
            busy = true
            streaming = ""
            chatMessages.add(LocalMessage("pending-user", conv.id, "user", text, null, System.currentTimeMillis(), false))
            try {
                val r = repo.chat(conv, text, chatRoute) { piece ->
                    viewModelScope.launch(Dispatchers.Main) { streaming += piece }
                }
                if (r.taskId != null) message = "Task started — see Tasks"
                onReply(r.text)
            } catch (e: Exception) {
                message = "Chat failed: ${e.message}"
            } finally {
                streaming = ""
                busy = false
                currentConv = repo.db.conversation(conv.id)
                refreshConversations()
            }
        }
    }

    // ------------------------------------------------------------ tasks
    fun loadTasks() = launchSafe {
        offlineTasks = repo.db.offlineTasks()
        if (repo.loggedIn && online) tasks = repo.get("/api/tasks?limit=100").getJSONArray("tasks").objects()
    }

    fun createTask(request: String, kind: String?, projectId: String? = null) = launchSafe("Task") {
        repo.createTask(request, kind, projectId)
        message = if (online) "Task created" else "Queued offline — will start when connected"
        loadTasks()
    }

    fun openTask(id: String) = launchSafe { taskDetail = repo.get("/api/tasks/$id") }
    fun cancelTask(id: String) = launchSafe { repo.post("/api/tasks/$id/cancel"); loadTasks(); taskDetail = null }
    fun retryTask(id: String) = launchSafe { repo.post("/api/tasks/$id/retry"); loadTasks() }
    fun answerTask(id: String, answer: String) = launchSafe {
        repo.post("/api/tasks/$id/input", JSONObject().put("answer", answer)); loadTasks(); taskDetail = null
    }

    // -------------------------------------------------------- approvals
    fun loadApprovals() = launchSafe {
        val j = repo.get("/api/approvals")
        approvals = j.getJSONArray("pending").objects()
        grants = j.getJSONArray("grants").objects()
    }

    fun decide(id: String, approve: Boolean, batchUses: Int = 0) = launchSafe {
        val body = JSONObject().put("approve", approve)
        if (approve && batchUses > 1) body.put("grant", JSONObject().put("max_uses", batchUses).put("ttl_hours", 24).put("scope", JSONObject()))
        repo.post("/api/approvals/$id/decide", body)
        message = if (approve) "Approved" else "Rejected"
        loadApprovals(); refreshStatus()
    }

    fun revokeGrant(id: String) = launchSafe { repo.delete("/api/grants/$id"); loadApprovals() }

    // --------------------------------------------------------- research
    fun loadReports() = launchSafe { reports = repo.get("/api/research").getJSONArray("reports").objects() }
    fun startResearch(q: String, urls: List<String>) = launchSafe("Research") {
        val body = JSONObject().put("question", q).put("urls", org.json.JSONArray(urls))
        repo.post("/api/research", body)
        message = "Research started — you'll be notified when the report is ready"
    }
    fun openReport(id: String) = launchSafe { report = repo.get("/api/research/$id") }

    // --------------------------------------------------------- projects
    fun loadProjects() = launchSafe { if (repo.loggedIn && online) projects = repo.get("/api/projects").getJSONArray("projects").objects() }
    fun createProject(name: String, desc: String, kind: String) = launchSafe {
        repo.post("/api/projects", JSONObject().put("name", name).put("description", desc).put("kind", kind)); loadProjects()
    }
    fun openProject(id: String) = launchSafe { project = repo.get("/api/projects/$id"); fileView = null }
    fun openFile(pid: String, path: String) = launchSafe {
        fileView = path to repo.get("/api/projects/$pid/file?path=${repo.enc(path)}").optString("content")
    }
    fun checkpoint(pid: String) = launchSafe { repo.post("/api/projects/$pid/checkpoint", JSONObject().put("message", "Checkpoint from phone")); openProject(pid) }
    fun rollback(pid: String, commit: String) = launchSafe { repo.post("/api/projects/$pid/rollback", JSONObject().put("commit", commit)); openProject(pid) }

    // ------------------------------------------------------------ files
    fun loadArtifacts() = launchSafe { artifacts = repo.get("/api/artifacts").getJSONArray("artifacts").objects() }

    fun downloadArtifact(ctx: Context, a: JSONObject, onReady: (java.io.File) -> Unit) = launchSafe("Download") {
        val (data, sha) = repo.bytes("/api/artifacts/${a.getString("id")}/download")
        val digest = java.security.MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
        if (sha != null && sha != digest) throw IllegalStateException("Integrity check failed (hash mismatch)")
        val dir = java.io.File(ctx.cacheDir, "shared").apply { mkdirs() }
        val safe = a.optString("name").replace(Regex("[^A-Za-z0-9._-]"), "_").ifBlank { "artifact" }
        val f = java.io.File(dir, safe)
        withContext(Dispatchers.IO) { f.writeBytes(data) }
        message = "Downloaded and verified (sha256 ${digest.take(12)}…)"
        onReady(f)
    }

    // ----------------------------------------------------------- memory
    fun loadMemory(q: String) = launchSafe {
        memories = repo.db.memories(q)
        if (repo.loggedIn && online) {
            val path = if (q.isBlank()) "/api/memory" else "/api/memory?q=${repo.enc(q)}"
            val j = repo.get(path)
            serverMemories = (j.optJSONArray("memories") ?: j.optJSONArray("results"))?.objects().orEmpty()
        }
    }
    fun addMemory(content: String, category: String) = launchSafe("Memory") { message = repo.addMemory(content, category); loadMemory("") }
    fun deleteServerMemory(id: String) = launchSafe { repo.delete("/api/memory/$id"); loadMemory("") }
    fun deleteLocalMemory(id: String) { repo.db.deleteMemory(id); memories = repo.db.memories("") }
    fun approveMemory(id: String) = launchSafe { repo.post("/api/memory/$id/approve"); loadMemory("") }
    fun exportMemory(onJson: (String) -> Unit) = launchSafe { onJson(repo.get("/api/memory/export").toString(2)) }

    // ----------------------------------------------------------- models
    fun loadModels() = launchSafe { if (repo.loggedIn && online) { models = repo.get("/api/models"); budget = repo.get("/api/budget") } }
    fun setPreference(order: List<String>) = launchSafe { repo.put("/api/models/preference", JSONObject().put("order", org.json.JSONArray(order))); loadModels() }
    fun setConsent(provider: String, level: String) = launchSafe {
        repo.put("/api/models/consent", JSONObject().put("provider_consent", JSONObject().put(provider, level))); loadModels()
    }
    fun setBudget(daily: Double, monthly: Double) = launchSafe {
        repo.put("/api/budget", JSONObject().put("daily_usd", daily).put("monthly_usd", monthly)); loadModels()
    }
    fun saveLocalModel(url: String, name: String) { repo.localModelUrl = url; repo.localModelName = name; message = "On-device model saved" }

    fun benchmarkLocal(ctx: Context) = launchSafe("Benchmark") {
        val bm = ctx.getSystemService(BatteryManager::class.java)
        val before = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        val tempBefore = batteryTemp(ctx)
        val r = withContext(Dispatchers.IO) { repo.localModel().benchmark() }
        val tempAfter = batteryTemp(ctx)
        val after = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        val mem = Runtime.getRuntime().let { (it.totalMemory() - it.freeMemory()) / 1_048_576 }
        benchmark = if (!r.ok) "Failed: ${r.error}\nIs the model server running in Termux at ${repo.localModelUrl}?"
        else "Time to first token: ${r.ttftMs} ms\nTotal: ${r.totalMs} ms\n≈ ${"%.1f".format(r.tokensPerSec)} tokens/s\n" +
            "Battery: $before% → $after%   Temp: ${tempBefore}°C → ${tempAfter}°C\nApp heap in use: $mem MB\nAnswer: ${r.text.take(120)}"
    }

    private fun batteryTemp(ctx: Context): Double {
        val i = ctx.registerReceiver(null, android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
        return (i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0) / 10.0
    }

    // --------------------------------------------------------- security
    fun loadSecurity() = launchSafe {
        if (!(repo.loggedIn && online)) return@launchSafe
        controls = repo.get("/api/controls")
        privacy = repo.get("/api/privacy")
        budget = repo.get("/api/budget")
        events = repo.get("/api/security/events").getJSONArray("events").objects()
        audit = repo.get("/api/audit?limit=60").getJSONArray("entries").objects()
        sessions = repo.get("/api/auth/sessions").getJSONArray("sessions").objects()
        auditIntact = repo.get("/api/audit/verify").optBoolean("intact")
    }

    fun setControl(key: String, value: Any) = launchSafe {
        controls = repo.put("/api/controls", JSONObject().put(key, value)); refreshStatus()
    }
    fun panic() = launchSafe { controls = repo.post("/api/controls/panic"); message = "Everything stopped"; refreshStatus() }
    fun resume() = launchSafe { controls = repo.post("/api/controls/resume"); message = "Resumed"; refreshStatus() }
    fun revokeSession(id: String) = launchSafe { repo.delete("/api/auth/sessions/$id"); loadSecurity() }
    fun ackEvent(id: String) = launchSafe { repo.post("/api/security/events/$id/ack"); loadSecurity() }
    fun backupNow() = launchSafe("Backup") { val j = repo.post("/api/backup"); message = "Encrypted backup ${j.optString("name")} created" }
    fun setRetention(days: Int) = launchSafe { repo.put("/api/memory/retention", JSONObject().put("conversation_retention_days", days)); message = "Retention set to $days days" }

    fun wipePhone() {
        repo.db.wipe()
        repo.store.wipe()
        openConversation(null)
        refreshConversations()
        message = "All SASTRA data on this phone was deleted"
        screen = Screen.SETTINGS
    }
}
