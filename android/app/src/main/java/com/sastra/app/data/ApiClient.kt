package com.sastra.app.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

class ApiException(val status: Int, message: String) : IOException(message)

/**
 * Minimal HTTPS client for the SASTRA server (HttpURLConnection + org.json,
 * no third-party networking libraries). Bearer token is supplied per call.
 * Call from a background dispatcher.
 */
class ApiClient(private val baseUrl: () -> String, private val token: () -> String?) {

    private fun open(path: String, method: String, timeoutMs: Int = 20_000): HttpURLConnection {
        val base = baseUrl().trimEnd('/')
        require(base.startsWith("https://") || base.startsWith("http://127.0.0.1") || base.startsWith("http://localhost") ||
            base.startsWith("http://10.0.2.2")) { "Server URL must use HTTPS" }
        val conn = URL(base + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = timeoutMs.coerceAtMost(15_000)
        conn.readTimeout = timeoutMs
        conn.setRequestProperty("Accept", "application/json")
        token()?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        conn.instanceFollowRedirects = false
        return conn
    }

    private fun send(conn: HttpURLConnection, body: JSONObject?) {
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
    }

    private fun readBody(conn: HttpURLConnection): String {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
        if (code !in 200..299) {
            val msg = try { JSONObject(text).optString("error", text) } catch (_: Exception) { text }
            throw ApiException(code, msg.ifBlank { "HTTP $code" })
        }
        return text
    }

    fun request(method: String, path: String, body: JSONObject? = null, timeoutMs: Int = 20_000): JSONObject {
        val conn = open(path, method, timeoutMs)
        try {
            send(conn, body)
            val text = readBody(conn)
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    fun get(path: String) = request("GET", path)
    fun post(path: String, body: JSONObject = JSONObject()) = request("POST", path, body)
    fun put(path: String, body: JSONObject) = request("PUT", path, body)
    fun delete(path: String) = request("DELETE", path)

    fun bytes(path: String): Pair<ByteArray, String?> {
        val conn = open(path, "GET", 60_000)
        try {
            val code = conn.responseCode
            if (code !in 200..299) throw ApiException(code, "HTTP $code")
            return conn.inputStream.use { it.readBytes() } to conn.getHeaderField("X-SHA256")
        } finally {
            conn.disconnect()
        }
    }

    fun reachable(): Boolean = try {
        val conn = open("/health", "GET", 4_000)
        try { conn.responseCode == 200 } finally { conn.disconnect() }
    } catch (_: Exception) {
        false
    }

    /** POST /api/chat and deliver server-sent events as they arrive. */
    fun streamChat(body: JSONObject, onEvent: (String, JSONObject) -> Unit) {
        val conn = open("/api/chat", "POST", 180_000)
        conn.setRequestProperty("Accept", "text/event-stream")
        try {
            send(conn, body)
            if (conn.responseCode !in 200..299) readBody(conn)
            BufferedReader(InputStreamReader(conn.inputStream, Charsets.UTF_8)).use { reader ->
                var event = "message"
                val data = StringBuilder()
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("event:") -> event = line.substring(6).trim()
                        line.startsWith("data:") -> data.append(line.substring(5).trim())
                        line.isEmpty() && data.isNotEmpty() -> {
                            onEvent(event, JSONObject(data.toString()))
                            data.setLength(0)
                            event = "message"
                        }
                    }
                }
            }
        } finally {
            conn.disconnect()
        }
    }
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
fun JSONObject.strOrNull(key: String): String? = if (isNull(key) || !has(key)) null else optString(key)
