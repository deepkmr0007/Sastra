package com.sastra.app.ui.screens

import android.content.Intent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.sastra.app.data.objects
import com.sastra.app.ui.*
import com.sastra.app.ui.theme.Bad
import com.sastra.app.ui.theme.Warn

private val TERMINAL = setOf("COMPLETED", "FAILED", "CANCELLED")

@Composable
fun TasksScreen(vm: AppViewModel) {
    val detail = vm.taskDetail
    if (detail != null) {
        TaskDetail(vm)
        return
    }
    var req by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf("auto") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("New task") {
                Field(req, { req = it }, "Describe the task", singleLine = false, minLines = 2)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Choice("Kind", listOf("auto", "direct", "research", "code", "data", "document", "android", "action", "compute"), kind) { kind = it }
                    Button(onClick = { vm.createTask(req, if (kind == "auto") null else kind); req = "" }, enabled = req.isNotBlank()) { Text("Start") }
                }
                Muted("Tasks run with limits on time, tokens, tool calls and cost. Consequential actions always wait for your approval.")
            }
        }
        if (vm.offlineTasks.isNotEmpty()) {
            item { Text("Queued offline", style = MaterialTheme.typography.titleSmall) }
            items(vm.offlineTasks) { t -> Section { Text(t.title); Pill("Will start when connected", Warn) } }
        }
        items(vm.tasks) { t ->
            Section(modifier = Modifier.clickable { vm.openTask(t.optString("id")) }) {
                Text(t.optString("title"), maxLines = 2)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Pill(t.optString("state"), stateColor(t.optString("state")))
                    Pill(t.optString("kind"))
                    Muted(fmtTime(t.optDouble("created_at")))
                }
                if (t.optString("error").isNotBlank() && t.optString("error") != "null") Text(t.optString("error"), color = Bad, maxLines = 3)
            }
        }
        if (vm.tasks.isEmpty()) item { Muted(if (vm.online) "No tasks yet." else "Connect to see server tasks.") }
    }
}

@Composable
private fun TaskDetail(vm: AppViewModel) {
    val d = vm.taskDetail ?: return
    val t = d.getJSONObject("task")
    val id = t.optString("id")
    var answer by remember { mutableStateOf("") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            TextButton(onClick = { vm.taskDetail = null; vm.loadTasks() }) { Text("← All tasks") }
            Section(t.optString("title")) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Pill(t.optString("state"), stateColor(t.optString("state"))); Pill(t.optString("kind")); Pill("mode ${t.optInt("mode")}")
                }
                val u = t.optJSONObject("usage")
                if (u != null) Muted("Tokens ${u.optInt("tokens")} · tool calls ${u.optInt("tool_calls")} · cost $${"%.5f".format(u.optDouble("cost_usd"))} · attempts ${t.optInt("attempts")}")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (t.optString("state") !in TERMINAL) OutlinedButton(onClick = { vm.cancelTask(id) }) { Text("Cancel") }
                    if (t.optString("state") == "FAILED") Button(onClick = { vm.retryTask(id) }) { Text("Retry") }
                }
            }
            if (t.optString("state") == "NEEDS_USER_INPUT") {
                Section("SASTRA needs your input") {
                    Text(t.optJSONObject("result")?.optString("question") ?: "")
                    Field(answer, { answer = it }, "Your answer", singleLine = false)
                    Button(onClick = { vm.answerTask(id, answer) }, enabled = answer.isNotBlank()) { Text("Send answer") }
                }
            }
            if (t.optString("state") == "AWAITING_APPROVAL") {
                Section("Waiting for approval") { TextButton(onClick = { vm.go(Screen.APPROVALS) }) { Text("Open approvals →") } }
            }
            val result = t.optJSONObject("result")
            if (result != null && result.has("summary")) {
                Section("Result") {
                    Muted("Verification: " + result.optString("verification"))
                    SelectionContainer { Text(result.optString("summary")) }
                }
            }
            Text("Timeline", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
        }
        items(d.optJSONArray("events")?.objects().orEmpty()) { e ->
            Row(Modifier.padding(vertical = 3.dp)) {
                Muted(fmtTime(e.optDouble("ts")) + "  ")
                Text(e.optString("message"), style = MaterialTheme.typography.bodySmall)
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
fun ResearchScreen(vm: AppViewModel) {
    val r = vm.report
    if (r != null) {
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            item {
                TextButton(onClick = { vm.report = null }) { Text("← Reports") }
                SelectionContainer { Text(r.optString("report_md")) }
            }
        }
        return
    }
    var q by remember { mutableStateOf("") }
    var urls by remember { mutableStateOf("") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("New research") {
                Field(q, { q = it }, "Question", singleLine = false)
                Field(urls, { urls = it }, "Optional sources (one URL per line)", singleLine = false, minLines = 2)
                Button(onClick = { vm.startResearch(q, urls.lines().map { it.trim() }.filter { it.startsWith("http") }); q = ""; urls = "" },
                    enabled = q.length > 3) { Text("Research") }
                Muted("Reports cite every source with access time. Facts, estimates and interpretations are labelled; disagreements between sources are shown.")
            }
        }
        items(vm.reports) { rep ->
            Section(modifier = Modifier.clickable { vm.openReport(rep.optString("id")) }) {
                Text(rep.optString("question")); Muted(fmtTime(rep.optDouble("created_at")))
            }
        }
    }
}

@Composable
fun ProjectsScreen(vm: AppViewModel) {
    val p = vm.project
    val fv = vm.fileView
    if (p != null) {
        val proj = p.getJSONObject("project")
        val pid = proj.optString("id")
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            item {
                TextButton(onClick = { if (fv != null) vm.fileView = null else vm.project = null }) { Text(if (fv != null) "← Files" else "← Projects") }
            }
            if (fv != null) {
                item { Section(fv.first) { SelectionContainer { Mono(fv.second) } } }
            } else {
                item {
                    Section(proj.optString("name")) {
                        Muted(proj.optString("description"))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { vm.checkpoint(pid) }) { Text("Checkpoint") }
                            OutlinedButton(onClick = { vm.go(Screen.TASKS) }) { Text("New task…") }
                        }
                    }
                    Text("Files", style = MaterialTheme.typography.titleSmall)
                }
                items(p.optJSONArray("files")?.objects().orEmpty()) { f ->
                    Row(Modifier.fillMaxWidth().clickable { vm.openFile(pid, f.optString("path")) }.padding(vertical = 6.dp)) {
                        Text(f.optString("path"), modifier = Modifier.weight(1f)); Muted("${f.optInt("size")} B")
                    }
                }
                item { Text("Checkpoints", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp)) }
                items(p.optJSONArray("log")?.objects().orEmpty()) { c ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) { Text(c.optString("message"), maxLines = 1); Muted(c.optString("commit").take(10)) }
                        ConfirmButton("Restore", "Restore files to this checkpoint? The current state is saved first, so this is reversible.", danger = false) {
                            vm.rollback(pid, c.optString("commit"))
                        }
                    }
                }
            }
        }
        return
    }
    var name by remember { mutableStateOf("") }
    var desc by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf("software") }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item {
            Section("New project") {
                Field(name, { name = it }, "Name")
                Field(desc, { desc = it }, "Goal / description", singleLine = false)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Choice("Kind", listOf("general", "software", "research", "venture", "android"), kind) { kind = it }
                    Button(onClick = { vm.createProject(name, desc, kind); name = ""; desc = "" }, enabled = name.isNotBlank()) { Text("Create") }
                }
            }
        }
        items(vm.projects) { pr ->
            Section(modifier = Modifier.clickable { vm.openProject(pr.optString("id")) }) {
                Text(pr.optString("name")); Muted(pr.optString("kind") + " · " + fmtTime(pr.optDouble("created_at")))
            }
        }
    }
}

@Composable
fun FilesScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item { Muted("Artifacts are versioned and hashed. Downloads are verified against their SHA-256 before opening.") }
        items(vm.artifacts) { a ->
            Section {
                Text("${a.optString("name")}  v${a.optInt("version")}")
                Muted("${a.optLong("size")} bytes · ${a.optString("mime")} · sha256 ${a.optString("sha256").take(12)}…" +
                    if (a.optInt("encrypted") == 1) " · encrypted" else "")
                Button(onClick = {
                    vm.downloadArtifact(ctx, a) { f ->
                        val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)
                        val send = Intent(Intent.ACTION_SEND).setType(a.optString("mime", "application/octet-stream"))
                            .putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        ctx.startActivity(Intent.createChooser(send, "Open / share"))
                    }
                }) { Text("Download & share") }
            }
        }
        if (vm.artifacts.isEmpty()) item { Muted("No artifacts yet.") }
    }
}

@Composable
fun ApprovalsScreen(vm: AppViewModel) {
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        item { Muted("Nothing consequential happens without you. Each approval covers exactly one action with exactly these details.") }
        items(vm.approvals) { a ->
            Section(a.optString("tool")) {
                Pill("risk: " + a.optString("risk"), stateColor(a.optString("risk")))
                Text(a.optString("reason"))
                SelectionContainer { Mono(a.optString("args")) }
                Muted("Requested " + fmtTime(a.optDouble("created_at")) + " · expires " + fmtTime(a.optDouble("expires_at")))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { vm.decide(a.optString("id"), true) }) { Text("Approve once") }
                    OutlinedButton(onClick = { vm.decide(a.optString("id"), false) }) { Text("Reject") }
                }
                if (a.optString("risk") != "critical") {
                    TextButton(onClick = { vm.decide(a.optString("id"), true, batchUses = 5) }) { Text("Approve + allow 4 more like this (24 h)") }
                }
            }
        }
        if (vm.approvals.isEmpty()) item { Muted("Nothing is waiting for approval.") }
        if (vm.grants.isNotEmpty()) item { Text("Active batch approvals", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp)) }
        items(vm.grants.filter { it.isNull("revoked_at") }) { g ->
            Section {
                Text(g.optString("tool") + " · used ${g.optInt("uses")}/${g.optInt("max_uses")}")
                Muted("scope " + g.optString("scope") + " · until " + fmtTime(g.optDouble("expires_at")))
                OutlinedButton(onClick = { vm.revokeGrant(g.optString("id")) }) { Text("Revoke") }
            }
        }
    }
}
