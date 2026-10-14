package com.sastra.app.ui.screens

import android.content.Intent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.sastra.app.data.objects
import com.sastra.app.ui.*
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Ok
import com.sastra.app.ui.theme.Warn

@Composable
fun MemoryScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    var q by remember { mutableStateOf("") }
    var content by remember { mutableStateOf("") }
    var cat by remember { mutableStateOf("preference") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("Remember something") {
                Field(content, { content = it }, "e.g. Reply in Hinglish unless I write in English", singleLine = false)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Choice("Category", listOf("preference", "project", "fact", "procedure", "working"), cat) { cat = it }
                    Button(onClick = { vm.addMemory(content, cat); content = "" }, enabled = content.isNotBlank()) { Text("Save") }
                }
                Muted("Passwords, OTPs, card/Aadhaar/PAN numbers and API keys are refused automatically.")
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(q, { q = it; vm.loadMemory(it) }, modifier = Modifier.weight(1f), placeholder = { Text("Search memory") }, singleLine = true)
                TextButton(onClick = {
                    vm.exportMemory { json ->
                        ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("application/json")
                            .putExtra(Intent.EXTRA_TEXT, json), "Export memory"))
                    }
                }) { Text("Export") }
            }
        }
        if (vm.serverMemories.isNotEmpty()) item { Text("On your SASTRA server", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
        items(vm.serverMemories) { m ->
            Section {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Pill(m.optString("category")); Pill(m.optString("status"), stateColor(m.optString("status")))
                    if (m.has("score")) Muted("score " + m.optDouble("score"))
                }
                Text(m.optString("content"))
                Muted("source: " + m.optString("source") + " · " + fmtTime(m.optDouble("updated_at")))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (m.optString("status") in setOf("quarantined", "pending_validation", "conflict")) {
                        OutlinedButton(onClick = { vm.approveMemory(m.optString("id")) }) { Text("Approve") }
                    }
                    TextButton(onClick = { vm.deleteServerMemory(m.optString("id")) }) { Text("Delete", color = Bad) }
                }
            }
        }
        val unsynced = vm.memories.filter { !it.synced }
        if (unsynced.isNotEmpty()) item { Text("Saved on this phone (not yet synced)", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
        items(unsynced) { m ->
            Section {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { Pill(m.category); Pill("local", Warn) }
                Text(m.content)
                TextButton(onClick = { vm.deleteLocalMemory(m.id) }) { Text("Delete", color = Bad) }
            }
        }
        if (vm.serverMemories.isEmpty() && vm.memories.isEmpty()) item { Muted("Nothing saved yet.") }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
fun ModelsScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    var lmUrl by remember { mutableStateOf(vm.repo.localModelUrl.ifBlank { "http://127.0.0.1:8080/v1" }) }
    var lmName by remember { mutableStateOf(vm.repo.localModelName) }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("On-device model (works with no internet)") {
                Muted("Run llama.cpp's llama-server or Ollama in Termux on this phone, then point SASTRA at it. Private chats use it and nothing leaves the phone. See SETUP_ANDROID.md for model sizes that fit your RAM.")
                Field(lmUrl, { lmUrl = it }, "Endpoint (OpenAI-compatible)")
                Field(lmName, { lmName = it }, "Model name (e.g. qwen2.5-1.5b-instruct-q4_k_m)")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { vm.saveLocalModel(lmUrl, lmName) }) { Text("Save") }
                    OutlinedButton(onClick = { vm.saveLocalModel(lmUrl, lmName); vm.benchmarkLocal(ctx) }) { Text("Benchmark on this phone") }
                }
                vm.benchmark?.let { Mono(it) }
            }
        }
        val m = vm.models
        if (m == null) {
            item { Muted("Connect to your server to manage cloud/server models.") }
        } else {
            val pref = (0 until (m.optJSONArray("preference")?.length() ?: 0)).map { m.getJSONArray("preference").getString(it) }
            item { Text("Server providers (routing order)", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
            items(m.optJSONArray("providers")?.objects().orEmpty().filter { it.optString("name") != "offline" }) { p ->
                val name = p.optString("name")
                Section(name) {
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Pill(if (p.optBoolean("available")) "configured" else "not configured", if (p.optBoolean("available")) Ok else Warn)
                        Pill(if (p.optBoolean("local")) "self-hosted" else "cloud")
                        if (p.optString("circuit") == "open") Pill("failing — paused", Bad)
                    }
                    Choice("May receive", listOf("none", "public", "normal", "private", "secret"), p.optString("consent", "none")) { lvl ->
                        vm.setConsent(if (p.optBoolean("local")) "local" else name, lvl)
                    }
                    val i = pref.indexOf(name)
                    if (i > 0) TextButton(onClick = { vm.setPreference(pref.toMutableList().apply { removeAt(i); add(i - 1, name) }) }) { Text("Move up") }
                }
            }
            item {
                Muted("The offline fallback is always last. 'private' data only goes to providers you allow; 'secret' never leaves the server.")
                val b = vm.budget
                if (b != null) {
                    var daily by remember { mutableStateOf(b.optDouble("daily_usd").toString()) }
                    var monthly by remember { mutableStateOf(b.optDouble("monthly_usd").toString()) }
                    Section("Spending caps (USD, hard limit — fails closed)") {
                        Muted("Today $%.4f · this month $%.4f".format(b.optDouble("spent_today_usd"), b.optDouble("spent_month_usd")))
                        Field(daily, { daily = it }, "Daily cap")
                        Field(monthly, { monthly = it }, "Monthly cap")
                        Button(onClick = { vm.setBudget(daily.toDoubleOrNull() ?: 0.0, monthly.toDoubleOrNull() ?: 0.0) }) { Text("Save caps") }
                    }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}
