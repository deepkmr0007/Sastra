package com.sastra.app.engine

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Optional on-device model through an OpenAI-compatible endpoint running on the
 * phone itself — e.g. llama.cpp `llama-server` or Ollama inside Termux at
 * http://127.0.0.1:8080/v1. Private conversations can use it with nothing
 * leaving the device. See docs/SETUP_ANDROID.md for setup and model sizes.
 */
class LocalModelClient(private val baseUrl: String, private val model: String) {

    data class Benchmark(val ok: Boolean, val ttftMs: Long, val totalMs: Long, val tokensPerSec: Double, val text: String, val error: String?)

    val configured: Boolean get() = baseUrl.isNotBlank() && model.isNotBlank()

    fun stream(system: String, history: List<Pair<String, String>>, onDelta: (String) -> Unit): String {
        val url = baseUrl.trimEnd('/') + "/chat/completions"
        require(url.startsWith("http://127.0.0.1") || url.startsWith("http://localhost") || url.startsWith("https://")) {
            "On-device model URL must be localhost (or HTTPS)"
        }
        val msgs = JSONArray()
        if (system.isNotBlank()) msgs.put(JSONObject().put("role", "system").put("content", system))
        history.forEach { (role, content) -> msgs.put(JSONObject().put("role", role).put("content", content)) }
        val body = JSONObject().put("model", model).put("messages", msgs).put("stream", true).put("max_tokens", 512)
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.connectTimeout = 3_000
        conn.readTimeout = 180_000
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        val out = StringBuilder()
        try {
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            if (conn.responseCode !in 200..299) throw IllegalStateException("Local model HTTP ${conn.responseCode}")
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { r ->
                while (true) {
                    val line = r.readLine() ?: break
                    if (!line.startsWith("data:")) continue
                    val data = line.substring(5).trim()
                    if (data == "[DONE]") break
                    val choices = try { JSONObject(data).optJSONArray("choices") } catch (_: Exception) { null } ?: continue
                    if (choices.length() == 0) continue
                    val piece = choices.getJSONObject(0).optJSONObject("delta")?.optString("content", "") ?: ""
                    if (piece.isNotEmpty()) {
                        out.append(piece)
                        onDelta(piece)
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
        return out.toString()
    }

    /** Real measurement on this phone: time to first token, total time, rough tokens/sec. */
    fun benchmark(): Benchmark {
        val start = System.nanoTime()
        var first = -1L
        return try {
            val text = stream("Answer in one short sentence.", listOf("user" to "What is the capital of Rajasthan?")) {
                if (first < 0) first = System.nanoTime()
            }
            val total = (System.nanoTime() - start) / 1_000_000
            val ttft = if (first > 0) (first - start) / 1_000_000 else total
            val genSec = ((total - ttft).coerceAtLeast(1)) / 1000.0
            Benchmark(true, ttft, total, (text.length / 4.0) / genSec, text, null)
        } catch (e: Exception) {
            Benchmark(false, 0, 0, 0.0, "", e.message)
        }
    }
}
