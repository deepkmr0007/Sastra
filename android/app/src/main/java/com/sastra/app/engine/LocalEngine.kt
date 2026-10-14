package com.sastra.app.engine

/**
 * Offline assistant used when the SASTRA server is unreachable and no on-device
 * model is configured. It is honest about what it is: deterministic tools
 * (arithmetic, unit conversion, saved-memory search, note capture) — not a
 * language model. Pure Kotlin so it is unit-tested on the JVM.
 */
class LocalEngine(private val memorySearch: (String) -> List<String>) {

    data class Reply(val text: String, val handled: Boolean, val queueForServer: Boolean, val remember: String? = null)

    private val conversions: Map<Pair<String, String>, Double> = mapOf(
        ("km" to "mi") to 0.621371, ("mi" to "km") to 1.609344,
        ("kg" to "lb") to 2.204623, ("lb" to "kg") to 0.453592,
        ("m" to "ft") to 3.28084, ("ft" to "m") to 0.3048,
        ("l" to "gal") to 0.264172, ("gal" to "l") to 3.785411,
        ("acre" to "bigha") to 1.6, ("bigha" to "acre") to 0.625, // Rajasthan pucca bigha ≈ 0.625 acre (varies by region)
        ("acre" to "hectare") to 0.404686, ("hectare" to "acre") to 2.471054,
    )

    fun answer(input: String): Reply {
        val text = input.trim()
        if (text.isEmpty()) return Reply("", handled = true, queueForServer = false)
        val lower = text.lowercase()

        // 1. Arithmetic
        val expr = text.replace(Regex("(?i)^(what is|what's|calculate|compute|kitna hai|=)\\s*"), "").trimEnd('?', '=', ' ')
        if (Calculator.looksLikeMath(expr)) {
            return try {
                Reply("$expr = ${Calculator.format(Calculator.evaluate(expr))}\n\n(Computed on this phone.)", true, false)
            } catch (e: Calculator.CalcException) {
                Reply("I couldn't compute that: ${e.message}", true, false)
            }
        }

        // 2. Unit conversion: "5 km to mi", "2.5 acre in bigha"
        Regex("(?i)^\\s*([\\d.]+)\\s*([a-z]+)\\s+(?:to|in|into)\\s+([a-z]+)\\s*\\??$").find(text)?.let { m ->
            val value = m.groupValues[1].toDoubleOrNull()
            val factor = conversions[m.groupValues[2].lowercase() to m.groupValues[3].lowercase()]
            if (value != null && factor != null) {
                val note = if ("bigha" in lower) "\n\nNote: bigha size differs by state/district — check your local revenue record." else ""
                return Reply("${Calculator.format(value)} ${m.groupValues[2]} ≈ ${Calculator.format(value * factor)} ${m.groupValues[3]}$note", true, false)
            }
        }

        // 3. Remember / note capture
        Regex("(?i)^(remember|note|yaad rakho)[:\\s]+(.+)$").find(text)?.let { m ->
            val note = m.groupValues[2].trim()
            return Reply("Saved on this phone. It will sync to SASTRA when you're back online.", true, false, remember = note)
        }

        // 4. Memory recall
        val hits = memorySearch(text).take(5)
        if (hits.isNotEmpty()) {
            return Reply(
                "I'm offline, but here's what you've saved that looks relevant:\n" + hits.joinToString("\n") { "• $it" } +
                    "\n\nI've also queued your question for SASTRA to answer properly when the connection is back.",
                handled = true, queueForServer = true,
            )
        }

        // 5. Honest fallback
        return Reply(
            "I'm offline and no on-device model is set up, so I can't reason about this yet. " +
                "I've queued it — SASTRA will answer when you're back online. " +
                "Offline I can do arithmetic, unit conversion and search your saved notes.",
            handled = false, queueForServer = true,
        )
    }
}
