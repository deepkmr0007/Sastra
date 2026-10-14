package com.sastra.app.ui.screens

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.sastra.app.ui.*
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Ok
import com.sastra.app.ui.theme.Warn
import java.util.Locale

@Composable
fun HomeScreen(vm: AppViewModel) {
    var command by remember { mutableStateOf("") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("What should SASTRA do?") {
                Field(command, { command = it }, "Ask, or describe a task", singleLine = false, minLines = 2)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { vm.chatRoute = "auto"; vm.go(Screen.CHAT); vm.send(command); command = "" }, enabled = command.isNotBlank()) { Text("Ask") }
                    OutlinedButton(onClick = { vm.createTask(command, null); command = "" }, enabled = command.isNotBlank()) { Text("Run as task") }
                }
                Muted("Questions are answered in chat. Bigger jobs (research, code, documents, data) run as background tasks you can watch and approve.")
            }
        }
        item {
            val s = vm.status
            Section("System") {
                if (!vm.online) {
                    Pill("Server unreachable — working offline", Warn)
                    Muted("You can still chat (on-device model or offline tools), save notes and queue tasks. They sync automatically.")
                } else if (s == null) {
                    Pill("Not signed in", Warn)
                    TextButton(onClick = { vm.go(Screen.SETTINGS) }) { Text("Connect to your SASTRA server") }
                } else {
                    FlowRowCompat {
                        Pill("Mode ${s.optInt("autonomy_mode")} · ${s.optString("autonomy_mode_name")}")
                        Pill(if (s.optBoolean("internet")) "Internet on" else "Internet off", if (s.optBoolean("internet")) Ok else Warn)
                        Pill("Sandbox: ${s.optJSONObject("sandbox")?.optString("backend") ?: "none"}")
                        Pill(if (s.optBoolean("encryption_at_rest")) "Encrypted at rest" else "No at-rest key", if (s.optBoolean("encryption_at_rest")) Ok else Warn)
                    }
                    val b = s.optJSONObject("budget")
                    if (b != null) Muted("Spent today $%.4f of $%.2f · month $%.4f of $%.2f".format(
                        b.optDouble("spent_today_usd"), b.optDouble("daily_usd"), b.optDouble("spent_month_usd"), b.optDouble("monthly_usd")))
                    if (s.optInt("pending_approvals") > 0) {
                        TextButton(onClick = { vm.go(Screen.APPROVALS) }) { Text("${s.optInt("pending_approvals")} action(s) waiting for your approval →") }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        ConfirmButton("Stop everything", "Pause all agents, stop workers, switch off internet and cloud models?") { vm.panic() }
                        if (s.optBoolean("agents_paused")) OutlinedButton(onClick = { vm.resume() }) { Text("Resume") }
                    }
                }
            }
        }
        item { Text("Active tasks", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
        val active = vm.tasks.filter { it.optString("state") !in setOf("COMPLETED", "FAILED", "CANCELLED") }.take(6)
        if (active.isEmpty() && vm.offlineTasks.isEmpty()) item { Muted("Nothing running.") }
        items(vm.offlineTasks) { t -> Section { Text(t.title); Pill("Queued offline", Warn) } }
        items(active) { t ->
            Section(modifier = Modifier.clickable { vm.go(Screen.TASKS); vm.openTask(t.optString("id")) }) {
                Text(t.optString("title"))
                Pill(t.optString("state"), stateColor(t.optString("state")))
            }
        }
        item { Text("Recent projects", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
        items(vm.projects.take(5)) { p ->
            Section(modifier = Modifier.clickable { vm.go(Screen.PROJECTS); vm.openProject(p.optString("id")) }) {
                Text(p.optString("name")); Muted(p.optString("kind"))
            }
        }
        item { Spacer(Modifier.height(32.dp)) }
    }
}

/** Simple wrapping row (avoids the experimental FlowRow API). */
@Composable
fun FlowRowCompat(content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) { Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { content() } }
}

@Composable
fun ChatScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    val conv = vm.currentConv
    if (conv == null) {
        ConversationList(vm)
        return
    }
    var input by remember { mutableStateOf("") }
    val voiceIn = vm.repo.store.getBool("voice_in", false)
    val voiceOut = vm.repo.store.getBool("voice_out", false)
    val tts = remember { mutableStateOf<TextToSpeech?>(null) }
    DisposableEffect(voiceOut) {
        if (voiceOut) tts.value = TextToSpeech(ctx) { status -> if (status == TextToSpeech.SUCCESS) tts.value?.language = Locale.getDefault() }
        onDispose { tts.value?.shutdown(); tts.value = null }
    }
    val speech = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        if (res.resultCode == Activity.RESULT_OK) {
            res.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.let { input = it }
        }
    }
    val listState = rememberLazyListState()
    LaunchedEffect(vm.chatMessages.size, vm.streaming.length) {
        val n = vm.chatMessages.size + if (vm.streaming.isNotEmpty()) 1 else 0
        if (n > 0) listState.animateScrollToItem(n - 1)
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { vm.openConversation(null) }) { Text("← Chats") }
            Text(conv.title, modifier = Modifier.weight(1f), maxLines = 1)
            if (conv.private) Pill("Private · stays local", Ok)
        }
        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), state = listState) {
            items(vm.chatMessages) { m -> Bubble(m.role, m.content, m.provider, m.queued) }
            if (vm.streaming.isNotEmpty()) item { Bubble("assistant", vm.streaming, "…", false) }
        }
        Surface(tonalElevation = 2.dp) {
            Column(Modifier.padding(12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Choice("Route", listOf("auto", "chat", "task"), vm.chatRoute) { vm.chatRoute = it }
                    if (voiceOut) {
                        TextButton(onClick = {
                            vm.chatMessages.lastOrNull { it.role == "assistant" }?.let { tts.value?.speak(it.content.take(3000), TextToSpeech.QUEUE_FLUSH, null, "last") }
                        }) { Text("🔊 Read") }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(input, { input = it }, modifier = Modifier.weight(1f), placeholder = { Text("Message SASTRA…") }, maxLines = 6)
                    if (voiceIn) {
                        TextButton(onClick = {
                            speech.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM))
                        }) { Text("🎤") }
                    }
                    Button(onClick = { val t = input; input = ""; vm.send(t) }, enabled = input.isNotBlank() && !vm.busy,
                        modifier = Modifier.padding(start = 6.dp)) { Text("Send") }
                }
            }
        }
    }
}

@Composable
private fun Bubble(role: String, text: String, provider: String?, queued: Boolean) {
    val mine = role == "user"
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Surface(
            color = if (mine) MaterialTheme.colorScheme.primary.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surface,
            shape = MaterialTheme.shapes.medium, modifier = Modifier.widthIn(max = 340.dp),
        ) {
            Column(Modifier.padding(10.dp)) {
                SelectionContainer { Text(text) }
                if (!mine && provider != null) Muted(provider)
                if (queued) Pill("Queued for server", Warn)
            }
        }
    }
}

@Composable
private fun ConversationList(vm: AppViewModel) {
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
                Button(onClick = { vm.newConversation(false) }) { Text("New chat") }
                OutlinedButton(onClick = { vm.newConversation(true) }) { Text("New private chat") }
            }
            Muted("Private chats are only answered by on-device or self-hosted models — never sent to cloud providers.")
        }
        items(vm.conversations) { c ->
            Section(modifier = Modifier.clickable { vm.openConversation(c) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(c.title)
                        Muted(fmtMillis(c.updatedAt) + if (c.private) " · private" else "")
                    }
                    TextButton(onClick = { vm.deleteConversation(c) }) { Text("Delete", color = Bad) }
                }
            }
        }
        if (vm.conversations.isEmpty()) item { Muted("No conversations yet.") }
    }
}
